"""Telemetry payload schema and the parser that guards the ingestion boundary.

``parse_telemetry`` never raises on bad input: it returns a ``ParseError``
whose ``reason`` is one of a small, bounded set so it can be a metric label.

Known limitation: ``ts`` is accepted only as an ISO 8601 string. Devices on
firmware 2.4.0 send it as an integer epoch in milliseconds and are rejected
with ``invalid_timestamp``.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

DeviceType = Literal["charger", "battery", "inverter"]
Status = Literal["charging", "idle", "discharging", "producing", "fault"]
Reason = Literal["invalid_json", "invalid_timestamp", "missing_field", "invalid_value"]


class Telemetry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    device_id: str
    site: str
    device_type: DeviceType
    ts: datetime
    firmware: str
    status: Status
    power_kw: float
    energy_kwh: float = Field(ge=0)
    soc_pct: float | None = Field(default=None, ge=0, le=100)
    voltage_v: float = Field(gt=0)

    @field_validator("ts", mode="before")
    @classmethod
    def _ts_must_be_iso_string(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("timestamp must be an ISO 8601 string")
        return value

    @field_validator("ts")
    @classmethod
    def _ts_must_be_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must carry a timezone")
        return value.astimezone(UTC)


@dataclass(frozen=True)
class ParseError:
    reason: Reason
    device_type: str | None
    firmware: str | None


def _str_or_none(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _classify(error: ValidationError, data: dict[str, Any]) -> ParseError:
    first = error.errors()[0]
    device_type, firmware = _str_or_none(data, "device_type"), _str_or_none(data, "firmware")
    if first["type"] == "missing":
        return ParseError("missing_field", device_type, firmware)
    if first["loc"] and first["loc"][0] == "ts":
        return ParseError("invalid_timestamp", device_type, firmware)
    return ParseError("invalid_value", device_type, firmware)


def parse_telemetry(raw: bytes) -> Telemetry | ParseError:
    """Decode and validate one MQTT payload."""
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return ParseError("invalid_json", None, None)
    if not isinstance(data, dict):
        return ParseError("invalid_json", None, None)
    try:
        return Telemetry.model_validate(data)
    except ValidationError as exc:
        return _classify(exc, data)
