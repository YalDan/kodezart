"""Tests for ClaudeAgentExecutor and ClaudeClientExecutor SDK exception wrapping."""

import inspect
from collections.abc import AsyncGenerator
from dataclasses import replace
from pathlib import Path
from typing import Any, Final
from unittest.mock import patch

import pytest
import structlog
from claude_agent_sdk import ClaudeAgentOptions, ProcessError

from kodezart.adapters._mcp_mapping import map_knowledge_mcp
from kodezart.adapters._skills_mapping import map_skills
from kodezart.adapters.claude_agent_executor import ClaudeAgentExecutor
from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.core.error_egress import _REDACTION_SENTINEL
from kodezart.domain.errors import AgentSDKError
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.session import KnowledgeGrant, SessionType
from kodezart.types.domain.skills import SkillsMode, SkillsSelection
from tests.fakes import (
    DEFAULT_SETTING_SOURCES,
    EXECUTOR_MODULES,
    FAKE_SESSION_TYPE,
    FIXTURE_KNOWLEDGE_MAP,
    FIXTURE_KNOWLEDGE_SERVER,
    NO_KNOWLEDGE_GRANT,
    SUPPRESS_ALL_SKILLS,
    executor_for,
    knowledge_grant_for,
)


async def _drain(gen: AsyncGenerator[AgentEvent, None]) -> list[AgentEvent]:
    """Consume an async generator into a list."""
    return [event async for event in gen]


def test_agent_executor_instantiates() -> None:
    """ClaudeAgentExecutor can be constructed without side effects."""
    executor = ClaudeAgentExecutor(
        setting_sources=DEFAULT_SETTING_SOURCES,
        knowledge_grant=NO_KNOWLEDGE_GRANT,
    )
    assert executor is not None


def test_client_executor_instantiates() -> None:
    """ClaudeClientExecutor can be constructed without side effects."""
    executor = ClaudeClientExecutor(
        setting_sources=DEFAULT_SETTING_SOURCES,
        knowledge_grant=NO_KNOWLEDGE_GRANT,
    )
    assert executor is not None


def test_agent_sdk_error_preserves_kind() -> None:
    """AgentSDKError stores error_kind for downstream handling."""
    err = AgentSDKError("something broke", error_kind="ProcessError")
    assert err.error_kind == "ProcessError"
    assert "something broke" in str(err)


def test_agent_sdk_error_preserves_process_error_detail() -> None:
    """AgentSDKError stores exit_code and stderr_tail as primitive scalars."""
    err = AgentSDKError(
        "process failed",
        error_kind="ProcessError",
        exit_code=137,
        stderr_tail="oom-killer fired",
    )
    assert err.exit_code == 137
    assert err.stderr_tail == "oom-killer fired"


def test_agent_sdk_error_exit_code_and_stderr_default_none() -> None:
    """exit_code and stderr_tail default to None for non-ProcessError branches."""
    err = AgentSDKError("connection dropped", error_kind="CLIConnectionError")
    assert err.exit_code is None
    assert err.stderr_tail is None


class _FakeSDKClient:
    """Stand-in for ``claude_agent_sdk.ClaudeSDKClient`` that raises on query."""

    def __init__(self, exc_to_raise: Exception) -> None:
        self._exc = exc_to_raise

    async def __aenter__(self) -> "_FakeSDKClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def query(self, prompt: str) -> None:
        _ = prompt
        raise self._exc

    async def receive_response(self) -> AsyncGenerator[Any, None]:  # pragma: no cover
        yield None


async def test_process_error_round_trips_exit_code_and_stderr_on_re_raise() -> None:
    """ProcessError(exit_code, stderr) survives the re-raise on AgentSDKError."""
    boom = ProcessError("boom", exit_code=137, stderr="<known-tail>" * 10)
    executor = ClaudeClientExecutor(
        setting_sources=DEFAULT_SETTING_SOURCES,
        knowledge_grant=NO_KNOWLEDGE_GRANT,
    )
    with patch(
        "kodezart.adapters.claude_client_executor.ClaudeSDKClient",
        lambda **_: _FakeSDKClient(boom),
    ):
        with pytest.raises(AgentSDKError) as excinfo:
            await _drain(
                executor.stream(
                    skills=SUPPRESS_ALL_SKILLS,
                    session_type=FAKE_SESSION_TYPE,
                    prompt="x",
                    cwd="/tmp",
                    permission_mode="default",
                    allowed_tools=[],
                )
            )
    err = excinfo.value
    assert err.error_kind == "ProcessError"
    assert err.exit_code == 137
    assert err.stderr_tail is not None
    assert "<known-tail>" in err.stderr_tail
    # STDERR_TAIL_BYTES = 4096 — verified at module level.
    assert len(err.stderr_tail) <= 4096


async def test_process_error_with_none_stderr_does_not_crash() -> None:
    """ProcessError(exit_code=137, stderr=None) re-raises with stderr_tail=None."""
    boom = ProcessError("boom", exit_code=137, stderr=None)
    executor = ClaudeClientExecutor(
        setting_sources=DEFAULT_SETTING_SOURCES,
        knowledge_grant=NO_KNOWLEDGE_GRANT,
    )
    with patch(
        "kodezart.adapters.claude_client_executor.ClaudeSDKClient",
        lambda **_: _FakeSDKClient(boom),
    ):
        with pytest.raises(AgentSDKError) as excinfo:
            await _drain(
                executor.stream(
                    skills=SUPPRESS_ALL_SKILLS,
                    session_type=FAKE_SESSION_TYPE,
                    prompt="x",
                    cwd="/tmp",
                    permission_mode="default",
                    allowed_tools=[],
                )
            )
    err = excinfo.value
    assert err.error_kind == "ProcessError"
    assert err.exit_code == 137
    assert err.stderr_tail is None


# ---------------------------------------------------------------------------
# Credential redaction — ensures the tokenized URL in ``ProcessError.stderr``
# does not leak through either the structured warning log or the
# ``AgentSDKError.stderr_tail`` field.  Token-named locals are avoided to
# dodge ruff S105 (active in tests).
# ---------------------------------------------------------------------------

_FAKE_GHP_BODY: Final[str] = "A" * 40
_FAKE_URL: Final[str] = (
    f"https://x-access-token:ghp_{_FAKE_GHP_BODY}@github.com/o/r.git"
)


async def test_process_error_redacts_token_in_warning_log() -> None:
    """``claude_sdk_process_error`` log scrubs the credential URL in stderr."""
    stderr_payload = f"git fetch failed: {_FAKE_URL} permission denied"
    boom = ProcessError("git fetch failed", exit_code=128, stderr=stderr_payload)
    executor = ClaudeClientExecutor(
        setting_sources=DEFAULT_SETTING_SOURCES,
        knowledge_grant=NO_KNOWLEDGE_GRANT,
    )
    with patch(
        "kodezart.adapters.claude_client_executor.ClaudeSDKClient",
        lambda **_: _FakeSDKClient(boom),
    ):
        with structlog.testing.capture_logs() as captured:
            with pytest.raises(AgentSDKError):
                await _drain(
                    executor.stream(
                        skills=SUPPRESS_ALL_SKILLS,
                        session_type=FAKE_SESSION_TYPE,
                        prompt="x",
                        cwd="/tmp",
                        permission_mode="default",
                        allowed_tools=[],
                    )
                )
    process_error_records = [
        rec for rec in captured if rec.get("event") == "claude_sdk_process_error"
    ]
    assert process_error_records, "expected a claude_sdk_process_error log record"
    record = process_error_records[0]
    assert "stderr" in record
    stderr_logged = record["stderr"]
    assert stderr_logged is not None
    assert _FAKE_GHP_BODY not in stderr_logged
    assert _REDACTION_SENTINEL in stderr_logged
    # Defense-in-depth: the secret body must not survive in ANY captured
    # record's serialized form.
    for rec in captured:
        assert _FAKE_GHP_BODY not in repr(rec)


async def test_process_error_stderr_tail_on_agent_sdk_error_is_redacted() -> None:
    """``AgentSDKError.stderr_tail`` is redact-before-slice; secret cannot leak."""
    boom = ProcessError("git fetch failed", exit_code=128, stderr=_FAKE_URL)
    executor = ClaudeClientExecutor(
        setting_sources=DEFAULT_SETTING_SOURCES,
        knowledge_grant=NO_KNOWLEDGE_GRANT,
    )
    with patch(
        "kodezart.adapters.claude_client_executor.ClaudeSDKClient",
        lambda **_: _FakeSDKClient(boom),
    ):
        with pytest.raises(AgentSDKError) as excinfo:
            await _drain(
                executor.stream(
                    skills=SUPPRESS_ALL_SKILLS,
                    session_type=FAKE_SESSION_TYPE,
                    prompt="x",
                    cwd="/tmp",
                    permission_mode="default",
                    allowed_tools=[],
                )
            )
    assert excinfo.value.stderr_tail is not None
    assert _FAKE_GHP_BODY not in excinfo.value.stderr_tail
    assert _REDACTION_SENTINEL in excinfo.value.stderr_tail


# ---------------------------------------------------------------------------
# KOD-46 — skill selection reaches ClaudeAgentOptions from AppConfig
# ---------------------------------------------------------------------------


def _capture(module: str):
    """Patch the SDK transport in *module* and record the options it receives."""
    recorded: list[ClaudeAgentOptions] = []

    def sink(*args, **kwargs):
        options = kwargs["options"]
        assert isinstance(options, ClaudeAgentOptions)
        recorded.append(options)
        msg = "stop after options"
        raise RuntimeError(msg)

    target = "ClaudeSDKClient" if module.endswith("claude_client_executor") else "query"
    return recorded, patch(f"{module}.{target}", sink)


async def _options_for(
    module: str,
    *,
    grant: KnowledgeGrant,
    session_type: SessionType,
) -> ClaudeAgentOptions:
    """The options *module*'s adapter constructs for one session."""
    recorded, patcher = _capture(module)
    executor = executor_for(module, grant)

    with patcher, pytest.raises(RuntimeError, match="stop after options"):
        await _drain(
            executor.stream(
                prompt="p",
                cwd="/tmp/fake",
                permission_mode="plan",
                allowed_tools=[],
                skills=SUPPRESS_ALL_SKILLS,
                session_type=session_type,
            )
        )

    assert len(recorded) == 1
    return recorded[0]


SKILLS_MATRIX = [
    (SkillsSelection(mode=SkillsMode.NONE), []),
    (SkillsSelection(mode=SkillsMode.ALL), "all"),
    (SkillsSelection(mode=SkillsMode.EXPLICIT, allowlist=("alpha",)), ["alpha"]),
]


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        (SkillsSelection(mode=SkillsMode.NONE), []),
        (SkillsSelection(mode=SkillsMode.ALL), "all"),
        (
            SkillsSelection(mode=SkillsMode.EXPLICIT, allowlist=("alpha", "beta")),
            ["alpha", "beta"],
        ),
    ],
)
def test_skills_mapping_is_exhaustive_over_the_enum(selection, expected) -> None:
    """NONE -> [], ALL -> "all", EXPLICIT -> the allowlist. Never None."""
    mapped = map_skills(selection)
    assert mapped == expected
    assert mapped is not None


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
@pytest.mark.parametrize(("selection", "expected"), SKILLS_MATRIX)
async def test_both_executors_pass_the_mapped_skills_never_none(
    module,
    selection,
    expected,
) -> None:
    """Neither adapter has a code path that hands the SDK ``skills=None``."""
    recorded, patcher = _capture(module)
    executor = executor_for(module)

    with patcher, pytest.raises(RuntimeError, match="stop after options"):
        await _drain(
            executor.stream(
                prompt="p",
                cwd="/tmp/fake",
                permission_mode="plan",
                allowed_tools=[],
                skills=selection,
                session_type=FAKE_SESSION_TYPE,
            )
        )

    assert len(recorded) == 1
    assert recorded[0].skills == expected
    assert recorded[0].skills is not None


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
@pytest.mark.parametrize(("selection", "expected"), SKILLS_MATRIX)
async def test_setting_sources_come_from_config_in_every_mode(
    module,
    selection,
    expected,
) -> None:
    """AC-1c: the skills knob never silently narrows loaded settings."""
    recorded, patcher = _capture(module)
    executor = executor_for(module)

    with patcher, pytest.raises(RuntimeError, match="stop after options"):
        await _drain(
            executor.stream(
                prompt="p",
                cwd="/tmp/fake",
                permission_mode="plan",
                allowed_tools=[],
                skills=selection,
                session_type=FAKE_SESSION_TYPE,
            )
        )

    assert recorded[0].setting_sources == ["user", "project", "local"]


def test_no_skill_name_literal_lives_in_the_adapters() -> None:
    """D-2: the configured skill set is data — no hardcoded lists in adapters."""
    adapters = Path(__file__).resolve().parents[2] / "src" / "kodezart" / "adapters"
    for path in adapters.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "skills=[" not in source
        assert 'skills = ["' not in source


# ---------------------------------------------------------------------------
# The knowledge grant: who is configured with the server, and who is untouched
# ---------------------------------------------------------------------------

#: The arguments each adapter constructed before any grant existed, so a
#: non-granted session can be compared against them argument for argument
#: rather than against a rebuilt copy of itself.
_PRE_FIRE_OPTIONS: dict[str, ClaudeAgentOptions] = {
    "kodezart.adapters.claude_client_executor": ClaudeAgentOptions(
        cwd="/tmp/fake",
        permission_mode="plan",
        allowed_tools=[],
        resume=None,
        output_format=None,
        model=None,
        skills=map_skills(SUPPRESS_ALL_SKILLS),
        setting_sources=["user", "project", "local"],
    ),
    "kodezart.adapters.claude_agent_executor": ClaudeAgentOptions(
        cwd="/tmp/fake",
        permission_mode="plan",
        allowed_tools=[],
        resume=None,
        output_format=None,
        skills=map_skills(SUPPRESS_ALL_SKILLS),
        setting_sources=["user", "project", "local"],
    ),
}


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_a_granted_session_carries_the_knowledge_server(module) -> None:
    """The grant names the type, so the server definition is in the options."""
    grant = knowledge_grant_for(SessionType.TICKET_FIRE)

    options = await _options_for(
        module,
        grant=grant,
        session_type=SessionType.TICKET_FIRE,
    )

    assert isinstance(options.mcp_servers, dict)
    assert set(options.mcp_servers) == {FIXTURE_KNOWLEDGE_SERVER}
    definition = options.mcp_servers[FIXTURE_KNOWLEDGE_SERVER]
    assert definition["type"] == "http"
    assert definition["url"] == grant.server_url
    assert definition["headers"] == {
        grant.auth_header: f"{grant.auth_scheme} {grant.credential}",
    }


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
@pytest.mark.parametrize(
    "session_type",
    [
        SessionType.API_QUERY,
        SessionType.COMMIT_MESSAGE,
        SessionType.CONTENT_AUDIT,
    ],
)
async def test_every_non_granted_type_constructs_the_pre_fire_options(
    module,
    session_type,
) -> None:
    """Not a spot check: each type outside the grant, argument for argument.

    The baseline literal records the pre-grant construction and is never
    edited.  The working-directory guard is replaced onto it here because
    it is the one argument this lane adds to EVERY session rather than to
    a granted one — so the comparison still ranges over every argument,
    and still fails if any other one moved.
    """
    options = await _options_for(
        module,
        grant=knowledge_grant_for(SessionType.TICKET_FIRE),
        session_type=session_type,
    )

    assert options == replace(_PRE_FIRE_OPTIONS[module], strict_mcp_config=True)


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_the_strict_flag_rides_with_the_session_never_with_the_server(
    module,
) -> None:
    """The server rides with the grant; the guard rides with the session.

    The two are pinned against each other exactly as before, with the
    pairing inverted: a session the grant does not name still carries no
    server, and now carries the guard all the same.
    """
    grant = knowledge_grant_for(SessionType.TICKET_FIRE)

    granted = await _options_for(
        module,
        grant=grant,
        session_type=SessionType.TICKET_FIRE,
    )
    plain = await _options_for(
        module,
        grant=grant,
        session_type=SessionType.API_QUERY,
    )

    assert granted.strict_mcp_config is True
    assert plain.strict_mcp_config is True
    assert set(granted.mcp_servers or {}) == {FIXTURE_KNOWLEDGE_SERVER}
    assert plain.mcp_servers == _PRE_FIRE_OPTIONS[module].mcp_servers


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_an_empty_grant_configures_no_server_at_either_site(module) -> None:
    """The shipped grant names nothing, so no site configures a server.

    Every one of those sessions carries the working-directory guard even
    so, which is the shipped arrangement this issue exists to correct.
    """
    for session_type in SessionType:
        options = await _options_for(
            module,
            grant=NO_KNOWLEDGE_GRANT,
            session_type=session_type,
        )
        assert options.mcp_servers == {}
        assert options == replace(_PRE_FIRE_OPTIONS[module], strict_mcp_config=True)


async def test_the_unwired_executor_is_covered_by_the_same_grant_logic() -> None:
    """Absence from the composition root must not make it a hole.

    Asserted as identity of the mapping helper both adapters call, so the
    coverage cannot regress by one adapter growing its own copy.
    """
    agent_source = Path(
        inspect.getfile(ClaudeAgentExecutor),
    ).read_text(encoding="utf-8")
    client_source = Path(
        inspect.getfile(ClaudeClientExecutor),
    ).read_text(encoding="utf-8")

    for source in (agent_source, client_source):
        assert "map_knowledge_mcp(self._knowledge_grant, session_type)" in source

    granted = await _options_for(
        "kodezart.adapters.claude_agent_executor",
        grant=knowledge_grant_for(SessionType.TICKET_FIRE),
        session_type=SessionType.TICKET_FIRE,
    )
    assert granted.mcp_servers == {
        FIXTURE_KNOWLEDGE_SERVER: {
            "type": "http",
            "url": "https://knowledge.invalid/mcp",
            "headers": {"Authorization": f"Bearer {knowledge_grant_for().credential}"},
        },
    }


def test_a_grant_without_a_credential_never_builds_a_header() -> None:
    """The dead configuration fails loudly rather than dialling unauthenticated."""
    grant = KnowledgeGrant(
        granted=(SessionType.TICKET_FIRE,),
        server_name=FIXTURE_KNOWLEDGE_SERVER,
        server_url="https://knowledge.invalid/mcp",
        auth_header="Authorization",
        auth_scheme="Bearer",
        credential=None,
        knowledge_map=FIXTURE_KNOWLEDGE_MAP,
    )

    with pytest.raises(ValueError, match="carries no credential"):
        map_knowledge_mcp(grant, SessionType.TICKET_FIRE)


def test_the_mapping_describes_no_server_for_a_type_the_grant_does_not_name() -> None:
    """Empty of servers is the mechanism, and it is not the same as empty.

    The mapping the grant does not name still carries the guard, so the
    keyword reaches the session rather than the SDK default doing so.
    """
    mapped = map_knowledge_mcp(
        knowledge_grant_for(SessionType.TICKET_FIRE),
        SessionType.API_QUERY,
    )

    assert mapped == {"mcp_servers": {}, "strict_mcp_config": True}
