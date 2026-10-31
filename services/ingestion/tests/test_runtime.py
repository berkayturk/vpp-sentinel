"""The process stops its workers as soon as the HTTP server exits (which is how SIGTERM arrives)."""

import asyncio

from ingestion.runtime import run_until_server_exits


async def test_workers_are_cancelled_when_the_server_exits():
    cancelled: list[bool] = []

    async def server() -> None:
        await asyncio.sleep(0.01)

    async def worker() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    await asyncio.wait_for(run_until_server_exits(server(), worker()), timeout=2)
    assert cancelled == [True]


async def test_worker_failure_propagates_and_stops_the_server():
    server_cancelled: list[bool] = []

    async def server() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            server_cancelled.append(True)
            raise

    async def worker() -> None:
        raise RuntimeError("boom")

    try:
        await asyncio.wait_for(run_until_server_exits(server(), worker()), timeout=2)
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("worker failure must propagate")
    assert server_cancelled == [True]
