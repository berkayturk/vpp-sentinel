"""The poller: alerts become incidents, incidents end, failures never stop the loop."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from agent.api import create_app
from agent.config import Settings, agent_enabled
from agent.fingerprint import Alert
from agent.memory import Incident
from agent.policy import PathGuard, load_allowlist
from agent.service import (
    OpenIncident,
    Page,
    PollFailed,
    ResolveIncident,
    ServiceDeps,
    decide,
    handle_events,
    poll,
    resolution_outcome,
)
from tests.fakes import (
    ALERTMANAGER_ALERTS,
    FakeIncidentStore,
    FakeWorkspace,
    Recorder,
    ScriptedByName,
    client,
    failing_router,
    json_router,
    prometheus_router,
)

NOW = datetime(2026, 11, 1, 10, 12, tzinfo=UTC)
DARK = Alert(
    name="VppSiteDevicesDark",
    labels={
        "alertname": "VppSiteDevicesDark",
        "site": "como",
        "device_type": "charger",
        "severity": "critical",
    },
    annotations={"summary": "como: 40% of chargers have not reported for 60 s"},
    starts_at=NOW - timedelta(minutes=3),
)
LAG = Alert(
    name="VppIngestionLagHigh",
    labels={"alertname": "VppIngestionLagHigh", "severity": "warning"},
    annotations={},
    starts_at=NOW - timedelta(minutes=2),
)
FORBIDDEN = ("allowlist.yaml", "services/agent/", ".github/", "CODEOWNERS")


def _incident(alert: Alert, incident_id: int = 1, actions=()) -> Incident:
    return Incident(
        id=incident_id,
        fingerprint="|".join(
            (alert.name, alert.labels.get("site", "-"), alert.labels.get("device_type", "-"))
        ),
        alert_name=alert.name,
        site=alert.labels.get("site"),
        device_type=alert.labels.get("device_type"),
        severity=alert.severity,
        started_at=alert.starts_at,
        resolved_at=None,
        report_md="",
        actions_taken=tuple(actions),
        outcome=None,
    )


def test_firing_alert_without_incident_opens_one():
    events = decide(active=(LAG,), open_incidents=(), now=NOW)
    assert events == (OpenIncident(LAG),)


def test_alert_with_an_open_incident_does_nothing():
    events = decide(active=(LAG,), open_incidents=(_incident(LAG),), now=NOW)
    assert events == ()


def test_open_incident_whose_alert_is_gone_is_resolved():
    events = decide(active=(), open_incidents=(_incident(LAG),), now=NOW)
    assert len(events) == 1 and isinstance(events[0], ResolveIncident)
    assert events[0].incident.id == 1 and events[0].outcome == "resolved"


def test_critical_alert_also_pages():
    events = decide(active=(DARK,), open_incidents=(), now=NOW)
    assert events == (Page(DARK), OpenIncident(DARK))


def test_resolution_after_a_recent_restart_says_mitigation_worked():
    restart = {
        "tool": "restart_deployment",
        "args": {"name": "ingestion"},
        "outcome": "ok",
        "at": (NOW - timedelta(minutes=2)).isoformat(),
    }
    assert "mitigation worked" in resolution_outcome(_incident(LAG, actions=[restart]), NOW)
    old = {**restart, "at": (NOW - timedelta(minutes=20)).isoformat()}
    assert resolution_outcome(_incident(LAG, actions=[old]), NOW) == "resolved"
    failed = {
        "tool": "agent",
        "args": {},
        "outcome": "error: RateLimitError",
        "at": NOW.isoformat(),
    }
    assert "after agent error" in resolution_outcome(_incident(LAG, actions=[failed]), NOW)


async def test_poll_turns_alertmanager_errors_into_an_event():
    store = FakeIncidentStore()
    deps = _deps(store, alertmanager=client(failing_router(Recorder())))
    events = await poll(deps, NOW)
    assert len(events) == 1 and isinstance(events[0], PollFailed)
    assert "500" in events[0].error


async def test_poll_reads_active_alerts_and_open_incidents():
    store = FakeIncidentStore()
    await store.open(LAG)
    deps = _deps(
        store, alertmanager=client(json_router({"/api/v2/alerts": ALERTMANAGER_ALERTS}, Recorder()))
    )
    events = await poll(deps, NOW)
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["Page", "OpenIncident", "ResolveIncident"]


async def test_handle_open_runs_the_agent_pages_and_annotates(tmp_path: Path):
    store = FakeIncidentStore()
    grafana_rec = Recorder()
    deps = _deps(
        store,
        grafana=client(json_router({"/api/annotations": {"id": 3}}, grafana_rec)),
        root=tmp_path,
    )
    await handle_events((OpenIncident(DARK), Page(DARK)), deps)
    opened = await store.find_open("VppSiteDevicesDark|como|charger")
    assert opened is not None and opened.report_md.startswith("# Incident report")
    assert deps.runner.calls == 1
    assert grafana_rec.paths() == ["POST /api/annotations"]
    assert deps.workspace_factory.prepared == ["vppsitedevicesdark-como-charger"]


async def test_handle_resolve_closes_the_incident():
    store = FakeIncidentStore()
    opened = await store.open(LAG)
    await handle_events((ResolveIncident(opened, "resolved"),), _deps(store))
    assert await store.find_open("VppIngestionLagHigh|-|-") is None
    assert store.rows[0].outcome == "resolved"


async def test_a_failing_handler_does_not_stop_the_others(tmp_path: Path):
    store = FakeIncidentStore()
    opened = await store.open(LAG)
    deps = _deps(store, root=tmp_path, prometheus=client(failing_router(Recorder())))
    deps.runner.explode = True
    await handle_events((OpenIncident(DARK), ResolveIncident(opened, "resolved")), deps)
    assert store.rows[0].outcome == "resolved"
    dark = await store.find_open("VppSiteDevicesDark|como|charger")
    assert dark is not None and dark.actions_taken[-1]["outcome"].startswith("error:")


def test_agent_is_disabled_without_an_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    settings = Settings.from_env()
    assert not agent_enabled(settings)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert agent_enabled(Settings.from_env())


async def test_healthz_is_ok_even_when_disabled():
    app = create_app(ready=lambda: True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        assert (await http.get("/healthz")).status_code == 200
        assert "vpp_agent_incidents_total" in (await http.get("/metrics")).text


# --- helpers ---------------------------------------------------------------------


class _WorkspaceFactory:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.prepared: list[str] = []

    def __call__(self, slug: str) -> FakeWorkspace:
        self.prepared.append(slug)
        ws = FakeWorkspace(self.root / slug, files={})
        return ws


def _deps(
    store: FakeIncidentStore,
    alertmanager: httpx.AsyncClient | None = None,
    grafana: httpx.AsyncClient | None = None,
    prometheus: httpx.AsyncClient | None = None,
    root: Path = Path("/tmp/agent-tests"),
) -> ServiceDeps:
    runbooks = root / "runbooks"
    runbooks.mkdir(parents=True, exist_ok=True)
    (runbooks / "site-devices-dark.md").write_text("# Runbook")
    runner = ScriptedByName(
        [
            ("query_prometheus", {"expr": "vpp_devices_reporting"}),
            (
                "file_report",
                dict(
                    summary="s",
                    blast_radius="b",
                    hypothesis="h",
                    evidence=["e"],
                    verification=["v"],
                    recommended_action="r",
                    seen_before=False,
                    mitigation_not_fix=False,
                    page_human=True,
                ),
            ),
        ]
    )
    return ServiceDeps(
        alertmanager=alertmanager or client(json_router({"/api/v2/alerts": []}, Recorder())),
        prometheus=prometheus
        or client(prometheus_router({"vpp_devices": [({"site": "como"}, 18.0)]}, Recorder())),
        loki=client(json_router({}, Recorder())),
        kube=client(json_router({}, Recorder())),
        github=client(json_router({}, Recorder())),
        grafana=grafana or client(json_router({"/api/annotations": {"id": 1}}, Recorder())),
        store=store,
        policy=load_allowlist(Path(__file__).resolve().parents[3] / "allowlist.yaml"),
        guard_factory=lambda ws_root: PathGuard(
            ws_root, allowed=("services/ingestion/",), forbidden=FORBIDDEN
        ),
        runner=runner,
        workspace_factory=_WorkspaceFactory(root),
        runbooks_dir=runbooks,
        repo="o/r",
        base_branch="main",
        salt=b"svc",
        now=lambda: NOW,
    )


def test_page_comes_before_the_model_run_and_duplicates_collapse():
    twin = Alert(name=DARK.name, labels=dict(DARK.labels), annotations={}, starts_at=NOW)
    events = decide(active=(DARK, twin), open_incidents=(), now=NOW)
    assert events == (Page(DARK), OpenIncident(DARK))


async def test_refused_or_failed_runs_page_a_human(tmp_path: Path):
    store = FakeIncidentStore()
    grafana_rec = Recorder()
    deps = _deps(
        store,
        grafana=client(json_router({"/api/annotations": {"id": 3}}, grafana_rec)),
        root=tmp_path,
    )
    deps.runner.explode = True
    await handle_events((OpenIncident(LAG),), deps)
    assert grafana_rec.paths() == ["POST /api/annotations"]


async def test_model_runs_are_bounded_by_a_wall_clock_timeout(tmp_path: Path):
    import asyncio

    from agent.loop import run_incident
    from agent.tools import ToolDeps, make_tools

    store = FakeIncidentStore()
    incident = await store.open(LAG)
    root = tmp_path / "ws"
    (root / "services/ingestion").mkdir(parents=True)
    deps = ToolDeps(
        prometheus=client(json_router({}, Recorder())),
        loki=client(json_router({}, Recorder())),
        kube=client(json_router({}, Recorder())),
        github=client(json_router({}, Recorder())),
        store=store,
        workspace=FakeWorkspace(root, files={}),
        policy=load_allowlist(Path(__file__).resolve().parents[3] / "allowlist.yaml"),
        guard=PathGuard(root, allowed=("services/ingestion/",), forbidden=FORBIDDEN),
        incident=incident,
        now=lambda: NOW,
        repo="o/r",
        salt=b"t",
    )
    from agent.context import BlastRadius, IncidentContext, Timing

    ctx = IncidentContext(
        alert=LAG,
        timing=Timing(NOW, NOW, NOW),
        metrics=(),
        blast_radius=BlastRadius((), None),
        severity="warning",
        memory=(),
        logs=(),
        runbook_md="",
    )

    class Slow:
        async def run(self, system, user, tools, max_iterations):
            await asyncio.sleep(5)

    outcome = await run_incident(ctx, make_tools(deps), Slow(), store, timeout_seconds=0.1)
    assert outcome.status == "error"
    assert "timed out" in store.rows[0].actions_taken[-1]["outcome"]


def test_settings_read_the_test_runner_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.delenv("TEST_RUNNER_URL", raising=False)
    assert Settings.from_env().test_runner_url is None
    monkeypatch.setenv("TEST_RUNNER_URL", "http://127.0.0.1:8002")
    assert Settings.from_env().test_runner_url == "http://127.0.0.1:8002"
