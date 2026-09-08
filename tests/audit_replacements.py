"""Native cached repositories whose replacement tree differs from their SHA."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService


def command(repository: Path, *args: str, replacements: bool = False) -> str:
    return subprocess.run(
        ["git", *([] if replacements else ["--no-replace-objects"]), *args],
        cwd=repository,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()


@dataclass
class NativeAuditRepository:
    source: Path
    cache_path: Path
    head: str
    alternate: str
    git: SubprocessGitService
    cache: LocalBareRepoCache
    workspace: GitWorktreeProvider

    async def replace(self, workspace: str | None = None) -> None:
        command(self.cache_path, "replace", self.head, self.alternate)
        command(self.cache_path, "pack-refs", "--all")
        if workspace is not None:
            command(Path(workspace), "reset", "--hard", self.head, replacements=True)
            assert await self.git.current_sha(workspace) == self.head
            assert not await self.git.has_changes(workspace)
            assert (Path(workspace) / "evidence.txt").read_text() == "Substituted.\n"
        assert (
            command(self.cache_path, "show", f"{self.head}:evidence.txt") == "Original."
        )
        assert (
            command(
                self.cache_path, "show", f"{self.head}:evidence.txt", replacements=True
            )
            == "Substituted."
        )


@pytest.fixture
async def native_repository(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    command(source, "init", "-q")
    command(source, "config", "user.name", "Fixture")
    command(source, "config", "user.email", "fixture@example.invalid")
    evidence = source / "evidence.txt"
    evidence.write_text("Original.\n")
    command(source, "add", "evidence.txt")
    command(source, "commit", "-qm", "Original evidence")
    head = command(source, "rev-parse", "HEAD")
    command(source, "branch", "ordinary-name", head)
    evidence.write_text("Substituted.\n")
    command(source, "commit", "-qam", "Alternate evidence")
    alternate = command(source, "rev-parse", "HEAD")
    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    cache_path = Path(await cache.ensure_available(source.as_uri(), "audit-cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
    )
    return NativeAuditRepository(
        source, cache_path, head, alternate, git, cache, workspace
    )
