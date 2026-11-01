"""Test doubles shared across the agent's test modules.

HTTP services are faked with ``httpx.MockTransport`` routers that serve canned
JSON by path and record every request they saw.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

Handler = Callable[[httpx.Request], httpx.Response]


@dataclass
class Recorder:
    """Collects the requests a fake service received."""

    requests: list[httpx.Request] = field(default_factory=list)

    def paths(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests]


def json_router(
    routes: dict[str, object], recorder: Recorder, status: int = 200
) -> httpx.MockTransport:
    """Serve ``routes[path]`` as JSON for any method; unknown paths get 404."""

    def handle(request: httpx.Request) -> httpx.Response:
        recorder.requests.append(request)
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"error": f"no route for {request.url.path}"})
        if callable(body):
            return body(request)  # type: ignore[no-any-return]
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handle)


def failing_router(recorder: Recorder, status: int = 500) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        recorder.requests.append(request)
        return httpx.Response(status, text="boom")

    return httpx.MockTransport(handle)


def client(transport: httpx.MockTransport, base_url: str = "http://fake") -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport, base_url=base_url)


# --- canned payloads ---------------------------------------------------------

ALERTMANAGER_ALERTS = [
    {
        "labels": {
            "alertname": "VppSiteDevicesDark",
            "severity": "critical",
            "service": "ingestion",
            "site": "como",
            "device_type": "charger",
            "pod": "ingestion-abc",
        },
        "annotations": {
            "summary": "como: 40% of chargers have not reported for 60 s",
            "runbook_url": "https://github.com/berkayturk/vpp-sentinel/blob/main/docs/runbooks/site-devices-dark.md",
        },
        "startsAt": "2026-11-01T10:00:00.000Z",
        "status": {"state": "active", "silencedBy": [], "inhibitedBy": []},
    },
    {
        "labels": {
            "alertname": "VppIngestionLagHigh",
            "severity": "warning",
            "service": "ingestion",
        },
        "annotations": {"summary": "Telemetry is arriving late: mean ingestion lag is 45 s"},
        "startsAt": "2026-11-01T10:05:00.000Z",
        "status": {"state": "suppressed", "silencedBy": ["x"], "inhibitedBy": []},
    },
]


def prometheus_instant(samples: list[tuple[dict[str, str], float]]) -> dict:
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": m, "value": [1761991200, str(v)]} for m, v in samples],
        },
    }


def prometheus_range(series: list[tuple[dict[str, str], list[tuple[int, float]]]]) -> dict:
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {"metric": m, "values": [[ts, str(v)] for ts, v in vals]} for m, vals in series
            ],
        },
    }


def loki_lines(lines: list[tuple[int, str]]) -> dict:
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {"app": "ingestion", "namespace": "vpp"},
                    "values": [[str(ts * 1_000_000_000), line] for ts, line in lines],
                }
            ],
        },
    }


def json_line(**fields: object) -> str:
    return json.dumps(fields)


# --- incident store ------------------------------------------------------------


class FakeIncidentStore:
    """In-memory IncidentStore with the same semantics as the Postgres one."""

    def __init__(self, seeded: tuple = ()) -> None:
        from agent.memory import Incident  # local import keeps fakes importable early

        self._incident_cls = Incident
        self.rows: list = list(seeded)
        self._next_id = max((i.id for i in self.rows), default=0) + 1

    async def open(self, alert):
        from agent.fingerprint import fingerprint

        incident = self._incident_cls(
            id=self._next_id,
            fingerprint=fingerprint(alert),
            alert_name=alert.name,
            site=alert.labels.get("site"),
            device_type=alert.labels.get("device_type"),
            severity=alert.severity,
            started_at=alert.starts_at,
            resolved_at=None,
            report_md="",
            actions_taken=(),
            outcome=None,
        )
        self._next_id += 1
        self.rows.append(incident)
        return incident

    async def find_open(self, fingerprint: str):
        for row in self.rows:
            if row.fingerprint == fingerprint and row.resolved_at is None:
                return row
        return None

    async def record_action(self, incident_id: int, action):
        return self._replace(incident_id, lambda i: i.with_action(action))

    async def save_report(self, incident_id: int, report_md: str):
        return self._replace(incident_id, lambda i: i.with_report(report_md))

    async def resolve(self, incident_id: int, outcome: str):
        from datetime import UTC, datetime

        return self._replace(incident_id, lambda i: i.resolved(datetime.now(UTC), outcome))

    async def list_open(self):
        return tuple(r for r in self.rows if r.resolved_at is None)

    async def recent_like(self, alert_name: str, limit: int = 3):
        done = [r for r in self.rows if r.alert_name == alert_name and r.resolved_at is not None]
        return tuple(sorted(done, key=lambda r: r.started_at, reverse=True)[:limit])

    async def last_restart_at(self, fingerprint: str):
        from datetime import datetime

        times = [
            datetime.fromisoformat(a["at"])
            for r in self.rows
            if r.fingerprint == fingerprint
            for a in r.actions_taken
            if a.get("tool") == "restart_deployment" and a.get("outcome") == "ok"
        ]
        return max(times) if times else None

    async def close(self) -> None:
        return None

    def _replace(self, incident_id: int, change):
        for index, row in enumerate(self.rows):
            if row.id == incident_id:
                updated = change(row)
                self.rows[index] = updated
                return updated
        raise KeyError(incident_id)


def prometheus_router(
    answers: dict[str, list],
    recorder: Recorder,
    before: dict[str, list] | None = None,
    cutoff: float = 0.0,
) -> httpx.MockTransport:
    """Serve /api/v1/query by matching a substring of the expression against ``answers``.

    When ``before`` is given, queries whose ``time`` parameter is earlier than
    ``cutoff`` (unix seconds) are answered from it instead, so a test can show
    how a metric moved across the context window.
    """

    def pick(request: httpx.Request) -> dict[str, list]:
        at = request.url.params.get("time")
        if before is not None and at is not None and float(at) < cutoff:
            return before
        return answers

    def handle(request: httpx.Request) -> httpx.Response:
        recorder.requests.append(request)
        expr = request.url.params.get("query", "")
        for needle, samples in pick(request).items():
            if needle in expr:
                return httpx.Response(200, json=prometheus_instant(samples))
        return httpx.Response(200, json=prometheus_instant([]))

    return httpx.MockTransport(handle)


# --- workspace -------------------------------------------------------------------


class FakeWorkspace:
    """An in-memory Workspace: files by repository-relative path, scripted test results."""

    def __init__(self, root, files: dict[str, str]) -> None:
        self.root = root
        self.files = dict(files)
        self.changed: tuple[str, ...] | None = None
        self.written: list[str] = []
        self.tests_pass = True
        self.test_output = "3 passed"
        self.pushed: list[tuple[str, str]] = []
        self.prepared = 0

    async def prepare(self) -> None:
        self.prepared += 1

    def _rel(self, path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    async def read(self, path) -> str:
        rel = self._rel(path)
        if rel not in self.files:
            from agent.workspace import WorkspaceError

            raise WorkspaceError(f"{rel}: no such file")
        return self.files[rel]

    async def write(self, path, content: str) -> None:
        rel = self._rel(path)
        self.files[rel] = content
        self.written.append(rel)

    async def list(self, path) -> tuple[str, ...]:
        rel = self._rel(path)
        return tuple(sorted(p.split("/")[-1] for p in self.files if p.startswith(rel + "/")))

    async def changed_paths(self) -> tuple[str, ...]:
        return self.changed if self.changed is not None else tuple(self.written)

    async def run_tests(self):
        from agent.workspace import TestResult

        return TestResult(self.tests_pass, self.test_output)

    async def commit_and_push(self, branch: str, message: str, paths=()) -> str:
        self.pushed.append((branch, message, tuple(paths)))
        return "0" * 40


# --- model runner ----------------------------------------------------------------


class ScriptedRunner:
    """A ModelRunner that calls the tool set's methods in a fixed order, then answers."""

    def __init__(self, toolset, steps: list[tuple[str, dict]], final_text: str = "") -> None:
        self._toolset = toolset
        self._steps = steps
        self._final_text = final_text
        self.received_system = ""
        self.received_user = ""
        self.received_tool_names: tuple[str, ...] = ()

    async def run(self, system: str, user: str, tools, max_iterations: int):
        from agent.loop import RunResult, Usage

        self.received_system = system
        self.received_user = user
        self.received_tool_names = tuple(t.name for t in tools)
        for name, kwargs in self._steps[:max_iterations]:
            await getattr(self._toolset, name)(**kwargs)
        return RunResult(
            final_text=self._final_text,
            usage=Usage(input_tokens=1000, output_tokens=200, cache_read_tokens=500),
            iterations=len(self._steps),
        )


class ScriptedByName:
    """A ModelRunner that finds tools by name in the list it is given and calls them."""

    def __init__(self, steps: list[tuple[str, dict]], final_text: str = "done") -> None:
        self._steps = steps
        self._final_text = final_text
        self.calls = 0
        self.explode = False

    async def run(self, system: str, user: str, tools, max_iterations: int):
        from agent.loop import RunResult, Usage

        self.calls += 1
        if self.explode:
            raise RuntimeError("model exploded")
        by_name = {t.name: t for t in tools}
        for name, kwargs in self._steps[:max_iterations]:
            await by_name[name].func(**kwargs)
        return RunResult(self._final_text, Usage(10, 5, 0), len(self._steps))
