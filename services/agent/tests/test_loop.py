"""The model loop with an injected runner: report filed, incomplete, or error — never a crash."""

from datetime import UTC, datetime
from pathlib import Path

import anthropic
import httpx
from prometheus_client import REGISTRY

from agent.context import BlastRadius, IncidentContext, Timing
from agent.fingerprint import Alert
from agent.loop import AnthropicRunner, RunResult, Usage, load_system_prompt, run_incident
from agent.policy import PathGuard, load_allowlist
from agent.tools import TOOL_NAMES, ToolDeps, make_tools
from tests.fakes import (
    FakeIncidentStore,
    FakeWorkspace,
    Recorder,
    ScriptedRunner,
    client,
    json_router,
    prometheus_router,
)

NOW = datetime(2026, 11, 1, 10, 12, tzinfo=UTC)
LAG = Alert(
    name="VppIngestionLagHigh",
    labels={"alertname": "VppIngestionLagHigh", "severity": "warning"},
    annotations={"summary": "Telemetry is arriving late"},
    starts_at=NOW,
)
FORBIDDEN = ("allowlist.yaml", "services/agent/", ".github/", "CODEOWNERS")


def _sample(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def _setup(tmp_path: Path):
    store = FakeIncidentStore()
    incident = await store.open(LAG)
    root = tmp_path / "ws"
    (root / "services/ingestion").mkdir(parents=True)
    kube = json_router(
        {
            "/apis/apps/v1/namespaces/vpp/deployments/ingestion": lambda r: httpx.Response(
                200, json={}
            )
        },
        Recorder(),
    )
    deps = ToolDeps(
        prometheus=client(prometheus_router({"lag": [({}, 45.0)]}, Recorder())),
        loki=client(json_router({}, Recorder())),
        kube=client(kube),
        github=client(json_router({}, Recorder())),
        store=store,
        workspace=FakeWorkspace(root, files={}),
        policy=load_allowlist(Path(__file__).resolve().parents[3] / "allowlist.yaml"),
        guard=PathGuard(root, allowed=("services/ingestion/",), forbidden=FORBIDDEN),
        incident=incident,
        now=lambda: NOW,
        repo="o/r",
        salt=b"loop-salt",
    )
    ctx = IncidentContext(
        alert=LAG,
        timing=Timing(NOW, NOW, NOW),
        metrics=(),
        blast_radius=BlastRadius((), "lugano-south"),
        severity="warning",
        memory=(),
        logs=("line",),
        runbook_md="# Runbook",
    )
    return store, incident, make_tools(deps), ctx


REPORT_ARGS = dict(
    summary="lag rose after a surge at lugano-south",
    blast_radius="fleet-wide staleness; surge at lugano-south",
    hypothesis="single consumer saturated under its CPU limit",
    evidence=["mean lag 45 s"],
    verification=["checked memory and restarts"],
    recommended_action="review the CPU limit",
    seen_before=True,
    seen_before_reference="#1 on 2026-10-18",
    mitigation_not_fix=True,
    page_human=False,
)


async def test_scripted_run_files_a_report_with_actions_from_the_log(tmp_path: Path):
    store, incident, toolset, ctx = await _setup(tmp_path)
    runner = ScriptedRunner(
        toolset,
        [
            ("query_prometheus", {"expr": "lag", "minutes_back": 10}),
            ("lookup_incident_memory", {"alertname": "VppIngestionLagHigh"}),
            ("restart_deployment", {"name": "ingestion"}),
            ("file_report", REPORT_ARGS),
        ],
        final_text="Report filed.",
    )
    before = _sample(
        "vpp_agent_incidents_total", {"alertname": "VppIngestionLagHigh", "outcome": "reported"}
    )
    outcome = await run_incident(ctx, toolset, runner, store)
    assert outcome.status == "reported"
    assert outcome.report is not None
    assert [a.tool for a in outcome.report.actions_taken] == ["restart_deployment"]
    assert outcome.report.actions_taken[0].outcome == "ok"
    stored = await store.find_open("VppIngestionLagHigh|-|-")
    assert "# Incident report" in stored.report_md and "mitigation, not a fix" in stored.report_md
    assert (
        _sample(
            "vpp_agent_incidents_total", {"alertname": "VppIngestionLagHigh", "outcome": "reported"}
        )
        == before + 1
    )
    assert _sample("vpp_agent_model_tokens_total", {"kind": "output"}) >= 1
    assert runner.received_system.startswith("You are")
    assert "# Alert: VppIngestionLagHigh" in runner.received_user
    assert set(runner.received_tool_names) == set(TOOL_NAMES)


async def test_run_without_a_report_is_incomplete_and_keeps_the_text(tmp_path: Path):
    store, incident, toolset, ctx = await _setup(tmp_path)
    runner = ScriptedRunner(
        toolset, [("query_prometheus", {"expr": "lag"})], final_text="I ran out of steps."
    )
    outcome = await run_incident(ctx, toolset, runner, store)
    assert outcome.status == "incomplete"
    stored = await store.find_open("VppIngestionLagHigh|-|-")
    assert stored.report_md.startswith("# Incident report (incomplete)")
    assert "I ran out of steps." in stored.report_md


async def test_model_api_errors_are_recorded_not_raised(tmp_path: Path):
    store, incident, toolset, ctx = await _setup(tmp_path)

    class Exploding:
        async def run(self, system, user, tools, max_iterations):
            request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.RateLimitError(
                "rate limited", response=httpx.Response(429, request=request), body=None
            )

    outcome = await run_incident(ctx, toolset, Exploding(), store)
    assert outcome.status == "error"
    stored = await store.find_open("VppIngestionLagHigh|-|-")
    assert stored.actions_taken[-1]["tool"] == "agent"
    assert stored.actions_taken[-1]["outcome"].startswith("error:")
    assert "rate limited" in stored.actions_taken[-1]["outcome"]


def test_system_prompt_states_the_policy_without_naming_the_defect():
    text = load_system_prompt()
    assert "Tier 1" in text and "Tier 2" in text and "Tier 3" in text
    for name in TOOL_NAMES:
        assert name in text
    for forbidden in ("epoch", "milliseconds", "timestamp format"):
        assert forbidden not in text.lower()
    assert "file_report" in text and "never merge" in text.lower()


async def test_anthropic_runner_sums_usage_and_extracts_the_final_text():
    class Msg:
        def __init__(self, text: str, inp: int, out: int, cached: int) -> None:
            self.content = [type("Block", (), {"type": "text", "text": text})()]
            self.usage = type(
                "U",
                (),
                {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cached},
            )()

    class FakeRunner:
        def __init__(self, msgs):
            self._msgs = msgs

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._msgs:
                raise StopAsyncIteration
            return self._msgs.pop(0)

    captured = {}

    class Messages:
        def tool_runner(self, **kwargs):
            captured.update(kwargs)
            return FakeRunner([Msg("step one", 100, 10, 0), Msg("final answer", 120, 20, 90)])

    class Beta:
        messages = Messages()

    class Client:
        beta = Beta()

    runner = AnthropicRunner(Client(), model="claude-opus-5-5", effort="high")
    result = await runner.run("sys", "user", tools=[], max_iterations=7)
    assert isinstance(result, RunResult)
    assert result.final_text == "final answer"
    assert result.usage == Usage(input_tokens=220, output_tokens=30, cache_read_tokens=90)
    assert result.iterations == 2
    assert captured["model"] == "claude-opus-5-5" and captured["max_iterations"] == 7
    assert captured["system"] == "sys" and captured["output_config"] == {"effort": "high"}
    assert captured["messages"] == [{"role": "user", "content": "user"}]
