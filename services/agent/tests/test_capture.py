"""The capture protocol: pure KPI maths over observations, honest gaps, and fault clean-up."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from agent.capture import (
    CaptureDeps,
    Observations,
    PrerequisiteError,
    ProtocolError,
    ProtocolInfo,
    ScenarioObs,
    check_prerequisites,
    compute_kpis,
    render_kpis_md,
    render_sample_report_md,
    run_protocol,
)
from agent.fingerprint import Alert
from agent.memory import Incident
from tests.fakes import FakeIncidentStore, Recorder, client, json_router, prometheus_router

T = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
PROTOCOL = ProtocolInfo(date="2026-11-02", model="claude-opus-5-5", agent_image="vpp-agent:dev")


def _s(seconds: float | None) -> datetime | None:
    return None if seconds is None else T + timedelta(seconds=seconds)


def _incident(
    report_md: str = "# Incident report\n\n## Summary\nx", incident_id: int = 2
) -> Incident:
    return Incident(
        id=incident_id,
        fingerprint="VppSiteDevicesDark|como|charger",
        alert_name="VppSiteDevicesDark",
        site="como",
        device_type="charger",
        severity="critical",
        started_at=T,
        resolved_at=None,
        report_md=report_md,
        actions_taken=(),
        outcome=None,
    )


def _observations(**overrides) -> Observations:
    fields = dict(
        surge_injected=0,
        lag_active=150,
        lag_opened=160,
        restart_at=180,
        lag_report=220,
        lag_resolved=260,
        drift_injected=600,
        dark_active=820,
        dark_opened=830,
        pr_at=920,
        dark_report=940,
        tokens={"input": 700_000, "output": 24_000, "cache_read": 0},
        tool_calls=36,
        refusals=0,
    )
    fields.update(overrides)
    surge = ScenarioObs(
        name="load-surge",
        injected_at=_s(fields["surge_injected"]),
        alert_active_at=_s(fields["lag_active"]),
        opened_at=_s(fields["lag_opened"]),
        action_at=_s(fields["restart_at"]),
        report_at=_s(fields["lag_report"]),
        resolved_at=_s(fields["lag_resolved"]),
        incident=None,
    )
    drift = ScenarioObs(
        name="firmware-drift",
        injected_at=_s(fields["drift_injected"]),
        alert_active_at=_s(fields["dark_active"]),
        opened_at=_s(fields["dark_opened"]),
        action_at=_s(fields["pr_at"]),
        report_at=_s(fields["dark_report"]),
        resolved_at=None,
        incident=_incident(),
    )
    return Observations(
        surge=surge,
        drift=drift,
        tokens=fields["tokens"],
        tool_calls=fields["tool_calls"],
        refusals=fields["refusals"],
        alerts_fired=2,
        actions_taken=2,
    )


def test_kpis_from_observations():
    table = compute_kpis(_observations())
    assert table.rows["load-surge"].detection_s == 150
    assert table.rows["load-surge"].triage_s == 10
    assert table.rows["load-surge"].action_s == 30
    assert table.rows["load-surge"].report_s == 70
    assert table.rows["load-surge"].resolution_s == 110
    assert table.rows["firmware-drift"].detection_s == 220
    assert table.rows["firmware-drift"].pull_request_s == 100
    assert table.rows["firmware-drift"].report_s == 120
    assert table.cost_usd == pytest.approx(700_000 * 4 / 1e6 + 24_000 * 20 / 1e6)
    assert table.alert_to_action_ratio == "2/2"
    assert table.complete is True


def test_unmeasurable_kpi_is_named_not_invented():
    table = compute_kpis(_observations(pr_at=None))
    assert table.rows["firmware-drift"].pull_request_s is None
    assert table.complete is False
    assert "not measured" in render_kpis_md(table, PROTOCOL)


def test_render_kpis_md_states_protocol_prices_and_precision():
    text = render_kpis_md(compute_kpis(_observations()), PROTOCOL)
    for needle in (
        "claude-opus-5-5",
        "4 USD",
        "20 USD",
        "±5 s",
        "make down",
        "make demo",
        "| Detection",
    ):
        assert needle in text
    assert "150 s" in text and "2/2" in text


def test_sample_report_is_quoted_as_agent_output():
    text = render_sample_report_md(_incident())
    assert text.startswith("> This is the agent's own report")
    assert "## Summary" in text


def _deps(
    store: FakeIncidentStore | None = None,
    secret_keys: frozenset[str] = frozenset({"ANTHROPIC_API_KEY", "GITHUB_TOKEN"}),
    active_alerts: list | None = None,
    agent_ready: bool = True,
    alert_never_fires: bool = False,
    timeout_seconds: float = 0.05,
) -> CaptureDeps:
    faults = FakeFaults()
    am = client(json_router({"/api/v2/alerts": active_alerts or []}, Recorder()))
    prom_answers = (
        {} if alert_never_fires else {"ALERTS_FOR_STATE": [({"alertname": "x"}, T.timestamp())]}
    )
    prom = client(prometheus_router(prom_answers, Recorder()))
    return CaptureDeps(
        faults=faults,
        alertmanager=am,
        prometheus=prom,
        store=store or FakeIncidentStore(),
        secret_keys=secret_keys,
        agent_ready=agent_ready,
        now=lambda: datetime.now(UTC),
        poll_seconds=0.0,
        scenario_timeout_seconds=timeout_seconds,
        quiet_seconds=0.0,
    )


class FakeFaults:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def post(self, name: str, body: dict) -> None:
        self.calls.append(("POST", f"/faults/{name}"))

    async def clear(self) -> None:
        self.calls.append(("DELETE", "/faults"))


async def test_prerequisite_check_refuses_without_credentials():
    with pytest.raises(PrerequisiteError, match="agent-credentials"):
        await check_prerequisites(_deps(secret_keys=frozenset()))


async def test_prerequisite_check_refuses_with_an_active_alert_or_open_incident():
    alerts = [
        {
            "labels": {"alertname": "VppIngestionLagHigh"},
            "annotations": {},
            "startsAt": "2026-11-02T09:00:00Z",
            "status": {"state": "active"},
        }
    ]
    with pytest.raises(PrerequisiteError, match="alert"):
        await check_prerequisites(_deps(active_alerts=alerts))
    store = FakeIncidentStore()
    await store.open(Alert(name="X", labels={"alertname": "X"}, annotations={}, starts_at=T))
    with pytest.raises(PrerequisiteError, match="open incident"):
        await check_prerequisites(_deps(store=store))
    with pytest.raises(PrerequisiteError, match="agent"):
        await check_prerequisites(_deps(agent_ready=False))


async def test_protocol_clears_faults_on_failure():
    deps = _deps(alert_never_fires=True)
    with pytest.raises(ProtocolError):
        await run_protocol(deps)
    assert deps.faults.calls[0] == ("POST", "/faults/load-surge")
    assert deps.faults.calls[-1] == ("DELETE", "/faults")


def test_observations_are_immutable():
    obs = _observations()
    with pytest.raises((AttributeError, TypeError)):
        obs.tool_calls = 1  # type: ignore[misc]
    assert replace(obs, tool_calls=1).tool_calls == 1
