from datetime import UTC, datetime

from prometheus_client import REGISTRY
from structlog.testing import capture_logs

from ingestion.consumer import handle_message, refresh_gauges
from ingestion.registry import LastSeen
from ingestion.schema import Telemetry
from ingestion.store import InsertResult

NOW = datetime(2026, 10, 31, 14, 5, 10, tzinfo=UTC)
TOPIC = "vpp/como/charger/como-charger-001/telemetry"
GOOD = (
    b'{"device_id":"como-charger-001","site":"como","device_type":"charger",'
    b'"ts":"2026-10-31T14:05:00Z","firmware":"2.3.1","status":"charging",'
    b'"power_kw":7.4,"energy_kwh":12.5,"soc_pct":null,"voltage_v":231.2}'
)


class FakeStore:
    def __init__(self, result: InsertResult = "stored", raises: Exception | None = None) -> None:
        self.result = result
        self.raises = raises
        self.inserted: list[Telemetry] = []

    async def insert(self, t: Telemetry) -> InsertResult:
        if self.raises:
            raise self.raises
        self.inserted.append(t)
        return self.result

    async def expected_counts(self) -> dict[tuple[str, str], int]:
        return {("como", "charger"): 30, ("como", "battery"): 15}

    async def device_index(self) -> dict[str, tuple[str, str]]:
        return {"como-charger-001": ("como", "charger"), "como-battery-001": ("como", "battery")}


def _sample(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_good_message_is_stored_and_registered():
    store = FakeStore()
    reg = await handle_message(GOOD, TOPIC, store, LastSeen({}), NOW)
    assert store.inserted[0].device_id == "como-charger-001"
    assert reg.seen["como-charger-001"] == NOW


async def test_invalid_json_is_rejected_not_raised():
    store = FakeStore()
    before = _sample(
        "vpp_ingest_rejects_total",
        {"reason": "invalid_json", "device_type": "unknown", "firmware": "unknown"},
    )
    reg = await handle_message(b"{bad", "vpp/x/y/z/telemetry", store, LastSeen({}), NOW)
    assert store.inserted == [] and reg.seen == {}
    after = _sample(
        "vpp_ingest_rejects_total",
        {"reason": "invalid_json", "device_type": "unknown", "firmware": "unknown"},
    )
    assert after == before + 1


async def test_duplicate_is_counted_and_not_registered_twice():
    store = FakeStore(result="duplicate")
    reg = await handle_message(GOOD, TOPIC, store, LastSeen({}), NOW)
    assert reg.seen == {}
    assert (
        _sample(
            "vpp_ingest_messages_total",
            {"site": "como", "device_type": "charger", "result": "duplicate"},
        )
        >= 1
    )


async def test_unknown_device_is_counted_not_registered():
    store = FakeStore(result="unknown_device")
    before = _sample(
        "vpp_ingest_rejects_total",
        {"reason": "unknown_device", "device_type": "charger", "firmware": "2.3.1"},
    )
    reg = await handle_message(GOOD, TOPIC, store, LastSeen({}), NOW)
    assert reg.seen == {}
    after = _sample(
        "vpp_ingest_rejects_total",
        {"reason": "unknown_device", "device_type": "charger", "firmware": "2.3.1"},
    )
    assert after == before + 1


async def test_store_exception_is_logged_and_swallowed():
    store = FakeStore(raises=RuntimeError("db down"))
    with capture_logs() as logs:
        reg = await handle_message(GOOD, TOPIC, store, LastSeen({}), NOW)
    assert reg.seen == {}
    failures = [entry for entry in logs if entry["event"] == "telemetry.store_failed"]
    assert failures and failures[0]["error"] == "db down"
    assert failures[0]["log_level"] == "error"


async def test_refresh_gauges_publishes_reporting_and_expected():
    store = FakeStore()
    reg = LastSeen({"como-charger-001": NOW})
    await refresh_gauges(store, reg, NOW)
    assert _sample("vpp_devices_reporting", {"site": "como", "device_type": "charger"}) == 1
    assert _sample("vpp_devices_reporting", {"site": "como", "device_type": "battery"}) == 0
    assert _sample("vpp_devices_expected", {"site": "como", "device_type": "charger"}) == 30
