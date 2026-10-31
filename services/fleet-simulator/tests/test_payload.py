from datetime import UTC, datetime

from fleet_simulator.fleet import Device
from fleet_simulator.payload import telemetry_payload, topic_for

TS = datetime(2026, 10, 31, 14, 5, tzinfo=UTC)
PAYLOAD_FIELDS = {
    "device_id",
    "site",
    "device_type",
    "ts",
    "firmware",
    "status",
    "power_kw",
    "energy_kwh",
    "soc_pct",
    "voltage_v",
}


def test_topic_shape():
    d = Device("como-battery-003", "como", "battery", "2.3.1", 50.0, 100.0, 55.0)
    assert topic_for(d) == "vpp/como/battery/como-battery-003/telemetry"


def test_payload_normal_firmware_uses_iso_ts():
    d = Device("como-charger-001", "como", "charger", "2.3.1", 11.0, None, None)
    p = telemetry_payload(d, TS, 7.4, "charging", 12.5)
    assert p["ts"] == "2026-10-31T14:05:00Z"
    assert p["soc_pct"] is None
    assert set(p) == PAYLOAD_FIELDS


def test_payload_drifted_firmware_uses_epoch_ms():
    d = Device("como-charger-001", "como", "charger", "2.4.0", 11.0, None, None)
    p = telemetry_payload(d, TS, 7.4, "charging", 12.5)
    assert p["ts"] == 1793455500000  # 2026-10-31T14:05:00Z in epoch milliseconds


def test_payload_voltage_is_near_nominal():
    d = Device("como-charger-001", "como", "charger", "2.3.1", 11.0, None, None)
    p = telemetry_payload(d, TS, 7.4, "charging", 12.5, voltage_v=231.2)
    assert p["voltage_v"] == 231.2
