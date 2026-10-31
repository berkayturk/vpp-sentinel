"""HTTP control plane for fault switches.

The current ``FaultState`` lives on ``app.state.faults`` and is *replaced* on
every change; the publisher reads it through ``app.state.get_faults``.
"""

from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import Body, FastAPI, HTTPException

from fleet_simulator.faults import FaultState, apply_fault, clear_faults, state_to_dict

log = structlog.get_logger("fleet_simulator.control")


def create_app(initial_state: FaultState) -> FastAPI:
    app = FastAPI(title="fleet-simulator control API", version="0.1.0")
    app.state.faults = initial_state
    app.state.get_faults = lambda: app.state.faults

    @app.get("/faults")
    def get_faults() -> dict[str, Any]:
        return state_to_dict(app.state.faults)

    @app.post("/faults/{name}")
    def post_fault(name: str, params: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        try:
            new_state = apply_fault(app.state.faults, name, params, datetime.now(UTC))
        except ValueError as exc:
            log.warning("fault.rejected", fault=name, reason=str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        app.state.faults = new_state
        log.info("fault.applied", fault=name, params=params)
        return state_to_dict(new_state)

    @app.delete("/faults")
    def delete_faults() -> dict[str, Any]:
        app.state.faults = clear_faults(app.state.faults)
        log.info("fault.cleared")
        return state_to_dict(app.state.faults)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
