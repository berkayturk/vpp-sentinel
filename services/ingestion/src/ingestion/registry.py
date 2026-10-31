"""Which devices have reported recently, kept as an immutable mapping."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType

FRESHNESS_WINDOW_SECONDS = 60.0

DeviceIndex = Mapping[str, tuple[str, str]]  # device id -> (site, device_type)


@dataclass(frozen=True)
class LastSeen:
    seen: Mapping[str, datetime]


def observe(reg: LastSeen, device_id: str, at: datetime) -> LastSeen:
    """A new registry with ``device_id`` last seen ``at``."""
    return LastSeen(MappingProxyType({**reg.seen, device_id: at}))


def reporting_counts(
    reg: LastSeen,
    devices: DeviceIndex,
    now: datetime,
    window_seconds: float = FRESHNESS_WINDOW_SECONDS,
) -> dict[tuple[str, str], int]:
    """Devices per (site, device_type) seen within the window; every known group is present."""
    cutoff = now - timedelta(seconds=window_seconds)
    counts = dict.fromkeys(devices.values(), 0)
    for device_id, group in devices.items():
        last = reg.seen.get(device_id)
        if last is not None and last >= cutoff:
            counts[group] += 1
    return counts
