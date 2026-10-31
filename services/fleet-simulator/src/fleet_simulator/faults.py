"""Fault switches: immutable state plus the pure functions that interpret it.

Two faults exist, matching the incident scenarios in the design document:

* ``load-surge`` — one site publishes ``multiplier`` times faster until a deadline.
* ``firmware-drift`` — a fraction of one site's devices of one type report the
  drifted firmware whose payload the ingestion parser rejects.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from fleet_simulator.fleet import DEFAULT_FLEET, DEVICE_TYPES, Device

DRIFTED_FIRMWARE = "2.4.0"
FAULT_LOAD_SURGE = "load-surge"
FAULT_FIRMWARE_DRIFT = "firmware-drift"
KNOWN_FAULTS = (FAULT_LOAD_SURGE, FAULT_FIRMWARE_DRIFT)


@dataclass(frozen=True)
class LoadSurge:
    site: str
    multiplier: int
    until: datetime


@dataclass(frozen=True)
class FirmwareDrift:
    site: str
    device_type: str
    fraction: float


@dataclass(frozen=True)
class FaultState:
    load_surge: LoadSurge | None = None
    firmware_drift: FirmwareDrift | None = None


def _require(params: dict[str, Any], key: str) -> Any:
    if key not in params:
        raise ValueError(f"missing parameter '{key}'")
    return params[key]


def _require_site(params: dict[str, Any], sites: tuple[str, ...]) -> str:
    site = _require(params, "site")
    if site not in sites:
        raise ValueError(f"unknown site '{site}'")
    return site


def _parse_load_surge(params: dict[str, Any], now: datetime, sites: tuple[str, ...]) -> LoadSurge:
    site = _require_site(params, sites)
    multiplier = int(_require(params, "multiplier"))
    duration = float(_require(params, "duration_seconds"))
    if multiplier < 1:
        raise ValueError("multiplier must be >= 1")
    if duration <= 0:
        raise ValueError("duration_seconds must be > 0")
    return LoadSurge(site, multiplier, now + timedelta(seconds=duration))


def _parse_firmware_drift(params: dict[str, Any], sites: tuple[str, ...]) -> FirmwareDrift:
    site = _require_site(params, sites)
    device_type = _require(params, "device_type")
    fraction = float(_require(params, "fraction"))
    if device_type not in DEVICE_TYPES:
        raise ValueError(f"unknown device_type '{device_type}'")
    if not 0.0 < fraction <= 1.0:
        raise ValueError("fraction must be in (0, 1]")
    return FirmwareDrift(site, device_type, fraction)


def apply_fault(
    state: FaultState,
    name: str,
    params: dict[str, Any],
    now: datetime,
    sites: tuple[str, ...] = DEFAULT_FLEET.sites,
) -> FaultState:
    """Return a new state with fault ``name`` switched on; ``ValueError`` on bad input."""
    if name == FAULT_LOAD_SURGE:
        return FaultState(_parse_load_surge(params, now, sites), state.firmware_drift)
    if name == FAULT_FIRMWARE_DRIFT:
        return FaultState(state.load_surge, _parse_firmware_drift(params, sites))
    raise ValueError(f"unknown fault '{name}'; known faults: {', '.join(KNOWN_FAULTS)}")


def clear_faults(state: FaultState) -> FaultState:
    return FaultState()


def effective_cadence(
    state: FaultState, device: Device, base_seconds: float, now: datetime
) -> float:
    surge = state.load_surge
    if surge is None or surge.site != device.site or now >= surge.until:
        return base_seconds
    return base_seconds / surge.multiplier


def effective_firmware(state: FaultState, device: Device, fleet: tuple[Device, ...]) -> str:
    """The firmware ``device`` reports: drifted for the first ``fraction`` of its peers."""
    drift = state.firmware_drift
    if drift is None or device.site != drift.site or device.device_type != drift.device_type:
        return device.firmware
    peers = sorted(
        d.id for d in fleet if d.site == drift.site and d.device_type == drift.device_type
    )
    drifted_count = round(drift.fraction * len(peers))
    return DRIFTED_FIRMWARE if device.id in peers[:drifted_count] else device.firmware


def state_to_dict(state: FaultState) -> dict[str, Any]:
    """JSON-friendly view of the state (datetimes as ISO 8601)."""
    raw = asdict(state)
    if raw["load_surge"] is not None:
        raw["load_surge"]["until"] = state.load_surge.until.isoformat()
    return raw
