"""Structured JSON logging shared by every entrypoint in this service.

Both structlog loggers and plain ``logging`` loggers (aiomqtt, uvicorn,
asyncpg) end up on the same root handler, rendered by the same JSON
processor chain, so every stdout line is one JSON object.
"""

import logging
import sys

import structlog

from agent.sanitize import sanitize_text


def _sanitise_event(_logger, _method, event_dict: dict) -> dict:
    """No device id, address or token leaves this process in a log line."""
    return _sanitise_value(event_dict)


def _sanitise_value(value):
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {k: _sanitise_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitise_value(v) for v in value]
    return value


def _pre_chain() -> list:
    return [
        structlog.contextvars.merge_contextvars,
        _sanitise_event,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", key="ts"),
    ]


def configure_logging(service: str, level: str = "INFO") -> None:
    pre_chain = _pre_chain()
    structlog.configure(
        processors=[*pre_chain, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=pre_chain,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                # Tracebacks become a list of frames under "exception", not a text blob.
                structlog.processors.dict_tracebacks,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    # Replace rather than append so a second call stays idempotent.
    root.handlers = [handler]
    root.setLevel(level)
    # HTTP client chatter would drown the incident events in Loki.
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    structlog.contextvars.bind_contextvars(service=service)
    # An exception that escapes the event loop would otherwise print a plain-text
    # traceback to stderr, which the pod log merges with stdout.
    sys.excepthook = _log_uncaught


def _log_uncaught(exc_type, exc_value, exc_tb) -> None:
    structlog.get_logger("process").critical(
        "process.crashed", exc_info=(exc_type, exc_value, exc_tb)
    )
