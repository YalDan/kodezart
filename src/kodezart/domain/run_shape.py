"""Run-shape predicates over recorded values, with no side effects."""

from typing import Annotated

from pydantic import Field, NonNegativeInt, TypeAdapter, ValidationError

from kodezart.domain.errors import RunShapeReadError
from kodezart.types.domain.escalation import (
    EscalationResolution,
    EscalationResolutionState,
)
from kodezart.types.domain.run_alarm import (
    AlarmBound,
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    AlarmSubjectKind,
    RunAlarm,
)
from kodezart.types.domain.run_state import LaneEscalation

ESCALATION_COMMITS_BOUND = "run_alarm_escalation_age_max_commits"
ESCALATION_TICKS_BOUND = "run_alarm_escalation_age_max_ticks"
BARREN_FILES_BOUND = "run_alarm_barren_tick_max_files_changed"
BARREN_COMMITS_BOUND = "run_alarm_barren_tick_max_commits_ahead"

_ESCALATION = TypeAdapter(LaneEscalation)
_RESOLUTION = TypeAdapter(EscalationResolution)
_COMMITS = TypeAdapter(tuple[Annotated[str, Field(min_length=1, pattern=r"\S")], ...])
_COUNT = TypeAdapter(NonNegativeInt)
_REFERENCES = TypeAdapter(
    tuple[Annotated[str, Field(min_length=1, pattern=r"\S")], ...]
)


def _unreadable(signal: AlarmSignal, source_ref: str, reason: str) -> RunShapeReadError:
    return RunShapeReadError(
        signal=signal.value,
        source_ref=source_ref,
        reason=reason,
    )


def _decode[T](
    reading: AlarmReading, adapter: TypeAdapter[T], signal: AlarmSignal
) -> T:
    try:
        return adapter.validate_json(reading.value, strict=True)
    except ValidationError as exc:
        raise _unreadable(signal, reading.source_ref, "invalid recorded value") from exc


def escalation_ageing(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Measure an unanswered occurrence on its lane's two recorded counters.

    Readings are ordered: the escalation value, its resolution value, the
    recorded commit SHA sequence, ticks since raise, and the two configured
    count limits (commits then ticks). Values use JSON; their original bytes
    and source references survive in the alarm. The resolution names the
    same escalation source, and bound sources are AppConfig field names.

    A counter must strictly exceed its limit to fire its own arm.
    Either arm raises the one subject/signal alarm; if both fire, the commit
    bound is reported first. Both readings remain available for replay.
    Missing or ambiguous history refuses observation instead of clearing it.
    """
    signal = AlarmSignal.ESCALATION_AGEING
    try:
        escalation, resolution, commits, ticks, max_commits, max_ticks = readings
    except ValueError as exc:
        raise _unreadable(
            signal, subject.member_id or subject.scope_key, "incomplete readings"
        ) from exc

    record = _decode(escalation, _ESCALATION, signal)
    answer = _decode(resolution, _RESOLUTION, signal)
    if (
        subject.kind is not AlarmSubjectKind.ESCALATION
        or subject.member_id != record.escalation_key
        or subject.issue_id != record.issue_id
        or subject.lane_key is None
    ):
        raise _unreadable(
            signal, escalation.source_ref, "subject does not identify this escalation"
        )
    if resolution.source_ref != escalation.source_ref:
        raise _unreadable(
            signal, resolution.source_ref, "resolution identifies another escalation"
        )
    if (max_commits.source_ref, max_ticks.source_ref) != (
        ESCALATION_COMMITS_BOUND,
        ESCALATION_TICKS_BOUND,
    ):
        raise _unreadable(
            signal, subject.member_id, "age bounds do not name their AppConfig fields"
        )

    commit_order = _decode(commits, _COMMITS, signal)
    tick_age = _decode(ticks, _COUNT, signal)
    commit_limit = _decode(max_commits, _COUNT, signal)
    tick_limit = _decode(max_ticks, _COUNT, signal)
    if len(set(commit_order)) != len(commit_order):
        raise _unreadable(
            signal, commits.source_ref, "recorded commit order repeats a SHA"
        )
    if record.raised_at_sha not in commit_order:
        raise _unreadable(
            signal, commits.source_ref, "recorded commit order omits the raise SHA"
        )
    commit_age = len(commit_order) - commit_order.index(record.raised_at_sha) - 1

    if answer.state is EscalationResolutionState.RESOLVED:
        return None
    for reading, configured, observed in (
        (max_commits, commit_limit, commit_age),
        (max_ticks, tick_limit, tick_age),
    ):
        if observed > configured:
            return RunAlarm(
                subject=subject,
                signal=AlarmSignal.ESCALATION_AGEING,
                readings=readings,
                bound=AlarmBound(
                    config_field=reading.source_ref,
                    configured_value=configured,
                    observed_value=observed,
                ),
                raised_at_sha=raised_at_sha,
                raised_by=raised_by,
            )
    return None


def barren_tick_with_diff_growth(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Observe recorded growth without closure of any previously-open identity.

    Six JSON readings, in order: previously-open reference identities,
    currently-closed identities, recorded files changed, recorded commits
    ahead, and the configured limits for files and commits. A newly-created
    closed reference or a reference missing from the current snapshot is
    not a closure of previous work. No text is interpreted as an identity.
    Both growth terms are against the lane base, as recorded by its owner.

    Either count strictly exceeding its limit fires; files take precedence
    when both do. All original readings remain in order for exact replay.
    """
    signal = AlarmSignal.BARREN_TICK_WITH_DIFF_GROWTH
    try:
        previous, current, files, commits, max_files, max_commits = readings
    except ValueError as exc:
        raise _unreadable(signal, subject.scope_key, "incomplete readings") from exc
    if subject.kind is not AlarmSubjectKind.LANE:
        raise _unreadable(
            signal, subject.scope_key, "a barren tick requires a lane subject"
        )
    if (max_files.source_ref, max_commits.source_ref) != (
        BARREN_FILES_BOUND,
        BARREN_COMMITS_BOUND,
    ):
        raise _unreadable(
            signal,
            subject.scope_key,
            "growth bounds do not name their AppConfig fields",
        )
    previous_open = _decode(previous, _REFERENCES, signal)
    current_closed = _decode(current, _REFERENCES, signal)
    for reading, identities in ((previous, previous_open), (current, current_closed)):
        if len(set(identities)) != len(identities):
            raise _unreadable(
                signal,
                reading.source_ref,
                "a reference identity appears more than once",
            )
    files_changed = _decode(files, _COUNT, signal)
    commits_ahead = _decode(commits, _COUNT, signal)
    file_limit = _decode(max_files, _COUNT, signal)
    commit_limit = _decode(max_commits, _COUNT, signal)
    if set(previous_open) & set(current_closed):
        return None
    for reading, configured, observed in (
        (max_files, file_limit, files_changed),
        (max_commits, commit_limit, commits_ahead),
    ):
        if observed > configured:
            return RunAlarm(
                subject=subject,
                signal=signal,
                readings=readings,
                bound=AlarmBound(
                    config_field=reading.source_ref,
                    configured_value=configured,
                    observed_value=observed,
                ),
                raised_at_sha=raised_at_sha,
                raised_by=raised_by,
            )
    return None
