"""Protocol definitions — composition without inheritance."""

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Protocol, runtime_checkable

from kodezart.core.prompt_rendering import PromptTemplate
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.consolidation import (
    ChangesetDigest,
    ConsolidationOutcome,
)
from kodezart.types.domain.criteria import ValidatedCriterion
from kodezart.types.domain.gating import (
    GateDecision,
    RepoVisibility,
    ScanHit,
    WriterShape,
)
from kodezart.types.domain.job import JobRecord
from kodezart.types.domain.persist import PersistResult
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run import RunState
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.requests.agent import WorkflowRequest


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

    async def head_commit_message(self, cwd: str) -> str:
        """Full commit message of HEAD.

        Maps to ``git log -1 --format=%B HEAD`` with the trailing
        newline stripped.
        """
        ...

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

    async def is_ancestor(
        self,
        cwd: str,
        ancestor_ref: str,
        descendant_ref: str,
    ) -> bool:
        """True iff *ancestor_ref* is reachable from *descendant_ref*.

        Maps to ``git merge-base --is-ancestor`` (exit 0 → True,
        exit 1 → False, any other exit raises).
        """
        ...

    async def remote_branch_sha(
        self,
        cwd: str,
        remote: str,
        branch: str,
    ) -> str | None:
        """Tip SHA of *branch* on *remote*, or ``None`` when absent.

        Maps to ``git ls-remote --exit-code --heads <remote> refs/heads/<branch>``
        (exit 0 → SHA, exit 2 → None, any other exit raises).  Does NOT
        invoke ``git fetch`` — ls-remote queries the remote directly.
        """
        ...

    async def diff_summary(
        self,
        cwd: str,
        base_ref: str,
        head_ref: str,
    ) -> ChangesetDigest:
        """File paths and commit subjects for ``base_ref..head_ref``.

        Empty digest when refs are equal.
        """
        ...

    async def reset_hard(self, cwd: str, ref: str) -> None:
        """Hard-reset working tree + index + HEAD to *ref*.

        Maps to ``git reset --hard <ref>``.
        """
        ...

    async def tree_of(self, cwd: str, ref: str) -> str:
        """Tree SHA reachable from *ref*.

        Maps to ``git rev-parse <ref>^{tree}``.
        """
        ...

    async def commit_tree(
        self,
        cwd: str,
        tree: str,
        parent: str,
        message: str,
        author_name: str,
        author_email: str,
    ) -> str:
        """Create a commit object referencing *tree* with one *parent* and *message*.

        Maps to ``git commit-tree <tree> -p <parent> -m <message>``. Returns
        the new commit SHA.
        """
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
        skills: SkillsSelection,
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
        ref: str,
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
        backup_ref_id_prefix: str,
        skills: SkillsSelection,
        visibility: RepoVisibility,
    ) -> PersistResult | None:
        """Commit and push changes. ``None`` if clean.

        ``backup_ref_id_prefix`` is an exactly-8-char identifier used to
        name a backup ref ``{branch}-backup-{prefix}`` if the persister
        needs to preserve a divergent local line during recovery.
        """
        ...


@runtime_checkable
class BranchMerger(Protocol):
    """Consolidates a source branch into a feature branch.

    `consolidate` is a total function over the four
    `ConsolidationStatus` values — it never raises on ``DIVERGENT`` or
    ``SOURCE_MISSING``.  Callers route on ``outcome.status``.  Source-
    branch deletion is an internal implementation detail of the
    FAST_FORWARDED branch and is never exposed on this protocol.
    """

    async def consolidate(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        base_branch: str,
        feature_branch: str,
        source_branch: str,
        cache_key: str | None = None,
    ) -> ConsolidationOutcome:
        """Classify and (if FAST_FORWARDED) merge source into feature.

        Never raises on ``DIVERGENT`` or ``SOURCE_MISSING``.  Returns
        a ``ConsolidationOutcome`` whose ``feature_tip_sha`` is the
        post-consolidation tip (or current tip on ALREADY_INTEGRATED
        and SOURCE_MISSING).
        """
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
        skills: SkillsSelection,
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
        skills: SkillsSelection,
        visibility: RepoVisibility,
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
        skills: SkillsSelection,
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
        acceptance_criteria: list[ValidatedCriterion],
        cache_key: str,
        repo_visibility: RepoVisibility,
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
        base_branch: str,
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
        cache_key: str,
    ) -> AsyncIterator[AgentEvent]:
        """Full pipeline: branch → ticket → criteria → loop → merge.

        ``cache_key`` IS the LangGraph thread id, so the caller's job id
        addresses the run's checkpoints.
        """
        ...


@runtime_checkable
class JobQueue(Protocol):
    """Accepts workflow submissions and streams the resulting run.

    NOT PERSISTENT.  The queue lives in the serving process: a restart
    drops every job still waiting and terminates every job in flight.
    An HTTP-submitted fire lost to a restart is re-submitted by its
    caller — this is documented behavior, not a silent one, and no
    persistence machinery stands behind it.
    """

    async def submit(self, *, lane: str, request: WorkflowRequest) -> JobRecord:
        """Enqueue *request* on *lane*. Raises ``QueueFullError`` at capacity."""
        ...

    def attach(self, *, job_id: str) -> AsyncIterator[AgentEvent]:
        """Replay the job's bounded event buffer, then stream live events."""
        ...


@runtime_checkable
class JobRegistry(Protocol):
    """Reads the lifecycle record of a submitted job."""

    async def get(self, *, job_id: str) -> JobRecord | None:
        """The job's current record, or ``None`` when unknown or evicted."""
        ...


@runtime_checkable
class RunStateReader(Protocol):
    """Reads a run's checkpointed state — the only seam onto LangGraph."""

    async def read(self, *, job_id: str) -> RunState | None:
        """Checkpointed state for *job_id*, or ``None`` when none exists."""
        ...


@runtime_checkable
class PromptProvider(Protocol):
    """Serves UNRENDERED prompt templates by function key.

    Consumers never import prompt modules — there are none.  Rendering is
    orthogonal: the provider returns a template, the single rendering path
    substitutes.  ``InRepoPromptRegistry`` is the first adapter.
    """

    def template_for(self, key: PromptKey) -> PromptTemplate:
        """Return the template registered for *key*."""
        ...

    def resolution_table(self) -> Mapping[PromptKey, str]:
        """Effective ``key -> set/source`` mapping over every key."""
        ...

    def declared_skills(self, key: PromptKey) -> Sequence[str]:
        """Skill names the resolved set declares for *key*. Empty is legal."""
        ...


@runtime_checkable
class SkillInventory(Protocol):
    """The skill names the host exposes at user scope."""

    def available(self) -> frozenset[str]:
        """Every resolvable skill name, including plugin-qualified ones."""
        ...


@runtime_checkable
class RepoVisibilityResolver(Protocol):
    """Resolves a repository's visibility once per run."""

    async def resolve_visibility(self, *, repo_url: str) -> RepoVisibility:
        """Return PRIVATE / PUBLIC, or UNKNOWN when resolution fails."""
        ...


@runtime_checkable
class ContentScanner(Protocol):
    """Finds deny-pattern matches in an outbound payload."""

    def scan(self, content: str) -> Sequence[ScanHit]:
        """Every match, with its category and span."""
        ...


@runtime_checkable
class OutboundContentGate(Protocol):
    """Assigns an explicit, observable verdict to every outbound payload."""

    def gate(
        self,
        *,
        content: str,
        visibility: RepoVisibility,
        shape: WriterShape,
    ) -> GateDecision:
        """CLEAN / REDACTED / BLOCKED — never silently dropped or posted."""
        ...
