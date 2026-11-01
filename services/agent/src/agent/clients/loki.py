"""Loki query_range: log lines in time order."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx

from agent.clients.errors import ClientError, get_json

SERVICE = "loki"
NANOS = 1_000_000_000


@dataclass(frozen=True)
class LogLine:
    at: float
    line: str


async def query_range(
    http: httpx.AsyncClient, logql: str, start: datetime, end: datetime, limit: int
) -> tuple[LogLine, ...]:
    raw = await get_json(
        http,
        SERVICE,
        "/loki/api/v1/query_range",
        query=logql,
        start=str(int(start.timestamp() * NANOS)),
        end=str(int(end.timestamp() * NANOS)),
        limit=str(limit),
        direction="forward",
    )
    streams = raw.get("data", {}).get("result") if isinstance(raw, dict) else None
    if not isinstance(streams, list):
        raise ClientError(SERVICE, "result is not a list of streams")
    lines = [_line(value) for stream in streams for value in stream.get("values", [])]
    return tuple(sorted(lines, key=lambda ln: ln.at))


def _line(value: object) -> LogLine:
    try:
        ts, text = value  # type: ignore[misc]
        return LogLine(int(ts) / NANOS, str(text))
    except (TypeError, ValueError) as exc:
        raise ClientError(SERVICE, f"malformed log entry: {exc}") from exc
