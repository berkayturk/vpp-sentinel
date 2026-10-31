"""Entrypoint: migrations, then the MQTT consumer and the HTTP API in one event loop."""

import asyncio
import os
from pathlib import Path

import structlog
import uvicorn

from ingestion.api import create_app
from ingestion.consumer import run_consumer
from ingestion.log import configure_logging
from ingestion.store import PostgresStore, apply_migrations

DEFAULT_MQTT_PORT = 1883
DEFAULT_HTTP_PORT = 8000
DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


class _Readiness:
    def __init__(self) -> None:
        self.ready = False

    def mark_ready(self) -> None:
        self.ready = True


async def serve() -> None:
    configure_logging("ingestion", os.getenv("LOG_LEVEL", "INFO"))
    log = structlog.get_logger("ingestion.main")

    dsn = os.environ["DATABASE_URL"]
    broker_host = os.environ["MQTT_HOST"]
    broker_port = int(os.getenv("MQTT_PORT", DEFAULT_MQTT_PORT))
    http_port = int(os.getenv("HTTP_PORT", DEFAULT_HTTP_PORT))
    migrations_dir = Path(os.getenv("MIGRATIONS_DIR", DEFAULT_MIGRATIONS_DIR))

    applied = await apply_migrations(dsn, migrations_dir)
    log.info("migrations.applied", files=applied)
    store = await PostgresStore.connect(dsn)

    readiness = _Readiness()
    app = create_app(lambda: readiness.ready)
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=http_port, log_level="warning")  # noqa: S104
    )
    try:
        await asyncio.gather(
            server.serve(),
            run_consumer(broker_host, broker_port, store, on_ready=readiness.mark_ready),
        )
    finally:
        await store.close()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
