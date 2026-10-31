"""Startup waits for PostgreSQL instead of crash-looping while it comes up."""

import pytest

from ingestion.startup import wait_for


async def test_wait_for_retries_until_the_operation_succeeds():
    attempts: list[int] = []
    sleeps: list[float] = []

    async def flaky() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionRefusedError("not yet")
        return "ok"

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    result = await wait_for(flaky, retries=5, initial_delay=0.5, sleep=fake_sleep)
    assert result == "ok"
    assert len(attempts) == 3
    assert sleeps == [0.5, 1.0]


async def test_wait_for_gives_up_after_the_last_retry():
    async def always_down() -> None:
        raise OSError("down")

    async def fake_sleep(seconds: float) -> None:
        return None

    with pytest.raises(OSError, match="down"):
        await wait_for(always_down, retries=2, initial_delay=0.1, sleep=fake_sleep)
