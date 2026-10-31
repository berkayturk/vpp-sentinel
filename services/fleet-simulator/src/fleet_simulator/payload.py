"""MQTT topic and telemetry payload construction.

Firmware ``2.4.0`` encodes ``ts`` as epoch milliseconds instead of ISO 8601.
That is the payload drift the ingestion parser currently does not accept.
"""

from datetime import UTC, datetime
from typing import Any

from fleet_simulator.fleet import Device

TOPIC_PREFIX = "vpp"
DRIFTED_FIRMWARE = "2.4.0"
NOMINAL_VOLTAGE_V = 230.0


def topic_for(device: Device) -> str:
    return f"{TOPIC_PREFIX}/{device.site}/{device.device_type}/{device.id}/telemetry"


def _encode_ts(ts: datetime, firmware: str) -> str | int:
    utc = ts.astimezone(UTC)
    if firmware == DRIFTED_FIRMWARE:
        return int(utc.timestamp() * 1000)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def telemetry_payload(
    device: Device,
    ts: datetime,
    power_kw: float,
    status: str,
    energy_kwh: float,
    voltage_v: float = NOMINAL_VOLTAGE_V,
) -> dict[str, Any]:
    return {
        "device_id": device.id,
        "site": device.site,
        "device_type": device.device_type,
        "ts": _encode_ts(ts, device.firmware),
        "firmware": device.firmware,
        "status": status,
        "power_kw": round(power_kw, 3),
        "energy_kwh": round(energy_kwh, 3),
        "soc_pct": None if device.soc_pct is None else round(device.soc_pct, 1),
        "voltage_v": round(voltage_v, 1),
    }
