"""The read-only context packet: the five items of spec §7.2, built in code."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent.context import ContextDeps, build_context, render_context
from agent.fingerprint import Alert
from agent.memory import Incident
from tests.fakes import (
    FakeIncidentStore,
    Recorder,
    client,
    json_line,
    json_router,
    loki_lines,
    prometheus_router,
)

NOW = datetime(2026, 11, 1, 10, 12, tzinfo=UTC)
STARTED = datetime(2026, 11, 1, 10, 8, tzinfo=UTC)
SALT = b"ctx-salt"

DARK = Alert(
    name="VppSiteDevicesDark",
    labels={
        "alertname": "VppSiteDevicesDark",
        "site": "como",
        "device_type": "charger",
        "severity": "critical",
    },
    annotations={"summary": "como: 40% of chargers have not reported for 60 s"},
    starts_at=STARTED,
)
LAG = Alert(
    name="VppIngestionLagHigh",
    labels={"alertname": "VppIngestionLagHigh", "severity": "warning"},
    annotations={"summary": "Telemetry is arriving late: mean ingestion lag is 45 s"},
    starts_at=STARTED,
)

GAUGES = {
    "vpp_devices_expected": [
        ({"site": "como", "device_type": "charger"}, 30.0),
        ({"site": "varese", "device_type": "battery"}, 15.0),
    ],
    "vpp_devices_reporting": [
        ({"site": "como", "device_type": "charger"}, 18.0),
        ({"site": "varese", "device_type": "battery"}, 15.0),
    ],
    "rate(vpp_ingest_messages_total": [
        ({"site": "como"}, 12.0),
        ({"site": "lugano-south"}, 600.0),
    ],
    "vpp_ingest_rejects_total": [({"reason": "invalid_timestamp", "firmware": "2.4.0"}, 2.1)],
    "vpp_ingest_lag_seconds": [({}, 45.0)],
    "container_memory_working_set_bytes": [({}, 80_000_000.0)],
    "restarts": [({}, 0.0)],
    "cfs_throttled": [({}, 0.9)],
}

REJECTED = json_line(
    event="telemetry.rejected",
    reason="invalid_timestamp",
    firmware="2.4.0",
    topic="vpp/como/charger/como-charger-012/telemetry",
    level="warning",
)
DISCONNECTED = json_line(
    event="mqtt.disconnected",
    error="push ghp_abcdef1234567890 failed from 10.244.0.9",
    level="warning",
)
LOGS = [(1761991680, REJECTED), (1761991690, DISCONNECTED)]

SEEDED = Incident(
    id=1,
    fingerprint="VppIngestionLagHigh|-|-",
    alert_name="VppIngestionLagHigh",
    site=None,
    device_type=None,
    severity="warning",
    started_at=datetime(2026, 10, 18, 9, 12, tzinfo=UTC),
    resolved_at=datetime(2026, 10, 18, 9, 18, 40, tzinfo=UTC),
    report_md="# old",
    actions_taken=(
        {
            "tool": "restart_deployment",
            "args": {"name": "ingestion"},
            "outcome": "ok",
            "at": "2026-10-18T09:16:05+00:00",
        },
    ),
    outcome="resolved: mitigation worked, lag drained in 70 s",
)


@pytest.fixture
def runbooks(tmp_path: Path) -> Path:
    d = tmp_path / "runbooks"
    d.mkdir()
    (d / "site-devices-dark.md").write_text(
        "# Runbook: VppSiteDevicesDark\n\nCheck rejects by firmware."
    )
    (d / "ingestion-lag-high.md").write_text(
        "# Runbook: VppIngestionLagHigh\n\nRestart is a mitigation."
    )
    return d


HEALTHY = {
    **GAUGES,
    "vpp_devices_reporting": GAUGES["vpp_devices_expected"],
    "rate(vpp_ingest_messages_total": [({"site": "como"}, 12.0), ({"site": "lugano-south"}, 12.0)],
}


async def _deps(
    runbooks: Path, store: FakeIncidentStore, now_answers: dict | None = None
) -> ContextDeps:
    # Queries at the window start see a healthy fleet; queries at "now" see the incident.
    prom = client(
        prometheus_router(
            now_answers or GAUGES, Recorder(), before=HEALTHY, cutoff=STARTED.timestamp()
        )
    )
    lk = client(json_router({"/loki/api/v1/query_range": loki_lines(LOGS)}, Recorder()))
    return ContextDeps(prometheus=prom, loki=lk, store=store, runbooks_dir=runbooks, salt=SALT)


async def test_dark_alert_blast_radius_lists_only_the_missing_group(runbooks: Path):
    ctx = await build_context(DARK, await _deps(runbooks, FakeIncidentStore()), NOW)
    groups = [
        (g.site, g.device_type, g.expected, g.reporting, g.missing) for g in ctx.blast_radius.groups
    ]
    assert groups == [("como", "charger", 30, 18, 12)]
    assert ctx.blast_radius.devices_affected == 12
    assert ctx.severity == "critical"


async def test_lag_alert_names_the_surge_site(runbooks: Path):
    surge = {**HEALTHY, "rate(vpp_ingest_messages_total": GAUGES["rate(vpp_ingest_messages_total"]}
    ctx = await build_context(LAG, await _deps(runbooks, FakeIncidentStore(), surge), NOW)
    assert ctx.blast_radius.surge_site == "lugano-south"
    assert ctx.blast_radius.groups == ()


async def test_memory_holds_prior_incidents_of_the_same_alert(runbooks: Path):
    ctx = await build_context(LAG, await _deps(runbooks, FakeIncidentStore(seeded=(SEEDED,))), NOW)
    assert [i.id for i in ctx.memory] == [1]
    other = await build_context(
        DARK, await _deps(runbooks, FakeIncidentStore(seeded=(SEEDED,))), NOW
    )
    assert other.memory == ()


async def test_timing_window_starts_ten_minutes_before_the_alert(runbooks: Path):
    ctx = await build_context(DARK, await _deps(runbooks, FakeIncidentStore()), NOW)
    assert ctx.timing.alert_started_at == STARTED
    assert ctx.timing.window_start == datetime(2026, 11, 1, 9, 58, tzinfo=UTC)
    assert ctx.timing.built_at == NOW


async def test_logs_are_sanitised(runbooks: Path):
    ctx = await build_context(DARK, await _deps(runbooks, FakeIncidentStore()), NOW)
    joined = "\n".join(ctx.logs)
    assert "como-charger-012" not in joined and "como-charger-#" in joined
    assert "ghp_" not in joined and "10.244.0.9" not in joined
    assert "invalid_timestamp" in joined


async def test_runbook_text_is_attached_by_alert_name(runbooks: Path):
    dark = await build_context(DARK, await _deps(runbooks, FakeIncidentStore()), NOW)
    lag = await build_context(LAG, await _deps(runbooks, FakeIncidentStore()), NOW)
    assert dark.runbook_md.startswith("# Runbook: VppSiteDevicesDark")
    assert lag.runbook_md.startswith("# Runbook: VppIngestionLagHigh")


async def test_render_context_has_every_section_and_no_secret(runbooks: Path):
    store = FakeIncidentStore(seeded=(SEEDED,))
    ctx = await build_context(DARK, await _deps(runbooks, store), NOW)
    text = render_context(ctx)
    headings = ("## Timing", "## Blast radius", "## Severity", "## Seen before", "## Metrics")
    for heading in headings + ("## Logs", "## Runbook"):
        assert heading in text
    assert "como/charger: 12 of 30 missing" in text
    assert "ghp_" not in text and "como-charger-012" not in text
    assert "Check rejects by firmware." in text


async def test_metrics_snapshot_names_each_query(runbooks: Path):
    ctx = await build_context(DARK, await _deps(runbooks, FakeIncidentStore()), NOW)
    names = {m.name for m in ctx.metrics}
    expected = {
        "stored_rate_by_site",
        "rejects_by_reason_firmware",
        "devices_missing_by_group",
        "memory_working_set_bytes",
        "cpu_throttled_share",
        "container_restarts",
    }
    assert expected <= names
    rejects = next(m for m in ctx.metrics if m.name == "rejects_by_reason_firmware")
    assert rejects.now[0].labels["firmware"] == "2.4.0"


async def test_repeated_log_lines_are_grouped_with_counts(runbooks: Path):
    repeated = [
        (
            1761991600 + i,
            json_line(
                event="telemetry.rejected",
                reason="invalid_timestamp",
                firmware="2.4.0",
                topic=f"vpp/como/charger/como-charger-{i:03d}/telemetry",
                level="warning",
            ),
        )
        for i in range(40)
    ] + [(1761991700, DISCONNECTED)]
    store = FakeIncidentStore()
    prom = client(prometheus_router(GAUGES, Recorder(), before=HEALTHY, cutoff=STARTED.timestamp()))
    lk = client(json_router({"/loki/api/v1/query_range": loki_lines(repeated)}, Recorder()))
    deps = ContextDeps(prometheus=prom, loki=lk, store=store, runbooks_dir=runbooks, salt=SALT)
    ctx = await build_context(DARK, deps, NOW)
    assert len(ctx.logs) <= 6
    joined = "\n".join(ctx.logs)
    assert "40 similar lines" in joined and "telemetry.rejected" in joined
    assert "mqtt.disconnected" in joined
    assert "como-charger-0" not in joined


async def test_rendered_context_is_sanitised_everywhere(runbooks: Path):
    noisy = Alert(
        name="VppSiteDevicesDark",
        labels={
            "alertname": "VppSiteDevicesDark",
            "site": "como",
            "device_type": "charger",
            "severity": "critical",
            "instance": "10.244.0.7:8000",
            "pod": "ingestion-x",
        },
        annotations={"summary": "como-charger-012 is dark; see ghp_abcdefABCDEF123456"},
        starts_at=STARTED,
    )
    (runbooks / "site-devices-dark.md").write_text(
        "# Runbook\n\nssh 10.0.0.1 and check como-charger-003"
    )
    ctx = await build_context(noisy, await _deps(runbooks, FakeIncidentStore()), NOW)
    text = render_context(ctx)
    for leak in ("10.244.0.7", "como-charger-012", "ghp_", "10.0.0.1", "como-charger-003"):
        assert leak not in text
