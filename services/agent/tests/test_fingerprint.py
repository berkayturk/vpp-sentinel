from datetime import UTC, datetime

from agent.fingerprint import Alert, fingerprint, slug

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)


def _alert(**labels: str) -> Alert:
    return Alert(name=labels["alertname"], labels=labels, annotations={}, starts_at=NOW)


def test_fingerprint_is_alertname_site_device_type():
    a = _alert(alertname="VppSiteDevicesDark", site="como", device_type="charger", pod="x")
    assert fingerprint(a) == "VppSiteDevicesDark|como|charger"


def test_missing_labels_become_dashes():
    a = _alert(alertname="VppIngestionLagHigh", severity="warning")
    assert fingerprint(a) == "VppIngestionLagHigh|-|-"


def test_slug_is_branch_safe():
    assert slug("VppSiteDevicesDark|como|charger") == "vppsitedevicesdark-como-charger"
    assert slug("VppIngestionLagHigh|-|-") == "vppingestionlaghigh"


def test_alert_exposes_severity_defaulting_to_warning():
    assert _alert(alertname="A", severity="critical").severity == "critical"
    assert _alert(alertname="A").severity == "warning"


def test_alert_is_immutable():
    a = _alert(alertname="A")
    try:
        a.name = "B"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("Alert must be frozen")
