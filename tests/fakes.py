"""Fake adapters — real protocol implementations with simplified behavior."""

from collections.abc import AsyncGenerator, Mapping
from pathlib import Path

from kodezart.core.protocols import AgentExecutor
from kodezart.domain.errors import WorkspaceError
from kodezart.types.domain.agent import (
    AcceptanceCriteriaOutput,
    AgentEvent,
    AssistantTextEvent,
    CriterionResult,
    FileChange,
    ResultEvent,
    TicketDraftOutput,
    WorkflowIterationEvent,
    WorkflowTicketEvent,
)
from kodezart.types.domain.persist import PersistResult


class FakeGitService:
    """Stub GitService for unit testing adapters."""

    def __init__(
        self,
        has_changes_result: bool = False,
        remote_branches: list[str] | None = None,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.has_changes_result: bool = has_changes_result
        self._remote_branches: list[str] = remote_branches or []

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

    async def merge_branch(self, cwd: str, source_branch: str) -> None:
        self.calls.append(("merge_branch", cwd, source_branch))

    async def current_sha(self, cwd: str) -> str:
        self.calls.append(("current_sha", cwd))
        return "a" * 40

    async def delete_remote_branch(
        self,
        cwd: str,
        remote: str,
        branch: str,
    ) -> None:
        self.calls.append(("delete_remote_branch", cwd, remote, branch))

    async def list_remote_branches(
        self,
        cwd: str,
        remote: str,
        prefix: str,
    ) -> list[str]:
        self.calls.append(("list_remote_branches", cwd, remote, prefix))
        return [b for b in self._remote_branches if b.startswith(prefix)]


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
                    "criteria": ["Tests pass", "No lint errors"],
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
                            "criterion": "Tests pass",
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
        ref: str = "HEAD",
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
    ) -> PersistResult | None:
        self.calls.append({"workspace_path": workspace_path, "branch": branch})
        return self._result


class FakeBranchMerger:
    def __init__(
        self,
        *,
        merge_sha: str = "m" * 40,
        fail: Exception | None = None,
        fail_cleanup: Exception | None = None,
    ) -> None:
        self._merge_sha = merge_sha
        self._fail = fail
        self._fail_cleanup = fail_cleanup
        self.calls: list[dict[str, str | None]] = []

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
        self.calls.append(
            {
                "repo_path": repo_path,
                "repo_url": repo_url,
                "base_branch": base_branch,
                "feature_branch": feature_branch,
                "source_branch": source_branch,
            }
        )
        if self._fail is not None:
            raise self._fail
        return self._merge_sha

    async def cleanup_source(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        source_branch: str,
        cache_key: str | None = None,
    ) -> None:
        self.calls.append(
            {
                "method": "cleanup_source",
                "source_branch": source_branch,
            }
        )

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
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
        cache_key: str | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append({"method": "stream", "prompt": prompt})
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
        create_branch: bool = True,
        cache_key: str | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append({"method": "stream_workflow", "prompt": prompt})
        for event in self._events:
            yield event

    async def stream_in_workspace(
        self,
        *,
        prompt: str,
        workspace_path: str,
        permission_mode: str,
        allowed_tools: list[str],
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
    - Schema with "criteriaResults" property → pops from eval_results (each entry
      should be shaped like {"criteriaResults": [{"criterion": ..., "passed": ...,
      "reasoning": ...}, ...]}).
    """

    def __init__(
        self,
        eval_results: list[dict[str, object]],
    ) -> None:
        self._eval_results = list(eval_results)
        self.calls: list[dict[str, object]] = []

    async def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: str,
        allowed_tools: list[str],
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
                                "The fix compiles without errors",
                                "All existing tests pass",
                                "Linting passes with no new warnings",
                            ],
                            "reasoning": "Generated from codebase analysis.",
                        },
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


def make_passing_evaluation(
    criterion: str = "Tests pass",
    reasoning: str = "Fake passing evaluation.",
) -> AcceptanceCriteriaOutput:
    """Construct an AcceptanceCriteriaOutput where the criterion passes."""
    return AcceptanceCriteriaOutput(
        criteria_results=[
            CriterionResult(criterion=criterion, passed=True, reasoning=reasoning),
        ],
    )


def make_failing_evaluation(
    criterion: str = "Tests pass",
    reasoning: str = "Fake failing evaluation.",
) -> AcceptanceCriteriaOutput:
    """Construct an AcceptanceCriteriaOutput where the criterion fails."""
    return AcceptanceCriteriaOutput(
        criteria_results=[
            CriterionResult(criterion=criterion, passed=False, reasoning=reasoning),
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
    ) -> None:
        self._events = events
        self._evaluation = evaluation
        self._total_iterations = total_iterations
        self._last_commit_sha = last_commit_sha
        self.calls: list[dict[str, object]] = []

    async def run(
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
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append(
            {
                "prompt": prompt,
                "repo_path": repo_path,
                "repo_url": repo_url,
                "feature_branch": feature_branch,
                "ralph_branch": ralph_branch,
                "base_branch": base_branch,
                "permission_mode": permission_mode,
                "allowed_tools": allowed_tools,
                "acceptance_criteria": acceptance_criteria,
                "cache_key": cache_key,
            }
        )
        for event in self._events:
            yield event
        yield WorkflowIterationEvent(
            iteration=self._total_iterations,
            branch=ralph_branch,
            commit_sha=self._last_commit_sha,
            accepted=all(r.passed for r in self._evaluation.criteria_results),
            evaluation=self._evaluation,
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
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append(
            {
                "prompt": prompt,
                "repo_path": repo_path,
                "repo_url": repo_url,
                "cache_key": cache_key,
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

    async def clean(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        branch: str,
        cache_key: str | None = None,
    ) -> None:
        self.clean_calls.append((repo_path, repo_url, branch))
