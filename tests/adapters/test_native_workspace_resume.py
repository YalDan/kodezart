"""Actual Git identity and ownership survive only an unchanged retained worktree."""

import os
import shutil
from pathlib import Path

import pytest

from kodezart.adapters.git.service import SubprocessGitService
from kodezart.adapters.git.worktree_provider import GitWorktreeProvider
from kodezart.domain.errors import WorkspaceError
from kodezart.types.domain.workspace import WorkspaceSnapshot
from tests.fakes import FakeRepoCache
from tests.services.test_native_amendments import git, repository

__all__ = ["repository"]


async def acquired(repository):
    service = SubprocessGitService(remote="origin")
    cache = FakeRepoCache(str(repository[0]))
    owner = GitWorktreeProvider(service, cache)
    workspace = await owner.acquire(
        repo_path=str(repository[0]), ref="main", branch_name="retained-native"
    )
    return service, cache, owner, workspace


async def test_fresh_provider_adopts_actual_serialized_owned_workspace(repository):
    service, cache, owner, workspace = await acquired(repository)
    path = Path(workspace)
    (path / "staged\n binary.bin").write_bytes(b"\x00\xffstaged")
    await git(path, "add", ".")
    (path / "staged\n binary.bin").write_bytes(b"\x00\xffworking")
    (path / "untracked\t\n bytes.bin").write_bytes(b"\x00\xfeuntracked")
    (path / "external-link").symlink_to("/unavailable/private-target")
    snapshot = await owner.capture(workspace_path=workspace, holder="actual-parent-job")
    captured = WorkspaceSnapshot.model_validate_json(snapshot.model_dump_json())
    fresh = GitWorktreeProvider(service, cache)
    try:
        await fresh.resume(
            snapshot=captured,
            holder="actual-parent-job",
            repo_path=str(repository[0]),
            repo_url=None,
            cache_key=None,
        )
        assert workspace in fresh._workspaces
        assert (
            await service.worktree_identity(
                workspace, repository_path=str(repository[0])
            )
            == captured.identity
        )
        assert (path / "staged\n binary.bin").read_bytes() == b"\x00\xffworking"
    finally:
        await fresh.release(workspace)
        owner._workspaces.pop(workspace, None)
    assert not path.exists()
    assert not await git(
        repository[0], "ls-remote", "origin", "refs/heads/retained-native"
    )


@pytest.mark.parametrize(
    "change", ["working", "index", "mode", "symlink", "holder", "missing", "replaced"]
)
async def test_resume_refuses_changed_actual_worktree(repository, change):
    service, cache, owner, workspace = await acquired(repository)
    path = Path(workspace)
    (path / "new.bin").write_bytes(b"staged")
    await git(path, "add", "new.bin")
    (path / "new.bin").write_bytes(b"working")
    (path / "link").symlink_to("/external-target-one")
    snapshot = await owner.capture(workspace_path=workspace, holder="job")
    if change == "working":
        (path / "new.bin").write_bytes(b"changed")
    elif change == "index":
        await git(path, "add", "new.bin")
    elif change == "mode":
        os.chmod(path / "new.bin", 0o700)
    elif change == "symlink":
        (path / "link").unlink()
        (path / "link").symlink_to("/external-target-two")
    elif change in {"missing", "replaced"}:
        await service.remove_worktree(str(repository[0]), workspace)
        if change == "replaced":
            await service.create_worktree(
                str(repository[0]), "main", workspace, "foreign-branch"
            )
    fresh = GitWorktreeProvider(service, cache)
    try:
        with pytest.raises(WorkspaceError):
            await fresh.resume(
                snapshot=snapshot,
                holder="other" if change == "holder" else "job",
                repo_path=str(repository[0]),
                repo_url=None,
                cache_key=None,
            )
        assert not fresh._workspaces
    finally:
        if path.exists():
            await service.remove_worktree(str(repository[0]), workspace)
        owner._workspaces.pop(workspace, None)


async def test_forged_workspace_path_cannot_adopt_another_actual_worktree(repository):
    service, cache, owner, workspace = await acquired(repository)
    other = await owner.acquire(
        repo_path=str(repository[0]), ref="main", branch_name="other"
    )
    try:
        snapshot = await owner.capture(workspace_path=workspace, holder="job")
        forged = WorkspaceSnapshot.model_validate(
            {**snapshot.model_dump(), "workspace_path": other}
        )
        fresh = GitWorktreeProvider(service, cache)
        with pytest.raises(WorkspaceError):
            await fresh.resume(
                snapshot=forged,
                holder="job",
                repo_path=str(repository[0]),
                repo_url=None,
                cache_key=None,
            )
        assert not fresh._workspaces
    finally:
        await owner.release(workspace)
        await owner.release(other)


async def test_identity_refuses_substituted_parent_symlink(repository, tmp_path):
    service, _cache, owner, workspace = await acquired(repository)
    path = Path(workspace)
    nested = path / "nested"
    nested.mkdir()
    (nested / "source.bin").write_bytes(b"tracked inside workspace")
    await git(path, "add", ".")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "source.bin").write_bytes(b"external bytes must not be read")
    (nested / "source.bin").unlink()
    nested.rmdir()
    nested.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(WorkspaceError):
            await service.worktree_identity(
                workspace, repository_path=str(repository[0])
            )
    finally:
        await owner.release(workspace)


async def test_forged_repository_association_cannot_adopt_foreign_worktree(
    repository, tmp_path
):
    service, cache, owner, workspace = await acquired(repository)
    other = tmp_path / "foreign-repository"
    other.mkdir()
    await git(other, "init", "-b", "main")
    await git(other, "config", "user.name", "Other owner")
    await git(other, "config", "user.email", "other@example.invalid")
    (other / "source").write_text("foreign content")
    await git(other, "add", ".")
    await git(other, "commit", "-m", "foreign base")
    foreign_owner = GitWorktreeProvider(service, FakeRepoCache(str(other)))
    foreign = await foreign_owner.acquire(
        repo_path=str(other), ref="main", branch_name="foreign-branch"
    )
    try:
        snapshot = await foreign_owner.capture(workspace_path=foreign, holder="job")
        fresh = GitWorktreeProvider(service, cache)
        with pytest.raises(WorkspaceError):
            await fresh.resume(
                snapshot=snapshot,
                holder="job",
                repo_path=str(repository[0]),
                repo_url=None,
                cache_key=None,
            )
        assert not fresh._workspaces
        assert Path(foreign).exists()
    finally:
        await foreign_owner.release(foreign)
        await owner.release(workspace)


async def test_unregistered_copy_cannot_supply_native_worktree_identity(
    repository, tmp_path
):
    service, _cache, owner, workspace = await acquired(repository)
    copied = tmp_path / "copied-native-worktree"
    shutil.copytree(workspace, copied, symlinks=True)
    assert (copied / ".git").read_bytes() == Path(workspace, ".git").read_bytes()
    try:
        with pytest.raises(WorkspaceError):
            await service.worktree_identity(
                str(copied), repository_path=str(repository[0])
            )
    finally:
        await owner.release(workspace)


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
async def test_native_resume_rejects_changed_index_flags(repository, flag):
    _service, _cache, owner, workspace = await acquired(repository)
    snapshot = await owner.capture(workspace_path=workspace, holder="job")
    await git(workspace, "update-index", flag, "policy.py")
    try:
        with pytest.raises(WorkspaceError):
            await owner.resume(
                snapshot=snapshot,
                holder="job",
                repo_path=str(repository[0]),
                repo_url=None,
                cache_key=None,
            )
    finally:
        await owner.release(workspace)


@pytest.mark.parametrize(
    "error", [ValueError("programmer value"), RuntimeError("programmer runtime")]
)
async def test_resume_preserves_programmer_errors_from_repository_port(
    repository, monkeypatch, error
):
    service, _cache, owner, workspace = await acquired(repository)
    snapshot = await owner.capture(workspace_path=workspace, holder="job")

    async def broken(repo_path):
        raise error

    monkeypatch.setattr(service, "validate_repo", broken)
    try:
        with pytest.raises(type(error)) as caught:
            await owner.resume(
                snapshot=snapshot,
                holder="job",
                repo_path=str(repository[0]),
                repo_url=None,
                cache_key=None,
            )
        assert caught.value is error
    finally:
        monkeypatch.undo()
        await owner.release(workspace)


@pytest.mark.parametrize("kind", ["missing", "file", "nonrepo"])
async def test_resume_refuses_actual_invalid_repository_path(
    repository, tmp_path, kind
):
    service, cache, owner, workspace = await acquired(repository)
    snapshot = await owner.capture(workspace_path=workspace, holder="job")
    requested = tmp_path / "invalid-repository"
    if kind == "file":
        requested.write_text("a file cannot own a Git worktree")
    elif kind == "nonrepo":
        requested.mkdir()
    fresh = GitWorktreeProvider(service, cache)
    try:
        with pytest.raises(WorkspaceError):
            await fresh.resume(
                snapshot=snapshot,
                holder="job",
                repo_path=str(requested),
                repo_url=None,
                cache_key=None,
            )
        assert not fresh._workspaces
        assert Path(workspace).exists()
    finally:
        await owner.release(workspace)
