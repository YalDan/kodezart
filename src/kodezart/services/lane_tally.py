"""Collect a lane's acceptance claims, criterion states and open questions."""

from collections.abc import Mapping, Sequence

from kodezart.core.protocols import TrackerPort
from kodezart.domain.run_shape import (
    CRITERION_CLASSIFICATION,
    read_alarm_value,
    tally_unmoved,
)
from kodezart.services.escalation_records import EscalationRecordReader
from kodezart.services.mandate_graph import read_lane_graph
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    CriterionStateMove,
    EscalationEvidence,
    GraphEvidence,
    LaneSubject,
    ResolutionEvidence,
    RunAlarm,
    RunEventProjection,
    RunEventsEvidence,
    StateMoveEvidence,
)
from kodezart.types.domain.scope import ScopeRef


async def observe_lane_tally(
    *,
    tracker: TrackerPort,
    operation: OperationConfig,
    scope_key: str,
    lane_key: str,
    fire_key: str,
    milestone: ScopeRef,
    supersession_refs: Mapping[str, str],
    moves: Sequence[CriterionStateMove],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Read the lane's stream, subtree and recorded questions, then observe.

    Three native reads, each through the port and none of them judged
    here: the complete fire subtree, the lane's posted event stream, and
    the escalation occurrences recorded on each criterion sub-issue with
    the resolution read at that same occurrence's address. The reads are
    what this function does; the observation over them is the shared
    signal's own lane arm, replayable from the readings it returns.

    *moves* are the criterion state moves already observed by the reader
    that produces them, carried in as readings sourced at each criterion's
    own key. They are an input rather than a fourth read because no
    tracker read in this tree reports a move: the port reports a
    criterion's current state and when it entered it, and deriving where
    it came from would be this collector inventing the fact the signal
    turns on. The same values feed the criterion-state signals.

    Questions are collected for criterion sub-issues only, because they
    are the sub-issues the tally counts; a question recorded on the fire
    or on a deliverable child excludes no criterion from it. This returns
    an observation and writes nothing: recording one belongs to a
    supervisor holding its own marker lease.
    """
    subtree = await read_lane_graph(
        tracker=tracker,
        lane_key=lane_key,
        fire_key=fire_key,
        milestone=milestone,
        supersession_refs=supersession_refs,
        source_ref=lane_key,
    )
    snapshot = read_alarm_value(subtree, GraphEvidence, AlarmSignal.TALLY_UNMOVED)
    stream = await tracker.lane_run_events(issue_key=fire_key, lane_key=lane_key)
    reader = EscalationRecordReader(tracker=tracker, operation=operation)
    questions: list[AlarmReading] = []
    for issue in snapshot.subtree:
        if CRITERION_CLASSIFICATION not in issue.issue_labels:
            continue
        for comment, record in await reader.read_all(
            issue_key=issue.issue_key, lane_key=lane_key
        ):
            resolution = await tracker.read_escalation_resolution(
                issue_key=issue.issue_key,
                lane_key=lane_key,
                escalation_key=record.escalation_key,
            )
            questions.extend(
                (
                    AlarmReading(
                        source_ref=comment.comment_key,
                        value=EscalationEvidence(value=record),
                        at_sha=record.raised_at_sha,
                    ),
                    AlarmReading(
                        source_ref=comment.comment_key,
                        value=ResolutionEvidence(value=resolution),
                    ),
                )
            )
    events = AlarmReading(
        source_ref=lane_key,
        value=RunEventsEvidence(
            value=tuple(
                RunEventProjection(kind=event.kind, subject_key=event.subject_key)
                for event in stream
            )
        ),
    )
    observed_moves = tuple(
        AlarmReading(source_ref=move.member_id, value=StateMoveEvidence(value=move))
        for move in moves
    )
    return tally_unmoved(
        subject=LaneSubject(scope_key=scope_key, lane_key=lane_key),
        readings=(events, subtree, *observed_moves, *questions),
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )
