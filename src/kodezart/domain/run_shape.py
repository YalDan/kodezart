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
    LaneFieldValue,
    RunAlarm,
    surface_alarm_member_id,
)
from kodezart.types.domain.run_state import LaneCommit, LaneEscalation
from kodezart.types.domain.surface import WritableSurface

ESCALATION_COMMITS_BOUND = "run_alarm_escalation_age_max_commits"
ESCALATION_TICKS_BOUND = "run_alarm_escalation_age_max_ticks"
BARREN_FILES_BOUND = "run_alarm_barren_tick_max_files_changed"
BARREN_COMMITS_BOUND = "run_alarm_barren_tick_max_commits_ahead"
SURFACE_HOLDERS_BOUND = "run_alarm_max_surface_holders"

_ESCALATION = TypeAdapter(LaneEscalation)
_RESOLUTION = TypeAdapter(EscalationResolution)
_COMMITS = TypeAdapter(tuple[Annotated[str, Field(min_length=1, pattern=r"\S")], ...])
_COUNT = TypeAdapter(NonNegativeInt)
_REFERENCES = TypeAdapter(
    tuple[Annotated[str, Field(min_length=1, pattern=r"\S")], ...]
)
_SURFACE = TypeAdapter(WritableSurface)
_PRESENT = TypeAdapter(bool)
_IDENTITY: TypeAdapter[str] = TypeAdapter(
    Annotated[str, Field(min_length=1, pattern=r"\S")]
)
_COMMIT_ROWS = TypeAdapter(tuple[LaneCommit, ...])
_LANE_FIELD = TypeAdapter(LaneFieldValue)


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


def record_superseded(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Compare one record field with a later event asserting that same field.

    Three JSON readings: the record's LaneFieldValue, the event's
    LaneFieldValue, and the lane record's ordered commit SHA projection.
    The first two carry their asserted SHAs in at_sha; commit history names
    the same source as the record. Lane and field keys match exactly.

    Opaque values are compared after JSON decoding, never by interpreting
    their prose. Only a contrary value at a strictly later recorded position
    supersedes the record. Equal or earlier positions cannot do so. Missing
    or repeated commit identities refuse observation, including when the
    values agree, because incomplete history cannot establish a clean read.
    """
    signal = AlarmSignal.RECORD_SUPERSEDED
    try:
        record, event, commits = readings
    except ValueError as exc:
        raise _unreadable(signal, subject.scope_key, "incomplete readings") from exc
    recorded = _decode(record, _LANE_FIELD, signal)
    asserted = _decode(event, _LANE_FIELD, signal)
    order = _decode(commits, _COMMITS, signal)
    if (
        subject.kind is not AlarmSubjectKind.LANE
        or subject.lane_key != recorded.lane_key
        or asserted.lane_key != recorded.lane_key
    ):
        raise _unreadable(signal, event.source_ref, "readings identify different lanes")
    if asserted.field_key != recorded.field_key:
        raise _unreadable(
            signal, event.source_ref, "assertions identify different fields"
        )
    if commits.source_ref != record.source_ref:
        raise _unreadable(
            signal, commits.source_ref, "history identifies another record"
        )
    if len(set(order)) != len(order):
        raise _unreadable(
            signal, commits.source_ref, "recorded commit order repeats a SHA"
        )
    record_sha, event_sha = record.at_sha, event.at_sha
    if record_sha is None or record_sha not in order:
        raise _unreadable(
            signal, record.source_ref, "assertion SHA is absent from recorded history"
        )
    if event_sha is None or event_sha not in order:
        raise _unreadable(
            signal, event.source_ref, "assertion SHA is absent from recorded history"
        )
    if recorded.value == asserted.value:
        return None
    if order.index(event_sha) <= order.index(record_sha):
        return None
    return RunAlarm(
        subject=subject,
        signal=signal,
        readings=readings,
        bound=None,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )


def write_back_missing(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Observe one event's explicit write obligation against a completed read.

    Two JSON readings: the event's owed WritableSurface, then a strict
    boolean recording whether its keyed record exists. The event reference
    is the first source; the canonical complete surface address is the
    second. The caller supplies the event's declared target and a successful
    complete lookup, without deriving either from event prose. An unreadable
    lookup cannot supply a false presence value.

    Event vocabulary, event-to-target projection and record collection are
    owned by their producers. This predicate consumes their explicit facts.
    """
    signal = AlarmSignal.WRITE_BACK_MISSING
    try:
        event_target, record_presence = readings
    except ValueError as exc:
        raise _unreadable(signal, subject.scope_key, "incomplete readings") from exc
    owed = _decode(event_target, _SURFACE, signal)
    address = surface_alarm_member_id(owed)
    if subject.kind is not AlarmSubjectKind.SURFACE or subject.member_id != address:
        raise _unreadable(
            signal, event_target.source_ref, "subject identifies another surface"
        )
    if record_presence.source_ref != address:
        raise _unreadable(
            signal,
            record_presence.source_ref,
            "record lookup identifies another surface",
        )
    present = _decode(record_presence, _PRESENT, signal)
    if present:
        return None
    return RunAlarm(
        subject=subject,
        signal=signal,
        readings=readings,
        bound=None,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )


def commits_ahead_of_record(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Compare the lane record's own count and enumerated commit rows.

    Four JSON readings from the same record: lane key, declared head SHA,
    commits-ahead count, and the ordered LaneCommit rows. A supplied reading
    SHA must identify that declared head. The head is recorded evidence;
    this predicate never resolves it against a repository. If the whole
    record is stale and both counts still agree, this signal cannot see it.

    Equality has no configured bound. Either direction of disagreement
    violates it; repeated commit identities refuse an ambiguous observation.
    """
    signal = AlarmSignal.COMMITS_AHEAD_OF_RECORD
    try:
        lane, head, count, rows = readings
    except ValueError as exc:
        raise _unreadable(signal, subject.scope_key, "incomplete readings") from exc
    lane_key = _decode(lane, _IDENTITY, signal)
    declared_head = _decode(head, _IDENTITY, signal)
    declared_count = _decode(count, _COUNT, signal)
    commits = _decode(rows, _COMMIT_ROWS, signal)
    if subject.kind is not AlarmSubjectKind.LANE or subject.lane_key != lane_key:
        raise _unreadable(signal, lane.source_ref, "subject identifies another lane")
    for reading in readings:
        if reading.source_ref != lane.source_ref:
            raise _unreadable(
                signal, reading.source_ref, "readings identify different lane records"
            )
        if reading.at_sha is not None and reading.at_sha != declared_head:
            raise _unreadable(
                signal, reading.source_ref, "reading SHA differs from the declared head"
            )
    identities = tuple(commit.sha for commit in commits)
    if any(not sha.strip() for sha in identities) or len(set(identities)) != len(
        identities
    ):
        raise _unreadable(
            signal, rows.source_ref, "ambiguous recorded commit identities"
        )
    if declared_count == len(commits):
        return None
    return RunAlarm(
        subject=subject,
        signal=signal,
        readings=readings,
        bound=None,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )


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


def surface_contended(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Count explicit run-holder identities for one complete surface address.

    Three JSON readings, in order: the WritableSurface address, its ordered
    holder history, and the configured distinct-holder limit. Address and
    history must name the same provenance source. Holder identities are
    opaque job identities supplied by the provenance reader; this function
    cannot infer them from account authors, timestamps or text.

    Repeated writes by one holder count once. Different runs holding the
    same address remain in that address's history, so they use this same
    signal arm. Original readings, including their order, survive replay.
    """
    signal = AlarmSignal.SURFACE_CONTENDED
    try:
        surface, history, max_holders = readings
    except ValueError as exc:
        raise _unreadable(signal, subject.scope_key, "incomplete readings") from exc
    address = _decode(surface, _SURFACE, signal)
    if (
        subject.kind is not AlarmSubjectKind.SURFACE
        or subject.member_id != surface_alarm_member_id(address)
    ):
        raise _unreadable(
            signal, surface.source_ref, "subject identifies another surface"
        )
    if surface.source_ref != history.source_ref:
        raise _unreadable(
            signal, history.source_ref, "holder history identifies another source"
        )
    if max_holders.source_ref != SURFACE_HOLDERS_BOUND:
        raise _unreadable(
            signal,
            max_holders.source_ref,
            "holder bound does not name its AppConfig field",
        )
    holders = _decode(history, _REFERENCES, signal)
    configured = _decode(max_holders, _COUNT, signal)
    observed = len(set(holders))
    if observed > configured:
        return RunAlarm(
            subject=subject,
            signal=signal,
            readings=readings,
            bound=AlarmBound(
                config_field=max_holders.source_ref,
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
