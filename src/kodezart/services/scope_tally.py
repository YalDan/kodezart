"""Actual native roster and configured marker collection for scope observations."""

from pydantic import TypeAdapter

from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.organize import is_organize_subject
from kodezart.domain.run_shape import (
    CRITERIA_MARKER_SOURCE,
    GROOM_MARKER_SOURCE,
    TICKET_MARKER_SOURCE,
    tally_unmoved,
)
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.organize import MandateKind, split_label_key
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    AlarmSubjectKind,
    RunAlarm,
)
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.tracker import TrackerIssue

_LABELS = TypeAdapter(tuple[str, ...])
_KEY = TypeAdapter(str)
# The governed phase sequence is graph, body, then criteria. Configuration
# table order carries no ordering authority.
_MARKER_TRANSITIONS = {
    MandateKind.GROOM: (MandateKind.TICKET, GROOM_MARKER_SOURCE, TICKET_MARKER_SOURCE),
    MandateKind.TICKET: (
        MandateKind.CRITERIA,
        TICKET_MARKER_SOURCE,
        CRITERIA_MARKER_SOURCE,
    ),
}


def _refuse(scope: ScopeRef, reason: str) -> RunShapeReadError:
    return RunShapeReadError(
        signal=AlarmSignal.TALLY_UNMOVED.value, source_ref=scope.key, reason=reason
    )


async def _read_members(
    *, tracker: TrackerPort, scope: ScopeRef
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
    tracker: TrackerPort,
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
    if phase not in _MARKER_TRANSITIONS:
        raise _refuse(
            scope, "execution entry requires a native lane-dispatched event reader"
        )
    next_phase, current_source, next_source = _MARKER_TRANSITIONS[phase]
    phases = {row.spec.kind: row.spec for row in operation.resolve_organize_mandates()}
    if phase not in phases or next_phase not in phases:
        raise OperationMemberAbsentError(
            missing="organize_mandates", stops="scope phase markers cannot be read"
        )
    current = phases[phase].terminal_marker_key
    following = phases[next_phase].terminal_marker_key
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
        AlarmReading(source_ref=current_source, value=_KEY.dump_json(current).decode()),
        AlarmReading(source_ref=next_source, value=_KEY.dump_json(following).decode()),
        AlarmReading(source_ref=scope.key, value=scope.model_dump_json(by_alias=True)),
        AlarmReading(source_ref=scope.key, value=_LABELS.dump_json(roster).decode()),
        *(
            AlarmReading(
                source_ref=key,
                value=_LABELS.dump_json(
                    tuple(sorted(facts[key].issue_labels))
                ).decode(),
            )
            for key in roster
        ),
    )
    return tally_unmoved(
        subject=AlarmSubject(kind=AlarmSubjectKind.SCOPE, scope_key=scope.key),
        readings=readings,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )
