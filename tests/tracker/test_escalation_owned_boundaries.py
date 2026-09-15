"""The actual escalation writer preserves policy guards and issued-write ownership."""

import asyncio

import pytest

from kodezart.core.errors import (
    McpCallUnansweredError,
    McpCredentialRefusedError,
    TrackerAccessDeniedError,
    TrackerUnavailableError,
)
from kodezart.services.lane_escalation import LaneEscalationWriter
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_state import LaneEscalation
from tests.fakes import PassThroughGate
from tests.services.test_run_surface_lease import _Board
from tests.tracker.conftest import CLAIMED_ISSUE
from tests.tracker.test_linear_mcp_tracker import tracker_over


def actual_writer():
    board = _Board()
    labels = {"criterion": "native-criterion", "decision": "decision-needed"}
    tracker = tracker_over(
        board.server,
        caller=board,
        clock=lambda: board.now,
        issue_labels=labels,
        max_retries=2,
    )
    writer = LaneEscalationWriter(
        tracker=tracker,
        gate=PassThroughGate(),
        operation=OperationConfig(
            operation_name="fixture",
            workspace="fixture",
            issue_labels=labels,
            marker_prefixes={"escalation": "question"},
        ),
        surface_lease_seconds=321.5,
    )
    return board, writer


def question():
    return LaneEscalation(
        issue_id=CLAIMED_ISSUE,
        escalation_key="scope-choice",
        raised_by="actual-job",
        question="Which of the two declared owners decides?",
        interim_reading="Hold this affected change.",
        interim_basis="The two supplied owner declarations conflict.",
        raised_at_sha="a" * 40,
    )


def questions(board):
    return [c for c in board.server.comments if c.body.startswith("[question:")]


def assert_both_surfaces_held(board):
    # The native adapter publishes one grant for the addressed surface set.
    grants = board.grants()
    assert len(grants) == 1
    assert "holder: actual-job\n" in grants[0].body
    assert "state: held\n" in grants[0].body
    assert "- marker_comment|issue|" in grants[0].body
    assert "- issue_label_set|issue|" in grants[0].body


@pytest.mark.parametrize("stage", [1, 2])
@pytest.mark.parametrize("failure", ["refusal", "cancellation"])
async def test_actual_before_write_guard_stops_each_write_after_internal_waits(
    stage, failure
):
    board, writer = actual_writer()
    entered = asyncio.Event()
    never = asyncio.Event()
    visits = []
    refusal = RuntimeError("the supplied current policy refused this write")

    async def guard():
        visits.append(len(questions(board)))
        assert_both_surfaces_held(board)
        assert "decision-needed" not in board.server.issues[CLAIMED_ISSUE].labels
        if len(visits) == stage:
            if failure == "refusal":
                raise refusal
            entered.set()
            await never.wait()

    task = asyncio.create_task(
        writer.raise_escalation(
            lane_key="actual-lane",
            job_id="actual-job",
            escalation=question(),
            visibility=RepoVisibility.PUBLIC,
            before_write=guard,
        )
    )
    try:
        if failure == "cancellation":
            await asyncio.wait_for(entered.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RuntimeError) as caught:
                await task
            assert caught.value is refusal
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert visits == list(range(stage))
    assert len(questions(board)) == stage - 1
    assert "decision-needed" not in board.server.issues[CLAIMED_ISSUE].labels
    assert not board.grants()


@pytest.mark.parametrize("issued", ["comment", "classification"])
async def test_cancelled_caller_settles_exact_issued_mutation_before_releasing(issued):
    board, writer = actual_writer()
    # The board pauses the issued call before the backend applies it, so the
    # cancellation lands between the write leaving the writer and its effect.
    if issued == "comment":
        board.pause = lambda name, arguments: (
            name == "save_comment"
            and str(arguments.get("body", "")).startswith("[question:")
        )
    else:
        board.pause = lambda name, arguments: (
            name == "save_issue" and ("addLabels" in arguments)
        )
    task = asyncio.create_task(
        writer.raise_escalation(
            lane_key="actual-lane",
            job_id="actual-job",
            escalation=question(),
            visibility=RepoVisibility.PUBLIC,
        )
    )
    try:
        await asyncio.wait_for(board.reached.wait(), timeout=5)
        assert len(questions(board)) == (1 if issued == "classification" else 0)
        assert "decision-needed" not in board.server.issues[CLAIMED_ISSUE].labels
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert_both_surfaces_held(board)
        board.resume.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
    finally:
        board.resume.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(questions(board)) == 1
    assert not board.grants()
    assert sum(name == "save_issue" for name, _ in board.calls) == (
        1 if issued == "classification" else 0
    )
    assert ("decision-needed" in board.server.issues[CLAIMED_ISSUE].labels) is (
        issued == "classification"
    )


@pytest.mark.parametrize("failure", ["unknown-outcome", "credential", "programmer"])
async def test_classification_failure_taxonomy_never_resends_an_unrepeatable_write(
    monkeypatch, failure
):
    board, writer = actual_writer()
    actual = board.call_tool
    attempts = 0
    if failure == "unknown-outcome":
        error = McpCallUnansweredError("receipt lost", server_name="native")
        expected = TrackerUnavailableError
    elif failure == "credential":
        error = McpCredentialRefusedError("access denied", server_name="native")
        expected = TrackerAccessDeniedError
    else:
        error = RuntimeError("unexpected adapter programming defect")
        expected = RuntimeError

    async def call(*, name, arguments):
        nonlocal attempts
        if name == "save_issue" and "addLabels" in arguments:
            attempts += 1
            if failure == "unknown-outcome":
                await actual(name=name, arguments=arguments)
            raise error
        return await actual(name=name, arguments=arguments)

    monkeypatch.setattr(board, "call_tool", call)
    with pytest.raises(expected) as caught:
        await writer.raise_escalation(
            lane_key="actual-lane",
            job_id="actual-job",
            escalation=question(),
            visibility=RepoVisibility.PUBLIC,
        )
    if failure == "programmer":
        assert caught.value is error
    else:
        assert caught.value.__cause__ is error
    assert attempts == 1
    assert len(questions(board)) == 1
    assert not board.grants()
    assert ("decision-needed" in board.server.issues[CLAIMED_ISSUE].labels) is (
        failure == "unknown-outcome"
    )
