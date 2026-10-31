"""run_consumer against a fake aiomqtt client: reconnects with backoff, keeps consuming."""

import aiomqtt
import pytest

from ingestion import consumer as consumer_module
from ingestion.consumer import run_consumer
from ingestion.schema import Telemetry
from ingestion.store import InsertResult

GOOD = (
    b'{"device_id":"como-charger-001","site":"como","device_type":"charger",'
    b'"ts":"2026-10-31T14:05:00Z","firmware":"2.3.1","status":"charging",'
    b'"power_kw":7.4,"energy_kwh":12.5,"soc_pct":null,"voltage_v":231.2}'
)


class StopConsumer(Exception):
    """Raised by the fake broker to end an otherwise infinite loop."""


class FakeMessage:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


class FakeStore:
    def __init__(self) -> None:
        self.inserted: list[Telemetry] = []

    async def insert(self, t: Telemetry) -> InsertResult:
        self.inserted.append(t)
        return "stored"

    async def expected_counts(self) -> dict[tuple[str, str], int]:
        return {}

    async def device_index(self) -> dict[str, tuple[str, str]]:
        return {}


class FakeBroker:
    """Scripted connection attempts: fail, then serve two messages and drop, then stop."""

    def __init__(self) -> None:
        self.attempts = 0
        self.subscriptions: list[str] = []

    def client(self, host: str, port: int) -> "FakeBroker":
        self.attempts += 1
        return self

    async def __aenter__(self) -> "FakeBroker":
        if self.attempts == 1:
            raise aiomqtt.MqttError("connection refused")
        if self.attempts >= 3:
            raise StopConsumer()
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def subscribe(self, topic: str) -> None:
        self.subscriptions.append(topic)

    @property
    async def messages(self):
        yield FakeMessage("vpp/como/charger/como-charger-001/telemetry", GOOD)
        yield FakeMessage("vpp/x/y/z/telemetry", b"{bad")
        raise aiomqtt.MqttError("broker went away")


async def test_reconnects_with_backoff_and_keeps_consuming(monkeypatch: pytest.MonkeyPatch):
    broker = FakeBroker()
    sleeps: list[float] = []
    ready_calls: list[None] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(consumer_module.aiomqtt, "Client", broker.client)
    monkeypatch.setattr(consumer_module.asyncio, "sleep", fake_sleep)
    store = FakeStore()

    with pytest.raises(StopConsumer):
        await run_consumer("broker", 1883, store, on_ready=lambda: ready_calls.append(None))

    assert broker.attempts == 3
    assert broker.subscriptions == ["vpp/#"]
    assert len(ready_calls) == 1
    assert [t.device_id for t in store.inserted] == ["como-charger-001"]
    # First failure backs off 1 s, the dropped connection backs off 1 s again (reset after success).
    assert [s for s in sleeps if s in (1.0, 2.0)] == [1.0, 1.0]
