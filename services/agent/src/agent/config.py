"""Settings from the environment, validated once at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"
DEFAULT_POLL_SECONDS = 15.0
DEFAULT_HTTP_PORT = 8001
DEFAULT_REPO = "berkayturk/vpp-sentinel"
DEFAULT_AUTHOR = "Berkay Turk <berkaytrk6@gmail.com>"
KUBE_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
KUBE_CA_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")


@dataclass(frozen=True)
class Settings:
    database_url: str
    anthropic_api_key: str | None
    github_token: str | None
    model: str
    effort: str
    alertmanager_url: str
    prometheus_url: str
    loki_url: str
    grafana_url: str
    grafana_user: str
    grafana_password: str
    kube_api_url: str
    kube_namespace: str
    repo: str
    repo_url: str
    git_author: str
    workspace_dir: Path
    runbooks_dir: Path
    allowlist_path: Path
    poll_seconds: float
    http_port: int
    log_level: str
    test_runner_url: str | None

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        repo = env.get("GITHUB_REPO", DEFAULT_REPO)
        return cls(
            database_url=env["DATABASE_URL"],
            anthropic_api_key=env.get("ANTHROPIC_API_KEY") or None,
            github_token=env.get("GITHUB_TOKEN") or None,
            model=env.get("AGENT_MODEL", DEFAULT_MODEL),
            effort=env.get("AGENT_EFFORT", DEFAULT_EFFORT),
            alertmanager_url=env.get("ALERTMANAGER_URL", "http://obs-alertmanager.monitoring:9093"),
            prometheus_url=env.get("PROMETHEUS_URL", "http://obs-prometheus.monitoring:9090"),
            loki_url=env.get("LOKI_URL", "http://loki.monitoring:3100"),
            grafana_url=env.get("GRAFANA_URL", "http://grafana.monitoring:80"),
            grafana_user=env.get("GRAFANA_USER", "admin"),
            grafana_password=env.get("GRAFANA_PASSWORD", ""),
            kube_api_url=env.get("KUBE_API_URL", "https://kubernetes.default.svc"),
            kube_namespace=env.get("KUBE_NAMESPACE", "vpp"),
            repo=repo,
            repo_url=env.get("REPO_URL", f"https://github.com/{repo}.git"),
            git_author=env.get("GIT_AUTHOR", DEFAULT_AUTHOR),
            workspace_dir=Path(env.get("WORKSPACE_DIR", "/workspace")),
            runbooks_dir=Path(env.get("RUNBOOKS_DIR", "/app/docs/runbooks")),
            allowlist_path=Path(env.get("ALLOWLIST_PATH", "/app/allowlist.yaml")),
            poll_seconds=float(env.get("POLL_INTERVAL_SECONDS", DEFAULT_POLL_SECONDS)),
            http_port=int(env.get("HTTP_PORT", DEFAULT_HTTP_PORT)),
            log_level=env.get("LOG_LEVEL", "INFO"),
            test_runner_url=env.get("TEST_RUNNER_URL") or None,
        )


def agent_enabled(settings: Settings) -> bool:
    """Without a model API key the service idles; the platform still runs without it."""
    return bool(settings.anthropic_api_key)
