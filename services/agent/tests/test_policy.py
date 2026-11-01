"""The allowlist and the path guard: every boundary the agent has, as a test."""

from datetime import timedelta
from pathlib import Path

import pytest

from agent.policy import Allowlist, PathGuard, PathRefused, load_allowlist

ALLOWLIST_YAML = """
actions:
  restart_deployment:
    namespace: vpp
    names: [ingestion]
    cooldown_minutes: 30
pull_requests:
  enabled: true
  allowed_paths: [services/ingestion/]
  forbidden_paths: [allowlist.yaml, services/agent/, .github/, CODEOWNERS]
"""

FORBIDDEN = ("allowlist.yaml", "services/agent/", ".github/", "CODEOWNERS")


@pytest.fixture
def allowlist(tmp_path: Path) -> Allowlist:
    path = tmp_path / "allowlist.yaml"
    path.write_text(ALLOWLIST_YAML)
    return load_allowlist(path)


@pytest.fixture
def guard(tmp_path: Path) -> PathGuard:
    for d in ("services/ingestion/src/ingestion", "services/agent/src/agent", ".github/workflows"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "allowlist.yaml").write_text("x")
    return PathGuard(tmp_path, allowed=("services/ingestion/",), forbidden=FORBIDDEN)


def test_restart_allowed_only_for_listed_deployment(allowlist: Allowlist):
    assert allowlist.restart_allowed("vpp", "ingestion").allowed
    assert not allowlist.restart_allowed("vpp", "postgres").allowed
    assert not allowlist.restart_allowed("monitoring", "ingestion").allowed
    assert "allowlist" in allowlist.restart_allowed("vpp", "postgres").reason


def test_cooldown_is_thirty_minutes(allowlist: Allowlist):
    assert allowlist.cooldown == timedelta(minutes=30)


def test_allowlist_is_immutable(allowlist: Allowlist):
    with pytest.raises((AttributeError, TypeError)):
        allowlist.cooldown = timedelta(minutes=1)  # type: ignore[misc]


def test_path_guard_resolves_inside_ingestion(guard: PathGuard, tmp_path: Path):
    p = guard.resolve("services/ingestion/src/ingestion/schema.py")
    assert p.is_relative_to(tmp_path / "services/ingestion")


@pytest.mark.parametrize(
    "bad",
    [
        "services/agent/src/agent/policy.py",
        "allowlist.yaml",
        ".github/workflows/ci.yml",
        "services/ingestion/../agent/x.py",
        "/etc/passwd",
        "services/ingestion/%2e%2e/x",
        "services/fleet-simulator/src/fleet_simulator/payload.py",
    ],
)
def test_path_guard_refuses(guard: PathGuard, bad: str):
    with pytest.raises(PathRefused):
        guard.resolve(bad)


def test_diff_check_refuses_forbidden_paths(guard: PathGuard):
    ok = guard.check_diff(["services/ingestion/src/ingestion/schema.py"])
    bad = guard.check_diff(["services/ingestion/src/x.py", "allowlist.yaml"])
    assert ok.allowed
    assert not bad.allowed and "allowlist.yaml" in bad.reason


def test_diff_check_refuses_paths_outside_scope(guard: PathGuard):
    bad = guard.check_diff(["docs/architecture.md"])
    assert not bad.allowed and "docs/architecture.md" in bad.reason


def test_allowlist_file_in_repo_root_parses():
    a = load_allowlist(Path(__file__).resolve().parents[3] / "allowlist.yaml")
    assert a.restart_allowed("vpp", "ingestion").allowed
    assert a.pull_requests_enabled
    assert "services/agent/" in a.forbidden_paths
