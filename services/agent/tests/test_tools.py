"""The ten tools and the boundaries each one enforces in code."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from agent.fingerprint import Alert
from agent.policy import PathGuard, load_allowlist
from agent.tools import TOOL_NAMES, ToolDeps, make_tools
from tests.fakes import (
    FakeIncidentStore,
    FakeWorkspace,
    Recorder,
    client,
    json_router,
    loki_lines,
    prometheus_router,
)

NOW = datetime(2026, 11, 1, 10, 12, tzinfo=UTC)
SALT = b"tool-salt"
DARK = Alert(
    name="VppSiteDevicesDark",
    labels={
        "alertname": "VppSiteDevicesDark",
        "site": "como",
        "device_type": "charger",
        "severity": "critical",
    },
    annotations={},
    starts_at=NOW,
)
FORBIDDEN = ("allowlist.yaml", "services/agent/", ".github/", "CODEOWNERS")


def _github_router(rec: Recorder, existing_pr: dict | None = None) -> httpx.MockTransport:
    def pulls(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[existing_pr] if existing_pr else [])
        body = json.loads(request.read())
        return httpx.Response(
            201, json={"number": 42, "html_url": "https://github.com/o/r/pull/42", **body}
        )

    def pull_42(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"number": 42, "html_url": "https://github.com/o/r/pull/42"}
        )

    return json_router({"/repos/o/r/pulls": pulls, "/repos/o/r/pulls/42": pull_42}, rec)


def _kube_router(rec: Recorder) -> httpx.MockTransport:
    def patch(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "metadata": {"generation": 3},
                    "spec": {"replicas": 1},
                    "status": {
                        "observedGeneration": 3,
                        "updatedReplicas": 1,
                        "availableReplicas": 1,
                    },
                },
            )
        assert request.method == "PATCH"
        assert "restartedAt" in request.read().decode()
        return httpx.Response(200, json={"metadata": {"name": "ingestion", "generation": 3}})

    return json_router({"/apis/apps/v1/namespaces/vpp/deployments/ingestion": patch}, rec)


@pytest.fixture
def setup(tmp_path: Path):
    allow = tmp_path / "allowlist.yaml"
    allow.write_text(Path(__file__).resolve().parents[3].joinpath("allowlist.yaml").read_text())
    root = tmp_path / "ws"
    for d in (
        "services/ingestion/src/ingestion",
        "services/ingestion/tests",
        "services/agent/src/agent",
    ):
        (root / d).mkdir(parents=True)
    workspace = FakeWorkspace(
        root,
        files={
            "services/ingestion/src/ingestion/schema.py": (
                "def parse(raw):\n    return raw  # como-charger-012\n"
            ),
            "services/agent/src/agent/policy.py": "secret = 1\n",
        },
    )
    recs = {"kube": Recorder(), "github": Recorder(), "prom": Recorder()}
    store = FakeIncidentStore()
    return {
        "allow": load_allowlist(allow),
        "guard": PathGuard(root, allowed=("services/ingestion/",), forbidden=FORBIDDEN),
        "workspace": workspace,
        "store": store,
        "recs": recs,
        "deps_kw": dict(
            prometheus=client(
                prometheus_router({"vpp_devices": [({"site": "como"}, 18.0)]}, recs["prom"])
            ),
            loki=client(
                json_router(
                    {
                        "/loki/api/v1/query_range": loki_lines(
                            [
                                (
                                    1761991680,
                                    '{"device_id":"como-charger-012","t":"ghp_abcdefABCDEF123456"}',
                                )
                            ]
                        )
                    },
                    Recorder(),
                )
            ),
            kube=client(_kube_router(recs["kube"])),
            github=client(_github_router(recs["github"])),
        ),
    }


async def _toolset(setup, now=NOW, github: httpx.AsyncClient | None = None):
    incident = await setup["store"].open(DARK)
    deps = ToolDeps(
        **{**setup["deps_kw"], **({"github": github} if github else {})},
        store=setup["store"],
        workspace=setup["workspace"],
        policy=setup["allow"],
        guard=setup["guard"],
        incident=incident,
        now=lambda: now,
        repo="o/r",
        base_branch="main",
        salt=SALT,
    )
    return make_tools(deps)


def test_registry_is_exactly_the_ten_tools(setup):
    import asyncio

    toolset = asyncio.run(_toolset(setup))
    assert set(toolset.names()) == set(TOOL_NAMES)
    assert len(TOOL_NAMES) == 10
    for name in toolset.names():
        for banned in ("bash", "shell", "kubectl", "exec", "merge"):
            assert banned not in name


async def test_restart_refused_when_not_in_allowlist(setup):
    ts = await _toolset(setup)
    out = await ts.restart_deployment(name="postgres")
    assert out.startswith("REFUSED") and "allowlist" in out
    assert setup["recs"]["kube"].requests == []
    assert ts.log[-1].tool == "restart_deployment" and ts.log[-1].outcome.startswith("REFUSED")


async def test_restart_allowed_patches_the_deployment_and_is_recorded(setup):
    ts = await _toolset(setup)
    out = await ts.restart_deployment(name="ingestion")
    assert out.startswith("ok")
    assert [p for p in setup["recs"]["kube"].paths() if p.startswith("PATCH")] == [
        "PATCH /apis/apps/v1/namespaces/vpp/deployments/ingestion"
    ]
    stored = await setup["store"].find_open("VppSiteDevicesDark|como|charger")
    assert stored.actions_taken[-1]["tool"] == "restart_deployment"
    assert stored.actions_taken[-1]["outcome"] == "ok"


async def test_restart_refused_during_cooldown(setup):
    ts = await _toolset(setup)
    await ts.restart_deployment(name="ingestion")
    later = await _toolset(setup, now=NOW + timedelta(minutes=5))
    out = await later.restart_deployment(name="ingestion")
    assert out.startswith("REFUSED") and "cooldown" in out
    assert sum(1 for r in setup["recs"]["kube"].requests if r.method == "PATCH") == 1


async def test_read_source_file_is_scoped_and_sanitised(setup):
    ts = await _toolset(setup)
    assert (await ts.read_source_file(path="services/agent/src/agent/policy.py")).startswith(
        "REFUSED"
    )
    content = await ts.read_source_file(path="services/ingestion/src/ingestion/schema.py")
    assert "def parse" in content and "como-charger-012" not in content


async def test_list_source_files_is_scoped(setup):
    ts = await _toolset(setup)
    assert "schema.py" in await ts.list_source_files(directory="services/ingestion/src/ingestion")
    assert (await ts.list_source_files(directory="services/agent")).startswith("REFUSED")


async def test_write_source_file_is_scoped(setup):
    ts = await _toolset(setup)
    assert (await ts.write_source_file(path="allowlist.yaml", content="x")).startswith("REFUSED")
    assert (
        await ts.write_source_file(path="services/ingestion/tests/test_fix.py", content="x")
    ).startswith("ok")
    assert await setup["workspace"].changed_paths() == ("services/ingestion/tests/test_fix.py",)


async def test_pull_request_requires_passing_tests(setup):
    ts = await _toolset(setup)
    await ts.write_source_file(path="services/ingestion/tests/test_fix.py", content="x")
    assert (await ts.open_pull_request(title="fix", body="b")).startswith("REFUSED")
    setup["workspace"].tests_pass = False
    await ts.run_tests()
    assert "tests" in await ts.open_pull_request(title="fix", body="b")
    assert setup["recs"]["github"].requests == []


async def test_pull_request_refuses_forbidden_paths_before_pushing(setup):
    ts = await _toolset(setup)
    setup["workspace"].changed = ("services/ingestion/src/x.py", "allowlist.yaml")
    await ts.run_tests()
    out = await ts.open_pull_request(title="fix", body="b")
    assert out.startswith("REFUSED") and "allowlist.yaml" in out
    assert setup["workspace"].pushed == [] and setup["recs"]["github"].requests == []


async def test_pull_request_happy_path_then_update(setup):
    ts = await _toolset(setup)
    await ts.write_source_file(path="services/ingestion/tests/test_fix.py", content="x")
    await ts.run_tests()
    out = await ts.open_pull_request(
        title="Accept the new payload",
        body="Report\n\nCo-Authored-By: Robot <r@x>\nGenerated with Something",
    )
    assert out.startswith("ok") and "https://github.com/o/r/pull/42" in out
    assert setup["workspace"].pushed == [
        (
            "agent/vppsitedevicesdark-como-charger",
            "fix(ingestion): Accept the new payload",
            ("services/ingestion/tests/test_fix.py",),
        )
    ]
    paths = setup["recs"]["github"].paths()
    assert paths == ["GET /repos/o/r/pulls", "POST /repos/o/r/pulls"]
    posted = json.loads(setup["recs"]["github"].requests[1].read())
    assert posted["head"] == "agent/vppsitedevicesdark-como-charger" and posted["base"] == "main"
    assert "Co-Authored-By" not in posted["body"] and "Generated with" not in posted["body"]
    # second call: the PR exists → update, never a second POST
    rec2 = Recorder()
    existing = {"number": 42, "html_url": "https://github.com/o/r/pull/42"}
    ts2 = await _toolset(setup, github=client(_github_router(rec2, existing_pr=existing)))
    await ts2.run_tests()
    out2 = await ts2.open_pull_request(title="Accept the new payload", body="Report v2")
    assert out2.startswith("ok")
    assert rec2.paths() == ["GET /repos/o/r/pulls", "PATCH /repos/o/r/pulls/42"]


async def test_tool_results_are_sanitised(setup):
    ts = await _toolset(setup)
    logs = await ts.query_logs(logql='{app="ingestion"}', minutes_back=10, limit=10)
    assert "como-charger-012" not in logs and "ghp_" not in logs and "como-charger-#" in logs
    setup["workspace"].test_output = "FAILED token ghp_abcdefABCDEF123456 in como-charger-012"
    setup["workspace"].tests_pass = False
    out = await ts.run_tests()
    assert "ghp_" not in out and "como-charger-012" not in out


async def test_file_report_fills_actions_from_the_log_not_the_model(setup):
    ts = await _toolset(setup)
    await ts.restart_deployment(name="ingestion")
    out = await ts.file_report(
        summary="s",
        blast_radius="b",
        hypothesis="h",
        evidence=["e"],
        verification=["v"],
        recommended_action="r",
        seen_before=False,
        mitigation_not_fix=True,
        page_human=False,
    )
    assert out.startswith("ok")
    assert ts.report is not None
    assert [a.tool for a in ts.report.actions_taken] == ["restart_deployment"]
    assert ts.report.started_at == NOW
    stored = await setup["store"].find_open("VppSiteDevicesDark|como|charger")
    assert stored.report_md.startswith("# Incident")


async def test_lookup_incident_memory_and_prometheus_read(setup):
    ts = await _toolset(setup)
    mem = await ts.lookup_incident_memory(alertname="VppSiteDevicesDark")
    assert "No earlier" in mem
    prom = await ts.query_prometheus(expr="vpp_devices_reporting", minutes_back=10)
    assert "como" in prom and "18" in prom


async def test_client_errors_become_tool_errors_not_exceptions(setup):
    ts = await _toolset(setup, github=client(json_router({}, Recorder())))
    out = await ts.query_prometheus(expr="up", minutes_back=1_000_000)
    assert isinstance(out, str)
    assert (await ts.lookup_incident_memory(alertname="x")) is not None


async def test_every_tool_call_is_logged_as_an_event(setup):
    import structlog

    ts = await _toolset(setup)
    with structlog.testing.capture_logs() as logs:
        await ts.restart_deployment(name="postgres")
        await ts.read_source_file(path="services/ingestion/src/ingestion/schema.py")
    events = [(e["event"], e.get("tool"), e.get("outcome_class")) for e in logs]
    assert ("incident.tool_call", "restart_deployment", "refused") in events
    assert ("incident.tool_call", "read_source_file", "ok") in events
    assert all("como-charger-012" not in json.dumps(e) for e in logs)


async def test_restart_result_tells_whether_the_rollout_completed(setup):
    ts = await _toolset(setup)
    out = await ts.restart_deployment(name="ingestion")
    assert out.startswith("ok") and "rolled out" in out


async def test_filed_report_is_appended_to_the_open_pull_request(setup):
    rec = Recorder()
    ts = await _toolset(setup, github=client(_github_router(rec)))
    await ts.write_source_file(path="services/ingestion/tests/test_fix.py", content="x")
    await ts.run_tests()
    await ts.open_pull_request(title="Accept the new payload", body="Problem and change.")
    await ts.file_report(
        summary="s",
        blast_radius="b",
        hypothesis="h",
        evidence=["e"],
        verification=["v"],
        recommended_action="r",
        seen_before=False,
        mitigation_not_fix=False,
        page_human=True,
    )
    assert rec.paths()[-1] == "PATCH /repos/o/r/pulls/42"
    patched = json.loads(rec.requests[-1].read())
    assert patched["body"].startswith("Problem and change.")
    assert "# Incident report" in patched["body"] and "## Hypothesis" in patched["body"]
    assert ts.report is not None and ts.report.pull_request_url == "https://github.com/o/r/pull/42"


async def test_pull_request_text_and_appended_report_are_sanitised(setup):
    rec = Recorder()
    ts = await _toolset(setup, github=client(_github_router(rec)))
    await ts.write_source_file(path="services/ingestion/tests/test_fix.py", content="x")
    await ts.run_tests()
    await ts.open_pull_request(
        title="Fix como-charger-012 parsing", body="token ghp_abcdefABCDEF123456 from 10.244.0.9"
    )
    await ts.file_report(
        summary="device como-charger-012 rejected",
        blast_radius="b",
        hypothesis="h",
        evidence=["saw sk-ant-api03-secretsecret in a log"],
        verification=["v"],
        recommended_action="r\n\nCo-Authored-By: Robot <r@x>",
        seen_before=False,
        mitigation_not_fix=False,
        page_human=False,
    )
    sent = "\n".join(r.read().decode() for r in rec.requests if r.method in ("POST", "PATCH"))
    for leak in ("como-charger-012", "ghp_", "10.244.0.9", "sk-ant-", "Co-Authored-By"):
        assert leak not in sent
    assert "como-charger-#" in sent
