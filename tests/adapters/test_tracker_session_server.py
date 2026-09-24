"""KOD-846 clause 5 — the pass sessions get kodezart's own tracker server.

Measured 2026-09-23: a grooming or fire-prep session got 0 Linear tools
under ``strict_mcp_config``, and the only way around it was the host opt-in,
which hands a session the tracker under the operator's stored login.  The
deployment's own server definition — the URL, the server identity and
``KODEZART_TRACKER__TOKEN`` the tracker client dials — is now built once at
the composition root and described to the scheduled pass alone, so a pass
works the board under this deployment's key and budget.  Pinned here: the
definition is the client's own, spelled once; the scheduled pass is the one
kind given it; the guard and the knowledge grant are untouched by it; the
prelude follows the knowledge server and not the map's truthiness; both
executors hand it to the session; and the root builds it from the settings.
"""

import inspect
from pathlib import Path

import pytest
from pydantic import SecretStr

from kodezart import main
from kodezart.adapters.mcp.mapping import (
    TrackerSessionServer,
    map_knowledge_mcp,
    prompt_with_knowledge_map,
)
from kodezart.composition import tracker as tracker_composition
from kodezart.composition.tracker import (
    make_mcp_tool_caller,
    tracker_auth_headers,
    tracker_session_server,
)
from kodezart.config.tracker import TrackerSettings
from kodezart.types.domain.session import SessionType
from tests.fakes import (
    EXECUTOR_MODULES,
    FIXTURE_KNOWLEDGE_SERVER,
    NO_KNOWLEDGE_GRANT,
    knowledge_grant_for,
    recorded_session,
)

TOKEN = "lin_api_" + "k" * 40
SETTINGS = TrackerSettings(token=SecretStr(TOKEN))


def _server() -> TrackerSessionServer:
    server = tracker_session_server(settings=SETTINGS)
    assert server is not None
    return server


def test_no_credential_describes_no_server() -> None:
    """The tracker is unwired without a key, and so are the pass sessions."""
    assert TrackerSettings().token is None
    assert tracker_session_server(settings=TrackerSettings()) is None


def test_the_session_server_is_the_client_definition_spelled_once() -> None:
    """URL, identity and credential header are the tracker client's own."""
    server = _server()
    assert server.server_name == SETTINGS.server_name
    assert server.definition == {
        "type": "http",
        "url": SETTINGS.server_url,
        "headers": tracker_auth_headers(settings=SETTINGS, token=TOKEN),
    }
    assert server.definition["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    # The client dials the same header through the same helper.
    source = inspect.getsource(make_mcp_tool_caller)
    assert "tracker_auth_headers(settings=settings, token=token)" in source
    assert inspect.getmodule(make_mcp_tool_caller) is tracker_composition


@pytest.mark.parametrize("session_type", list(SessionType))
def test_only_the_scheduled_pass_is_given_the_tracker_server(
    session_type: SessionType,
) -> None:
    """Every other kind's map is as it was, and the guard stays on for all."""
    server = _server()
    mapped = map_knowledge_mcp(NO_KNOWLEDGE_GRANT, session_type, tracker=server)
    expected = (
        {server.server_name: server.definition}
        if session_type is SessionType.SCHEDULED_PASS
        else {}
    )
    assert mapped["mcp_servers"] == expected
    assert mapped["strict_mcp_config"] is True
    assert map_knowledge_mcp(NO_KNOWLEDGE_GRANT, session_type)["mcp_servers"] == {}


def test_the_tracker_server_sits_beside_the_granted_knowledge_server() -> None:
    grant = knowledge_grant_for(SessionType.SCHEDULED_PASS)
    server = _server()
    mapped = map_knowledge_mcp(grant, SessionType.SCHEDULED_PASS, tracker=server)
    assert set(mapped["mcp_servers"]) == {FIXTURE_KNOWLEDGE_SERVER, server.server_name}


def test_the_prelude_follows_the_knowledge_server_and_not_the_maps_truthiness() -> None:
    """A pass given the tracker alone is told nothing about the knowledge store."""
    server = _server()
    tracker_only = map_knowledge_mcp(
        NO_KNOWLEDGE_GRANT, SessionType.SCHEDULED_PASS, tracker=server
    )
    assert tracker_only["mcp_servers"]
    assert (
        prompt_with_knowledge_map("p", grant=NO_KNOWLEDGE_GRANT, attached=tracker_only)
        == "p"
    )
    granted = knowledge_grant_for(SessionType.SCHEDULED_PASS)
    both = map_knowledge_mcp(granted, SessionType.SCHEDULED_PASS, tracker=server)
    assert prompt_with_knowledge_map("p", grant=granted, attached=both).endswith("p")
    assert prompt_with_knowledge_map("p", grant=granted, attached=both) != "p"


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_a_scheduled_pass_session_carries_the_tracker_server(module: str) -> None:
    """Both adapters hand the server to the pass session and to no fire."""
    server = _server()
    scheduled = await recorded_session(
        module, session_type=SessionType.SCHEDULED_PASS, tracker_server=server
    )
    assert scheduled.options.mcp_servers == {server.server_name: server.definition}
    assert scheduled.options.strict_mcp_config is True
    fire = await recorded_session(
        module, session_type=SessionType.TICKET_FIRE, tracker_server=server
    )
    assert fire.options.mcp_servers == {}


def test_the_composition_root_builds_the_server_from_the_tracker_settings() -> None:
    source = Path(inspect.getfile(main)).read_text(encoding="utf-8")
    assert "tracker_server=tracker_session_server(settings=config.tracker)" in source
