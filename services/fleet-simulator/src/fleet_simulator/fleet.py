"""Fleet composition and per-device time stepping.

A ``Device`` is immutable; ``step_device`` returns a new one together with
the power and status it reports for this interval.
"""

import random
from dataclasses import dataclass, replace

from fleet_simulator.physics import charger_power_kw, next_soc_pct, solar_power_kw

DEVICE_TYPES = ("charger", "battery", "inverter")
NORMAL_FIRMWARE = "2.3.1"

CHARGER_RATED_KW = (7.4, 11.0, 22.0)
BATTERY_RATED_KW = 50.0
BATTERY_CAPACITY_KWH = 100.0
INVERTER_PEAK_KW = 25.0

# Charging sessions cluster around commute hours.
CHARGER_PEAK_HOURS = frozenset(range(7, 10)) | frozenset(range(17, 23))
CHARGER_SESSION_PROBABILITY_PEAK = 0.7
CHARGER_SESSION_PROBABILITY_OFF_PEAK = 0.25


@dataclass(frozen=True)
class Device:
    id: str
    site: str
    device_type: str
    firmware: str
    rated_kw: float
    capacity_kwh: float | None
    soc_pct: float | None


@dataclass(frozen=True)
class FleetConfig:
    sites: tuple[str, ...]
    chargers_per_site: int
    batteries_per_site: int
    inverters_per_site: int
    firmware: str = NORMAL_FIRMWARE


DEFAULT_FLEET = FleetConfig(
    sites=("lugano-south", "lugano-north", "bellinzona", "como", "varese"),
    chargers_per_site=30,
    batteries_per_site=15,
    inverters_per_site=15,
)


def _device_id(site: str, device_type: str, index: int) -> str:
    return f"{site}-{device_type}-{index:03d}"


def _charger(site: str, index: int, firmware: str) -> Device:
    rated = CHARGER_RATED_KW[index % len(CHARGER_RATED_KW)]
    return Device(_device_id(site, "charger", index), site, "charger", firmware, rated, None, None)


def _battery(site: str, index: int, firmware: str) -> Device:
    initial_soc = 40.0 + 10.0 * (index % 6)
    return Device(
        _device_id(site, "battery", index),
        site,
        "battery",
        firmware,
        BATTERY_RATED_KW,
        BATTERY_CAPACITY_KWH,
        initial_soc,
    )


def _inverter(site: str, index: int, firmware: str) -> Device:
    return Device(
        _device_id(site, "inverter", index),
        site,
        "inverter",
        firmware,
        INVERTER_PEAK_KW,
        None,
        None,
    )


def build_fleet(config: FleetConfig) -> tuple[Device, ...]:
    """Deterministically build every device of every site in ``config``."""
    devices: list[Device] = []
    for site in config.sites:
        devices += [
            _charger(site, i, config.firmware) for i in range(1, config.chargers_per_site + 1)
        ]
        devices += [
            _battery(site, i, config.firmware) for i in range(1, config.batteries_per_site + 1)
        ]
        devices += [
            _inverter(site, i, config.firmware) for i in range(1, config.inverters_per_site + 1)
        ]
    return tuple(devices)


def _step_inverter(device: Device, hour_utc: float) -> tuple[Device, float, str]:
    power = solar_power_kw(hour_utc, device.rated_kw)
    return replace(device), power, "producing" if power > 0 else "idle"


def _step_battery(
    device: Device, dt_seconds: float, rng: random.Random
) -> tuple[Device, float, str]:
    assert device.soc_pct is not None and device.capacity_kwh is not None
    if device.soc_pct >= 100.0:
        charging = False
    elif device.soc_pct <= 20.0:
        charging = True
    else:
        charging = rng.random() < 0.5
    magnitude = device.rated_kw * rng.uniform(0.2, 1.0)
    power = magnitude if charging else -magnitude
    new_soc = next_soc_pct(device.soc_pct, power, device.capacity_kwh, dt_seconds)
    return replace(device, soc_pct=new_soc), power, "charging" if charging else "discharging"


def _step_charger(device: Device, hour_utc: float, rng: random.Random) -> tuple[Device, float, str]:
    peak = int(hour_utc) % 24 in CHARGER_PEAK_HOURS
    probability = CHARGER_SESSION_PROBABILITY_PEAK if peak else CHARGER_SESSION_PROBABILITY_OFF_PEAK
    active = rng.random() < probability
    power = charger_power_kw(hour_utc, device.rated_kw, active)
    return replace(device), power, "charging" if active else "idle"


def step_device(
    device: Device, hour_utc: float, dt_seconds: float, rng: random.Random
) -> tuple[Device, float, str]:
    """Advance one device by ``dt_seconds``; returns (new device, power_kw, status)."""
    if device.device_type == "inverter":
        return _step_inverter(device, hour_utc)
    if device.device_type == "battery":
        return _step_battery(device, dt_seconds, rng)
    return _step_charger(device, hour_utc, rng)
