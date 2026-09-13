"""Read-only tracker collection for mandate and graph observations."""

from collections.abc import Mapping

from pydantic import ValidationError

from kodezart.core.config import AppConfig
from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.gap import compute_gap
from kodezart.domain.mandate_graph import (
    RULINGS_BOUND,
    lane_graph_members,
    rulings_outpace_closures,
    structural_write_uncrosses_milestone,
)
from kodezart.domain.run_shape import read_alarm_value
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.types.domain.mandate_graph import (
    IssueSupersession,
    LaneGraphSnapshot,
    LaneRulingSnapshot,
    RulingAuthorship,
)
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    AlarmSubjectKind,
    CountEvidence,
    GraphEvidence,
    ReferencesEvidence,
    RulingsEvidence,
    RunAlarm,
)
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import TrackerIssue


async def read_lane_rulings(
    *,
    tracker: TrackerPort,
    operation: OperationConfig,
    lane_key: str,
    issue_keys: tuple[str, ...],
    source_ref: str,
) -> AlarmReading:
    """Read full native ruling artifacts for every declared lane issue.

    The caller supplies current lane membership and the retained window's
    source identity. Only the decoded ruling's required authored_by field
    supplies authorship; the tracker account has no role in this projection.
    """
    try:
        empty = LaneRulingSnapshot(lane_key=lane_key, issue_keys=issue_keys, rulings=())
        AlarmReading(source_ref=source_ref, value=RulingsEvidence(value=empty))
    except ValidationError as exc:
        raise RunShapeReadError(
            signal=AlarmSignal.RULINGS_OUTPACE_CLOSURES.value,
            source_ref=source_ref,
            reason="invalid lane or window identity",
        ) from exc
    if len(set(issue_keys)) != len(issue_keys):
        raise RunShapeReadError(
            signal=AlarmSignal.RULINGS_OUTPACE_CLOSURES.value,
            source_ref=source_ref,
            reason="the lane lists an issue more than once",
        )
    reader = RulingRecordReader(tracker=tracker, operation=operation)
    rows: list[RulingAuthorship] = []
    for issue_key in issue_keys:
        records = await reader.read_all(issue_key=issue_key, lane_key=lane_key)
        rows.extend(
            RulingAuthorship(
                ruling_id=ruling.ruling_id,
                issue_key=ruling.issue_ref,
                authored_by=ruling.authored_by,
            )
            for _, ruling in records
        )
    snapshot = LaneRulingSnapshot(
        lane_key=lane_key, issue_keys=issue_keys, rulings=tuple(rows)
    )
    return AlarmReading(source_ref=source_ref, value=RulingsEvidence(value=snapshot))


async def observe_recorded_ruling_growth(
    *,
    tracker: TrackerPort,
    operation: OperationConfig,
    config: AppConfig,
    subject: AlarmSubject,
    issue_keys: tuple[str, ...],
    baseline_rulings: AlarmReading,
    previous_open: AlarmReading,
    supersession_refs: Mapping[str, str],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Combine current native ruling records and live criterion closure.

    Baseline retention and advancement remain the window writer's job. The
    returned alarm is an observation, not a tracker publication.
    """
    if subject.kind is not AlarmSubjectKind.LANE or subject.lane_key is None:
        raise RunShapeReadError(
            signal=AlarmSignal.RULINGS_OUTPACE_CLOSURES.value,
            source_ref=baseline_rulings.source_ref,
            reason="a lane subject is required",
        )
    current = await read_lane_rulings(
        tracker=tracker,
        operation=operation,
        lane_key=subject.lane_key,
        issue_keys=issue_keys,
        source_ref=baseline_rulings.source_ref,
    )
    return await observe_ruling_growth(
        tracker=tracker,
        config=config,
        subject=subject,
        baseline_rulings=baseline_rulings,
        current_rulings=current,
        previous_open=previous_open,
        supersession_refs=supersession_refs,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )


async def read_lane_graph(
    *,
    tracker: TrackerPort,
    lane_key: str,
    fire_key: str,
    milestone: ScopeRef,
    supersession_refs: Mapping[str, str],
    source_ref: str,
) -> AlarmReading:
    """Collect the complete fire subtree and native milestone membership.

    The port owns pagination, archived-member inclusion and hydration.
    Conflicting shared members refuse observation rather than pretending
    these sequential reads are an atomic snapshot. The caller retains the
    returned reading to compare with a later collection.
    """
    subtree = await tracker.scope_issues(
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=fire_key)
    )
    members = await tracker.scope_issues(ref=milestone)
    snapshot = LaneGraphSnapshot(
        lane_key=lane_key,
        fire_key=fire_key,
        milestone=milestone,
        subtree=tuple(subtree),
        milestone_members=tuple(members),
        supersessions=tuple(
            IssueSupersession(issue_key=key, source_ref=ref)
            for key, ref in supersession_refs.items()
        ),
    )
    lane_graph_members(snapshot, source_ref=source_ref)
    return AlarmReading(
        source_ref=source_ref,
        value=GraphEvidence(value=snapshot),
    )


async def observe_structural_write(
    *,
    tracker: TrackerPort,
    subject: AlarmSubject,
    previous: AlarmReading,
    fire_key: str,
    milestone: ScopeRef,
    supersession_refs: Mapping[str, str],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Read current membership and compare it with a retained prior graph."""
    if subject.kind is not AlarmSubjectKind.LANE or subject.lane_key is None:
        raise RunShapeReadError(
            signal=AlarmSignal.STRUCTURAL_WRITE_UNCROSSES_MILESTONE.value,
            source_ref=previous.source_ref,
            reason="a lane subject is required",
        )
    current = await read_lane_graph(
        tracker=tracker,
        lane_key=subject.lane_key,
        fire_key=fire_key,
        milestone=milestone,
        supersession_refs=supersession_refs,
        source_ref=previous.source_ref,
    )
    return structural_write_uncrosses_milestone(
        subject=subject,
        readings=(previous, current),
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )


async def observe_ruling_growth(
    *,
    tracker: TrackerPort,
    config: AppConfig,
    subject: AlarmSubject,
    baseline_rulings: AlarmReading,
    current_rulings: AlarmReading,
    previous_open: AlarmReading,
    supersession_refs: Mapping[str, str],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Combine recorded ruling projections with live criterion closure.

    The ruling reader must supply the explicit required authorship field
    and identities, plus the snapshot retained at the last closure. Missing
    authorship refuses typed decoding; it never borrows a transport author.
    This service reads current criteria and uses the shared gap arithmetic.
    Recording the next window and publishing alarms belong to their writers.
    """
    snapshot = read_alarm_value(
        current_rulings, RulingsEvidence, AlarmSignal.RULINGS_OUTPACE_CLOSURES
    )
    criteria: list[TrackerIssue] = []
    for issue_key in snapshot.issue_keys:
        criteria.extend(await tracker.read_criteria(issue_key=issue_key))
    gap = compute_gap(criteria, supersession_refs=supersession_refs)
    open_keys = {criterion.issue_key for criterion in gap}
    closed = tuple(
        criterion.issue_key
        for criterion in criteria
        if criterion.issue_key not in open_keys
    )
    return rulings_outpace_closures(
        subject=subject,
        readings=(
            baseline_rulings,
            current_rulings,
            previous_open,
            AlarmReading(
                source_ref=baseline_rulings.source_ref,
                value=ReferencesEvidence(value=closed),
            ),
            AlarmReading(
                source_ref=RULINGS_BOUND,
                value=CountEvidence(value=config.run_alarm_max_rulings_without_closure),
            ),
        ),
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )
