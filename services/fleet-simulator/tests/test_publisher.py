import json
import random
from datetime import UTC, datetime, timedelta

from fleet_simulator.faults import FaultState, apply_fault
from fleet_simulator.fleet import DEFAULT_FLEET, build_fleet
from fleet_simulator.publisher import DeviceRun, build_message, device_loop

NOW = datetime(2026, 10, 31, 14, 0, tzinfo=UTC)
FLEET = build_fleet(DEFAULT_FLEET)
CHARGER = next(d for d in FLEET if d.id == "como-charger-001")


class FakePublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes]] = []

    async def publish(self, topic: str, payload: bytes) -> None:
        self.messages.append((topic, payload))


def test_build_message_accumulates_energy_and_returns_new_run():
    run = DeviceRun(CHARGER, energy_kwh=10.0)
    topic, payload, new_run = build_message(run, FaultState(), FLEET, NOW, random.Random(1), 5.0)
    body = json.loads(payload)
    assert topic == "vpp/como/charger/como-charger-001/telemetry"
    assert body["device_id"] == "como-charger-001"
    assert new_run is not run
    assert new_run.energy_kwh >= 10.0


def test_build_message_reports_drifted_firmware_when_fault_active():
    state = apply_fault(
        FaultState(),
        "firmware-drift",
        {"site": "como", "device_type": "charger", "fraction": 0.4},
        NOW,
    )
    run = DeviceRun(CHARGER, energy_kwh=0.0)
    _, payload, _ = build_message(run, state, FLEET, NOW, random.Random(1), 5.0)
    body = json.loads(payload)
    assert body["firmware"] == "2.4.0"
    assert isinstance(body["ts"], int)


async def test_device_loop_publishes_each_tick_and_honours_cadence():
    publisher = FakePublisher()
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    clock = iter(NOW + timedelta(seconds=5 * i) for i in range(10))
    await device_loop(
        publisher,
        CHARGER,
        FLEET,
        lambda: FaultState(),
        cadence_seconds=5.0,
        clock=lambda: next(clock),
        sleep=fake_sleep,
        rng=random.Random(1),
        max_ticks=3,
    )
    assert len(publisher.messages) == 3
    assert sleeps == [5.0, 5.0, 5.0]


async def test_device_loop_speeds_up_under_load_surge():
    publisher = FakePublisher()
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    surged = apply_fault(
        FaultState(),
        "load-surge",
        {"site": "como", "multiplier": 10, "duration_seconds": 180},
        NOW,
    )
    await device_loop(
        publisher,
        CHARGER,
        FLEET,
        lambda: surged,
        cadence_seconds=5.0,
        clock=lambda: NOW,
        sleep=fake_sleep,
        rng=random.Random(1),
        max_ticks=2,
    )
    assert sleeps == [0.5, 0.5]
