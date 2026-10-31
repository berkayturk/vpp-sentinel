import pytest

from fleet_simulator.physics import charger_power_kw, next_soc_pct, solar_power_kw


def test_solar_is_zero_at_night():
    assert solar_power_kw(hour_utc=2.0, peak_kw=10.0) == 0.0


def test_solar_peaks_at_noon():
    assert solar_power_kw(12.0, 10.0) == pytest.approx(10.0)


def test_solar_morning_is_between_zero_and_peak():
    assert 0.0 < solar_power_kw(9.0, 10.0) < 10.0


def test_soc_never_exceeds_100():
    assert next_soc_pct(99.9, 50.0, 60.0, 3600) == 100.0


def test_soc_never_drops_below_20():
    assert next_soc_pct(20.5, -50.0, 60.0, 3600) == 20.0


def test_soc_charges_proportionally():
    # 6 kW for 1 h into 60 kWh = +10 %
    assert next_soc_pct(50.0, 6.0, 60.0, 3600) == pytest.approx(60.0)


def test_charger_power_zero_without_session():
    assert charger_power_kw(10.0, 11.0, session_active=False) == 0.0


def test_charger_power_rated_with_session():
    assert charger_power_kw(10.0, 11.0, session_active=True) == 11.0
