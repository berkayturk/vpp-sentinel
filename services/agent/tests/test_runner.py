"""The test-runner sidecar: runs a fixed command in a workspace directory, nothing else."""

import time
from pathlib import Path

import httpx
import pytest

from agent.runner import create_runner_app


async def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner")


async def test_runs_the_configured_command_in_the_requested_root(tmp_path: Path):
    root = tmp_path / "ws" / "slug"
    root.mkdir(parents=True)
    app = create_runner_app(
        workspace_dir=tmp_path / "ws",
        command=("sh", "-c", "pwd && echo ran"),
        timeout_seconds=5,
    )
    async with await _client(app) as http:
        response = await http.post("/run", json={"root": str(root)})
    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is True
    assert body["output"].strip().endswith("ran")
    assert str(root.resolve()) in body["output"]


async def test_failures_are_reported_not_raised(tmp_path: Path):
    root = tmp_path / "ws" / "slug"
    root.mkdir(parents=True)
    app = create_runner_app(
        workspace_dir=tmp_path / "ws",
        command=("sh", "-c", "echo boom; exit 3"),
        timeout_seconds=5,
    )
    async with await _client(app) as http:
        body = (await http.post("/run", json={"root": str(root)})).json()
    assert body["passed"] is False and "boom" in body["output"]


@pytest.mark.parametrize("bad", ["/etc", "../x", "/workspace-other/slug", ""])
async def test_roots_outside_the_workspace_are_refused(tmp_path: Path, bad: str):
    app = create_runner_app(workspace_dir=tmp_path / "ws", command=("true",), timeout_seconds=5)
    async with await _client(app) as http:
        response = await http.post("/run", json={"root": bad})
    assert response.status_code == 400


async def test_timeouts_kill_the_process_group(tmp_path: Path):
    root = tmp_path / "ws" / "slug"
    root.mkdir(parents=True)
    marker = tmp_path / "alive"
    app = create_runner_app(
        workspace_dir=tmp_path / "ws",
        command=("sh", "-c", f"(sleep 2; touch {marker}) & sleep 10"),
        timeout_seconds=0.3,
    )
    async with await _client(app) as http:
        body = (await http.post("/run", json={"root": str(root)})).json()
    assert body["passed"] is False and "timed out" in body["output"]
    time.sleep(2.5)
    assert not marker.exists()


async def test_the_runner_only_exposes_run_and_health(tmp_path: Path):
    app = create_runner_app(workspace_dir=tmp_path / "ws", command=("true",), timeout_seconds=1)
    paths = {route.path for route in app.routes}
    assert paths == {"/run", "/healthz"}
