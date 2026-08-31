"""The knowledge connection's working shapes — routes, refusals, hygiene.

The grant carries its transport explicitly, and each route carries its own
fields and only its own.  Every half-specified shape is a typed refusal
naming what is missing; nothing is ignored, defaulted sideways, or read by
nobody.  The shipped default configuration stays exactly as shipped.
"""

from typing import Final

import pytest
from pydantic import ValidationError

from kodezart.types.domain.session import (
    PACKAGE_RUNNER_COMMANDS,
    KnowledgeGrant,
    KnowledgeTransport,
    SessionType,
)

#: Built by concatenation so ruff S105 (hardcoded-password-string) does not
#: trip over a literal credential-shaped value, matching the convention of
#: the credential suites.
_CREDENTIAL: Final[str] = "ntn_" + ("C" * 44)
_GATEWAY_CREDENTIAL: Final[str] = "gw-" + ("G" * 40)
_MAP: Final[str] = "── fixture map ──"
_SERVER_COMMAND: Final[str] = "/opt/knowledge/bin/knowledge-mcp-server"


def _http_grant(**overrides: object) -> KnowledgeGrant:
    """A coherent HTTP-shaped grant the assertions can perturb."""
    fields: dict[str, object] = {
        "granted": (SessionType.TICKET_FIRE,),
        "server_name": "fixture-knowledge",
        "server_url": "https://knowledge.invalid/mcp",
        "auth_header": "Authorization",
        "auth_scheme": "Bearer",
        "credential": _CREDENTIAL,
        "knowledge_map": _MAP,
    }
    fields.update(overrides)
    return KnowledgeGrant.model_validate(fields)


def _stdio_grant(**overrides: object) -> KnowledgeGrant:
    """A coherent stdio-shaped grant the assertions can perturb."""
    fields: dict[str, object] = {
        "granted": (SessionType.TICKET_FIRE,),
        "transport": KnowledgeTransport.STDIO,
        "server_name": "fixture-knowledge",
        "command": _SERVER_COMMAND,
        "args": ("--stdio",),
        "credential_env": "KNOWLEDGE_TOKEN",
        "credential": _CREDENTIAL,
        "knowledge_map": _MAP,
    }
    fields.update(overrides)
    return KnowledgeGrant.model_validate(fields)


# ---------------------------------------------------------------------------
# The route is a stated fact, never an inference
# ---------------------------------------------------------------------------


def test_the_default_transport_is_the_shipped_http_shape() -> None:
    """A grant that says nothing about its route carries the shipped one."""
    grant = _http_grant()

    assert grant.transport is KnowledgeTransport.HTTP


def test_both_documented_transports_are_expressible() -> None:
    """The vendor's two client shapes each construct as a coherent grant."""
    assert _http_grant().transport is KnowledgeTransport.HTTP
    assert _stdio_grant().transport is KnowledgeTransport.STDIO


def test_a_stdio_grant_carries_no_url_at_all() -> None:
    """The stdio route has no endpoint — absence, not an ignored default."""
    grant = _stdio_grant()

    assert grant.server_url is None
    assert grant.auth_header is None
    assert grant.auth_scheme is None


# ---------------------------------------------------------------------------
# Half-specified shapes are typed refusals naming what is missing
# ---------------------------------------------------------------------------


def test_an_http_grant_without_a_url_refuses_naming_it() -> None:
    """The endpoint is owed to the sessions the grant names."""
    with pytest.raises(ValidationError, match="server_url"):
        _http_grant(server_url=None)


def test_an_http_grant_naming_no_session_needs_no_url() -> None:
    """The shipped shape: nothing is granted, so nothing would be dialled."""
    grant = _http_grant(granted=(), knowledge_map="", server_url=None)

    assert grant.granted == ()
    assert grant.server_url is None


def test_a_stdio_grant_without_a_command_refuses_naming_it() -> None:
    with pytest.raises(ValidationError, match="command"):
        _stdio_grant(command=None)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command", _SERVER_COMMAND),
        ("credential_env", "KNOWLEDGE_TOKEN"),
        ("args", ("--stdio",)),
        ("env", {"LOG_LEVEL": "debug"}),
    ],
)
def test_a_stdio_field_on_an_http_grant_refuses_naming_it(
    field: str,
    value: object,
) -> None:
    """A member the declared route never reads is dialled by nothing."""
    with pytest.raises(ValidationError, match=field):
        _http_grant(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("server_url", "https://knowledge.invalid/mcp"),
        ("auth_header", "Authorization"),
        ("auth_scheme", "Bearer"),
        ("gateway_credential", _GATEWAY_CREDENTIAL),
        ("interactive_auth_hosts", ("hosted.invalid",)),
    ],
)
def test_an_http_field_on_a_stdio_grant_refuses_naming_it(
    field: str,
    value: object,
) -> None:
    """A stdio server has no endpoint and no headers to read these into."""
    with pytest.raises(ValidationError, match=field):
        _stdio_grant(**{field: value})


# ---------------------------------------------------------------------------
# The stdio command resolves nowhere but itself
# ---------------------------------------------------------------------------


def test_a_relative_stdio_command_refuses() -> None:
    """Fire sessions run in cloned working directories an attacker authored."""
    with pytest.raises(ValidationError, match="absolute"):
        _stdio_grant(command="knowledge-mcp-server")


@pytest.mark.parametrize("runner", sorted(PACKAGE_RUNNER_COMMANDS))
def test_every_package_runner_command_refuses_by_basename(runner: str) -> None:
    """A runner resolves or fetches its payload at spawn time — refused."""
    with pytest.raises(ValidationError, match="package"):
        _stdio_grant(command=f"/usr/local/bin/{runner}")


def test_an_absolute_installed_binary_is_accepted() -> None:
    """The refusal is the runner family, not the transport."""
    assert _stdio_grant().command == _SERVER_COMMAND


# ---------------------------------------------------------------------------
# Credential hygiene extends to every secret-bearing field
# ---------------------------------------------------------------------------


def test_neither_credential_survives_grant_serialization() -> None:
    """Both secret-bearing fields are excluded from both serializations."""
    grant = _http_grant(gateway_credential=_GATEWAY_CREDENTIAL)

    assert grant.credential == _CREDENTIAL
    assert grant.gateway_credential == _GATEWAY_CREDENTIAL
    assert _CREDENTIAL not in grant.model_dump_json()
    assert _GATEWAY_CREDENTIAL not in grant.model_dump_json()
    assert _CREDENTIAL not in str(grant.model_dump())
    assert _GATEWAY_CREDENTIAL not in str(grant.model_dump())


def test_the_two_layer_shape_is_expressible_on_the_grant() -> None:
    """A gateway credential and an upstream one ride one value together."""
    grant = _http_grant(
        gateway_credential=_GATEWAY_CREDENTIAL,
        auth_header="X-Upstream-Token",
        auth_scheme=None,
    )

    assert grant.gateway_credential == _GATEWAY_CREDENTIAL
    assert grant.credential == _CREDENTIAL
    assert grant.auth_header == "X-Upstream-Token"
    assert grant.auth_scheme is None


def test_a_scheme_less_header_is_expressible_on_the_grant() -> None:
    """A raw credential in a named header, with no scheme prefix."""
    grant = _http_grant(auth_header="X-API-Key", auth_scheme=None)

    assert grant.auth_header == "X-API-Key"
    assert grant.auth_scheme is None


# ---------------------------------------------------------------------------
# The configuration layer — env-sourced shapes and their boot refusals
# ---------------------------------------------------------------------------


_TRANSPORT_VAR: Final[str] = "KODEZART_KNOWLEDGE_MCP_TRANSPORT"
_TOKEN_VAR: Final[str] = "KODEZART_KNOWLEDGE_MCP_TOKEN"
_GATEWAY_VAR: Final[str] = "KODEZART_KNOWLEDGE_MCP_GATEWAY_TOKEN"
_COMMAND_VAR: Final[str] = "KODEZART_KNOWLEDGE_MCP_COMMAND"
_CREDENTIAL_ENV_VAR: Final[str] = "KODEZART_KNOWLEDGE_MCP_CREDENTIAL_ENV"
_URL_VAR: Final[str] = "KODEZART_KNOWLEDGE_MCP_SERVER_URL"
_HOSTS_VAR: Final[str] = "KODEZART_KNOWLEDGE_MCP_INTERACTIVE_AUTH_HOSTS"
_SELF_HOSTED_URL: Final[str] = "https://knowledge.invalid/mcp"
_INTERACTIVE_HOST: Final[str] = "hosted.invalid"


def _stdio_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fully specified stdio route in the environment."""
    monkeypatch.setenv(_TRANSPORT_VAR, "stdio")
    monkeypatch.setenv(_COMMAND_VAR, _SERVER_COMMAND)
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_ARGS", '["--stdio"]')
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_ENV", '{"LOG_LEVEL": "debug"}')
    monkeypatch.setenv(_CREDENTIAL_ENV_VAR, "KNOWLEDGE_TOKEN")
    monkeypatch.setenv(_TOKEN_VAR, _CREDENTIAL)


def test_the_transport_resolves_from_its_env_var_and_ships_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kodezart.core.config import AppConfig

    assert AppConfig().knowledge_mcp_transport is KnowledgeTransport.HTTP
    monkeypatch.setenv(_TRANSPORT_VAR, "stdio")
    monkeypatch.setenv(_COMMAND_VAR, _SERVER_COMMAND)
    assert AppConfig().knowledge_mcp_transport is KnowledgeTransport.STDIO


def test_the_shipped_default_configuration_is_exactly_as_shipped() -> None:
    """No new field changes what a fresh deployment starts from."""
    from kodezart.core.config import AppConfig

    config = AppConfig()

    assert config.knowledge_mcp_server_name == "notion"
    assert config.knowledge_mcp_server_url is None
    assert config.knowledge_mcp_auth_header == "Authorization"
    assert config.knowledge_mcp_auth_scheme == "Bearer"
    assert config.knowledge_mcp_transport is KnowledgeTransport.HTTP
    assert config.knowledge_mcp_token is None
    assert config.knowledge_mcp_gateway_token is None
    assert config.knowledge_mcp_command is None
    assert config.knowledge_mcp_args == []
    assert config.knowledge_mcp_env == {}
    assert config.knowledge_mcp_credential_env is None
    assert config.knowledge_session_grants == []


def test_a_full_stdio_route_resolves_into_the_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KOD-129-AC-2, configuration half: every stdio field threads through."""
    from kodezart.core.config import AppConfig

    _stdio_env(monkeypatch)
    monkeypatch.setenv("KODEZART_KNOWLEDGE_SESSION_GRANTS", '["ticket_fire"]')

    grant = AppConfig().knowledge_grant(knowledge_map=_MAP)

    assert grant.transport is KnowledgeTransport.STDIO
    assert grant.command == _SERVER_COMMAND
    assert grant.args == ("--stdio",)
    assert grant.env == {"LOG_LEVEL": "debug"}
    assert grant.credential_env == "KNOWLEDGE_TOKEN"
    assert grant.credential == _CREDENTIAL
    assert grant.server_url is None
    assert grant.auth_header is None
    assert grant.auth_scheme is None


def test_a_null_auth_scheme_loads_as_absence_not_a_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KOD-129-AC-3: the scheme-less header is expressible from the env."""
    from kodezart.core.config import AppConfig

    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_AUTH_SCHEME", "null")
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_AUTH_HEADER", "X-API-Key")

    config = AppConfig()

    assert config.knowledge_mcp_auth_scheme is None
    assert config.knowledge_mcp_auth_header == "X-API-Key"


def test_the_gateway_credential_is_env_sourced_and_never_serialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new secret-bearing field carries the same hygiene as the first."""
    import json

    from kodezart.core.config import AppConfig

    monkeypatch.setenv(_GATEWAY_VAR, _GATEWAY_CREDENTIAL)

    config = AppConfig()

    assert config.knowledge_mcp_gateway_token == _GATEWAY_CREDENTIAL
    assert _GATEWAY_CREDENTIAL not in json.dumps(config.model_dump(mode="json"))
    assert _GATEWAY_CREDENTIAL not in config.model_dump_json()
    monkeypatch.delenv(_GATEWAY_VAR)
    assert AppConfig().knowledge_mcp_gateway_token is None


def test_no_credential_value_appears_in_any_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The repr surface carries no secret either, on config or on grant.

    A model repr reaches tracebacks, debuggers and log payloads by paths
    that never call a serializer, so exclusion alone leaves it uncovered.
    """
    from kodezart.core.config import AppConfig

    monkeypatch.setenv(_TOKEN_VAR, _CREDENTIAL)
    monkeypatch.setenv(_GATEWAY_VAR, _GATEWAY_CREDENTIAL)

    config = AppConfig()
    grant = _http_grant(gateway_credential=_GATEWAY_CREDENTIAL)

    assert config.knowledge_mcp_token == _CREDENTIAL
    assert grant.gateway_credential == _GATEWAY_CREDENTIAL
    assert _CREDENTIAL not in repr(config)
    assert _GATEWAY_CREDENTIAL not in repr(config)
    assert _CREDENTIAL not in repr(grant)
    assert _GATEWAY_CREDENTIAL not in repr(grant)


async def test_no_boot_log_line_carries_the_gateway_credential(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from kodezart.main import create_app, lifespan

    monkeypatch.setenv(_GATEWAY_VAR, _GATEWAY_CREDENTIAL)

    app = create_app()
    async with lifespan(app):
        pass

    emitted = capsys.readouterr().out + capsys.readouterr().err
    assert '"event"' in emitted
    assert _GATEWAY_CREDENTIAL not in emitted


def test_a_gateway_credential_satisfies_the_grant_credential_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A self-hosted server holding its own upstream token needs no second."""
    from kodezart.core.config import AppConfig

    monkeypatch.setenv("KODEZART_KNOWLEDGE_SESSION_GRANTS", '["ticket_fire"]')
    monkeypatch.setenv(_URL_VAR, _SELF_HOSTED_URL)
    monkeypatch.setenv(_GATEWAY_VAR, _GATEWAY_CREDENTIAL)
    monkeypatch.delenv(_TOKEN_VAR, raising=False)

    grant = AppConfig().knowledge_grant(knowledge_map=_MAP)

    assert grant.gateway_credential == _GATEWAY_CREDENTIAL
    assert grant.credential is None


def test_a_grant_with_neither_credential_still_aborts_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The amended cross-field rule names both variables."""
    from kodezart.core.config import AppConfig

    monkeypatch.setenv("KODEZART_KNOWLEDGE_SESSION_GRANTS", '["ticket_fire"]')
    monkeypatch.delenv(_TOKEN_VAR, raising=False)
    monkeypatch.delenv(_GATEWAY_VAR, raising=False)

    with pytest.raises(ValidationError) as excinfo:
        AppConfig()

    reported = str(excinfo.value)
    assert _TOKEN_VAR in reported
    assert _GATEWAY_VAR in reported


@pytest.mark.parametrize(
    ("var", "value"),
    [
        (_COMMAND_VAR, "/opt/knowledge/bin/server"),
        (_CREDENTIAL_ENV_VAR, "KNOWLEDGE_TOKEN"),
        ("KODEZART_KNOWLEDGE_MCP_ARGS", '["--stdio"]'),
        ("KODEZART_KNOWLEDGE_MCP_ENV", '{"LOG_LEVEL": "debug"}'),
    ],
)
def test_a_stdio_field_under_the_http_transport_aborts_boot_naming_it(
    monkeypatch: pytest.MonkeyPatch,
    var: str,
    value: str,
) -> None:
    from kodezart.core.config import AppConfig

    monkeypatch.setenv(var, value)
    if var == _CREDENTIAL_ENV_VAR:
        monkeypatch.setenv(_TOKEN_VAR, _CREDENTIAL)

    with pytest.raises(ValidationError, match=var):
        AppConfig()


@pytest.mark.parametrize(
    "var",
    [
        "KODEZART_KNOWLEDGE_MCP_SERVER_URL",
        "KODEZART_KNOWLEDGE_MCP_AUTH_HEADER",
        "KODEZART_KNOWLEDGE_MCP_AUTH_SCHEME",
    ],
)
def test_an_http_field_explicitly_set_under_stdio_aborts_boot_naming_it(
    monkeypatch: pytest.MonkeyPatch,
    var: str,
) -> None:
    """Explicitly set is the offence; the inert shipped default is not."""
    from kodezart.core.config import AppConfig

    _stdio_env(monkeypatch)
    monkeypatch.setenv(var, "Bearer" if "SCHEME" in var else "X-Value")

    with pytest.raises(ValidationError, match=var):
        AppConfig()


def test_the_inert_http_defaults_are_legal_under_stdio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control for the explicit-set rule: defaults an operator never
    wrote do not abort a stdio boot."""
    from kodezart.core.config import AppConfig

    _stdio_env(monkeypatch)

    assert AppConfig().knowledge_mcp_transport is KnowledgeTransport.STDIO


def test_a_gateway_credential_under_stdio_aborts_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kodezart.core.config import AppConfig

    _stdio_env(monkeypatch)
    monkeypatch.setenv(_GATEWAY_VAR, _GATEWAY_CREDENTIAL)

    with pytest.raises(ValidationError, match=_GATEWAY_VAR):
        AppConfig()


def test_a_stdio_transport_without_a_command_aborts_boot_naming_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kodezart.core.config import AppConfig

    monkeypatch.setenv(_TRANSPORT_VAR, "stdio")

    with pytest.raises(ValidationError, match=_COMMAND_VAR):
        AppConfig()


def test_a_stdio_credential_without_its_delivery_entry_aborts_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kodezart.core.config import AppConfig

    monkeypatch.setenv(_TRANSPORT_VAR, "stdio")
    monkeypatch.setenv(_COMMAND_VAR, _SERVER_COMMAND)
    monkeypatch.setenv(_TOKEN_VAR, _CREDENTIAL)

    with pytest.raises(ValidationError, match=_CREDENTIAL_ENV_VAR):
        AppConfig()


def test_a_delivery_entry_without_a_credential_aborts_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kodezart.core.config import AppConfig

    monkeypatch.setenv(_TRANSPORT_VAR, "stdio")
    monkeypatch.setenv(_COMMAND_VAR, _SERVER_COMMAND)
    monkeypatch.setenv(_CREDENTIAL_ENV_VAR, "KNOWLEDGE_TOKEN")
    monkeypatch.delenv(_TOKEN_VAR, raising=False)

    with pytest.raises(ValidationError, match="no credential to deliver"):
        AppConfig()


def test_a_delivery_entry_colliding_with_a_declared_env_member_aborts_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kodezart.core.config import AppConfig

    _stdio_env(monkeypatch)
    monkeypatch.setenv(
        "KODEZART_KNOWLEDGE_MCP_ENV",
        '{"KNOWLEDGE_TOKEN": "not-the-secret"}',
    )

    with pytest.raises(ValidationError, match="two writers"):
        AppConfig()


def test_a_relative_command_is_refused_when_the_grant_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command-safety rules live on the grant value and fire at boot."""
    from kodezart.core.config import AppConfig

    _stdio_env(monkeypatch)
    monkeypatch.setenv(_COMMAND_VAR, "knowledge-mcp-server")

    with pytest.raises(ValidationError, match="absolute"):
        AppConfig().knowledge_grant(knowledge_map=_MAP)


def test_a_package_runner_command_is_refused_when_the_grant_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kodezart.core.config import AppConfig

    _stdio_env(monkeypatch)
    monkeypatch.setenv(_COMMAND_VAR, "/usr/local/bin/npx")

    with pytest.raises(ValidationError, match="package"):
        AppConfig().knowledge_grant(knowledge_map=_MAP)


def test_a_granted_http_route_without_an_endpoint_aborts_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No endpoint ships, so a granted http deployment must name one."""
    from kodezart.core.config import AppConfig

    monkeypatch.setenv("KODEZART_KNOWLEDGE_SESSION_GRANTS", '["ticket_fire"]')
    monkeypatch.setenv(_TOKEN_VAR, _CREDENTIAL)
    monkeypatch.delenv(_URL_VAR, raising=False)

    with pytest.raises(ValidationError, match="server_url"):
        AppConfig().knowledge_grant(knowledge_map=_MAP)


def test_a_static_credential_aimed_at_an_interactive_host_aborts_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dead combination is refused where the grant resolves, at boot.

    The refusal names the host and every variable in the conflict, because
    no credential value rescues it: the endpoint has to move, or the route
    has to change.
    """
    from kodezart.core.config import AppConfig

    monkeypatch.setenv("KODEZART_KNOWLEDGE_SESSION_GRANTS", '["ticket_fire"]')
    monkeypatch.setenv(_HOSTS_VAR, f'["{_INTERACTIVE_HOST}"]')
    monkeypatch.setenv(_URL_VAR, f"https://{_INTERACTIVE_HOST}/mcp")
    monkeypatch.setenv(_TOKEN_VAR, _CREDENTIAL)

    with pytest.raises(ValidationError) as excinfo:
        AppConfig().knowledge_grant(knowledge_map=_MAP)

    reported = str(excinfo.value)
    assert _INTERACTIVE_HOST in reported
    assert _URL_VAR in reported
    assert _TOKEN_VAR in reported
    assert _GATEWAY_VAR in reported


def test_a_gateway_credential_aimed_at_an_interactive_host_also_aborts_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any statically composed header is dead against such a host."""
    from kodezart.core.config import AppConfig

    monkeypatch.setenv("KODEZART_KNOWLEDGE_SESSION_GRANTS", '["ticket_fire"]')
    monkeypatch.setenv(_HOSTS_VAR, f'["{_INTERACTIVE_HOST}"]')
    monkeypatch.setenv(_URL_VAR, f"https://{_INTERACTIVE_HOST}/mcp")
    monkeypatch.setenv(_GATEWAY_VAR, _GATEWAY_CREDENTIAL)
    monkeypatch.delenv(_TOKEN_VAR, raising=False)

    with pytest.raises(ValidationError, match="interactively"):
        AppConfig().knowledge_grant(knowledge_map=_MAP)


def test_an_interactive_host_granted_to_nobody_is_a_legal_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal is grant-conditioned: nothing dials, nothing is dead."""
    from kodezart.core.config import AppConfig

    monkeypatch.setenv("KODEZART_KNOWLEDGE_SESSION_GRANTS", "[]")
    monkeypatch.setenv(_HOSTS_VAR, f'["{_INTERACTIVE_HOST}"]')
    monkeypatch.setenv(_URL_VAR, f"https://{_INTERACTIVE_HOST}/mcp")
    monkeypatch.setenv(_TOKEN_VAR, _CREDENTIAL)

    grant = AppConfig().knowledge_grant(knowledge_map="")

    assert grant.granted == ()


def test_the_same_credential_against_a_self_hosted_url_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control: the refusal is the host, never the credential."""
    from kodezart.core.config import AppConfig

    monkeypatch.setenv("KODEZART_KNOWLEDGE_SESSION_GRANTS", '["ticket_fire"]')
    monkeypatch.setenv(_HOSTS_VAR, f'["{_INTERACTIVE_HOST}"]')
    monkeypatch.setenv(_URL_VAR, _SELF_HOSTED_URL)
    monkeypatch.setenv(_TOKEN_VAR, _CREDENTIAL)

    grant = AppConfig().knowledge_grant(knowledge_map=_MAP)

    assert grant.server_url == _SELF_HOSTED_URL
    assert grant.credential == _CREDENTIAL


# ---------------------------------------------------------------------------
# KOD-129-AC-4 — absence is named at boot, and nothing substitutes for it
# ---------------------------------------------------------------------------


async def test_an_unconfigured_knowledge_store_is_named_at_boot_and_starts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: object,
) -> None:
    """Boot names the unused capability, the service starts, and no local
    file is written as a substitute."""
    from pathlib import Path

    from kodezart.main import create_app, lifespan

    assert isinstance(tmp_path, Path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KODEZART_KNOWLEDGE_SESSION_GRANTS", raising=False)
    monkeypatch.delenv(_TOKEN_VAR, raising=False)
    monkeypatch.delenv(_GATEWAY_VAR, raising=False)

    app = create_app()
    async with lifespan(app):
        assert app.state.workflow_engine is not None

    emitted = capsys.readouterr().out + capsys.readouterr().err
    assert "knowledge_capability_unconfigured" in emitted
    assert list(tmp_path.iterdir()) == []
