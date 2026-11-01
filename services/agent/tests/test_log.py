"""The agent's log lines are JSON and carry no identifiers or secrets."""

import json

import structlog

from agent.log import configure_logging


def _last_json_line(capsys) -> dict:
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    return json.loads(lines[-1])


def test_every_log_event_is_sanitised(capsys):
    configure_logging("agent", "INFO")
    structlog.get_logger("agent.service").warning(
        "incident.page",
        labels={"instance": "10.244.0.7:8000"},
        text="como-charger-012 ghp_abcdefABCDEF123456",
    )
    record = _last_json_line(capsys)
    flat = json.dumps(record)
    assert "10.244.0.7" not in flat and "como-charger-012" not in flat and "ghp_" not in flat
