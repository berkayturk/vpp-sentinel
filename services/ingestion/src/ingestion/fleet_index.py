"""The registered fleet, loaded once from the database and treated as the source of truth.

Metric labels come from here, never from payloads or topics, so a publisher
cannot create new time series or attribute messages to the wrong site.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from ingestion.store import TelemetryStore

Group = tuple[str, str]  # (site, device_type)


@dataclass(frozen=True)
class FleetIndex:
    devices: Mapping[str, Group]
    expected: Mapping[Group, int]
    sites: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "devices", MappingProxyType(dict(self.devices)))
        object.__setattr__(self, "expected", MappingProxyType(dict(self.expected)))
        object.__setattr__(self, "sites", frozenset(site for site, _ in self.devices.values()))

    def group_of(self, device_id: str) -> Group | None:
        return self.devices.get(device_id)


async def load_fleet_index(store: TelemetryStore) -> FleetIndex:
    return FleetIndex(devices=await store.device_index(), expected=await store.expected_counts())
