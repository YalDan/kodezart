"""Independent extracted-tree identity, transport and cancellation controls."""

import asyncio
from collections.abc import Mapping

import pytest

from kodezart.core.errors import (
    McpCallUnansweredError,
    McpCredentialRefusedError,
    TrackerAccessDeniedError,
    TrackerUnavailableError,
)
from kodezart.core.protocols import McpToolResult
from kodezart.domain.errors import ScopeReadError
from tests.tracker.conftest import linear_over_fake_mcp
from tests.tracker.test_linear_mcp_tracker import tracker_over
from tests.tracker.test_scope_reads import INITIATIVE, PROJECT, ROOT, ScopeMcpServer


async def test_native_ancestor_edge_cannot_be_satisfied_by_another_identity():
    server = ScopeMcpServer()
    server.initiatives[INITIATIVE.key]["id"] = "foreign-initiative"
    tracker = linear_over_fake_mcp(server)
    with pytest.raises(ScopeReadError):
        await tracker.container_metadata(ref=PROJECT)
    assert not server.tool_calls("save_issue")


class _RefusingBoundary(ScopeMcpServer):
    def __init__(self, failure):
        super().__init__()
        self.failure = failure
        self.attempts = 0

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        self.attempts += 1
        raise self.failure


@pytest.mark.parametrize("credential", [False, True])
async def test_native_failure_preserves_exact_cause_and_never_repeats_unsafe_write(
    credential,
):
    if credential:
        failure = McpCredentialRefusedError(
            "authority refused", server_name="scope-test", tool_name="save_issue"
        )
        expected = TrackerAccessDeniedError
    else:
        failure = McpCallUnansweredError(
            "write receipt absent", server_name="scope-test", tool_name="save_issue"
        )
        expected = TrackerUnavailableError
    boundary = _RefusingBoundary(failure)
    tracker = tracker_over(boundary, max_retries=4)
    with pytest.raises(expected) as caught:
        await tracker.update_issue(issue_key=ROOT.key, title="changed")
    assert caught.value.__cause__ is failure
    assert boundary.attempts == 1


class _PausedPage(ScopeMcpServer):
    def __init__(self):
        super().__init__()
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.pause = True

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        if self.pause and name == "list_issues" and "cursor" in arguments:
            self.waiting.set()
            await self.release.wait()
        return await super().call_tool(name=name, arguments=arguments)


async def test_cancelled_page_read_does_not_return_partial_or_poison_next_scope_read():
    boundary = _PausedPage()
    tracker = linear_over_fake_mcp(boundary)
    task = asyncio.create_task(tracker.scope_issues(ref=PROJECT))
    await asyncio.wait_for(boundary.waiting.wait(), timeout=2)
    task.cancel("scope-cancel")
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert caught.value.args == ("scope-cancel",)
    boundary.pause = False
    rows = await tracker.scope_issues(ref=PROJECT)
    assert {row.issue_key for row in rows} == {ROOT.key, "FIX-2", "FIX-4"}
    assert not boundary.tool_calls("save_issue")
