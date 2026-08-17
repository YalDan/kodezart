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
    with pytest.raises(ValidationError, match="server_url"):
        _http_grant(server_url=None)


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
