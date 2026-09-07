"""P4 is identical for the shipped adapter and its consumer double."""

from collections.abc import Awaitable, Callable

import pytest
from pydantic import ValidationError

from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import EscalationReadError
from kodezart.types.domain.escalation import (
    EscalationResolution,
    EscalationResolutionState,
)
from tests.fakes import FakeLinearMcpServer, FakeTrackerPort
from tests.tracker.conftest import APPROVED_ISSUE

LANE = "lane:one"
KEY = "criterion:answer"
ADDRESS = {"issue_key": APPROVED_ISSUE, "lane_key": LANE, "escalation_key": KEY}
ESCALATION = "[fixture-escalation:lane%3Aone:criterion%3Aanswer]"
DECISION = "[fixture-decision:lane%3Aone:criterion%3Aanswer]"


@pytest.fixture
def reply(
    tracker: TrackerPort, server: FakeLinearMcpServer
) -> Callable[[str, str], Awaitable[str]]:
    """An external decision uses the measured native reply tool contract."""

    async def add(parent: str, body: str) -> str:
        if isinstance(tracker, FakeTrackerPort):
            comment = await tracker.post_comment(issue_key=APPROVED_ISSUE, body=body)
            tracker.comments[tracker.comments.index(comment)] = comment.model_copy(
                update={"reply_to": parent}
            )
            return comment.comment_key
        await server.call_tool(
            name="save_comment", arguments={"parentId": parent, "body": body}
        )
        return server.comments[-1].id

    return add


async def raise_question(tracker: TrackerPort) -> str:
    comment = await tracker.upsert_comment(
        target=APPROVED_ISSUE,
        marker=ESCALATION,
        body="Historical prose is a readable record; no JSON migration is required.",
    )
    return comment.comment_key


async def test_p4_unanswered_then_answered_carries_reference(
    tracker, reply, tracker_writes
):
    parent = await raise_question(tracker)
    before = tracker_writes()
    assert await tracker.read_escalation_resolution(**ADDRESS) == EscalationResolution(
        state=EscalationResolutionState.UNRESOLVED, decision_ref=None
    )
    assert tracker_writes() == before
    decision = await reply(parent, f"{DECISION}\nThe recorded decision.")
    before = tracker_writes()
    for _ in range(2):
        resolved = await tracker.read_escalation_resolution(**ADDRESS)
        assert resolved.state is EscalationResolutionState.RESOLVED
        assert resolved.decision_ref == decision
    assert tracker_writes() == before


async def test_p4_unwritten_is_a_typed_error(tracker):
    with pytest.raises(EscalationReadError, match="found 0") as raised:
        await tracker.read_escalation_resolution(**ADDRESS)
    assert raised.value.issue_key == APPROVED_ISSUE
    assert raised.value.lane_key == LANE
    assert raised.value.escalation_key == KEY


async def test_p4_unreachable_is_never_unresolved(tracker, server):
    await raise_question(tracker)
    if isinstance(tracker, FakeTrackerPort):
        tracker.comment_read_error = "the tracker is unreachable"
    else:
        server._tool_errors["list_comments"] = "the tracker is unreachable"
    with pytest.raises(EscalationReadError):
        await tracker.read_escalation_resolution(**ADDRESS)


@pytest.mark.parametrize("mode", ["top-level", "other-parent", "nested-reply"])
async def test_a_matching_marker_must_directly_address_this_escalation(
    tracker, reply, mode
):
    parent = await raise_question(tracker)
    if mode == "top-level":
        await tracker.post_comment(issue_key=APPROVED_ISSUE, body=DECISION)
    elif mode == "other-parent":
        other = await tracker.post_comment(
            issue_key=APPROVED_ISSUE, body="other question"
        )
        await reply(other.comment_key, DECISION)
    else:
        intermediary = await reply(parent, "discussion")
        await reply(intermediary, DECISION)
    assert (
        await tracker.read_escalation_resolution(**ADDRESS)
    ).state is EscalationResolutionState.UNRESOLVED


@pytest.mark.parametrize(
    "body",
    [
        "prefix\n" + DECISION,
        DECISION + " suffix",
        " " + DECISION,
        "[fixture-decision:lane%3Atwo:criterion%3Aanswer]",
        "[fixture-decision:lane%3Aone:criterion%3Aother]",
        "[decision:lane%3Aone:criterion%3Aanswer]",
        "Resolved. The owner approved the interim reading.",
    ],
)
async def test_only_the_exact_configured_first_line_answers(tracker, reply, body):
    parent = await raise_question(tracker)
    await reply(parent, body)
    assert (
        await tracker.read_escalation_resolution(**ADDRESS)
    ).state is EscalationResolutionState.UNRESOLVED


async def test_issue_decision_classification_is_not_an_answer(tracker, server):
    await raise_question(tracker)
    if isinstance(tracker, FakeTrackerPort):
        issue = tracker.issues[APPROVED_ISSUE]
        tracker.issues[APPROVED_ISSUE] = issue.model_copy(
            update={"issue_labels": {"decision"}}
        )
    else:
        server.issues[APPROVED_ISSUE].labels.append("decision")
    assert (
        await tracker.read_escalation_resolution(**ADDRESS)
    ).state is EscalationResolutionState.UNRESOLVED


@pytest.mark.parametrize("duplicate", ["escalation", "decision"])
async def test_duplicate_owned_records_refuse_instead_of_choosing(
    tracker, reply, duplicate
):
    parent = await raise_question(tracker)
    await reply(parent, DECISION)
    if duplicate == "escalation":
        await tracker.post_comment(issue_key=APPROVED_ISSUE, body=ESCALATION)
    else:
        await reply(parent, DECISION)
    with pytest.raises(EscalationReadError, match="found 2"):
        await tracker.read_escalation_resolution(**ADDRESS)


async def test_resolution_is_read_again_after_a_decision_is_removed(
    tracker, server, reply
):
    parent = await raise_question(tracker)
    decision = await reply(parent, DECISION)
    assert (
        await tracker.read_escalation_resolution(**ADDRESS)
    ).decision_ref == decision
    if isinstance(tracker, FakeTrackerPort):
        tracker.comments[:] = [c for c in tracker.comments if c.comment_key != decision]
    else:
        server.comments[:] = [c for c in server.comments if c.id != decision]
    assert (
        await tracker.read_escalation_resolution(**ADDRESS)
    ).state is EscalationResolutionState.UNRESOLVED


@pytest.mark.parametrize(
    "state, reference",
    [
        ("unresolved", "decision-1"),
        ("resolved", None),
        ("resolved", ""),
        ("missing", None),
    ],
)
def test_invalid_resolution_states_are_not_constructible(state, reference):
    with pytest.raises(ValidationError):
        EscalationResolution(state=state, decision_ref=reference)


def test_resolution_is_closed_frozen_and_serializes_the_reference():
    resolved = EscalationResolution(
        state=EscalationResolutionState.RESOLVED, decision_ref="comment-1"
    )
    assert resolved.model_dump(by_alias=True) == {
        "state": "resolved",
        "decisionRef": "comment-1",
    }
    with pytest.raises(ValidationError, match="frozen"):
        resolved.decision_ref = "other"
    with pytest.raises(ValidationError, match="Extra inputs"):
        EscalationResolution.model_validate(
            {**resolved.model_dump(), "reason": "guessed"}
        )
    assert {member.value for member in EscalationResolutionState} == {
        "unresolved",
        "resolved",
    }
