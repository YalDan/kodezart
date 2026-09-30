"""A native save omits history that the full issue read still reports."""

import asyncio
from collections.abc import Mapping

import pytest

from kodezart.core.errors import McpSessionClosedError
from kodezart.core.protocols import McpToolResult
from kodezart.types.domain.dispatch import PassSignal, SelfWriteLedger
from kodezart.types.domain.operation import LifecycleStage
from tests.adapters.test_tracker_self_writes import (
    ISSUE,
    TEAM_KEY,
    _gate,
    _server,
    _tracker,
)
from tests.fakes import FakeLinearMcpServer


class SaveWithoutHistory:
    def __init__(self, server: FakeLinearMcpServer, *, omit: bool):
        self.server = server
        self.omit = omit
        self.saved: list[dict[str, object]] = []

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        answer = await self.server.call_tool(name=name, arguments=arguments)
        if name != "save_issue":
            return answer
        assert isinstance(answer, Mapping)
        returned = dict(answer)
        if self.omit:
            returned.pop("stateHistory")
        self.saved.append(returned)
        return returned


@pytest.mark.parametrize("omit", [False, True])
@pytest.mark.parametrize("comment_after_state", [False, True])
async def test_own_state_then_comment_retains_native_history_without_a_wake(
    omit, comment_after_state
):
    server = _server()
    boundary = SaveWithoutHistory(server, omit=omit)
    ledger = SelfWriteLedger()
    tracker = _tracker(boundary, ledger)
    gate = _gate(tracker, ledger)
    initial = await tracker.read_issue_movement(issue_key=ISSUE)
    assert "stateHistory" in dict(initial.fields)
    assert (await gate.delta()).changed == (ISSUE,)
    original_mark = gate.mark(PassSignal.approved_changed, container=TEAM_KEY)

    changed = await tracker.set_workflow_state(
        issue_key=ISSUE, stage=LifecycleStage.DONE
    )
    assert ("stateHistory" in boundary.saved[-1]) is not omit
    assert ledger.wrote(issue_key=ISSUE, updated_at=changed.updated_at)
    if comment_after_state:
        await tracker.post_comment(issue_key=ISSUE, body="own follow-up")
        assert server.issues[ISSUE].updated_at > changed.updated_at
    current = await tracker.read_issue_movement(issue_key=ISSUE)
    assert "stateHistory" in dict(current.fields)
    assert dict(initial.fields)["stateHistory"] != dict(current.fields)["stateHistory"]

    delta = await gate.delta()
    assert gate.mark(PassSignal.approved_changed, container=TEAM_KEY) > original_mark
    assert delta.changed == ()


@pytest.mark.parametrize("omit", [False, True])
@pytest.mark.parametrize("position", ["before_read", "before_save", "after_save"])
async def test_principal_state_history_is_never_subtracted_as_our_transition(
    omit, position
):
    server = _server()

    async def principal():
        await server.call_tool(
            name="save_issue", arguments={"id": ISSUE, "state": "In Progress"}
        )

    class Interleaved(SaveWithoutHistory):
        async def call_tool(self, *, name, arguments):
            if name == "save_issue" and position == "before_save":
                await principal()
            response = await super().call_tool(name=name, arguments=arguments)
            if name == "save_issue" and position == "after_save":
                await principal()
            return response

    boundary = Interleaved(server, omit=omit)
    ledger = SelfWriteLedger()
    tracker = _tracker(boundary, ledger)
    gate = _gate(tracker, ledger)
    await gate.delta()
    if position == "before_read":
        await principal()
    await tracker.set_workflow_state(issue_key=ISSUE, stage=LifecycleStage.DONE)
    await tracker.post_comment(issue_key=ISSUE, body="own follow-up")
    assert len(server.issues[ISSUE].wire()["stateHistory"]) == 3
    assert (await gate.delta()).changed == (ISSUE,)


@pytest.mark.parametrize("failure", ["unreadable", "later_body", "wrong_issue"])
async def test_optional_enrichment_never_fails_or_restamps_a_landed_write(failure):
    server = _server()

    class Interleaved(SaveWithoutHistory):
        async def call_tool(self, *, name, arguments):
            if name == "get_issue" and self.saved and not self.checked:
                self.checked = True
                if failure == "unreadable":
                    raise McpSessionClosedError(
                        "history unavailable", server_name="native", tool_name=name
                    )
                if failure == "later_body":
                    await server.call_tool(
                        name="save_issue",
                        arguments={"id": ISSUE, "description": "principal body"},
                    )
                response = await super().call_tool(name=name, arguments=arguments)
                if failure == "wrong_issue":
                    return {**response, "id": "OTHER-1"}
                return response
            return await super().call_tool(name=name, arguments=arguments)

    boundary = Interleaved(server, omit=True)
    boundary.checked = False
    ledger = SelfWriteLedger()
    tracker = _tracker(boundary, ledger)
    gate = _gate(tracker, ledger)
    await gate.delta()
    returned = await tracker.set_workflow_state(
        issue_key=ISSUE, stage=LifecycleStage.DONE
    )
    assert returned.state_name == "Done"
    assert ledger.wrote(issue_key=ISSUE, updated_at=returned.updated_at)
    assert len(ledger.receipts(issue_key=ISSUE)[1]) == 1
    if failure == "later_body":
        assert server.issues[ISSUE].updated_at > returned.updated_at
        assert not ledger.wrote(
            issue_key=ISSUE, updated_at=server.issues[ISSUE].updated_at
        )
    await tracker.post_comment(issue_key=ISSUE, body="own follow-up")
    assert (await gate.delta()).changed == (ISSUE,)


async def test_cancelled_history_read_keeps_the_atomic_write_and_no_read_receipt():
    server = _server()
    entered = asyncio.Event()

    class Paused(SaveWithoutHistory):
        async def call_tool(self, *, name, arguments):
            if name == "get_issue" and self.saved and not self.checked:
                self.checked = True
                entered.set()
                await asyncio.Event().wait()
            return await super().call_tool(name=name, arguments=arguments)

    boundary = Paused(server, omit=True)
    boundary.checked = False
    ledger = SelfWriteLedger()
    tracker = _tracker(boundary, ledger)
    gate = _gate(tracker, ledger)
    await gate.delta()
    task = asyncio.create_task(
        tracker.set_workflow_state(issue_key=ISSUE, stage=LifecycleStage.DONE)
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert server.issues[ISSUE].status == "Done"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ledger.wrote(issue_key=ISSUE, updated_at=server.issues[ISSUE].updated_at)
        assert len(ledger.receipts(issue_key=ISSUE)[1]) == 1
        await tracker.post_comment(issue_key=ISSUE, body="own follow-up")
        assert (await gate.delta()).changed == (ISSUE,)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("omit", [False, True])
async def test_repeated_own_state_intervals_replay_without_losing_closed_history(omit):
    server = _server()
    boundary = SaveWithoutHistory(server, omit=omit)
    ledger = SelfWriteLedger()
    tracker = _tracker(boundary, ledger)
    first, second = _gate(tracker, ledger), _gate(tracker, ledger)
    await first.delta()
    await second.delta()
    for state in ["Done", "In Progress", "Done"]:
        await tracker.restore_workflow_state(issue_key=ISSUE, state_name=state)
    await tracker.post_comment(issue_key=ISSUE, body="own follow-up")
    history = server.issues[ISSUE].wire()["stateHistory"]
    assert len(history) == 4
    assert [row["state"]["name"] for row in history] == [
        "Todo",
        "Done",
        "In Progress",
        "Done",
    ]
    assert (await first.delta()).changed == ()
    first.rearm()
    assert (await first.delta()).changed == ()
    assert (await second.delta()).changed == ()


@pytest.mark.parametrize(
    "damage",
    ["missing", "malformed", "old_row", "new_type", "extra_open", "future_start"],
)
async def test_native_history_enrichment_must_be_exactly_one_own_interval(damage):
    server = _server()

    class Damaged(SaveWithoutHistory):
        async def call_tool(self, *, name, arguments):
            answer = await super().call_tool(name=name, arguments=arguments)
            if name != "get_issue" or not self.saved:
                return answer
            response = dict(answer)
            if damage == "missing":
                response.pop("stateHistory")
                return response
            rows = response["stateHistory"]
            if damage == "malformed":
                rows[-1].pop("endedAt")
            elif damage == "old_row":
                rows[0]["foreignMetadata"] = "not written by the state mutation"
            elif damage == "new_type":
                rows[-1]["state"]["type"] = "unstarted"
            elif damage == "extra_open":
                rows[0]["endedAt"] = None
            elif damage == "future_start":
                rows[-1]["startedAt"] = "2099-01-01T00:00:00+00:00"
                rows[0]["endedAt"] = rows[-1]["startedAt"]
            return response

    boundary = Damaged(server, omit=True)
    ledger = SelfWriteLedger()
    tracker = _tracker(boundary, ledger)
    gate = _gate(tracker, ledger)
    await gate.delta()
    result = await tracker.set_workflow_state(
        issue_key=ISSUE, stage=LifecycleStage.DONE
    )
    assert result.state_name == "Done"
    assert len(ledger.receipts(issue_key=ISSUE)[1]) == 1
    await tracker.post_comment(issue_key=ISSUE, body="own follow-up")
    assert (await gate.delta()).changed == (ISSUE,)
