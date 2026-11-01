"""Entrypoint.

The HTTP server starts first so ``/healthz`` and ``/metrics`` answer at once.
Without an Anthropic API key the worker logs ``agent.disabled`` and idles, so
``make demo`` works for a reader who has no key. With one, it waits for the
database, wires the clients and polls Alertmanager.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import anthropic
import httpx
import structlog
import uvicorn

from agent.api import create_app
from agent.config import KUBE_CA_PATH, KUBE_TOKEN_PATH, Settings, agent_enabled
from agent.log import configure_logging
from agent.loop import AnthropicRunner
from agent.memory import PostgresIncidentStore
from agent.policy import PathGuard, load_allowlist
from agent.runtime import run_until_server_exits
from agent.service import ServiceDeps, run_forever
from agent.workspace import GitWorkspace

DB_RETRY_SECONDS = 3.0
DB_RETRIES = 40
IDLE_SECONDS = 3600


class _Readiness:
    def __init__(self) -> None:
        self.ready = False

    def mark_ready(self) -> None:
        self.ready = True


async def _connect_store(dsn: str) -> PostgresIncidentStore:
    log = structlog.get_logger("agent.main")
    for attempt in range(1, DB_RETRIES + 1):
        try:
            return await PostgresIncidentStore.connect(dsn)
        except (OSError, ConnectionError) as exc:
            log.warning("db.unavailable", attempt=attempt, error=str(exc))
            await asyncio.sleep(DB_RETRY_SECONDS)
    raise RuntimeError("database did not become reachable")


def _kube_client(settings: Settings) -> httpx.AsyncClient:
    token = KUBE_TOKEN_PATH.read_text().strip() if KUBE_TOKEN_PATH.exists() else ""
    verify: str | bool = str(KUBE_CA_PATH) if KUBE_CA_PATH.exists() else True
    return httpx.AsyncClient(
        base_url=settings.kube_api_url,
        headers={"Authorization": f"Bearer {token}"},
        verify=verify,
        timeout=20.0,
    )


def _github_client(settings: Settings) -> httpx.AsyncClient:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return httpx.AsyncClient(base_url="https://api.github.com", headers=headers, timeout=30.0)


def _deps(settings: Settings, store: PostgresIncidentStore) -> ServiceDeps:
    from datetime import UTC, datetime

    policy = load_allowlist(settings.allowlist_path)

    def workspace(slug: str) -> GitWorkspace:
        return GitWorkspace(
            repo_url=settings.repo_url,
            root=settings.workspace_dir / slug,
            token_provider=lambda: settings.github_token,
            author=settings.git_author,
            repo=settings.repo,
        )

    def guard(root: Path) -> PathGuard:
        return PathGuard(root, allowed=policy.allowed_paths, forbidden=policy.forbidden_paths)

    return ServiceDeps(
        alertmanager=httpx.AsyncClient(base_url=settings.alertmanager_url, timeout=20.0),
        prometheus=httpx.AsyncClient(base_url=settings.prometheus_url, timeout=30.0),
        loki=httpx.AsyncClient(base_url=settings.loki_url, timeout=30.0),
        kube=_kube_client(settings),
        github=_github_client(settings),
        grafana=httpx.AsyncClient(
            base_url=settings.grafana_url,
            auth=(settings.grafana_user, settings.grafana_password),
            timeout=20.0,
        ),
        store=store,
        policy=policy,
        guard_factory=guard,
        runner=AnthropicRunner(
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key),
            model=settings.model,
            effort=settings.effort,
        ),
        workspace_factory=workspace,
        runbooks_dir=settings.runbooks_dir,
        repo=settings.repo,
        base_branch="main",
        salt=None,
        now=lambda: datetime.now(UTC),
    )


async def _worker(settings: Settings, readiness: _Readiness) -> None:
    log = structlog.get_logger("agent.main")
    if not agent_enabled(settings):
        log.warning("agent.disabled", reason="ANTHROPIC_API_KEY is not set; idling")
        readiness.mark_ready()
        while True:
            await asyncio.sleep(IDLE_SECONDS)
    store = await _connect_store(settings.database_url)
    try:
        log.info(
            "agent.ready",
            model=settings.model,
            effort=settings.effort,
            repo=settings.repo,
            pull_requests=bool(settings.github_token),
        )
        await run_forever(_deps(settings, store), settings.poll_seconds, readiness.mark_ready)
    finally:
        await store.close()


async def serve() -> None:
    settings = Settings.from_env()
    configure_logging("agent", settings.log_level)
    readiness = _Readiness()
    app = create_app(lambda: readiness.ready)
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="0.0.0.0", port=settings.http_port, log_level="warning", log_config=None
        )  # noqa: S104
    )
    await run_until_server_exits(server.serve(), _worker(settings, readiness))


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
