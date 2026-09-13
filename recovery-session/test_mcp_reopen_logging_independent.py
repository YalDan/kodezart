"""External native failure locals must not hold the host's reopen path."""

import asyncio
import threading
from contextlib import nullcontext

import pytest

from kodezart.core.errors import McpCallUnansweredError
from tests.adapters.test_http_mcp_tool_caller import (
    _CallBehaviour,
    _FakeStreamableServer,
    _HANG_CEILING_SECONDS,
    caller_fixture,
    client_over,
)
from tests.core.test_logging_chain import configured_chain


def awaited_names(task):
    current = task.get_coro()
    names = []
    while current is not None:
        code = getattr(current, "cr_code", None)
        if code is not None:
            names.append(code.co_name)
        current = getattr(current, "cr_await", None)
    return names


@pytest.mark.parametrize("logging_mode", ["default", "pretty", "json"])
async def test_reopen_does_not_render_live_failure_locals(logging_mode):
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    repr_calls = []

    class ExternalServer(_FakeStreamableServer):
        def __repr__(self):
            repr_calls.append(threading.get_ident())
            loop.call_soon_threadsafe(entered.set)
            release.wait(timeout=_HANG_CEILING_SECONDS)
            return "external-live-native-server"

    server = ExternalServer(on_call=_CallBehaviour.DROPS_ONCE)
    chain = (
        nullcontext()
        if logging_mode == "default"
        else configured_chain(pretty=logging_mode == "pretty")
    )
    first = None
    waiting = None
    evidence = {}
    with chain:
        caller = caller_fixture(client_factory=client_over(server.transport))
        await caller.open()
        try:
            with pytest.raises(McpCallUnansweredError):
                await caller.call_tool(name="get_issue", arguments={})
            first = asyncio.create_task(
                caller.call_tool(name="get_issue", arguments={})
            )
            waiting = asyncio.create_task(entered.wait())
            async with asyncio.timeout(_HANG_CEILING_SECONDS):
                await asyncio.wait(
                    {first, waiting}, return_when=asyncio.FIRST_COMPLETED
                )
            if entered.is_set():
                # The external object's repr is held in the logging worker.
                # Inspect the real host/reopen chain before letting it finish.
                evidence = {
                    "initialize_count": server.requests.count("initialize"),
                    "reopen": awaited_names(first),
                    "tasks": [awaited_names(task) for task in asyncio.all_tasks()],
                }
                assert not first.done()
                assert server.requests.count("initialize") == 1
            release.set()
            async with asyncio.timeout(_HANG_CEILING_SECONDS):
                assert await first == {"id": "K-1"}
            assert server.requests.count("initialize") == 2
            assert server.calls == ["tools/call", "tools/call"]
        finally:
            release.set()
            if waiting is not None:
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
            if first is not None:
                await asyncio.gather(first, return_exceptions=True)
            await caller.close()
    assert repr_calls == [], evidence
