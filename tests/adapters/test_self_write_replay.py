"""Native receipt replay preserves principal movement and each gate's window."""

import asyncio
from collections.abc import Mapping

import pytest

from kodezart.core.errors import McpSessionClosedError
from kodezart.core.protocols import McpToolResult
from kodezart.types.domain.dispatch import PassSignal, SelfWriteLedger
from kodezart.types.domain.operation import LifecycleStage, QueueState
from tests.adapters.test_tracker_self_writes import (
    DONE_STATE,
    HOLDER,
    ISSUE,
    LEASE_SECONDS,
    STAMP,
    TEAM_KEY,
    _claim_granted,
    _gate,
    _server,
    _tracker,
)
from tests.fakes import FakeLinearMcpServer


class NativeBoundary:
    def __init__(self, server: FakeLinearMcpServer) -> None:
        self.server = server
        self.mode: str | None = None
        self.extra: str = "original"

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        result = await self.server.call_tool(name=name, arguments=arguments)
        assert isinstance(result, Mapping)
        result = dict(result)
        if name == "get_issue":
            result["futureNativeField"] = self.extra
            if self.mode == "wrong-issue":
                result["id"] = "FOREIGN-1"
            elif self.mode == "missing-labels":
                result.pop("labels")
        if name == "list_comments":
            comments = result["comments"]
            assert isinstance(comments, list)
            if self.mode == "unreadable":
                raise McpSessionClosedError(
                    "unreadable", server_name="native", tool_name=name
                )
            if self.mode == "missing-completeness":
                result.pop("hasNextPage")
            elif self.mode == "duplicate":
                result["comments"] = [*comments, comments[0]]
            elif self.mode == "cursor":
                result.update(hasNextPage=True, cursor="repeated")
            elif self.mode == "changing":
                self.mode = None
                await self.server.call_tool(
                    name="save_issue",
                    arguments={"id": ISSUE, "description": "changed during snapshot"},
                )
            elif self.mode == "paged":
                index = int(str(arguments.get("cursor", 0)))
                result.update(
                    comments=comments[index : index + 1],
                    hasNextPage=index + 1 < len(comments),
                    cursor=str(index + 1),
                )
        return result


@pytest.mark.parametrize(
    "foreign",
    [
        "body",
        "state",
        "title",
        "priority",
        "unconfigured-label",
        "unknown-field",
        "old-comment-edit",
        "old-comment-delete",
    ],
)
@pytest.mark.parametrize("position", ["before", "after"])
async def test_only_explicit_receipts_are_subtracted(
    foreign: str, position: str
) -> None:
    server = _server()
    old = await server.call_tool(
        name="save_comment", arguments={"issueId": ISSUE, "body": "principal original"}
    )
    assert isinstance(old, Mapping)
    boundary = NativeBoundary(server)
    ledger = SelfWriteLedger()
    tracker = _tracker(boundary, ledger)
    gate = _gate(tracker, ledger)
    assert (await gate.delta()).changed == (ISSUE,)

    async def principal() -> None:
        if foreign in {"body", "state", "title", "unconfigured-label"}:
            field, value = {
                "body": ("description", "principal body"),
                "state": ("state", DONE_STATE),
                "title": ("title", "principal title"),
                "unconfigured-label": ("addLabels", ["not-an-operation-label"]),
            }[foreign]
            await server.call_tool(
                name="save_issue", arguments={"id": ISSUE, field: value}
            )
        elif foreign == "priority":
            server.issues[ISSUE].priority_raw = 3
            server._moved(ISSUE)
        elif foreign == "unknown-field":
            boundary.extra = "principal new value"
            server._moved(ISSUE)
        else:
            await server.call_tool(
                name="save_comment"
                if foreign == "old-comment-edit"
                else "delete_comment",
                arguments={"id": old["id"], "body": "principal revised"},
            )

    if position == "before":
        await principal()
    await tracker.post_comment(issue_key=ISSUE, body="our comment")
    if position == "after":
        await principal()
    assert (await gate.delta()).changed == (ISSUE,)
    assert not ledger.wrote(issue_key=ISSUE, updated_at=server.issues[ISSUE].updated_at)


@pytest.mark.parametrize("foreign", [False, True])
async def test_issue_receipts_never_copy_unwritten_response_fields(
    foreign: bool,
) -> None:
    server = _server()
    ledger = SelfWriteLedger()
    tracker = _tracker(server, ledger)
    gate = _gate(tracker, ledger)
    await gate.delta()
    if foreign:
        await server.call_tool(
            name="save_issue",
            arguments={"id": ISSUE, "description": "principal before our state write"},
        )
    await tracker.set_workflow_state(issue_key=ISSUE, stage=LifecycleStage.DONE)
    await tracker.post_comment(issue_key=ISSUE, body="our later comment")
    assert (await gate.delta()).changed == ((ISSUE,) if foreign else ())


async def test_mixed_declared_issue_fields_and_comment_churn_stay_quiet() -> None:
    server = _server()
    ledger = SelfWriteLedger()
    tracker = _tracker(server, ledger)
    gate = _gate(tracker, ledger)
    await gate.delta()
    await tracker.update_issue(issue_key=ISSUE, title="our title", body="our body")
    await tracker.set_queue_state(issue_key=ISSUE, state=QueueState.PROPOSED)
    await tracker.set_queue_state(issue_key=ISSUE, state=QueueState.APPROVED)
    await tracker.set_issue_classification(issue_key=ISSUE, classification="criterion")
    await _claim_granted(tracker)
    await _claim_granted(tracker)
    await tracker.renew_claim(
        issue_key=ISSUE, holder=HOLDER, lease_seconds=LEASE_SECONDS
    )
    await tracker.release_claim(issue_key=ISSUE, holder=HOLDER)
    await tracker.upsert_comment(target=ISSUE, marker="<!-- own note -->", body="first")
    initial = server.comments[-1].created_at
    await tracker.upsert_comment(
        target=ISSUE, marker="<!-- own note -->", body="second"
    )
    assert server.comments[-1].created_at == initial
    assert (await gate.delta()).changed == ()
    assert gate.mark(PassSignal.approved_changed, container=TEAM_KEY) > STAMP


@pytest.mark.parametrize(
    "failure",
    [
        "unreadable",
        "missing-completeness",
        "duplicate",
        "cursor",
        "wrong-issue",
        "missing-labels",
        "changing",
    ],
)
async def test_failed_snapshot_leaves_receipts_and_window_unspent(failure: str) -> None:
    server = _server()
    boundary = NativeBoundary(server)
    ledger = SelfWriteLedger()
    tracker = _tracker(boundary, ledger)
    gate = _gate(tracker, ledger)
    await gate.delta()
    original_mark = gate.mark(PassSignal.approved_changed, container=TEAM_KEY)
    await tracker.post_comment(issue_key=ISSUE, body="our comment")
    boundary.mode = failure
    assert (await gate.delta()).changed == ()
    assert gate.mark(PassSignal.approved_changed, container=TEAM_KEY) == original_mark
    boundary.mode = None
    assert (await gate.delta()).changed == ((ISSUE,) if failure == "changing" else ())
    assert (
        gate.mark(PassSignal.approved_changed, container=TEAM_KEY)
        == server.issues[ISSUE].updated_at
    )


async def test_receipts_are_independent_for_two_gates_and_rearm_restores_snapshot() -> (
    None
):
    server = _server()
    ledger = SelfWriteLedger()
    tracker = _tracker(server, ledger)
    first, second = _gate(tracker, ledger), _gate(tracker, ledger)
    await first.delta()
    await second.delta()
    await tracker.post_comment(issue_key=ISSUE, body="our first")
    assert (await first.delta()).changed == ()
    first.rearm()
    assert (await first.delta()).changed == ()
    assert (await second.delta()).changed == ()
    await server.call_tool(
        name="save_issue", arguments={"id": ISSUE, "description": "principal"}
    )
    await tracker.post_comment(issue_key=ISSUE, body="our second")
    assert (await first.delta()).changed == (ISSUE,)
    first.rearm()
    first.rearm()
    assert (await first.delta()).changed == (ISSUE,)
    assert (await second.delta()).changed == (ISSUE,)


async def test_paged_old_comment_edit_remains_news_and_timestamp_only_does_too() -> (
    None
):
    server = _server()
    for index in range(3):
        await server.call_tool(
            name="save_comment",
            arguments={"issueId": ISSUE, "body": f"principal {index}"},
        )
    boundary = NativeBoundary(server)
    boundary.mode = "paged"
    ledger = SelfWriteLedger()
    tracker = _tracker(boundary, ledger)
    gate = _gate(tracker, ledger)
    await gate.delta()
    await tracker.post_comment(issue_key=ISSUE, body="our latest")
    await server.call_tool(
        name="save_comment",
        arguments={"id": server.comments[1].id, "body": "old principal edit"},
    )
    assert (await gate.delta()).changed == (ISSUE,)
    server._moved(ISSUE)
    assert (await gate.delta()).changed == (ISSUE,)


async def test_cold_gate_conservatively_reports_unobserved_comment_history() -> None:
    server = _server()
    ledger = SelfWriteLedger()
    tracker = _tracker(server, ledger)
    await tracker.post_comment(issue_key=ISSUE, body="own before gate existed")
    assert (await _gate(tracker, ledger).delta()).changed == (ISSUE,)


async def test_cancellation_does_not_spend_the_snapshot_or_mark() -> None:
    server = _server()
    entered = asyncio.Event()
    release = asyncio.Event()
    pause = False

    class Paused(NativeBoundary):
        async def call_tool(
            self, *, name: str, arguments: Mapping[str, object]
        ) -> McpToolResult:
            if pause and name == "list_comments":
                entered.set()
                await release.wait()
            return await super().call_tool(name=name, arguments=arguments)

    ledger = SelfWriteLedger()
    tracker = _tracker(Paused(server), ledger)
    gate = _gate(tracker, ledger)
    await gate.delta()
    await tracker.post_comment(issue_key=ISSUE, body="ours")
    pause = True
    task = asyncio.create_task(gate.delta())
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gate.mark(PassSignal.approved_changed, container=TEAM_KEY) == STAMP
    pause = False
    assert (await gate.delta()).changed == ()


async def test_a_late_receipt_does_not_advance_a_window_read_with_an_old_cursor() -> (
    None
):
    server = _server()
    written = asyncio.Event()
    release_response = asyncio.Event()
    writer: asyncio.Task[object] | None = None
    pause = False

    class DelayedResponse(NativeBoundary):
        async def call_tool(
            self, *, name: str, arguments: Mapping[str, object]
        ) -> McpToolResult:
            result = await super().call_tool(name=name, arguments=arguments)
            if pause and name == "save_comment":
                written.set()
                await release_response.wait()
            if pause and name == "list_comments":
                release_response.set()
                assert writer is not None
                await writer
            return result

    ledger = SelfWriteLedger()
    tracker = _tracker(DelayedResponse(server), ledger)
    gate = _gate(tracker, ledger)
    await gate.delta()
    pause = True
    writer = asyncio.create_task(
        tracker.post_comment(issue_key=ISSUE, body="delayed receipt")
    )
    await asyncio.wait_for(written.wait(), timeout=5)
    assert (await gate.delta()).changed == ()
    assert gate.mark(PassSignal.approved_changed, container=TEAM_KEY) == STAMP
    pause = False
    assert (await gate.delta()).changed == ()
    assert (
        gate.mark(PassSignal.approved_changed, container=TEAM_KEY)
        == server.issues[ISSUE].updated_at
    )


async def test_later_unreadable_issue_does_not_commit_earlier_issue_snapshot() -> None:
    from dataclasses import replace

    server = _server()
    second = "FIX-2"
    server.issues[second] = replace(server.issues[ISSUE], id=second)
    fail = False
    observed: list[str] = []

    class PartialWindow(NativeBoundary):
        async def call_tool(
            self, *, name: str, arguments: Mapping[str, object]
        ) -> McpToolResult:
            if name == "get_issue":
                observed.append(str(arguments["id"]))
                if fail and arguments["id"] == second:
                    raise McpSessionClosedError(
                        "second issue refused", server_name="native", tool_name=name
                    )
            return await super().call_tool(name=name, arguments=arguments)

    ledger = SelfWriteLedger()
    tracker = _tracker(PartialWindow(server), ledger)
    gate = _gate(tracker, ledger)
    assert set((await gate.delta()).changed) == {ISSUE, second}
    await server.call_tool(
        name="save_issue", arguments={"id": second, "description": "principal changed"}
    )
    await tracker.post_comment(issue_key=ISSUE, body="our later stamp")
    observed.clear()
    fail = True
    assert (await gate.delta()).changed == ()
    assert observed.index(ISSUE) < observed.index(second)
    assert gate.mark(PassSignal.approved_changed, container=TEAM_KEY) == STAMP
    fail = False
    assert (await gate.delta()).changed == (second,)


async def test_comment_edit_receipt_does_not_absorb_other_response_fields() -> None:
    server = _server()
    extra = "original"

    class CommentMetadata(NativeBoundary):
        async def call_tool(
            self, *, name: str, arguments: Mapping[str, object]
        ) -> McpToolResult:
            result = await super().call_tool(name=name, arguments=arguments)
            assert isinstance(result, Mapping)
            result = dict(result)
            if name == "save_comment":
                result["futureCommentField"] = extra
            elif name == "list_comments":
                comments = result["comments"]
                assert isinstance(comments, list)
                result["comments"] = [
                    dict(comment, futureCommentField=extra) for comment in comments
                ]
            return result

    ledger = SelfWriteLedger()
    tracker = _tracker(CommentMetadata(server), ledger)
    await tracker.upsert_comment(target=ISSUE, marker="<!-- note -->", body="first")
    gate = _gate(tracker, ledger)
    await gate.delta()
    extra = "principal changed metadata"
    server._moved(ISSUE)
    await tracker.upsert_comment(target=ISSUE, marker="<!-- note -->", body="our edit")
    assert (await gate.delta()).changed == (ISSUE,)
