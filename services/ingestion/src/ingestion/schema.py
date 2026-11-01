"""Telemetry payload schema and the parser that guards the ingestion boundary.

``parse_telemetry`` never raises on bad input: it returns a ``ParseError``
whose ``reason`` is one of a small, bounded set so it can be a metric label.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

DeviceType = Literal["charger", "battery", "inverter"]
Status = Literal["charging", "idle", "discharging", "producing", "fault"]
Reason = Literal[
    "invalid_json", "invalid_timestamp", "missing_field", "invalid_value", "unknown_device"
]

# Integer timestamps (sent by charger firmware 2.4.0) are milliseconds since the Unix epoch.
# Only values between 2000-01-01 and 2100-01-01 are plausible; this also rejects epoch
# seconds, which would otherwise be misread as a date in January 1970.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MIN_EPOCH_MS = 946_684_800_000  # 2000-01-01T00:00:00Z
_MAX_EPOCH_MS = 4_102_444_800_000  # 2100-01-01T00:00:00Z


def _from_epoch_ms(value: int) -> datetime:
    if not _MIN_EPOCH_MS <= value < _MAX_EPOCH_MS:
        raise ValueError("integer timestamp must be epoch milliseconds between 2000 and 2100")
    return _EPOCH + timedelta(milliseconds=value)


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
    def _ts_must_be_iso_string_or_epoch_ms(cls, value: Any) -> Any:
        if isinstance(value, int) and not isinstance(value, bool):
            return _from_epoch_ms(value)
        if not isinstance(value, str | datetime):
            raise ValueError("timestamp must be an ISO 8601 string or integer epoch milliseconds")
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
    # The first validation message, for logs; not part of equality so callers can compare reasons.
    detail: str = field(default="", compare=False)


def _str_or_none(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _detail(error: ValidationError) -> str:
    first = error.errors()[0] if error.errors() else {}
    loc = ".".join(str(part) for part in first.get("loc", ())) or "payload"
    value = first.get("input")
    return f"{loc}: {first.get('msg', 'invalid')} (input_type={type(value).__name__})"[:200]


def _classify(error: ValidationError, data: dict[str, Any]) -> ParseError:
    first = error.errors()[0]
    device_type, firmware = _str_or_none(data, "device_type"), _str_or_none(data, "firmware")
    if first["type"] == "missing":
        return ParseError("missing_field", device_type, firmware, _detail(error))
    if first["loc"] and first["loc"][0] == "ts":
        return ParseError("invalid_timestamp", device_type, firmware, _detail(error))
    return ParseError("invalid_value", device_type, firmware, _detail(error))


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
