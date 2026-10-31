from datetime import UTC, datetime, timedelta

from prometheus_client import REGISTRY

from ingestion.metrics import publish_gauges, record_duplicate, record_rejected, record_stored
from ingestion.schema import ParseError, Telemetry

NOW = datetime(2026, 10, 31, 14, 5, 10, tzinfo=UTC)
T = Telemetry(
    device_id="como-charger-001",
    site="como",
    device_type="charger",
    ts=NOW - timedelta(seconds=4),
    firmware="2.3.1",
    status="charging",
    power_kw=7.4,
    energy_kwh=12.5,
    soc_pct=None,
    voltage_v=231.2,
)


def _sample(name: str, labels: dict[str, str] | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


def test_stored_increments_counter_and_lag():
    before = _sample("vpp_ingest_lag_seconds_sum")
    record_stored(T, NOW)
    labels = {"site": "como", "device_type": "charger", "result": "stored"}
    assert _sample("vpp_ingest_messages_total", labels) >= 1
    assert _sample("vpp_ingest_lag_seconds_sum") - before >= 4.0


def test_future_timestamp_lag_is_clamped_to_zero():
    before = _sample("vpp_ingest_lag_seconds_sum")
    record_stored(T.model_copy(update={"ts": NOW + timedelta(seconds=30)}), NOW)
    assert _sample("vpp_ingest_lag_seconds_sum") == before


def test_duplicate_counts_under_its_own_result():
    record_duplicate(T)
    labels = {"site": "como", "device_type": "charger", "result": "duplicate"}
    assert _sample("vpp_ingest_messages_total", labels) >= 1


def test_rejected_carries_reason_and_firmware():
    record_rejected(ParseError("invalid_timestamp", "charger", "2.4.0"), site="como")
    labels = {"reason": "invalid_timestamp", "device_type": "charger", "firmware": "2.4.0"}
    assert _sample("vpp_ingest_rejects_total", labels) >= 1
    assert (
        _sample(
            "vpp_ingest_messages_total",
            {"site": "como", "device_type": "charger", "result": "rejected"},
        )
        >= 1
    )


def test_rejected_without_context_uses_unknown_labels():
    record_rejected(ParseError("invalid_json", None, None), site=None)
    labels = {"reason": "invalid_json", "device_type": "unknown", "firmware": "unknown"}
    assert _sample("vpp_ingest_rejects_total", labels) >= 1


def test_publish_gauges_sets_reporting_and_expected():
    publish_gauges({("como", "charger"): 18}, {("como", "charger"): 30})
    assert _sample("vpp_devices_reporting", {"site": "como", "device_type": "charger"}) == 18
    assert _sample("vpp_devices_expected", {"site": "como", "device_type": "charger"}) == 30
