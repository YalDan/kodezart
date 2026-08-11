"""Fake adapters — real protocol implementations with simplified behavior."""

import asyncio
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI

from kodezart.adapters.asyncio_job_queue import AsyncioJobQueue
from kodezart.adapters.claude_agent_executor import ClaudeAgentExecutor
from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
from kodezart.core.errors import TrackerEnsureConflictError
from kodezart.core.prompt_rendering import PromptTemplate
from kodezart.core.protocols import AgentExecutor, PromptProvider, WorkflowEngine
from kodezart.domain.accept_gate import accept_verdict
from kodezart.domain.criteria import mint_criteria
from kodezart.domain.errors import (
    DuplicateWorkRefError,
    MergeConflictError,
    TransientAPIError,
    WorkspaceError,
)
from kodezart.domain.trajectory import fold_trajectory
from kodezart.types.domain.agent import (
    AcceptanceCriteriaOutput,
    AgentEvent,
    AssistantTextEvent,
    CriterionResult,
    FileChange,
    ResultEvent,
    TicketDraftOutput,
    WorkflowIterationEvent,
    WorkflowRemediationEvent,
    WorkflowTicketEvent,
)
from kodezart.types.domain.branch import BaseSpec, WorkRef, WorkRefRole
from kodezart.types.domain.consolidation import (
    ChangesetDigest,
    ConsolidationOutcome,
    ConsolidationStatus,
)
from kodezart.types.domain.criteria import (
    CriterionClass,
    CriterionFeasibility,
    CriterionVerdict,
    DraftedCriterion,
    GeneratedCriterion,
    ValidatedCriterion,
)
from kodezart.types.domain.gating import (
    JUDGMENT_ROUTING,
    ContentClass,
    GateDecision,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
    ScanFailureKind,
    ScanHit,
    ScannerRouting,
    ScanResult,
    WriterShape,
)
from kodezart.types.domain.job import JobRecord, JobState
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.persist import ArtifactPersistStatus, PersistResult
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import KnowledgeGrant, SessionType
from kodezart.types.domain.skills import SettingSource, SkillsMode, SkillsSelection
from kodezart.types.domain.tracker import (
    INSTATABLE_MAPPING_KINDS,
    ClaimResult,
    ClaimStatus,
    EnsureAction,
    IssuePriority,
    IssueQuery,
    IssueRelation,
    IssueRelationKind,
    MappingKind,
    MappingOutcome,
    MappingRef,
    StateTransition,
    TrackerAsset,
    TrackerComment,
    TrackerIssue,
    WorkflowStateKind,
)
from kodezart.types.domain.trajectory import IterationRecord, LoopTrajectory
from kodezart.types.domain.workflow import RemediationRequest
from kodezart.types.requests.agent import WorkflowRequest

SUPPRESS_ALL_SKILLS: SkillsSelection = SkillsSelection(mode=SkillsMode.NONE)
#: The kind a fake session reports when a test does not care which kind it
#: is.  Deliberately NOT the kind the shipped grant names, so a test that
#: means "granted" has to say so.
FAKE_SESSION_TYPE: SessionType = SessionType.API_QUERY
#: A knowledge server declared HERE, in the fixtures, never dialled.  Every
#: assertion about which servers a session is configured with is therefore
#: answered offline: what is under test is this codebase's own grant wiring,
#: not what a vendor's server offers.
FIXTURE_KNOWLEDGE_SERVER: str = "fixture-knowledge"
_FIXTURE_KNOWLEDGE_CREDENTIAL: str = "ntn_" + ("K" * 44)
#: A stand-in for the rendered what-lives-where map.  Deliberately not the
#: shipped fragment: a test asserting the map reached a prompt must fail for
#: a reason other than "some prose happens to match".
FIXTURE_KNOWLEDGE_MAP: str = "── FIXTURE MAP ── where the fixture things live"


def knowledge_grant_for(
    *granted: SessionType,
    knowledge_map: str = FIXTURE_KNOWLEDGE_MAP,
) -> KnowledgeGrant:
    """The fixture knowledge server, granted to *granted* and nothing else.

    The map rides with the grant exactly as the model requires: a grant
    naming no session type carries none, because nothing would render it.
    """
    return KnowledgeGrant(
        granted=granted,
        server_name=FIXTURE_KNOWLEDGE_SERVER,
        server_url="https://knowledge.invalid/mcp",
        auth_header="Authorization",
        auth_scheme="Bearer",
        credential=_FIXTURE_KNOWLEDGE_CREDENTIAL,
        knowledge_map=knowledge_map if granted else "",
    )


#: The shipped shape: no session type is granted, so no session is configured
#: with a knowledge server at all.
NO_KNOWLEDGE_GRANT: KnowledgeGrant = knowledge_grant_for()
FIXTURE_EPOCH: datetime = datetime(2026, 1, 1, tzinfo=UTC)
DEFAULT_SETTING_SOURCES: list[SettingSource] = [
    SettingSource.USER,
    SettingSource.PROJECT,
    SettingSource.LOCAL,
]

#: Both adapters implementing the executor protocol, including the one the
#: default composition root does not wire.  Absence from the composition root
#: is never absence from a guarantee, so every executor-level assertion runs
#: over this list rather than over the default.
EXECUTOR_MODULES: list[str] = [
    "kodezart.adapters.claude_client_executor",
    "kodezart.adapters.claude_agent_executor",
]


def executor_for(module: str, grant: KnowledgeGrant = NO_KNOWLEDGE_GRANT):
    """Build the adapter that lives in *module* with configured setting sources."""
    if module.endswith("claude_client_executor"):
        return ClaudeClientExecutor(
            setting_sources=DEFAULT_SETTING_SOURCES,
            knowledge_grant=grant,
        )
    return ClaudeAgentExecutor(
        setting_sources=DEFAULT_SETTING_SOURCES,
        knowledge_grant=grant,
    )


@dataclass(frozen=True)
class RecordedSession:
    """Everything an executor handed the SDK for one session.

    The prompt is recorded beside the options because they are two
    consequences of one decision, and an assertion that can only see the
    options cannot tell whether the other consequence agreed with it.
    """

    options: object
    prompt: str


async def _no_messages() -> AsyncGenerator[object, None]:
    """A transport that accepts a session and returns nothing from it."""
    for message in ():
        yield message


def _recording_client(recorded: list[RecordedSession]) -> Callable[..., object]:
    """Stand-in for the persistent SDK client that records and yields nothing."""

    class _Client:
        def __init__(self, *, options: object) -> None:
            self._options = options

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def query(self, prompt: str) -> None:
            recorded.append(RecordedSession(options=self._options, prompt=prompt))

        def receive_response(self) -> AsyncGenerator[object, None]:
            return _no_messages()

    return _Client


def _recording_query(recorded: list[RecordedSession]) -> Callable[..., object]:
    """Stand-in for the one-shot SDK entry point, same recording contract."""

    def query(*, prompt: str, options: object) -> AsyncGenerator[object, None]:
        recorded.append(RecordedSession(options=options, prompt=prompt))
        return _no_messages()

    return query


async def recorded_session(
    module: str,
    *,
    grant: KnowledgeGrant = NO_KNOWLEDGE_GRANT,
    session_type: SessionType = FAKE_SESSION_TYPE,
    prompt: str = "p",
    cwd: str = "/tmp/fake",
    skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
) -> RecordedSession:
    """Run one session through *module*'s adapter against a recording transport."""
    recorded: list[RecordedSession] = []
    target = "ClaudeSDKClient" if module.endswith("claude_client_executor") else "query"
    replacement = (
        _recording_client(recorded)
        if target == "ClaudeSDKClient"
        else _recording_query(recorded)
    )
    executor = executor_for(module, grant)

    with patch(f"{module}.{target}", replacement):
        async for _event in executor.stream(
            prompt=prompt,
            cwd=cwd,
            permission_mode="plan",
            allowed_tools=[],
            skills=skills,
            session_type=session_type,
        ):
            pass

    assert len(recorded) == 1
    return recorded[0]


class FakeGitService:
    """Stub GitService for unit testing adapters."""

    def __init__(
        self,
        has_changes_result: bool = False,
        remote_branches: list[str] | None = None,
        *,
        is_path_ignored_result: bool = False,
        remote_branch_shas: dict[str, str | None] | None = None,
        remote_branch_sha_sequences: dict[str, list[str | None]] | None = None,
        delete_remote_branch_error: Exception | None = None,
        ancestor_pairs: set[tuple[str, str]] | None = None,
        diff_digests: dict[tuple[str, str], ChangesetDigest] | None = None,
        trees: dict[str, str] | None = None,
        commit_tree_result: str = "c" * 40,
        push_error: Exception | None = None,
        merge_conflicts: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._merge_conflicts: dict[str, tuple[str, ...]] = dict(merge_conflicts or {})
        self.has_changes_result: bool = has_changes_result
        self._is_path_ignored_result: bool = is_path_ignored_result
        self._remote_branches: list[str] = remote_branches or []
        self._remote_branch_shas: dict[str, str | None] = (
            dict(remote_branch_shas) if remote_branch_shas is not None else {}
        )
        self._remote_branch_sha_sequences: dict[str, list[str | None]] = (
            {branch: list(shas) for branch, shas in remote_branch_sha_sequences.items()}
            if remote_branch_sha_sequences is not None
            else {}
        )
        self._delete_remote_branch_error: Exception | None = delete_remote_branch_error
        self._ancestor_pairs: set[tuple[str, str]] = (
            set(ancestor_pairs) if ancestor_pairs is not None else set()
        )
        self._diff_digests: dict[tuple[str, str], ChangesetDigest] = (
            dict(diff_digests) if diff_digests is not None else {}
        )
        self._trees: dict[str, str] = dict(trees) if trees is not None else {}
        self._commit_tree_result: str = commit_tree_result
        self._push_error: Exception | None = push_error

    async def validate_repo(self, repo_path: str) -> None:
        self.calls.append(("validate_repo", repo_path))

    def is_repo(self, path: str) -> bool:
        self.calls.append(("is_repo", path))
        return False

    async def clone_bare(self, url: str, target: str) -> None:
        self.calls.append(("clone_bare", url, target))

    async def fetch(self, repo_path: str) -> None:
        self.calls.append(("fetch", repo_path))

    async def create_worktree(
        self,
        repo_path: str,
        base_ref: str,
        worktree_path: str,
        branch_name: str | None = None,
        create_branch: bool = True,
    ) -> None:
        self.calls.append(("create_worktree", repo_path, base_ref, worktree_path))

    async def remove_worktree(
        self,
        repo_path: str,
        worktree_path: str,
    ) -> None:
        self.calls.append(("remove_worktree", repo_path, worktree_path))

    async def has_changes(self, cwd: str) -> bool:
        self.calls.append(("has_changes", cwd))
        return self.has_changes_result

    async def is_path_ignored(self, cwd: str, path: str) -> bool:
        self.calls.append(("is_path_ignored", cwd, path))
        return self._is_path_ignored_result

    async def add_all(self, cwd: str) -> None:
        self.calls.append(("add_all", cwd))

    async def commit(
        self,
        cwd: str,
        message: str,
        author_name: str,
        author_email: str,
    ) -> str:
        self.calls.append(("commit", cwd, message))
        return "a" * 40

    async def push(self, cwd: str, branch: str) -> None:
        self.calls.append(("push", cwd, branch))
        if self._push_error is not None:
            err, self._push_error = self._push_error, None
            raise err

    async def merge_branch(self, cwd: str, source_branch: str) -> None:
        self.calls.append(("merge_branch", cwd, source_branch))
        paths = self._merge_conflicts.get(source_branch)
        if paths is not None:
            raise MergeConflictError(
                f"merge of {source_branch} could not be completed",
                source_branch=source_branch,
                paths=paths,
            )

    async def current_sha(self, cwd: str) -> str:
        self.calls.append(("current_sha", cwd))
        return "a" * 40

    async def head_commit_message(self, cwd: str) -> str:
        self.calls.append(("head_commit_message", cwd))
        return "fake: HEAD commit message"

    async def delete_remote_branch(
        self,
        cwd: str,
        remote: str,
        branch: str,
    ) -> None:
        self.calls.append(("delete_remote_branch", cwd, remote, branch))
        if self._delete_remote_branch_error is not None:
            err, self._delete_remote_branch_error = (
                self._delete_remote_branch_error,
                None,
            )
            raise err

    async def list_remote_branches(
        self,
        cwd: str,
        remote: str,
        prefix: str,
    ) -> list[str]:
        self.calls.append(("list_remote_branches", cwd, remote, prefix))
        return [b for b in self._remote_branches if b.startswith(prefix)]

    async def is_ancestor(
        self,
        cwd: str,
        ancestor_ref: str,
        descendant_ref: str,
    ) -> bool:
        self.calls.append(("is_ancestor", cwd, ancestor_ref, descendant_ref))
        return (ancestor_ref, descendant_ref) in self._ancestor_pairs

    async def remote_branch_sha(
        self,
        cwd: str,
        remote: str,
        branch: str,
    ) -> str | None:
        self.calls.append(("remote_branch_sha", cwd, remote, branch))
        sequence = self._remote_branch_sha_sequences.get(branch)
        if sequence:
            return sequence.pop(0)
        if branch in self._remote_branch_shas:
            return self._remote_branch_shas[branch]
        # Defaults: treat all branches as present at a deterministic SHA
        # so existing tests are not forced to opt into a remote-shas dict.
        # Tests that need to assert SOURCE_MISSING set the entry to None.
        return "f" * 40

    async def diff_summary(
        self,
        cwd: str,
        base_ref: str,
        head_ref: str,
    ) -> ChangesetDigest:
        self.calls.append(("diff_summary", cwd, base_ref, head_ref))
        if (base_ref, head_ref) in self._diff_digests:
            return self._diff_digests[(base_ref, head_ref)]
        if base_ref == head_ref:
            return ChangesetDigest(
                file_paths=[],
                commit_subjects=[],
                commit_count=0,
            )
        return ChangesetDigest(
            file_paths=["fake.py"],
            commit_subjects=["feat: scripted"],
            commit_count=1,
        )

    async def reset_hard(self, cwd: str, ref: str) -> None:
        self.calls.append(("reset_hard", cwd, ref))

    async def tree_of(self, cwd: str, ref: str) -> str:
        self.calls.append(("tree_of", cwd, ref))
        return self._trees.get(ref, "t" * 40)

    async def commit_tree(
        self,
        cwd: str,
        tree: str,
        parent: str,
        message: str,
        author_name: str,
        author_email: str,
    ) -> str:
        self.calls.append(("commit_tree", cwd, tree, parent, message))
        return self._commit_tree_result


class FakeAgentExecutor:
    def __init__(
        self,
        events: list[AgentEvent],
        branch_slug: str = "test-branch",
    ) -> None:
        self._events = events
        self._branch_slug = branch_slug
        self.calls: list[dict[str, object]] = []

    def _is_branch_name_schema(self, output_format: dict[str, object] | None) -> bool:
        if output_format is None:
            return False
        schema = output_format.get("schema")
        if not isinstance(schema, dict):
            return False
        props = schema.get("properties", {})
        return isinstance(props, dict) and "slug" in props

    def _is_generated_criteria_schema(
        self, output_format: dict[str, object] | None
    ) -> bool:
        if output_format is None:
            return False
        schema = output_format.get("schema")
        if not isinstance(schema, dict):
            return False
        props = schema.get("properties", {})
        return (
            isinstance(props, dict)
            and "criteria" in props
            and "criteriaResults" not in props
        )

    def _is_ticket_draft_schema(self, output_format: dict[str, object] | None) -> bool:
        if output_format is None:
            return False
        schema = output_format.get("schema")
        if not isinstance(schema, dict):
            return False
        props = schema.get("properties", {})
        return (
            isinstance(props, dict) and "title" in props and "requiredChanges" in props
        )

    def _is_ticket_review_schema(self, output_format: dict[str, object] | None) -> bool:
        if output_format is None:
            return False
        schema = output_format.get("schema")
        if not isinstance(schema, dict):
            return False
        props = schema.get("properties", {})
        return (
            isinstance(props, dict)
            and "approved" in props
            and "feedback" in props
            and "suggestions" in props
        )

    def _is_acceptance_criteria_schema(
        self, output_format: dict[str, object] | None
    ) -> bool:
        if output_format is None:
            return False
        schema = output_format.get("schema")
        if not isinstance(schema, dict):
            return False
        props = schema.get("properties", {})
        return isinstance(props, dict) and "criteriaResults" in props

    def _is_criteria_validation_schema(
        self, output_format: dict[str, object] | None
    ) -> bool:
        if output_format is None:
            return False
        schema = output_format.get("schema")
        if not isinstance(schema, dict):
            return False
        props = schema.get("properties", {})
        return isinstance(props, dict) and "findings" in props

    def _is_pr_description_schema(
        self, output_format: dict[str, object] | None
    ) -> bool:
        if output_format is None:
            return False
        schema = output_format.get("schema")
        if not isinstance(schema, dict):
            return False
        props = schema.get("properties", {})
        return isinstance(props, dict) and "title" in props and "description" in props

    async def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: str,
        allowed_tools: list[str],
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_type: SessionType = FAKE_SESSION_TYPE,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append(
            {
                "prompt": prompt,
                "cwd": cwd,
                "output_format": output_format,
                "allowed_tools": allowed_tools,
                "session_id": session_id,
                "permission_mode": permission_mode,
                "skills": skills,
                "session_type": session_type,
            }
        )
        if self._is_branch_name_schema(output_format):
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="fake",
                structured_output={"slug": self._branch_slug},
            )
            return
        if self._is_generated_criteria_schema(output_format):
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="fake",
                structured_output={
                    "criteria": [
                        {"text": "Tests pass", "criterionClass": "hard_gate"},
                        {"text": "No lint errors", "criterionClass": "soft_signal"},
                    ],
                    "reasoning": "Fake criteria.",
                },
            )
            return
        if self._is_ticket_draft_schema(output_format):
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="draft-session",
                structured_output={
                    "title": "Test ticket",
                    "summary": "Test",
                    "context": "Test",
                    "references": [],
                    "requiredChanges": [
                        {
                            "filePath": "test.py",
                            "changeType": "modify",
                            "description": "fix",
                            "rationale": "needed",
                        },
                    ],
                    "outOfScope": [],
                    "openQuestions": [],
                },
            )
            return
        if self._is_ticket_review_schema(output_format):
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="review-session",
                structured_output={
                    "approved": True,
                    "feedback": "Looks good.",
                    "suggestions": [],
                },
            )
            return
        if self._is_criteria_validation_schema(output_format):
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="fake",
                structured_output={
                    "findings": [
                        {
                            "criterionId": "AC-1",
                            "verdict": "feasible",
                            "smallestRepair": "none",
                        },
                        {
                            "criterionId": "AC-2",
                            "verdict": "feasible",
                            "smallestRepair": "none",
                        },
                    ],
                    "contradictions": [],
                },
            )
            return
        if self._is_acceptance_criteria_schema(output_format) and not self._events:
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="fake",
                structured_output={
                    "criteriaResults": [
                        {
                            "criterionId": "AC-1",
                            "criterion": "Tests pass",
                            "passed": True,
                            "reasoning": "Fake passing review.",
                        },
                        {
                            "criterionId": "AC-2",
                            "criterion": "No lint errors",
                            "passed": True,
                            "reasoning": "Fake passing review.",
                        },
                    ],
                },
            )
            return
        if self._is_pr_description_schema(output_format) and not self._events:
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="fake",
                structured_output={
                    "title": "feat: test PR",
                    "description": "Test PR description.",
                },
            )
            return
        for event in self._events:
            yield event


class FakeRaisingExecutor:
    """Executor that raises on stream — simulates transient failure."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: str,
        allowed_tools: list[str],
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_type: SessionType = FAKE_SESSION_TYPE,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        raise self._exc
        yield  # pragma: no cover — makes this an async generator


class FakeRepoCache:
    def __init__(self, repo_path: str = "/tmp/fake-cache") -> None:
        self._repo_path = repo_path
        self.calls: list[dict[str, object]] = []

    async def ensure_available(self, url: str, cache_key: str | None = None) -> str:
        self.calls.append({"url": url, "cache_key": cache_key})
        return self._repo_path


class FakeWorkspaceProvider:
    def __init__(
        self,
        *,
        fail_acquire: str | None = None,
        fail_after: int = 0,
        workspace_path: str = "/tmp/fake-workspace",
    ) -> None:
        self._fail_acquire = fail_acquire
        self._fail_after = fail_after
        self._acquire_count = 0
        self._workspace_path = workspace_path
        self.calls: list[tuple[str, ...]] = []

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
        self.calls.append(("acquire", repo_path or repo_url or "", ref))
        self._acquire_count += 1
        if self._fail_acquire and self._acquire_count > self._fail_after:
            raise WorkspaceError(self._fail_acquire)
        return self._workspace_path

    async def release(self, workspace_path: str) -> None:
        self.calls.append(("release", workspace_path))


class FakeChangePersister:
    def __init__(self, *, result: PersistResult | None = None) -> None:
        self._result = result
        self.calls: list[dict[str, str]] = []

    async def persist(
        self,
        *,
        workspace_path: str,
        branch: str,
        executor: AgentExecutor,
        backup_ref_id_prefix: str,
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_type: SessionType = FAKE_SESSION_TYPE,
        visibility: RepoVisibility = RepoVisibility.UNKNOWN,
    ) -> PersistResult | None:
        self.calls.append(
            {
                "workspace_path": workspace_path,
                "branch": branch,
                "backup_ref_id_prefix": backup_ref_id_prefix,
            }
        )
        return self._result


class FakeBranchMerger:
    """Fake BranchMerger that scripts ConsolidationOutcomes.

    Default queue (empty/absent) yields ``FAST_FORWARDED`` with a
    deterministic 40-char SHA so existing default-construct call sites
    continue to pass.
    """

    def __init__(
        self,
        *,
        merge_sha: str = "m" * 40,
        consolidation_outcomes: list[ConsolidationOutcome] | None = None,
    ) -> None:
        self._merge_sha = merge_sha
        self._outcomes: list[ConsolidationOutcome] = (
            list(consolidation_outcomes) if consolidation_outcomes is not None else []
        )
        self.calls: list[dict[str, object]] = []

    def _next_outcome(self) -> ConsolidationOutcome:
        if self._outcomes:
            return self._outcomes.pop(0)
        return ConsolidationOutcome(
            status=ConsolidationStatus.FAST_FORWARDED,
            feature_tip_sha=self._merge_sha,
        )

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
        outcome = self._next_outcome()
        self.calls.append(
            {
                "method": "consolidate",
                "repo_path": repo_path,
                "repo_url": repo_url,
                "base_branch": base_branch,
                "feature_branch": feature_branch,
                "source_branch": source_branch,
                "cache_key": cache_key,
                "status": outcome.status,
                "feature_tip_sha": outcome.feature_tip_sha,
            }
        )
        return outcome

    async def cleanup_backup_branches(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        prefix: str,
        cache_key: str | None = None,
    ) -> None:
        self.calls.append({"method": "cleanup_backup_branches", "prefix": prefix})


class FakeAgentRunner:
    """Fake AgentRunner for testing callers without constructing AgentService."""

    def __init__(self, events: list[AgentEvent]) -> None:
        self._events = events
        self.calls: list[dict[str, object]] = []

    async def stream(
        self,
        *,
        prompt: str,
        repo_path: str | None = None,
        repo_url: str | None = None,
        branch: str | None = None,
        permission_mode: str,
        allowed_tools: list[str],
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_type: SessionType = FAKE_SESSION_TYPE,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
        cache_key: str | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append(
            {
                "method": "stream",
                "prompt": prompt,
                "skills": skills,
                "session_type": session_type,
            }
        )
        for event in self._events:
            yield event

    async def stream_workflow(
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
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_type: SessionType = FAKE_SESSION_TYPE,
        visibility: RepoVisibility = RepoVisibility.UNKNOWN,
        create_branch: bool = True,
        cache_key: str | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append(
            {
                "method": "stream_workflow",
                "prompt": prompt,
                "skills": skills,
                "visibility": visibility,
                "base_branch": base_branch,
            },
        )
        for event in self._events:
            yield event

    async def stream_in_workspace(
        self,
        *,
        prompt: str,
        workspace_path: str,
        permission_mode: str,
        allowed_tools: list[str],
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_type: SessionType = FAKE_SESSION_TYPE,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append(
            {
                "method": "stream_in_workspace",
                "prompt": prompt,
                "workspace_path": workspace_path,
                "session_id": session_id,
            }
        )
        for event in self._events:
            yield event


class ScriptedFakeExecutor:
    """Purpose-built fake that scripts per-iteration evaluation outputs.

    Behaviour depends on output_format:
    - None → writes scripted_change.txt to cwd, yields text + result.
    - Schema with "title" + "body" → commit message result.
    - Schema with "title" + "requiredChanges" → ticket draft result.
    - Schema with "approved" + "feedback" → ticket review result.
    - Schema with "findings" property → pops from validation_results, or a
      clean all-feasible sweep over AC-1..AC-3 when none were scripted.
    - Schema with "criteriaResults" property → pops from eval_results (each entry
      should be shaped like {"criteriaResults": [{"criterionId": ...,
      "criterion": ..., "passed": ..., "reasoning": ...}, ...]}).
    """

    def __init__(
        self,
        eval_results: list[dict[str, object]],
        validation_results: list[dict[str, object]] | None = None,
    ) -> None:
        self._eval_results = list(eval_results)
        self._validation_results = list(validation_results or [])
        self.calls: list[dict[str, object]] = []

    async def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: str,
        allowed_tools: list[str],
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_type: SessionType = FAKE_SESSION_TYPE,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append(
            {
                "prompt": prompt,
                "cwd": cwd,
                "output_format": output_format,
                "allowed_tools": allowed_tools,
                "session_id": session_id,
                "permission_mode": permission_mode,
                "skills": skills,
                "session_type": session_type,
            }
        )
        if output_format is None:
            Path(cwd).joinpath("scripted_change.txt").write_text(
                "scripted",
            )
            yield AssistantTextEvent(
                text="scripted change",
                model="scripted",
            )
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="scripted",
            )
            return

        schema = output_format.get("schema")
        if isinstance(schema, dict):
            props = schema.get("properties", {})
            if isinstance(props, dict):
                if "slug" in props:
                    yield ResultEvent(
                        subtype="result",
                        duration_ms=1,
                        duration_api_ms=1,
                        is_error=False,
                        num_turns=1,
                        session_id="scripted",
                        structured_output={"slug": "scripted-branch"},
                    )
                    return
                if "title" in props and "description" in props:
                    yield ResultEvent(
                        subtype="result",
                        duration_ms=1,
                        duration_api_ms=1,
                        is_error=False,
                        num_turns=1,
                        session_id="scripted",
                        structured_output={
                            "title": "feat: scripted PR",
                            "description": "Scripted PR body.",
                        },
                    )
                    return
                if "title" in props and "body" in props:
                    yield ResultEvent(
                        subtype="result",
                        duration_ms=1,
                        duration_api_ms=1,
                        is_error=False,
                        num_turns=1,
                        session_id="scripted",
                        structured_output={
                            "title": "feat: scripted change",
                            "body": "E2E test commit.",
                        },
                    )
                    return
                if "title" in props and "requiredChanges" in props:
                    yield ResultEvent(
                        subtype="result",
                        duration_ms=1,
                        duration_api_ms=1,
                        is_error=False,
                        num_turns=1,
                        session_id="scripted",
                        structured_output={
                            "title": "Scripted ticket",
                            "summary": "Scripted summary",
                            "context": "Scripted context",
                            "references": [],
                            "requiredChanges": [
                                {
                                    "filePath": "test.py",
                                    "changeType": "modify",
                                    "description": "scripted change",
                                    "rationale": "scripted rationale",
                                },
                            ],
                            "outOfScope": [],
                            "openQuestions": [],
                        },
                    )
                    return
                if "approved" in props and "feedback" in props:
                    yield ResultEvent(
                        subtype="result",
                        duration_ms=1,
                        duration_api_ms=1,
                        is_error=False,
                        num_turns=1,
                        session_id="scripted",
                        structured_output={
                            "approved": True,
                            "feedback": "Approved.",
                            "suggestions": [],
                        },
                    )
                    return
                if "criteria" in props and "criteriaResults" not in props:
                    yield ResultEvent(
                        subtype="result",
                        duration_ms=1,
                        duration_api_ms=1,
                        is_error=False,
                        num_turns=1,
                        session_id="scripted",
                        structured_output={
                            "criteria": [
                                {
                                    "text": "The fix compiles without errors",
                                    "criterionClass": "hard_gate",
                                },
                                {
                                    "text": "All existing tests pass",
                                    "criterionClass": "hard_gate",
                                },
                                {
                                    "text": ("Linting passes with no new warnings"),
                                    "criterionClass": "soft_signal",
                                },
                            ],
                            "reasoning": "Generated from codebase analysis.",
                        },
                    )
                    return
                if "findings" in props:
                    yield ResultEvent(
                        subtype="result",
                        duration_ms=1,
                        duration_api_ms=1,
                        is_error=False,
                        num_turns=1,
                        session_id="scripted",
                        structured_output=(
                            self._validation_results.pop(0)
                            if self._validation_results
                            else {
                                "findings": [
                                    {
                                        "criterionId": f"AC-{n}",
                                        "verdict": "feasible",
                                        "smallestRepair": "none",
                                    }
                                    for n in (1, 2, 3)
                                ],
                                "contradictions": [],
                            }
                        ),
                    )
                    return
                if "criteriaResults" in props:
                    result = self._eval_results.pop(0)
                    yield ResultEvent(
                        subtype="result",
                        duration_ms=1,
                        duration_api_ms=1,
                        is_error=False,
                        num_turns=1,
                        session_id="scripted",
                        structured_output=result,
                    )
                    return


DEFAULT_CRITERION_ID = "AC-1"


def as_validated(
    criteria: Sequence[GeneratedCriterion],
    *,
    verdict: CriterionVerdict = CriterionVerdict.feasible,
    missing_resource: str | None = None,
) -> list[ValidatedCriterion]:
    """Wrap minted criteria in the post-sweep shape the loop is handed.

    The loop never receives a bare criterion: every dispatched criterion
    carries the verdict the sweep computed and, when the verdict is
    ``unverifiable``, the resource whose absence blocks its demonstration.
    """
    return [
        ValidatedCriterion(
            id=criterion.id,
            text=criterion.text,
            criterion_class=criterion.criterion_class,
            feasibility=CriterionFeasibility(
                criterion_id=criterion.id,
                verdict=verdict,
                missing_resource=missing_resource,
            ),
        )
        for criterion in criteria
    ]


def make_minted_criteria(
    *texts: str,
    criterion_class: CriterionClass = CriterionClass.hard_gate,
) -> list[GeneratedCriterion]:
    """Mint AC-n identities for *texts* the way the generation node does."""
    return list(
        mint_criteria(
            [
                DraftedCriterion(text=text, criterion_class=criterion_class)
                for text in (texts or ("Tests pass",))
            ]
        )
    )


def make_criteria(
    *texts: str,
    criterion_class: CriterionClass = CriterionClass.hard_gate,
) -> list[ValidatedCriterion]:
    """The dispatch shape: minted, then carrying a sweep verdict."""
    return as_validated(make_minted_criteria(*texts, criterion_class=criterion_class))


def make_dispatched_criteria() -> list[ValidatedCriterion]:
    """What the gate is handed once the fake generator's criteria are swept."""
    return as_validated(make_generated_criteria())


def make_generated_criteria() -> list[GeneratedCriterion]:
    """The minted criteria the fake generator emits — the harness's own copy."""
    return list(
        mint_criteria(
            [
                DraftedCriterion(
                    text="Tests pass",
                    criterion_class=CriterionClass.hard_gate,
                ),
                DraftedCriterion(
                    text="No lint errors",
                    criterion_class=CriterionClass.soft_signal,
                ),
            ]
        )
    )


def make_passing_evaluation(
    criterion: str = "Tests pass",
    reasoning: str = "Fake passing evaluation.",
    criterion_id: str = DEFAULT_CRITERION_ID,
) -> AcceptanceCriteriaOutput:
    """Construct an AcceptanceCriteriaOutput where the criterion passes."""
    return AcceptanceCriteriaOutput(
        criteria_results=[
            CriterionResult(
                criterion_id=criterion_id,
                criterion=criterion,
                passed=True,
                reasoning=reasoning,
            ),
        ],
    )


def make_passing_evaluation_over(*criterion_ids: str) -> AcceptanceCriteriaOutput:
    """A pass for every dispatched id — the shape a real evaluator returns.

    The gate grades against the DISPATCHED set, so an evaluation that
    answers fewer ids than were dispatched is a failing run, not a
    passing one.  A fixture that means "everything passed" has to say so
    for every id.
    """
    return AcceptanceCriteriaOutput(
        criteria_results=[
            CriterionResult(
                criterion_id=criterion_id,
                criterion=f"criterion {criterion_id}",
                passed=True,
                reasoning="Fake passing evaluation.",
            )
            for criterion_id in criterion_ids
        ],
    )


def make_failing_evaluation(
    criterion: str = "Tests pass",
    reasoning: str = "Fake failing evaluation.",
    criterion_id: str = DEFAULT_CRITERION_ID,
) -> AcceptanceCriteriaOutput:
    """Construct an AcceptanceCriteriaOutput where the criterion fails."""
    return AcceptanceCriteriaOutput(
        criteria_results=[
            CriterionResult(
                criterion_id=criterion_id,
                criterion=criterion,
                passed=False,
                reasoning=reasoning,
            ),
        ],
    )


class FakeQualityGate:
    """Fake QualityGate for testing the outer workflow pipeline."""

    def __init__(
        self,
        events: list[AgentEvent],
        evaluation: AcceptanceCriteriaOutput,
        total_iterations: int = 1,
        last_commit_sha: str | None = None,
        trajectory: LoopTrajectory | None = None,
    ) -> None:
        self._events = events
        self._evaluation = evaluation
        self._total_iterations = total_iterations
        self._last_commit_sha = last_commit_sha
        self._trajectory = trajectory
        self.calls: list[dict[str, object]] = []

    async def run(
        self,
        *,
        prompt: str,
        repo_path: str | None,
        repo_url: str | None,
        feature_branch: str,
        ralph_branch: str,
        base_spec: BaseSpec,
        permission_mode: str,
        allowed_tools: list[str],
        acceptance_criteria: list[ValidatedCriterion],
        cache_key: str,
        repo_visibility: RepoVisibility = RepoVisibility.UNKNOWN,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append(
            {
                "repo_visibility": repo_visibility,
                "prompt": prompt,
                "repo_path": repo_path,
                "repo_url": repo_url,
                "feature_branch": feature_branch,
                "ralph_branch": ralph_branch,
                "base_spec": base_spec,
                "base_branch": base_spec.base_branch,
                "permission_mode": permission_mode,
                "allowed_tools": allowed_tools,
                "acceptance_criteria": acceptance_criteria,
                "cache_key": cache_key,
            }
        )
        for event in self._events:
            yield event
        results = self._evaluation.criteria_results
        yield WorkflowIterationEvent(
            iteration=self._total_iterations,
            branch=ralph_branch,
            commit_sha=self._last_commit_sha,
            verdict=accept_verdict(acceptance_criteria, results),
            evaluation=self._evaluation,
            trajectory=self._trajectory
            or fold_trajectory(
                [
                    IterationRecord(
                        iteration=self._total_iterations,
                        passed_count=sum(1 for r in results if r.passed),
                        failing_criterion_ids=[
                            r.criterion_id for r in results if not r.passed
                        ],
                        commit_sha=self._last_commit_sha,
                    ),
                ],
                plateau_window=2,
            ),
        )


def make_ticket_draft(
    title: str = "Test ticket",
    summary: str = "Test summary",
    context: str = "Test context",
) -> TicketDraftOutput:
    """Construct a TicketDraftOutput with sensible defaults for tests."""
    return TicketDraftOutput(
        title=title,
        summary=summary,
        context=context,
        references=[],
        required_changes=[
            FileChange(
                file_path="test.py",
                change_type="modify",
                description="test change",
                rationale="test rationale",
            ),
        ],
        out_of_scope=[],
        open_questions=[],
    )


class FakeRefPublisher:
    """Fake RefPublisher recording every published (ref, commit) pair."""

    def __init__(self, *, fail: Exception | None = None) -> None:
        self._fail = fail
        self.calls: list[dict[str, object]] = []

    async def publish(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        commit_sha: str,
        ref: str,
        cache_key: str | None = None,
    ) -> None:
        self.calls.append(
            {
                "repo_path": repo_path,
                "repo_url": repo_url,
                "commit_sha": commit_sha,
                "ref": ref,
                "cache_key": cache_key,
            }
        )
        if self._fail is not None:
            raise self._fail


class FakeRemediator:
    """Fake Remediator yielding one remediation ticket per round."""

    def __init__(self, *, title: str = "Remediate the failure") -> None:
        self._title = title
        self.calls: list[RemediationRequest] = []

    async def run(
        self,
        request: RemediationRequest,
        *,
        repo_path: str | None,
        repo_url: str | None,
        cache_key: str,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append(request)
        yield WorkflowRemediationEvent(
            entry=request.entry,
            round_index=request.round_index,
            ticket=make_ticket_draft(
                title=f"{self._title} ({request.entry.value})",
            ),
            base_ref=request.work_base_ref,
        )


class FakePRCreator:
    """Fake PRCreator for testing the outer workflow pipeline."""

    def __init__(
        self,
        *,
        pr_url: str = "https://github.com/o/r/pull/1",
        pr_number: int = 1,
        fail_create: Exception | None = None,
        fail_comment: Exception | None = None,
    ) -> None:
        self._pr_url = pr_url
        self._pr_number = pr_number
        self._fail_create = fail_create
        self._fail_comment = fail_comment
        self.calls: list[dict[str, object]] = []

    async def create_pr(
        self,
        *,
        repo_url: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> tuple[str, int]:
        self.calls.append(
            {
                "method": "create_pr",
                "repo_url": repo_url,
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            }
        )
        if self._fail_create is not None:
            raise self._fail_create
        return (self._pr_url, self._pr_number)

    async def comment_on_pr(
        self,
        *,
        repo_url: str,
        pr_number: int,
        body: str,
    ) -> None:
        self.calls.append(
            {
                "method": "comment_on_pr",
                "repo_url": repo_url,
                "pr_number": pr_number,
                "body": body,
            }
        )
        if self._fail_comment is not None:
            raise self._fail_comment


class FakeCIMonitor:
    """Fake CIMonitor for testing the outer workflow pipeline."""

    def __init__(
        self,
        *,
        passed: bool | None = True,
        summary: str = "All CI checks passed.",
        fail: Exception | None = None,
    ) -> None:
        self._passed = passed
        self._summary = summary
        self._fail = fail
        self.calls: list[dict[str, object]] = []

    async def wait_for_checks(
        self,
        *,
        repo_url: str,
        ref: str,
    ) -> tuple[bool | None, str]:
        self.calls.append(
            {
                "repo_url": repo_url,
                "ref": ref,
            }
        )
        if self._fail is not None:
            raise self._fail
        return (self._passed, self._summary)


class SequentialCIMonitor:
    """CIMonitor that returns a different result on each call.

    Takes a list of ``(passed, summary)`` tuples and pops the first entry
    on every ``wait_for_checks`` invocation.  Raises ``IndexError`` if
    called more times than results were provided (fail-fast).
    """

    def __init__(self, results: list[tuple[bool | None, str]]) -> None:
        self._results = list(results)
        self.calls: list[dict[str, object]] = []

    async def wait_for_checks(
        self,
        *,
        repo_url: str,
        ref: str,
    ) -> tuple[bool | None, str]:
        self.calls.append({"repo_url": repo_url, "ref": ref})
        return self._results.pop(0)


class FakeTicketGenerator:
    """Fake TicketGenerator for testing the outer workflow pipeline."""

    def __init__(self, ticket: TicketDraftOutput | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._ticket = ticket or make_ticket_draft()

    async def run(
        self,
        *,
        prompt: str,
        repo_path: str | None,
        repo_url: str | None,
        cache_key: str,
        base_branch: str,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append(
            {
                "prompt": prompt,
                "repo_path": repo_path,
                "repo_url": repo_url,
                "cache_key": cache_key,
                "base_branch": base_branch,
            }
        )
        yield WorkflowTicketEvent(
            ticket=self._ticket,
            review_rounds=1,
            approved=True,
        )


class FakeArtifactPersister:
    """Records persist/clean calls for assertion."""

    def __init__(
        self,
        *,
        persist_status: ArtifactPersistStatus = ArtifactPersistStatus.PERSISTED,
    ) -> None:
        self.persist_calls: list[tuple[str | None, str | None, str, str]] = []
        self.clean_calls: list[tuple[str | None, str | None, str]] = []
        self.artifacts: list[Mapping[str, str]] = []
        self._persist_status: ArtifactPersistStatus = persist_status

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
        self.persist_calls.append((repo_path, repo_url, branch, base_branch))
        self.artifacts.append(dict(artifacts))
        return self._persist_status

    async def clean(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        branch: str,
        cache_key: str | None = None,
    ) -> None:
        self.clean_calls.append((repo_path, repo_url, branch))


DEFAULT_PROMPT_SET = "claude-opus"


def make_prompt_provider() -> InRepoPromptRegistry:
    """The real in-repo registry addressed by set name — prompts are data."""
    return InRepoPromptRegistry.load(
        sets_root=default_sets_root(),
        default_set=DEFAULT_PROMPT_SET,
        set_overrides={},
        template_overrides={},
        bindings={},
    )


@dataclass(frozen=True)
class _RecordingTemplate(PromptTemplate):
    """Template that appends every render call to a shared recorder."""

    recorder: list[tuple[PromptKey, dict[str, object]]] = field(default_factory=list)

    def render(self, variables: Mapping[str, object]) -> str:
        self.recorder.append((self.key, dict(variables)))
        return super().render(variables)


class RecordingPromptProvider:
    """PromptProvider that records the key and variables of every render."""

    def __init__(self, inner: PromptProvider) -> None:
        self._inner = inner
        self.renders: list[tuple[PromptKey, dict[str, object]]] = []

    def template_for(self, key: PromptKey) -> PromptTemplate:
        inner = self._inner.template_for(key)
        return _RecordingTemplate(
            key=inner.key,
            source=inner.source,
            body=inner.body,
            bindings=inner.bindings,
            recorder=self.renders,
        )

    def resolution_table(self) -> Mapping[PromptKey, str]:
        return self._inner.resolution_table()

    def declared_skills(self, key: PromptKey) -> Sequence[str]:
        return self._inner.declared_skills(key)

    def variables_for(self, key: PromptKey) -> list[dict[str, object]]:
        """Every recorded variable mapping rendered under *key*."""
        return [variables for recorded, variables in self.renders if recorded is key]


class PassThroughGate:
    """OutboundContentGate that records calls and passes everything CLEAN."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, RepoVisibility, WriterShape]] = []
        self.destinations: list[OutboundDestination] = []
        self.content_classes: list[ContentClass] = []

    async def gate(
        self,
        *,
        content: str,
        visibility: RepoVisibility,
        shape: WriterShape,
        destination: OutboundDestination,
        content_class: ContentClass,
    ) -> GateDecision:
        self.calls.append((content, visibility, shape))
        self.destinations.append(destination)
        self.content_classes.append(content_class)
        return GateDecision(verdict=GateVerdict.CLEAN, content=content)


class FakeVisibilityResolver:
    """RepoVisibilityResolver that scripts one visibility per run."""

    def __init__(
        self,
        visibility: RepoVisibility = RepoVisibility.PUBLIC,
        *,
        fail: Exception | None = None,
    ) -> None:
        self._visibility = visibility
        self._fail = fail
        self.calls: list[str] = []

    async def resolve_visibility(self, *, repo_url: str) -> RepoVisibility:
        self.calls.append(repo_url)
        if self._fail is not None:
            raise self._fail
        return self._visibility


class FakeContentScanner:
    """ContentScanner that reports a scripted result, and counts its calls.

    Scripted rather than intelligent on purpose: what the corpus measures
    under this double is the MECHANISM around a verdict — that a reported
    hit reaches the fold, is applied at its reported span, and resolves to
    the right verdict for its destination.  The model itself is measured by
    the separately-marked live target, never here.
    """

    def __init__(
        self,
        hits: list[ScanHit] | None = None,
        *,
        failure: ScanFailureKind | None = None,
        routing: ScannerRouting | None = None,
        hits_by_destination: dict[OutboundDestination, list[ScanHit]] | None = None,
    ) -> None:
        self._hits = list(hits or [])
        self._failure = failure
        self._hits_by_destination = hits_by_destination
        self._routing = routing or JUDGMENT_ROUTING
        self.calls: list[str] = []
        self.destinations: list[OutboundDestination] = []

    @property
    def routing(self) -> ScannerRouting:
        """The routing this double declares to the gate."""
        return self._routing

    async def scan(
        self,
        *,
        content: str,
        destination: OutboundDestination,
    ) -> ScanResult:
        self.calls.append(content)
        self.destinations.append(destination)
        if self._failure is not None:
            return ScanResult(failure=self._failure)
        if self._hits_by_destination is not None:
            return ScanResult(
                hits=tuple(self._hits_by_destination.get(destination, [])),
            )
        return ScanResult(hits=tuple(self._hits))


@asynccontextmanager
async def attached_job_queue(
    app: FastAPI,
    engine: WorkflowEngine,
    *,
    max_concurrent_runs_per_lane: int = 1,
    max_depth_per_lane: int = 64,
    terminal_retention_seconds: float = 86400.0,
    event_buffer_retention_seconds: float = 900.0,
    event_buffer_capacity: int = 512,
) -> AsyncGenerator[AsyncioJobQueue, None]:
    """Attach a started AsyncioJobQueue to *app*, stopping it on exit.

    The httpx ASGITransport does not run lifespan events, so tests that
    exercise queued endpoints wire the dispatcher the way the lifespan
    does and stop it the same way.
    """
    queue = AsyncioJobQueue(
        engine=engine,
        max_concurrent_runs_per_lane=max_concurrent_runs_per_lane,
        max_depth_per_lane=max_depth_per_lane,
        terminal_retention_seconds=terminal_retention_seconds,
        event_buffer_retention_seconds=event_buffer_retention_seconds,
        event_buffer_capacity=event_buffer_capacity,
    )
    app.state.job_queue = queue
    await queue.start()
    try:
        yield queue
    finally:
        await queue.stop()


# --------------------------------------------------------------------------
# Tracker test doubles
#
# Two levels, deliberately.  ``FakeLinearMcpServer`` is an in-process MCP
# SERVER: it satisfies ``McpToolCaller`` and serves the vendor tool contract,
# so the real Linear adapter runs against it unmodified and the conformance
# suite needs no live workspace.  ``FakeTrackerPort`` satisfies ``TrackerPort``
# directly and is what consumers of the port (the dispatcher, the passes) are
# tested against — a consumer test that had to know the vendor's tool names
# would have a vendor dependency the port exists to remove.
# --------------------------------------------------------------------------


@dataclass
class FakeMcpAsset:
    """One attachment or document reference the fake server serves."""

    id: str
    title: str
    url: str
    content_type: str | None = None
    size: int | None = None

    def wire(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "contentType": self.content_type,
            "size": self.size,
        }


@dataclass
class FakeMcpDocument:
    """One document the fake workspace holds: server id, title, body.

    Three fields rather than an id-to-body mapping, because the ensure
    path addresses a document by TITLE and the read path by id, and a
    registry that carried only one of them would make one of the two
    untestable.
    """

    id: str
    title: str
    content: str

    def summary(self) -> dict[str, object]:
        return {"id": self.id, "title": self.title}


@dataclass
class FakeMcpIssue:
    """One issue in the fake workspace, in the vendor's own shape."""

    id: str
    title: str = "fixture issue"
    description: str = ""
    priority_raw: int = 0
    status: str = "Backlog"
    status_type: str = "backlog"
    labels: list[str] = field(default_factory=list)
    relations: list[tuple[str, str]] = field(default_factory=list)
    attachments: list[FakeMcpAsset] = field(default_factory=list)
    documents: list[FakeMcpAsset] = field(default_factory=list)
    parent_id: str | None = None
    assignee: str | None = None
    created_at: datetime = FIXTURE_EPOCH
    updated_at: datetime = FIXTURE_EPOCH
    url: str = ""

    def wire(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "priority": {"value": self.priority_raw},
            "status": self.status,
            "statusType": self.status_type,
            "labels": list(self.labels),
            "relations": [
                {"type": kind, "identifier": key} for kind, key in self.relations
            ],
            "attachments": [asset.wire() for asset in self.attachments],
            "documents": [asset.wire() for asset in self.documents],
            "parentId": self.parent_id,
            "assignee": self.assignee,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
            "url": self.url or f"https://tracker.invalid/issue/{self.id}",
        }


@dataclass
class FakeMcpComment:
    """One comment in the fake workspace's append-only log."""

    id: str
    issue_id: str
    user: str
    body: str
    created_at: datetime

    def wire(self) -> dict[str, object]:
        return {
            "id": self.id,
            "issueId": self.issue_id,
            "user": self.user,
            "body": self.body,
            "createdAt": self.created_at.isoformat(),
        }


@dataclass
class FakeMcpHistoryEntry:
    """One label-change history entry — the provenance record."""

    actor: str
    created_at: datetime
    added_labels: list[str] = field(default_factory=list)
    removed_labels: list[str] = field(default_factory=list)

    def wire(self) -> dict[str, object]:
        return {
            "actor": self.actor,
            "addedLabels": list(self.added_labels),
            "removedLabels": list(self.removed_labels),
            "createdAt": self.created_at.isoformat(),
        }


class FakeLinearMcpServer:
    """In-process MCP server satisfying ``McpToolCaller``.

    Serves the exact tool contract ``LinearMcpTracker`` speaks.  Comment
    creation is append-only with server-assigned identifiers and timestamps,
    which is what the adapter's atomic claim is built on: ``comment_instants``
    can be set to a repeated value to force the same-instant tie-break path.
    """

    def __init__(
        self,
        *,
        issues: Sequence[FakeMcpIssue] = (),
        documents: Sequence[FakeMcpDocument] = (),
        history: Mapping[str, Sequence[FakeMcpHistoryEntry]] | None = None,
        users: Sequence[str] = (),
        teams: Sequence[str] = (),
        labels: Sequence[str] = (),
        label_containers: Mapping[str, str] | None = None,
        statuses: Sequence[str] = (),
        state_types: Mapping[str, str] | None = None,
        actor: str = "fixture-actor",
        comment_instants: Sequence[datetime] = (),
        transient_failures: Mapping[str, int] | None = None,
    ) -> None:
        self.issues: dict[str, FakeMcpIssue] = {issue.id: issue for issue in issues}
        self.comments: list[FakeMcpComment] = []
        self.documents: dict[str, FakeMcpDocument] = {
            document.id: document for document in documents
        }
        self.history: dict[str, list[FakeMcpHistoryEntry]] = {
            key: list(entries) for key, entries in (history or {}).items()
        }
        self.users: list[str] = list(users)
        self.teams: list[str] = list(teams)
        self.labels: list[str] = list(labels)
        self.label_containers: dict[str, str] = dict(label_containers or {})
        self.statuses: list[str] = list(statuses)
        self.state_types: dict[str, str] = dict(state_types or {})
        self.actor: str = actor
        self.calls: list[tuple[str, Mapping[str, object]]] = []
        self.comment_instants: list[datetime] = list(comment_instants)
        self._transient_failures: dict[str, int] = dict(transient_failures or {})
        self._sequence: int = 0

    async def call_tool(
        self,
        *,
        name: str,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        # Yield to the scheduler at every tool boundary so concurrent callers
        # genuinely interleave: a claim race that never interleaves proves
        # nothing about exactly-once semantics.
        await asyncio.sleep(0)
        self.calls.append((name, dict(arguments)))
        remaining = self._transient_failures.get(name, 0)
        if remaining > 0:
            self._transient_failures[name] = remaining - 1
            raise TransientAPIError(f"fake transient failure on {name}")
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            msg = f"fake MCP server exposes no tool named {name!r}"
            raise LookupError(msg)
        result: Mapping[str, object] = handler(arguments)
        return result

    def tool_calls(self, name: str) -> list[Mapping[str, object]]:
        """Every argument mapping the named tool was invoked with."""
        return [args for tool, args in self.calls if tool == name]

    def _next_instant(self) -> datetime:
        if self.comment_instants:
            return self.comment_instants[
                min(self._sequence, len(self.comment_instants) - 1)
            ]
        return FIXTURE_EPOCH + timedelta(seconds=self._sequence)

    def _issue(self, arguments: Mapping[str, object], key: str) -> FakeMcpIssue:
        issue_key = str(arguments[key])
        issue = self.issues.get(issue_key)
        if issue is None:
            msg = f"fake workspace has no issue {issue_key!r}"
            raise LookupError(msg)
        return issue

    def _tool_list_issues(
        self,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        label = arguments.get("label")
        selected = [
            issue
            for issue in self.issues.values()
            if label is None or label in issue.labels
        ]
        limit = int(str(arguments.get("limit", len(selected))))
        return {"issues": [issue.wire() for issue in selected[:limit]]}

    def _tool_get_issue(
        self,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        return self._issue(arguments, "id").wire()

    def _tool_save_issue(
        self,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        if "id" not in arguments:
            self._sequence += 1
            created = FakeMcpIssue(
                id=f"NEW-{self._sequence}",
                title=str(arguments.get("title", "")),
                description=str(arguments.get("description", "")),
                priority_raw=int(str(arguments.get("priority", 0))),
            )
            self.issues[created.id] = created
            return created.wire()
        issue = self._issue(arguments, "id")
        if "title" in arguments:
            issue.title = str(arguments["title"])
        if "description" in arguments:
            issue.description = str(arguments["description"])
        if "state" in arguments:
            issue.status = str(arguments["state"])
            issue.status_type = self.state_types[issue.status]
        if "labels" in arguments:
            raw_labels = arguments["labels"]
            assert isinstance(raw_labels, list)
            new_labels = [str(entry) for entry in raw_labels]
            self.history.setdefault(issue.id, []).append(
                FakeMcpHistoryEntry(
                    actor=self.actor,
                    created_at=self._next_instant(),
                    added_labels=[
                        label for label in new_labels if label not in issue.labels
                    ],
                    removed_labels=[
                        label for label in issue.labels if label not in new_labels
                    ],
                )
            )
            issue.labels = new_labels
        return issue.wire()

    def _tool_save_comment(
        self,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        created_at = self._next_instant()
        self._sequence += 1
        comment = FakeMcpComment(
            id=f"comment-{self._sequence:04d}",
            issue_id=str(arguments["issueId"]),
            user=self.actor,
            body=str(arguments["body"]),
            created_at=created_at,
        )
        self.comments.append(comment)
        return comment.wire()

    def _tool_list_comments(
        self,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        issue_id = str(arguments["issueId"])
        return {
            "comments": [
                comment.wire()
                for comment in self.comments
                if comment.issue_id == issue_id
            ]
        }

    def _tool_delete_comment(
        self,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        comment_id = str(arguments["id"])
        self.comments = [c for c in self.comments if c.id != comment_id]
        return {}

    def _tool_list_issue_history(
        self,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        entries = self.history.get(str(arguments["id"]), [])
        return {"history": [entry.wire() for entry in entries]}

    def _tool_get_document(
        self,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        document_id = str(arguments["id"])
        document = self.documents[document_id]
        return {"id": document.id, "content": document.content}

    def _tool_list_documents(
        self,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        return {
            "documents": [document.summary() for document in self.documents.values()],
        }

    def _tool_save_document(
        self,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Create a document under the given title, with a server id.

        Creation only, because that is the whole of what the adapter asks
        for: a call naming an existing id would be an update, and an
        ensure that updated a document would be the rename the refusal
        exists to prevent.
        """
        title = str(arguments["title"])
        self._sequence += 1
        document = FakeMcpDocument(
            id=f"fake-document-{self._sequence:04d}",
            title=title,
            content="",
        )
        self.documents[document.id] = document
        return document.summary()

    def _named(self, names: Sequence[str]) -> Mapping[str, object]:
        return {"entries": [{"name": name} for name in names]}

    def _tool_list_users(
        self,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        return self._named(self.users)

    def _tool_list_teams(
        self,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        return self._named(self.teams)

    def _tool_list_issue_labels(
        self,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Labels with the container the workspace holds each one in.

        A label seeded into the fixture reports NO container, exactly as a
        listing that does not carry the field would: the fake states what
        the workspace knows and never invents workspace scope for a label
        nobody said anything about.
        """
        return {
            "entries": [
                {"name": name}
                if self.label_containers.get(name) is None
                else {"name": name, "teamId": self.label_containers[name]}
                for name in self.labels
            ],
        }

    def _tool_create_issue_label(
        self,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        name = str(arguments["name"])
        if name in self.labels:
            msg = f"fake workspace already carries the label {name!r}"
            raise LookupError(msg)
        self.labels.append(name)
        team = arguments.get("teamId")
        if team is not None:
            self.label_containers[name] = str(team)
        return {"name": name}

    def _tool_list_issue_statuses(
        self,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        return self._named(self.statuses)


class ManagedFakeLinearMcpServer(FakeLinearMcpServer):
    """The fake MCP server plus the session lifetime the composition root drives.

    Satisfies ``ManagedMcpToolCaller``, so the lifespan opens and closes it
    exactly as it opens and closes the real HTTP transport, and the real
    adapter runs above it.  Counts rather than flags: an unbalanced
    open/close is visible.
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.opens: int = 0
        self.closes: int = 0

    async def open(self) -> None:
        await asyncio.sleep(0)
        self.opens += 1

    async def close(self) -> None:
        await asyncio.sleep(0)
        self.closes += 1


#: What a lifecycle stage means as a workflow-state KIND.  The fake owns the
#: mapping because the conformance suite asks the port what the write did,
#: and a double that recorded the write without applying it would answer for
#: a state the issue is not in.
_STAGE_KIND: Mapping[LifecycleStage, WorkflowStateKind] = {
    LifecycleStage.IN_PROGRESS: WorkflowStateKind.STARTED,
    LifecycleStage.IN_REVIEW: WorkflowStateKind.STARTED,
    LifecycleStage.DONE: WorkflowStateKind.COMPLETED,
}


class FakeTrackerPort:
    """In-process ``TrackerPort`` — the double every port CONSUMER is tested on.

    Holds domain objects directly, so a consumer test states its fixture in
    the same vocabulary the consumer reads.  The claim is genuinely
    first-writer-wins: concurrent claimants on one issue produce exactly one
    ``GRANTED``.
    """

    def __init__(
        self,
        *,
        issues: Sequence[TrackerIssue] = (),
        provenance: Mapping[tuple[str, QueueState], StateTransition] | None = None,
        assets: Mapping[str, Sequence[TrackerAsset]] | None = None,
        documents: Mapping[str, str] | None = None,
        document_titles: Mapping[str, str] | None = None,
        known_identifiers: Sequence[str] = (),
        recorded_work_refs: Mapping[str, Sequence[WorkRef]] | None = None,
        recorded_base_specs: Mapping[str, BaseSpec] | None = None,
        clock: Callable[[], datetime] = lambda: FIXTURE_EPOCH,
    ) -> None:
        self.issues: dict[str, TrackerIssue] = {
            issue.issue_key: issue for issue in issues
        }
        self.recorded_work_refs: dict[str, list[WorkRef]] = {
            key: list(value) for key, value in (recorded_work_refs or {}).items()
        }
        self.recorded_base_specs: dict[str, BaseSpec] = dict(recorded_base_specs or {})
        self.claims: dict[str, ClaimResult] = {}
        self.comments: list[TrackerComment] = []
        self.workflow_writes: list[tuple[str, LifecycleStage]] = []
        self.queue_writes: list[tuple[str, QueueState]] = []
        self.scans: list[IssueQuery] = []
        self._provenance: dict[tuple[str, QueueState], StateTransition] = dict(
            provenance or {}
        )
        self._assets: dict[str, tuple[TrackerAsset, ...]] = {
            key: tuple(value) for key, value in (assets or {}).items()
        }
        self._documents: dict[str, str] = dict(documents or {})
        #: Title per document id, for the ensure path.  Separate from the
        #: body registry above because a consumer reads a document by key
        #: and an ensure addresses it by title; a fixture that seeded only
        #: one of them would leave the other untestable.
        self.document_titles: dict[str, str] = dict(document_titles or {})
        self.known_identifiers: set[str] = set(known_identifiers)
        #: The container each INSTATED value was created in.  Seeded empty,
        #: because a value the fixture merely knows about reports no
        #: container — the same state a listing that omits the field is in.
        self.mapping_containers: dict[str, str | None] = {}
        self._clock: Callable[[], datetime] = clock
        self._sequence: int = 0

    async def scan_issues(self, *, query: IssueQuery) -> Sequence[TrackerIssue]:
        await asyncio.sleep(0)
        self.scans.append(query)
        matched = [
            issue
            for issue in self.issues.values()
            if (query.queue_state is None or query.queue_state in issue.queue_states)
            and (query.updated_since is None or issue.updated_at > query.updated_since)
        ]
        return tuple(matched[: query.page_size])

    async def read_issue(self, *, issue_key: str) -> TrackerIssue:
        await asyncio.sleep(0)
        return self.issues[issue_key]

    async def create_issue(
        self,
        *,
        title: str,
        body: str,
        team_key: str,
        priority: IssuePriority,
    ) -> TrackerIssue:
        self._sequence += 1
        issue = TrackerIssue(
            issue_key=f"FAKE-{self._sequence}",
            title=title,
            body=body,
            priority=priority,
            state_name="Backlog",
            state_kind=WorkflowStateKind.BACKLOG,
            queue_states=frozenset(),
            created_at=self._clock(),
            updated_at=self._clock(),
            url=f"https://tracker.invalid/issue/FAKE-{self._sequence}",
        )
        self.issues[issue.issue_key] = issue
        return issue

    async def update_issue(
        self,
        *,
        issue_key: str,
        title: str | None = None,
        body: str | None = None,
    ) -> TrackerIssue:
        issue = self.issues[issue_key]
        updated = issue.model_copy(
            update={
                "title": issue.title if title is None else title,
                "body": issue.body if body is None else body,
            }
        )
        self.issues[issue_key] = updated
        return updated

    async def set_workflow_state(
        self,
        *,
        issue_key: str,
        stage: LifecycleStage,
    ) -> TrackerIssue:
        self.workflow_writes.append((issue_key, stage))
        issue = self.issues[issue_key]
        updated = issue.model_copy(
            update={
                "state_name": stage.value,
                "state_kind": _STAGE_KIND[stage],
            },
        )
        self.issues[issue_key] = updated
        return updated

    async def set_queue_state(
        self,
        *,
        issue_key: str,
        state: QueueState,
    ) -> TrackerIssue:
        self.queue_writes.append((issue_key, state))
        issue = self.issues[issue_key]
        updated = issue.model_copy(update={"queue_states": frozenset({state})})
        self.issues[issue_key] = updated
        return updated

    async def post_comment(self, *, issue_key: str, body: str) -> TrackerComment:
        self._sequence += 1
        comment = TrackerComment(
            comment_key=f"comment-{self._sequence:04d}",
            issue_key=issue_key,
            author_key="kodezart",
            body=body,
            created_at=self._clock(),
        )
        self.comments.append(comment)
        return comment

    async def list_comments(self, *, issue_key: str) -> Sequence[TrackerComment]:
        return tuple(c for c in self.comments if c.issue_key == issue_key)

    async def claim_issue(
        self,
        *,
        issue_key: str,
        holder: str,
        lease_seconds: float,
    ) -> ClaimResult:
        # The scheduling point is BEFORE the check-and-set, never inside it:
        # a fake whose claim is not genuinely atomic proves nothing about
        # exactly-once semantics.
        await asyncio.sleep(0)
        expires_at = self._clock() + timedelta(seconds=lease_seconds)
        held = self.claims.get(issue_key)
        if held is not None and held.expires_at > self._clock():
            return ClaimResult(
                issue_key=issue_key,
                status=ClaimStatus.LOST,
                holder=holder,
                expires_at=expires_at,
            )
        granted = ClaimResult(
            issue_key=issue_key,
            status=ClaimStatus.GRANTED,
            holder=holder,
            expires_at=expires_at,
        )
        self.claims[issue_key] = granted
        return granted

    async def release_claim(self, *, issue_key: str, holder: str) -> None:
        held = self.claims.get(issue_key)
        if held is not None and held.holder == holder:
            del self.claims[issue_key]

    async def active_claim(self, *, issue_key: str) -> ClaimResult | None:
        await asyncio.sleep(0)
        held = self.claims.get(issue_key)
        if held is None or held.expires_at <= self._clock():
            return None
        return held

    async def queue_state_provenance(
        self,
        *,
        issue_key: str,
        state: QueueState,
    ) -> StateTransition | None:
        await asyncio.sleep(0)
        return self._provenance.get((issue_key, state))

    async def list_issue_assets(self, *, issue_key: str) -> Sequence[TrackerAsset]:
        return self._assets.get(issue_key, ())

    async def read_document(self, *, document_key: str) -> str:
        return self._documents[document_key]

    async def record_work_ref(self, *, ref: WorkRef) -> None:
        await asyncio.sleep(0)
        held = self.recorded_work_refs.setdefault(ref.issue_id, [])
        for existing in held:
            if existing.identity() == ref.identity():
                return
            if existing.role is WorkRefRole.DELIVERABLE is ref.role:
                raise DuplicateWorkRefError(
                    "an issue carries at most one deliverable ref",
                    issue_id=ref.issue_id,
                    role=ref.role.value,
                    existing_branch=existing.branch,
                    offered_branch=ref.branch,
                )
        held.append(ref)

    async def work_refs(self, *, issue_key: str) -> Sequence[WorkRef]:
        await asyncio.sleep(0)
        return tuple(self.recorded_work_refs.get(issue_key, ()))

    async def record_base_spec(self, *, issue_key: str, spec: BaseSpec) -> None:
        await asyncio.sleep(0)
        self.recorded_base_specs[issue_key] = spec

    async def read_base_spec(self, *, issue_key: str) -> BaseSpec | None:
        await asyncio.sleep(0)
        return self.recorded_base_specs.get(issue_key)

    async def resolve_mappings(
        self,
        *,
        refs: Sequence[MappingRef],
    ) -> Sequence[MappingRef]:
        await asyncio.sleep(0)
        return tuple(
            ref for ref in refs if ref.identifier not in self.known_identifiers
        )

    async def ensure_mappings(
        self,
        *,
        refs: Sequence[MappingRef],
    ) -> Sequence[MappingOutcome]:
        """The port's ensure contract, held to the same rule the adapter is.

        The double refused nothing before this: it accepted every kind and
        created every ref, so a consumer could pass over behaviour the port
        does not have.  R8's rule is the domain's, not a vendor's, so it
        lives here identically — kinds outside ``INSTATABLE_MAPPING_KINDS``
        and reported-container disagreements both raise and write nothing.
        """
        await asyncio.sleep(0)
        outcomes: list[MappingOutcome] = []
        for ref in refs:
            if ref.kind not in INSTATABLE_MAPPING_KINDS:
                raise TrackerEnsureConflictError(
                    "this kind belongs to no field the operation owns",
                    entry=ref.describe(),
                )
            if ref.kind is MappingKind.DOCUMENT:
                outcomes.append(self._ensure_document(ref))
                continue
            identifier = ref.identifier
            if identifier is None:
                raise TrackerEnsureConflictError(
                    "this kind is declared by its own identifier and this ref "
                    "carries none",
                    entry=ref.describe(),
                )
            if identifier in self.known_identifiers:
                container = self.mapping_containers.get(identifier)
                if container is not None and container != ref.scope:
                    raise TrackerEnsureConflictError(
                        "the workspace defines this value in another container; "
                        f"declared {ref.scope!r}, found {container!r}",
                        entry=ref.describe(),
                    )
                outcomes.append(
                    MappingOutcome(
                        ref=ref,
                        action=EnsureAction.ADOPTED,
                        identifier=identifier,
                    ),
                )
                continue
            self.known_identifiers.add(identifier)
            self.mapping_containers[identifier] = ref.scope
            outcomes.append(
                MappingOutcome(
                    ref=ref,
                    action=EnsureAction.CREATED,
                    identifier=identifier,
                ),
            )
        return tuple(outcomes)

    def _ensure_document(self, ref: MappingRef) -> MappingOutcome:
        """The document arm of the ensure contract, held identically here.

        Same three refusals as the adapter, for the same reasons: an id the
        workspace does not hold, an id whose document carries another
        title, and a title two documents share.
        """
        if ref.identifier is not None:
            title = self.document_titles.get(ref.identifier)
            if title is None:
                raise TrackerEnsureConflictError(
                    "the workspace holds no document with this identifier",
                    entry=ref.describe(),
                )
            if title != ref.name:
                raise TrackerEnsureConflictError(
                    "the workspace holds this document under another title; "
                    f"declared {ref.name!r}, found {title!r}",
                    entry=ref.describe(),
                )
            return MappingOutcome(
                ref=ref,
                action=EnsureAction.ADOPTED,
                identifier=ref.identifier,
            )
        held = sorted(
            identifier
            for identifier, title in self.document_titles.items()
            if title == ref.name
        )
        if len(held) > 1:
            raise TrackerEnsureConflictError(
                "the workspace holds several documents under this title",
                entry=ref.describe(),
            )
        if held:
            return MappingOutcome(
                ref=ref,
                action=EnsureAction.ADOPTED,
                identifier=held[0],
            )
        self._sequence += 1
        identifier = f"fake-document-{self._sequence:04d}"
        self.document_titles[identifier] = ref.name
        self._documents[identifier] = ""
        self.known_identifiers.add(identifier)
        return MappingOutcome(
            ref=ref,
            action=EnsureAction.CREATED,
            identifier=identifier,
        )


class FakeDeliveryProbe:
    """``DeliveryProbe`` over a fixed set of issue keys with an open delivery."""

    def __init__(self, *, delivered: Sequence[str] = ()) -> None:
        self.delivered: set[str] = set(delivered)
        self.calls: list[str] = []

    async def open_delivery_exists(self, *, repo_url: str, issue_key: str) -> bool:
        self.calls.append(issue_key)
        return issue_key in self.delivered


def make_tracker_issue(
    issue_key: str,
    *,
    priority: IssuePriority = IssuePriority.NONE,
    state_name: str = "Todo",
    state_kind: WorkflowStateKind = WorkflowStateKind.UNSTARTED,
    queue_states: Sequence[QueueState] = (QueueState.APPROVED,),
    blocked_by: Sequence[str] = (),
    parent_key: str | None = None,
    created_at: datetime = FIXTURE_EPOCH,
    body: str = "fixture body",
) -> TrackerIssue:
    """A domain issue for port-consumer fixtures."""
    return TrackerIssue(
        issue_key=issue_key,
        parent_key=parent_key,
        title=issue_key,
        body=body,
        priority=priority,
        state_name=state_name,
        state_kind=state_kind,
        queue_states=frozenset(queue_states),
        relations=tuple(
            IssueRelation(kind=IssueRelationKind.BLOCKED_BY, issue_key=key)
            for key in blocked_by
        ),
        created_at=created_at,
        updated_at=created_at,
        url=f"https://tracker.invalid/issue/{issue_key}",
    )


def approved_by(
    issue_key: str,
    actor_key: str,
    *,
    occurred_at: datetime = FIXTURE_EPOCH,
) -> tuple[tuple[str, QueueState], StateTransition]:
    """One provenance entry for ``FakeTrackerPort(provenance=dict([...]))``."""
    return (
        (issue_key, QueueState.APPROVED),
        StateTransition(
            issue_key=issue_key,
            queue_state=QueueState.APPROVED,
            actor_key=actor_key,
            occurred_at=occurred_at,
        ),
    )


class FakeJobQueue:
    """``JobQueue`` and ``JobRegistry`` over an in-memory submission list.

    One queue, two producers: this double is what proves the dispatcher
    enqueues onto the same surface HTTP submissions use.
    """

    def __init__(
        self,
        *,
        states: Mapping[str, JobState] | None = None,
        events: Sequence[AgentEvent] = (),
    ) -> None:
        self.submissions: list[tuple[str, WorkflowRequest]] = []
        self.records: dict[str, JobRecord] = {}
        self.attached: list[str] = []
        self._states: dict[str, JobState] = dict(states or {})
        self._events: tuple[AgentEvent, ...] = tuple(events)
        self._sequence: int = 0

    async def submit(self, *, lane: str, request: WorkflowRequest) -> JobRecord:
        await asyncio.sleep(0)
        self._sequence += 1
        job_id = f"job-{self._sequence:04d}"
        record = JobRecord(
            job_id=job_id,
            lane=lane,
            state=self._states.get(job_id, JobState.QUEUED),
            queue_position=len(self.submissions) + 1,
            submitted_at=FIXTURE_EPOCH,
        )
        self.submissions.append((lane, request))
        self.records[job_id] = record
        return record

    def attach(self, *, job_id: str) -> AsyncGenerator[AgentEvent, None]:
        """Replay the scripted run, exactly as the real queue's stream does.

        The frames the real queue publishes begin only once the worker has
        dequeued the job, so a scripted stream that starts empty and then
        yields is the same shape a consumer sees in production.
        """
        self.attached.append(job_id)
        scripted = self._events

        async def _replay() -> AsyncGenerator[AgentEvent, None]:
            for event in scripted:
                await asyncio.sleep(0)
                yield event

        return _replay()

    async def get(self, *, job_id: str) -> JobRecord | None:
        await asyncio.sleep(0)
        return self.records.get(job_id)

    def mark(self, job_id: str, state: JobState) -> None:
        """Move a submitted job to *state*, as the dispatcher would observe."""
        self.records[job_id] = self.records[job_id].model_copy(
            update={"state": state},
        )
