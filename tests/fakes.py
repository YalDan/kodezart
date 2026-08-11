"""Fake adapters — real protocol implementations with simplified behavior."""

from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI

from kodezart.adapters.asyncio_job_queue import AsyncioJobQueue
from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
from kodezart.core.prompt_rendering import PromptTemplate
from kodezart.core.protocols import AgentExecutor, PromptProvider, WorkflowEngine
from kodezart.domain.accept_gate import accept_verdict
from kodezart.domain.criteria import mint_criteria
from kodezart.domain.errors import WorkspaceError
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
from kodezart.types.domain.base_spec import BaseSpec
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
    GateDecision,
    GateVerdict,
    RepoVisibility,
    ScanHit,
    WriterShape,
)
from kodezart.types.domain.persist import PersistResult
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.skills import SettingSource, SkillsMode, SkillsSelection
from kodezart.types.domain.trajectory import IterationRecord, LoopTrajectory
from kodezart.types.domain.workflow import RemediationRequest

SUPPRESS_ALL_SKILLS: SkillsSelection = SkillsSelection(mode=SkillsMode.NONE)
DEFAULT_SETTING_SOURCES: list[SettingSource] = [
    SettingSource.USER,
    SettingSource.PROJECT,
    SettingSource.LOCAL,
]


class FakeGitService:
    """Stub GitService for unit testing adapters."""

    def __init__(
        self,
        has_changes_result: bool = False,
        remote_branches: list[str] | None = None,
        *,
        remote_branch_shas: dict[str, str | None] | None = None,
        remote_branch_sha_sequences: dict[str, list[str | None]] | None = None,
        delete_remote_branch_error: Exception | None = None,
        ancestor_pairs: set[tuple[str, str]] | None = None,
        diff_digests: dict[tuple[str, str], ChangesetDigest] | None = None,
        trees: dict[str, str] | None = None,
        commit_tree_result: str = "c" * 40,
        push_error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.has_changes_result: bool = has_changes_result
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
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
        cache_key: str | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append({"method": "stream", "prompt": prompt, "skills": skills})
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
                "base_branch": base_spec.base_ref,
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

    def __init__(self) -> None:
        self.persist_calls: list[tuple[str | None, str | None, str, str]] = []
        self.clean_calls: list[tuple[str | None, str | None, str]] = []
        self.artifacts: list[Mapping[str, str]] = []

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
        self.persist_calls.append((repo_path, repo_url, branch, base_branch))
        self.artifacts.append(dict(artifacts))

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

    def gate(
        self,
        *,
        content: str,
        visibility: RepoVisibility,
        shape: WriterShape,
    ) -> GateDecision:
        self.calls.append((content, visibility, shape))
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
    """ContentScanner that reports scripted hits, for scanner-ordering tests."""

    def __init__(self, hits: list[ScanHit]) -> None:
        self._hits = hits
        self.calls: list[str] = []

    def scan(self, content: str) -> list[ScanHit]:
        self.calls.append(content)
        return list(self._hits)


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
