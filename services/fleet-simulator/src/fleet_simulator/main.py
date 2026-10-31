"""Entrypoint: control API and MQTT publisher in one event loop."""

import asyncio
import os

import structlog
import uvicorn

from fleet_simulator.control_api import create_app
from fleet_simulator.faults import FaultState
from fleet_simulator.fleet import DEFAULT_FLEET, build_fleet
from fleet_simulator.log import configure_logging
from fleet_simulator.publisher import run_publisher
from fleet_simulator.runtime import run_until_server_exits

DEFAULT_MQTT_PORT = 1883
DEFAULT_CADENCE_SECONDS = 5.0
DEFAULT_CONTROL_PORT = 8080


async def serve() -> None:
    configure_logging("fleet-simulator", os.getenv("LOG_LEVEL", "INFO"))
    log = structlog.get_logger("fleet_simulator.main")

    broker_host = os.environ["MQTT_HOST"]
    broker_port = int(os.getenv("MQTT_PORT", DEFAULT_MQTT_PORT))
    cadence = float(os.getenv("FLEET_CADENCE_SECONDS", DEFAULT_CADENCE_SECONDS))
    control_port = int(os.getenv("CONTROL_PORT", DEFAULT_CONTROL_PORT))

    fleet = build_fleet(DEFAULT_FLEET)
    app = create_app(FaultState())
    log.info(
        "fleet.started", devices=len(fleet), cadence_seconds=cadence, sites=DEFAULT_FLEET.sites
    )

    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=control_port, log_level="warning", log_config=None)  # noqa: S104
    )
    # uvicorn owns SIGTERM; when it exits, the publisher is cancelled with it.
    await run_until_server_exits(
        server.serve(),
        run_publisher(broker_host, broker_port, fleet, app.state.get_faults, cadence),
    )


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
