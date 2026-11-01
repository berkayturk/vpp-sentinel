"""Prometheus HTTP API: instant and range queries as frozen samples and series."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

import httpx

from agent.clients.errors import ClientError, get_json

SERVICE = "prometheus"


@dataclass(frozen=True)
class Sample:
    labels: Mapping[str, str]
    value: float
    at: float


@dataclass(frozen=True)
class Series:
    labels: Mapping[str, str]
    values: tuple[tuple[float, float], ...]


async def instant(
    http: httpx.AsyncClient, expr: str, at: datetime | None = None
) -> tuple[Sample, ...]:
    params = {"query": expr}
    if at is not None:
        params["time"] = str(at.timestamp())
    result = _result(await get_json(http, SERVICE, "/api/v1/query", **params))
    return tuple(_sample(item) for item in result)


async def range_query(
    http: httpx.AsyncClient, expr: str, start: datetime, end: datetime, step_seconds: int
) -> tuple[Series, ...]:
    result = _result(
        await get_json(
            http,
            SERVICE,
            "/api/v1/query_range",
            query=expr,
            start=str(start.timestamp()),
            end=str(end.timestamp()),
            step=str(step_seconds),
        )
    )
    return tuple(_series(item) for item in result)


def _result(raw: object) -> list:
    if not isinstance(raw, dict) or raw.get("status") != "success":
        raise ClientError(SERVICE, f"query failed: {str(raw)[:200]}")
    result = raw.get("data", {}).get("result")
    if not isinstance(result, list):
        raise ClientError(SERVICE, "result is not a list")
    return result


def _labels(item: dict) -> Mapping[str, str]:
    return MappingProxyType({k: str(v) for k, v in item.get("metric", {}).items()})


def _sample(item: dict) -> Sample:
    try:
        at, value = item["value"]
        return Sample(_labels(item), float(value), float(at))
    except (KeyError, TypeError, ValueError) as exc:
        raise ClientError(SERVICE, f"malformed sample: {exc}") from exc


def _series(item: dict) -> Series:
    try:
        values = tuple((float(at), float(v)) for at, v in item["values"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ClientError(SERVICE, f"malformed series: {exc}") from exc
    return Series(_labels(item), values)
