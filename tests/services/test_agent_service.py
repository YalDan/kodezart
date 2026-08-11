"""Tests for AgentService persistence integration."""

import pytest

from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import AssistantTextEvent, ResultEvent
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.persist import PersistResult, PersistSource
from tests.fakes import (
    FAKE_SESSION_TYPE,
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeChangePersister,
    FakeRaisingExecutor,
    FakeWorkspaceProvider,
)


async def test_stream_workflow_persists_changes() -> None:
    persister = FakeChangePersister(
        result=PersistResult(
            commit_sha="a" * 40,
            branch="kodezart/test",
            message="feat: scripted commit",
            source=PersistSource.WORKING_TREE_COMMIT,
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
            skills=SUPPRESS_ALL_SKILLS,
            session_type=FAKE_SESSION_TYPE,
            prompt="fix it",
            repo_path="/tmp/fake",
            branch_name="kodezart/test-branch-abc12345",
            ralph_branch="kodezart/test-branch-abc12345-ralph-def67890",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            visibility=RepoVisibility.UNKNOWN,
        )
    ]
    assert len(persister.calls) == 1
    result_events = [e for e in collected if isinstance(e, ResultEvent)]
    assert result_events[-1].commit_sha == "a" * 40


async def test_stream_passes_output_format() -> None:
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
            skills=SUPPRESS_ALL_SKILLS,
            session_type=FAKE_SESSION_TYPE,
            prompt="x",
            repo_path="/tmp/fake",
            permission_mode="plan",
            allowed_tools=["Bash"],
            output_format=fmt,
        )
    ]
    assert executor.calls[0]["output_format"] == fmt


async def test_stream_propagates_executor_error() -> None:
    service = AgentService(
        executor=FakeRaisingExecutor(RuntimeError("network error")),
        workspace=FakeWorkspaceProvider(),
    )
    with pytest.raises(RuntimeError, match="network error"):
        [
            e
            async for e in service.stream(
                skills=SUPPRESS_ALL_SKILLS,
                session_type=FAKE_SESSION_TYPE,
                prompt="x",
                repo_path="/tmp/fake",
                permission_mode="plan",
                allowed_tools=["Bash"],
            )
        ]
