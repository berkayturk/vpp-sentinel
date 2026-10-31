from datetime import UTC, datetime, timedelta

from ingestion.registry import LastSeen, observe, reporting_counts

NOW = datetime(2026, 10, 31, 14, 5, 10, tzinfo=UTC)


def test_observe_returns_new_registry():
    r0 = LastSeen({})
    r1 = observe(r0, "a", NOW)
    assert r0.seen == {} and r1.seen == {"a": NOW}


def test_observe_keeps_latest_timestamp():
    r = observe(observe(LastSeen({}), "a", NOW), "a", NOW + timedelta(seconds=5))
    assert r.seen["a"] == NOW + timedelta(seconds=5)


def test_reporting_counts_respects_window():
    reg = observe(
        observe(LastSeen({}), "a", NOW - timedelta(seconds=10)), "b", NOW - timedelta(seconds=120)
    )
    devices = {"a": ("como", "charger"), "b": ("como", "charger"), "c": ("como", "battery")}
    assert reporting_counts(reg, devices, NOW) == {("como", "charger"): 1, ("como", "battery"): 0}


def test_reporting_counts_ignores_devices_not_in_index():
    reg = observe(LastSeen({}), "ghost", NOW)
    assert reporting_counts(reg, {"a": ("como", "charger")}, NOW) == {("como", "charger"): 0}
