"""Criterion labels are covered by the criterion's complete native surface."""

import pytest

from kodezart.domain.errors import SurfaceLeaseError
from kodezart.services.lane_escalation import LaneEscalationWriter
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_state import LaneEscalation
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from tests.fakes import PassThroughGate
from tests.services.test_run_surface_lease import _Board
from tests.tracker.conftest import CLAIMED_ISSUE
from tests.tracker.test_linear_mcp_tracker import tracker_over


def criterion_board():
    board = _Board()
    board.server.issues[CLAIMED_ISSUE].labels.append("native-criterion")
    tracker = tracker_over(
        board.server,
        caller=board,
        clock=lambda: board.now,
        issue_labels={"criterion": "native-criterion", "decision": "decision-needed"},
    )
    return board, tracker


def surface(kind):
    return WritableSurface(
        kind=kind, ref=ScopeRef(kind=ScopeKind.ISSUE, key=CLAIMED_ISSUE)
    )


async def test_criterion_holder_can_classify_its_own_complete_surface():
    board, tracker = criterion_board()
    async with RunSurfaceLease(
        tracker=tracker,
        job_id="criterion-owner",
        lease_seconds=900,
        surfaces=frozenset({surface(SurfaceKind.CRITERION_SUB_ISSUE)}),
    ):
        result = await tracker.set_issue_classification(
            issue_key=CLAIMED_ISSUE, classification="decision", holder="criterion-owner"
        )
    assert {"criterion", "decision"} <= result.issue_labels
    assert "decision-needed" in board.server.issues[CLAIMED_ISSUE].labels


async def test_separate_label_holder_cannot_write_another_holders_criterion():
    board, tracker = criterion_board()
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
            with pytest.raises(SurfaceLeaseError):
                await tracker.set_issue_classification(
                    issue_key=CLAIMED_ISSUE,
                    classification="decision",
                    holder="label-owner",
                )
    assert "decision-needed" not in board.server.issues[CLAIMED_ISSUE].labels
    assert not any(name == "save_issue" for name, _ in board.calls)


async def test_actual_escalation_cannot_bypass_the_existing_criterion_holder():
    board, tracker = criterion_board()
    writer = LaneEscalationWriter(
        tracker=tracker,
        gate=PassThroughGate(),
        surface_lease_seconds=900,
        operation=OperationConfig(
            operation_name="fixture",
            workspace="fixture",
            marker_prefixes={"escalation": "escalation"},
            issue_labels={
                "criterion": "native-criterion",
                "decision": "decision-needed",
            },
        ),
    )
    async with RunSurfaceLease(
        tracker=tracker,
        job_id="criterion-owner",
        lease_seconds=900,
        surfaces=frozenset({surface(SurfaceKind.CRITERION_SUB_ISSUE)}),
    ):
        with pytest.raises(SurfaceLeaseError):
            await writer.raise_escalation(
                lane_key="lane",
                job_id="other-job",
                visibility=RepoVisibility.PUBLIC,
                escalation=LaneEscalation(
                    issue_id=CLAIMED_ISSUE,
                    escalation_key="cost",
                    raised_by="other-job",
                    question="Resolve cost",
                    interim_reading="UPHELD",
                    interim_basis="Recorded measurement",
                    raised_at_sha="a" * 40,
                ),
            )
    assert "decision-needed" not in board.server.issues[CLAIMED_ISSUE].labels
    assert not any(c.body.startswith("[escalation:") for c in board.server.comments)


async def test_classification_changed_during_grant_read_needs_its_current_surface(
    monkeypatch,
):
    board, tracker = criterion_board()
    board.server.issues[CLAIMED_ISSUE].labels.remove("native-criterion")
    async with RunSurfaceLease(
        tracker=tracker,
        job_id="label-owner",
        lease_seconds=900,
        surfaces=frozenset({surface(SurfaceKind.ISSUE_LABEL_SET)}),
    ):
        actual = board.call_tool
        changed = False

        async def call(*, name, arguments):
            nonlocal changed
            result = await actual(name=name, arguments=arguments)
            if name == "list_comments" and not changed:
                changed = True
                board.server.issues[CLAIMED_ISSUE].labels.append("native-criterion")
            return result

        monkeypatch.setattr(board, "call_tool", call)
        with pytest.raises(SurfaceLeaseError):
            await tracker.set_issue_classification(
                issue_key=CLAIMED_ISSUE, classification="decision", holder="label-owner"
            )
        assert changed
    assert "decision-needed" not in board.server.issues[CLAIMED_ISSUE].labels
    assert not any(name == "save_issue" for name, _ in board.calls)
