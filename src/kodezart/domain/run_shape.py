"""Run-shape predicates over recorded values, with no side effects."""

from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.run_alarm_record import surface_alarm_member_id
from kodezart.types.domain.escalation import EscalationResolutionState
from kodezart.types.domain.organize import (
    OrganizeLabelNamespace,
    phase_marker_source,
    split_label_key,
)
from kodezart.types.domain.run_alarm import (
    AlarmBound,
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    AlarmSubjectKind,
    CommitsEvidence,
    CountEvidence,
    EscalationEvidence,
    Evidence,
    LabelsEvidence,
    LaneFieldEvidence,
    LaneSubject,
    PresenceEvidence,
    ReferencesEvidence,
    ResolutionEvidence,
    RunAlarm,
    ScopeEvidence,
    SurfaceEvidence,
    TallyEvidence,
    TextEvidence,
)

ESCALATION_COMMITS_BOUND = "run_alarm_escalation_age_max_commits"
ESCALATION_TICKS_BOUND = "run_alarm_escalation_age_max_ticks"
BARREN_FILES_BOUND = "run_alarm_barren_tick_max_files_changed"
BARREN_COMMITS_BOUND = "run_alarm_barren_tick_max_commits_ahead"
SURFACE_HOLDERS_BOUND = "run_alarm_max_surface_holders"
COMMITS_WITHOUT_CLOSURE_BOUND = "run_alarm_max_commits_without_closure"


def unreadable_reading(
    signal: AlarmSignal, source_ref: str, reason: str
) -> RunShapeReadError:
    """One refusal shape for every reading a signal cannot read.

    Public, because the signals live in more than one module and each of
    them refuses the same way: a second spelling of this refusal would be a
    second answer to "what does an unreadable reading report".
    """
    return RunShapeReadError(
        signal=signal.value,
        source_ref=source_ref,
        reason=reason,
    )


def read_alarm_value[T](
    reading: AlarmReading, expected: type[Evidence[T]], signal: AlarmSignal
) -> T:
    """Extract a typed projection or refuse with its observed source identity."""
    value = reading.value
    if not isinstance(value, expected):
        raise unreadable_reading(
            signal, reading.source_ref, "another evidence kind was recorded"
        )
    return value.value


def _identity(reading: AlarmReading, signal: AlarmSignal) -> str:
    value = read_alarm_value(reading, TextEvidence, signal)
    if not value.strip():
        raise unreadable_reading(
            signal, reading.source_ref, "recorded identity is empty"
        )
    return value


def record_superseded(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Compare one record field with a later event asserting that same field.

    Three typed readings: the record's LaneFieldValue, the event's
    LaneFieldValue, and the lane record's ordered commit SHA projection.
    The first two carry their asserted SHAs in at_sha; commit history names
    the same source as the record. Lane and field keys match exactly.

    Opaque values are compared as typed field values, never by interpreting
    their prose. Only a contrary value at a strictly later recorded position
    supersedes the record. Equal or earlier positions cannot do so. Missing
    or repeated commit identities refuse observation, including when the
    values agree, because incomplete history cannot establish a clean read.
    """
    signal = AlarmSignal.RECORD_SUPERSEDED
    try:
        record, event, commits = readings
    except ValueError as exc:
        raise unreadable_reading(
            signal, subject.scope_key, "incomplete readings"
        ) from exc
    recorded = read_alarm_value(record, LaneFieldEvidence, signal)
    asserted = read_alarm_value(event, LaneFieldEvidence, signal)
    order = read_alarm_value(commits, ReferencesEvidence, signal)
    if (
        subject.kind is not AlarmSubjectKind.LANE
        or subject.lane_key != recorded.lane_key
        or asserted.lane_key != recorded.lane_key
    ):
        raise unreadable_reading(
            signal, event.source_ref, "readings identify different lanes"
        )
    if asserted.field_key != recorded.field_key:
        raise unreadable_reading(
            signal, event.source_ref, "assertions identify different fields"
        )
    if commits.source_ref != record.source_ref:
        raise unreadable_reading(
            signal, commits.source_ref, "history identifies another record"
        )
    if len(set(order)) != len(order):
        raise unreadable_reading(
            signal, commits.source_ref, "recorded commit order repeats a SHA"
        )
    record_sha, event_sha = record.at_sha, event.at_sha
    if record_sha is None or record_sha not in order:
        raise unreadable_reading(
            signal, record.source_ref, "assertion SHA is absent from recorded history"
        )
    if event_sha is None or event_sha not in order:
        raise unreadable_reading(
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

    Two typed readings: the event's owed WritableSurface, then a strict
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
        raise unreadable_reading(
            signal, subject.scope_key, "incomplete readings"
        ) from exc
    owed = read_alarm_value(event_target, SurfaceEvidence, signal)
    address = surface_alarm_member_id(owed)
    if subject.kind is not AlarmSubjectKind.SURFACE or subject.surface != owed:
        raise unreadable_reading(
            signal, event_target.source_ref, "subject identifies another surface"
        )
    if record_presence.source_ref != address:
        raise unreadable_reading(
            signal,
            record_presence.source_ref,
            "record lookup identifies another surface",
        )
    present = read_alarm_value(record_presence, PresenceEvidence, signal)
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

    Four typed readings from the same record: lane key, declared head SHA,
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
        raise unreadable_reading(
            signal, subject.scope_key, "incomplete readings"
        ) from exc
    lane_key = _identity(lane, signal)
    declared_head = _identity(head, signal)
    declared_count = read_alarm_value(count, CountEvidence, signal)
    commits = read_alarm_value(rows, CommitsEvidence, signal)
    if subject.kind is not AlarmSubjectKind.LANE or subject.lane_key != lane_key:
        raise unreadable_reading(
            signal, lane.source_ref, "subject identifies another lane"
        )
    for reading in readings:
        if reading.source_ref != lane.source_ref:
            raise unreadable_reading(
                signal, reading.source_ref, "readings identify different lane records"
            )
        if reading.at_sha is not None and reading.at_sha != declared_head:
            raise unreadable_reading(
                signal, reading.source_ref, "reading SHA differs from the declared head"
            )
    identities = tuple(commit.sha for commit in commits)
    if any(not sha.strip() for sha in identities) or len(set(identities)) != len(
        identities
    ):
        raise unreadable_reading(
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
    count limits (commits then ticks). Values remain typed; their original values
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
        raise unreadable_reading(
            signal, subject.scope_key, "incomplete readings"
        ) from exc

    record = read_alarm_value(escalation, EscalationEvidence, signal)
    answer = read_alarm_value(resolution, ResolutionEvidence, signal)
    if (
        subject.kind is not AlarmSubjectKind.ESCALATION
        or subject.member_id != record.escalation_key
        or subject.issue_id != record.issue_id
        or subject.lane_key is None
    ):
        raise unreadable_reading(
            signal, escalation.source_ref, "subject does not identify this escalation"
        )
    if resolution.source_ref != escalation.source_ref:
        raise unreadable_reading(
            signal, resolution.source_ref, "resolution identifies another escalation"
        )
    if (max_commits.source_ref, max_ticks.source_ref) != (
        ESCALATION_COMMITS_BOUND,
        ESCALATION_TICKS_BOUND,
    ):
        raise unreadable_reading(
            signal, subject.member_id, "age bounds do not name their AppConfig fields"
        )

    commit_order = read_alarm_value(commits, ReferencesEvidence, signal)
    tick_age = read_alarm_value(ticks, CountEvidence, signal)
    commit_limit = read_alarm_value(max_commits, CountEvidence, signal)
    tick_limit = read_alarm_value(max_ticks, CountEvidence, signal)
    if len(set(commit_order)) != len(commit_order):
        raise unreadable_reading(
            signal, commits.source_ref, "recorded commit order repeats a SHA"
        )
    if record.raised_at_sha not in commit_order:
        raise unreadable_reading(
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

    Three typed readings, in order: the WritableSurface address, its ordered
    holder history, and the configured distinct-holder limit. Address and
    history must name the same provenance source. Holder identities are
    opaque job identities supplied by the provenance reader; this function
    cannot infer them from account authors, timestamps or text.

    Repeated writes by one holder count once. Different runs holding the
    same address remain in that address's history, so they use this same
    signal arm. Original readings, including their order, survive replay.

    The cross-run case is a WIDENING of ``SURFACE_CONTENDED``, never a
    second member: it is one more firing/clean pair on this function.
    """
    signal = AlarmSignal.SURFACE_CONTENDED
    try:
        surface, history, max_holders = readings
    except ValueError as exc:
        raise unreadable_reading(
            signal, subject.scope_key, "incomplete readings"
        ) from exc
    address = read_alarm_value(surface, SurfaceEvidence, signal)
    if subject.kind is not AlarmSubjectKind.SURFACE or subject.surface != address:
        raise unreadable_reading(
            signal, surface.source_ref, "subject identifies another surface"
        )
    if surface.source_ref != history.source_ref:
        raise unreadable_reading(
            signal, history.source_ref, "holder history identifies another source"
        )
    if max_holders.source_ref != SURFACE_HOLDERS_BOUND:
        raise unreadable_reading(
            signal,
            max_holders.source_ref,
            "holder bound does not name its AppConfig field",
        )
    holders = read_alarm_value(history, ReferencesEvidence, signal)
    configured = read_alarm_value(max_holders, CountEvidence, signal)
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

    Six typed readings, in order: previously-open reference identities,
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
        raise unreadable_reading(
            signal, subject.scope_key, "incomplete readings"
        ) from exc
    if subject.kind is not AlarmSubjectKind.LANE:
        raise unreadable_reading(
            signal, subject.scope_key, "a barren tick requires a lane subject"
        )
    if (max_files.source_ref, max_commits.source_ref) != (
        BARREN_FILES_BOUND,
        BARREN_COMMITS_BOUND,
    ):
        raise unreadable_reading(
            signal,
            subject.scope_key,
            "growth bounds do not name their AppConfig fields",
        )
    previous_open = read_alarm_value(previous, ReferencesEvidence, signal)
    current_closed = read_alarm_value(current, ReferencesEvidence, signal)
    for reading, identities in ((previous, previous_open), (current, current_closed)):
        if len(set(identities)) != len(identities):
            raise unreadable_reading(
                signal,
                reading.source_ref,
                "a reference identity appears more than once",
            )
    files_changed = read_alarm_value(files, CountEvidence, signal)
    commits_ahead = read_alarm_value(commits, CountEvidence, signal)
    file_limit = read_alarm_value(max_files, CountEvidence, signal)
    commit_limit = read_alarm_value(max_commits, CountEvidence, signal)
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


GROOM_MARKER_SOURCE = phase_marker_source("groom")
TICKET_MARKER_SOURCE = phase_marker_source("ticket")
CRITERIA_MARKER_SOURCE = phase_marker_source("criteria")


def tally_unmoved(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """One signal over two observation windows, chosen by the subject's kind.

    A lane subject reads the same tally twice — what its subtree owed, what
    it owes now, and what it recorded between the two — while a scope
    subject reads one snapshot of an ORGANIZE marker barrier. Both are the
    same question asked of what the run's shape is addressed to, so both are
    arms of this member rather than a second signal, and a subject with no
    arm refuses instead of being answered from some other arm's readings.

    The scope arm is a WIDENING of ``TALLY_UNMOVED``, not a member of its
    own: it is one more firing/clean pair on this function, and a second
    signal for it would be a thirteenth member the vocabulary refuses.
    """
    if isinstance(subject, LaneSubject):
        return _lane_tally_unmoved(
            subject=subject,
            readings=readings,
            raised_at_sha=raised_at_sha,
            raised_by=raised_by,
        )
    if subject.kind is AlarmSubjectKind.SCOPE:
        return _scope_tally_unmoved(
            subject=subject,
            readings=readings,
            raised_at_sha=raised_at_sha,
            raised_by=raised_by,
        )
    raise unreadable_reading(
        AlarmSignal.TALLY_UNMOVED, subject.scope_key, "no tally arm for this subject"
    )


def _lane_tally_unmoved(
    *,
    subject: LaneSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Observe a lane's tally standing still across its own recorded commits.

    Four readings in order: the earlier tally of this lane, its current
    tally, the earlier reading's identities that have since closed, and the
    configured bound. The clock is the lane's own record — the shas the
    current reading carries that the earlier one did not — so a lane nobody
    fired records nothing and is quiet by arithmetic rather than by a rule.

    Work counts identities and never lengths: a reading that closed two
    criteria while three more were surfaced did work the difference of two
    counts would report as negative. A lane that closed something, or owes
    nothing at all, is quiet whatever it recorded.

    A lane's commit shas may repeat: a landing records the best commit again
    as its own row (KOD-681), so a returning sha is a recorded act. The clock
    counts distinct new shas, so a returning sha is never new work. Criterion
    identities may not repeat, and a reading that repeats one refuses.
    """
    signal = AlarmSignal.TALLY_UNMOVED
    try:
        anchor_reading, latest_reading, closed_reading, bound_reading = readings
    except ValueError as exc:
        raise unreadable_reading(
            signal, subject.lane_key, "incomplete lane tally readings"
        ) from exc
    for reading in (anchor_reading, latest_reading, closed_reading):
        if reading.source_ref != subject.lane_key:
            raise unreadable_reading(
                signal, reading.source_ref, "a tally reading names another lane"
            )
    if bound_reading.source_ref != COMMITS_WITHOUT_CLOSURE_BOUND:
        raise unreadable_reading(
            signal, bound_reading.source_ref, "the bound names another configured field"
        )
    anchor = read_alarm_value(anchor_reading, TallyEvidence, signal)
    latest = read_alarm_value(latest_reading, TallyEvidence, signal)
    closed = read_alarm_value(closed_reading, ReferencesEvidence, signal)
    configured = read_alarm_value(bound_reading, CountEvidence, signal)
    for reading, identities in (
        (anchor_reading, anchor.open),
        (latest_reading, latest.open),
        (closed_reading, closed),
    ):
        if len(set(identities)) != len(identities):
            raise unreadable_reading(
                signal, reading.source_ref, "an identity appears more than once"
            )
    if not set(closed) <= set(anchor.open):
        raise unreadable_reading(
            signal, closed_reading.source_ref, "a closed identity was never owed"
        )
    if set(closed) & set(latest.open):
        raise unreadable_reading(
            signal, closed_reading.source_ref, "a closed identity is still owed"
        )
    if not latest.open or closed:
        return None
    observed = len(set(latest.commits) - set(anchor.commits))
    if observed > configured:
        return RunAlarm(
            subject=subject,
            signal=signal,
            readings=readings,
            bound=AlarmBound(
                config_field=COMMITS_WITHOUT_CLOSURE_BOUND,
                configured_value=configured,
                observed_value=observed,
            ),
            raised_at_sha=raised_at_sha,
            raised_by=raised_by,
        )
    return None


def _scope_tally_unmoved(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Observe a configured adjacent ORGANIZE marker barrier over its roster.

    Readings retain two qualified configuration keys, the native scope
    address, its ORGANIZE work-target keys, then per-member semantic label
    sets. An absent member reading or a absent label set counts as open;
    malformed or foreign readings refuse. The execution-entry event reader
    is not implemented by substituting other tracker facts.
    """
    signal = AlarmSignal.TALLY_UNMOVED
    try:
        current, following, scope_reading, roster_reading, *members = readings
    except ValueError as exc:
        raise unreadable_reading(
            signal, subject.scope_key, "incomplete scope tally readings"
        ) from exc
    if (current.source_ref, following.source_ref) not in {
        (GROOM_MARKER_SOURCE, TICKET_MARKER_SOURCE),
        (TICKET_MARKER_SOURCE, CRITERIA_MARKER_SOURCE),
    }:
        raise unreadable_reading(
            signal, subject.scope_key, "wrong phase marker sources"
        )
    try:
        current_namespace, current_key = split_label_key(_identity(current, signal))
        next_namespace, next_key = split_label_key(_identity(following, signal))
    except ValueError as exc:
        raise unreadable_reading(
            signal, current.source_ref, "invalid phase marker key"
        ) from exc
    if (
        current_namespace is not OrganizeLabelNamespace.ISSUE
        or next_namespace is not OrganizeLabelNamespace.ISSUE
        or current_key == next_key
    ):
        raise unreadable_reading(
            signal, current.source_ref, "distinct issue phase markers required"
        )
    scope = read_alarm_value(scope_reading, ScopeEvidence, signal)
    roster = read_alarm_value(roster_reading, ReferencesEvidence, signal)
    if (
        scope.key != subject.scope_key
        or scope_reading.source_ref != scope.key
        or roster_reading.source_ref != scope.key
    ):
        raise unreadable_reading(
            signal, scope_reading.source_ref, "scope identity disagrees"
        )
    if len(set(roster)) != len(roster):
        raise unreadable_reading(
            signal, roster_reading.source_ref, "roster repeats a member"
        )
    labels: dict[str, tuple[str, ...] | None] = {}
    for member in members:
        if member.source_ref not in roster or member.source_ref in labels:
            raise unreadable_reading(
                signal, member.source_ref, "foreign or repeated member reading"
            )
        labels[member.source_ref] = read_alarm_value(member, LabelsEvidence, signal)
    carrying = sum(current_key in (labels.get(key) or ()) for key in roster)
    entered = any(next_key in (labels.get(key) or ()) for key in roster)
    if carrying == len(roster) or not entered:
        return None
    return RunAlarm(
        subject=subject,
        signal=signal,
        readings=readings,
        bound=None,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )
