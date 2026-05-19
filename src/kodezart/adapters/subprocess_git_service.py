"""Infrastructure adapter implementing the GitService port.

Git operations via subprocess.
"""

import asyncio
import os
from pathlib import Path

from kodezart.core.protocols import GitAuth
from kodezart.types.domain.consolidation import ChangesetDigest
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

    async def head_commit_message(self, cwd: str) -> str:
        """Return the full HEAD commit message (no trailing newline)."""
        return await self._run_output(
            ["git", "log", "-1", "--format=%B", "HEAD"],
            cwd=cwd,
        )

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

    async def is_ancestor(
        self,
        cwd: str,
        ancestor_ref: str,
        descendant_ref: str,
    ) -> bool:
        """Return True iff *ancestor_ref* is reachable from *descendant_ref*.

        Maps to ``git merge-base --is-ancestor``: exit 0 → True,
        exit 1 → False, any other exit raises.
        """
        exit_code, _ = await self._run_with_exit_codes(
            ["git", "merge-base", "--is-ancestor", ancestor_ref, descendant_ref],
            cwd=cwd,
            allowed=frozenset({0, 1}),
        )
        return exit_code == 0

    async def remote_branch_sha(
        self,
        cwd: str,
        remote: str,
        branch: str,
    ) -> str | None:
        """Tip SHA of *branch* on *remote*, or ``None`` when absent.

        Maps to ``git ls-remote --exit-code --heads <remote>
        refs/heads/<branch>``: exit 0 → parse SHA, exit 2 → None,
        any other exit raises.  Does NOT invoke ``git fetch``.
        """
        exit_code, stdout = await self._run_with_exit_codes(
            [
                "git",
                "ls-remote",
                "--exit-code",
                "--heads",
                remote,
                f"refs/heads/{branch}",
            ],
            cwd=cwd,
            allowed=frozenset({0, 2}),
            env=self._auth.subprocess_env() if self._auth else None,
        )
        if exit_code == 2:
            return None
        if not stdout:
            return None
        first_line = stdout.split("\n", 1)[0]
        parts = first_line.split("\t")
        if len(parts) != 2:
            msg = f"Unexpected ls-remote output: {first_line!r}"
            raise RuntimeError(msg)
        return parts[0]

    async def diff_summary(
        self,
        cwd: str,
        base_ref: str,
        head_ref: str,
    ) -> ChangesetDigest:
        """Return a ``ChangesetDigest`` for ``base_ref..head_ref``."""
        if base_ref == head_ref:
            return ChangesetDigest(
                file_paths=[],
                commit_subjects=[],
                commit_count=0,
            )
        files_output = await self._run_output(
            ["git", "diff", "--name-only", f"{base_ref}..{head_ref}"],
            cwd=cwd,
        )
        subjects_output = await self._run_output(
            [
                "git",
                "log",
                "--no-merges",
                "--format=%s",
                f"{base_ref}..{head_ref}",
            ],
            cwd=cwd,
        )
        file_paths = [line for line in files_output.split("\n") if line.strip()]
        commit_subjects = [line for line in subjects_output.split("\n") if line.strip()]
        return ChangesetDigest(
            file_paths=file_paths,
            commit_subjects=commit_subjects,
            commit_count=len(commit_subjects),
        )

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

    async def _run_with_exit_codes(
        self,
        cmd: list[str],
        cwd: str,
        allowed: frozenset[int],
        env: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        """Run *cmd*, allow declared exit codes, return ``(exit_code, stdout)``.

        Raises ``RuntimeError`` if the exit code is not in *allowed*.  Used
        by ``is_ancestor`` (exit 1 valid) and ``remote_branch_sha``
        (exit 2 valid) — the existing ``_run`` and ``_run_output`` continue
        to raise on any non-zero exit.
        """
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
        stdout, stderr = await proc.communicate()
        returncode = proc.returncode if proc.returncode is not None else -1
        if returncode not in allowed:
            msg = (
                f"{' '.join(cmd[:3])} exited {returncode} "
                f"(allowed {sorted(allowed)}): {stderr.decode().strip()}"
            )
            raise RuntimeError(msg)
        return returncode, stdout.decode().strip()
