"""The Tier 3 working copy: a fresh clone the agent edits, tests and pushes from.

Paths handed to ``read``/``write``/``list`` are absolute and already past the
``PathGuard``; this module never widens them. The push URL carries the token
only on the command line and every error message is masked before it leaves.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agent.sanitize import sanitize_text

DEFAULT_TEST_COMMAND = (
    "uv",
    "run",
    "--directory",
    "services/ingestion",
    "pytest",
    "-q",
    "-x",
    "--no-cov",
)
DEFAULT_PUSH_URL = "https://x-access-token:{token}@github.com/{repo}.git"
TEST_TIMEOUT_SECONDS = 180
COMMAND_TIMEOUT_SECONDS = 120
OUTPUT_TAIL_LINES = 60


class WorkspaceError(Exception):
    """A git or filesystem step failed; the message is already sanitised."""


@dataclass(frozen=True)
class TestResult:
    passed: bool
    output: str


class Workspace(Protocol):
    root: Path

    async def prepare(self) -> None: ...
    async def read(self, path: Path) -> str: ...
    async def write(self, path: Path, content: str) -> None: ...
    async def list(self, path: Path) -> tuple[str, ...]: ...
    async def changed_paths(self) -> tuple[str, ...]: ...
    async def run_tests(self) -> TestResult: ...
    async def commit_and_push(self, branch: str, message: str, paths: Sequence[str]) -> str: ...


class GitWorkspace:
    def __init__(
        self,
        repo_url: str,
        root: Path,
        token_provider: Callable[[], str | None],
        author: str,
        base_branch: str = "main",
        test_command: Sequence[str] = DEFAULT_TEST_COMMAND,
        push_url: str = DEFAULT_PUSH_URL,
        repo: str = "",
    ) -> None:
        self.repo_url = repo_url
        self.root = root
        self._token_provider = token_provider
        self._author = author
        self._base_branch = base_branch
        self._test_command = tuple(test_command)
        self._push_url = push_url
        self._repo = repo

    async def prepare(self) -> None:
        """A clean clone of the base branch, even if a previous incident left edits behind."""
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.parent.mkdir(parents=True, exist_ok=True)
        await _run(
            ["git", "clone", "-q", "--branch", self._base_branch, self.repo_url, str(self.root)],
            cwd=self.root.parent,
        )

    async def read(self, path: Path) -> str:
        try:
            return path.read_text()
        except OSError as exc:
            raise WorkspaceError(sanitize_text(f"{_rel(self.root, path)}: {exc.strerror}")) from exc

    async def write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    async def list(self, path: Path) -> tuple[str, ...]:
        if not path.is_dir():
            raise WorkspaceError(f"{_rel(self.root, path)}: not a directory")
        return tuple(sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir()))

    async def changed_paths(self) -> tuple[str, ...]:
        """Every modified, added, deleted or renamed path (both sides of a rename), NUL-safe."""
        out = await _run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=self.root,
            raw=True,
        )
        return tuple(sorted(_parse_status(out)))

    async def run_tests(self) -> TestResult:
        """Run the test command with a minimal environment: no secrets, no inherited config."""
        try:
            output = await _run(
                list(self._test_command),
                cwd=self.root,
                timeout=TEST_TIMEOUT_SECONDS,
                env=_test_env(self.root),
            )
        except WorkspaceError as exc:
            return TestResult(False, str(exc))
        return TestResult(True, _tail(output))

    async def commit_and_push(self, branch: str, message: str, paths: Sequence[str] = ()) -> str:
        """Stage exactly ``paths`` (already validated by the caller), commit and push."""
        if not paths:
            raise WorkspaceError("nothing to commit: no validated paths were given")
        name, email = _split_author(self._author)
        identity = ["-c", f"user.name={name}", "-c", f"user.email={email}"]
        await _run(["git", "checkout", "-q", "-B", branch], cwd=self.root)
        await _run(["git", "add", "--", *paths], cwd=self.root)
        await _run(["git", *identity, "commit", "-q", "-m", message], cwd=self.root)
        sha = (await _run(["git", "rev-parse", "HEAD"], cwd=self.root)).strip()
        token = self._token_provider() or ""
        # Without a GitHub repo name (local repositories in tests) the clone source is the remote.
        url = self._push_url.format(token=token, repo=self._repo) if self._repo else self.repo_url
        await _run(
            ["git", "push", "-q", "--force-with-lease", url, f"HEAD:{branch}"], cwd=self.root
        )
        return sha


async def _run(
    cmd: list[str],
    cwd: Path,
    timeout: int = COMMAND_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
    raw: bool = False,
) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (OSError, TimeoutError) as exc:
        raise WorkspaceError(sanitize_text(f"{cmd[0]} failed: {exc}")) from exc
    decoded = out.decode(errors="replace")
    output = decoded if raw else sanitize_text(decoded)
    if proc.returncode != 0:
        raise WorkspaceError(f"{cmd[0]} exited {proc.returncode}: {_tail(sanitize_text(decoded))}")
    return output


def _parse_status(porcelain_z: str) -> list[str]:
    """``git status --porcelain=v1 -z``: ``XY path\0`` records; renames carry ``new\0old``."""
    records = porcelain_z.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        status, path = record[:2], record[3:]
        paths.append(path)
        if "R" in status or "C" in status:
            paths.append(records[index])
            index += 1
    return paths


def _test_env(root: Path) -> dict[str, str]:
    """A bare environment for model-written tests: a PATH, a scratch HOME, nothing secret."""
    home = root.parent / ".test-home"
    home.mkdir(parents=True, exist_ok=True)
    keep = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "TZ")}
    return {**keep, "HOME": str(home), "UV_CACHE_DIR": str(home / "uv-cache")}


def _tail(text: str) -> str:
    return "\n".join(text.splitlines()[-OUTPUT_TAIL_LINES:])


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _split_author(author: str) -> tuple[str, str]:
    name, _, rest = author.partition("<")
    return name.strip(), rest.rstrip(">").strip()
