"""Tests for GitArtifactPersister — persist and clean .kodezart/ artifacts."""

import ast
import asyncio
import hashlib
import json
from pathlib import Path

import pytest
import structlog

from kodezart.adapters.git.artifact_persister import ARTIFACT_DIR, GitArtifactPersister
from kodezart.adapters.git.bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.git.service import SubprocessGitService
from kodezart.adapters.git.worktree_provider import GitWorktreeProvider
from kodezart.types.domain.persist import ArtifactPersistStatus
from tests.negative_shape import REPO_ROOT

PROTOCOLS = REPO_ROOT / "src" / "kodezart" / "core" / "protocols.py"
ADAPTER = REPO_ROOT / "src" / "kodezart" / "adapters" / "git" / "artifact_persister.py"
PORT = "ArtifactPersister"

#: The sha256 of the port's own block of source and of the whole adapter, at
#: the head that recorded them.  Both sit on a live path from the
#: application's boot, and a widened signature the fakes do not follow is a
#: change the behaviour tests below cannot see: they construct the adapter
#: with keywords, so a defaulted parameter added to the port passes them all.
#: A commit that moves either surface moves the digest here and says why.
PORT_BLOCK_DIGEST = "db05f1ceca36ff6f2c7d8cb0756c59dfbb9851202d5e56c3192cffa3db238975"
ADAPTER_DIGEST = "8510d78e5dc03cccbebb2d2a30462e6220bd726159c59ad1404fc100e7e9fc77"


def digest(text: str | bytes) -> str:
    """The sha256 of *text* as hex."""
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def class_block(source: str, name: str) -> str:
    """The source of the one top-level class *name*, decorators included.

    Located by parsing rather than by a line range, so an insertion anywhere
    else in the file -- and that file is edited by nearly every other piece
    of work -- does not move the block.  Not exactly one definition of the
    name is a refusal that says so.
    """
    defined = [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == name
    ]
    if len(defined) != 1:
        msg = f"{len(defined)} top-level definitions of {name}"
        raise LookupError(msg)
    block = defined[0]
    opens = min([block.lineno, *(node.lineno for node in block.decorator_list)])
    return "".join(source.splitlines(keepends=True)[opens - 1 : block.end_lineno])


def _clean_widened(source: str) -> str:
    """*source* with one defaulted keyword parameter added to the port's clean."""
    block = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == PORT
    )
    clean = next(
        node
        for node in block.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "clean"
    )
    lines = source.splitlines(keepends=True)
    lines.insert(clean.args.kwonlyargs[-1].lineno, "        extra: int = 0,\n")
    return "".join(lines)


async def _run_git(cmd: list[str], cwd: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = f"{' '.join(cmd[:3])} failed: {stderr.decode()}"
        raise RuntimeError(msg)


async def _run_git_output(cmd: list[str], cwd: Path) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = f"{' '.join(cmd[:3])} failed: {stderr.decode()}"
        raise RuntimeError(msg)
    return stdout.decode().strip()


@pytest.fixture
async def git_env(tmp_path: Path) -> tuple[Path, Path]:
    """Create a local repo with a bare remote, like test_git_change_persister."""
    repo = tmp_path / "repo"
    bare = tmp_path / "bare.git"
    repo.mkdir()
    bare.mkdir()

    await _run_git(["git", "init", "--bare", str(bare)], cwd=tmp_path)
    await _run_git(["git", "clone", str(bare), str(repo)], cwd=tmp_path)
    (repo / "README.md").write_text("init")
    await _run_git(["git", "add", "README.md"], cwd=repo)
    await _run_git(["git", "commit", "-m", "init"], cwd=repo)
    await _run_git(["git", "push", "-u", "origin", "HEAD:refs/heads/main"], cwd=repo)
    return repo, bare


async def test_persist_creates_kodezart_files_and_pushes(
    git_env: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, bare = git_env
    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
    )
    persister = GitArtifactPersister(
        git=git,
        workspace=workspace,
        committer_name="test",
        committer_email="t@t.dev",
    )

    status = await persister.persist(
        repo_path=str(repo),
        repo_url=None,
        branch="test-branch",
        base_branch="main",
        artifacts={
            "ticket.json": '{"title": "test"}',
            "criteria.json": '["criterion 1"]',
        },
    )
    assert status is ArtifactPersistStatus.PERSISTED

    # Clone to verify the files were pushed
    verify = tmp_path / "verify"
    clone_cmd = ["git", "clone", "-b", "test-branch", str(bare), str(verify)]
    await _run_git(clone_cmd, cwd=tmp_path)
    ticket = (verify / ARTIFACT_DIR / "ticket.json").read_text()
    criteria = (verify / ARTIFACT_DIR / "criteria.json").read_text()
    assert json.loads(ticket) == {"title": "test"}
    assert json.loads(criteria) == ["criterion 1"]


async def test_clean_removes_kodezart_directory(
    git_env: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, bare = git_env
    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
    )
    persister = GitArtifactPersister(
        git=git,
        workspace=workspace,
        committer_name="test",
        committer_email="t@t.dev",
    )

    # Persist first
    await persister.persist(
        repo_path=str(repo),
        repo_url=None,
        branch="clean-branch",
        base_branch="main",
        artifacts={"test.json": "{}"},
    )

    # Clean
    await persister.clean(
        repo_path=str(repo),
        repo_url=None,
        branch="clean-branch",
    )

    # Verify cleaned
    verify = tmp_path / "verify-clean"
    clone_cmd = ["git", "clone", "-b", "clean-branch", str(bare), str(verify)]
    await _run_git(clone_cmd, cwd=tmp_path)
    assert not (verify / ARTIFACT_DIR).exists()


async def test_clean_noop_when_no_artifacts(
    git_env: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, _bare = git_env
    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
    )
    persister = GitArtifactPersister(
        git=git,
        workspace=workspace,
        committer_name="test",
        committer_email="t@t.dev",
    )

    # Create branch without artifacts
    await _run_git(["git", "branch", "empty-branch"], cwd=repo)
    await _run_git(["git", "push", "origin", "empty-branch"], cwd=repo)

    # Clean should be a no-op (no crash, no commit)
    await persister.clean(
        repo_path=str(repo),
        repo_url=None,
        branch="empty-branch",
    )


async def test_persist_skips_when_target_gitignores_artifact_dir(
    git_env: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """A target whose .gitignore matches .kodezart/ skips commit and push."""
    repo, bare = git_env
    (repo / ".gitignore").write_text(f"{ARTIFACT_DIR}/\n")
    await _run_git(["git", "add", ".gitignore"], cwd=repo)
    await _run_git(["git", "commit", "-m", "chore: ignore artifacts"], cwd=repo)
    await _run_git(["git", "push", "origin", "HEAD:refs/heads/main"], cwd=repo)

    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
    )
    persister = GitArtifactPersister(
        git=git,
        workspace=workspace,
        committer_name="test",
        committer_email="t@t.dev",
    )

    with structlog.testing.capture_logs() as logs:
        await persister.persist(
            repo_path=str(repo),
            repo_url=None,
            branch="ignored-branch",
            base_branch="main",
            artifacts={"ticket.json": '{"title": "test"}'},
        )

    skipped = [e for e in logs if e["event"] == "artifacts_persist_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["branch"] == "ignored-branch"
    assert [e for e in logs if e["event"] == "artifacts_persisted"] == []

    remote_branches = await _run_git_output(["git", "branch", "--list"], cwd=bare)
    assert "ignored-branch" not in remote_branches

    verify = tmp_path / "verify-ignored"
    clone_cmd = ["git", "clone", "-b", "main", str(bare), str(verify)]
    await _run_git(clone_cmd, cwd=tmp_path)
    assert not (verify / ARTIFACT_DIR).exists()


async def test_persist_reports_ignored_by_target_status(
    git_env: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """A gitignored .kodezart/ returns IGNORED_BY_TARGET and warns, no push."""
    repo, bare = git_env
    (repo / ".gitignore").write_text(f"{ARTIFACT_DIR}/\n")
    await _run_git(["git", "add", ".gitignore"], cwd=repo)
    await _run_git(["git", "commit", "-m", "chore: ignore artifacts"], cwd=repo)
    await _run_git(["git", "push", "origin", "HEAD:refs/heads/main"], cwd=repo)

    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
    )
    persister = GitArtifactPersister(
        git=git,
        workspace=workspace,
        committer_name="test",
        committer_email="t@t.dev",
    )

    with structlog.testing.capture_logs() as logs:
        status = await persister.persist(
            repo_path=str(repo),
            repo_url=None,
            branch="ignored-status-branch",
            base_branch="main",
            artifacts={"ticket.json": '{"title": "test"}'},
        )

    assert status is ArtifactPersistStatus.IGNORED_BY_TARGET
    skipped = [e for e in logs if e["event"] == "artifacts_persist_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["reason"] == ArtifactPersistStatus.IGNORED_BY_TARGET
    assert skipped[0]["log_level"] == "warning"

    remote_branches = await _run_git_output(["git", "branch", "--list"], cwd=bare)
    assert "ignored-status-branch" not in remote_branches


async def test_persist_reports_unchanged_when_artifacts_already_committed(
    git_env: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """Byte-identical artifacts are UNCHANGED, never confused with ignored."""
    repo, _bare = git_env
    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
    )
    persister = GitArtifactPersister(
        git=git,
        workspace=workspace,
        committer_name="test",
        committer_email="t@t.dev",
    )
    artifacts = {"ticket.json": '{"title": "test"}'}

    first = await persister.persist(
        repo_path=str(repo),
        repo_url=None,
        branch="unchanged-branch",
        base_branch="main",
        artifacts=artifacts,
    )
    with structlog.testing.capture_logs() as logs:
        second = await persister.persist(
            repo_path=str(repo),
            repo_url=None,
            branch="unchanged-branch",
            base_branch="main",
            artifacts=artifacts,
        )

    assert first is ArtifactPersistStatus.PERSISTED
    assert second is ArtifactPersistStatus.UNCHANGED
    skipped = [e for e in logs if e["event"] == "artifacts_persist_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["reason"] == ArtifactPersistStatus.UNCHANGED
    assert skipped[0]["log_level"] == "info"


def test_the_persister_port_block_is_the_recorded_one() -> None:
    """The port the adapter above implements is a wired surface, pinned here."""
    source = PROTOCOLS.read_text(encoding="utf-8")

    assert digest(class_block(source, PORT)) == PORT_BLOCK_DIGEST


def test_the_git_persister_adapter_is_the_recorded_one() -> None:
    """One class, one file, on the boot path; its bytes are the pin."""
    assert digest(ADAPTER.read_bytes()) == ADAPTER_DIGEST


def test_a_widened_clean_signature_changes_the_port_digest() -> None:
    """The mutation nothing caught: one defaulted keyword added to clean.

    The fakes match the port's keywords exactly, and every caller passes them
    by name, so the parameter below is invisible to the suite's behaviour.
    It is not invisible to the pin.
    """
    source = PROTOCOLS.read_text(encoding="utf-8")
    widened = _clean_widened(source)

    assert widened != source
    assert digest(class_block(widened, PORT)) != PORT_BLOCK_DIGEST


def test_an_insertion_above_the_block_leaves_the_digest_unchanged() -> None:
    """Other work adds members above this one; that is not a change to it."""
    source = PROTOCOLS.read_text(encoding="utf-8")

    assert digest(class_block(f"\n# moved\n{source}", PORT)) == PORT_BLOCK_DIGEST
