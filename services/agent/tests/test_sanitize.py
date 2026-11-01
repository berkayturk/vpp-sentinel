"""Nothing identifying or secret survives the sanitiser."""

import pytest

from agent.sanitize import pseudonymise_device_id, sanitize_text

SALT = b"test-salt"


def test_device_id_becomes_stable_pseudonym_keeping_site_and_type():
    a = pseudonymise_device_id("lugano-south-charger-001", SALT)
    b = pseudonymise_device_id("lugano-south-charger-001", SALT)
    assert a == b
    assert a.startswith("lugano-south-charger-#")
    assert "001" not in a


def test_different_ids_get_different_pseudonyms():
    a = pseudonymise_device_id("como-battery-003", SALT)
    b = pseudonymise_device_id("como-battery-004", SALT)
    assert a != b


def test_sanitize_text_replaces_every_device_id():
    text = (
        "telemetry.rejected topic=vpp/como/charger/como-charger-012/telemetry "
        'device_id="como-charger-012"'
    )
    out = sanitize_text(text, SALT)
    assert "como-charger-012" not in out
    assert out.count("como-charger-#") == 2


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_abcdef1234567890ABCDEF",
        "gho_abcdef1234567890ABCDEF",
        "github_pat_11AAAAAAA0bbbbbbbbbbbbbb_cccccccccccccccccccc",
        "sk-ant-api03-xyzXYZ123_-abc",
        "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
    ],
)
def test_tokens_are_masked(secret: str):
    out = sanitize_text(f"error pushing: {secret} rejected", SALT)
    assert secret not in out
    assert "<token>" in out


def test_ip_addresses_are_masked():
    out = sanitize_text("connected from 10.244.0.7:51234 to 172.18.0.2", SALT)
    assert "10.244.0.7" not in out and "172.18.0.2" not in out
    assert out.count("<ip>") == 2


def test_metric_labels_without_identifiers_are_unchanged():
    text = '{site="como", device_type="charger", firmware="2.4.0"} 18'
    assert sanitize_text(text, SALT) == text


def test_default_salt_is_used_when_none_given(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_PSEUDONYM_SALT", "env-salt")
    one = sanitize_text("varese-inverter-009", None)
    two = sanitize_text("varese-inverter-009", None)
    assert one == two and "009" not in one
