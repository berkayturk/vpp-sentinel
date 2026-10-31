"""Pure, side-effect-free physics for the three device types.

Everything here is deliberately simple: the point is domain-plausible
numbers (no solar at night, state of charge that respects its limits),
not an accurate plant model.
"""

import math

SOLAR_SUNRISE_HOUR = 6.0
SOLAR_SUNSET_HOUR = 18.0
SOC_MIN_PCT = 20.0
SOC_MAX_PCT = 100.0
SECONDS_PER_HOUR = 3600.0


def solar_power_kw(hour_utc: float, peak_kw: float) -> float:
    """Half-sine production curve: zero outside daylight, ``peak_kw`` at solar noon."""
    if hour_utc <= SOLAR_SUNRISE_HOUR or hour_utc >= SOLAR_SUNSET_HOUR:
        return 0.0
    daylight = SOLAR_SUNSET_HOUR - SOLAR_SUNRISE_HOUR
    return peak_kw * math.sin(math.pi * (hour_utc - SOLAR_SUNRISE_HOUR) / daylight)


def next_soc_pct(soc_pct: float, power_kw: float, capacity_kwh: float, dt_seconds: float) -> float:
    """State of charge after ``dt_seconds`` at ``power_kw`` (positive charges), clamped."""
    delta_kwh = power_kw * dt_seconds / SECONDS_PER_HOUR
    unclamped = soc_pct + 100.0 * delta_kwh / capacity_kwh
    return min(SOC_MAX_PCT, max(SOC_MIN_PCT, unclamped))


def charger_power_kw(hour_utc: float, rated_kw: float, session_active: bool) -> float:
    """A charger draws its rated power while a session is active and nothing otherwise."""
    return rated_kw if session_active else 0.0
