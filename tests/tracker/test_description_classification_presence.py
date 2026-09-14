"""Explicit description authority cannot infer ordinary issues from absent facts."""

import pytest

from kodezart.core.errors import TrackerProtocolError
from kodezart.types.domain.operation import OperationMemberAbsentError
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import (
    DescriptionWriteAuthority,
    SurfaceKind,
    WritableSurface,
)
from tests.tracker.test_linear_mcp_tracker import tracker_over
from tests.tracker.test_native_criterion_amendment import (
    BODY,
    HOLDER,
    KEY,
    board_and_tracker,
    lease,
)


@pytest.mark.parametrize(
    "missing", ["mapping", "blank_mapping", "labels", "labels_after_first"]
)
async def test_missing_native_classification_authority_cannot_grant_generic_description(
    monkeypatch, missing
):
    board, tracker = board_and_tracker()
    if missing in {"mapping", "blank_mapping"}:
        tracker = tracker_over(
            board.server,
            caller=board,
            clock=lambda: board.now,
            issue_labels={"criteria_ready": "criteria-prepared"}
            | ({"criterion": "  "} if missing == "blank_mapping" else {}),
        )
    actual = board.call_tool
    issue_reads = 0

    async def reply(*, name, arguments):
        nonlocal issue_reads
        if name == "get_issue" and arguments.get("id") == KEY:
            issue_reads += 1
        payload = await actual(name=name, arguments=arguments)
        if (
            (
                missing == "labels"
                or (missing == "labels_after_first" and issue_reads > 1)
            )
            and name == "get_issue"
            and arguments.get("id") == KEY
        ):
            assert isinstance(payload, dict) and "labels" in payload
            payload = {key: value for key, value in payload.items() if key != "labels"}
        return payload

    kind = (
        SurfaceKind.CRITERION_SUB_ISSUE
        if missing == "labels_after_first"
        else SurfaceKind.ISSUE_DESCRIPTION
    )
    async with lease(tracker, kind):
        monkeypatch.setattr(board, "call_tool", reply)
        with pytest.raises((TrackerProtocolError, OperationMemberAbsentError)):
            await tracker.edit_description(
                target=KEY,
                expected=BODY,
                replacement=BODY + "wrong generic surface write",
                authorization=DescriptionWriteAuthority(
                    holder=HOLDER,
                    surface=WritableSurface(
                        kind=kind,
                        ref=ScopeRef(kind=ScopeKind.ISSUE, key=KEY),
                    ),
                ),
            )
    if missing == "labels_after_first":
        assert issue_reads >= 2
    assert board.server.issues[KEY].description == BODY
    assert not any(name == "save_issue" for name, _ in board.calls)
