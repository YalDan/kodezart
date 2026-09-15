"""An acceptance claim is read against the lane subtree's own criterion states."""

import pytest

from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.run_event_stream import LaneRunEvent
from kodezart.domain.run_shape import tally_unmoved
from kodezart.domain.tracker_writes import marked_comment_body
from kodezart.services.lane_tally import observe_lane_tally
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_alarm import AlarmSignal, LaneSubject, RunAlarm
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.run_state import LaneEscalation
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import (
    IssuePriority,
    TrackerComment,
    TrackerIssue,
    WorkflowStateKind,
)
from tests.fakes import FakeTrackerPort
from tests.tracker.conftest import FIXTURE_NOW
from tests.tracker.marker_config import MARKER_PREFIXES

SCOPE = "scope/opaque"
LANE = "lane/one"
FIRE = "KOD-900"
DELIVERABLE = "KOD-899"
DIRECT = "KOD-901"
NESTED = "KOD-902"
MILESTONE = ScopeRef(kind=ScopeKind.MILESTONE, key="milestone/one")
OPERATION = OperationConfig(
    operation_name="fixture", workspace="fixture", marker_prefixes=MARKER_PREFIXES
)

TODO = WorkflowStateKind.UNSTARTED
REVIEW = WorkflowStateKind.STARTED
DONE = WorkflowStateKind.COMPLETED


def issue(
    key: str,
    state: WorkflowStateKind,
    *,
    parent: str | None = None,
    labels: frozenset[str] = frozenset(),
    milestone: str | None = None,
) -> TrackerIssue:
    return TrackerIssue(
        issue_key=key,
        title=key,
        body="body",
        priority=IssuePriority.NONE,
        state_name=state.value,
        state_kind=state,
        queue_states=frozenset(),
        issue_labels=labels,
        team_key=None,
        created_at=FIXTURE_NOW,
        updated_at=FIXTURE_NOW,
        url=f"https://tracker.invalid/{key}",
        parent_key=parent,
        milestone_key=milestone,
    )


def criterion(key: str, state: WorkflowStateKind, *, parent: str) -> TrackerIssue:
    return issue(key, state, parent=parent, labels=frozenset({"criterion"}))


async def lane(
    *,
    direct: WorkflowStateKind = TODO,
    nested: WorkflowStateKind = TODO,
    accepted: bool = True,
) -> FakeTrackerPort:
    """A fire carrying one criterion of its own and one under a deliverable."""
    tracker = FakeTrackerPort(
        issues=(
            issue(FIRE, REVIEW, milestone=MILESTONE.key),
            issue(DELIVERABLE, REVIEW, parent=FIRE),
            criterion(DIRECT, direct, parent=FIRE),
            criterion(NESTED, nested, parent=DELIVERABLE),
        ),
        marker_prefixes=MARKER_PREFIXES,
        scope_memberships={MILESTONE: (FIRE,)},
    )
    if accepted:
        await tracker.post_run_event(
            issue_key=FIRE,
            event=LaneRunEvent(kind=RunEventKind.EVALUATOR_ACCEPTED, lane_key=LANE),
        )
    return tracker


def escalate(
    tracker: FakeTrackerPort,
    *,
    issue_key: str,
    answered: bool = False,
    lane_key: str = LANE,
) -> None:
    """Record one question over *issue_key*, optionally with its decision."""
    occurrence = f"question:{issue_key}"
    record = LaneEscalation(
        issue_id=issue_key,
        escalation_key=occurrence,
        raised_by="loop/holder",
        question="Does the recorded Check still describe the work?",
        interim_reading="The criterion stays as recorded.",
        interim_basis="the reading the loop was working under",
        raised_at_sha="raise-sha",
    )
    question = TrackerComment(
        comment_key=f"escalation-{issue_key}",
        issue_key=issue_key,
        author_key="kodezart",
        body=marked_comment_body(
            marker=compose_comment_marker(
                prefixes=MARKER_PREFIXES,
                purpose="escalation",
                lane=lane_key,
                occurrence_key=occurrence,
            ),
            body=record.model_dump_json(by_alias=True),
        ),
        created_at=FIXTURE_NOW,
    )
    tracker.comments.append(question)
    if not answered:
        return
    tracker.comments.append(
        TrackerComment(
            comment_key=f"decision-{issue_key}",
            issue_key=issue_key,
            author_key="principal",
            body=marked_comment_body(
                marker=compose_comment_marker(
                    prefixes=MARKER_PREFIXES,
                    purpose="decision",
                    lane=lane_key,
                    occurrence_key=occurrence,
                ),
                body="The recorded Check stands.",
            ),
            created_at=FIXTURE_NOW,
            reply_to=question.comment_key,
        )
    )


async def observe(tracker: FakeTrackerPort) -> RunAlarm | None:
    return await observe_lane_tally(
        tracker=tracker,
        operation=OPERATION,
        scope_key=SCOPE,
        lane_key=LANE,
        fire_key=FIRE,
        milestone=MILESTONE,
        supersession_refs={},
        raised_at_sha="tick-sha",
        raised_by="supervisor/holder",
    )


async def test_an_acceptance_claim_with_every_criterion_still_in_todo_alarms() -> None:
    alarm = await observe(await lane())

    assert alarm is not None
    assert alarm.subject == LaneSubject(scope_key=SCOPE, lane_key=LANE)
    assert alarm.signal is AlarmSignal.TALLY_UNMOVED
    assert alarm.bound is None
    assert alarm.raised_at_sha == "tick-sha"
    assert alarm.raised_by == "supervisor/holder"


async def test_the_alarm_replays_from_the_readings_it_carries() -> None:
    alarm = await observe(await lane())

    assert alarm is not None
    stored = RunAlarm.model_validate_json(alarm.model_dump_json(by_alias=True))
    assert (
        tally_unmoved(
            subject=stored.subject,
            readings=stored.readings,
            raised_at_sha=stored.raised_at_sha,
            raised_by=stored.raised_by,
        )
        == alarm
    )


@pytest.mark.parametrize("state", [REVIEW, DONE])
async def test_one_criterion_out_of_todo_is_movement(
    state: WorkflowStateKind,
) -> None:
    assert await observe(await lane(direct=state)) is None


@pytest.mark.parametrize("state", [REVIEW, DONE])
async def test_the_only_moved_criterion_under_a_deliverable_child_is_movement(
    state: WorkflowStateKind,
) -> None:
    assert await observe(await lane(nested=state)) is None


async def test_criteria_held_by_unanswered_questions_are_not_counted() -> None:
    tracker = await lane()
    escalate(tracker, issue_key=DIRECT)
    escalate(tracker, issue_key=NESTED)

    assert await observe(tracker) is None


async def test_one_held_criterion_does_not_excuse_the_criterion_beside_it() -> None:
    tracker = await lane()
    escalate(tracker, issue_key=DIRECT)

    assert await observe(tracker) is not None


async def test_an_answered_question_leaves_its_criterion_in_the_tally() -> None:
    tracker = await lane()
    escalate(tracker, issue_key=DIRECT, answered=True)
    escalate(tracker, issue_key=NESTED, answered=True)

    assert await observe(tracker) is not None


async def test_an_unanswered_question_over_the_fire_holds_no_criterion() -> None:
    tracker = await lane()
    escalate(tracker, issue_key=FIRE)

    assert await observe(tracker) is not None


async def test_a_question_recorded_in_another_lane_holds_no_criterion() -> None:
    tracker = await lane()
    escalate(tracker, issue_key=DIRECT, lane_key="lane/two")
    escalate(tracker, issue_key=NESTED, lane_key="lane/two")

    assert await observe(tracker) is not None


async def test_nothing_is_observed_without_an_acceptance_claim() -> None:
    assert await observe(await lane(accepted=False)) is None
