from ingestion.fleet_index import FleetIndex, load_fleet_index


class FakeStore:
    async def insert(self, t):  # pragma: no cover - unused here
        return "stored"

    async def expected_counts(self):
        return {("como", "charger"): 30, ("varese", "battery"): 15}

    async def device_index(self):
        return {
            "como-charger-001": ("como", "charger"),
            "varese-battery-001": ("varese", "battery"),
        }


async def test_load_fleet_index_derives_sites_and_groups():
    fleet = await load_fleet_index(FakeStore())
    assert isinstance(fleet, FleetIndex)
    assert fleet.sites == frozenset({"como", "varese"})
    assert fleet.group_of("como-charger-001") == ("como", "charger")
    assert fleet.group_of("ghost") is None
    assert fleet.expected[("como", "charger")] == 30


def test_fleet_index_is_immutable():
    fleet = FleetIndex(devices={}, expected={})
    try:
        fleet.devices = {"x": ("y", "z")}  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("FleetIndex must be frozen")
