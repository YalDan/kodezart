"""A criterion's regression and its lapse are separate replayable readings."""

import pytest
from pydantic import ValidationError

from kodezart.domain.criterion_tally import lapse_undischarged, tally_regressed
from kodezart.domain.errors import RunShapeReadError
from kodezart.types.domain.mandate_graph import LaneGraphSnapshot
from kodezart.types.domain.operation import LifecycleStage
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    CriterionStateMove,
    CriterionSubject,
    GraphEvidence,
    LaneSubject,
    PresenceEvidence,
    RunAlarm,
    RunEventProjection,
    RunEventsEvidence,
    StateMoveEvidence,
    TextEvidence,
)
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import IssuePriority, TrackerIssue, WorkflowStateKind
from tests.tracker.conftest import FIXTURE_NOW

SCOPE = "scope/opaque"
LANE = "lane/one"
OTHER_LANE = "lane/two"
MEMBER = "KOD-901"
FIRE = "KOD-900"
DELIVERABLE = "KOD-899"
MILESTONE = ScopeRef(kind=ScopeKind.MILESTONE, key="milestone/one")

DONE = WorkflowStateKind.COMPLETED
TODO = WorkflowStateKind.UNSTARTED
REVIEW = WorkflowStateKind.STARTED


def move(
    *,
    to_kind: WorkflowStateKind = TODO,
    to_stage: LifecycleStage | None = None,
    member: str = MEMBER,
) -> CriterionStateMove:
    return CriterionStateMove(
        member_id=member,
        from_kind=DONE,
        from_stage=LifecycleStage.DONE,
        to_kind=to_kind,
        to_stage=to_stage,
    )


def lapse(member: str = MEMBER) -> CriterionStateMove:
    return move(to_kind=REVIEW, to_stage=LifecycleStage.IN_REVIEW, member=member)


def move_reading(value: CriterionStateMove, source: str = MEMBER) -> AlarmReading:
    return AlarmReading(source_ref=source, value=StateMoveEvidence(value=value))


def events_reading(
    events: tuple[RunEventProjection, ...] = (), source: str = LANE
) -> AlarmReading:
    return AlarmReading(source_ref=source, value=RunEventsEvidence(value=events))


def refutation(subject_key: str | None = MEMBER) -> RunEventProjection:
    return RunEventProjection(
        kind=RunEventKind.CRITERION_REFUTED, subject_key=subject_key
    )


def issue(
    key: str,
    state: WorkflowStateKind,
    *,
    parent: str | None = None,
) -> TrackerIssue:
    return TrackerIssue(
        issue_key=key,
        title=key,
        body="body",
        priority=IssuePriority.NONE,
        state_name=state.value,
        state_kind=state,
        queue_states=frozenset(),
        team_key=None,
        created_at=FIXTURE_NOW,
        updated_at=FIXTURE_NOW,
        url=f"https://tracker.invalid/{key}",
        parent_key=parent,
        milestone_key=MILESTONE.key,
    )


def snapshot(
    *,
    lane: str = LANE,
    fire: str = FIRE,
    members: tuple[TrackerIssue, ...] = (),
) -> LaneGraphSnapshot:
    root = issue(fire, WorkflowStateKind.STARTED)
    return LaneGraphSnapshot(
        lane_key=lane,
        fire_key=fire,
        milestone=MILESTONE,
        subtree=(root, *members),
        milestone_members=(root, *members),
        supersessions=(),
    )


def lane_readings(
    *,
    lane: str = LANE,
    holds_claim: bool = False,
    members: tuple[TrackerIssue, ...] = (),
    fire: str = FIRE,
) -> tuple[AlarmReading, AlarmReading]:
    return (
        AlarmReading(
            source_ref=lane,
            value=GraphEvidence(value=snapshot(lane=lane, fire=fire, members=members)),
        ),
        AlarmReading(source_ref=lane, value=PresenceEvidence(value=holds_claim)),
    )


def criterion(state: WorkflowStateKind = REVIEW, *, parent: str = FIRE) -> TrackerIssue:
    return issue(MEMBER, state, parent=parent)


def subject(
    *, owning_issue: str = FIRE, lane: str | None = LANE, member: str = MEMBER
) -> CriterionSubject:
    return CriterionSubject(
        scope_key=SCOPE, issue_id=owning_issue, member_id=member, lane_key=lane
    )


def regression(
    readings: tuple[AlarmReading, ...], target: AlarmSubject | None = None
) -> RunAlarm | None:
    return tally_regressed(
        subject=target if target is not None else subject(),
        readings=readings,
        raised_at_sha="tick-sha",
        raised_by="supervisor/holder",
    )


def undischarged(
    readings: tuple[AlarmReading, ...], target: AlarmSubject | None = None
) -> RunAlarm | None:
    return lapse_undischarged(
        subject=target if target is not None else subject(),
        readings=readings,
        raised_at_sha="tick-sha",
        raised_by="supervisor/holder",
    )


def test_move_out_of_done_to_todo_without_its_refutation_regresses() -> None:
    readings = (move_reading(move()), events_reading())
    alarm = regression(readings)
    assert alarm is not None
    assert alarm.subject == subject()
    assert alarm.signal is AlarmSignal.TALLY_REGRESSED
    assert alarm.bound is None
    assert alarm.readings == readings
    assert alarm.raised_at_sha == "tick-sha"
    assert alarm.raised_by == "supervisor/holder"
    restored = RunAlarm.model_validate_json(alarm.model_dump_json())
    assert regression(restored.readings) == alarm


def test_the_same_move_carrying_its_own_refutation_is_quiet() -> None:
    readings = (move_reading(move()), events_reading((refutation(),)))
    assert regression(readings) is None


@pytest.mark.parametrize(
    "events",
    [
        (),
        (refutation(subject_key=None),),
        (refutation(subject_key="KOD-902"),),
        (RunEventProjection(kind=RunEventKind.AUDIT_REFUTED, subject_key=MEMBER),),
    ],
)
def test_events_keyed_elsewhere_or_of_another_kind_discharge_nothing(
    events: tuple[RunEventProjection, ...],
) -> None:
    assert regression((move_reading(move()), events_reading(events))) is not None


@pytest.mark.parametrize("events", [(), (refutation(),)])
def test_a_lapse_out_of_done_is_no_regression(
    events: tuple[RunEventProjection, ...],
) -> None:
    assert regression((move_reading(lapse()), events_reading(events))) is None


@pytest.mark.parametrize(
    "state_move",
    [
        CriterionStateMove(member_id=MEMBER, from_kind=TODO, to_kind=TODO),
        CriterionStateMove(member_id=MEMBER, from_kind=REVIEW, to_kind=TODO),
        CriterionStateMove(
            member_id=MEMBER,
            from_kind=DONE,
            to_kind=REVIEW,
            to_stage=LifecycleStage.IN_PROGRESS,
        ),
    ],
)
def test_moves_that_leave_no_completed_state_for_todo_are_quiet(
    state_move: CriterionStateMove,
) -> None:
    assert regression((move_reading(state_move), events_reading())) is None


def test_a_stage_that_is_not_a_named_state_of_its_kind_is_unreadable() -> None:
    with pytest.raises(ValidationError):
        CriterionStateMove(
            member_id=MEMBER,
            from_kind=DONE,
            to_kind=TODO,
            to_stage=LifecycleStage.IN_REVIEW,
        )


@pytest.mark.parametrize(
    "damage",
    [
        "short",
        "long",
        "wrong-move-arm",
        "wrong-events-arm",
        "foreign-move-source",
        "foreign-lane-source",
        "foreign-member",
        "lane-less-subject",
        "lane-subject",
    ],
)
def test_incomplete_foreign_or_malformed_regression_inputs_refuse(damage: str) -> None:
    values: list[AlarmReading] = [move_reading(move()), events_reading()]
    target = subject()
    if damage == "short":
        values = values[:1]
    elif damage == "long":
        values.append(events_reading())
    elif damage == "wrong-move-arm":
        values[0] = AlarmReading(source_ref=MEMBER, value=TextEvidence(value=MEMBER))
    elif damage == "wrong-events-arm":
        values[1] = AlarmReading(source_ref=LANE, value=TextEvidence(value=LANE))
    elif damage == "foreign-move-source":
        values[0] = move_reading(move(), source="KOD-902")
    elif damage == "foreign-lane-source":
        values[1] = events_reading(source=OTHER_LANE)
    elif damage == "foreign-member":
        target = subject(member="KOD-902")
    elif damage == "lane-less-subject":
        target = subject(lane=None)
    elif damage == "lane-subject":
        with pytest.raises(RunShapeReadError) as refused:
            regression(tuple(values), LaneSubject(scope_key=SCOPE, lane_key=LANE))
        assert refused.value.signal == AlarmSignal.TALLY_REGRESSED.value
        return
    with pytest.raises(RunShapeReadError) as caught:
        regression(tuple(values), target)
    assert caught.value.signal == AlarmSignal.TALLY_REGRESSED.value


def test_a_lapse_on_a_lane_without_a_live_claim_is_undischarged() -> None:
    readings = (
        move_reading(lapse()),
        *lane_readings(members=(criterion(),)),
    )
    alarm = undischarged(readings)
    assert alarm is not None
    assert alarm.subject == subject()
    assert alarm.signal is AlarmSignal.LAPSE_UNDISCHARGED
    assert alarm.bound is None
    assert alarm.readings == readings
    restored = RunAlarm.model_validate_json(alarm.model_dump_json())
    assert undischarged(restored.readings) == alarm


def test_the_identical_lapse_on_a_lane_holding_a_live_claim_is_quiet() -> None:
    readings = (
        move_reading(lapse()),
        *lane_readings(members=(criterion(),), holds_claim=True),
    )
    assert undischarged(readings) is None


def test_a_descendant_criterion_is_owned_by_the_lane_whose_subtree_holds_it() -> None:
    deliverable = issue(DELIVERABLE, WorkflowStateKind.STARTED, parent=FIRE)
    readings = (
        move_reading(lapse()),
        *lane_readings(members=(deliverable, criterion(parent=DELIVERABLE))),
        *lane_readings(lane=OTHER_LANE, fire="KOD-800", holds_claim=True),
    )
    alarm = undischarged(readings, subject(owning_issue=DELIVERABLE))
    assert alarm is not None
    assert alarm.signal is AlarmSignal.LAPSE_UNDISCHARGED
    assert undischarged(alarm.readings, subject(owning_issue=DELIVERABLE)) == alarm


def test_a_claim_on_the_subtree_lane_quiets_a_descendant_lapse() -> None:
    deliverable = issue(DELIVERABLE, WorkflowStateKind.STARTED, parent=FIRE)
    readings = (
        move_reading(lapse()),
        *lane_readings(
            members=(deliverable, criterion(parent=DELIVERABLE)), holds_claim=True
        ),
        *lane_readings(lane=OTHER_LANE, fire="KOD-800"),
    )
    assert undischarged(readings, subject(owning_issue=DELIVERABLE)) is None


@pytest.mark.parametrize(
    "state_move",
    [
        CriterionStateMove(member_id=MEMBER, from_kind=DONE, to_kind=TODO),
        CriterionStateMove(
            member_id=MEMBER,
            from_kind=REVIEW,
            to_kind=REVIEW,
            to_stage=LifecycleStage.IN_REVIEW,
        ),
    ],
)
def test_a_move_that_is_not_a_lapse_raises_no_lapse(
    state_move: CriterionStateMove,
) -> None:
    readings = (
        move_reading(state_move),
        *lane_readings(members=(criterion(state_move.to_kind),)),
    )
    assert undischarged(readings) is None


@pytest.mark.parametrize(
    "damage",
    [
        "empty",
        "unpaired",
        "no-owning-lane",
        "two-owning-lanes",
        "repeated-lane",
        "repeated-member",
        "wrong-graph-arm",
        "wrong-claim-arm",
        "foreign-graph-source",
        "foreign-claim-source",
        "stale-member-state",
        "foreign-owning-issue",
        "parentless-criterion",
        "foreign-lane-subject",
    ],
)
def test_incomplete_foreign_or_malformed_lapse_inputs_refuse(damage: str) -> None:
    values: list[AlarmReading] = [
        move_reading(lapse()),
        *lane_readings(members=(criterion(),)),
    ]
    target = subject()
    if damage == "empty":
        values = []
    elif damage == "unpaired":
        values = values[:2]
    elif damage == "no-owning-lane":
        values[1:] = list(lane_readings())
    elif damage == "two-owning-lanes":
        values.extend(
            lane_readings(lane=OTHER_LANE, fire="KOD-800", members=(criterion(),))
        )
    elif damage == "repeated-lane":
        values.extend(lane_readings(members=(criterion(),)))
    elif damage == "repeated-member":
        values[1] = AlarmReading(
            source_ref=LANE,
            value=GraphEvidence(
                value=snapshot(members=(criterion(), criterion())).model_copy()
            ),
        )
    elif damage == "wrong-graph-arm":
        values[1] = AlarmReading(source_ref=LANE, value=TextEvidence(value=LANE))
    elif damage == "wrong-claim-arm":
        values[2] = AlarmReading(source_ref=LANE, value=TextEvidence(value=LANE))
    elif damage == "foreign-graph-source":
        values[1] = values[1].model_copy(update={"source_ref": OTHER_LANE})
    elif damage == "foreign-claim-source":
        values[2] = values[2].model_copy(update={"source_ref": OTHER_LANE})
    elif damage == "stale-member-state":
        values[1:3] = list(lane_readings(members=(criterion(DONE),)))
    elif damage == "foreign-owning-issue":
        target = subject(owning_issue=DELIVERABLE)
    elif damage == "parentless-criterion":
        values[1] = AlarmReading(
            source_ref=LANE,
            value=GraphEvidence(value=snapshot(members=(issue(MEMBER, REVIEW),))),
        )
    elif damage == "foreign-lane-subject":
        target = subject(lane=OTHER_LANE)
    with pytest.raises(RunShapeReadError) as caught:
        undischarged(tuple(values), target)
    assert caught.value.signal == AlarmSignal.LAPSE_UNDISCHARGED.value
