"""Read-only tracker collection for mandate and graph observations."""

from collections.abc import Mapping

from pydantic import TypeAdapter, ValidationError

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
from kodezart.types.domain.mandate_graph import (
    IssueSupersession,
    LaneGraphSnapshot,
    LaneRulingSnapshot,
)
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    AlarmSubjectKind,
    RunAlarm,
)
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import TrackerIssue

_REFS = TypeAdapter(tuple[str, ...])


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
        value=snapshot.model_dump_json(by_alias=True),
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
    try:
        snapshot = LaneRulingSnapshot.model_validate_json(
            current_rulings.value, strict=True
        )
    except ValidationError as exc:
        raise RunShapeReadError(
            signal=AlarmSignal.RULINGS_OUTPACE_CLOSURES.value,
            source_ref=current_rulings.source_ref,
            reason="invalid ruling projection",
        ) from exc
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
                value=_REFS.dump_json(closed).decode("utf-8"),
            ),
            AlarmReading(
                source_ref=RULINGS_BOUND,
                value=str(config.run_alarm_max_rulings_without_closure),
            ),
        ),
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )
