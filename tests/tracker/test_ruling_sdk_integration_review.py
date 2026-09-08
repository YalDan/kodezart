"""Independent combined SDK/AgentService/ruling integration controls."""

import asyncio

import pytest
from claude_agent_sdk import ResultMessage, SystemMessage

from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.core.errors import NoStructuredOutputError
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import RULING_PROPOSAL_SCHEMA
from tests.fakes import DEFAULT_SETTING_SOURCES, NO_KNOWLEDGE_GRANT
from tests.tracker import test_rule_open_questions as fixtures
from tests.tracker import test_rule_open_questions_git as native_fixtures

server = fixtures.server
setup = fixtures.setup
proposal = fixtures.proposal
git_repo = native_fixtures.git_repo
provider = native_fixtures.provider
native = native_fixtures.native


@pytest.mark.parametrize("mode", ["success", "missing_output", "cancel"])
async def test_combined_native_sdk_ruling_keeps_its_session_and_workspace_contract(
    native, provider, tracker_writes, monkeypatch, mode
):
    build, scripted, request, _ = native
    clients, closed = [], []
    entered = asyncio.Event()
    before = tracker_writes()

    class Client:
        def __init__(self, *, options):
            self.options = options
            clients.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append(self)

        async def query(self, prompt):
            assert fixtures.source.BODY in prompt
            assert request.head_sha in prompt

        async def receive_response(self):
            yield SystemMessage(
                subtype="init", data={"session_id": "native-ruling-session"}
            )
            entered.set()
            if mode == "cancel":
                await asyncio.Event().wait()
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="native-ruling-session",
                structured_output=(scripted.output if mode == "success" else None),
            )

    monkeypatch.setattr(
        "kodezart.adapters.claude_client_executor.ClaudeSDKClient", Client
    )
    executor = ClaudeClientExecutor(
        setting_sources=DEFAULT_SETTING_SOURCES,
        knowledge_grant=NO_KNOWLEDGE_GRANT,
    )
    service = AgentService(
        executor=executor, workspace=provider, git_base_url="https://forge.invalid"
    )
    task = asyncio.create_task(build(runner=service).propose(request))
    try:
        if mode == "cancel":
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        elif mode == "missing_output":
            with pytest.raises(NoStructuredOutputError) as caught:
                await task
            assert caught.value.raise_site == "fire_time_ruling"
        else:
            assert len((await task).rulings) == 1
        assert len(clients) == 1 and closed == clients
        options = clients[0].options
        assert options.resume is None
        assert options.allowed_tools == list(fixtures.EVAL_TOOLS)
        assert options.permission_mode == fixtures.EVAL_PERMISSION_MODE
        assert options.output_format["schema"] == RULING_PROPOSAL_SCHEMA
        assert not provider._workspaces
        assert tracker_writes() == before
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
