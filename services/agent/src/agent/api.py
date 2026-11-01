"""Health and metrics endpoints."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest


def create_app(ready: Callable[[], bool]) -> FastAPI:
    app = FastAPI(title="vpp-agent", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz() -> Response:
        status = 200 if ready() else 503
        return Response(content="ok" if status == 200 else "starting", status_code=status)

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
