"""MQTT consumer: parse → store → metrics → log, one message at a time.

``handle_message`` is pure orchestration over injected collaborators and
never raises on bad input; ``run_consumer`` binds it to a broker and keeps
the connection alive with exponential backoff.
"""

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime

import aiomqtt
import structlog

from ingestion.metrics import (
    observe_db_write,
    publish_gauges,
    record_duplicate,
    record_rejected,
    record_stored,
)
from ingestion.registry import LastSeen, observe, reporting_counts
from ingestion.schema import ParseError, Telemetry, parse_telemetry
from ingestion.store import TelemetryStore

log = structlog.get_logger("ingestion.consumer")

TOPIC_FILTER = "vpp/#"
TOPIC_PARTS = 5  # vpp/{site}/{device_type}/{device_id}/telemetry
GAUGE_REFRESH_SECONDS = 15.0
RECONNECT_INITIAL_SECONDS = 1.0
RECONNECT_MAX_SECONDS = 30.0


def _site_from_topic(topic: str) -> str | None:
    parts = topic.split("/")
    return parts[1] if len(parts) == TOPIC_PARTS else None


async def _store_with_timing(store: TelemetryStore, t: Telemetry) -> str:
    started = time.perf_counter()
    try:
        return await store.insert(t)
    finally:
        observe_db_write(time.perf_counter() - started)


async def handle_message(
    raw: bytes, topic: str, store: TelemetryStore, reg: LastSeen, now: datetime
) -> LastSeen:
    """Process one payload and return the (possibly updated) last-seen registry."""
    parsed = parse_telemetry(raw)
    if isinstance(parsed, ParseError):
        record_rejected(parsed, _site_from_topic(topic))
        log.warning(
            "telemetry.rejected", topic=topic, reason=parsed.reason, firmware=parsed.firmware
        )
        return reg
    try:
        result = await _store_with_timing(store, parsed)
    except Exception as exc:
        log.error("telemetry.store_failed", device_id=parsed.device_id, error=str(exc))
        return reg
    if result == "stored":
        record_stored(parsed, now)
        return observe(reg, parsed.device_id, now)
    if result == "duplicate":
        record_duplicate(parsed)
        return reg
    record_rejected(ParseError("unknown_device", parsed.device_type, parsed.firmware), parsed.site)
    log.warning("telemetry.unknown_device", device_id=parsed.device_id, site=parsed.site)
    return reg


async def refresh_gauges(store: TelemetryStore, reg: LastSeen, now: datetime) -> None:
    """Recompute the per-group reporting/expected gauges from the store and the registry."""
    index = await store.device_index()
    expected = await store.expected_counts()
    publish_gauges(reporting_counts(reg, index, now), expected)


class _Registry:
    """Holds the current immutable registry so two tasks can share it by reference."""

    def __init__(self) -> None:
        self.current = LastSeen({})


async def _gauge_loop(store: TelemetryStore, holder: _Registry) -> None:
    while True:
        try:
            await refresh_gauges(store, holder.current, datetime.now(UTC))
        except Exception as exc:
            log.error("gauges.refresh_failed", error=str(exc))
        await asyncio.sleep(GAUGE_REFRESH_SECONDS)


async def _consume(
    client: aiomqtt.Client, store: TelemetryStore, holder: _Registry, topic_filter: str
) -> None:
    await client.subscribe(topic_filter)
    async for message in client.messages:
        payload = message.payload if isinstance(message.payload, bytes) else b""
        holder.current = await handle_message(
            payload, str(message.topic), store, holder.current, datetime.now(UTC)
        )


async def run_consumer(
    broker_host: str,
    broker_port: int,
    store: TelemetryStore,
    topic_filter: str = TOPIC_FILTER,
    on_ready: Callable[[], None] | None = None,
) -> None:
    """Consume forever; reconnect with exponential backoff capped at 30 s."""
    holder = _Registry()
    backoff = RECONNECT_INITIAL_SECONDS
    gauges = asyncio.create_task(_gauge_loop(store, holder))
    try:
        while True:
            try:
                async with aiomqtt.Client(broker_host, broker_port) as client:
                    log.info(
                        "mqtt.connected", host=broker_host, port=broker_port, topic=topic_filter
                    )
                    backoff = RECONNECT_INITIAL_SECONDS
                    if on_ready:
                        on_ready()
                    await _consume(client, store, holder, topic_filter)
            except aiomqtt.MqttError as exc:
                log.warning("mqtt.disconnected", error=str(exc), retry_in_seconds=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_SECONDS)
    finally:
        gauges.cancel()
