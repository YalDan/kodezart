"""Every Organize write rechecks its application authority on native retries."""

import pytest

from kodezart.core.errors import McpTransportError
from kodezart.domain.errors import OrganizeWriteRefusalError
from tests.chains import test_organize_owner as fixtures
from tests.chains.test_organize import result
from tests.tracker.conftest import CLAIMED_ISSUE

#: The surfaces the criteria stage owns. That stage runs inside an approved
#: scope run, so what takes its write authority away between an unsent
#: attempt and the retry is approval WITHDRAWN; for a surface the
#: pre-approval row authors it is approval ARRIVING.
RUN_STAGE_KINDS = ("criteria",)


@pytest.mark.parametrize("kind", ["graph", "split", "criteria"])
async def test_a_scope_approval_move_during_an_unsent_attempt_stops_each_surface(
    monkeypatch, kind
):
    original_tracker = fixtures.tracker_over

    def tracker_with_retry(*args, **kwargs):
        return original_tracker(*args, **kwargs, max_retries=1)

    monkeypatch.setattr(fixtures, "tracker_over", tracker_with_retry)
    under_approval = kind in RUN_STAGE_KINDS
    owner, board, executor = fixtures.factory(under_approval=under_approval)
    original_stream = executor.stream

    async def stream(**kwargs):
        if (
            kind != "criteria"
            and kwargs["output_format"]["schema"].get("title") == "OrganizeProposal"
        ):
            payload = (
                {"changes": [{"kind": "priority", "priority": "urgent"}]}
                if kind == "graph"
                else {
                    "children": [
                        {
                            "deliverable_key": "retained-child",
                            "title": "Prepared split",
                            "body": "The required source-grounded split.",
                        }
                    ]
                }
            )
            yield result(
                structured_output={"kind": kind, "issue_id": CLAIMED_ISSUE, **payload}
            )
            return
        async for event in original_stream(**kwargs):
            yield event

    original_call = board.call_tool
    attempts = 0

    def selected(name, args):
        if name != "save_issue":
            return False
        return "priority" in args if kind == "graph" else "id" not in args

    async def call_tool(*, name, arguments):
        nonlocal attempts
        if selected(name, arguments):
            attempts += 1
            if attempts == 1:
                labels = board.server.issues[CLAIMED_ISSUE].labels
                if under_approval:
                    labels.remove("approved scope")
                else:
                    labels.append("approved scope")
                raise McpTransportError(
                    "Connection failed before sending",
                    server_name="fixture",
                    tool_name=name,
                )
        return await original_call(name=name, arguments=arguments)

    monkeypatch.setattr(executor, "stream", stream)
    monkeypatch.setattr(board, "call_tool", call_tool)
    error = None
    try:
        await fixtures.run_owner(owner)
    except OrganizeWriteRefusalError as exc:
        error = exc
    assert error is not None
    assert attempts == 1, "an authoring surface was retried without its authority"
    assert not [(name, args) for name, args in board.calls if selected(name, args)]
    assert board.grants() == []
