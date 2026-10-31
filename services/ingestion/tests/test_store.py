"""Integration tests against a real PostgreSQL; skipped unless TEST_DATABASE_URL is set."""

import os
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

from ingestion.schema import Telemetry
from ingestion.store import PostgresStore, apply_migrations

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs postgres")

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
T = Telemetry(
    device_id="como-charger-001",
    site="como",
    device_type="charger",
    ts=datetime(2026, 10, 31, 14, 5, tzinfo=UTC),
    firmware="2.3.1",
    status="charging",
    power_kw=7.4,
    energy_kwh=12.5,
    soc_pct=None,
    voltage_v=231.2,
)


@pytest.fixture
async def dsn() -> str:
    url = os.environ["TEST_DATABASE_URL"]
    await apply_migrations(url, MIGRATIONS_DIR)
    conn = await asyncpg.connect(url)
    try:
        await conn.execute("TRUNCATE telemetry")
    finally:
        await conn.close()
    return url


@pytest.fixture
async def store(dsn: str):
    s = await PostgresStore.connect(dsn)
    try:
        yield s
    finally:
        await s.close()


async def test_insert_then_duplicate(store: PostgresStore):
    assert await store.insert(T) == "stored"
    assert await store.insert(T) == "duplicate"


async def test_unknown_device(store: PostgresStore):
    assert await store.insert(T.model_copy(update={"device_id": "ghost-001"})) == "unknown_device"


async def test_expected_counts_match_seed(store: PostgresStore):
    counts = await store.expected_counts()
    assert counts[("lugano-south", "charger")] == 30
    assert sum(counts.values()) == 300


async def test_device_index_maps_id_to_site_and_type(store: PostgresStore):
    index = await store.device_index()
    assert index["como-battery-003"] == ("como", "battery")
    assert len(index) == 300


async def test_migrations_are_idempotent(dsn: str):
    await apply_migrations(dsn, MIGRATIONS_DIR)  # second run must not fail
