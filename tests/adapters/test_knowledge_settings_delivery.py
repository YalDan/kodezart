"""Configured knowledge credentials reach both real consumer boundaries."""

import asyncio
import json
import sys

import httpx
import pytest

from kodezart.adapters._mcp_mapping import map_knowledge_mcp
from kodezart.composition.records import _knowledge_caller
from kodezart.core.config import AppConfig
from kodezart.types.domain.session import SessionType
from tests.fakes import EXECUTOR_MODULES, recorded_session, write_stdio_fake_server

TOKEN = "ntn_" + "T" * 44
GATEWAY = "gw-" + "H" * 40


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
@pytest.mark.parametrize(
    "credential,gateway,scheme",
    [
        (TOKEN, None, "Bearer"),
        (TOKEN, None, None),
        (None, GATEWAY, "Bearer"),
        (TOKEN, GATEWAY, None),
    ],
)
async def test_sdk_and_programmatic_probe_deliver_identical_headers(
    monkeypatch, module, credential, gateway, scheme
):
    monkeypatch.setenv(
        "KODEZART_KNOWLEDGE",
        json.dumps(
            {
                "session_grants": ["ticket_fire"],
                "server_name": "knowledge-native",
                "connection": {
                    "transport": "http",
                    "server_url": "https://knowledge.invalid/mcp",
                    "credential": credential,
                    "gateway_credential": gateway,
                    "auth_header": "X-Upstream",
                    "auth_scheme": scheme,
                },
            }
        ),
    )
    settings = AppConfig().knowledge
    grant = settings.grant(knowledge_map="Actual map")
    session = await recorded_session(
        module, grant=grant, session_type=SessionType.TICKET_FIRE
    )
    wanted = {}
    if gateway is not None:
        wanted["Authorization"] = "Bearer " + gateway
    if credential is not None:
        wanted["X-Upstream"] = (
            credential if scheme is None else scheme + " " + credential
        )
    assert session.options.mcp_servers[settings.server_name]["headers"] == wanted
    requests = []

    def answer(request):
        requests.append(request)
        return httpx.Response(200)

    def client_factory(**kwargs):
        return httpx.AsyncClient(**kwargs, transport=httpx.MockTransport(answer))

    caller = _knowledge_caller(settings, ["records.fire"])
    caller._server._client_factory = client_factory
    await caller.probe()
    assert len(requests) == 1
    for header, value in wanted.items():
        assert requests[0].headers[header] == value
    assert ("Authorization" in requests[0].headers) == (gateway is not None)
    assert ("X-Upstream" in requests[0].headers) == (credential is not None)
    assert json.loads(requests[0].content)["method"] == "initialize"


async def test_stdio_settings_deliver_credentials_to_the_actual_child(
    monkeypatch, tmp_path
):
    server = write_stdio_fake_server(tmp_path)
    observed = tmp_path / "credential.json"
    wrapper = tmp_path / "launch.py"
    wrapper.write_text(
        "import json, os, runpy\n" + f"open({str(observed)!r}, 'w').write(json.dumps("
        "{'credential': os.environ['SERVER_TOKEN'], "
        "'mode': os.environ['MODE']}))\n"
        + f"runpy.run_path({str(server)!r}, run_name='__main__')\n"
    )
    monkeypatch.setenv(
        "KODEZART_KNOWLEDGE",
        json.dumps(
            {
                "session_grants": ["ticket_fire"],
                "connection": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(wrapper)],
                    "credential": TOKEN,
                    "credential_env": "SERVER_TOKEN",
                    "env": {
                        "MODE": "explicit",
                        "FAKE_MCP_SPAWN_LOG": str(tmp_path / "spawn.log"),
                        "FAKE_MCP_CALLS": "10",
                    },
                },
            }
        ),
    )
    settings = AppConfig().knowledge
    grant = settings.grant(knowledge_map="Map")
    definition = map_knowledge_mcp(grant, SessionType.TICKET_FIRE)["mcp_servers"][
        settings.server_name
    ]
    assert definition["env"]["SERVER_TOKEN"] == TOKEN
    assert definition["env"]["MODE"] == "explicit"
    caller = _knowledge_caller(settings, ["records.fire"])
    async with asyncio.timeout(10):
        try:
            await caller.open()
            result = await caller.call_tool(name="append-block", arguments={})
            assert not result.get("isError", False)
        finally:
            await caller.close()
    assert json.loads(observed.read_text()) == {"credential": TOKEN, "mode": "explicit"}
