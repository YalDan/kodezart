"""Real escalation publication retains its label grant through internal waits."""

from datetime import timedelta

import pytest

from kodezart.core.errors import McpTransportError, TrackerUnavailableError
from kodezart.domain.errors import IssueLabelReadError, SurfaceLeaseError
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


@pytest.mark.parametrize(
    "change", ["expiry", "withdrawal", "retry", "unchanged", "retry_unchanged"]
)
async def test_escalation_label_write_requires_current_grant_after_waits(
    monkeypatch, change
):
    board = _Board()
    tracker = tracker_over(
        board.server,
        caller=board,
        max_retries=2,
        clock=lambda: board.now,
        issue_labels={"decision": "decision-needed"},
    )
    writer = LaneEscalationWriter(
        tracker=tracker,
        gate=PassThroughGate(),
        surface_lease_seconds=900,
        operation=OperationConfig(
            operation_name="fixture",
            workspace="fixture",
            marker_prefixes={"escalation": "escalation"},
            issue_labels={"decision": "decision-needed"},
        ),
    )
    actual = board.call_tool
    recorded = False
    altered = False
    attempts = 0

    async def call(*, name, arguments):
        nonlocal recorded, altered, attempts
        if name == "save_comment" and str(arguments.get("body", "")).startswith(
            "[escalation:"
        ):
            recorded = True
        if (
            name == "get_issue"
            and recorded
            and not altered
            and change in {"expiry", "withdrawal"}
        ):
            altered = True
            if change == "expiry":
                board.now = max(
                    (c.updated_at or c.created_at) for c in board.server.comments
                ) + timedelta(seconds=901)
            else:
                board.server.comments[:] = [
                    c for c in board.server.comments if "kind: lease\n" not in c.body
                ]
        if name == "save_issue" and "addLabels" in arguments:
            attempts += 1
            if change.startswith("retry") and attempts == 1:
                altered = True
                if change == "retry":
                    board.server.comments[:] = [
                        c
                        for c in board.server.comments
                        if "kind: lease\n" not in c.body
                    ]
                raise McpTransportError("not sent", server_name="fixture")
        return await actual(name=name, arguments=arguments)

    monkeypatch.setattr(board, "call_tool", call)

    async def publish():
        return await writer.raise_escalation(
            lane_key="actual-lane",
            job_id="actual-parent-job",
            visibility=RepoVisibility.PUBLIC,
            escalation=LaneEscalation(
                issue_id=CLAIMED_ISSUE,
                escalation_key="measured-cost",
                raised_by="actual-parent-job",
                question="Resolve measured cost",
                interim_reading="UPHELD",
                interim_basis="Actual measured cost",
                raised_at_sha="a" * 40,
            ),
        )

    if change in {"unchanged", "retry_unchanged"}:
        await publish()
        assert "decision-needed" in board.server.issues[CLAIMED_ISSUE].labels
        assert attempts == (2 if change == "retry_unchanged" else 1)
    else:
        with pytest.raises(SurfaceLeaseError):
            await publish()
        assert altered
        assert "decision-needed" not in board.server.issues[CLAIMED_ISSUE].labels
        assert attempts == (1 if change == "retry" else 0)
    assert any(c.body.startswith("[escalation:") for c in board.server.comments)


@pytest.mark.parametrize("failure", ["outage", "missing"])
async def test_actual_label_readback_failure_cannot_resend_completed_write(
    monkeypatch, failure
):
    board = _Board()
    tracker = tracker_over(
        board.server,
        caller=board,
        max_retries=2,
        clock=lambda: board.now,
        issue_labels={"decision": "decision-needed"},
    )
    surface = WritableSurface(
        kind=SurfaceKind.ISSUE_LABEL_SET,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=CLAIMED_ISSUE),
    )
    async with RunSurfaceLease(
        tracker=tracker, job_id="job", lease_seconds=900, surfaces=frozenset({surface})
    ):
        actual = board.call_tool
        writes = 0

        async def call(*, name, arguments):
            nonlocal writes
            if name == "get_issue" and writes and failure == "outage":
                raise McpTransportError("read unavailable", server_name="fixture")
            result = await actual(name=name, arguments=arguments)
            if name == "save_issue" and "addLabels" in arguments:
                writes += 1
                if failure == "missing":
                    board.server.issues[CLAIMED_ISSUE].labels.remove("decision-needed")
            return result

        monkeypatch.setattr(board, "call_tool", call)
        with pytest.raises(
            TrackerUnavailableError if failure == "outage" else IssueLabelReadError
        ):
            await tracker.set_issue_classification(
                issue_key=CLAIMED_ISSUE, classification="decision", holder="job"
            )
        assert writes == 1
