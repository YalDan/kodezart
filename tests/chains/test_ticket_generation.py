"""Tests for TicketGenerationLoop (ticket draft + review sub-graph) with fakes."""

from collections.abc import AsyncGenerator

import pytest
from pydantic import ValidationError

from kodezart.chains.ticket_generation import TicketGenerationLoop
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AgentEvent,
    ResultEvent,
    WorkflowTicketDraftEvent,
    WorkflowTicketEvent,
    WorkflowTicketReviewEvent,
)
from tests.fakes import FakeAgentExecutor, FakeWorkspaceProvider


def _make_loop(
    *,
    executor: FakeAgentExecutor | object,
    max_reviews: int = 2,
) -> TicketGenerationLoop:
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=None,
    )
    return TicketGenerationLoop(
        service=service,
        workspace=FakeWorkspaceProvider(),
        max_reviews=max_reviews,
    )


def _run_kwargs() -> dict[str, object]:
    return {
        "prompt": "fix a bug",
        "repo_path": "/tmp/fake",
        "repo_url": None,
        "cache_key": "test-cache-key",
    }


# ---------------------------------------------------------------------------
# Multi-iteration executor that scripts review outcomes per call count
# ---------------------------------------------------------------------------


class _ScriptedReviewExecutor:
    """Executor that scripts review outcomes based on call order.

    review_outcomes: list of bools — True=approved, False=rejected.
    The executor auto-detects ticket-draft vs ticket-review schemas and
    returns appropriate structured outputs.
    """

    def __init__(
        self,
        review_outcomes: list[bool],
        *,
        reviewer_feedback: str = "Needs improvement.",
        reviewer_suggestions: list[str] | None = None,
    ) -> None:
        self._review_outcomes = list(review_outcomes)
        self._review_index = 0
        self._reviewer_feedback = reviewer_feedback
        self._reviewer_suggestions = reviewer_suggestions or []
        self.calls: list[dict[str, object]] = []

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
                    "summary": "Test summary",
                    "context": "Test context",
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
            approved = self._review_outcomes[self._review_index]
            self._review_index += 1
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="review-session",
                structured_output={
                    "approved": approved,
                    "feedback": "Looks good." if approved else self._reviewer_feedback,
                    "suggestions": ([] if approved else self._reviewer_suggestions),
                },
            )
            return
        yield ResultEvent(
            subtype="result",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="fake",
        )


# ---------------------------------------------------------------------------
# 9-02: Approved on first review
# ---------------------------------------------------------------------------


async def test_approved_on_first_review() -> None:
    """Script: create -> review (approved). Expect 1 draft, 1 review, 1 final."""
    executor = FakeAgentExecutor(events=[])
    loop = _make_loop(executor=executor)

    events = [e async for e in loop.run(**_run_kwargs())]

    draft_events = [e for e in events if isinstance(e, WorkflowTicketDraftEvent)]
    review_events = [e for e in events if isinstance(e, WorkflowTicketReviewEvent)]
    ticket_events = [e for e in events if isinstance(e, WorkflowTicketEvent)]

    assert len(draft_events) == 1
    assert len(review_events) == 1
    assert len(ticket_events) == 1
    assert ticket_events[0].review_rounds == 1
    assert ticket_events[0].approved is True


# ---------------------------------------------------------------------------
# 9-03: Approved on second review
# ---------------------------------------------------------------------------


async def test_approved_on_second_review() -> None:
    """Script: create -> review (reject) -> create -> review (approve)."""
    executor = _ScriptedReviewExecutor(review_outcomes=[False, True])
    loop = _make_loop(executor=executor)

    events = [e async for e in loop.run(**_run_kwargs())]

    draft_events = [e for e in events if isinstance(e, WorkflowTicketDraftEvent)]
    review_events = [e for e in events if isinstance(e, WorkflowTicketReviewEvent)]
    ticket_events = [e for e in events if isinstance(e, WorkflowTicketEvent)]

    assert len(draft_events) == 2
    assert len(review_events) == 2
    assert len(ticket_events) == 1
    assert ticket_events[0].review_rounds == 2
    assert ticket_events[0].approved is True


# ---------------------------------------------------------------------------
# 9-04: Max reviews exhausted
# ---------------------------------------------------------------------------


async def test_max_reviews_exhausted() -> None:
    """Script: both reviews reject -> finalize with approved=False."""
    executor = _ScriptedReviewExecutor(review_outcomes=[False, False])
    loop = _make_loop(executor=executor, max_reviews=2)

    events = [e async for e in loop.run(**_run_kwargs())]

    draft_events = [e for e in events if isinstance(e, WorkflowTicketDraftEvent)]
    review_events = [e for e in events if isinstance(e, WorkflowTicketReviewEvent)]
    ticket_events = [e for e in events if isinstance(e, WorkflowTicketEvent)]

    assert len(draft_events) == 2
    assert len(review_events) == 2
    assert len(ticket_events) == 1
    assert ticket_events[0].approved is False
    assert ticket_events[0].review_rounds == 2

    # CRITICAL: no third draft — loop stops after 2nd review
    assert len(draft_events) == 2, (
        "Loop must stop after max_reviews exhausted, no third draft."
    )


# ---------------------------------------------------------------------------
# 9-05: Revision prompt includes feedback
# ---------------------------------------------------------------------------


async def test_revision_prompt_includes_feedback() -> None:
    """After rejected first review, the second create call's prompt must
    include reviewer feedback AND the first draft's title."""
    feedback = "Missing error handling for edge case."
    suggestions = ["Add try/except around parse call"]
    executor = _ScriptedReviewExecutor(
        review_outcomes=[False, True],
        reviewer_feedback=feedback,
        reviewer_suggestions=suggestions,
    )
    loop = _make_loop(executor=executor)

    _ = [e async for e in loop.run(**_run_kwargs())]

    # Call order: [0]=create, [1]=review, [2]=create(revision), [3]=review
    assert len(executor.calls) >= 3
    revision_prompt = str(executor.calls[2]["prompt"])
    assert feedback in revision_prompt
    assert "Test ticket" in revision_prompt
    assert suggestions[0] in revision_prompt


# ---------------------------------------------------------------------------
# 9-06: Configurable values flow to executor
# ---------------------------------------------------------------------------


async def test_configurable_values_flow_to_executor() -> None:
    """repo_path, repo_url, cache_key from _run_kwargs appear in ALL executor calls."""
    executor = FakeAgentExecutor(events=[])
    loop = _make_loop(executor=executor)

    _ = [e async for e in loop.run(**_run_kwargs())]

    assert len(executor.calls) >= 2
    for call in executor.calls:
        assert call["cwd"] == "/tmp/fake-workspace"


# ---------------------------------------------------------------------------
# 9-07: WebSearch in allowed_tools
# ---------------------------------------------------------------------------


async def test_websearch_in_allowed_tools() -> None:
    """Every executor call must have WebSearch and WebFetch in allowed_tools."""
    executor = FakeAgentExecutor(events=[])
    loop = _make_loop(executor=executor)

    _ = [e async for e in loop.run(**_run_kwargs())]

    assert len(executor.calls) >= 2
    for call in executor.calls:
        allowed = call["allowed_tools"]
        assert isinstance(allowed, list)
        assert "WebSearch" in allowed
        assert "WebFetch" in allowed


# ---------------------------------------------------------------------------
# 9-08: Reviewer prompt has Sherlock framing
# ---------------------------------------------------------------------------


async def test_reviewer_prompt_has_sherlock_framing() -> None:
    """The review call's prompt contains the Sherlock/Watson pattern markers."""
    executor = FakeAgentExecutor(events=[])
    loop = _make_loop(executor=executor)

    _ = [e async for e in loop.run(**_run_kwargs())]

    # Find the review call — it's the one with ticket_review_schema
    review_calls = [
        c
        for c in executor.calls
        if c.get("output_format") is not None
        and isinstance(c["output_format"], dict)
        and _is_review_schema(c["output_format"])
    ]
    assert len(review_calls) >= 1
    review_prompt = str(review_calls[0]["prompt"])
    assert "You are Sherlock" in review_prompt
    assert "WATSON 1: ALIGNMENT" in review_prompt
    assert "WATSON 4: OFFICIAL DOCS" in review_prompt
    assert "Medium articles" in review_prompt
    assert "NO-DEFER RULE" in review_prompt


def _is_review_schema(output_format: dict[str, object]) -> bool:
    schema = output_format.get("schema")
    if not isinstance(schema, dict):
        return False
    props = schema.get("properties", {})
    return isinstance(props, dict) and "approved" in props and "feedback" in props


# ---------------------------------------------------------------------------
# Session resume lifecycle
# ---------------------------------------------------------------------------


async def test_first_call_passes_no_session_id() -> None:
    """Every run starts with session_id=None for both roles."""
    executor = FakeAgentExecutor(events=[])
    loop = _make_loop(executor=executor)
    _ = [e async for e in loop.run(**_run_kwargs())]
    assert executor.calls[0]["session_id"] is None
    assert executor.calls[1]["session_id"] is None


async def test_session_resume_lifecycle() -> None:
    """First call: session_id=None (fresh). Subsequent: captured UUID (resume)."""
    executor = _ScriptedReviewExecutor(review_outcomes=[False, True])
    loop = _make_loop(executor=executor)
    _ = [e async for e in loop.run(**_run_kwargs())]
    assert len(executor.calls) == 4
    assert executor.calls[0]["session_id"] is None
    assert executor.calls[1]["session_id"] is None
    assert executor.calls[2]["session_id"] == "draft-session"
    assert executor.calls[3]["session_id"] == "review-session"
    assert "draft-session" != "review-session"


# ---------------------------------------------------------------------------
# 9-11: Validation rejects invalid context
# ---------------------------------------------------------------------------


async def test_validation_rejects_invalid_context() -> None:
    """Empty prompt must raise pydantic.ValidationError."""
    executor = FakeAgentExecutor(events=[])
    loop = _make_loop(executor=executor)

    with pytest.raises(ValidationError):
        _ = [
            e
            async for e in loop.run(
                prompt="",
                repo_path="/tmp/fake",
                repo_url=None,
                cache_key="test-cache-key",
            )
        ]


# ---------------------------------------------------------------------------
# 9-12: No structured output from creator raises
# ---------------------------------------------------------------------------


async def test_no_structured_output_from_creator_raises() -> None:
    """Executor returns ResultEvent with structured_output=None for creator."""

    class NullCreatorExecutor:
        """Creator returns None structured_output."""

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def _is_ticket_draft_schema(
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
                and "title" in props
                and "requiredChanges" in props
            )

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
            self.calls.append({"output_format": output_format})
            if self._is_ticket_draft_schema(output_format):
                yield ResultEvent(
                    subtype="result",
                    duration_ms=1,
                    duration_api_ms=1,
                    is_error=False,
                    num_turns=1,
                    session_id="fake",
                    structured_output=None,
                )
                return
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="fake",
            )

    executor = NullCreatorExecutor()
    loop = _make_loop(executor=executor)

    with pytest.raises(RuntimeError, match="no structured output"):
        _ = [e async for e in loop.run(**_run_kwargs())]


# ---------------------------------------------------------------------------
# 9-13: No structured output from reviewer raises
# ---------------------------------------------------------------------------


async def test_no_structured_output_from_reviewer_raises() -> None:
    """Creator succeeds, reviewer returns structured_output=None."""

    class NullReviewerExecutor:
        """Creator succeeds, reviewer returns None structured_output."""

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def _is_ticket_draft_schema(
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
                and "title" in props
                and "requiredChanges" in props
            )

        def _is_ticket_review_schema(
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
                and "approved" in props
                and "feedback" in props
                and "suggestions" in props
            )

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
            self.calls.append({"output_format": output_format})
            if self._is_ticket_draft_schema(output_format):
                yield ResultEvent(
                    subtype="result",
                    duration_ms=1,
                    duration_api_ms=1,
                    is_error=False,
                    num_turns=1,
                    session_id="fake",
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
                    session_id="fake",
                    structured_output=None,
                )
                return
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="fake",
            )

    executor = NullReviewerExecutor()
    loop = _make_loop(executor=executor)

    with pytest.raises(RuntimeError, match="no structured output"):
        _ = [e async for e in loop.run(**_run_kwargs())]


# ---------------------------------------------------------------------------
# Workspace lifecycle tests
# ---------------------------------------------------------------------------


def _make_loop_with_workspace(
    *,
    executor: FakeAgentExecutor | object,
    workspace: FakeWorkspaceProvider,
    max_reviews: int = 2,
) -> TicketGenerationLoop:
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=None,
    )
    return TicketGenerationLoop(
        service=service,
        workspace=workspace,
        max_reviews=max_reviews,
    )


async def test_single_workspace_per_run() -> None:
    """Exactly one acquire and one release per run."""
    workspace = FakeWorkspaceProvider()
    executor = FakeAgentExecutor(events=[])
    loop = _make_loop_with_workspace(executor=executor, workspace=workspace)

    _ = [e async for e in loop.run(**_run_kwargs())]

    acquire_calls = [c for c in workspace.calls if c[0] == "acquire"]
    release_calls = [c for c in workspace.calls if c[0] == "release"]
    assert len(acquire_calls) == 1
    assert len(release_calls) == 1


async def test_all_calls_share_same_cwd() -> None:
    """Every executor call uses the workspace path from acquire."""
    workspace = FakeWorkspaceProvider()
    executor = FakeAgentExecutor(events=[])
    loop = _make_loop_with_workspace(executor=executor, workspace=workspace)

    _ = [e async for e in loop.run(**_run_kwargs())]

    assert len(executor.calls) >= 2
    for call in executor.calls:
        assert call["cwd"] == "/tmp/fake-workspace"


async def test_workspace_released_on_node_error() -> None:
    """Workspace is released even when a node raises."""

    class RaisingExecutor:
        """Raises on any stream call."""

        def __init__(self) -> None:
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
            self.calls.append({"cwd": cwd})
            raise RuntimeError("simulated node failure")
            yield  # pragma: no cover — makes this an async generator

    workspace = FakeWorkspaceProvider()
    executor = RaisingExecutor()
    loop = _make_loop_with_workspace(executor=executor, workspace=workspace)

    with pytest.raises(RuntimeError, match="simulated node failure"):
        _ = [e async for e in loop.run(**_run_kwargs())]

    release_calls = [c for c in workspace.calls if c[0] == "release"]
    assert len(release_calls) == 1
    assert release_calls[0][1] == "/tmp/fake-workspace"


async def test_workspace_released_on_success() -> None:
    """On a normal run, release is called with the exact path from acquire."""
    workspace = FakeWorkspaceProvider()
    executor = FakeAgentExecutor(events=[])
    loop = _make_loop_with_workspace(executor=executor, workspace=workspace)

    _ = [e async for e in loop.run(**_run_kwargs())]

    acquire_calls = [c for c in workspace.calls if c[0] == "acquire"]
    release_calls = [c for c in workspace.calls if c[0] == "release"]
    assert len(acquire_calls) == 1
    assert len(release_calls) == 1
    assert release_calls[0][1] == "/tmp/fake-workspace"
