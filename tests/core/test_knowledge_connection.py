"""Typed knowledge configuration through the actual settings and grant boundary."""

import json

import pytest
from pydantic import ValidationError

from kodezart.adapters._mcp_mapping import map_knowledge_mcp
from kodezart.core.config import AppConfig
from kodezart.core.knowledge_settings import KnowledgeSettings
from kodezart.types.domain.session import (
    PACKAGE_RUNNER_COMMANDS,
    HttpKnowledge,
    KnowledgeGrant,
    SessionType,
    StdioKnowledge,
)

TOKEN = "ntn_" + "C" * 44
GATEWAY = "gw-" + "G" * 40
URL = "https://knowledge.invalid/mcp"
COMMAND = "/opt/knowledge/bin/server"


def http(**changes):
    return {"transport": "http", "server_url": URL, "credential": TOKEN, **changes}


def stdio(**changes):
    return {
        "transport": "stdio",
        "command": COMMAND,
        "credential": TOKEN,
        "credential_env": "SERVER_TOKEN",
        **changes,
    }


@pytest.mark.parametrize(
    "connection,kind", [(http(), HttpKnowledge), (stdio(), StdioKnowledge)]
)
def test_one_validated_connection_reaches_the_actual_mapping(connection, kind):
    config = AppConfig(
        knowledge={"session_grants": ["ticket_fire"], "connection": connection}
    )
    grant = config.knowledge.grant(knowledge_map="Recorded knowledge map")
    assert isinstance(grant.connection, kind)
    assert grant.connection is config.knowledge.connection
    assert map_knowledge_mcp(grant, SessionType.TICKET_FIRE)["mcp_servers"]
    assert map_knowledge_mcp(grant, SessionType.API_QUERY) == {
        "mcp_servers": {},
        "strict_mcp_config": True,
    }


def test_default_configuration_has_no_connection_or_grant():
    config = AppConfig(_env_file=None)
    assert config.knowledge == KnowledgeSettings()
    assert config.knowledge.connection is None
    for kind in SessionType:
        assert not config.knowledge.grant(knowledge_map="").grants(kind)


@pytest.mark.parametrize(
    "connection,field,value",
    [
        *[
            (http(), field, value)
            for field, value in [
                ("command", COMMAND),
                ("args", []),
                ("env", {}),
                ("credential_env", None),
                ("stderr_tail_limit", 2000),
            ]
        ],
        *[
            (stdio(), field, value)
            for field, value in [
                ("server_url", URL),
                ("auth_header", "Authorization"),
                ("auth_scheme", None),
                ("gateway_credential", GATEWAY),
                ("interactive_auth_hosts", []),
                ("sse_read_timeout_seconds", 300),
                ("timeout_seconds", 30),
            ]
        ],
    ],
)
def test_transport_rejects_even_explicit_empty_foreign_fields(connection, field, value):
    with pytest.raises(ValidationError, match=field):
        AppConfig(knowledge={"connection": {**connection, field: value}})


@pytest.mark.parametrize(
    "connection,missing",
    [(http(), "server_url"), (stdio(), "command"), (http(), "transport")],
)
def test_required_transport_fields_are_not_inferred(connection, missing):
    connection.pop(missing)
    with pytest.raises(ValidationError, match=missing):
        AppConfig(knowledge={"connection": connection})


@pytest.mark.parametrize(
    "command",
    ["server", *[f"/usr/bin/{name}" for name in sorted(PACKAGE_RUNNER_COMMANDS)]],
)
def test_stdio_still_requires_an_absolute_installed_server(command):
    with pytest.raises(ValidationError, match=r"absolute|package"):
        AppConfig(knowledge={"connection": stdio(command=command)})


@pytest.mark.parametrize(
    "changes",
    [
        {"credential": None},
        {"credential_env": None},
        {"env": {"SERVER_TOKEN": "other"}},
    ],
)
def test_stdio_credential_delivery_remains_coherent(changes):
    with pytest.raises(ValidationError, match="credential_env"):
        AppConfig(knowledge={"connection": stdio(**changes)})


@pytest.mark.parametrize("header", ["Authorization", "authorization", "AUTHORIZATION"])
def test_two_credentials_cannot_share_a_case_insensitive_http_header(header):
    with pytest.raises(ValidationError, match="collides"):
        AppConfig(
            knowledge={
                "connection": http(auth_header=header, gateway_credential=GATEWAY)
            }
        )


def test_interactive_hosts_still_refuse_static_authentication():
    with pytest.raises(ValidationError, match="interactively"):
        AppConfig(
            knowledge={
                "connection": http(
                    server_url="https://hosted.invalid/mcp",
                    interactive_auth_hosts=["hosted.invalid"],
                )
            }
        )


def test_gateway_and_upstream_credentials_survive_only_in_delivery_headers():
    config = AppConfig(
        knowledge={
            "session_grants": ["ticket_fire"],
            "connection": http(
                gateway_credential=GATEWAY, auth_header="X-Upstream", auth_scheme=None
            ),
        }
    )
    grant = config.knowledge.grant(knowledge_map="Map")
    assert grant.connection.headers() == {
        "Authorization": f"Bearer {GATEWAY}",
        "X-Upstream": TOKEN,
    }
    for value in (config, grant):
        for rendered in (repr(value), value.model_dump_json(), str(value.model_dump())):
            assert TOKEN not in rendered
            assert GATEWAY not in rendered


@pytest.mark.parametrize(
    "field,value",
    [
        ("timeout_seconds", 4),
        ("timeout_seconds", 121),
        ("sse_read_timeout_seconds", 29),
        ("sse_read_timeout_seconds", 3601),
    ],
)
def test_http_bounds_still_refuse_invalid_values(field, value):
    with pytest.raises(ValidationError, match=field):
        AppConfig(knowledge={"connection": http(**{field: value})})


def test_nested_env_overrides_json_and_preserves_normal_source_precedence(
    monkeypatch, tmp_path
):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "KODEZART_KNOWLEDGE="
        + json.dumps({"connection": http(), "server_name": "file"})
        + "\n"
    )
    monkeypatch.setenv(
        "KODEZART_KNOWLEDGE",
        json.dumps({"connection": http(), "server_name": "environment"}),
    )
    monkeypatch.setenv("KODEZART_KNOWLEDGE__CONNECTION__AUTH_SCHEME", "null")
    monkeypatch.setenv(
        "KODEZART_KNOWLEDGE__CONNECTION__SSE_READ_TIMEOUT_SECONDS", "450"
    )
    value = AppConfig(
        _env_file=env_file, knowledge={"server_name": "initializer"}
    ).knowledge
    assert value.server_name == "initializer"
    assert value.connection.auth_scheme is None
    assert value.connection.sse_read_timeout_seconds == 450
    assert value.connection.credential.get_secret_value() == TOKEN


@pytest.mark.parametrize(
    "name",
    [
        "KODEZART_KNOWLEDGE_MCP_TOKEN",
        "KODEZART_KNOWLEDGE_SESSION_GRANTS",
        "kodezart_knowledge_mcp_command",
    ],
)
@pytest.mark.parametrize("source", ["environment", "dotenv", "initializer"])
def test_removed_names_fail_instead_of_silently_disabling_knowledge(
    monkeypatch, tmp_path, name, source
):
    kwargs = {"_env_file": None}
    if source == "environment":
        monkeypatch.setenv(name, TOKEN)
    elif source == "dotenv":
        file = tmp_path / ".env"
        file.write_text(f"{name}={TOKEN}\n")
        kwargs["_env_file"] = file
    else:
        kwargs[name.removeprefix("KODEZART_").removeprefix("kodezart_").lower()] = TOKEN
    with pytest.raises(ValidationError) as caught:
        AppConfig(**kwargs)
    assert "knowledge" in str(caught.value).lower()
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize(
    "granted,map_text", [([SessionType.TICKET_FIRE], ""), ([], "orphan map")]
)
def test_the_knowledge_map_remains_bound_to_the_grant(granted, map_text):
    with pytest.raises(ValidationError, match="knowledge_map"):
        KnowledgeGrant(
            granted=granted,
            knowledge_map=map_text,
            server_name="server",
            connection=HttpKnowledge(**http()),
        )


def test_file_secret_source_stays_below_environment_and_initializer(
    monkeypatch, tmp_path
):
    (tmp_path / "kodezart_knowledge").write_text(
        json.dumps({"server_name": "secret-file", "connection": http()})
    )
    assert (
        AppConfig(_env_file=None, _secrets_dir=tmp_path).knowledge.server_name
        == "secret-file"
    )
    monkeypatch.setenv("KODEZART_KNOWLEDGE__SERVER_NAME", "environment")
    assert (
        AppConfig(_env_file=None, _secrets_dir=tmp_path).knowledge.server_name
        == "environment"
    )
    assert (
        AppConfig(
            _env_file=None,
            _secrets_dir=tmp_path,
            knowledge={"server_name": "initializer"},
        ).knowledge.server_name
        == "initializer"
    )


def test_http_defaults_retain_the_actual_record_transport_bounds():
    connection = AppConfig(knowledge={"connection": http()}).knowledge.connection
    assert connection.timeout_seconds == 30
    assert connection.sse_read_timeout_seconds == 300


@pytest.mark.parametrize("arm", [http, stdio])
def test_nested_unknown_fields_are_refused_without_exposing_credentials(
    monkeypatch, arm
):
    monkeypatch.setenv("KODEZART_KNOWLEDGE", json.dumps({"connection": arm()}))
    monkeypatch.setenv("KODEZART_KNOWLEDGE__CONNECTION__MISSPELLED", TOKEN)
    with pytest.raises(ValidationError) as caught:
        AppConfig()
    assert "misspelled" in str(caught.value).lower()
    assert TOKEN not in str(caught.value)
