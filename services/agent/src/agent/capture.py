"""The capture protocol: run both scenarios on a cluster, measure them, write the KPI table.

The maths is pure (``compute_kpis``, ``render_*``); ``run_protocol`` does the
polling and always clears every fault on the way out. A KPI the protocol
could not observe is reported as "not measured", never estimated.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx

from agent.clients import alertmanager, prometheus
from agent.clients.errors import ClientError
from agent.memory import Incident, IncidentStore, PostgresIncidentStore

# Anthropic list prices per million tokens on the date below (Claude Opus 5.5).
PRICES_USD_PER_M = {"input": 4.0, "output": 20.0, "cache_read": 0.20}
PRICES_DATE = "2026-11-01"
POLL_PRECISION_SECONDS = 5
SURGE = ("load-surge", {"site": "lugano-south", "multiplier": 50, "duration_seconds": 180})
DRIFT = ("firmware-drift", {"site": "como", "device_type": "charger", "fraction": 0.4})
LAG_ALERT, DARK_ALERT = "VppIngestionLagHigh", "VppSiteDevicesDark"
LAG_FP, DARK_FP = f"{LAG_ALERT}|-|-", f"{DARK_ALERT}|como|charger"


class PrerequisiteError(Exception):
    """The cluster is not in the state the protocol needs; nothing was injected."""


class ProtocolError(Exception):
    """A step did not complete in time; faults have been cleared."""


class FaultSwitch(Protocol):
    async def post(self, name: str, body: dict) -> None: ...
    async def clear(self) -> None: ...


@dataclass(frozen=True)
class ScenarioObs:
    name: str
    injected_at: datetime | None
    alert_active_at: datetime | None
    opened_at: datetime | None
    action_at: datetime | None
    report_at: datetime | None
    resolved_at: datetime | None
    incident: Incident | None


@dataclass(frozen=True)
class Observations:
    surge: ScenarioObs
    drift: ScenarioObs
    tokens: Mapping[str, int]
    tool_calls: int
    refusals: int
    alerts_fired: int
    actions_taken: int


@dataclass(frozen=True)
class KpiRow:
    detection_s: float | None
    triage_s: float | None
    action_s: float | None
    pull_request_s: float | None
    report_s: float | None
    resolution_s: float | None


@dataclass(frozen=True)
class KpiTable:
    rows: Mapping[str, KpiRow]
    tokens: Mapping[str, int]
    cost_usd: float
    tool_calls: int
    refusals: int
    alert_to_action_ratio: str
    complete: bool


@dataclass(frozen=True)
class ProtocolInfo:
    date: str
    model: str
    agent_image: str


@dataclass(frozen=True)
class CaptureDeps:
    faults: FaultSwitch
    alertmanager: httpx.AsyncClient
    prometheus: httpx.AsyncClient
    store: IncidentStore
    secret_keys: frozenset[str]
    agent_ready: bool
    now: Callable[[], datetime]
    poll_seconds: float = 5.0
    scenario_timeout_seconds: float = 20 * 60
    quiet_seconds: float = 120.0


# --- pure ------------------------------------------------------------------------


def _delta(later: datetime | None, earlier: datetime | None) -> float | None:
    if later is None or earlier is None:
        return None
    return round((later - earlier).total_seconds(), 1)


def compute_kpis(obs: Observations) -> KpiTable:
    rows = {}
    for scenario in (obs.surge, obs.drift):
        action = _delta(scenario.action_at, scenario.alert_active_at)
        rows[scenario.name] = KpiRow(
            detection_s=_delta(scenario.alert_active_at, scenario.injected_at),
            triage_s=_delta(scenario.opened_at, scenario.alert_active_at),
            action_s=action if scenario.name == "load-surge" else None,
            pull_request_s=action if scenario.name == "firmware-drift" else None,
            report_s=_delta(scenario.report_at, scenario.alert_active_at),
            resolution_s=_delta(scenario.resolved_at, scenario.alert_active_at),
        )
    cost = sum(obs.tokens.get(kind, 0) * price / 1e6 for kind, price in PRICES_USD_PER_M.items())
    required = [
        rows["load-surge"].detection_s,
        rows["load-surge"].action_s,
        rows["load-surge"].resolution_s,
        rows["firmware-drift"].detection_s,
        rows["firmware-drift"].pull_request_s,
        rows["firmware-drift"].report_s,
    ]
    return KpiTable(
        rows=rows,
        tokens=dict(obs.tokens),
        cost_usd=round(cost, 2),
        tool_calls=obs.tool_calls,
        refusals=obs.refusals,
        alert_to_action_ratio=f"{obs.actions_taken}/{obs.alerts_fired}",
        complete=all(value is not None for value in required),
    )


def _kpi_line(label: str, a: float | str | None, b: float | str | None, why: str) -> str:
    return f"| {label} | {_cell(a, why)} | {_cell(b, why)} |"


def _cell(value: float | str | None, why: str) -> str:
    if isinstance(value, str):
        return value
    return f"{value:g} s" if value is not None else f"not measured ({why})"


def render_kpis_md(table: KpiTable, protocol: ProtocolInfo) -> str:
    surge, drift = table.rows["load-surge"], table.rows["firmware-drift"]
    prices = PRICES_USD_PER_M
    header = (
        f"Measured on {protocol.date} with `make capture` on a fresh kind cluster"
        f" (`make down`, `make demo`, credentials, then this protocol), model `{protocol.model}`,"
        f" agent image `{protocol.agent_image}`. Timestamps come from Alertmanager (the moment"
        " an alert began firing, stored on the incident), the `incidents` table (actions,"
        " reports, resolution) and the capture script's own clock (fault injection, incident"
        f" opening — polled every {POLL_PRECISION_SECONDS} s, so ±{POLL_PRECISION_SECONDS} s)."
    )
    rows = [
        ("Detection: fault injected → alert firing", surge.detection_s, drift.detection_s),
        ("Triage: alert firing → incident opened", surge.triage_s, drift.triage_s),
        ("Action: alert firing → allowlisted restart", surge.action_s, "—"),
        ("Pull request: alert firing → PR opened or updated", "—", drift.pull_request_s),
        ("Report: alert firing → report filed", surge.report_s, drift.report_s),
        (
            "Resolution: alert firing → alert resolved",
            surge.resolution_s,
            "— (resolves when the fix is deployed or the fault is cleared)",
        ),
    ]
    why = {
        "Detection": "alert did not fire",
        "Triage": "incident not opened",
        "Action": "no restart recorded",
        "Pull request": "no pull request recorded",
        "Report": "no report",
        "Resolution": "not resolved in time",
    }
    tokens = table.tokens
    lines = [
        "# Measured KPIs",
        "",
        header,
        "",
        "| KPI | Load surge (restart) | Firmware drift (pull request) |",
        "|---|---|---|",
        *(_kpi_line(label, a, b, why[label.split(":")[0]]) for label, a, b in rows),
        "",
        "| Whole protocol | |",
        "|---|---|",
        f"| Alerts fired → actions taken | {table.alert_to_action_ratio} |",
        f"| Tool calls / refusals | {table.tool_calls} / {table.refusals} |",
        "| Model tokens (input / output / cache read) |"
        f" {tokens.get('input', 0):,} / {tokens.get('output', 0):,}"
        f" / {tokens.get('cache_read', 0):,} |",
        f"| Cost at list prices ({PRICES_DATE}: input {prices['input']:g} USD, output"
        f" {prices['output']:g} USD, cache read {prices['cache_read']:g} USD per million tokens)"
        f" | {table.cost_usd:.2f} USD |",
        "",
    ]
    if not table.complete:
        lines += [
            "**Incomplete run:** at least one KPI could not be measured; see the cells above.",
            "",
        ]
    return "\n".join(lines)


def render_sample_report_md(incident: Incident) -> str:
    return (
        "> This is the agent's own report for the firmware-drift incident, copied verbatim from"
        f" the `incidents` table (incident #{incident.id}, {incident.alert_name}).\n\n"
        + incident.report_md.rstrip()
        + "\n"
    )


# --- protocol ------------------------------------------------------------------


async def check_prerequisites(deps: CaptureDeps) -> None:
    missing = {"ANTHROPIC_API_KEY", "GITHUB_TOKEN"} - deps.secret_keys
    if missing:
        raise PrerequisiteError(
            f"the agent-credentials secret lacks {', '.join(sorted(missing))}; see README"
        )
    if not deps.agent_ready:
        raise PrerequisiteError("the agent has not logged agent.ready; is the model key set?")
    try:
        active = await alertmanager.active_alerts(deps.alertmanager)
    except ClientError as exc:
        raise PrerequisiteError(f"Alertmanager unreachable: {exc}") from exc
    if active:
        raise PrerequisiteError(f"an alert is active: {', '.join(a.name for a in active)}")
    if await deps.store.list_open():
        raise PrerequisiteError("an open incident exists; wait for it to resolve")


async def run_protocol(deps: CaptureDeps) -> Observations:
    tokens_before = await _counters(deps, "vpp_agent_model_tokens_total", "kind")
    calls_before = await _counters(deps, "vpp_agent_tool_calls_total", "outcome")
    try:
        surge = await _scenario(deps, SURGE, LAG_ALERT, LAG_FP, "restart_deployment", resolve=True)
        await _quiet(deps)
        drift = await _scenario(
            deps, DRIFT, DARK_ALERT, DARK_FP, "open_pull_request", resolve=False
        )
    finally:
        await deps.faults.clear()
    tokens_after = await _counters(deps, "vpp_agent_model_tokens_total", "kind")
    calls_after = await _counters(deps, "vpp_agent_tool_calls_total", "outcome")
    calls = {k: calls_after.get(k, 0) - calls_before.get(k, 0) for k in calls_after}
    return Observations(
        surge=surge,
        drift=drift,
        tokens={k: int(tokens_after.get(k, 0) - tokens_before.get(k, 0)) for k in tokens_after},
        tool_calls=int(sum(calls.values())),
        refusals=int(calls.get("refused", 0)),
        alerts_fired=2,
        actions_taken=sum(1 for s in (surge, drift) if s.action_at is not None),
    )


async def _scenario(
    deps: CaptureDeps, fault: tuple[str, dict], alert: str, fp: str, action_tool: str, resolve: bool
) -> ScenarioObs:
    name, body = fault
    injected_at = deps.now()
    await deps.faults.post(name, body)
    await _wait(deps, lambda: _alert_firing(deps, alert), f"{alert} firing")
    opened = await _wait(deps, lambda: deps.store.find_open(fp), f"incident for {alert}")
    opened_at = deps.now()
    # The incident row carries Alertmanager's startsAt for the alert: the moment it began firing.
    active_at = opened.started_at
    done = await _wait(deps, lambda: _with_action(deps, opened.id, action_tool), f"{action_tool}")
    report = await _wait(deps, lambda: _with_action(deps, opened.id, "file_report"), "report")
    resolved_at = None
    if resolve:
        resolved = await _wait(deps, lambda: _resolved(deps, opened.id), f"{alert} resolved")
        resolved_at = resolved.resolved_at
    return ScenarioObs(
        name=name,
        injected_at=injected_at,
        alert_active_at=active_at,
        opened_at=opened_at,
        action_at=_action_time(done, action_tool),
        report_at=_action_time(report, "file_report"),
        resolved_at=resolved_at,
        incident=report,
    )


async def _wait(deps: CaptureDeps, probe, what: str):
    deadline = deps.now().timestamp() + deps.scenario_timeout_seconds
    while True:
        try:
            value = await probe()
        except ClientError:
            value = None
        if value:
            return value
        if deps.now().timestamp() >= deadline:
            raise ProtocolError(f"timed out waiting for {what}")
        await asyncio.sleep(deps.poll_seconds)


async def _quiet(deps: CaptureDeps) -> None:
    await asyncio.sleep(deps.quiet_seconds)
    await _wait(deps, lambda: _no_alert(deps), "a quiet cluster")


async def _no_alert(deps: CaptureDeps) -> bool:
    return not await alertmanager.active_alerts(deps.alertmanager)


async def _alert_firing(deps: CaptureDeps, alert: str) -> bool:
    active = await alertmanager.active_alerts(deps.alertmanager)
    return any(a.name == alert for a in active)


async def _with_action(deps: CaptureDeps, incident_id: int, tool: str) -> Incident | None:
    incident = await _by_id(deps, incident_id)
    if incident is None or _action_time(incident, tool) is None:
        return None
    return incident


async def _resolved(deps: CaptureDeps, incident_id: int) -> Incident | None:
    incident = await _by_id(deps, incident_id)
    return incident if incident and incident.resolved_at is not None else None


async def _by_id(deps: CaptureDeps, incident_id: int) -> Incident | None:
    for incident in await deps.store.list_open():
        if incident.id == incident_id:
            return incident
    for alert in (LAG_ALERT, DARK_ALERT):
        for incident in await deps.store.recent_like(alert, limit=10):
            if incident.id == incident_id:
                return incident
    return None


def _action_time(incident: Incident, tool: str) -> datetime | None:
    for action in incident.actions_taken:
        if action.get("tool") == tool and str(action.get("outcome", "")).startswith("ok"):
            return datetime.fromisoformat(str(action["at"]))
    return None


async def _counters(deps: CaptureDeps, metric: str, label: str) -> dict[str, float]:
    try:
        samples = await prometheus.instant(deps.prometheus, f"sum by ({label}) ({metric})")
    except ClientError:
        return {}
    return {s.labels.get(label, "-"): s.value for s in samples}


# --- entrypoint ----------------------------------------------------------------


class ShellFaultSwitch:
    """Drives deploy/scripts/fault.sh, the same path the Makefile targets use."""

    def __init__(self, script: str) -> None:
        self._script = script

    async def post(self, name: str, body: dict) -> None:
        import json

        await asyncio.to_thread(
            subprocess.run, [self._script, "POST", f"/faults/{name}", json.dumps(body)], check=True
        )

    async def clear(self) -> None:
        await asyncio.to_thread(subprocess.run, [self._script, "DELETE", "/faults"], check=False)


async def _main(out_dir: Path) -> int:
    env = os.environ
    store = await PostgresIncidentStore.connect(env["DATABASE_URL"])
    deps = CaptureDeps(
        faults=ShellFaultSwitch(env["CAPTURE_FAULT_SCRIPT"]),
        alertmanager=httpx.AsyncClient(base_url=env["ALERTMANAGER_URL"], timeout=20.0),
        prometheus=httpx.AsyncClient(base_url=env["PROMETHEUS_URL"], timeout=30.0),
        store=store,
        secret_keys=frozenset(k for k in env.get("CAPTURE_SECRET_KEYS", "").split(",") if k),
        agent_ready=env.get("CAPTURE_AGENT_READY", "0") not in ("", "0"),
        now=lambda: datetime.now(UTC),
    )
    try:
        await check_prerequisites(deps)
    except PrerequisiteError as exc:
        print(f"prerequisites: {exc}", file=sys.stderr)
        return 2
    print("prerequisites ok; starting the protocol (this takes about 15 minutes)")
    try:
        obs = await run_protocol(deps)
    except ProtocolError as exc:
        print(f"protocol failed: {exc} (faults cleared)", file=sys.stderr)
        return 3
    finally:
        await store.close()
    table = compute_kpis(obs)
    info = ProtocolInfo(
        date=datetime.now(UTC).date().isoformat(),
        model=env.get("CAPTURE_AGENT_MODEL", "unknown"),
        agent_image="vpp-agent:dev",
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "kpis.md").write_text(render_kpis_md(table, info))
    if obs.drift.incident is not None:
        (out_dir / "sample-report.md").write_text(render_sample_report_md(obs.drift.incident))
    print(f"wrote {out_dir / 'kpis.md'} and {out_dir / 'sample-report.md'}")
    return 0 if table.complete else 3


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure both incident scenarios.")
    parser.add_argument("--out", default="docs", type=Path)
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.out)))


if __name__ == "__main__":
    main()
