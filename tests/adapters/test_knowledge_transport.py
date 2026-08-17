"""Each transport renders its own session definition — and only a live one.

The mapping is the one reader of the grant's connection fields, and the one
layer every granted session passes before the SDK receives its options.
That makes it the enforcement point for the shapes the grant can express:
the stdio definition, the gateway bearer, the two-header pass-through, the
raw scheme-less header — and the refusal of a static credential aimed at a
host that only authenticates interactively.
"""

from typing import Final

import pytest

from kodezart.adapters._mcp_mapping import map_knowledge_mcp
from kodezart.types.domain.session import (
    KnowledgeGrant,
    KnowledgeTransport,
    SessionType,
)
from tests.fakes import EXECUTOR_MODULES, recorded_session

_CREDENTIAL: Final[str] = "ntn_" + ("T" * 44)
_GATEWAY_CREDENTIAL: Final[str] = "gw-" + ("H" * 40)
_MAP: Final[str] = "── transport fixture map ──"
_SERVER: Final[str] = "fixture-knowledge"
_COMMAND: Final[str] = "/opt/knowledge/bin/knowledge-mcp-server"
_SELF_HOSTED_URL: Final[str] = "https://knowledge.invalid/mcp"
_INTERACTIVE_HOST: Final[str] = "hosted.invalid"


def _http_grant(**overrides: object) -> KnowledgeGrant:
    fields: dict[str, object] = {
        "granted": (SessionType.TICKET_FIRE,),
        "server_name": _SERVER,
        "server_url": _SELF_HOSTED_URL,
        "auth_header": "Authorization",
        "auth_scheme": "Bearer",
        "credential": _CREDENTIAL,
        "knowledge_map": _MAP,
    }
    fields.update(overrides)
    return KnowledgeGrant.model_validate(fields)


def _stdio_grant(**overrides: object) -> KnowledgeGrant:
    fields: dict[str, object] = {
        "granted": (SessionType.TICKET_FIRE,),
        "transport": KnowledgeTransport.STDIO,
        "server_name": _SERVER,
        "command": _COMMAND,
        "args": ("--stdio",),
        "env": {"LOG_LEVEL": "debug"},
        "credential_env": "KNOWLEDGE_TOKEN",
        "credential": _CREDENTIAL,
        "knowledge_map": _MAP,
    }
    fields.update(overrides)
    return KnowledgeGrant.model_validate(fields)


# ---------------------------------------------------------------------------
# KOD-129-AC-2 — the stdio definition, exactly as the SDK receives it
# ---------------------------------------------------------------------------


def test_a_stdio_grant_renders_the_stdio_definition() -> None:
    """Command, args and env — the credential delivered as one env entry."""
    mapped = map_knowledge_mcp(_stdio_grant(), SessionType.TICKET_FIRE)

    assert mapped["strict_mcp_config"] is True
    assert mapped["mcp_servers"] == {
        _SERVER: {
            "type": "stdio",
            "command": _COMMAND,
            "args": ["--stdio"],
            "env": {"LOG_LEVEL": "debug", "KNOWLEDGE_TOKEN": _CREDENTIAL},
        },
    }


def test_the_stdio_definition_carries_no_url_key_at_all() -> None:
    """No URL is the shape of the route, not an empty placeholder."""
    mapped = map_knowledge_mcp(_stdio_grant(), SessionType.TICKET_FIRE)

    definition = mapped["mcp_servers"][_SERVER]
    assert "url" not in definition
    assert "headers" not in definition


def test_a_session_outside_a_stdio_grant_still_gets_nothing_and_the_guard() -> None:
    """The grant decision is transport-independent."""
    mapped = map_knowledge_mcp(_stdio_grant(), SessionType.API_QUERY)

    assert mapped["mcp_servers"] == {}
    assert mapped["strict_mcp_config"] is True


def test_a_stdio_grant_without_a_credential_refuses() -> None:
    """The unauthenticated spawn is refused exactly as the dial is."""
    grant = _stdio_grant(credential=None)

    with pytest.raises(ValueError, match="carries no credential"):
        map_knowledge_mcp(grant, SessionType.TICKET_FIRE)


def test_a_stdio_credential_without_its_delivery_entry_refuses() -> None:
    """A credential with nowhere to land is half a shape, not a default."""
    grant = _stdio_grant(credential_env=None)

    with pytest.raises(ValueError, match="CREDENTIAL_ENV"):
        map_knowledge_mcp(grant, SessionType.TICKET_FIRE)


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_a_stdio_grant_round_trips_into_both_executors_options(
    module: str,
) -> None:
    """KOD-129-AC-2: configuration to SDK options, through the real adapters."""
    session = await recorded_session(
        module,
        grant=_stdio_grant(),
        session_type=SessionType.TICKET_FIRE,
    )

    assert session.options.strict_mcp_config is True
    assert session.options.mcp_servers == {
        _SERVER: {
            "type": "stdio",
            "command": _COMMAND,
            "args": ["--stdio"],
            "env": {"LOG_LEVEL": "debug", "KNOWLEDGE_TOKEN": _CREDENTIAL},
        },
    }
    assert session.prompt.startswith(_MAP)


# ---------------------------------------------------------------------------
# KOD-129-AC-3 — the header shapes the HTTP arm can express
# ---------------------------------------------------------------------------


def test_a_gateway_credential_alone_is_a_bearer_to_the_self_hosted_server() -> None:
    """The self-hosted single-layer shape: the server holds its own upstream."""
    grant = _http_grant(credential=None, gateway_credential=_GATEWAY_CREDENTIAL)

    mapped = map_knowledge_mcp(grant, SessionType.TICKET_FIRE)

    assert mapped["mcp_servers"] == {
        _SERVER: {
            "type": "http",
            "url": _SELF_HOSTED_URL,
            "headers": {"Authorization": f"Bearer {_GATEWAY_CREDENTIAL}"},
        },
    }


def test_the_two_header_pass_through_sends_both_credentials_at_once() -> None:
    """KOD-129-AC-3: gateway bearer plus upstream token, one request."""
    grant = _http_grant(
        gateway_credential=_GATEWAY_CREDENTIAL,
        auth_header="X-Upstream-Token",
        auth_scheme=None,
    )

    mapped = map_knowledge_mcp(grant, SessionType.TICKET_FIRE)

    assert mapped["mcp_servers"][_SERVER]["headers"] == {
        "Authorization": f"Bearer {_GATEWAY_CREDENTIAL}",
        "X-Upstream-Token": _CREDENTIAL,
    }


def test_a_non_authorization_header_with_no_scheme_prefix_is_expressible() -> None:
    """KOD-129-AC-3: the raw-credential header shape."""
    grant = _http_grant(auth_header="X-API-Key", auth_scheme=None)

    mapped = map_knowledge_mcp(grant, SessionType.TICKET_FIRE)

    assert mapped["mcp_servers"][_SERVER]["headers"] == {"X-API-Key": _CREDENTIAL}


def test_both_credentials_into_one_header_refuses_naming_the_collision() -> None:
    """The gateway owns its header; a pass-through must name a different one."""
    grant = _http_grant(gateway_credential=_GATEWAY_CREDENTIAL)

    with pytest.raises(ValueError, match="gateway credential owns"):
        map_knowledge_mcp(grant, SessionType.TICKET_FIRE)


def test_a_credential_with_no_header_to_ride_in_refuses() -> None:
    """Half a shape: the value exists and its presentation does not."""
    grant = _http_grant(auth_header=None, auth_scheme=None)

    with pytest.raises(ValueError, match="AUTH_HEADER"):
        map_knowledge_mcp(grant, SessionType.TICKET_FIRE)


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_the_two_header_shape_round_trips_into_both_executors(
    module: str,
) -> None:
    """KOD-129-AC-2 + AC-3 together, through the real adapters."""
    session = await recorded_session(
        module,
        grant=_http_grant(
            gateway_credential=_GATEWAY_CREDENTIAL,
            auth_header="X-Upstream-Token",
            auth_scheme=None,
        ),
        session_type=SessionType.TICKET_FIRE,
    )

    assert session.options.strict_mcp_config is True
    assert session.options.mcp_servers == {
        _SERVER: {
            "type": "http",
            "url": _SELF_HOSTED_URL,
            "headers": {
                "Authorization": f"Bearer {_GATEWAY_CREDENTIAL}",
                "X-Upstream-Token": _CREDENTIAL,
            },
        },
    }


# ---------------------------------------------------------------------------
# KOD-129-AC-1 — a static credential aimed at an interactive-auth host
# ---------------------------------------------------------------------------


def test_a_static_credential_aimed_at_an_interactive_host_refuses() -> None:
    """The dead combination cannot reach a session, per the recorded ruling.

    The refusal names the host and the environment variables in conflict —
    this is the layer the fire-ruling of 2026-08-17 places it at, because
    every earlier layer is pinned legal by the lane's protected suites.
    """
    grant = _http_grant(
        server_url=f"https://{_INTERACTIVE_HOST}/mcp",
        interactive_auth_hosts=(_INTERACTIVE_HOST,),
    )

    with pytest.raises(ValueError) as excinfo:
        map_knowledge_mcp(grant, SessionType.TICKET_FIRE)

    reported = str(excinfo.value)
    assert _INTERACTIVE_HOST in reported
    assert "KODEZART_KNOWLEDGE_MCP_SERVER_URL" in reported
    assert "KODEZART_KNOWLEDGE_MCP_TOKEN" in reported


def test_a_gateway_credential_aimed_at_an_interactive_host_also_refuses() -> None:
    """Any statically composed header is dead against an interactive host."""
    grant = _http_grant(
        credential=None,
        gateway_credential=_GATEWAY_CREDENTIAL,
        server_url=f"https://{_INTERACTIVE_HOST}/mcp",
        interactive_auth_hosts=(_INTERACTIVE_HOST,),
    )

    with pytest.raises(ValueError, match="interactively"):
        map_knowledge_mcp(grant, SessionType.TICKET_FIRE)


def test_the_same_credential_against_a_self_hosted_url_maps_cleanly() -> None:
    """The control: the refusal is the host, never the credential."""
    grant = _http_grant(interactive_auth_hosts=(_INTERACTIVE_HOST,))

    mapped = map_knowledge_mcp(grant, SessionType.TICKET_FIRE)

    assert set(mapped["mcp_servers"]) == {_SERVER}


# ---------------------------------------------------------------------------
# KOD-129-AC-1 + AC-2 through the real configuration origin
# ---------------------------------------------------------------------------


def test_the_shipped_default_endpoint_with_a_static_credential_never_maps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-1 wired through AppConfig: the dead combination refuses before
    any session receives it, naming the shipped host and the variables."""
    from kodezart.core.config import AppConfig

    monkeypatch.setenv("KODEZART_KNOWLEDGE_SESSION_GRANTS", '["ticket_fire"]')
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_TOKEN", _CREDENTIAL)

    grant = AppConfig().knowledge_grant(knowledge_map=_MAP)

    with pytest.raises(ValueError) as excinfo:
        map_knowledge_mcp(grant, SessionType.TICKET_FIRE)

    reported = str(excinfo.value)
    assert "mcp.notion.com" in reported
    assert "KODEZART_KNOWLEDGE_MCP_SERVER_URL" in reported
    assert "KODEZART_KNOWLEDGE_MCP_TOKEN" in reported


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_a_stdio_route_round_trips_from_the_environment_to_the_sdk(
    monkeypatch: pytest.MonkeyPatch,
    module: str,
) -> None:
    """AC-2 end to end: env vars to AppConfig to grant to SDK options."""
    from kodezart.core.config import AppConfig

    monkeypatch.setenv("KODEZART_KNOWLEDGE_SESSION_GRANTS", '["ticket_fire"]')
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_COMMAND", _COMMAND)
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_ARGS", '["--stdio"]')
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_CREDENTIAL_ENV", "KNOWLEDGE_TOKEN")
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_TOKEN", _CREDENTIAL)

    grant = AppConfig().knowledge_grant(knowledge_map=_MAP)
    session = await recorded_session(
        module,
        grant=grant,
        session_type=SessionType.TICKET_FIRE,
    )

    assert session.options.strict_mcp_config is True
    assert session.options.mcp_servers == {
        "notion": {
            "type": "stdio",
            "command": _COMMAND,
            "args": ["--stdio"],
            "env": {"KNOWLEDGE_TOKEN": _CREDENTIAL},
        },
    }


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_a_self_hosted_http_route_round_trips_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
    module: str,
) -> None:
    """AC-2 end to end for the http arm, against a self-hosted endpoint."""
    from kodezart.core.config import AppConfig

    monkeypatch.setenv("KODEZART_KNOWLEDGE_SESSION_GRANTS", '["ticket_fire"]')
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_SERVER_URL", _SELF_HOSTED_URL)
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_GATEWAY_TOKEN", _GATEWAY_CREDENTIAL)
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_AUTH_HEADER", "X-Upstream-Token")
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_AUTH_SCHEME", "null")
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_TOKEN", _CREDENTIAL)

    grant = AppConfig().knowledge_grant(knowledge_map=_MAP)
    session = await recorded_session(
        module,
        grant=grant,
        session_type=SessionType.TICKET_FIRE,
    )

    assert session.options.strict_mcp_config is True
    assert session.options.mcp_servers == {
        "notion": {
            "type": "http",
            "url": _SELF_HOSTED_URL,
            "headers": {
                "Authorization": f"Bearer {_GATEWAY_CREDENTIAL}",
                "X-Upstream-Token": _CREDENTIAL,
            },
        },
    }
