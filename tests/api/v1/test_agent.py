"""End-to-end SSE streaming tests for agent endpoints."""

import json
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.main import create_app
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AssistantTextEvent,
    ErrorEvent,
    ResultEvent,
)
from tests.fakes import (
    FakeAgentExecutor,
    FakeBranchMerger,
    FakeChangePersister,
    FakeQualityGate,
    FakeRaisingExecutor,
    FakeTicketGenerator,
    FakeWorkspaceProvider,
    make_passing_evaluation,
)


async def _collect_sse_events(response) -> list[dict]:
    events = []
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


async def test_stream_query_returns_events(agent_client: AsyncClient):
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


async def test_stream_query_workspace_failure():
    app = create_app()
    app.state.agent_service = AgentService(
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


async def test_stream_query_validates_request_body(agent_client: AsyncClient):
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


@pytest.fixture
async def workflow_client() -> AsyncGenerator[AsyncClient, None]:
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
                            "criterion": "All changes compile.",
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
        service=service,
        quality_gate=gate,
        ticket_generator=FakeTicketGenerator(),
        merger=merger,
        git_base_url="https://github.com",
        artifact_persister=None,
    )
    app.state.agent_service = service
    app.state.workflow_engine = engine
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


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
    assert len(criteria_event["criteria"]) > 0
    assert len(criteria_event["reasoning"]) > 0


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


async def test_stream_query_handler_catches_executor_error():
    app = create_app()
    app.state.agent_service = AgentService(
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
