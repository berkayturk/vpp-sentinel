"""The agent's authority, read from ``allowlist.yaml``, and the repository path guard.

Both are checked in code by every tool. A refusal is a ``Decision`` with a
reason the model can read; nothing here raises into the model loop except
``PathRefused``, which the tools catch and render as a refusal.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote

import yaml


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


class PathRefused(Exception):
    """A model-supplied path falls outside the agent's read/write scope."""


@dataclass(frozen=True)
class Allowlist:
    restart_namespace: str
    restart_names: tuple[str, ...]
    cooldown: timedelta
    pull_requests_enabled: bool
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]

    def restart_allowed(self, namespace: str, name: str) -> Decision:
        if namespace == self.restart_namespace and name in self.restart_names:
            return Decision(True)
        listed = ", ".join(f"{self.restart_namespace}/{n}" for n in self.restart_names)
        return Decision(
            False,
            f"{namespace}/{name} is not in the allowlist; restarts are allowed only for {listed}",
        )


def load_allowlist(path: Path) -> Allowlist:
    """Parse ``allowlist.yaml``; fail fast on a missing or malformed section."""
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path}: allowlist must be a mapping")
    restart = _section(raw, "actions", "restart_deployment", path)
    prs = _section(raw, "pull_requests", None, path)
    return Allowlist(
        restart_namespace=str(restart["namespace"]),
        restart_names=tuple(str(n) for n in restart["names"]),
        cooldown=timedelta(minutes=int(restart["cooldown_minutes"])),
        pull_requests_enabled=bool(prs["enabled"]),
        allowed_paths=tuple(str(p) for p in prs["allowed_paths"]),
        forbidden_paths=tuple(str(p) for p in prs["forbidden_paths"]),
    )


def _section(raw: Mapping, key: str, sub: str | None, path: Path) -> Mapping:
    value = raw.get(key)
    if sub is not None and isinstance(value, Mapping):
        value = value.get(sub)
    if not isinstance(value, Mapping):
        where = f"{key}.{sub}" if sub else key
        raise ValueError(f"{path}: missing or malformed section '{where}'")
    return value


@dataclass(frozen=True)
class PathGuard:
    """Confines every file operation to ``allowed`` prefixes under ``root``.

    ``allowed`` and ``forbidden`` are repository-relative prefixes; a directory
    prefix ends with ``/``.
    """

    root: Path
    allowed: tuple[str, ...]
    forbidden: tuple[str, ...]

    def resolve(self, path: str) -> Path:
        """Return the absolute path for a repository-relative one, or raise ``PathRefused``."""
        cleaned = unquote(path)
        if Path(cleaned).is_absolute():
            raise PathRefused(f"{path}: absolute paths are not allowed")
        resolved = (self.root / cleaned).resolve()
        root = self.root.resolve()
        if not resolved.is_relative_to(root):
            raise PathRefused(f"{path}: escapes the repository")
        relative = resolved.relative_to(root).as_posix()
        decision = self._check_relative(relative)
        if not decision.allowed:
            raise PathRefused(decision.reason)
        return resolved

    def check_diff(self, paths: Iterable[str]) -> Decision:
        """Every changed path must be inside the allowed scope and outside the forbidden one."""
        refused = [p for p in paths if not self._check_relative(p).allowed]
        if refused:
            return Decision(False, f"paths outside the agent's scope: {', '.join(refused)}")
        return Decision(True)

    def _check_relative(self, relative: str) -> Decision:
        for prefix in self.forbidden:
            if relative == prefix.rstrip("/") or relative.startswith(prefix):
                return Decision(False, f"{relative}: forbidden path ({prefix})")
        if not any(relative.startswith(prefix) for prefix in self.allowed):
            scope = ", ".join(self.allowed)
            return Decision(False, f"{relative}: outside the agent's scope ({scope})")
        return Decision(True)
