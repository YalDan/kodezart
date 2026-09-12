"""Infrastructure adapter implementing the GitService port.

Git operations via subprocess.
"""

import asyncio
import hashlib
import os
import re
import stat
from contextlib import ExitStack
from pathlib import Path

from pydantic import ValidationError

from kodezart.core.protocols import GitAuth
from kodezart.domain.errors import (
    GitOperationError,
    GitRepositoryError,
    MergeConflictError,
    WorkspaceError,
)
from kodezart.types.domain.consolidation import ChangesetDigest
from kodezart.types.domain.git import LsRemoteEntry
from kodezart.types.domain.workspace import GitWorktreeIdentity

_UNKNOWN_EXIT_CODE = -1

_CONFLICT_LINE = re.compile(r"^CONFLICT \([^)]*\): .*? in (?P<path>.+)$", re.MULTILINE)


def _conflicting_paths(output: str) -> tuple[str, ...]:
    """Every path git named in a ``CONFLICT (...)`` line of *output*."""
    return tuple(
        match.group("path").strip() for match in _CONFLICT_LINE.finditer(output)
    )


class SubprocessGitService:
    """Git operations adapter using asyncio subprocess calls to the git CLI.

    Optionally injects ``GitAuth`` credentials.  Implements the ``GitService``
    protocol.
    """

    def __init__(self, *, remote: str, auth: GitAuth | None = None) -> None:
        # `self._remote` is the canonical remote used by `fetch()` and `push()`
        # (the two GitService methods whose protocol surface takes no per-call
        # `remote` arg). `delete_remote_branch`, `list_remote_branches`, and
        # `remote_branch_sha` continue to accept `remote: str` as a per-call
        # parameter for caller flexibility.
        self._remote = remote
        self._auth = auth

    async def validate_repo(self, repo_path: str) -> None:
        """Verify the path is a valid git repository."""
        repo = Path(repo_path)
        if not repo.is_dir():
            msg = f"Repository path does not exist: {repo_path}"
            raise GitRepositoryError(msg)
        if not ((repo / ".git").exists() or (repo / "HEAD").exists()):
            msg = f"Not a git repository: {repo_path}"
            raise GitRepositoryError(msg)

    async def worktree_identity(
        self, cwd: str, *, repository_path: str
    ) -> GitWorktreeIdentity:
        """Read actual worktree, index and working content without changing Git.

        Ignored untracked files are outside ``git add --all`` and this identity.
        Gitlinks refuse: their nested working copies require a separate ownership
        contract. NUL-delimited Git inventories retain arbitrary native filenames.
        """
        try:
            return await self._worktree_identity(cwd, repository_path=repository_path)
        except (OSError, ValidationError) as exc:
            raise WorkspaceError("The native worktree identity cannot be read") from exc

    async def _identity_git(self, cwd: str, *args: str) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            raise WorkspaceError(
                f"Native worktree identity read failed: git {args[0]} exited "
                f"{proc.returncode}"
            )
        return stdout

    async def _worktree_identity(
        self, cwd: str, *, repository_path: str
    ) -> GitWorktreeIdentity:
        if Path(cwd).is_symlink():
            raise WorkspaceError("A native workspace cannot be a substituted symlink")

        async def path(flag: str) -> Path:
            raw = await self._identity_git(
                cwd, "rev-parse", "--path-format=absolute", flag
            )
            return Path(os.fsdecode(raw.removesuffix(b"\n"))).resolve(strict=True)

        root = await path("--show-toplevel")
        if root != Path(cwd).resolve(strict=True):
            raise WorkspaceError("The native workspace is not its worktree root")
        common = await path("--git-common-dir")
        repository_common = Path(
            os.fsdecode(
                (
                    await self._identity_git(
                        repository_path,
                        "rev-parse",
                        "--path-format=absolute",
                        "--git-common-dir",
                    )
                ).removesuffix(b"\n")
            )
        ).resolve(strict=True)
        if repository_common != common:
            raise WorkspaceError("The retained worktree belongs to another repository")
        git_dir = await path("--git-dir")
        branch = os.fsdecode(
            (
                await self._identity_git(cwd, "symbolic-ref", "--short", "HEAD")
            ).removesuffix(b"\n")
        )
        head = (await self._identity_git(cwd, "rev-parse", "HEAD")).decode().strip()
        index = await self._identity_git(cwd, "ls-files", "--stage", "-v", "-z")
        untracked = await self._identity_git(
            cwd, "ls-files", "--others", "--exclude-standard", "-z"
        )
        paths: set[bytes] = set(untracked.split(b"\0")) - {b""}
        for entry in index.split(b"\0"):
            if not entry:
                continue
            if b"\t" not in entry:
                raise WorkspaceError("Git returned a malformed index entry")
            metadata, name = entry.split(b"\t", 1)
            fields = metadata.split(b" ")
            if len(fields) != 4:
                raise WorkspaceError("Git returned malformed index metadata")
            if fields[1] == b"160000":
                raise WorkspaceError(
                    "Native checkpoint ownership of gitlinks is unavailable"
                )
            paths.add(name)
        digest = hashlib.sha256()

        def add(value: bytes) -> None:
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)

        add(index)
        for name in sorted(paths):
            if name.startswith(b"/") or any(
                part in {b"", b".", b".."} for part in name.split(b"/")
            ):
                raise WorkspaceError("Git returned an invalid relative worktree path")
            add(name)
            # Open every directory component without following links. Protecting
            # only the final file would still traverse a substituted parent link.
            with ExitStack() as descriptors:
                directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                descriptors.callback(os.close, directory)
                parts = name.split(b"/")
                try:
                    for part in parts[:-1]:
                        directory = os.open(
                            part,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory,
                        )
                        descriptors.callback(os.close, directory)
                    leaf = parts[-1]
                    info = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    add(b"missing")
                    continue
                add(str(info.st_mode).encode())
                if stat.S_ISLNK(info.st_mode):
                    add(os.readlink(leaf, dir_fd=directory))
                elif stat.S_ISREG(info.st_mode):
                    descriptor = os.open(
                        leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory
                    )
                    with os.fdopen(descriptor, "rb") as content:
                        file_digest = hashlib.file_digest(content, "sha256").digest()
                    add(file_digest)
                elif not stat.S_ISDIR(info.st_mode):
                    raise WorkspaceError(
                        "A native worktree contains an unsupported file type"
                    )
        roster = await self._identity_git(
            repository_path, "worktree", "list", "--porcelain", "-z"
        )
        registrations = [
            record.split(b"\0")
            for record in roster.split(b"\0\0")
            if record.split(b"\0", 1)[0] == b"worktree " + os.fsencode(root)
        ]
        if (
            len(registrations) != 1
            or b"HEAD " + head.encode("ascii") not in registrations[0]
            or b"branch refs/heads/" + os.fsencode(branch) not in registrations[0]
        ):
            raise WorkspaceError(
                "The native path is not the repository's registered branch worktree"
            )
        root_stat, common_stat, git_stat = root.stat(), common.stat(), git_dir.stat()
        return GitWorktreeIdentity(
            root=str(root),
            root_device=root_stat.st_dev,
            root_inode=root_stat.st_ino,
            common_dir=str(common),
            common_device=common_stat.st_dev,
            common_inode=common_stat.st_ino,
            git_dir=str(git_dir),
            git_device=git_stat.st_dev,
            git_inode=git_stat.st_ino,
            branch=branch,
            head_sha=head,
            content_digest=digest.hexdigest(),
        )

    def is_repo(self, path: str) -> bool:
        """Check if path is an existing git repo (regular or bare)."""
        p = Path(path)
        return p.is_dir() and ((p / ".git").exists() or (p / "HEAD").exists())

    async def clone_bare(self, url: str, target: str) -> None:
        """Clone a remote URL as a bare repository."""
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        effective_url = self._auth.authenticated_url(url) if self._auth else url
        await self._run(
            ["git", "clone", "--bare", "--origin", self._remote, effective_url, target],
            cwd=str(Path(target).parent),
            env=self._auth.subprocess_env() if self._auth else None,
        )

    async def fetch(self, repo_path: str) -> None:
        """Fetch latest from the configured remote, populating remote-tracking refs.

        Bare clones created with ``git clone --bare`` do NOT configure
        ``remote.<name>.fetch`` or create the ``refs/remotes/<name>/*``
        namespace (per git-clone docs); a bare ``git fetch <remote>``
        therefore leaves ``<remote>/<branch>`` unresolvable downstream
        (e.g. by ``git merge-base --is-ancestor``).  Passing the
        canonical refspec explicitly forces the remote-tracking refs
        to be populated regardless of clone-time configuration.
        """
        await self._run(
            [
                "git",
                "fetch",
                self._remote,
                f"+refs/heads/*:refs/remotes/{self._remote}/*",
            ],
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

    async def has_replace_refs(self, cwd: str) -> bool:
        """Read replacement refs from Git's active replacement namespace."""
        output = await self._run_output(["git", "replace", "--list"], cwd=cwd)
        return bool(output)

    async def is_path_ignored(self, cwd: str, path: str) -> bool:
        """Return True iff *path* is excluded by the repository's ignore rules.

        Maps to ``git check-ignore --quiet``: exit 0 → ignored,
        exit 1 → not ignored, any other exit raises.  Consults the index,
        so a tracked path is reported as not ignored.
        """
        exit_code, _ = await self._run_with_exit_codes(
            ["git", "check-ignore", "--quiet", path],
            cwd=cwd,
            allowed=frozenset({0, 1}),
        )
        return exit_code == 0

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
            env=self._author_env(author_name, author_email),
        )
        return await self._run_output(["git", "rev-parse", "HEAD"], cwd=cwd)

    async def push(self, cwd: str, branch: str) -> None:
        """Push HEAD to the named branch on the default remote.

        Uses an explicit refspec ``HEAD:refs/heads/{branch}`` instead of
        a bare branch name for portable worktree push behavior.
        """
        await self._run(
            ["git", "push", self._remote, f"HEAD:refs/heads/{branch}"],
            cwd=cwd,
            env=self._auth.subprocess_env() if self._auth else None,
        )

    async def merge_branch(self, cwd: str, source_branch: str) -> None:
        """Fast-forward merge a source branch into HEAD.

        A merge git refuses raises ``MergeConflictError`` carrying the paths
        git named as conflicting.  The list is empty when git refused
        without naming any — a divergence that cannot fast-forward, for
        instance — because "conflicts, here" and "conflicts, somewhere" are
        different facts and collapsing them would lose the second.
        """
        try:
            await self._run(["git", "merge", "--ff-only", source_branch], cwd=cwd)
        except GitOperationError as exc:
            raise MergeConflictError(
                f"merge of {source_branch} could not be completed",
                source_branch=source_branch,
                paths=_conflicting_paths(str(exc)),
            ) from exc

    async def merge_scratch_head(
        self, *, cwd: str, head_sha: str, author_name: str, author_email: str
    ) -> None:
        """Compose a commit without updating any named branch.

        Sibling heads require a real merge; the existing fast-forward-only
        consolidation operation deliberately retains its separate contract.
        """
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", head_sha) is None:
            raise ValueError("scratch merge requires a full immutable commit SHA")
        attached, _ = await self._run_with_exit_codes(
            ["git", "symbolic-ref", "--quiet", "HEAD"],
            cwd=cwd,
            allowed=frozenset({0, 1}),
        )
        if attached == 0:
            raise ValueError("scratch merge requires detached HEAD")
        try:
            await self._run(
                [
                    "git",
                    "merge",
                    "--no-ff",
                    "--no-edit",
                    "--no-gpg-sign",
                    "--",
                    head_sha,
                ],
                cwd=cwd,
                env=self._author_env(author_name, author_email),
            )
        except GitOperationError as exc:
            unmerged = await self._run_output(
                ["git", "diff", "--name-only", "--diff-filter=U"],
                cwd=cwd,
            )
            if not unmerged:
                raise
            raise MergeConflictError(
                f"scratch merge of {head_sha} could not be completed",
                source_branch=head_sha,
                paths=tuple(unmerged.splitlines()),
            ) from exc

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
            raise GitOperationError(msg)
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

    async def reset_hard(self, cwd: str, ref: str) -> None:
        """Hard-reset working tree + index + HEAD to *ref*."""
        await self._run(["git", "reset", "--hard", ref], cwd=cwd)

    async def tree_of(self, cwd: str, ref: str) -> str:
        """Tree SHA reachable from *ref*."""
        return await self._run_output(
            ["git", "rev-parse", f"{ref}^{{tree}}"],
            cwd=cwd,
        )

    async def commit_tree(
        self,
        cwd: str,
        tree: str,
        parent: str,
        message: str,
        author_name: str,
        author_email: str,
    ) -> str:
        """Create a commit object referencing *tree* with one *parent* and *message*."""
        return await self._run_output(
            ["git", "commit-tree", tree, "-p", parent, "-m", message],
            cwd=cwd,
            env=self._author_env(author_name, author_email),
        )

    @staticmethod
    def _failure_detail(
        stdout: bytes,
        stderr: bytes,
        returncode: int | None,
    ) -> str:
        """Best available failure text for a non-zero git exit.

        Git writes diagnostics to stderr for most failures but to stdout
        for others (``git commit`` on a clean tree), and to neither when
        ``--quiet`` is in effect.
        """
        code = returncode if returncode is not None else _UNKNOWN_EXIT_CODE
        return stderr.decode().strip() or stdout.decode().strip() or f"exit code {code}"

    @staticmethod
    def _author_env(name: str, email: str) -> dict[str, str]:
        """Build the GIT_{AUTHOR,COMMITTER}_{NAME,EMAIL} env dict for git."""
        return {
            "GIT_AUTHOR_NAME": name,
            "GIT_COMMITTER_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_EMAIL": email,
        }

    async def _branch_exists(self, repo_path: str, branch_name: str) -> bool:
        try:
            await self._run_output(
                ["git", "rev-parse", "--verify", branch_name],
                cwd=repo_path,
            )
            return True
        except GitOperationError:
            return False

    async def _run_output(
        self,
        cmd: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> str:
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
        if proc.returncode != 0:
            detail = self._failure_detail(stdout, stderr, proc.returncode)
            msg = f"{' '.join(cmd[:3])} failed: {detail}"
            raise GitOperationError(msg)
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
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            detail = self._failure_detail(stdout, stderr, proc.returncode)
            msg = f"{' '.join(cmd[:3])} failed: {detail}"
            raise GitOperationError(msg)

    async def _run_with_exit_codes(
        self,
        cmd: list[str],
        cwd: str,
        allowed: frozenset[int],
        env: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        """Run *cmd*, allow declared exit codes, return ``(exit_code, stdout)``.

        Raises ``GitOperationError`` if the exit code is not in *allowed*.  Used
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
        returncode = (
            proc.returncode if proc.returncode is not None else _UNKNOWN_EXIT_CODE
        )
        if returncode not in allowed:
            detail = self._failure_detail(stdout, stderr, proc.returncode)
            msg = (
                f"{' '.join(cmd[:3])} exited {returncode} "
                f"(allowed {sorted(allowed)}): {detail}"
            )
            raise GitOperationError(msg)
        return returncode, stdout.decode().strip()
