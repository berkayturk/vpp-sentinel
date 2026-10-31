"""Every line written to stdout is one JSON object, whichever library wrote it."""

import json
import logging
import sys

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


def test_exceptions_carry_a_structured_traceback(capsys):
    configure_logging("fleet-simulator", "INFO")
    try:
        raise ZeroDivisionError("division by zero")
    except ZeroDivisionError:
        logging.getLogger("asyncio").exception("Task exception was never retrieved")
    record = _last_json_line(capsys)
    assert record["event"] == "Task exception was never retrieved"
    frames = record["exception"][0]["exc_type"], record["exception"][0]["frames"]
    assert frames[0] == "ZeroDivisionError"
    assert any(f["name"] == "test_exceptions_carry_a_structured_traceback" for f in frames[1])


def test_uncaught_exception_is_logged_as_json(capsys):
    configure_logging("fleet-simulator", "INFO")
    try:
        raise RuntimeError("worker crashed")
    except RuntimeError as exc:
        sys.excepthook(type(exc), exc, exc.__traceback__)
    record = _last_json_line(capsys)
    assert record["event"] == "process.crashed"
    assert record["level"] == "critical"
    assert record["exception"][0]["exc_type"] == "RuntimeError"
