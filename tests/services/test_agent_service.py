"""Tests for AgentService persistence integration."""

import pytest

from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import AssistantTextEvent, ResultEvent
from kodezart.types.domain.persist import PersistResult
from tests.fakes import (
    FakeAgentExecutor,
    FakeChangePersister,
    FakeRaisingExecutor,
    FakeWorkspaceProvider,
)


async def test_stream_workflow_persists_changes():
    persister = FakeChangePersister(
        result=PersistResult(
            commit_sha="a" * 40,
            branch="kodezart/test",
            message="fix: it",
        ),
    )
    service = AgentService(
        executor=FakeAgentExecutor(
            events=[
                AssistantTextEvent(text="done", model="m"),
                ResultEvent(
                    subtype="result",
                    duration_ms=10,
                    duration_api_ms=5,
                    is_error=False,
                    num_turns=1,
                    session_id="s1",
                ),
            ]
        ),
        workspace=FakeWorkspaceProvider(),
        persister=persister,
    )
    collected = [
        e
        async for e in service.stream_workflow(
            prompt="fix it",
            repo_path="/tmp/fake",
            branch_name="kodezart/test-branch-abc12345",
            ralph_branch="kodezart/test-branch-abc12345-ralph-def67890",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]
    assert len(persister.calls) == 1
    result_events = [e for e in collected if isinstance(e, ResultEvent)]
    assert result_events[-1].commit_sha == "a" * 40


async def test_stream_passes_output_format():
    executor = FakeAgentExecutor(
        events=[
            ResultEvent(
                subtype="result",
                duration_ms=10,
                duration_api_ms=5,
                is_error=False,
                num_turns=1,
                session_id="s1",
            ),
        ]
    )
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
    )
    fmt: dict[str, object] = {
        "type": "json_schema",
        "schema": {"type": "object"},
    }
    [
        e
        async for e in service.stream(
            prompt="x",
            repo_path="/tmp/fake",
            permission_mode="plan",
            allowed_tools=["Bash"],
            output_format=fmt,
        )
    ]
    assert executor.calls[0]["output_format"] == fmt


async def test_stream_propagates_executor_error():
    service = AgentService(
        executor=FakeRaisingExecutor(RuntimeError("network error")),
        workspace=FakeWorkspaceProvider(),
    )
    with pytest.raises(RuntimeError, match="network error"):
        [
            e
            async for e in service.stream(
                prompt="x",
                repo_path="/tmp/fake",
                permission_mode="plan",
                allowed_tools=["Bash"],
            )
        ]
