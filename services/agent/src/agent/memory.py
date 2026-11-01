"""Incident memory: the ``incidents`` table as an immutable record of what happened before.

The table is owned by the ingestion service's migrations; the agent only
reads and appends. ``Incident`` values are frozen and every change returns a
new one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

import asyncpg

from agent.fingerprint import Alert, fingerprint

COLUMNS = (
    "id, fingerprint, alert_name, site, device_type, severity, started_at, resolved_at, "
    "report_md, actions_taken, outcome"
)


@dataclass(frozen=True)
class Incident:
    id: int
    fingerprint: str
    alert_name: str
    site: str | None
    device_type: str | None
    severity: str
    started_at: datetime
    resolved_at: datetime | None
    report_md: str
    actions_taken: tuple[Mapping[str, Any], ...]
    outcome: str | None

    def with_action(self, action: Mapping[str, Any]) -> Incident:
        return replace(self, actions_taken=(*self.actions_taken, dict(action)))

    def with_report(self, report_md: str) -> Incident:
        return replace(self, report_md=report_md)

    def resolved(self, at: datetime, outcome: str) -> Incident:
        return replace(self, resolved_at=at, outcome=outcome)


@runtime_checkable
class IncidentStore(Protocol):
    async def open(self, alert: Alert) -> Incident: ...
    async def find_open(self, fingerprint: str) -> Incident | None: ...
    async def list_open(self) -> tuple[Incident, ...]: ...
    async def record_action(self, incident_id: int, action: Mapping[str, Any]) -> Incident: ...
    async def save_report(self, incident_id: int, report_md: str) -> Incident: ...
    async def resolve(self, incident_id: int, outcome: str) -> Incident: ...
    async def recent_like(self, alert_name: str, limit: int = 3) -> tuple[Incident, ...]: ...
    async def last_restart_at(self, fingerprint: str) -> datetime | None: ...
    async def close(self) -> None: ...


class PostgresIncidentStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> PostgresIncidentStore:
        return cls(await asyncpg.create_pool(dsn, min_size=1, max_size=3))

    async def close(self) -> None:
        await self._pool.close()

    async def open(self, alert: Alert) -> Incident:
        row = await self._pool.fetchrow(
            "INSERT INTO incidents"
            " (fingerprint, alert_name, site, device_type, severity, started_at)"
            f" VALUES ($1, $2, $3, $4, $5, $6) RETURNING {COLUMNS}",
            fingerprint(alert),
            alert.name,
            alert.labels.get("site"),
            alert.labels.get("device_type"),
            alert.severity,
            alert.starts_at,
        )
        return _incident(row)

    async def find_open(self, fingerprint: str) -> Incident | None:
        row = await self._pool.fetchrow(
            f"SELECT {COLUMNS} FROM incidents WHERE fingerprint = $1 AND resolved_at IS NULL"
            " ORDER BY started_at DESC LIMIT 1",
            fingerprint,
        )
        return _incident(row) if row else None

    async def list_open(self) -> tuple[Incident, ...]:
        rows = await self._pool.fetch(
            f"SELECT {COLUMNS} FROM incidents WHERE resolved_at IS NULL ORDER BY started_at"
        )
        return tuple(_incident(row) for row in rows)

    async def record_action(self, incident_id: int, action: Mapping[str, Any]) -> Incident:
        row = await self._pool.fetchrow(
            f"UPDATE incidents SET actions_taken = actions_taken || $2::jsonb WHERE id = $1"
            f" RETURNING {COLUMNS}",
            incident_id,
            json.dumps([dict(action)]),
        )
        return _incident(_require(row, incident_id))

    async def save_report(self, incident_id: int, report_md: str) -> Incident:
        row = await self._pool.fetchrow(
            f"UPDATE incidents SET report_md = $2 WHERE id = $1 RETURNING {COLUMNS}",
            incident_id,
            report_md,
        )
        return _incident(_require(row, incident_id))

    async def resolve(self, incident_id: int, outcome: str) -> Incident:
        row = await self._pool.fetchrow(
            f"UPDATE incidents SET resolved_at = now(), outcome = $2 WHERE id = $1"
            f" RETURNING {COLUMNS}",
            incident_id,
            outcome,
        )
        return _incident(_require(row, incident_id))

    async def recent_like(self, alert_name: str, limit: int = 3) -> tuple[Incident, ...]:
        rows = await self._pool.fetch(
            f"SELECT {COLUMNS} FROM incidents WHERE alert_name = $1 AND resolved_at IS NOT NULL"
            " ORDER BY started_at DESC LIMIT $2",
            alert_name,
            limit,
        )
        return tuple(_incident(row) for row in rows)

    async def last_restart_at(self, fingerprint: str) -> datetime | None:
        value = await self._pool.fetchval(
            "SELECT max((a->>'at')::timestamptz) FROM incidents,"
            " jsonb_array_elements(actions_taken) AS a"
            " WHERE fingerprint = $1 AND a->>'tool' = 'restart_deployment'"
            " AND a->>'outcome' = 'ok'",
            fingerprint,
        )
        return value


def _require(row: asyncpg.Record | None, incident_id: int) -> asyncpg.Record:
    if row is None:
        raise KeyError(f"incident {incident_id} does not exist")
    return row


def _incident(row: asyncpg.Record) -> Incident:
    actions = row["actions_taken"]
    if isinstance(actions, str):
        actions = json.loads(actions)
    return Incident(
        id=row["id"],
        fingerprint=row["fingerprint"],
        alert_name=row["alert_name"],
        site=row["site"],
        device_type=row["device_type"],
        severity=row["severity"],
        started_at=row["started_at"],
        resolved_at=row["resolved_at"],
        report_md=row["report_md"],
        actions_taken=tuple(dict(a) for a in actions),
        outcome=row["outcome"],
    )
