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
