from datetime import UTC, datetime

from prometheus_client import REGISTRY
from structlog.testing import capture_logs

from ingestion.consumer import handle_message, refresh_gauges
from ingestion.fleet_index import FleetIndex
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
FLEET = FleetIndex(
    devices={"como-charger-001": ("como", "charger"), "como-battery-001": ("como", "battery")},
    expected={("como", "charger"): 30, ("como", "battery"): 15},
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
        return dict(FLEET.expected)

    async def device_index(self) -> dict[str, tuple[str, str]]:
        return dict(FLEET.devices)


def _sample(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _messages(result: str, site: str = "como", device_type: str = "charger") -> float:
    return _sample(
        "vpp_ingest_messages_total", {"site": site, "device_type": device_type, "result": result}
    )


def _rejects(reason: str, device_type: str = "unknown", firmware: str = "unknown") -> float:
    return _sample(
        "vpp_ingest_rejects_total",
        {"reason": reason, "device_type": device_type, "firmware": firmware},
    )


async def test_good_message_is_stored_and_registered():
    store = FakeStore()
    before = _messages("stored")
    reg = await handle_message(GOOD, TOPIC, store, LastSeen({}), NOW, FLEET)
    assert store.inserted[0].device_id == "como-charger-001"
    assert reg.seen["como-charger-001"] == NOW
    assert _messages("stored") == before + 1


async def test_stored_labels_use_the_fleet_index_not_the_payload_site():
    # A known device claiming another site is stored, but attributed to its real site.
    store = FakeStore()
    before_real, before_claimed = _messages("stored"), _messages("stored", site="atlantis")
    spoofed = GOOD.replace(b'"site":"como"', b'"site":"atlantis"')
    await handle_message(spoofed, TOPIC, store, LastSeen({}), NOW, FLEET)
    assert _messages("stored") == before_real + 1
    assert _messages("stored", site="atlantis") == before_claimed


async def test_invalid_json_is_rejected_not_raised():
    store = FakeStore()
    before = _rejects("invalid_json")
    with capture_logs() as logs:
        reg = await handle_message(b"{bad", "vpp/x/y/z/telemetry", store, LastSeen({}), NOW, FLEET)
    assert store.inserted == [] and reg.seen == {}
    assert _rejects("invalid_json") == before + 1
    warning = next(e for e in logs if e["event"] == "telemetry.rejected")
    assert warning["log_level"] == "warning" and warning["topic"] == "vpp/x/y/z/telemetry"


async def test_reject_site_label_only_trusts_known_sites():
    store = FakeStore()
    before_unknown = _messages("rejected", site="unknown", device_type="unknown")
    await handle_message(b"{bad", "vpp/atlantis/x/y/telemetry", store, LastSeen({}), NOW, FLEET)
    assert _messages("rejected", site="unknown", device_type="unknown") == before_unknown + 1
    assert _messages("rejected", site="atlantis", device_type="unknown") == 0


async def test_duplicate_is_counted_and_not_registered():
    store = FakeStore(result="duplicate")
    before = _messages("duplicate")
    reg = await handle_message(GOOD, TOPIC, store, LastSeen({}), NOW, FLEET)
    assert reg.seen == {}
    assert _messages("duplicate") == before + 1


async def test_unknown_device_is_counted_not_registered():
    store = FakeStore(result="unknown_device")
    ghost = GOOD.replace(b"como-charger-001", b"ghost-001")
    before = _rejects("unknown_device", "charger", "2.3.1")
    reg = await handle_message(ghost, TOPIC, store, LastSeen({}), NOW, FLEET)
    assert reg.seen == {}
    assert _rejects("unknown_device", "charger", "2.3.1") == before + 1


async def test_store_exception_is_logged_counted_and_swallowed():
    store = FakeStore(raises=RuntimeError("db down"))
    before = _messages("error")
    with capture_logs() as logs:
        reg = await handle_message(GOOD, TOPIC, store, LastSeen({}), NOW, FLEET)
    assert reg.seen == {}
    assert _messages("error") == before + 1
    failure = next(e for e in logs if e["event"] == "telemetry.store_failed")
    assert failure["error"] == "db down" and failure["log_level"] == "error"


def test_refresh_gauges_uses_the_cached_index_not_the_database():
    reg = LastSeen({"como-charger-001": NOW})
    refresh_gauges(FLEET, reg, NOW)
    assert _sample("vpp_devices_reporting", {"site": "como", "device_type": "charger"}) == 1
    assert _sample("vpp_devices_reporting", {"site": "como", "device_type": "battery"}) == 0
    assert _sample("vpp_devices_expected", {"site": "como", "device_type": "charger"}) == 30


async def test_rejected_messages_log_a_payload_sample_and_the_validation_detail():
    import structlog

    from ingestion.consumer import handle_message
    from ingestion.registry import LastSeen

    raw = (
        b'{"device_id":"como-charger-001","site":"como","device_type":"charger",'
        b'"ts":1793455500000,"firmware":"2.4.0","status":"charging",'
        b'"power_kw":7.4,"energy_kwh":12.5,"soc_pct":null,"voltage_v":231.2}'
    )
    with structlog.testing.capture_logs() as logs:
        await handle_message(
            raw,
            "vpp/como/charger/como-charger-001/telemetry",
            FakeStore(),
            LastSeen({}),
            NOW,
            FLEET,
        )
    rejected = [e for e in logs if e["event"] == "telemetry.rejected"][0]
    assert rejected["reason"] == "invalid_timestamp"
    assert '"ts":1793455500000' in rejected["payload_sample"]
    assert "ts" in rejected["detail"]
    assert len(rejected["payload_sample"]) <= 300
