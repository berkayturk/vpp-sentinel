"""run_publisher against a fake aiomqtt client: connects, configures the client, reconnects."""

import aiomqtt
import pytest

from fleet_simulator import publisher as publisher_module
from fleet_simulator.faults import FaultState
from fleet_simulator.fleet import DEFAULT_FLEET, FleetConfig, build_fleet
from fleet_simulator.publisher import run_publisher

SMALL_FLEET = build_fleet(FleetConfig(DEFAULT_FLEET.sites[:1], 2, 1, 1))


class StopPublisher(Exception):
    """Raised by the fake broker to end an otherwise infinite loop."""


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port
        self.pending_calls_threshold = 10
        self.published: list[tuple[str, bytes]] = []
        FakeClient.instances.append(self)

    async def __aenter__(self) -> "FakeClient":
        if len(FakeClient.instances) == 1:
            raise aiomqtt.MqttError("connection refused")
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def publish(self, topic: str, payload: bytes, qos: int = 0) -> None:
        self.published.append((topic, payload))
        if len(self.published) >= len(SMALL_FLEET):
            raise StopPublisher()


async def test_run_publisher_reconnects_and_raises_pending_threshold(
    monkeypatch: pytest.MonkeyPatch,
):
    FakeClient.instances = []
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(publisher_module.aiomqtt, "Client", FakeClient)
    monkeypatch.setattr(publisher_module.asyncio, "sleep", fake_sleep)

    with pytest.raises(StopPublisher):
        await run_publisher("broker", 1883, SMALL_FLEET, lambda: FaultState(), 5.0)

    assert len(FakeClient.instances) == 2
    assert sleeps[0] == 1.0  # backoff after the refused connection
    connected = FakeClient.instances[1]
    assert connected.pending_calls_threshold == len(SMALL_FLEET) + 100
    assert len(connected.published) == len(SMALL_FLEET)
