"""Health and metrics endpoints."""

from collections.abc import Callable

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

HTTP_SERVICE_UNAVAILABLE = 503


def create_app(ready: Callable[[], bool]) -> FastAPI:
    app = FastAPI(title="ingestion", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> Response:
        if ready():
            return Response(content='{"status":"ok"}', media_type="application/json")
        return Response(
            content='{"status":"starting"}',
            media_type="application/json",
            status_code=HTTP_SERVICE_UNAVAILABLE,
        )

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
