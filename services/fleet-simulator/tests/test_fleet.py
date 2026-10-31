import random

from fleet_simulator.fleet import DEFAULT_FLEET, build_fleet, step_device


def test_default_fleet_has_300_devices():
    fleet = build_fleet(DEFAULT_FLEET)
    assert len(fleet) == 300
    assert sum(d.device_type == "charger" for d in fleet) == 150


def test_device_ids_follow_convention():
    fleet = build_fleet(DEFAULT_FLEET)
    assert "lugano-south-charger-001" in {d.id for d in fleet}


def test_build_fleet_is_deterministic():
    assert build_fleet(DEFAULT_FLEET) == build_fleet(DEFAULT_FLEET)


def test_step_device_returns_new_object():
    battery = next(d for d in build_fleet(DEFAULT_FLEET) if d.device_type == "battery")
    new, _, _ = step_device(battery, 12.0, 5.0, random.Random(1))
    assert new is not battery


def test_inverter_produces_zero_at_night():
    inv = next(d for d in build_fleet(DEFAULT_FLEET) if d.device_type == "inverter")
    _, power, status = step_device(inv, 1.0, 5.0, random.Random(1))
    assert power == 0.0 and status == "idle"


def test_battery_soc_stays_within_limits_over_many_steps():
    battery = next(d for d in build_fleet(DEFAULT_FLEET) if d.device_type == "battery")
    rng = random.Random(7)
    for hour in range(0, 24 * 12):
        battery, _, _ = step_device(battery, (hour / 12) % 24, 300.0, rng)
        assert 20.0 <= battery.soc_pct <= 100.0
