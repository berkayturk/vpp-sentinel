"""The read-only context packet the model receives first (spec §7.2).

Everything here is assembled by code from fixed queries: sanitised logs,
timing, blast radius, severity and incident memory, plus the runbook text.
The model can dig further with its tools, but it never has to guess what
the alert is about.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from agent.clients import loki, prometheus
from agent.clients.errors import ClientError
from agent.clients.prometheus import Sample
from agent.fingerprint import Alert
from agent.memory import Incident, IncidentStore
from agent.sanitize import sanitize_text

WINDOW_BEFORE_ALERT = timedelta(minutes=10)
LOG_LIMIT = 200
LOG_QUERY = '{namespace="vpp", app="ingestion"} | json | level=~"warning|error"'
RUNBOOKS = {
    "VppIngestionLagHigh": "ingestion-lag-high.md",
    "VppSiteDevicesDark": "site-devices-dark.md",
}
INGESTION = 'namespace="vpp", container="ingestion"'
QUERIES: Mapping[str, str] = {
    "stored_rate_by_site": 'sum by (site) (rate(vpp_ingest_messages_total{result="stored"}[1m]))',
    "rejects_by_reason_firmware": "sum by (reason, firmware) (rate(vpp_ingest_rejects_total[5m]))",
    "devices_expected": "vpp_devices_expected",
    "devices_reporting": "vpp_devices_reporting",
    "devices_missing_by_group": (
        "sum by (site, device_type) (vpp_devices_expected - vpp_devices_reporting)"
    ),
    "mean_lag_seconds": (
        "sum(rate(vpp_ingest_lag_seconds_sum[1m])) / sum(rate(vpp_ingest_lag_seconds_count[1m]))"
    ),
    "memory_working_set_bytes": f"container_memory_working_set_bytes{{{INGESTION}}}",
    "cpu_throttled_share": (
        f"rate(container_cpu_cfs_throttled_periods_total{{{INGESTION}}}[1m])"
        f" / rate(container_cpu_cfs_periods_total{{{INGESTION}}}[1m])"
    ),
    "container_restarts": f"kube_pod_container_status_restarts_total{{{INGESTION}}}",
}


@dataclass(frozen=True)
class ContextDeps:
    prometheus: httpx.AsyncClient
    loki: httpx.AsyncClient
    store: IncidentStore
    runbooks_dir: Path
    salt: bytes | None = None


@dataclass(frozen=True)
class Timing:
    alert_started_at: datetime
    window_start: datetime
    built_at: datetime


@dataclass(frozen=True)
class MetricSnapshot:
    name: str
    expr: str
    before: tuple[Sample, ...]
    now: tuple[Sample, ...]
    error: str | None = None


@dataclass(frozen=True)
class GroupImpact:
    site: str
    device_type: str
    expected: int
    reporting: int

    @property
    def missing(self) -> int:
        return self.expected - self.reporting


@dataclass(frozen=True)
class BlastRadius:
    groups: tuple[GroupImpact, ...]
    surge_site: str | None

    @property
    def devices_affected(self) -> int:
        return sum(g.missing for g in self.groups)


@dataclass(frozen=True)
class IncidentContext:
    alert: Alert
    timing: Timing
    metrics: tuple[MetricSnapshot, ...]
    blast_radius: BlastRadius
    severity: str
    memory: tuple[Incident, ...]
    logs: tuple[str, ...]
    runbook_md: str
    salt: bytes | None = None


async def build_context(alert: Alert, deps: ContextDeps, now: datetime) -> IncidentContext:
    timing = Timing(alert.starts_at, alert.starts_at - WINDOW_BEFORE_ALERT, now)
    metrics = tuple([await _snapshot(deps, name, expr, timing) for name, expr in QUERIES.items()])
    return IncidentContext(
        alert=alert,
        timing=timing,
        metrics=metrics,
        blast_radius=_blast_radius(metrics),
        severity=alert.severity,
        memory=await deps.store.recent_like(alert.name),
        logs=await _logs(deps, timing),
        runbook_md=_runbook(deps.runbooks_dir, alert.name),
        salt=deps.salt,
    )


async def _snapshot(deps: ContextDeps, name: str, expr: str, timing: Timing) -> MetricSnapshot:
    try:
        before = await prometheus.instant(deps.prometheus, expr, at=timing.window_start)
        now = await prometheus.instant(deps.prometheus, expr, at=timing.built_at)
    except ClientError as exc:
        return MetricSnapshot(name, expr, (), (), error=str(exc))
    return MetricSnapshot(name, expr, before, now)


async def _logs(deps: ContextDeps, timing: Timing) -> tuple[str, ...]:
    try:
        lines = await loki.query_range(
            deps.loki, LOG_QUERY, timing.window_start, timing.built_at, LOG_LIMIT
        )
    except ClientError as exc:
        return (f"(logs unavailable: {sanitize_text(str(exc), deps.salt)})",)
    return _group_lines(tuple(sanitize_text(ln.line, deps.salt) for ln in lines))


def _group_lines(lines: tuple[str, ...], samples_per_group: int = 2) -> tuple[str, ...]:
    """Collapse near-identical JSON lines: two samples and a count per event/reason/firmware."""
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for line in lines:
        key = _group_key(line)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(line)
    out: list[str] = []
    for key in order:
        members = groups[key]
        out.extend(members[:samples_per_group])
        if len(members) > samples_per_group:
            out.append(f"({len(members)} similar lines: {key})")
    return tuple(out)


def _group_key(line: str) -> str:
    try:
        data = json.loads(line)
    except ValueError:
        return line[:80]
    if not isinstance(data, dict) or "event" not in data:
        return line[:80]
    parts = [str(data.get(k)) for k in ("event", "reason", "firmware", "level") if data.get(k)]
    return " ".join(parts)


def _runbook(directory: Path, alert_name: str) -> str:
    filename = RUNBOOKS.get(alert_name)
    path = directory / filename if filename else None
    if path is None or not path.exists():
        return f"(no runbook is mapped to {alert_name})"
    return path.read_text()


def _blast_radius(metrics: tuple[MetricSnapshot, ...]) -> BlastRadius:
    by_name = {m.name: m for m in metrics}
    expected = {_group(s): int(s.value) for s in by_name["devices_expected"].now}
    reporting = {_group(s): int(s.value) for s in by_name["devices_reporting"].now}
    groups = tuple(
        GroupImpact(site, device_type, total, reporting.get((site, device_type), 0))
        for (site, device_type), total in sorted(expected.items())
        if total - reporting.get((site, device_type), 0) > 0
    )
    return BlastRadius(groups, _surge_site(by_name["stored_rate_by_site"]))


def _group(sample: Sample) -> tuple[str, str]:
    return sample.labels.get("site", "-"), sample.labels.get("device_type", "-")


def _surge_site(stored: MetricSnapshot) -> str | None:
    """The site whose stored rate rose most across the window, if any rose at all."""
    before = {s.labels.get("site", "-"): s.value for s in stored.before}
    rises = {
        s.labels.get("site", "-"): s.value - before.get(s.labels.get("site", "-"), 0.0)
        for s in stored.now
    }
    if not rises:
        return None
    site, rise = max(rises.items(), key=lambda item: item[1])
    return site if rise > 0 else None


def render_context(ctx: IncidentContext) -> str:
    """The Markdown the model reads as its first user message, sanitised as a whole."""
    return sanitize_text(_render(ctx), ctx.salt)


def _render(ctx: IncidentContext) -> str:
    lines = [
        f"# Alert: {ctx.alert.name}",
        "",
        f"Labels: {_fmt_labels(ctx.alert.labels)}",
        f"Summary: {ctx.alert.annotations.get('summary', '')}",
        "",
        "## Timing",
        f"- Alert started: {ctx.timing.alert_started_at.isoformat()}",
        f"- Context window: {ctx.timing.window_start.isoformat()}"
        f" to {ctx.timing.built_at.isoformat()}",
        "",
        "## Blast radius",
        *_blast_radius_lines(ctx.blast_radius),
        "",
        "## Severity",
        f"- {ctx.severity}",
        "",
        "## Seen before",
        *_memory_lines(ctx.memory),
        "",
        "## Metrics",
        *_metric_lines(ctx.metrics),
        "",
        "## Logs",
        *(f"- {line}" for line in ctx.logs[:LOG_LIMIT]),
        "" if ctx.logs else "- (no warning or error lines in the window)",
        "",
        "## Runbook",
        ctx.runbook_md,
    ]
    return "\n".join(lines)


def _fmt_labels(labels: Mapping[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(labels.items()))


def _blast_radius_lines(radius: BlastRadius) -> list[str]:
    lines = [
        f"- {g.site}/{g.device_type}: {g.missing} of {g.expected} missing" for g in radius.groups
    ]
    if radius.surge_site:
        lines.append(f"- Stored message rate rose most at: {radius.surge_site}")
    return lines or ["- No device group is missing devices"]


def _memory_lines(memory: tuple[Incident, ...]) -> list[str]:
    if not memory:
        return ["- No earlier incident with this alert name"]
    return [
        f"- #{i.id} started {i.started_at.isoformat()}, outcome: {i.outcome or 'unknown'};"
        f" actions: {', '.join(a.get('tool', '?') for a in i.actions_taken) or 'none'}"
        for i in memory
    ]


def _metric_lines(metrics: tuple[MetricSnapshot, ...]) -> list[str]:
    lines = []
    for m in metrics:
        if m.error:
            lines.append(f"- {m.name}: unavailable ({m.error})")
            continue
        lines.append(f"- {m.name} (`{m.expr}`)")
        lines.append(f"  - at window start: {_fmt_samples(m.before)}")
        lines.append(f"  - now: {_fmt_samples(m.now)}")
    return lines


def _fmt_samples(samples: tuple[Sample, ...]) -> str:
    if not samples:
        return "no data"
    return "; ".join(f"{_fmt_labels(s.labels) or '-'} = {s.value:g}" for s in samples)
