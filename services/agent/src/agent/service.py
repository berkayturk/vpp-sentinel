"""The poller: active alerts become incidents, incidents run once, and end when the alert does.

``decide`` is pure (data in, events out) so the lifecycle is testable without
I/O; ``poll`` gathers the data; ``handle_events`` executes, one event at a
time, and never lets one failure stop the next.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import structlog

from agent.clients import alertmanager, grafana
from agent.clients.errors import ClientError
from agent.context import ContextDeps, build_context
from agent.fingerprint import Alert, fingerprint, slug
from agent.loop import ModelRunner, run_incident
from agent.memory import Incident, IncidentStore
from agent.policy import Allowlist, PathGuard
from agent.tools import ToolDeps, make_tools
from agent.workspace import Workspace

log = structlog.get_logger("agent.service")

MITIGATION_WINDOW = timedelta(minutes=5)
PAGE_SEVERITY = "critical"


@dataclass(frozen=True)
class OpenIncident:
    alert: Alert


@dataclass(frozen=True)
class ResolveIncident:
    incident: Incident
    outcome: str


@dataclass(frozen=True)
class Page:
    alert: Alert


@dataclass(frozen=True)
class PollFailed:
    error: str


Event = OpenIncident | ResolveIncident | Page | PollFailed


@dataclass(frozen=True)
class ServiceDeps:
    alertmanager: httpx.AsyncClient
    prometheus: httpx.AsyncClient
    loki: httpx.AsyncClient
    kube: httpx.AsyncClient
    github: httpx.AsyncClient
    grafana: httpx.AsyncClient
    store: IncidentStore
    policy: Allowlist
    guard_factory: Callable[[Path], PathGuard]
    runner: ModelRunner
    workspace_factory: Callable[[str], Workspace]
    runbooks_dir: Path
    repo: str
    base_branch: str
    salt: bytes | None
    now: Callable[[], datetime]


def decide(
    active: tuple[Alert, ...], open_incidents: tuple[Incident, ...], now: datetime
) -> tuple[Event, ...]:
    """Which alerts open an incident, which open incidents are over, who gets paged."""
    open_by_fp = {i.fingerprint: i for i in open_incidents}
    active_fps = {fingerprint(a) for a in active}
    events: list[Event] = []
    seen: set[str] = set()
    for alert in active:
        fp = fingerprint(alert)
        if fp in open_by_fp or fp in seen:
            continue
        seen.add(fp)
        if alert.severity == PAGE_SEVERITY:
            events.append(Page(alert))  # before the run: a page must not wait for the model
        events.append(OpenIncident(alert))
    for fp, incident in open_by_fp.items():
        if fp not in active_fps:
            events.append(ResolveIncident(incident, resolution_outcome(incident, now)))
    return tuple(events)


def resolution_outcome(incident: Incident, now: datetime) -> str:
    """What the resolved incident records about how it ended."""
    for action in reversed(incident.actions_taken):
        if action.get("tool") == "agent" and str(action.get("outcome", "")).startswith("error"):
            return f"resolved after agent error ({action['outcome']})"
        if action.get("tool") == "restart_deployment" and action.get("outcome") == "ok":
            at = datetime.fromisoformat(str(action.get("at")))
            if now - at <= MITIGATION_WINDOW:
                return f"resolved: mitigation worked (restart at {at.isoformat()})"
    return "resolved"


async def poll(deps: ServiceDeps, now: datetime) -> tuple[Event, ...]:
    try:
        active = await alertmanager.active_alerts(deps.alertmanager)
        open_incidents = await deps.store.list_open()
    except (ClientError, Exception) as exc:
        return (PollFailed(str(exc)),)
    return decide(active, open_incidents, now)


async def handle_events(events: tuple[Event, ...], deps: ServiceDeps) -> None:
    for event in events:
        try:
            await _handle(event, deps)
        except Exception as exc:  # one failed event must not stop the others
            log.error("event.failed", event=type(event).__name__, error=str(exc))


async def _handle(event: Event, deps: ServiceDeps) -> None:
    if isinstance(event, OpenIncident):
        await _open(event.alert, deps)
    elif isinstance(event, ResolveIncident):
        await deps.store.resolve(event.incident.id, event.outcome)
        log.info("incident.resolved", incident_id=event.incident.id, outcome=event.outcome)
    elif isinstance(event, Page):
        await _page(event.alert, deps)
    else:
        log.warning("poll.failed", error=event.error)


async def _open(alert: Alert, deps: ServiceDeps) -> None:
    incident = await deps.store.open(alert)
    log.info("incident.opened", incident_id=incident.id, fingerprint=incident.fingerprint)
    workspace = deps.workspace_factory(slug(incident.fingerprint))  # cloned lazily by the tools
    ctx = await build_context(
        alert,
        ContextDeps(deps.prometheus, deps.loki, deps.store, deps.runbooks_dir, deps.salt),
        deps.now(),
    )
    toolset = make_tools(
        ToolDeps(
            prometheus=deps.prometheus,
            loki=deps.loki,
            kube=deps.kube,
            github=deps.github,
            store=deps.store,
            workspace=workspace,
            policy=deps.policy,
            guard=deps.guard_factory(workspace.root),
            incident=incident,
            now=deps.now,
            repo=deps.repo,
            base_branch=deps.base_branch,
            salt=deps.salt,
        )
    )
    outcome = await run_incident(ctx, toolset, deps.runner, deps.store)
    log.info(
        "incident.report",
        incident_id=incident.id,
        status=outcome.status,
        report=outcome.report.model_dump(mode="json") if outcome.report else None,
    )
    if alert.severity != PAGE_SEVERITY and _needs_a_human(outcome, toolset.log):
        await _page(alert, deps)


def _needs_a_human(outcome, calls) -> bool:
    """Page when the model asked for it, could not finish, or had its action refused."""
    if outcome.status != "reported":
        return True
    if outcome.report is not None and outcome.report.page_human:
        return True
    return any(c.tool == "restart_deployment" and c.outcome.startswith("REFUSED") for c in calls)


async def _page(alert: Alert, deps: ServiceDeps) -> None:
    """Paging is represented by a log line and a Grafana annotation (spec §7.3)."""
    site, device_type = alert.labels.get("site", ""), alert.labels.get("device_type", "")
    text = f"page: {alert.name} {site} {device_type}".strip()
    log.warning("incident.page", alert=alert.name, labels=dict(alert.labels))
    try:
        await grafana.annotate(deps.grafana, text, ["page", "vpp-sentinel"])
    except ClientError as exc:
        log.error("incident.page_annotation_failed", error=str(exc))


async def run_forever(
    deps: ServiceDeps, interval_seconds: float, on_ready: Callable[[], None]
) -> None:
    on_ready()
    while True:
        events = await poll(deps, deps.now())
        await handle_events(events, deps)
        await asyncio.sleep(interval_seconds)
