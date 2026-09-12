"""Independent native classification authority controls."""

from datetime import timedelta

import pytest

from kodezart.core.errors import TrackerProtocolError
from kodezart.domain.errors import IssueLabelReadError, SurfaceLeaseError
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.operation import OperationMemberAbsentError
from kodezart.types.domain.surface import SurfaceKind
from tests.tracker.conftest import CLAIMED_ISSUE
from tests.tracker.test_criterion_label_surface import criterion_board, surface
from tests.tracker.test_linear_mcp_tracker import tracker_over


@pytest.mark.parametrize("metadata", ["missing-mapping", "missing-labels"])
async def test_unknown_native_classification_cannot_authorize_other_surface(metadata):
    board, tracker = criterion_board()
    actual = board.call_tool

    class Boundary:
        async def call_tool(self, *, name, arguments):
            payload = await actual(name=name, arguments=arguments)
            if metadata == "missing-labels" and name == "get_issue":
                payload = dict(payload)
                payload.pop("labels")
            return payload

    tracker = tracker_over(
        board.server,
        caller=Boundary(),
        clock=lambda: board.now,
        issue_labels={"decision": "decision-needed"}
        if metadata == "missing-mapping"
        else {"criterion": "native-criterion", "decision": "decision-needed"},
    )
    async with RunSurfaceLease(
        tracker=tracker,
        job_id="criterion-owner",
        lease_seconds=900,
        surfaces=frozenset({surface(SurfaceKind.CRITERION_SUB_ISSUE)}),
    ):
        async with RunSurfaceLease(
            tracker=tracker,
            job_id="label-owner",
            lease_seconds=900,
            surfaces=frozenset({surface(SurfaceKind.ISSUE_LABEL_SET)}),
        ):
            with pytest.raises(
                (
                    OperationMemberAbsentError,
                    TrackerProtocolError,
                    IssueLabelReadError,
                    SurfaceLeaseError,
                )
            ):
                await tracker.set_issue_classification(
                    issue_key=CLAIMED_ISSUE,
                    classification="decision",
                    holder="label-owner",
                )
    assert "decision-needed" not in board.server.issues[CLAIMED_ISSUE].labels
    assert not any(name == "save_issue" for name, _ in board.calls)


@pytest.mark.parametrize("criterion", [False, True])
@pytest.mark.parametrize("expired", [False, True])
async def test_final_native_read_preserves_clock_and_surface_authority(
    monkeypatch, criterion, expired
):
    board, tracker = criterion_board()
    if not criterion:
        board.server.issues[CLAIMED_ISSUE].labels.remove("native-criterion")
    kind = (
        SurfaceKind.CRITERION_SUB_ISSUE if criterion else SurfaceKind.ISSUE_LABEL_SET
    )
    actual = board.call_tool
    async with RunSurfaceLease(
        tracker=tracker,
        job_id="actual-job",
        lease_seconds=900,
        surfaces=frozenset({surface(kind)}),
    ):
        reads = 0

        async def call(*, name, arguments):
            nonlocal reads
            payload = await actual(name=name, arguments=arguments)
            if name == "get_issue":
                reads += 1
                if reads == 2 and expired:
                    board.now += timedelta(seconds=901)
            return payload

        monkeypatch.setattr(board, "call_tool", call)
        if expired:
            with pytest.raises(SurfaceLeaseError) as caught:
                await tracker.set_issue_classification(
                    issue_key=CLAIMED_ISSUE,
                    classification="decision",
                    holder="actual-job",
                )
            assert caught.value.surface_kind == kind.value
            assert caught.value.scope_kind == "issue"
            assert caught.value.scope_key == CLAIMED_ISSUE
            assert caught.value.current_holder is None
        else:
            result = await tracker.set_issue_classification(
                issue_key=CLAIMED_ISSUE,
                classification="decision",
                holder="actual-job",
            )
            assert "decision" in result.issue_labels
        assert reads >= 2
    assert ("decision-needed" in board.server.issues[CLAIMED_ISSUE].labels) is (
        not expired
    )
    assert sum(name == "save_issue" for name, _ in board.calls) == (0 if expired else 1)
