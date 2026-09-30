"""Tests for AgentService persistence integration."""

import pytest

from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import AssistantTextEvent, ResultEvent
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.persist import PersistResult, PersistSource
from kodezart.types.domain.session import PermissionMode
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
        git_base_url="https://github.com",
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
            permission_mode=PermissionMode.UNATTENDED,
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
        git_base_url="https://github.com",
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
            permission_mode=PermissionMode.PLAN,
            allowed_tools=["Bash"],
            output_format=fmt,
        )
    ]
    assert executor.calls[0]["output_format"] == fmt


async def test_stream_propagates_executor_error() -> None:
    service = AgentService(
        git_base_url="https://github.com",
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
                permission_mode=PermissionMode.PLAN,
                allowed_tools=["Bash"],
            )
        ]


async def test_authored_persistence_failure_keeps_workspace_cleanup(monkeypatch):
    workspace = FakeWorkspaceProvider()
    persister = FakeChangePersister()
    failure = RuntimeError("authored persistence failed")

    async def refuse(**kwargs):
        raise failure

    monkeypatch.setattr(persister, "persist", refuse)
    service = AgentService(
        git_base_url="https://github.com",
        executor=FakeAgentExecutor(
            events=[
                ResultEvent(
                    subtype="result",
                    duration_ms=10,
                    duration_api_ms=5,
                    is_error=False,
                    num_turns=1,
                    session_id="s1",
                )
            ]
        ),
        workspace=workspace,
        persister=persister,
    )
    with pytest.raises(RuntimeError) as caught:
        [
            event
            async for event in service.stream_workflow(
                skills=SUPPRESS_ALL_SKILLS,
                session_type=FAKE_SESSION_TYPE,
                prompt="fix it",
                repo_path="/tmp/fake",
                branch_name="authored",
                permission_mode=PermissionMode.UNATTENDED,
                allowed_tools=["Bash"],
                visibility=RepoVisibility.UNKNOWN,
            )
        ]
    assert caught.value is failure
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


async def test_a_session_over_several_repositories_leaves_no_directory_behind() -> None:
    """The shared parent directory is removed after the session (KOD-1301).

    The removal runs on a thread of its own so it never holds the event
    loop, which makes its end something to wait for rather than a fact at
    the moment the stream closes.
    """
    import asyncio
    from pathlib import Path

    from kodezart.types.domain.operation import RepoEntry
    from tests.fakes import FakeGitService, FakeRepoCache

    class LeavesAFile:
        """A session that writes beside the checkouts, as a live one does."""

        def __init__(self) -> None:
            self.cwd: Path | None = None

        async def stream(self, **kwargs: object):
            self.cwd = Path(str(kwargs["cwd"]))
            (self.cwd / "left-by-the-session").write_text("x")
            yield AssistantTextEvent(text="done", model="m")

    executor = LeavesAFile()
    service = AgentService(
        git_base_url="https://forge.invalid",
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        git=FakeGitService(remote_branch_shas={"work": None}),
        cache=FakeRepoCache(),
    )
    [
        e
        async for e in service.stream(
            skills=SUPPRESS_ALL_SKILLS,
            session_type=FAKE_SESSION_TYPE,
            prompt="x",
            branch="work",
            permission_mode=PermissionMode.UNATTENDED,
            allowed_tools=["Read"],
            repositories=(
                RepoEntry(url="https://forge.invalid/one", trunk="main"),
                RepoEntry(url="https://forge.invalid/two", trunk="main"),
            ),
        )
    ]

    parent = executor.cwd
    assert parent is not None
    for _ in range(500):
        if not parent.exists():
            break
        await asyncio.sleep(0.01)
    assert not parent.exists()
