"""Every line written to stdout is one JSON object, whichever library wrote it."""

import json
import logging

import structlog

from ingestion.log import configure_logging


def _last_json_line(capsys) -> dict:
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    return json.loads(lines[-1])


def test_library_log_lines_are_json(capsys):
    configure_logging("ingestion", "INFO")
    logging.getLogger("aiomqtt").warning("pending publish calls: %d", 7)
    record = _last_json_line(capsys)
    assert record["event"] == "pending publish calls: 7"
    assert record["level"] == "warning"
    assert record["logger"] == "aiomqtt"
    assert record["service"] == "ingestion"
    assert "ts" in record


def test_structlog_lines_keep_their_fields(capsys):
    configure_logging("ingestion", "INFO")
    structlog.get_logger("ingestion.consumer").warning("telemetry.rejected", reason="invalid_json")
    record = _last_json_line(capsys)
    assert record["event"] == "telemetry.rejected"
    assert record["reason"] == "invalid_json"
    assert record["logger"] == "ingestion.consumer"
    assert record["service"] == "ingestion"


def test_level_filter_applies_to_library_loggers(capsys):
    configure_logging("ingestion", "WARNING")
    logging.getLogger("asyncpg").info("hidden")
    assert capsys.readouterr().out.strip() == ""
