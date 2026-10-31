"""Per-device publishing loops and the MQTT connection that carries them.

``build_message`` and ``device_loop`` are pure with respect to I/O: the
publisher, clock and sleep are injected so they can be exercised in tests.
``run_publisher`` binds them to a real broker and reconnects on failure.
"""

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

import aiomqtt
import structlog

from fleet_simulator.faults import FaultState, effective_cadence, effective_firmware
from fleet_simulator.fleet import Device, step_device
from fleet_simulator.payload import telemetry_payload, topic_for

log = structlog.get_logger("fleet_simulator.publisher")

SECONDS_PER_HOUR = 3600.0
RECONNECT_INITIAL_SECONDS = 1.0
RECONNECT_MAX_SECONDS = 30.0
PENDING_CALLS_HEADROOM = 100
VOLTAGE_JITTER_V = 3.0
NOMINAL_VOLTAGE_V = 230.0

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]
StateGetter = Callable[[], FaultState]


class Publisher(Protocol):
    async def publish(self, topic: str, payload: bytes) -> None: ...


@dataclass(frozen=True)
class DeviceRun:
    """Mutable-looking per-device state, carried immutably from tick to tick."""

    device: Device
    energy_kwh: float


def _hour_utc(now: datetime) -> float:
    return now.hour + now.minute / 60.0 + now.second / 3600.0


def build_message(
    run: DeviceRun,
    state: FaultState,
    fleet: tuple[Device, ...],
    now: datetime,
    rng: random.Random,
    dt_seconds: float,
) -> tuple[str, bytes, DeviceRun]:
    """One tick: step the device and encode what it reports."""
    firmware = effective_firmware(state, run.device, fleet)
    reporting = replace(run.device, firmware=firmware)
    stepped, power_kw, status = step_device(reporting, _hour_utc(now), dt_seconds, rng)
    energy_kwh = run.energy_kwh + abs(power_kw) * dt_seconds / SECONDS_PER_HOUR
    voltage = NOMINAL_VOLTAGE_V + rng.uniform(-VOLTAGE_JITTER_V, VOLTAGE_JITTER_V)
    payload = telemetry_payload(stepped, now, power_kw, status, energy_kwh, voltage)
    # Keep the device's configured firmware; the drift is applied per tick from fault state.
    persisted = replace(stepped, firmware=run.device.firmware)
    return topic_for(stepped), json.dumps(payload).encode(), DeviceRun(persisted, energy_kwh)


async def device_loop(
    publisher: Publisher,
    device: Device,
    fleet: tuple[Device, ...],
    state_getter: StateGetter,
    cadence_seconds: float,
    clock: Clock = lambda: datetime.now(UTC),
    sleep: Sleep = asyncio.sleep,
    rng: random.Random | None = None,
    max_ticks: int | None = None,
) -> None:
    """Publish ``device``'s telemetry forever (or for ``max_ticks`` ticks in tests)."""
    rng = rng or random.Random(hash(device.id))
    run = DeviceRun(device, energy_kwh=0.0)
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        now = clock()
        state = state_getter()
        interval = effective_cadence(state, run.device, cadence_seconds, now)
        topic, payload, run = build_message(run, state, fleet, now, rng, interval)
        await publisher.publish(topic, payload)
        ticks += 1
        await sleep(interval)


class _MqttPublisher:
    def __init__(self, client: aiomqtt.Client) -> None:
        self._client = client

    async def publish(self, topic: str, payload: bytes) -> None:
        await self._client.publish(topic, payload, qos=0)


async def run_publisher(
    broker_host: str,
    broker_port: int,
    fleet: tuple[Device, ...],
    state_getter: StateGetter,
    cadence_seconds: float,
) -> None:
    """Run one loop per device over a single MQTT connection; reconnect with backoff."""
    backoff = RECONNECT_INITIAL_SECONDS
    while True:
        try:
            async with aiomqtt.Client(broker_host, broker_port) as client:
                # Every device awaits its own publish, so in-flight calls legitimately reach
                # fleet size; raise aiomqtt's warning threshold (an attribute, not a kwarg).
                client.pending_calls_threshold = len(fleet) + PENDING_CALLS_HEADROOM
                log.info("mqtt.connected", host=broker_host, port=broker_port, devices=len(fleet))
                backoff = RECONNECT_INITIAL_SECONDS
                publisher = _MqttPublisher(client)
                await asyncio.gather(
                    *(
                        device_loop(publisher, d, fleet, state_getter, cadence_seconds)
                        for d in fleet
                    )
                )
        except aiomqtt.MqttError as exc:
            log.warning("mqtt.disconnected", error=str(exc), retry_in_seconds=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_SECONDS)
