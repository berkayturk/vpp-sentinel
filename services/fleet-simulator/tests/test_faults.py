from datetime import UTC, datetime, timedelta

import pytest

from fleet_simulator.faults import (
    FaultState,
    apply_fault,
    clear_faults,
    effective_cadence,
    effective_firmware,
)
from fleet_simulator.fleet import DEFAULT_FLEET, build_fleet

NOW = datetime(2026, 10, 31, 14, 0, tzinfo=UTC)
FLEET = build_fleet(DEFAULT_FLEET)
SURGE = {"site": "lugano-south", "multiplier": 10, "duration_seconds": 180}
DRIFT = {"site": "como", "device_type": "charger", "fraction": 0.4}


def test_apply_returns_new_state():
    s0 = FaultState()
    s1 = apply_fault(s0, "load-surge", SURGE, NOW)
    assert s0.load_surge is None and s1.load_surge is not None


def test_load_surge_only_hits_target_site_until_expiry():
    s = apply_fault(FaultState(), "load-surge", SURGE, NOW)
    hit = next(d for d in FLEET if d.site == "lugano-south")
    miss = next(d for d in FLEET if d.site == "como")
    assert effective_cadence(s, hit, 5.0, NOW) == 0.5
    assert effective_cadence(s, miss, 5.0, NOW) == 5.0
    assert effective_cadence(s, hit, 5.0, NOW + timedelta(seconds=181)) == 5.0


def test_firmware_drift_hits_exact_fraction():
    s = apply_fault(FaultState(), "firmware-drift", DRIFT, NOW)
    chargers = [d for d in FLEET if d.site == "como" and d.device_type == "charger"]
    drifted = [d for d in chargers if effective_firmware(s, d, FLEET) == "2.4.0"]
    assert len(drifted) == 12  # 40 % of 30
    other = next(d for d in FLEET if d.site == "varese" and d.device_type == "charger")
    assert effective_firmware(s, other, FLEET) == "2.3.1"


def test_firmware_drift_is_deterministic():
    s = apply_fault(FaultState(), "firmware-drift", DRIFT, NOW)
    first = [effective_firmware(s, d, FLEET) for d in FLEET]
    assert first == [effective_firmware(s, d, FLEET) for d in FLEET]


def test_unknown_fault_rejected():
    with pytest.raises(ValueError):
        apply_fault(FaultState(), "meteor", {}, NOW)


def test_invalid_fraction_rejected():
    with pytest.raises(ValueError):
        apply_fault(FaultState(), "firmware-drift", {**DRIFT, "fraction": 1.5}, NOW)


def test_missing_param_rejected():
    with pytest.raises(ValueError):
        apply_fault(FaultState(), "load-surge", {"site": "como"}, NOW)


def test_unknown_site_rejected():
    with pytest.raises(ValueError):
        apply_fault(FaultState(), "load-surge", {**SURGE, "site": "atlantis"}, NOW)


def test_clear_resets_everything():
    s = apply_fault(FaultState(), "firmware-drift", DRIFT, NOW)
    assert clear_faults(s) == FaultState()
