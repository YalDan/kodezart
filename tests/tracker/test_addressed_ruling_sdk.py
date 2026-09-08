"""Actual Git, AgentService and native SDK sessions at the addressed entry."""

import asyncio
from pathlib import Path

import pytest
from claude_agent_sdk import ResultMessage, SystemMessage

from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS
from kodezart.domain.errors import ScopedExecutionUnavailableError
from kodezart.types.domain.agent import (
    RULING_PROPOSAL_SCHEMA,
    TRACKER_CRITERIA_VALIDATION_SCHEMA,
)
from kodezart.types.domain.session import SessionType
from tests.fakes import DEFAULT_SETTING_SOURCES, knowledge_grant_for
from tests.prompts.test_fire_record import template
from tests.services.test_union_composition import git
from tests.tracker.test_addressed_preloop import BODY, IDENTITY, TODO, output
from tests.tracker.test_addressed_preloop import prepared as prepared
from tests.tracker.test_addressed_preloop_native import (
    native_repository as native_repository,
)


@pytest.mark.parametrize("mode", ["success", "cancel"])
async def test_public_native_entry_has_two_fresh_granted_sessions_on_the_same_head(
    native_repository, monkeypatch, mode
):
    prepared, url, bare, _, head = native_repository
    clients, closed, paths = [], [], []
    ruling_entered = asyncio.Event()

    class Client:
        def __init__(self, *, options):
            self.options = options
            self.session_id = f"native-session-{len(clients)}"
            clients.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append(self)

        async def query(self, prompt):
            self.prompt = prompt
            path = Path(self.options.cwd)
            paths.append(path)
            assert path.joinpath("source.txt").read_text() == "recorded dispatch head\n"
            assert await git(path, "rev-parse", "HEAD") == head
            assert await git(path, "branch", "--show-current") == ""
            assert BODY in prompt and TODO in prompt and "criterion/fake" not in prompt
            assert f"title is exactly:\n\n{IDENTITY.title()}\n" in prompt
            assert "honest account" in prompt and "What happened" in prompt

        async def receive_response(self):
            yield SystemMessage(subtype="init", data={"session_id": self.session_id})
            is_ruling = self.options.output_format["schema"] == RULING_PROPOSAL_SCHEMA
            if is_ruling:
                ruling_entered.set()
                if mode == "cancel":
                    await asyncio.Event().wait()
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id=self.session_id,
                structured_output={"rulings": [], "unresolvedQuestions": []}
                if is_ruling
                else output(),
            )

    monkeypatch.setattr(
        "kodezart.adapters.claude_client_executor.ClaudeSDKClient", Client
    )
    prepared.executor = ClaudeClientExecutor(
        setting_sources=DEFAULT_SETTING_SOURCES,
        knowledge_grant=knowledge_grant_for(SessionType.TICKET_FIRE),
        fire_record=template(),
    )
    before_refs = await git(bare, "show-ref")
    task = asyncio.create_task(prepared.drive(repo_url=url))
    try:
        if mode == "cancel":
            await asyncio.wait_for(ruling_entered.wait(), 10)
            task.cancel()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 10)
        else:
            with pytest.raises(
                ScopedExecutionUnavailableError, match="publication and loop"
            ):
                await task
        assert len(clients) == 2 and closed == clients
        assert len(set(paths)) == 2 and all(not path.exists() for path in paths)
        assert [row.options.output_format["schema"] for row in clients] == [
            TRACKER_CRITERIA_VALIDATION_SCHEMA,
            RULING_PROPOSAL_SCHEMA,
        ]
        for client in clients:
            assert client.options.resume is None
            assert client.options.permission_mode == EVAL_PERMISSION_MODE
            assert client.options.allowed_tools == list(EVAL_TOOLS)
        assert await git(bare, "show-ref") == before_refs
        assert not prepared.workspace._workspaces
        prepared.no_writes()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("read_number", [4, 5])
async def test_public_ruling_settles_native_replacement_reads_before_release(
    prepared, monkeypatch, tmp_path, read_number
):
    from tests.git_read_cancellation import assert_git_read_settles_before_release

    await assert_git_read_settles_before_release(
        invoke=prepared.drive,
        git=prepared.git,
        workspace=prepared.workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        phase="has_replace_refs",
        read_number=read_number,
    )
    assert len(prepared.executor.calls) == (1 if read_number == 4 else 2)
    prepared.no_writes()
