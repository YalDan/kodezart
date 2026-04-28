"""Infrastructure adapter implementing the GitService port.

Git operations via subprocess.
"""

import asyncio
import os
from pathlib import Path

from kodezart.core.protocols import GitAuth
from kodezart.types.domain.git import LsRemoteEntry

_REMOTE = "origin"


class SubprocessGitService:
    """Git operations adapter using asyncio subprocess calls to the git CLI.

    Optionally injects ``GitAuth`` credentials.  Implements the ``GitService``
    protocol.
    """

    def __init__(self, auth: GitAuth | None = None) -> None:
        self._auth = auth

    async def validate_repo(self, repo_path: str) -> None:
        """Verify the path is a valid git repository."""
        repo = Path(repo_path)
        if not repo.is_dir():
            msg = f"Repository path does not exist: {repo_path}"
            raise ValueError(msg)
        if not ((repo / ".git").exists() or (repo / "HEAD").exists()):
            msg = f"Not a git repository: {repo_path}"
            raise ValueError(msg)

    def is_repo(self, path: str) -> bool:
        """Check if path is an existing git repo (regular or bare)."""
        p = Path(path)
        return p.is_dir() and ((p / ".git").exists() or (p / "HEAD").exists())

    async def clone_bare(self, url: str, target: str) -> None:
        """Clone a remote URL as a bare repository."""
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        effective_url = self._auth.authenticated_url(url) if self._auth else url
        await self._run(
            ["git", "clone", "--bare", effective_url, target],
            cwd=str(Path(target).parent),
            env=self._auth.subprocess_env() if self._auth else None,
        )

    async def fetch(self, repo_path: str) -> None:
        """Fetch latest from all remotes."""
        await self._run(
            ["git", "fetch", _REMOTE],
            cwd=repo_path,
            env=self._auth.subprocess_env() if self._auth else None,
        )

    async def create_worktree(
        self,
        repo_path: str,
        base_ref: str,
        worktree_path: str,
        branch_name: str | None = None,
        create_branch: bool = True,
    ) -> None:
        """Create a git worktree at the given path."""
        if branch_name is not None and create_branch:
            if await self._branch_exists(repo_path, branch_name):
                cmd = ["git", "worktree", "add", worktree_path, branch_name]
            else:
                cmd = [
                    "git",
                    "worktree",
                    "add",
                    "-b",
                    branch_name,
                    worktree_path,
                    base_ref,
                ]
        elif branch_name is not None:
            cmd = ["git", "worktree", "add", worktree_path, branch_name]
        else:
            cmd = [
                "git",
                "worktree",
                "add",
                "--detach",
                worktree_path,
                base_ref,
            ]
        await self._run(cmd, cwd=repo_path)

    async def remove_worktree(
        self,
        repo_path: str,
        worktree_path: str,
    ) -> None:
        """Remove a git worktree and prune."""
        await self._run(
            ["git", "worktree", "remove", "--force", worktree_path],
            cwd=repo_path,
        )

    async def has_changes(self, cwd: str) -> bool:
        """Return True if the working tree has uncommitted changes."""
        output = await self._run_output(["git", "status", "--porcelain"], cwd=cwd)
        return len(output) > 0

    async def add_all(self, cwd: str) -> None:
        """Stage all changes."""
        await self._run(["git", "add", "--all"], cwd=cwd)

    async def commit(
        self,
        cwd: str,
        message: str,
        author_name: str,
        author_email: str,
    ) -> str:
        """Create a commit with the given message and author."""
        await self._run(
            ["git", "commit", "-m", message],
            cwd=cwd,
            env={
                "GIT_AUTHOR_NAME": author_name,
                "GIT_COMMITTER_NAME": author_name,
                "GIT_AUTHOR_EMAIL": author_email,
                "GIT_COMMITTER_EMAIL": author_email,
            },
        )
        return await self._run_output(["git", "rev-parse", "HEAD"], cwd=cwd)

    async def push(self, cwd: str, branch: str) -> None:
        """Push HEAD to the named branch on the default remote.

        Uses an explicit refspec ``HEAD:refs/heads/{branch}`` instead of
        a bare branch name for portable worktree push behavior.
        """
        await self._run(
            ["git", "push", _REMOTE, f"HEAD:refs/heads/{branch}"],
            cwd=cwd,
            env=self._auth.subprocess_env() if self._auth else None,
        )

    async def merge_branch(self, cwd: str, source_branch: str) -> None:
        """Fast-forward merge a source branch into HEAD."""
        await self._run(["git", "merge", "--ff-only", source_branch], cwd=cwd)

    async def current_sha(self, cwd: str) -> str:
        """Return the current HEAD SHA."""
        return await self._run_output(["git", "rev-parse", "HEAD"], cwd=cwd)

    async def delete_remote_branch(
        self,
        cwd: str,
        remote: str,
        branch: str,
    ) -> None:
        """Delete a branch from a remote."""
        await self._run(
            ["git", "push", remote, "--delete", branch],
            cwd=cwd,
            env=self._auth.subprocess_env() if self._auth else None,
        )

    async def list_remote_branches(
        self,
        cwd: str,
        remote: str,
        prefix: str,
    ) -> list[str]:
        """List remote branch names starting with *prefix* via ls-remote."""
        output = await self._run_output(
            ["git", "ls-remote", "--heads", remote],
            cwd=cwd,
        )
        if not output:
            return []
        ref_prefix = "refs/heads/"
        branches: list[str] = []
        for line in output.split("\n"):
            parts = line.split("\t")
            if len(parts) != 2:
                continue
            entry = LsRemoteEntry(sha=parts[0], ref=parts[1])
            if entry.ref.startswith(ref_prefix):
                name = entry.ref[len(ref_prefix) :]
                if name.startswith(prefix):
                    branches.append(name)
        return branches

    async def _branch_exists(self, repo_path: str, branch_name: str) -> bool:
        try:
            await self._run_output(
                ["git", "rev-parse", "--verify", branch_name],
                cwd=repo_path,
            )
            return True
        except RuntimeError:
            return False

    async def _run_output(self, cmd: list[str], cwd: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            msg = f"{' '.join(cmd[:3])} failed: {stderr.decode().strip()}"
            raise RuntimeError(msg)
        return stdout.decode().strip()

    async def _run(
        self,
        cmd: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        process_env: dict[str, str] | None = None
        if env is not None:
            process_env = {**os.environ, **env}
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=process_env,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            msg = f"{' '.join(cmd[:3])} failed: {stderr.decode().strip()}"
            raise RuntimeError(msg)
