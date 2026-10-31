"""Telemetry persistence behind a small protocol so the consumer can be tested without a DB."""

from pathlib import Path
from typing import Literal, Protocol

import asyncpg

from ingestion.schema import Telemetry

InsertResult = Literal["stored", "duplicate", "unknown_device"]
GroupCounts = dict[tuple[str, str], int]
DeviceIndex = dict[str, tuple[str, str]]

MIGRATIONS_TABLE = "schema_migrations"
INSERT_SQL = """
INSERT INTO telemetry (ts, device_id, firmware, status, power_kw, energy_kwh, soc_pct, voltage_v)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (device_id, ts) DO NOTHING
"""
EXPECTED_SQL = "SELECT site_id, device_type, count(*) FROM devices GROUP BY site_id, device_type"
INDEX_SQL = "SELECT id, site_id, device_type FROM devices"


class TelemetryStore(Protocol):
    async def insert(self, t: Telemetry) -> InsertResult: ...
    async def expected_counts(self) -> GroupCounts: ...
    async def device_index(self) -> DeviceIndex: ...


class PostgresStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> "PostgresStore":
        return cls(await asyncpg.create_pool(dsn, min_size=1, max_size=4))

    async def close(self) -> None:
        await self._pool.close()

    async def insert(self, t: Telemetry) -> InsertResult:
        try:
            status = await self._pool.execute(
                INSERT_SQL,
                t.ts,
                t.device_id,
                t.firmware,
                t.status,
                t.power_kw,
                t.energy_kwh,
                t.soc_pct,
                t.voltage_v,
            )
        except asyncpg.ForeignKeyViolationError:
            return "unknown_device"
        return "stored" if status.endswith("1") else "duplicate"

    async def expected_counts(self) -> GroupCounts:
        rows = await self._pool.fetch(EXPECTED_SQL)
        return {(r["site_id"], r["device_type"]): int(r["count"]) for r in rows}

    async def device_index(self) -> DeviceIndex:
        rows = await self._pool.fetch(INDEX_SQL)
        return {r["id"]: (r["site_id"], r["device_type"]) for r in rows}


async def apply_migrations(dsn: str, migrations_dir: Path) -> list[str]:
    """Apply every ``*.sql`` file not yet recorded; returns the filenames applied this run."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            f"CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} (filename text PRIMARY KEY)"
        )
        done = {r["filename"] for r in await conn.fetch(f"SELECT filename FROM {MIGRATIONS_TABLE}")}
        applied: list[str] = []
        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name in done:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute(
                    f"INSERT INTO {MIGRATIONS_TABLE} (filename) VALUES ($1)", path.name
                )
            applied.append(path.name)
        return applied
    finally:
        await conn.close()
