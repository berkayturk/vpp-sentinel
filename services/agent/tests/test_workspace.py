"""GitWorkspace against a local bare repository: clone, edit, test, push, and no token leaks."""

import subprocess
from pathlib import Path

import pytest

from agent.workspace import GitWorkspace, WorkspaceError

TOKEN = "ghp_SECRETsecretSECRET123456"


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def bare(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    _git("init", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    seed = tmp_path / "seed"
    _git("clone", "-q", str(origin), str(seed), cwd=tmp_path)
    (seed / "services/ingestion/src/ingestion").mkdir(parents=True)
    (seed / "services/ingestion/src/ingestion/schema.py").write_text("x = 1\n")
    (seed / "services/ingestion/tests").mkdir()
    _git("add", "-A", cwd=seed)
    _git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "-m",
        "seed",
        cwd=seed,
    )
    _git("push", "-q", "origin", "HEAD:main", cwd=seed)
    return origin


def _workspace(
    bare: Path, root: Path, test_command=("python3", "-c", "print('ok')")
) -> GitWorkspace:
    return GitWorkspace(
        repo_url=str(bare),
        root=root,
        token_provider=lambda: TOKEN,
        author="Agent Test <agent@example.com>",
        test_command=test_command,
    )


async def test_prepare_clones_and_read_list_work(bare: Path, tmp_path: Path):
    ws = _workspace(bare, tmp_path / "ws")
    await ws.prepare()
    assert await ws.read(ws.root / "services/ingestion/src/ingestion/schema.py") == "x = 1\n"
    assert "schema.py" in await ws.list(ws.root / "services/ingestion/src/ingestion")
    assert await ws.changed_paths() == ()


async def test_write_commit_push_creates_the_branch_upstream(bare: Path, tmp_path: Path):
    ws = _workspace(bare, tmp_path / "ws")
    await ws.prepare()
    await ws.write(ws.root / "services/ingestion/tests/test_fix.py", "def test_x():\n    pass\n")
    assert await ws.changed_paths() == ("services/ingestion/tests/test_fix.py",)
    sha = await ws.commit_and_push("agent/test-branch", "fix(ingestion): accept the new payload")
    assert len(sha) == 40
    assert "agent/test-branch" in _git("branch", "--list", "agent/test-branch", cwd=bare)
    log = _git("log", "-1", "--format=%an <%ae> %s", "agent/test-branch", cwd=bare)
    assert log.strip() == "Agent Test <agent@example.com> fix(ingestion): accept the new payload"


async def test_run_tests_reports_pass_and_fail(bare: Path, tmp_path: Path):
    ok = _workspace(bare, tmp_path / "ws1")
    await ok.prepare()
    assert (await ok.run_tests()).passed
    failing = _workspace(
        bare, tmp_path / "ws2", test_command=("python3", "-c", "import sys; sys.exit(1)")
    )
    await failing.prepare()
    result = await failing.run_tests()
    assert not result.passed


async def test_push_errors_never_contain_the_token(tmp_path: Path):
    ws = GitWorkspace(
        repo_url="https://github.com/example/does-not-exist.git",
        root=tmp_path / "ws",
        token_provider=lambda: TOKEN,
        author="Agent Test <agent@example.com>",
        push_url="https://x-access-token:{token}@localhost:1/{repo}.git",
        repo="example/does-not-exist",
    )
    (tmp_path / "ws").mkdir()
    _git("init", "-q", "-b", "main", str(tmp_path / "ws"), cwd=tmp_path)
    (tmp_path / "ws/f.txt").write_text("x")
    with pytest.raises(WorkspaceError) as info:
        await ws.commit_and_push("agent/x", "chore: x")
    assert TOKEN not in str(info.value)
    assert "<token>" in str(info.value) or "localhost" in str(info.value)


async def test_prepare_twice_resets_to_a_clean_clone(bare: Path, tmp_path: Path):
    ws = _workspace(bare, tmp_path / "ws")
    await ws.prepare()
    await ws.write(ws.root / "services/ingestion/src/ingestion/schema.py", "x = 2\n")
    await ws.prepare()
    assert await ws.read(ws.root / "services/ingestion/src/ingestion/schema.py") == "x = 1\n"
