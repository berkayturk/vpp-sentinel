from datetime import UTC, datetime, timedelta

from prometheus_client import REGISTRY

from ingestion.metrics import (
    publish_gauges,
    record_duplicate,
    record_rejected,
    record_store_error,
    record_stored,
)
from ingestion.schema import ParseError, Telemetry

NOW = datetime(2026, 10, 31, 14, 5, 10, tzinfo=UTC)
GROUP = ("como", "charger")
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


def _messages(result: str, site: str = "como", device_type: str = "charger") -> float:
    return _sample(
        "vpp_ingest_messages_total",
        {"site": site, "device_type": device_type, "result": result},
    )


def _lag_sum(site: str = "como") -> float:
    return _sample("vpp_ingest_lag_seconds_sum", {"site": site})


def test_stored_increments_counter_and_lag():
    before_lag = _lag_sum()
    before = _messages("stored")
    record_stored(T, GROUP, NOW)
    assert _messages("stored") == before + 1
    assert _lag_sum() - before_lag >= 4.0


def test_lag_is_labelled_with_the_group_site():
    before = _lag_sum("varese")
    record_stored(T, ("varese", "battery"), NOW)
    assert _lag_sum("varese") - before >= 4.0
    assert _lag_sum("bellinzona") == 0.0


def test_labels_come_from_the_group_not_the_payload():
    before = _messages("stored", site="varese", device_type="battery")
    record_stored(T, ("varese", "battery"), NOW)
    assert _messages("stored", site="varese", device_type="battery") == before + 1


def test_future_timestamp_lag_is_clamped_to_zero():
    before = _lag_sum()
    record_stored(T.model_copy(update={"ts": NOW + timedelta(seconds=30)}), GROUP, NOW)
    assert _lag_sum() == before


def test_duplicate_counts_under_its_own_result():
    before = _messages("duplicate")
    record_duplicate(GROUP)
    assert _messages("duplicate") == before + 1


def test_store_error_counts_under_its_own_result():
    before = _messages("error")
    record_store_error(GROUP)
    assert _messages("error") == before + 1


def test_rejected_carries_reason_and_firmware():
    labels = {"reason": "invalid_timestamp", "device_type": "charger", "firmware": "2.4.0"}
    before = _sample("vpp_ingest_rejects_total", labels)
    before_msgs = _messages("rejected")
    record_rejected(ParseError("invalid_timestamp", "charger", "2.4.0"), site="como")
    assert _sample("vpp_ingest_rejects_total", labels) == before + 1
    assert _messages("rejected") == before_msgs + 1


def test_rejected_without_context_uses_unknown_labels():
    labels = {"reason": "invalid_json", "device_type": "unknown", "firmware": "unknown"}
    before = _sample("vpp_ingest_rejects_total", labels)
    record_rejected(ParseError("invalid_json", None, None), site=None)
    assert _sample("vpp_ingest_rejects_total", labels) == before + 1
    assert _messages("rejected", site="unknown", device_type="unknown") >= 1


def test_rejected_sanitises_untrusted_label_values():
    # Anything a publisher could put in the payload must not become a new label series.
    labels = {"reason": "invalid_value", "device_type": "unknown", "firmware": "other"}
    before = _sample("vpp_ingest_rejects_total", labels)
    record_rejected(ParseError("invalid_value", "toaster-" * 40, "rand-123"), site="como")
    assert _sample("vpp_ingest_rejects_total", labels) == before + 1
    assert (
        _sample(
            "vpp_ingest_rejects_total",
            {"reason": "invalid_value", "device_type": "toaster-" * 40, "firmware": "rand-123"},
        )
        == 0
    )


def test_publish_gauges_sets_reporting_and_expected():
    publish_gauges({GROUP: 18}, {GROUP: 30})
    assert _sample("vpp_devices_reporting", {"site": "como", "device_type": "charger"}) == 18
    assert _sample("vpp_devices_expected", {"site": "como", "device_type": "charger"}) == 30
