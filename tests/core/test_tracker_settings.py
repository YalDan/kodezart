"""Tracker settings reach the native HTTP client and one adapter retry policy."""

import asyncio
import json
from http import HTTPStatus

import httpx
import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsError

from kodezart.composition.tracker import boot_tracker, make_mcp_tool_caller
from kodezart.core.config import AppConfig
from kodezart.core.errors import (
    McpCredentialRefusedError,
    McpTransportError,
    TrackerCredentialShapeError,
    TrackerUnavailableError,
)
from kodezart.core.logging import get_logger
from tests.adapters.test_http_mcp_tool_caller import _FakeStreamableServer
from tests.core.test_retired_config import _from_source
from tests.tracker.conftest import AGENT_IDENTITY, CLAIMED_ISSUE, fixture_server
from tests.tracker.test_tracker_boot import operation_config

TOKEN = "lin_api_" + "Q7" * 24
OLD = {
    "tracker_mcp_server_name": "fixture-server",
    "tracker_mcp_server_url": "https://tracker.invalid/custom",
    "tracker_mcp_auth_header": "X-Credential",
    "tracker_mcp_auth_scheme": "Token",
    "tracker_token": TOKEN,
    "tracker_timeout_seconds": 9.0,
    "tracker_mcp_call_timeout_seconds": 2.0,
    "tracker_mcp_sse_read_timeout_seconds": 47.0,
    "tracker_mcp_error_detail_limit": 91,
    "tracker_max_retries": 2,
    "tracker_retry_backoff_factor": 0.5,
}
VALUES = {
    "backend": "linear",
    "server_name": "fixture-server",
    "server_url": "https://tracker.invalid/custom",
    "auth_header": "X-Credential",
    "auth_scheme": "Token",
    "token": TOKEN,
    "timeout_seconds": 9.0,
    "call_timeout_seconds": 2.0,
    "sse_read_timeout_seconds": 47.0,
    "error_detail_limit": 91,
    "max_retries": 2,
    "retry_backoff_factor": 0.5,
}


def configured(source, tmp_path, monkeypatch):
    if source == "init":
        return AppConfig(_env_file=None, tracker=VALUES)
    if source == "env":
        for key, value in VALUES.items():
            monkeypatch.setenv("KODEZART_TRACKER__" + key.upper(), str(value))
        return AppConfig(_env_file=None)
    if source == "dotenv":
        path = tmp_path / ".env"
        path.write_text(
            "\n".join(
                f"KODEZART_TRACKER__{key.upper()}={value}"
                for key, value in VALUES.items()
            )
        )
        return AppConfig(_env_file=path)
    (tmp_path / "KODEZART_TRACKER").write_text(json.dumps(VALUES))
    return AppConfig(_env_file=None, _secrets_dir=tmp_path)


class NativeEndpoint(_FakeStreamableServer):
    def __init__(self, monkeypatch, status=HTTPStatus.OK):
        super().__init__(initialize_status=status)
        self.native = fixture_server(actor=AGENT_IDENTITY)
        self.wire = []
        self.clients = []
        self.hold = asyncio.Event()
        self.fail_get_issue = 0
        original = httpx.AsyncClient

        def client(**kwargs):
            result = original(transport=self.transport(), **kwargs)
            self.clients.append(result)
            return result

        monkeypatch.setattr(httpx, "AsyncClient", client)

    async def _answer(self, request):
        self.wire.append(request)
        message = json.loads(request.content)
        if message.get("method") == "tools/call":
            self.requests.append("tools/call")
            params = message["params"]
            if params["name"] == "never_answers":
                await self.hold.wait()
                return self._frame(message["id"], {"content": []})
            if params["name"] == "tool_error":
                return self._frame(
                    message["id"],
                    {"isError": True, "content": [{"type": "text", "text": "E" * 500}]},
                )
            if params["name"] == "get_issue" and self.fail_get_issue:
                self.fail_get_issue -= 1
                # An answered tool error leaves this native session open, so the
                # count measures adapter retries without session reopen attempts.
                return self._frame(
                    message["id"],
                    {
                        "isError": True,
                        "content": [{"type": "text", "text": "try again"}],
                    },
                )
            result = await self.native.call_tool(
                name=params["name"], arguments=params.get("arguments", {})
            )
            return self._frame(
                message["id"],
                {"content": [{"type": "text", "text": json.dumps(result)}]},
            )
        return await super()._answer(request)


@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
async def test_four_sources_reach_native_http_boot_and_issue_read(
    source, tmp_path, monkeypatch
):
    config = configured(source, tmp_path, monkeypatch)
    endpoint = NativeEndpoint(monkeypatch)
    dialled = await boot_tracker(
        settings=config.tracker, operation=operation_config(), log=get_logger(__name__)
    )
    assert dialled is not None
    try:
        issue = await dialled.tracker.read_issue(issue_key=CLAIMED_ISSUE)
        assert issue.issue_key == CLAIMED_ISSUE
        assert endpoint.requests[:3] == [
            "initialize",
            "initialize",
            "notifications/initialized",
        ]
        assert endpoint.native.tool_calls("get_issue")
        for request in endpoint.wire:
            assert str(request.url) == VALUES["server_url"]
            assert request.headers["X-Credential"] == "Token " + TOKEN
            assert "authorization" not in request.headers
        # These are native httpx client values, after the production factory.
        assert endpoint.clients[0].timeout.connect == 9
        assert endpoint.clients[0].timeout.read == 9
        assert endpoint.clients[1].timeout.connect == 9
        assert endpoint.clients[1].timeout.read == 47
        assert dialled.caller._server.call_timeout().total_seconds() == 2
        assert dialled.caller._server._error_detail_limit == 91
        assert "token" not in config.model_dump()["tracker"]
        assert "token" not in json.loads(config.model_dump_json())["tracker"]
        assert TOKEN not in config.model_dump_json()
        assert TOKEN not in repr(config)
    finally:
        await dialled.caller.close()
    assert all(client.is_closed for client in endpoint.clients)


@pytest.mark.parametrize(
    "status, error",
    [
        (HTTPStatus.UNAUTHORIZED, McpCredentialRefusedError),
        (HTTPStatus.FORBIDDEN, McpTransportError),
    ],
)
async def test_refused_native_probe_keeps_server_identity_and_no_open(
    status, error, tmp_path, monkeypatch
):
    config = configured("init", tmp_path, monkeypatch)
    endpoint = NativeEndpoint(monkeypatch, status)
    with pytest.raises(error) as caught:
        await boot_tracker(
            settings=config.tracker,
            operation=operation_config(),
            log=get_logger(__name__),
        )
    assert caught.value.server_name == "fixture-server"
    assert endpoint.requests == ["initialize"]
    assert all(client.is_closed for client in endpoint.clients)


async def test_expiring_credential_is_refused_before_native_http(tmp_path, monkeypatch):
    endpoint = NativeEndpoint(monkeypatch)
    config = AppConfig(_env_file=None, tracker={"token": "lin_oauth_fixture"})
    with pytest.raises(TrackerCredentialShapeError) as caught:
        await boot_tracker(
            settings=config.tracker,
            operation=operation_config(),
            log=get_logger(__name__),
        )
    assert caught.value.field == "KODEZART_TRACKER__TOKEN"
    assert endpoint.wire == [] and endpoint.clients == []


@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
@pytest.mark.parametrize("field", OLD)
def test_retired_tracker_fields_refuse(source, field, tmp_path, monkeypatch):
    with pytest.raises(ValidationError, match="Extra inputs") as caught:
        _from_source(source, field, OLD[field], tmp_path, monkeypatch)
    assert field in str(caught.value).lower()
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
def test_old_scalar_selector_refuses_as_group_shape(source, tmp_path, monkeypatch):
    with pytest.raises((ValidationError, SettingsError)) as caught:
        _from_source(source, "tracker", "linear", tmp_path, monkeypatch)
    assert "tracker" in str(caught.value).lower()


@pytest.mark.parametrize(
    "values",
    [
        {"backend": "unshipped"},
        {"tokne": "hidden-value"},
        {"auth_header": ""},
        {"auth_scheme": ""},
        {"timeout_seconds": 4},
        {"timeout_seconds": 121},
        {"call_timeout_seconds": 0},
        {"call_timeout_seconds": 121},
        {"sse_read_timeout_seconds": 29},
        {"sse_read_timeout_seconds": 3601},
        {"error_detail_limit": 79},
        {"error_detail_limit": 8001},
        {"max_retries": -1},
        {"max_retries": 11},
        {"retry_backoff_factor": 0},
        {"retry_backoff_factor": 31},
    ],
)
def test_nested_tracker_boundaries_refuse(values):
    with pytest.raises(ValidationError) as caught:
        AppConfig(_env_file=None, tracker=values)
    assert "hidden-value" not in str(caught.value)


def test_defaults_and_existing_source_precedence(tmp_path, monkeypatch):
    defaults = AppConfig(_env_file=None).tracker
    assert defaults.backend.value == "linear"
    assert defaults.server_name == "linear"
    assert defaults.server_url == "https://mcp.linear.app/mcp"
    assert (defaults.auth_header, defaults.auth_scheme, defaults.token) == (
        "Authorization",
        "Bearer",
        None,
    )
    assert (
        defaults.timeout_seconds,
        defaults.call_timeout_seconds,
        defaults.sse_read_timeout_seconds,
    ) == (30, 60, 300)
    assert (
        defaults.error_detail_limit,
        defaults.max_retries,
        defaults.retry_backoff_factor,
    ) == (500, 3, 1)
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "KODEZART_TRACKER").write_text(json.dumps({"server_name": "secret"}))
    dotenv = tmp_path / ".env"
    dotenv.write_text("KODEZART_TRACKER__SERVER_NAME=dotenv\n")
    monkeypatch.setenv("KODEZART_TRACKER__SERVER_NAME", "env")
    kwargs = {"_env_file": dotenv, "_secrets_dir": secrets}
    assert (
        AppConfig(**kwargs, tracker={"server_name": "init"}).tracker.server_name
        == "init"
    )
    assert AppConfig(**kwargs).tracker.server_name == "env"
    monkeypatch.delenv("KODEZART_TRACKER__SERVER_NAME")
    assert AppConfig(**kwargs).tracker.server_name == "dotenv"
    assert (
        AppConfig(_env_file=None, _secrets_dir=secrets).tracker.server_name == "secret"
    )


async def test_configured_call_deadline_ends_a_native_unanswered_call(
    tmp_path, monkeypatch
):
    endpoint = NativeEndpoint(monkeypatch)
    config = AppConfig(_env_file=None, tracker={"call_timeout_seconds": 1.0})
    caller = make_mcp_tool_caller(settings=config.tracker, token=TOKEN)
    await caller.open()
    try:
        async with asyncio.timeout(5):
            with pytest.raises(McpTransportError):
                await caller.call_tool(name="never_answers", arguments={})
    finally:
        endpoint.hold.set()
        await caller.close()
    assert all(client.is_closed for client in endpoint.clients)


async def test_configured_detail_bound_truncates_the_native_server_error(
    tmp_path, monkeypatch
):
    endpoint = NativeEndpoint(monkeypatch)
    config = configured("init", tmp_path, monkeypatch)
    caller = make_mcp_tool_caller(settings=config.tracker, token=TOKEN)
    await caller.open()
    try:
        with pytest.raises(McpTransportError) as caught:
            await caller.call_tool(name="tool_error", arguments={})
        assert caught.value.server_name == "fixture-server"
        assert "E" * 88 in str(caught.value)
        assert "E" * 92 not in str(caught.value)
    finally:
        await caller.close()
    assert all(client.is_closed for client in endpoint.clients)


@pytest.mark.parametrize("retries", [0, 2])
async def test_boot_constructs_shared_retry_for_native_answered_errors(
    retries, tmp_path, monkeypatch
):
    endpoint = NativeEndpoint(monkeypatch)
    config = AppConfig(_env_file=None, tracker={**VALUES, "max_retries": retries})
    dialled = await boot_tracker(
        settings=config.tracker, operation=operation_config(), log=get_logger(__name__)
    )
    assert dialled is not None
    waits = []
    original_sleep = asyncio.sleep

    async def sleep(seconds):
        if seconds:
            waits.append(seconds)
        await original_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    endpoint.fail_get_issue = 10
    before = len(endpoint.wire)
    try:
        with pytest.raises(TrackerUnavailableError):
            await dialled.tracker.read_issue(issue_key=CLAIMED_ISSUE)
    finally:
        await dialled.caller.close()
    calls = [json.loads(request.content) for request in endpoint.wire[before:]]
    assert (
        len([row for row in calls if row.get("params", {}).get("name") == "get_issue"])
        == retries + 1
    )
    assert waits == ([0.5, 1.0] if retries else [])
