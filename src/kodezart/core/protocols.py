"""Protocol definitions — composition without inheritance."""

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Protocol, runtime_checkable

from kodezart.core.prompt_rendering import PromptTemplate
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.branch import BaseSpec, WorkRef
from kodezart.types.domain.consolidation import (
    ChangesetDigest,
    ConsolidationOutcome,
)
from kodezart.types.domain.criteria import ValidatedCriterion
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.gating import (
    ContentClass,
    GateDecision,
    OutboundDestination,
    RepoVisibility,
    ScannerRouting,
    ScanResult,
    WriterShape,
)
from kodezart.types.domain.job import JobRecord
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.persist import ArtifactPersistStatus, PersistResult
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run import RunState
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import (
    NO_SUBAGENTS,
    UNCONFIGURED_SESSION_POLICY,
    AgentDefinition,
    SessionPolicy,
)
from kodezart.types.domain.tracker import (
    ClaimResult,
    IssuePriority,
    IssueQuery,
    MappingOutcome,
    MappingRef,
    ReviewQuery,
    TrackerAsset,
    TrackerComment,
    TrackerIssue,
    TrackerReview,
)
from kodezart.types.domain.workflow import RemediationRequest
from kodezart.types.requests.agent import WorkflowRequest


@runtime_checkable
class LogEmitter(Protocol):
    """Structured logging port — structlog's stdlib BoundLogger satisfies it.

    The five methods are the five this codebase actually awaits, and a test
    derives that set from the syntax tree rather than from a reading, so the
    port cannot drift from its callers in either direction: a method called
    but not declared fails, and a method declared but never called fails too.

    Values are ``object`` rather than ``Any`` because the renderer accepts
    whatever it is handed and the strict-mode ban on explicit ``Any`` holds
    here as everywhere else.
    """

    async def ainfo(self, event: str, **kwargs: object) -> None: ...

    async def adebug(self, event: str, **kwargs: object) -> None: ...

    async def awarning(self, event: str, **kwargs: object) -> None: ...

    async def aerror(self, event: str, **kwargs: object) -> None: ...

    async def aexception(self, event: str, **kwargs: object) -> None: ...


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

    async def is_path_ignored(self, cwd: str, path: str) -> bool:
        """True iff *path* is excluded by the repository's ignore rules.

        Maps to ``git check-ignore --quiet <path>`` (exit 0 → True,
        exit 1 → False, any other exit raises).  A path already tracked
        in the index is reported as not ignored.
        """
        ...

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
        session_type: SessionType,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Stream events by executing a prompt in *cwd*.

        *session_type* names what the session is for.  It carries no
        default: every caller states its kind, because the kind is what
        the knowledge grant is resolved against.

        *agents* and *session_policy* are what makes a session role
        expressible at the port instead of around it.  An empty *agents*
        sequence is a guarantee that the session spawns nothing; an
        unconfigured *session_policy* leaves construction-time
        configuration in force and constructs the same options this port
        constructed before it widened.
        """
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
class RefPublisher(Protocol):
    """Publishes an existing commit under a named ref on the remote."""

    async def publish(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        commit_sha: str,
        ref: str,
        cache_key: str | None = None,
    ) -> None:
        """Point *ref* at *commit_sha* on the remote.

        Separate from ``BranchMerger`` because publishing combines no
        trees: there is no conflict state, so no outcome to route on and
        nothing for a caller to decide.  It exists so a commit can be
        made visible to a forge without first being integrated anywhere
        — which is exactly what a pull request needs and a merge is not.
        """
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
    ) -> None:
        """Post *body* on the pull request, or raise the forge's refusal.

        Refusals are typed — ``ForgeAPIError`` for a status no retry
        changes, ``TransientAPIError`` once the adapter's own budget is
        spent — and NEVER the transport's own exception types, so a
        consumer contains them without importing an adapter's vendor.

        A failure-report comment is the one write whose failure is
        logged rather than fatal: it reports an outcome the run reports
        again terminally, so crashing here would lose more than it
        reports.  That containment is the CALLER's (see
        ``_comment_failure_node``); this port always raises.
        """
        ...


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
class DeliveryProbe(Protocol):
    """Answers whether an open pull request already delivers an issue.

    The forge side of the delivered-in-review / crashed discrimination:
    workflow state alone conflates the two, and the open pull request is
    the mechanical discriminator.  Matching a pull request to an issue is
    adapter-owned — no consumer parses a branch name or a body.
    """

    async def open_delivery_exists(
        self,
        *,
        repo_url: str,
        issue_key: str,
    ) -> bool:
        """True iff an OPEN pull request on *repo_url* delivers *issue_key*."""
        ...


#: What a tool call answers with.  A JSON object OR a JSON array: the MCP
#: spec constrains a tool result to neither shape, and a measured server
#: answered some of its tools with a bare array carrying no envelope at
#: all (KOD-143).  Narrowing this to an object would put those payloads
#: out of reach of every adapter above the transport.  WHICH server and
#: which tool is an adapter's knowledge; this seam holds only the fact
#: that both shapes are legal.
type McpToolResult = Mapping[str, object] | Sequence[object]


@runtime_checkable
class McpToolCaller(Protocol):
    """Speaks MCP to one server: a tool name plus arguments, in, result out.

    The transport seam under every MCP-backed adapter.  No model is in
    this loop — the caller names the tool, so the deterministic path stays
    deterministic.  An in-process fake server satisfies this protocol,
    which is what keeps CI free of live-workspace access.
    """

    async def call_tool(
        self,
        *,
        name: str,
        arguments: Mapping[str, object],
    ) -> McpToolResult:
        """Invoke the named tool and return its structured result."""
        ...


@runtime_checkable
class ManagedMcpToolCaller(McpToolCaller, Protocol):
    """An ``McpToolCaller`` whose connection has a lifetime the host owns.

    The composition root opens one at boot and closes it at shutdown, so a
    session handshake is not re-run per tool call.  Separate from
    ``McpToolCaller`` because a consumer never opens or closes anything —
    it names a tool and reads a result.
    """

    async def open(self) -> None:
        """Establish the session. Opening an open caller is an error."""
        ...

    async def close(self) -> None:
        """Close the session. Closing a closed caller is a no-op."""
        ...


@runtime_checkable
class TrackerPort(Protocol):
    """The whole capability surface the passes and the runner need.

    Vendor-neutral by construction: every parameter and every return type
    is domain vocabulary.  Substitutability is total — an adapter
    implements ALL of this or it is not an adapter.  There are no
    capability flags and no feature detection, so no consumer ever
    branches on which backend is configured.
    """

    async def scan_issues(self, *, query: IssueQuery) -> Sequence[TrackerIssue]:
        """Issues matching *query*, in backend order."""
        ...

    async def scan_reviews(self, *, query: ReviewQuery) -> Sequence[TrackerReview]:
        """Reviews matching *query*, newest first.

        A separate call rather than a flag on :meth:`scan_issues` because
        a review is a separate object class: no issue scan reaches one at
        any page size, so a consumer asking "did anything move?" over
        issues alone is answering a narrower question than it thinks.

        Newest first is part of the contract, not an accident of a
        backend: it is what lets a recency question be answered from one
        page instead of walking the whole set.
        """
        ...

    async def verify_scan_capability(
        self,
        *,
        signals: Sequence[PassSignal],
    ) -> Mapping[PassSignal, str]:
        """Which of *signals* this credential cannot scan for, and why.

        Answered by CALLING each signal's scan, never by reading a roster
        of what the backend offers: a scan a credential holds no scope for
        is offered like any other, so a listing check passes and changes
        nothing.  One minimal call per distinct scan — two signals served
        by the same one cost one call.

        The port speaks signals; which scan answers a signal is the
        adapter's own business and never crosses this surface.

        One entry per REFUSED signal, carrying the backend's own diagnosis
        of the refusal.  A signal the credential can scan has no entry, so
        an empty mapping means every one of them is answerable.  Every
        other failure RAISES: a transport that could not answer at all has
        said nothing about scope, and reporting it as a refusal would take
        a pass off the air for the length of an outage.
        """
        ...

    async def read_issue(self, *, issue_key: str) -> TrackerIssue:
        """The full issue — body, state, relations, parent, assignee."""
        ...

    async def create_issue(
        self,
        *,
        title: str,
        body: str,
        team_key: str,
        priority: IssuePriority,
    ) -> TrackerIssue:
        """Create an issue on *team_key* and return it as stored."""
        ...

    async def update_issue(
        self,
        *,
        issue_key: str,
        title: str | None = None,
        body: str | None = None,
    ) -> TrackerIssue:
        """Update the given fields; ``None`` leaves a field untouched."""
        ...

    async def set_workflow_state(
        self,
        *,
        issue_key: str,
        stage: LifecycleStage,
    ) -> TrackerIssue:
        """Move the issue to the state the configuration binds *stage* to."""
        ...

    async def restore_workflow_state(
        self,
        *,
        issue_key: str,
        state_name: str,
    ) -> TrackerIssue:
        """Put the issue back in the state a reader found it in.

        The undo of ``set_workflow_state``, and the only write naming a
        backend state directly.  It has to: the operation's mapping binds
        three lifecycle stages, and the state a fire finds its issue in is
        almost never one of them — a claimed issue comes from the backlog,
        which the mapping never named and no ``LifecycleStage`` can
        express.  ``state_name`` is not vendor vocabulary leaking inward:
        it is the value this port already reports on every
        ``TrackerIssue``, handed straight back.
        """
        ...

    async def set_queue_state(
        self,
        *,
        issue_key: str,
        state: QueueState,
    ) -> TrackerIssue:
        """Set the semantic queue state, replacing any other member."""
        ...

    async def post_comment(self, *, issue_key: str, body: str) -> TrackerComment:
        """Post a comment and return it as stored."""
        ...

    async def list_comments(self, *, issue_key: str) -> Sequence[TrackerComment]:
        """Every comment on the issue, oldest first."""
        ...

    async def claim_issue(
        self,
        *,
        issue_key: str,
        holder: str,
        lease_seconds: float,
    ) -> ClaimResult:
        """Attempt an exactly-once claim.

        Concurrent claimants on one issue produce exactly one
        ``GRANTED``; every other claimant observes ``LOST``.  Losing is a
        value, never an exception.
        """
        ...

    async def renew_claim(
        self,
        *,
        issue_key: str,
        holder: str,
        lease_seconds: float,
    ) -> ClaimResult | None:
        """Extend a claim *holder* already holds, so it outlives its lease.

        Returns the claim as it now stands — expiring no earlier than
        *lease_seconds* from now — when *holder* holds a live claim on the
        issue.  Returns ``None``, writing NOTHING, when it does not.

        Renewal EXTENDS and never acquires.  A claim that has already
        lapsed stays lapsed and the issue stays claimable: the lapse is how
        a process that died mid-run hands its work back, and a renewal that
        could resurrect one would take that recovery away.
        """
        ...

    async def release_claim(self, *, issue_key: str, holder: str) -> None:
        """Release a claim held by *holder*. A claim it does not hold is a no-op."""
        ...

    async def active_claim(self, *, issue_key: str) -> ClaimResult | None:
        """The unexpired claim on the issue, or ``None`` when unclaimed.

        ``expires_at`` is when the CLAIM lapses, not when any one write
        that carried it does: a holder that renewed holds until the last of
        its renewals runs out.
        """
        ...

    async def list_issue_assets(self, *, issue_key: str) -> Sequence[TrackerAsset]:
        """Attachment and document metadata referenced by the issue."""
        ...

    async def read_document(self, *, document_key: str) -> str:
        """The document's text content."""
        ...

    async def record_work_ref(self, *, ref: WorkRef) -> None:
        """Record *ref* against its issue; ``work_refs`` is the read.

        At most one ``DELIVERABLE`` ref exists per issue: a second raises
        ``DuplicateWorkRefError`` and is never a silent replacement.
        Recording the same ref twice is idempotent.
        """
        ...

    async def work_refs(self, *, issue_key: str) -> Sequence[WorkRef]:
        """Every ref recorded against the issue, oldest first.

        This is the read D2 requires: *which refs deliver issue X, in which
        roles, at which shas* is answerable through the port, so no code
        anywhere derives an issue identity, a role or a parent from a
        branch name.
        """
        ...

    async def record_base_spec(self, *, issue_key: str, spec: BaseSpec) -> None:
        """Record the base *issue_key*'s lane was dispatched on.

        KOD-67 R3: the spec is written THROUGH the port, on the dependent
        issue.  Staleness compares a recorded spec against the one the
        blockers imply now, and with nothing recorded there is nothing to
        compare — the arithmetic would only ever compare a value with
        itself.  Recording the same spec twice is idempotent; recording a
        different one supersedes, because a lane dispatched again was
        dispatched on the base of that dispatch.
        """
        ...

    async def read_base_spec(self, *, issue_key: str) -> BaseSpec | None:
        """The base most recently recorded for *issue_key*, or ``None``.

        ``None`` means no dispatch ever recorded one — a first dispatch,
        not a stale base.  The two are different states and no caller may
        conflate them.
        """
        ...

    async def resolve_mappings(
        self,
        *,
        refs: Sequence[MappingRef],
    ) -> Sequence[MappingRef]:
        """The subset of *refs* that does NOT resolve in the workspace.

        Empty means every configured mapping exists.  The adapter resolves;
        deciding what an unresolvable entry means is the caller's.
        """
        ...

    async def ensure_mappings(
        self,
        *,
        refs: Sequence[MappingRef],
    ) -> Sequence[MappingOutcome]:
        """Instate every ref the operation OWNS, and say what that did.

        Creates only.  A value already present is adopted unchanged and
        never renamed, recoloured or repurposed; an ensure that would alter
        an existing definition raises ``TrackerEnsureConflictError`` and
        performs no write.  One outcome per ref, in the order given.
        """
        ...


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
    ) -> ArtifactPersistStatus:
        """Write artifacts to .kodezart/, commit, push.

        Returns which of the three outcomes occurred; a caller that only
        knows "it did not raise" cannot tell a successful push from a
        target that ignores the artifact directory.
        """
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
        session_type: SessionType,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
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
        session_type: SessionType,
        visibility: RepoVisibility,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
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
        session_type: SessionType,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
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
    """Iterates agent work until acceptance criteria pass or max iterations.

    ``work_base_ref`` and ``base_spec`` answer two different questions:
    the first is where the loop's first iteration cuts its branch, the
    second is what the work is diffed against.  A round built on top of
    an earlier round's consolidated work has them name different refs.
    """

    def run(
        self,
        *,
        prompt: str,
        repo_path: str | None,
        repo_url: str | None,
        feature_branch: str,
        ralph_branch: str,
        base_spec: BaseSpec,
        work_base_ref: str,
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
class Remediator(Protocol):
    """Turns failure evidence into one targeted follow-up ticket.

    Every failure route in the pipeline reaches this port — the entry is
    a field on the request, never a second method, so no caller can be
    served by a path the others do not share.
    """

    def run(
        self,
        request: RemediationRequest,
        *,
        repo_path: str | None,
        repo_url: str | None,
        cache_key: str,
    ) -> AsyncIterator[AgentEvent]:
        """Draft the remediation ticket for one round."""
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
        base_spec: BaseSpec,
        implied_base: BaseSpec | None = None,
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
class PromptSetProvider(PromptProvider, Protocol):
    """A prompt provider whose set also contributes SESSION content.

    Templates are keyed by function; a set additionally carries content
    that belongs to no single key — the lens definitions its generative
    roles dispatch and the house rules every session is appended.  Both
    are set data, so they are served by the same adapter, and both are
    read by dispatch sites that already hold the provider.
    """

    def definitions(self) -> Sequence[AgentDefinition]:
        """Typed lens definitions the resolved set declares. Empty is legal."""
        ...

    def system_prompt_append(self) -> str | None:
        """The set's system-prompt append, or ``None`` when it declares none."""
        ...

    def session_skills(
        self,
        key: PromptKey,
        configured: SkillsSelection,
    ) -> SkillsSelection:
        """*configured*, narrowed to what *key*'s role declares.

        The deployment decides what is available and the set decides what
        each role reaches for; what a dispatch gets is the intersection.
        """
        ...

    def session_policy(self, key: PromptKey) -> SessionPolicy:
        """What *key*'s dispatch declares about its session.

        Read from the set, never decided at the call site: a dispatch site
        asks the provider what this role runs at and passes the answer on.
        Set content, so it lives on the extending port beside the lens
        definitions and the house rules rather than on the keyed one.
        """
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
    """Finds outbound-content findings in one payload.

    ``async`` because a judgment scanner cannot answer behind a ``def``; a
    scanner that needs no I/O conforms with an ``async def`` awaiting
    nothing, which is the honest shape rather than a concession.

    ``destination`` is an input because the same string can be unremarkable
    on one surface and a leak on another — a verdict that depends on where
    the payload is going cannot be computed from the payload alone.

    Returns a :class:`ScanResult`: hits or a typed failure, never an
    exception crossing the port and never ``None``.
    """

    @property
    def routing(self) -> ScannerRouting:
        """When this scanner must be consulted."""
        ...

    async def scan(
        self,
        *,
        content: str,
        destination: OutboundDestination,
    ) -> ScanResult:
        """Every finding, or the typed reason there is no answer."""
        ...


@runtime_checkable
class OutboundContentGate(Protocol):
    """Assigns an explicit, observable verdict to every outbound payload."""

    async def gate(
        self,
        *,
        content: str,
        visibility: RepoVisibility,
        shape: WriterShape,
        destination: OutboundDestination,
        content_class: ContentClass,
    ) -> GateDecision:
        """CLEAN / REDACTED / BLOCKED — never silently dropped or posted.

        ``content_class`` is declared by the caller and has no default: the
        writer is the only party that knows where its bytes came from, and a
        default would let a payload take the cheap path without anyone
        saying so.
        """
        ...
