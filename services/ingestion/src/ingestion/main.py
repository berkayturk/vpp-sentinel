"""Entrypoint.

The HTTP server starts first so ``/healthz`` (503 until ready) and ``/metrics``
answer immediately and SIGTERM is handled from the first second. The worker
then waits for PostgreSQL, applies migrations, loads the fleet index and
consumes MQTT. When the server exits, the worker is cancelled with it.
"""

import asyncio
import os
from pathlib import Path

import structlog
import uvicorn

from ingestion.api import create_app
from ingestion.consumer import run_consumer
from ingestion.fleet_index import load_fleet_index
from ingestion.log import configure_logging
from ingestion.runtime import run_until_server_exits
from ingestion.startup import wait_for
from ingestion.store import PostgresStore, apply_migrations

DEFAULT_MQTT_PORT = 1883
DEFAULT_HTTP_PORT = 8000
DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


class _Readiness:
    def __init__(self) -> None:
        self.ready = False

    def mark_ready(self) -> None:
        self.ready = True


async def _worker(readiness: _Readiness) -> None:
    log = structlog.get_logger("ingestion.main")
    dsn = os.environ["DATABASE_URL"]
    broker_host = os.environ["MQTT_HOST"]
    broker_port = int(os.getenv("MQTT_PORT", DEFAULT_MQTT_PORT))
    migrations_dir = Path(os.getenv("MIGRATIONS_DIR", DEFAULT_MIGRATIONS_DIR))

    applied = await wait_for(lambda: apply_migrations(dsn, migrations_dir))
    log.info("migrations.applied", files=applied)
    store = await wait_for(lambda: PostgresStore.connect(dsn))
    try:
        fleet = await load_fleet_index(store)
        log.info("fleet.loaded", devices=len(fleet.devices), sites=sorted(fleet.sites))
        await run_consumer(broker_host, broker_port, store, fleet, on_ready=readiness.mark_ready)
    finally:
        await store.close()


async def serve() -> None:
    configure_logging("ingestion", os.getenv("LOG_LEVEL", "INFO"))
    http_port = int(os.getenv("HTTP_PORT", DEFAULT_HTTP_PORT))
    readiness = _Readiness()
    app = create_app(lambda: readiness.ready)
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=http_port, log_level="warning")  # noqa: S104
    )
    await run_until_server_exits(server.serve(), _worker(readiness))


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
