"""End-to-end SSE streaming tests for agent endpoints."""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Response

from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.chains.ticket_generation import TicketGenerationLoop
from kodezart.main import create_app
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AssistantTextEvent,
    ErrorEvent,
    ResultEvent,
)
from kodezart.types.domain.consolidation import (
    ConsolidationOutcome,
    ConsolidationStatus,
)
from kodezart.types.domain.ticket_review import TicketApproval, TicketReviewMode
from tests.chains.test_dispatch_definitions import v5_provider
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeBranchMerger,
    FakeChangePersister,
    FakeCIMonitor,
    FakeGitService,
    FakePRCreator,
    FakeQualityGate,
    FakeRaisingExecutor,
    FakeRepoCache,
    FakeTicketGenerator,
    FakeWorkspaceProvider,
    PassThroughGate,
    attached_job_queue,
    make_passing_evaluation,
    make_prompt_provider,
)


async def _collect_sse_events(response: Response) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


async def test_stream_query_returns_events(agent_client: AsyncClient) -> None:
    async with agent_client.stream(
        "POST",
        "/api/v1/agent/query",
        json={"prompt": "analyze", "repoPath": "/tmp/fake"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
        events = await _collect_sse_events(response)

    assert len(events) == 2
    text_event = AssistantTextEvent.model_validate(events[0])
    assert text_event.text == "analysis complete"
    assert text_event.model == "test-model"

    result_event = ResultEvent.model_validate(events[1])
    assert result_event.is_error is False
    assert result_event.session_id == "test-session"


async def test_stream_query_workspace_failure() -> None:
    app = create_app()
    app.state.skills = SUPPRESS_ALL_SKILLS
    app.state.agent_service = AgentService(
        git_base_url="https://github.com",
        executor=FakeAgentExecutor(events=[]),
        workspace=FakeWorkspaceProvider(fail_acquire="Not a git repository: /bad/path"),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        async with ac.stream(
            "POST",
            "/api/v1/agent/query",
            json={"prompt": "analyze", "repoPath": "/bad/path"},
        ) as response:
            events = await _collect_sse_events(response)

    assert len(events) == 1
    error_event = ErrorEvent.model_validate(events[0])
    assert "Not a git repository" in error_event.error


async def test_stream_query_validates_request_body(agent_client: AsyncClient) -> None:
    response = await agent_client.post(
        "/api/v1/agent/query",
        json={"prompt": "", "repoPath": "/tmp/fake"},
    )
    assert response.status_code == 422


async def test_stream_query_repo_url_shorthand(agent_client: AsyncClient) -> None:
    async with agent_client.stream(
        "POST",
        "/api/v1/agent/query",
        json={"prompt": "analyze", "repoUrl": "owner/repo"},
    ) as response:
        assert response.status_code == 200
        events = await _collect_sse_events(response)
    assert len(events) == 2
    assert events[0]["type"] == "assistant_text"


async def test_stream_query_repo_url_full(agent_client: AsyncClient) -> None:
    async with agent_client.stream(
        "POST",
        "/api/v1/agent/query",
        json={"prompt": "analyze", "repoUrl": "https://github.com/o/r"},
    ) as response:
        assert response.status_code == 200
        events = await _collect_sse_events(response)
    assert len(events) == 2


async def test_stream_query_repo_url_with_branch(agent_client: AsyncClient) -> None:
    async with agent_client.stream(
        "POST",
        "/api/v1/agent/query",
        json={"prompt": "analyze", "repoUrl": "o/r", "branch": "dev"},
    ) as response:
        assert response.status_code == 200
        events = await _collect_sse_events(response)
    assert len(events) == 2


async def test_stream_query_mutual_exclusion(agent_client: AsyncClient) -> None:
    response = await agent_client.post(
        "/api/v1/agent/query",
        json={"prompt": "x", "repoPath": "/tmp/test", "repoUrl": "o/r"},
    )
    assert response.status_code == 422


async def test_stream_query_branch_without_repo_url(agent_client: AsyncClient) -> None:
    response = await agent_client.post(
        "/api/v1/agent/query",
        json={"prompt": "x", "repoPath": "/tmp/test", "branch": "main"},
    )
    assert response.status_code == 422


async def test_stream_query_missing_repo_source(agent_client: AsyncClient) -> None:
    response = await agent_client.post(
        "/api/v1/agent/query",
        json={"prompt": "analyze"},
    )
    assert response.status_code == 422


# loop_scope="function" pins the fixture to the test's event loop so the
# dispatcher's worker tasks run while the request is in flight; the
# project default fixture loop scope is "session".
@pytest_asyncio.fixture(loop_scope="function")
async def workflow_client() -> AsyncGenerator[AsyncClient, None]:
    async with _workflow_client(FakeTicketGenerator()) as client:
        yield client


# The ticket generator is a parameter because one test drives the REAL
# create-only loop through this pipeline; everything else about the app is
# the same app, so it is built once.
@asynccontextmanager
async def _workflow_client(
    ticket_generator: object,
) -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    executor = FakeAgentExecutor(
        events=[
            AssistantTextEvent(text="done", model="test-model"),
            ResultEvent(
                subtype="result",
                duration_ms=10,
                duration_api_ms=5,
                is_error=False,
                num_turns=1,
                session_id="wf-session",
                structured_output={
                    "criteriaResults": [
                        {
                            "criterionId": "AC-1",
                            "criterion": "Tests pass",
                            "passed": True,
                            "reasoning": "Everything passes.",
                        },
                        {
                            "criterionId": "AC-2",
                            "criterion": "No lint errors",
                            "passed": True,
                            "reasoning": "Everything passes.",
                        },
                    ],
                },
            ),
        ]
    )
    workspace = FakeWorkspaceProvider()
    persister = FakeChangePersister()
    service = AgentService(
        git_base_url="https://github.com",
        executor=executor,
        workspace=workspace,
        persister=persister,
    )
    merger = FakeBranchMerger()
    gate = FakeQualityGate(
        events=[
            AssistantTextEvent(text="done", model="test-model"),
        ],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        quality_gate=gate,
        ticket_generator=ticket_generator,
        merger=merger,
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(),
        cache=FakeRepoCache(),
        artifact_persister=None,
        retry_max_attempts=3,
        retry_initial_interval=1.0,
        remediation_max_rounds=1,
        criteria_max_regeneration_rounds=1,
        fan_in_max_attempts=2,
    )
    app.state.skills = SUPPRESS_ALL_SKILLS
    app.state.agent_service = service
    app.state.workflow_engine = engine
    async with attached_job_queue(app, engine):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            yield ac


@pytest_asyncio.fixture(loop_scope="function")
async def create_only_workflow_client() -> AsyncGenerator[AsyncClient, None]:
    """The pipeline running the REAL ticket loop, compiled without a review arm."""
    loop = TicketGenerationLoop(
        service=AgentService(
            git_base_url="https://github.com",
            executor=FakeAgentExecutor(events=[]),
            workspace=FakeWorkspaceProvider(),
            persister=None,
        ),
        workspace=FakeWorkspaceProvider(),
        prompts=v5_provider(TicketReviewMode.CREATE_ONLY),
        skills=SUPPRESS_ALL_SKILLS,
        review_mode=TicketReviewMode.CREATE_ONLY,
        retry_max_attempts=3,
        retry_initial_interval=1.0,
    )
    async with _workflow_client(loop) as client:
        yield client


async def test_stream_workflow_sse(
    workflow_client: AsyncClient,
) -> None:
    async with workflow_client.stream(
        "POST",
        "/api/v1/agent/workflow",
        json={"prompt": "fix", "repoPath": "/tmp/fake"},
    ) as response:
        assert response.status_code == 200
        events = await _collect_sse_events(response)

    types = [e["type"] for e in events]
    assert "workflow_complete" in types


async def test_workflow_streams_criteria_event_via_sse(
    workflow_client: AsyncClient,
) -> None:
    """workflow_criteria appears in SSE stream before workflow_iteration."""
    async with workflow_client.stream(
        "POST",
        "/api/v1/agent/workflow",
        json={
            "prompt": "fix a bug",
            "repoPath": "/tmp/fake",
        },
    ) as response:
        assert response.status_code == 200
        events = await _collect_sse_events(response)

    event_types = [e["type"] for e in events]
    assert "workflow_criteria" in event_types, (
        f"No workflow_criteria event in {event_types}"
    )

    criteria_idx = event_types.index("workflow_criteria")
    assert "workflow_iteration" in event_types, (
        f"No workflow_iteration event in {event_types}"
    )
    iteration_idx = event_types.index("workflow_iteration")
    assert criteria_idx < iteration_idx, (
        f"workflow_criteria at {criteria_idx} must precede "
        f"workflow_iteration at {iteration_idx}"
    )

    criteria_event = events[criteria_idx]
    criteria_field = criteria_event["criteria"]
    reasoning_field = criteria_event["reasoning"]
    assert isinstance(criteria_field, list)
    assert isinstance(reasoning_field, str)
    assert len(criteria_field) > 0
    assert len(reasoning_field) > 0


async def test_workflow_rejects_acceptance_criteria_in_body(
    workflow_client: AsyncClient,
) -> None:
    """POST /api/v1/agent/workflow rejects acceptanceCriteria field in body."""
    response = await workflow_client.post(
        "/api/v1/agent/workflow",
        json={
            "prompt": "fix it",
            "repoPath": "/tmp/fake",
            "acceptanceCriteria": ["Tests pass"],
        },
    )
    assert response.status_code == 422


async def test_stream_query_handler_catches_executor_error() -> None:
    app = create_app()
    app.state.skills = SUPPRESS_ALL_SKILLS
    app.state.agent_service = AgentService(
        git_base_url="https://github.com",
        executor=FakeRaisingExecutor(RuntimeError("transient failure")),
        workspace=FakeWorkspaceProvider(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        async with ac.stream(
            "POST",
            "/api/v1/agent/query",
            json={"prompt": "analyze", "repoPath": "/tmp/fake"},
        ) as response:
            events = await _collect_sse_events(response)

    assert len(events) == 1
    error_event = ErrorEvent.model_validate(events[0])
    assert "transient failure" in error_event.error


async def test_error_event_carries_exception_class_on_runtime_path() -> None:
    """Bare RuntimeError surfaces as ErrorEvent(error_kind='RuntimeError')."""
    app = create_app()
    app.state.skills = SUPPRESS_ALL_SKILLS
    app.state.agent_service = AgentService(
        git_base_url="https://github.com",
        executor=FakeRaisingExecutor(RuntimeError("x")),
        workspace=FakeWorkspaceProvider(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        async with ac.stream(
            "POST",
            "/api/v1/agent/query",
            json={"prompt": "analyze", "repoPath": "/tmp/fake"},
        ) as response:
            events = await _collect_sse_events(response)

    assert len(events) == 1
    payload = events[0]
    error_event = ErrorEvent.model_validate(payload)
    assert error_event.error == "x"
    assert error_event.error_kind == "RuntimeError"
    assert error_event.cause_class is None
    assert error_event.raise_site is None
    assert error_event.exit_code is None
    assert error_event.stderr_tail is None
    # Tracebacks NEVER appear on the wire — they go on the log record
    # via exc_info, not on ErrorEvent.error.
    assert "Traceback (most recent call last)" not in error_event.error
    assert '\n  File "' not in error_event.error


async def test_error_event_carries_no_structured_output_payload() -> None:
    """NoStructuredOutputError surfaces raise_site/rate_limit_rejected populated."""
    from kodezart.core.errors import NoStructuredOutputError

    app = create_app()
    soft_failure = NoStructuredOutputError(
        "Creator produced no structured output.",
        raise_site="ticket_creator",
        result_event=None,
        rate_limit_rejected=False,
    )
    app.state.skills = SUPPRESS_ALL_SKILLS
    app.state.agent_service = AgentService(
        git_base_url="https://github.com",
        executor=FakeRaisingExecutor(soft_failure),
        workspace=FakeWorkspaceProvider(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        async with ac.stream(
            "POST",
            "/api/v1/agent/query",
            json={"prompt": "analyze", "repoPath": "/tmp/fake"},
        ) as response:
            events = await _collect_sse_events(response)

    assert len(events) == 1
    error_event = ErrorEvent.model_validate(events[0])
    assert error_event.error_kind == "NoStructuredOutputError"
    assert error_event.raise_site == "ticket_creator"
    assert error_event.rate_limit_rejected is False


# ---------------------------------------------------------------------------
# KOD-91/AC-10 — the renamed keys on the wire, and the old ones nowhere
# ---------------------------------------------------------------------------

_OLD_WIRE_KEYS = ("ciPassed", "ci_passed", "fixRound", "fix_round", "error")


@pytest_asyncio.fixture(loop_scope="function")
async def _ci_workflow_client() -> AsyncGenerator[AsyncClient, None]:
    """A run that opens a pull request and monitors CI on a repo that has none.

    The no-CI case AC-10 names: the monitor answers "no checks ran", which
    used to be ``passed: null`` on two events and needed a serializer hack
    to appear at all.
    """
    async with _workflow_client_with(
        pr_creator=FakePRCreator(),
        ci_monitor=FakeCIMonitor(
            passed=None,
            summary="No CI checks are configured for this repository.",
        ),
    ) as client:
        yield client


@pytest_asyncio.fixture(loop_scope="function")
async def _divergent_workflow_client() -> AsyncGenerator[AsyncClient, None]:
    """A run whose consolidation diverges — the only producer of a merge error."""
    async with _workflow_client_with(
        merger=FakeBranchMerger(
            consolidation_outcomes=[
                ConsolidationOutcome(
                    status=ConsolidationStatus.DIVERGENT,
                    feature_tip_sha="0" * 40,
                ),
            ],
        ),
    ) as client:
        yield client


@asynccontextmanager
async def _workflow_client_with(
    *,
    pr_creator: FakePRCreator | None = None,
    ci_monitor: FakeCIMonitor | None = None,
    merger: FakeBranchMerger | None = None,
) -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    service = AgentService(
        git_base_url="https://github.com",
        executor=FakeAgentExecutor(events=[]),
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    engine = RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        quality_gate=FakeQualityGate(
            events=[],
            evaluation=make_passing_evaluation(),
            total_iterations=1,
            last_commit_sha="a" * 40,
        ),
        ticket_generator=FakeTicketGenerator(),
        merger=merger or FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
        artifact_persister=None,
        retry_max_attempts=3,
        retry_initial_interval=1.0,
        remediation_max_rounds=1,
        criteria_max_regeneration_rounds=1,
        fan_in_max_attempts=2,
    )
    app.state.skills = SUPPRESS_ALL_SKILLS
    app.state.agent_service = service
    app.state.workflow_engine = engine
    async with attached_job_queue(app, engine):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


async def _workflow_events(client: AsyncClient) -> list[dict[str, object]]:
    async with client.stream(
        "POST",
        "/api/v1/agent/workflow",
        json={
            "prompt": "fix",
            "repoUrl": "https://github.com/owner/repo",
        },
    ) as response:
        assert response.status_code == 200
        return await _collect_sse_events(response)


def _of_type(events: list[dict[str, object]], wire_type: str) -> dict[str, object]:
    matching = [event for event in events if event["type"] == wire_type]
    assert len(matching) == 1, f"expected one {wire_type}, got {len(matching)}"
    return matching[0]


async def test_ci_status_is_on_the_wire_including_the_no_ci_case(
    _ci_workflow_client: AsyncClient,
) -> None:
    """KOD-91/AC-10: an enum member always serializes; ``null`` never did.

    Both serializer hacks existed to force a dropped key back into the
    payload under ``exclude_none=True``.  The status is a required enum
    now, so the key is on both events by construction — and the no-CI run
    says ``not_configured`` in its own name instead of ``passed: null``,
    which a consumer could not tell from "CI has not run yet".
    """
    events = await _workflow_events(_ci_workflow_client)

    assert _of_type(events, "workflow_ci")["ciStatus"] == "not_configured"
    assert _of_type(events, "workflow_complete")["ciStatus"] == "not_configured"


async def test_the_review_event_carries_the_count_it_actually_holds(
    _ci_workflow_client: AsyncClient,
) -> None:
    """KOD-91/AC-10: ``fixRoundsUsed`` — populated from the rounds-used state."""
    review = _of_type(await _workflow_events(_ci_workflow_client), "workflow_review")

    assert review["fixRoundsUsed"] == 0
    assert "fixRound" not in review


async def test_the_complete_event_names_the_merge_error_as_one(
    _divergent_workflow_client: AsyncClient,
) -> None:
    """KOD-91/AC-10: ``mergeError`` — the only thing that field ever carried."""
    complete = _of_type(
        await _workflow_events(_divergent_workflow_client),
        "workflow_complete",
    )

    assert isinstance(complete["mergeError"], str)
    assert "error" not in complete
    assert complete["outcome"] == "merge_divergent"


async def test_no_emitted_event_carries_any_of_the_old_keys(
    _ci_workflow_client: AsyncClient,
    _divergent_workflow_client: AsyncClient,
) -> None:
    """KOD-91/AC-10: the renames leave nothing behind, on any frame.

    Swept over every event both runs emit rather than the three that were
    renamed: a shim re-adding an old key anywhere would be invisible to a
    per-event assertion.
    """
    events = await _workflow_events(_ci_workflow_client)
    events += await _workflow_events(_divergent_workflow_client)

    assert len(events) > 1
    survivors = {
        (str(event["type"]), key)
        for event in events
        for key in _OLD_WIRE_KEYS
        if key in event
    }
    assert survivors == set()


async def test_the_create_only_ticket_event_reaches_the_wire(
    create_only_workflow_client: AsyncClient,
) -> None:
    """KOD-90-AC-8 — the three-state value and the mode, as a client sees them.

    Driven through the real loop rather than a double: the payload is only
    evidence if something produced it by running the mode.
    """
    async with create_only_workflow_client.stream(
        "POST",
        "/api/v1/agent/workflow",
        json={"prompt": "fix a bug", "repoPath": "/tmp/fake"},
    ) as response:
        assert response.status_code == 200
        events = await _collect_sse_events(response)

    ticket_events = [e for e in events if e["type"] == "workflow_ticket"]
    assert len(ticket_events) == 1
    assert ticket_events[0]["approved"] == TicketApproval.NOT_REVIEWED.value
    assert ticket_events[0]["mode"] == TicketReviewMode.CREATE_ONLY.value
    assert ticket_events[0]["reviewRounds"] == 0
