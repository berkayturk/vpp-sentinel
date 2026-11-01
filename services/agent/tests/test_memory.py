"""Incident memory over the real incidents table; skipped unless TEST_DATABASE_URL is set."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from agent.fingerprint import Alert
from agent.memory import Incident, IncidentStore, PostgresIncidentStore
from tests.fakes import FakeIncidentStore

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs postgres")

MIGRATIONS = sorted((Path(__file__).resolve().parents[2] / "ingestion/migrations").glob("*.sql"))
NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
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
LAG = Alert(
    name="VppIngestionLagHigh",
    labels={"alertname": "VppIngestionLagHigh", "severity": "warning"},
    annotations={},
    starts_at=NOW,
)


@pytest.fixture
async def store():
    dsn = os.environ["TEST_DATABASE_URL"]
    conn = await asyncpg.connect(dsn)
    try:
        for path in MIGRATIONS:
            await conn.execute(path.read_text())
        await conn.execute("TRUNCATE incidents RESTART IDENTITY")
        await conn.execute(MIGRATIONS[-1].read_text())  # the seed row is back as id 1
    finally:
        await conn.close()
    s = await PostgresIncidentStore.connect(dsn)
    try:
        yield s
    finally:
        await s.close()


async def test_open_then_find_open(store: PostgresIncidentStore):
    opened = await store.open(DARK)
    assert isinstance(opened, Incident)
    assert opened.fingerprint == "VppSiteDevicesDark|como|charger"
    assert opened.resolved_at is None
    found = await store.find_open("VppSiteDevicesDark|como|charger")
    assert found is not None and found.id == opened.id
    assert await store.find_open("VppIngestionLagHigh|-|-") is None
    assert [i.id for i in await store.list_open()] == [opened.id]


async def test_record_action_appends_immutably(store: PostgresIncidentStore):
    opened = await store.open(DARK)
    action = {
        "tool": "restart_deployment",
        "args": {"name": "ingestion"},
        "outcome": "ok",
        "at": NOW.isoformat(),
    }
    updated = await store.record_action(opened.id, action)
    assert opened.actions_taken == ()
    assert updated.actions_taken == (action,)
    again = await store.record_action(
        opened.id, {"tool": "file_report", "args": {}, "outcome": "ok", "at": NOW.isoformat()}
    )
    assert len(again.actions_taken) == 2


async def test_save_report_and_resolve(store: PostgresIncidentStore):
    opened = await store.open(LAG)
    with_report = await store.save_report(opened.id, "# Incident\n\nhello")
    assert with_report.report_md.startswith("# Incident")
    resolved = await store.resolve(opened.id, "resolved: mitigation worked")
    assert resolved.resolved_at is not None and resolved.outcome == "resolved: mitigation worked"
    assert await store.find_open("VppIngestionLagHigh|-|-") is None


async def test_recent_like_returns_resolved_newest_first_without_open_ones(store):
    seeded = await store.recent_like("VppIngestionLagHigh")
    assert [i.started_at.year for i in seeded] == [2026] and seeded[0].outcome.startswith(
        "resolved"
    )
    newer = await store.open(LAG)
    await store.resolve(newer.id, "resolved: x")
    still_open = await store.open(LAG)
    recent = await store.recent_like("VppIngestionLagHigh", limit=3)
    assert [i.id for i in recent] == [newer.id, seeded[0].id]
    assert still_open.id not in [i.id for i in recent]


async def test_last_restart_at_reads_the_newest_ok_restart(store: PostgresIncidentStore):
    assert await store.last_restart_at("VppIngestionLagHigh|-|-") == datetime(
        2026, 10, 18, 9, 16, 5, tzinfo=UTC
    )
    opened = await store.open(LAG)
    later = NOW + timedelta(minutes=3)
    await store.record_action(
        opened.id,
        {
            "tool": "restart_deployment",
            "args": {"name": "ingestion"},
            "outcome": "REFUSED",
            "at": later.isoformat(),
        },
    )
    assert await store.last_restart_at("VppIngestionLagHigh|-|-") == datetime(
        2026, 10, 18, 9, 16, 5, tzinfo=UTC
    )
    await store.record_action(
        opened.id,
        {
            "tool": "restart_deployment",
            "args": {"name": "ingestion"},
            "outcome": "ok",
            "at": later.isoformat(),
        },
    )
    assert await store.last_restart_at("VppIngestionLagHigh|-|-") == later
    assert await store.last_restart_at("VppSiteDevicesDark|como|charger") is None


def test_fake_store_satisfies_the_protocol():
    fake: IncidentStore = FakeIncidentStore()
    assert isinstance(fake, IncidentStore)
