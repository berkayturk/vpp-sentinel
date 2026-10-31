"""Process lifecycle: the HTTP server owns the signal handling; workers follow it.

uvicorn installs the SIGTERM/SIGINT handlers and returns from ``serve()`` when
one arrives. Everything else in the process is cancelled at that moment, so a
pod stops within a second instead of running out its grace period next to its
replacement.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any


async def run_until_server_exits(
    server: Coroutine[Any, Any, None], *workers: Coroutine[Any, Any, None]
) -> None:
    """Run ``server`` and ``workers`` together; stop everything when any of them finishes.

    A worker that raises propagates its exception after the others are cancelled.
    """
    tasks = [asyncio.create_task(server, name="server")]
    tasks += [asyncio.create_task(w, name=f"worker-{i}") for i, w in enumerate(workers)]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        task.result()  # re-raise a worker failure, if that is what ended the run
