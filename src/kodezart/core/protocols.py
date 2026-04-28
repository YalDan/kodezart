"""Protocol definitions — composition without inheritance."""

from collections.abc import AsyncIterator, Mapping
from typing import Protocol, runtime_checkable

from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.persist import PersistResult


@runtime_checkable
class LogEmitter(Protocol):
    """Structured logging port — structlog.stdlib.BoundLogger satisfies this."""

    async def ainfo(self, event: str, **kwargs: object) -> None: ...
    async def adebug(self, event: str, **kwargs: object) -> None: ...
    async def awarning(self, event: str, **kwargs: object) -> None: ...
    async def aerror(self, event: str, **kwargs: object) -> None: ...


@runtime_checkable
class GitService(Protocol):
    """Git operations port — SubprocessGitService satisfies this."""

    async def validate_repo(self, repo_path: str) -> None: ...

    def is_repo(self, path: str) -> bool: ...

    async def clone_bare(self, url: str, target: str) -> None: ...

    async def fetch(self, repo_path: str) -> None: ...

    async def create_worktree(
        self,
        repo_path: str,
        base_ref: str,
        worktree_path: str,
        branch_name: str | None = None,
        create_branch: bool = True,
    ) -> None: ...

    async def remove_worktree(
        self,
        repo_path: str,
        worktree_path: str,
    ) -> None: ...

    async def has_changes(self, cwd: str) -> bool: ...

    async def add_all(self, cwd: str) -> None: ...

    async def commit(
        self,
        cwd: str,
        message: str,
        author_name: str,
        author_email: str,
    ) -> str: ...

    async def push(self, cwd: str, branch: str) -> None:
        """Push HEAD to the named branch on the remote."""
        ...

    async def merge_branch(self, cwd: str, source_branch: str) -> None: ...

    async def current_sha(self, cwd: str) -> str: ...

    async def delete_remote_branch(
        self,
        cwd: str,
        remote: str,
        branch: str,
    ) -> None: ...

    async def list_remote_branches(
        self,
        cwd: str,
        remote: str,
        prefix: str,
    ) -> list[str]:
        """List remote branch names starting with *prefix*."""
        ...


@runtime_checkable
class RepoCache(Protocol):
    """Ensures a remote repo is locally available as a bare clone."""

    async def ensure_available(
        self,
        url: str,
        cache_key: str | None = None,
    ) -> str:
        """Returns local path to bare repo."""
        ...


@runtime_checkable
class AgentExecutor(Protocol):
    """Executes agent prompts against a codebase and streams typed events."""

    def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: str,
        allowed_tools: list[str],
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Stream events by executing a prompt in *cwd*."""
        ...


@runtime_checkable
class WorkspaceProvider(Protocol):
    """Provides isolated workspaces for agent execution."""

    async def acquire(
        self,
        *,
        repo_path: str | None = None,
        repo_url: str | None = None,
        ref: str = "HEAD",
        branch_name: str | None = None,
        create_branch: bool = True,
        cache_key: str | None = None,
    ) -> str:
        """Acquire an isolated workspace. Returns its path."""
        ...

    async def release(self, workspace_path: str) -> None:
        """Release and clean up a previously acquired workspace."""
        ...


@runtime_checkable
class ChangePersister(Protocol):
    """Detects changes, generates commit message, commits, pushes."""

    async def persist(
        self,
        *,
        workspace_path: str,
        branch: str,
        executor: AgentExecutor,
    ) -> PersistResult | None:
        """Commit and push changes. ``None`` if clean."""
        ...


@runtime_checkable
class BranchMerger(Protocol):
    """Merges a source branch into a feature branch and pushes."""

    async def merge_and_push(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        base_branch: str,
        feature_branch: str,
        source_branch: str,
        cache_key: str | None = None,
    ) -> str:
        """FF-merge source into feature, push. Returns SHA."""
        ...

    async def cleanup_source(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        source_branch: str,
        cache_key: str | None = None,
    ) -> None:
        """Delete source_branch from the remote. Must not raise."""
        ...

    async def cleanup_backup_branches(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        prefix: str,
        cache_key: str | None = None,
    ) -> None:
        """Batch-delete backup branches matching *prefix*. Must not raise."""
        ...


@runtime_checkable
class PRCreator(Protocol):
    """Opens pull requests and posts comments on a code hosting platform."""

    async def create_pr(
        self,
        *,
        repo_url: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> tuple[str, int]: ...

    async def comment_on_pr(
        self,
        *,
        repo_url: str,
        pr_number: int,
        body: str,
    ) -> None: ...


@runtime_checkable
class CIMonitor(Protocol):
    """Polls CI status for a commit ref."""

    async def wait_for_checks(
        self,
        *,
        repo_url: str,
        ref: str,
    ) -> tuple[bool | None, str]: ...


@runtime_checkable
class ArtifactPersister(Protocol):
    """Persists and cleans named files under .kodezart/ on a branch."""

    async def persist(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        branch: str,
        base_branch: str,
        artifacts: Mapping[str, str],
        cache_key: str | None = None,
    ) -> None:
        """Write artifacts to .kodezart/, commit, push."""
        ...

    async def clean(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        branch: str,
        cache_key: str | None = None,
    ) -> None:
        """Remove .kodezart/ directory, commit, push. Must not raise."""
        ...


@runtime_checkable
class AgentRunner(Protocol):
    """Runs agents in isolated workspaces with optional persistence.

    This protocol exists for DIP consistency, information hiding, and
    testability — not because multiple implementations are expected.
    The real variation points (LLM, workspace, persistence) are behind
    their own protocols inside AgentService.
    """

    def stream(
        self,
        *,
        prompt: str,
        repo_path: str | None = None,
        repo_url: str | None = None,
        branch: str | None = None,
        permission_mode: str,
        allowed_tools: list[str],
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
        cache_key: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """One-shot agent query with workspace lifecycle."""
        ...

    def stream_workflow(
        self,
        *,
        prompt: str,
        repo_path: str | None = None,
        repo_url: str | None = None,
        base_branch: str = "main",
        branch_name: str | None = None,
        ralph_branch: str | None = None,
        permission_mode: str,
        allowed_tools: list[str],
        create_branch: bool = True,
        cache_key: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Workflow mode with branch creation and persistence."""
        ...

    def stream_in_workspace(
        self,
        *,
        prompt: str,
        workspace_path: str,
        permission_mode: str,
        allowed_tools: list[str],
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Execute in a pre-acquired workspace (no lifecycle)."""
        ...


@runtime_checkable
class GitAuth(Protocol):
    """Provides credentials for git network operations."""

    def authenticated_url(self, clone_url: str) -> str:
        """Return URL with credentials embedded (or unchanged)."""
        ...

    def subprocess_env(self) -> dict[str, str]:
        """Return env vars for git subprocess (e.g. GIT_ASKPASS). Empty if none."""
        ...


@runtime_checkable
class QualityGate(Protocol):
    """Iterates agent work until acceptance criteria pass or max iterations."""

    def run(
        self,
        *,
        prompt: str,
        repo_path: str | None,
        repo_url: str | None,
        feature_branch: str,
        ralph_branch: str,
        base_branch: str,
        permission_mode: str,
        allowed_tools: list[str],
        acceptance_criteria: list[str],
        cache_key: str,
    ) -> AsyncIterator[AgentEvent]:
        """Iterate execute/evaluate until pass or max."""
        ...


@runtime_checkable
class TicketGenerator(Protocol):
    """Iteratively drafts and reviews a ticket from a raw user prompt."""

    def run(
        self,
        *,
        prompt: str,
        repo_path: str | None,
        repo_url: str | None,
        cache_key: str,
    ) -> AsyncIterator[AgentEvent]:
        """Draft/review loop until approved or max reviews."""
        ...


@runtime_checkable
class WorkflowEngine(Protocol):
    """Runs the iterative agent loop with quality gating."""

    def run(
        self,
        *,
        prompt: str,
        repo_path: str | None,
        repo_url: str | None,
        base_branch: str,
        permission_mode: str,
        allowed_tools: list[str],
    ) -> AsyncIterator[AgentEvent]:
        """Full pipeline: branch → ticket → criteria → loop → merge."""
        ...
