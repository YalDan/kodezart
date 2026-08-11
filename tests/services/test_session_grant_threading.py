"""The grant decision travels one path, and every session states its kind.

The grant is ONE decision with consequences at the executor, so what has
to be true is that the kind a caller names arrives at the option
construction unchanged — through each runner entry point, and from every
dispatching site in the source rather than from the three this file could
have spot-checked.
"""

import ast
from pathlib import Path

import pytest

from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.core.config import AppConfig
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import AssistantTextEvent, ResultEvent
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.session import SessionType
from tests.fakes import (
    DEFAULT_SETTING_SOURCES,
    NO_KNOWLEDGE_GRANT,
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeWorkspaceProvider,
    knowledge_grant_for,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "kodezart"

#: The dispatching methods a session travels through. A call to one of
#: these that names no session type is a session whose grant was decided
#: by a default somewhere else.
_DISPATCH_METHODS: frozenset[str] = frozenset(
    {"stream", "stream_workflow", "stream_in_workspace"},
)


def _service() -> tuple[AgentService, FakeAgentExecutor]:
    executor = FakeAgentExecutor(
        events=[
            AssistantTextEvent(text="done", model="test-model"),
            ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="fake",
            ),
        ],
    )
    return (
        AgentService(
            executor=executor,
            workspace=FakeWorkspaceProvider(),
            persister=None,
        ),
        executor,
    )


@pytest.mark.parametrize("session_type", list(SessionType))
async def test_stream_carries_the_session_type_to_the_executor(
    session_type,
) -> None:
    """Entry point 1 of 3."""
    service, executor = _service()

    async for _ in service.stream(
        prompt="p",
        repo_path="/tmp/fake",
        permission_mode="plan",
        allowed_tools=[],
        skills=SUPPRESS_ALL_SKILLS,
        session_type=session_type,
    ):
        pass

    assert [call["session_type"] for call in executor.calls] == [session_type]


@pytest.mark.parametrize("session_type", list(SessionType))
async def test_stream_in_workspace_carries_the_session_type(session_type) -> None:
    """Entry point 2 of 3."""
    service, executor = _service()

    async for _ in service.stream_in_workspace(
        prompt="p",
        workspace_path="/tmp/fake",
        permission_mode="plan",
        allowed_tools=[],
        skills=SUPPRESS_ALL_SKILLS,
        session_type=session_type,
    ):
        pass

    assert [call["session_type"] for call in executor.calls] == [session_type]


@pytest.mark.parametrize("session_type", list(SessionType))
async def test_stream_workflow_carries_the_session_type(session_type) -> None:
    """Entry point 3 of 3."""
    service, executor = _service()

    async for _ in service.stream_workflow(
        prompt="p",
        repo_path="/tmp/fake",
        branch_name="feature",
        permission_mode="plan",
        allowed_tools=[],
        skills=SUPPRESS_ALL_SKILLS,
        session_type=session_type,
        visibility=RepoVisibility.PRIVATE,
    ):
        pass

    assert [call["session_type"] for call in executor.calls] == [session_type]


def _dispatch_calls_missing_a_session_type() -> list[str]:
    """Every dispatching call in the source that names no session type."""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in _DISPATCH_METHODS:
                continue
            named = {kw.arg for kw in node.keywords}
            if "prompt" not in named:
                continue
            if "session_type" not in named:
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    return offenders


def test_no_dispatching_call_in_the_source_omits_its_session_type() -> None:
    """Census, not a spot check: a new call site cannot inherit a default."""
    assert _dispatch_calls_missing_a_session_type() == []


def test_the_census_reads_real_call_sites() -> None:
    """An empty sweep would let the rule above pass over nothing."""
    found = 0
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _DISPATCH_METHODS
                and any(kw.arg == "session_type" for kw in node.keywords)
            ):
                found += 1

    assert found >= 10


async def test_the_grant_reaching_the_options_is_the_one_app_config_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AppConfig is the origin: one configuration change, one decision."""
    monkeypatch.setenv("KODEZART_KNOWLEDGE_SESSION_GRANTS", '["ticket_fire"]')
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_TOKEN", "ntn_" + ("R" * 44))
    config = AppConfig()

    grant = config.knowledge_grant(knowledge_map="── fixture map ──")
    executor = ClaudeClientExecutor(
        setting_sources=DEFAULT_SETTING_SOURCES,
        knowledge_grant=grant,
    )

    assert grant.server_name == config.knowledge_mcp_server_name
    assert grant.server_url == config.knowledge_mcp_server_url
    assert grant.credential == config.knowledge_mcp_token
    assert grant.grants(SessionType.TICKET_FIRE) is True
    assert executor is not None


def test_a_grant_never_serializes_its_credential() -> None:
    """The resolved value is passed around; the secret is not part of it."""
    grant = knowledge_grant_for(SessionType.TICKET_FIRE)

    assert grant.credential is not None
    assert grant.credential not in grant.model_dump_json()
    assert NO_KNOWLEDGE_GRANT.granted == ()
