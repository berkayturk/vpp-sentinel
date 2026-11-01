"""The test-runner sidecar: executes model-written tests away from the agent's secrets.

It runs in its own container (another user, no secrets, no service-account
token, the workspace mounted read-only) and does exactly one thing: run the
fixed test command in a directory under the shared workspace. It listens on
localhost only; the agent container reaches it over the pod's loopback.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agent.log import configure_logging
from agent.workspace import DEFAULT_TEST_COMMAND, TEST_TIMEOUT_SECONDS, WorkspaceError, _run

DEFAULT_PORT = 8002
LOOPBACK = "127.0.0.1"


class RunRequest(BaseModel):
    root: str


class RunResponse(BaseModel):
    passed: bool
    output: str


def create_runner_app(
    workspace_dir: Path, command: Sequence[str], timeout_seconds: float
) -> FastAPI:
    app = FastAPI(title="vpp-agent test runner", docs_url=None, redoc_url=None, openapi_url=None)
    base = workspace_dir.resolve()

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/run", response_model=RunResponse)
    async def run(request: RunRequest) -> RunResponse:
        root = _validated_root(base, request.root)
        try:
            output = await _run(
                list(command), cwd=root, timeout=timeout_seconds, env=_env(), own_group=True
            )
        except WorkspaceError as exc:
            return RunResponse(passed=False, output=str(exc))
        return RunResponse(passed=True, output=output)

    return app


def _validated_root(base: Path, requested: str) -> Path:
    if not requested:
        raise HTTPException(status_code=400, detail="root is required")
    root = Path(requested)
    if not root.is_absolute():
        raise HTTPException(status_code=400, detail="root must be absolute")
    resolved = root.resolve()
    if resolved == base or not resolved.is_relative_to(base) or not resolved.is_dir():
        raise HTTPException(status_code=400, detail="root must be a directory under the workspace")
    return resolved


def _env() -> dict[str, str]:
    keep = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "TZ", "HOME")}
    scratch = Path(os.getenv("HOME", "/tmp"))
    return {
        **keep,
        "HOME": str(scratch),
        "UV_CACHE_DIR": os.getenv("UV_CACHE_DIR", str(scratch / "uv-cache")),
        "UV_PROJECT_ENVIRONMENT": os.getenv("UV_PROJECT_ENVIRONMENT", str(scratch / "venv")),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def main() -> None:
    configure_logging("test-runner", os.getenv("LOG_LEVEL", "INFO"))
    app = create_runner_app(
        workspace_dir=Path(os.getenv("WORKSPACE_DIR", "/workspace")),
        command=DEFAULT_TEST_COMMAND,
        timeout_seconds=float(os.getenv("TEST_TIMEOUT_SECONDS", TEST_TIMEOUT_SECONDS)),
    )
    port = int(os.getenv("TEST_RUNNER_PORT", DEFAULT_PORT))
    asyncio.run(
        uvicorn.Server(
            uvicorn.Config(app, host=LOOPBACK, port=port, log_level="warning", log_config=None)
        ).serve()
    )


if __name__ == "__main__":
    main()
