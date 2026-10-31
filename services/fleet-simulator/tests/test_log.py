"""Every line written to stdout is one JSON object, whichever library wrote it."""

import json
import logging

import structlog

from fleet_simulator.log import configure_logging


def _last_json_line(capsys) -> dict:
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    return json.loads(lines[-1])


def test_library_log_lines_are_json(capsys):
    configure_logging("fleet-simulator", "INFO")
    logging.getLogger("aiomqtt").warning("pending publish calls: %d", 7)
    record = _last_json_line(capsys)
    assert record["event"] == "pending publish calls: 7"
    assert record["level"] == "warning"
    assert record["logger"] == "aiomqtt"
    assert record["service"] == "fleet-simulator"
    assert "ts" in record


def test_structlog_lines_keep_their_fields(capsys):
    configure_logging("fleet-simulator", "INFO")
    structlog.get_logger("fleet_simulator.publisher").warning("mqtt.disconnected", error="x")
    record = _last_json_line(capsys)
    assert record["event"] == "mqtt.disconnected"
    assert record["error"] == "x"
    assert record["logger"] == "fleet_simulator.publisher"
    assert record["service"] == "fleet-simulator"


def test_level_filter_applies_to_library_loggers(capsys):
    configure_logging("fleet-simulator", "WARNING")
    logging.getLogger("uvicorn").info("hidden")
    assert capsys.readouterr().out.strip() == ""
