"""Tests for TicketGenerationLoop (ticket draft + review sub-graph) with fakes."""

from collections.abc import AsyncGenerator, Sequence

import pytest
import structlog
from pydantic import ValidationError

from kodezart.chains.ticket_generation import TicketGenerationLoop
from kodezart.core.errors import NoStructuredOutputError, TicketReviewModeError
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AgentEvent,
    CritiqueFlag,
    ResultEvent,
    WorkflowTicketDraftEvent,
    WorkflowTicketEvent,
    WorkflowTicketReviewEvent,
)
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import (
    NO_SUBAGENTS,
    UNCONFIGURED_SESSION_POLICY,
    AgentDefinition,
    SessionPolicy,
)
from kodezart.types.domain.ticket_review import (
    DRAFT_CRITIC_LENS,
    TicketApproval,
    TicketReviewMode,
)
from tests.chains.test_dispatch_definitions import (
    LENS_NAMES,
    chain_source,
    dispatch_block,
    v5_provider,
)
from tests.fakes import (
    FAKE_SESSION_TYPE,
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeWorkspaceProvider,
    make_prompt_provider,
)


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
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        workspace=FakeWorkspaceProvider(),
        max_reviews=max_reviews,
        review_mode=TicketReviewMode.REVIEWED,
        retry_max_attempts=3,
        retry_initial_interval=1.0,
    )


def _run_kwargs() -> dict[str, object]:
    return {
        "prompt": "fix a bug",
        "repo_path": "/tmp/fake",
        "repo_url": None,
        "cache_key": "test-cache-key",
        "base_branch": "main",
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
        draft_flags: list[dict[str, str]] | None = None,
    ) -> None:
        self._review_outcomes = list(review_outcomes)
        self._review_index = 0
        self._reviewer_feedback = reviewer_feedback
        self._reviewer_suggestions = reviewer_suggestions or []
        self._draft_flags = draft_flags
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
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_type: SessionType = FAKE_SESSION_TYPE,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
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
                "agents": tuple(definition.name for definition in agents),
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
                    **(
                        {}
                        if self._draft_flags is None
                        else {"sherlockFlags": self._draft_flags}
                    ),
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
    assert ticket_events[0].approved is TicketApproval.APPROVED


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
    assert ticket_events[0].approved is TicketApproval.APPROVED


# ---------------------------------------------------------------------------
# 9-04: Max reviews exhausted
# ---------------------------------------------------------------------------


async def test_max_reviews_exhausted() -> None:
    """Script: both reviews reject -> finalize unapproved."""
    executor = _ScriptedReviewExecutor(review_outcomes=[False, False])
    loop = _make_loop(executor=executor, max_reviews=2)

    events = [e async for e in loop.run(**_run_kwargs())]

    draft_events = [e for e in events if isinstance(e, WorkflowTicketDraftEvent)]
    review_events = [e for e in events if isinstance(e, WorkflowTicketReviewEvent)]
    ticket_events = [e for e in events if isinstance(e, WorkflowTicketEvent)]

    assert len(draft_events) == 2
    assert len(review_events) == 2
    assert len(ticket_events) == 1
    assert ticket_events[0].approved is TicketApproval.UNAPPROVED
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
# KOD-65/AC-1 — no session is resumed, on any iteration, by either role
# ---------------------------------------------------------------------------


async def test_first_call_passes_no_session_id() -> None:
    """Every run starts with session_id=None for both roles."""
    executor = FakeAgentExecutor(events=[])
    loop = _make_loop(executor=executor)
    _ = [e async for e in loop.run(**_run_kwargs())]
    assert executor.calls[0]["session_id"] is None
    assert executor.calls[1]["session_id"] is None


async def test_no_call_in_a_revision_run_resumes_a_session() -> None:
    """The revision iterations are the ones that died, and they resume nothing.

    Both recorded deaths were revision-side: the resumed creator session
    carried a dangling task notification, and the CLI answered it with a
    zero-API-call turn that ended the SDK's receive loop before the
    revision prompt was dequeued.  Asserted over EVERY call rather than
    the first pair, because the second pair is where the resume was.

    The executor hands back distinct, non-null session ids on both
    schemas, so a reinstated resume shows up here as a real id.
    """
    executor = _ScriptedReviewExecutor(review_outcomes=[False, True])
    loop = _make_loop(executor=executor)
    _ = [e async for e in loop.run(**_run_kwargs())]

    assert len(executor.calls) == 4
    assert [call["session_id"] for call in executor.calls] == [None] * 4


async def test_the_revision_breadcrumb_reports_the_null_resume() -> None:
    """KOD-65/AC-3: the breadcrumb says what the revision call was handed."""
    executor = _ScriptedReviewExecutor(
        review_outcomes=[False, True],
        reviewer_suggestions=["tighten the summary", "name the base ref"],
    )
    loop = _make_loop(executor=executor)
    with structlog.testing.capture_logs() as logs:
        _ = [e async for e in loop.run(**_run_kwargs())]

    attempts = [entry for entry in logs if entry["event"] == "ticket_create_attempt"]
    assert [entry["iteration"] for entry in attempts] == [1, 2]
    assert [entry["resumed_session_id"] for entry in attempts] == [None, None]
    assert attempts[1]["suggestion_count"] == 2
    assert all(int(str(entry["prompt_chars"])) > 0 for entry in attempts)

    reviews = [entry for entry in logs if entry["event"] == "ticket_review_attempt"]
    assert [entry["iteration"] for entry in reviews] == [1, 2]
    assert [entry["resumed_session_id"] for entry in reviews] == [None, None]


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
                base_branch="main",
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
            skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
            session_type: SessionType = FAKE_SESSION_TYPE,
            agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
            session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
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

    with pytest.raises(
        NoStructuredOutputError, match="no structured output"
    ) as excinfo:
        _ = [e async for e in loop.run(**_run_kwargs())]
    assert excinfo.value.raise_site == "ticket_creator"
    # ``result_event`` is the ``structured_output=None`` ResultEvent the
    # NullCreatorExecutor emitted — primitives must be snapshotted.
    assert excinfo.value.result_event_observed is True
    assert excinfo.value.session_id == "fake"
    assert excinfo.value.rate_limit_rejected is False


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
            skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
            session_type: SessionType = FAKE_SESSION_TYPE,
            agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
            session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
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

    with pytest.raises(
        NoStructuredOutputError, match="no structured output"
    ) as excinfo:
        _ = [e async for e in loop.run(**_run_kwargs())]
    assert excinfo.value.raise_site == "ticket_reviewer"
    assert excinfo.value.result_event_observed is True
    assert excinfo.value.rate_limit_rejected is False


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
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        workspace=workspace,
        max_reviews=max_reviews,
        review_mode=TicketReviewMode.REVIEWED,
        retry_max_attempts=3,
        retry_initial_interval=1.0,
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
            skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
            session_type: SessionType = FAKE_SESSION_TYPE,
            agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
            session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
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


async def test_run_forwards_base_branch_to_acquire() -> None:
    """base_branch passed to run() must reach workspace.acquire() as ref."""
    workspace = FakeWorkspaceProvider()
    executor = FakeAgentExecutor(events=[])
    loop = _make_loop_with_workspace(executor=executor, workspace=workspace)

    _ = [e async for e in loop.run(**(_run_kwargs() | {"base_branch": "develop"}))]

    acquire_calls = [c for c in workspace.calls if c[0] == "acquire"]
    assert acquire_calls == [("acquire", "/tmp/fake", "develop")]


def test_the_create_dispatch_passes_exactly_the_sets_three_definitions() -> None:
    """KOD-87-AC-6 — the creator is the generative site of this loop."""
    block = dispatch_block(chain_source("ticket_generation.py"), "TICKET_DRAFT_SCHEMA")
    assert "agents=self._prompts.definitions()" in block
    assert tuple(d.name for d in v5_provider().definitions()) == LENS_NAMES


# ---------------------------------------------------------------------------
# KOD-92-AC-2 and AC-5 — the ticket loop's two roles, observed at dispatch
# ---------------------------------------------------------------------------


async def test_the_creator_dispatch_carries_the_generative_roles_policy() -> None:
    """The create session is authoring, so it runs at the authoring level."""
    from kodezart.types.domain.prompts import SessionRole
    from tests.chains.test_dispatch_definitions import creator_dispatches, v5_provider
    from tests.prompts.test_session_policy import v5_metadata

    runner = await creator_dispatches(v5_provider())
    generative = v5_metadata().session_roles[SessionRole.GENERATIVE]

    assert runner.dispatches[0].policy.effort is generative.effort
    assert runner.dispatches[0].policy.system_prompt_append is not None


async def test_the_creator_dispatch_names_no_skill_of_another_role() -> None:
    """A skill declared for the judgment role never reaches the author."""
    from kodezart.types.domain.prompts import SessionRole
    from kodezart.types.domain.skills import SkillsMode, SkillsSelection
    from tests.chains.test_dispatch_definitions import creator_dispatches, v5_provider
    from tests.prompts.test_session_policy import v5_metadata

    provider = v5_provider()
    runner = await creator_dispatches(provider)
    roles = v5_metadata().session_roles
    foreign = set(roles[SessionRole.EVALUATIVE].skills) - set(
        roles[SessionRole.GENERATIVE].skills,
    )
    assert foreign, "non-vacuity: the two roles declare different loadouts"

    # The dispatch runs under whatever the deployment allows, and the
    # recording fixture suppresses everything — so the narrowing is
    # exercised against a deployment that allows all of them, which is the
    # only configuration where a foreign skill COULD leak through.
    available = SkillsSelection(mode=SkillsMode.ALL)
    from kodezart.types.domain.prompts import PromptKey

    creator = provider.session_skills(PromptKey.TICKET_CREATE, available)
    reviewer = provider.session_skills(PromptKey.TICKET_REVIEW, available)

    assert set(creator.allowlist) == set(roles[SessionRole.GENERATIVE].skills)
    assert set(reviewer.allowlist) == set(roles[SessionRole.EVALUATIVE].skills)
    for skill in foreign:
        assert skill not in creator.allowlist
    assert runner.dispatches[0].skills == SUPPRESS_ALL_SKILLS


# ---------------------------------------------------------------------------
# KOD-90 — the create-only mode: which graph exists, and what it emits
#
# Every loop below resolves the set that ACTUALLY declares the three lenses.
# A create-only guarantee measured against a set with no critic to attach
# would be a guarantee about the fixture.
# ---------------------------------------------------------------------------


def _v5_loop(
    *,
    executor: FakeAgentExecutor | object,
    review_mode: TicketReviewMode,
    max_reviews: int | None = None,
) -> TicketGenerationLoop:
    """A loop over the lens-declaring set, compiled in *review_mode*."""
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=None,
    )
    return TicketGenerationLoop(
        skills=SUPPRESS_ALL_SKILLS,
        prompts=v5_provider(review_mode),
        service=service,
        workspace=FakeWorkspaceProvider(),
        review_mode=review_mode,
        max_reviews=max_reviews,
        retry_max_attempts=3,
        retry_initial_interval=1.0,
    )


def _graph_nodes(loop: TicketGenerationLoop) -> set[str]:
    """The compiled node set, without LangGraph's start/end sentinels."""
    return {
        name for name in loop._compiled.get_graph().nodes if not name.startswith("__")
    }


def _terminal_ticket_event(events: list[AgentEvent]) -> WorkflowTicketEvent:
    ticket_events = [e for e in events if isinstance(e, WorkflowTicketEvent)]
    assert len(ticket_events) == 1
    return ticket_events[0]


# --- AC-1 -----------------------------------------------------------------


def test_create_only_graph_has_no_review_node() -> None:
    """The reviewer is unreachable, not merely unvisited."""
    create_only = _v5_loop(
        executor=FakeAgentExecutor(events=[]),
        review_mode=TicketReviewMode.CREATE_ONLY,
    )
    reviewed = _v5_loop(
        executor=FakeAgentExecutor(events=[]),
        review_mode=TicketReviewMode.REVIEWED,
    )

    assert _graph_nodes(create_only) == {"create", "finalize"}
    assert _graph_nodes(reviewed) == {"create", "review", "finalize"}


def test_create_only_wires_the_creator_straight_to_finalize() -> None:
    """No conditional edge either: the branch itself is gone."""
    graph = _v5_loop(
        executor=FakeAgentExecutor(events=[]),
        review_mode=TicketReviewMode.CREATE_ONLY,
    )._compiled.get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("create", "finalize") in edges
    assert not [edge for edge in edges if "review" in edge]


# --- AC-2 -----------------------------------------------------------------


async def test_create_only_runs_exactly_one_creator_session() -> None:
    """One dispatch, and a terminal event that says no reviewer ran.

    The criterion names ``review_count``; that is the STATE key, which the
    finalize node emits as ``review_rounds`` on the event. Same number,
    asserted where the criterion asserts it — on the emitted event.
    """
    executor = _ScriptedReviewExecutor(review_outcomes=[])
    loop = _v5_loop(executor=executor, review_mode=TicketReviewMode.CREATE_ONLY)

    events = [e async for e in loop.run(**_run_kwargs())]
    terminal = _terminal_ticket_event(events)

    assert len(executor.calls) == 1
    assert [e for e in events if isinstance(e, WorkflowTicketReviewEvent)] == []
    assert terminal.approved is TicketApproval.NOT_REVIEWED
    assert terminal.review_rounds == 0
    assert terminal.mode is TicketReviewMode.CREATE_ONLY


# --- AC-3 -----------------------------------------------------------------


async def test_reviewed_mode_unchanged() -> None:
    """The whole reviewed sequence, end to end, and its exhaustion arm.

    Asserted here rather than inferred from the module's other tests
    passing: the mode work must leave create -> review -> revise ->
    finalize and the budget stop exactly as they were.
    """
    accepted = _ScriptedReviewExecutor(review_outcomes=[False, True])
    loop = _v5_loop(executor=accepted, review_mode=TicketReviewMode.REVIEWED)
    events = [e async for e in loop.run(**_run_kwargs())]

    drafts = [e for e in events if isinstance(e, WorkflowTicketDraftEvent)]
    reviews = [e for e in events if isinstance(e, WorkflowTicketReviewEvent)]
    assert [d.iteration for d in drafts] == [1, 2]
    assert [r.iteration for r in reviews] == [1, 2]
    assert [r.approved for r in reviews] == [False, True]
    terminal = _terminal_ticket_event(events)
    assert terminal.approved is TicketApproval.APPROVED
    assert terminal.review_rounds == 2
    assert terminal.mode is TicketReviewMode.REVIEWED

    exhausted = _ScriptedReviewExecutor(review_outcomes=[False, False])
    stopped = _v5_loop(
        executor=exhausted,
        review_mode=TicketReviewMode.REVIEWED,
        max_reviews=2,
    )
    stopped_events = [e async for e in stopped.run(**_run_kwargs())]

    assert (
        len([e for e in stopped_events if isinstance(e, WorkflowTicketDraftEvent)]) == 2
    )
    assert _terminal_ticket_event(stopped_events).review_rounds == 2


# --- AC-4 -----------------------------------------------------------------


def test_explicit_max_reviews_under_create_only_raises() -> None:
    """A configured knob the mode compiles nothing for is refused, not ignored."""
    with pytest.raises(TicketReviewModeError) as excinfo:
        _v5_loop(
            executor=FakeAgentExecutor(events=[]),
            review_mode=TicketReviewMode.CREATE_ONLY,
            max_reviews=3,
        )

    assert excinfo.value.settings == (
        "ticket_review_mode=create_only",
        "max_reviews=3",
    )


def test_an_unconfigured_max_reviews_under_create_only_constructs() -> None:
    """A default sitting where nobody put it is not a configuration."""
    loop = _v5_loop(
        executor=FakeAgentExecutor(events=[]),
        review_mode=TicketReviewMode.CREATE_ONLY,
    )

    assert _graph_nodes(loop) == {"create", "finalize"}


def test_the_configured_budget_still_binds_under_the_reviewed_mode() -> None:
    """Non-vacuity: the refusal is the mode's, not the parameter's."""
    loop = _v5_loop(
        executor=FakeAgentExecutor(events=[]),
        review_mode=TicketReviewMode.REVIEWED,
        max_reviews=3,
    )

    assert _graph_nodes(loop) == {"create", "review", "finalize"}


# --- AC-5: one test per value of the three-state, on the emitted event -----


async def test_a_rejected_draft_ships_unapproved() -> None:
    """The reviewer read it and said no — one round, unapproved."""
    executor = _ScriptedReviewExecutor(review_outcomes=[False])
    loop = _v5_loop(
        executor=executor,
        review_mode=TicketReviewMode.REVIEWED,
        max_reviews=1,
    )

    terminal = _terminal_ticket_event([e async for e in loop.run(**_run_kwargs())])

    assert terminal.approved is TicketApproval.UNAPPROVED
    assert terminal.review_rounds == 1
    assert terminal.mode is TicketReviewMode.REVIEWED


async def test_an_exhausted_budget_ships_unapproved_with_its_rounds() -> None:
    """Same value, different fact — the rounds are what tell them apart."""
    executor = _ScriptedReviewExecutor(review_outcomes=[False, False])
    loop = _v5_loop(
        executor=executor,
        review_mode=TicketReviewMode.REVIEWED,
        max_reviews=2,
    )

    terminal = _terminal_ticket_event([e async for e in loop.run(**_run_kwargs())])

    assert terminal.approved is TicketApproval.UNAPPROVED
    assert terminal.review_rounds == 2
    assert terminal.mode is TicketReviewMode.REVIEWED


async def test_an_approved_draft_ships_approved() -> None:
    executor = _ScriptedReviewExecutor(review_outcomes=[True])
    loop = _v5_loop(executor=executor, review_mode=TicketReviewMode.REVIEWED)

    terminal = _terminal_ticket_event([e async for e in loop.run(**_run_kwargs())])

    assert terminal.approved is TicketApproval.APPROVED
    assert terminal.review_rounds == 1
    assert terminal.mode is TicketReviewMode.REVIEWED


async def test_an_unreviewed_draft_ships_not_reviewed() -> None:
    executor = _ScriptedReviewExecutor(review_outcomes=[])
    loop = _v5_loop(executor=executor, review_mode=TicketReviewMode.CREATE_ONLY)

    terminal = _terminal_ticket_event([e async for e in loop.run(**_run_kwargs())])

    assert terminal.approved is TicketApproval.NOT_REVIEWED
    assert terminal.review_rounds == 0
    assert terminal.mode is TicketReviewMode.CREATE_ONLY


# --- AC-7 -----------------------------------------------------------------


async def test_create_only_attaches_draft_critic() -> None:
    """The lens the mode depends on is on the create dispatch, with the fragment."""
    executor = _ScriptedReviewExecutor(review_outcomes=[])
    loop = _v5_loop(executor=executor, review_mode=TicketReviewMode.CREATE_ONLY)

    _ = [e async for e in loop.run(**_run_kwargs())]

    create_call = executor.calls[0]
    assert DRAFT_CRITIC_LENS in create_call["agents"]
    assert "dispatch a draft-critic agent" in str(create_call["prompt"])


async def test_the_reviewed_create_dispatch_carries_no_critique_fragment() -> None:
    """Under reviewed the definitions are the set's and the fragment is absent."""
    executor = _ScriptedReviewExecutor(review_outcomes=[True])
    loop = _v5_loop(executor=executor, review_mode=TicketReviewMode.REVIEWED)

    _ = [e async for e in loop.run(**_run_kwargs())]

    create_call = executor.calls[0]
    assert create_call["agents"] == tuple(
        definition.name for definition in v5_provider().definitions()
    )
    assert "dispatch a draft-critic agent" not in str(create_call["prompt"])


def test_create_only_refuses_a_set_that_declares_no_critic() -> None:
    """The mandate is code-enforced: a set with no critic cannot run this mode."""
    service = AgentService(
        executor=FakeAgentExecutor(events=[]),
        workspace=FakeWorkspaceProvider(),
        persister=None,
    )

    with pytest.raises(TicketReviewModeError) as excinfo:
        TicketGenerationLoop(
            skills=SUPPRESS_ALL_SKILLS,
            prompts=make_prompt_provider(),
            service=service,
            workspace=FakeWorkspaceProvider(),
            review_mode=TicketReviewMode.CREATE_ONLY,
            retry_max_attempts=3,
            retry_initial_interval=1.0,
        )

    assert excinfo.value.settings == (
        "ticket_review_mode=create_only",
        "prompt_set declares no lens at all",
    )


# --- the critic's flag channel, consumed (fire-ruling FR-3) ---------------


async def test_the_critics_flags_ride_out_on_the_emitted_ticket() -> None:
    """A typed channel nothing reads is the silence the persona left behind."""
    executor = _ScriptedReviewExecutor(
        review_outcomes=[],
        draft_flags=[
            {
                "subject": "the retry wrapper around the parser",
                "reason": "no measured failure justifies it",
            },
        ],
    )
    loop = _v5_loop(executor=executor, review_mode=TicketReviewMode.CREATE_ONLY)

    terminal = _terminal_ticket_event([e async for e in loop.run(**_run_kwargs())])

    assert terminal.ticket.sherlock_flags == [
        CritiqueFlag(
            subject="the retry wrapper around the parser",
            reason="no measured failure justifies it",
        ),
    ]
    assert terminal.ticket.model_dump(by_alias=True)["sherlockFlags"] == [
        {
            "subject": "the retry wrapper around the parser",
            "reason": "no measured failure justifies it",
        },
    ]
