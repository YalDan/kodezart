"""KOD-1239 — the operator's host-MCP opt-in, and the guard it switches off.

Every session runs with ``strict_mcp_config=True``: its working directory
is a cloned repository, and a ``.mcp.json`` committed there would otherwise
start a command on this machine.  Measured 2026-09-24 on the bundled Claude
Code, the same flag is what keeps a session from the servers the operator's
own user-level Claude configuration declares — the tracker, under the
operator's stored login.  ``dangerously_allow_host_mcp`` is the one switch
that trades the first for the second, for every session kind at once.

Each half of that trade is pinned here: off keeps the guard on every kind
(the shipped state); on takes it off every kind and changes nothing else
the mapping decides — the servers this process describes and the
knowledge-map prelude still follow the grant; boot says so once, as a
warning, and says nothing when the switch is off; and the composition root
hands the setting to the executor it builds and to that warning.
"""

import inspect
from pathlib import Path
from typing import cast

import pytest
import structlog
from claude_agent_sdk import ClaudeAgentOptions

from kodezart import main
from kodezart.adapters.mcp.mapping import map_knowledge_mcp
from kodezart.composition.preflight import (
    HOST_MCP_ALLOWED_EVENT,
    warn_host_mcp_opt_in,
)
from kodezart.config.agent import AgentSettings
from kodezart.config.app import AppConfig
from kodezart.core.logging import get_logger
from kodezart.types.domain.session import SessionType
from tests.fakes import (
    EXECUTOR_MODULES,
    FIXTURE_KNOWLEDGE_SERVER,
    NO_KNOWLEDGE_GRANT,
    knowledge_grant_for,
    recorded_session,
)

ENVIRONMENT_NAME = "KODEZART_AGENT__DANGEROUSLY_ALLOW_HOST_MCP"


def _repo_declaring_a_server(root: Path) -> Path:
    """A cloned-repository stand-in whose ``.mcp.json`` declares a server."""
    repo = root / "cloned-target"
    repo.mkdir()
    (repo / ".mcp.json").write_text(
        '{"mcpServers": {"planted": {"command": "/bin/false"}}}\n',
        encoding="utf-8",
    )
    return repo


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------


def test_the_opt_in_ships_off() -> None:
    """The shipped state is the guarded one, at the model and through boot."""
    assert AgentSettings().dangerously_allow_host_mcp is False
    assert AppConfig(_env_file=None).agent.dangerously_allow_host_mcp is False


def test_the_opt_in_is_read_under_its_nested_environment_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The name the operator sets is the agent group's, two underscores deep."""
    monkeypatch.setenv(ENVIRONMENT_NAME, "true")

    assert AppConfig(_env_file=None).agent.dangerously_allow_host_mcp is True


def test_the_setting_names_the_measured_risk() -> None:
    """What it costs is on the field, where an operator reading the model sees it."""
    described = AgentSettings.model_fields["dangerously_allow_host_mcp"].description

    assert described is not None
    assert ".mcp.json" in described
    assert "stored login" in described
    assert "2026-09-24" in described


# ---------------------------------------------------------------------------
# The mapping: off keeps every kind guarded, on unguards every kind alike
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("session_type", list(SessionType))
def test_off_keeps_the_guard_on_every_session_kind(
    session_type: SessionType,
) -> None:
    """Pinned per kind: the default and the explicit ``False`` are one answer."""
    mapped = map_knowledge_mcp(NO_KNOWLEDGE_GRANT, session_type)
    explicit = map_knowledge_mcp(
        NO_KNOWLEDGE_GRANT,
        session_type,
        dangerously_allow_host_mcp=False,
    )

    assert mapped["strict_mcp_config"] is True
    assert explicit == mapped


@pytest.mark.parametrize("session_type", list(SessionType))
def test_on_takes_the_guard_off_every_session_kind_and_nothing_else(
    session_type: SessionType,
) -> None:
    """The opt-in answers the guard alone: the described servers do not move."""
    grant = knowledge_grant_for(SessionType.TICKET_FIRE)
    guarded = map_knowledge_mcp(grant, session_type)
    opened = map_knowledge_mcp(grant, session_type, dangerously_allow_host_mcp=True)

    assert opened["strict_mcp_config"] is False
    assert opened["mcp_servers"] == guarded["mcp_servers"]


# ---------------------------------------------------------------------------
# The executors: the session gets the guard the executor was built with
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_a_session_carries_the_guard_its_executor_was_built_with(
    module: str,
    tmp_path: Path,
) -> None:
    """Both adapters, every kind, in a directory that declares a server.

    The SDK is what would read that file; this process only decides the
    flag.  So the assertion is about the option the session is handed:
    off is the guard, on is its absence, and the server map this process
    describes is empty either way — nothing here reads the file into it.
    """
    repo = _repo_declaring_a_server(tmp_path)

    for session_type in SessionType:
        off = cast(
            ClaudeAgentOptions,
            (
                await recorded_session(module, session_type=session_type, cwd=str(repo))
            ).options,
        )
        on = cast(
            ClaudeAgentOptions,
            (
                await recorded_session(
                    module,
                    session_type=session_type,
                    cwd=str(repo),
                    dangerously_allow_host_mcp=True,
                )
            ).options,
        )

        assert off.strict_mcp_config is True, session_type
        assert on.strict_mcp_config is False, session_type
        assert off.mcp_servers == {}, session_type
        assert on.mcp_servers == {}, session_type


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_the_knowledge_map_prelude_follows_the_server_not_the_guard(
    module: str,
) -> None:
    """With the guard off, the prelude still gates on the described server.

    A session the grant does not name gets its prompt back unchanged even
    though it is unguarded; a granted one gets the map, and the server.
    """
    grant = knowledge_grant_for(SessionType.TICKET_FIRE)

    ungranted = await recorded_session(
        module,
        grant=grant,
        session_type=SessionType.API_QUERY,
        dangerously_allow_host_mcp=True,
    )
    granted = await recorded_session(
        module,
        grant=grant,
        session_type=SessionType.TICKET_FIRE,
        dangerously_allow_host_mcp=True,
    )

    unguarded = cast(ClaudeAgentOptions, ungranted.options)
    attached = cast(ClaudeAgentOptions, granted.options)

    assert ungranted.prompt == "p"
    assert unguarded.mcp_servers == {}
    assert granted.prompt.startswith(grant.knowledge_map)
    assert isinstance(attached.mcp_servers, dict)
    assert set(attached.mcp_servers) == {FIXTURE_KNOWLEDGE_SERVER}
    assert attached.strict_mcp_config is False


# ---------------------------------------------------------------------------
# Boot: one warning when on, silence when off
# ---------------------------------------------------------------------------


async def test_boot_warns_once_when_the_opt_in_is_on() -> None:
    """The startup log names the decision and what it risks, as a warning."""
    with structlog.testing.capture_logs() as logs:
        await warn_host_mcp_opt_in(
            settings=AgentSettings(dangerously_allow_host_mcp=True),
            log=get_logger(__name__),
        )

    warned = [entry for entry in logs if entry["event"] == HOST_MCP_ALLOWED_EVENT]
    assert len(warned) == 1
    assert warned[0]["log_level"] == "warning"
    assert ".mcp.json" in warned[0]["risk"]
    assert "stored login" in warned[0]["risk"]


async def test_boot_says_nothing_when_the_opt_in_is_off() -> None:
    """Off is the shipped state, and the shipped state has no risk to name."""
    with structlog.testing.capture_logs() as logs:
        await warn_host_mcp_opt_in(settings=AgentSettings(), log=get_logger(__name__))

    assert logs == []


def test_the_composition_root_wires_the_setting_and_the_warning() -> None:
    """The executor the root builds is given the setting, and boot warns on it."""
    source = Path(inspect.getfile(main)).read_text(encoding="utf-8")

    assert "dangerously_allow_host_mcp=config.agent.dangerously_allow_host_mcp" in (
        source
    )
    assert "await warn_host_mcp_opt_in(settings=config.agent, log=log)" in source
