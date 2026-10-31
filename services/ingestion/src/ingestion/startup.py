"""Retry helper for dependencies that come up after this service does (PostgreSQL, MQTT)."""

import asyncio
from collections.abc import Awaitable, Callable

import structlog

log = structlog.get_logger("ingestion.startup")

DEFAULT_RETRIES = 30
DEFAULT_INITIAL_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 10.0
RETRYABLE = (OSError, ConnectionError, TimeoutError)


async def wait_for[T](
    operation: Callable[[], Awaitable[T]],
    retries: int = DEFAULT_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Call ``operation`` until it succeeds with exponential backoff; re-raise after ``retries``."""
    delay = initial_delay
    for attempt in range(1, retries + 1):
        try:
            return await operation()
        except RETRYABLE as exc:
            if attempt == retries:
                raise
            log.warning("startup.waiting", attempt=attempt, error=str(exc), retry_in_seconds=delay)
            await sleep(delay)
            delay = min(delay * 2, MAX_DELAY_SECONDS)
    raise RuntimeError("unreachable")  # pragma: no cover
