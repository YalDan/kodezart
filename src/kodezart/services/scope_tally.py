"""Actual native roster and configured marker collection for scope observations."""

from kodezart.core.protocols import ScopeRosterReader
from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.organize import is_organize_subject
from kodezart.domain.run_shape import tally_unmoved
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.organize import (
    MANDATE_PHASE_ROLES,
    MandateKind,
    phase_successor,
    split_label_key,
)
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    LabelsEvidence,
    ReferencesEvidence,
    RunAlarm,
    ScopeEvidence,
    ScopeSubject,
    TextEvidence,
)
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.tracker import TrackerIssue


def _refuse(scope: ScopeRef, reason: str) -> RunShapeReadError:
    return RunShapeReadError(
        signal=AlarmSignal.TALLY_UNMOVED.value, source_ref=scope.key, reason=reason
    )


async def _read_members(
    *, tracker: ScopeRosterReader, scope: ScopeRef
) -> dict[str, TrackerIssue]:
    facts: dict[str, TrackerIssue] = {}
    for issue in await tracker.scope_issues(ref=scope):
        if issue.issue_key in facts:
            raise _refuse(scope, "scope roster repeats a member")
        try:
            current = await tracker.read_planning_issue(issue_key=issue.issue_key)
        except LookupError as exc:
            raise _refuse(
                scope, "scope member disappeared during classification"
            ) from exc
        if current != issue:
            raise _refuse(
                scope, "scope membership or classification changed during read"
            )
        facts[issue.issue_key] = current
    return facts


async def observe_scope_tally(
    *,
    tracker: ScopeRosterReader,
    operation: OperationConfig,
    scope: ScopeRef,
    phase: MandateKind,
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Read the actual ORGANIZE roster, then replay the marker-only signal.

    The graph-to-body and body-to-criteria transitions are supported. The
    execution transition needs each member's native lane-dispatched event;
    absence of that reader refuses before any query. No run event is inferred
    from labels or issue workflow states. This collector returns an observation
    and performs no publication, repository read or model session.
    """
    next_phase = phase_successor(phase)
    if next_phase is None:
        raise _refuse(
            scope, "execution entry requires a native lane-dispatched event reader"
        )
    phases = {row.spec.kind: row for row in operation.resolve_organize_mandates()}
    if phase not in phases or next_phase not in phases:
        raise OperationMemberAbsentError(
            missing="organize_mandates", stops="scope phase markers cannot be read"
        )
    current_source = phases[phase].marker_source
    next_source = phases[next_phase].marker_source
    current = phases[phase].spec.terminal_marker_key
    following = phases[next_phase].spec.terminal_marker_key
    current_key, next_key = split_label_key(current)[1], split_label_key(following)[1]
    if (
        current_key == next_key
        or operation.issue_labels[current_key] == operation.issue_labels[next_key]
    ):
        raise _refuse(scope, "phase markers do not distinguish entry from completion")
    tracker.require_issue_classification_reads(
        additional_keys=frozenset({current_key, next_key})
    )
    classification_labels = {
        label
        for key in ("criterion", "tracker", "decision")
        if (label := operation.issue_labels.get(key)) is not None
    }
    if classification_labels & {
        operation.issue_labels[current_key],
        operation.issue_labels[next_key],
    }:
        raise _refuse(scope, "phase marker aliases a roster classification")
    facts = await _read_members(tracker=tracker, scope=scope)
    if await _read_members(tracker=tracker, scope=scope) != facts:
        raise _refuse(scope, "scope roster or marker facts changed during observation")
    roster = tuple(
        sorted(key for key, issue in facts.items() if is_organize_subject(issue))
    )
    readings = (
        AlarmReading(source_ref=current_source, value=TextEvidence(value=current)),
        AlarmReading(source_ref=next_source, value=TextEvidence(value=following)),
        AlarmReading(source_ref=scope.key, value=ScopeEvidence(value=scope)),
        AlarmReading(source_ref=scope.key, value=ReferencesEvidence(value=roster)),
        *(
            AlarmReading(
                source_ref=key,
                value=LabelsEvidence(value=tuple(sorted(facts[key].issue_labels))),
            )
            for key in roster
        ),
    )
    return tally_unmoved(
        subject=ScopeSubject(scope_key=scope.key),
        readings=readings,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )


async def observe_scope_barrier(
    *,
    tracker: ScopeRosterReader,
    operation: OperationConfig,
    scope: ScopeRef,
    raised_at_sha: str,
    raised_by: str,
) -> tuple[RunAlarm, ...]:
    """The scope's stage barrier at every rung that has a later stage.

    The rungs are read off ``phase_successor``, the same function the
    collector refuses the last rung with, so the two cannot disagree about
    which rungs exist. Every open rung's alarm is returned, in the governed
    order, so two barriers open at once are two alarms. It holds the roster
    role alone, as the collector does: nothing it is handed can read a
    stream or write anything.
    """
    raised: list[RunAlarm] = []
    for rung in MANDATE_PHASE_ROLES:
        if phase_successor(rung) is None:
            continue
        alarm = await observe_scope_tally(
            tracker=tracker,
            operation=operation,
            scope=scope,
            phase=rung,
            raised_at_sha=raised_at_sha,
            raised_by=raised_by,
        )
        if alarm is not None:
            raised.append(alarm)
    return tuple(raised)
