"""Identity arithmetic for mandate growth and structural lane regressions."""

from collections.abc import Sequence

from kodezart.domain.run_shape import _unreadable, read_alarm_value
from kodezart.types.domain.agent import RulingAuthor, RulingId
from kodezart.types.domain.mandate_graph import LaneGraphSnapshot, LaneRulingSnapshot
from kodezart.types.domain.run_alarm import (
    AlarmBound,
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
from kodezart.types.domain.scope import ScopeKind
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind

RULINGS_BOUND = "run_alarm_max_rulings_without_closure"


def _ruling_authors(
    snapshot: LaneRulingSnapshot, source_ref: str
) -> dict[RulingId, RulingAuthor]:
    if len(set(snapshot.issue_keys)) != len(snapshot.issue_keys):
        raise _unreadable(
            AlarmSignal.RULINGS_OUTPACE_CLOSURES,
            source_ref,
            "lane issue identities repeat",
        )
    authors: dict[RulingId, RulingAuthor] = {}
    issues: dict[RulingId, str] = {}
    for ruling in snapshot.rulings:
        if ruling.issue_key not in snapshot.issue_keys:
            raise _unreadable(
                AlarmSignal.RULINGS_OUTPACE_CLOSURES,
                source_ref,
                "ruling belongs to another lane's issue",
            )
        old = authors.get(ruling.ruling_id)
        if old is not None and (
            old is not ruling.authored_by
            or issues[ruling.ruling_id] != ruling.issue_key
        ):
            raise _unreadable(
                AlarmSignal.RULINGS_OUTPACE_CLOSURES,
                source_ref,
                "one ruling identity has conflicting authorship",
            )
        authors[ruling.ruling_id] = ruling.authored_by
        issues[ruling.ruling_id] = ruling.issue_key
    return authors


def rulings_outpace_closures(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Count newly recorded machine ruling identities inside one closure window.

    Five typed readings: the lane's ruling snapshot at its last closure,
    current ruling snapshot, previously-open obligation references, current
    closed references, and the configured bound. The four observations name
    the same lane source. A closure is an identity intersection, never an
    absent reference. The caller records the window boundary and advances
    it after a closure; this function does not manufacture history.
    """
    signal = AlarmSignal.RULINGS_OUTPACE_CLOSURES
    try:
        baseline, current, previous_open, current_closed, bound = readings
    except ValueError as exc:
        raise _unreadable(signal, subject.scope_key, "incomplete readings") from exc
    before = read_alarm_value(baseline, RulingsEvidence, signal)
    after = read_alarm_value(current, RulingsEvidence, signal)
    open_refs = read_alarm_value(previous_open, ReferencesEvidence, signal)
    closed_refs = read_alarm_value(current_closed, ReferencesEvidence, signal)
    was_open = set(open_refs)
    now_closed = set(closed_refs)
    limit = read_alarm_value(bound, CountEvidence, signal)
    if (
        subject.kind is not AlarmSubjectKind.LANE
        or subject.lane_key != before.lane_key
        or before.lane_key != after.lane_key
    ):
        raise _unreadable(signal, current.source_ref, "readings identify other lanes")
    if any(item.source_ref != baseline.source_ref for item in readings[:4]):
        raise _unreadable(signal, current.source_ref, "window sources differ")
    if bound.source_ref != RULINGS_BOUND:
        raise _unreadable(signal, bound.source_ref, "incorrect configuration field")
    old_authors = _ruling_authors(before, baseline.source_ref)
    authors = _ruling_authors(after, current.source_ref)
    old_owners = {row.ruling_id: row.issue_key for row in before.rulings}
    if any(
        row.ruling_id in old_owners and old_owners[row.ruling_id] != row.issue_key
        for row in after.rulings
    ):
        raise _unreadable(signal, current.source_ref, "a ruling changed owning issue")
    count = sum(
        author is RulingAuthor.MACHINE and ruling_id not in old_authors
        for ruling_id, author in authors.items()
    )
    if was_open & now_closed or count <= limit:
        return None
    return RunAlarm(
        subject=subject,
        signal=signal,
        readings=readings,
        bound=AlarmBound(
            config_field=RULINGS_BOUND,
            configured_value=limit,
            observed_value=count,
        ),
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )


def _index(issues: Sequence[TrackerIssue], source_ref: str) -> dict[str, TrackerIssue]:
    result: dict[str, TrackerIssue] = {}
    for issue in issues:
        if issue.issue_key in result:
            raise _unreadable(
                AlarmSignal.STRUCTURAL_WRITE_UNCROSSES_MILESTONE,
                source_ref,
                "membership read repeats an issue identity",
            )
        result[issue.issue_key] = issue
    return result


def lane_graph_members(
    snapshot: LaneGraphSnapshot, *, source_ref: str
) -> dict[str, TrackerIssue]:
    """Validate and combine the charter's two complete membership reads.

    Conflicting versions refuse observation: two sequential port reads do
    not imply an atomic snapshot. Parent paths must reach the named fire.
    """
    signal = AlarmSignal.STRUCTURAL_WRITE_UNCROSSES_MILESTONE
    subtree = _index(snapshot.subtree, source_ref)
    members = _index(snapshot.milestone_members, source_ref)
    if snapshot.milestone.kind is not ScopeKind.MILESTONE:
        raise _unreadable(signal, source_ref, "container is not a milestone")
    fire = subtree.get(snapshot.fire_key)
    if fire is None or fire.milestone_key != snapshot.milestone.key:
        raise _unreadable(signal, source_ref, "fire is absent or belongs elsewhere")
    if snapshot.fire_key not in members:
        raise _unreadable(signal, source_ref, "milestone membership omits its fire")
    for issue in members.values():
        if issue.milestone_key != snapshot.milestone.key:
            raise _unreadable(signal, source_ref, "foreign milestone member")
    for key, issue in subtree.items():
        old = members.get(key)
        if old is not None and old != issue:
            raise _unreadable(signal, source_ref, "membership reads disagree")
        cursor = key
        visited: set[str] = set()
        while cursor != snapshot.fire_key:
            if cursor in visited or cursor not in subtree:
                raise _unreadable(signal, source_ref, "unrooted or cyclic subtree")
            visited.add(cursor)
            parent = subtree[cursor].parent_key
            if parent is None:
                raise _unreadable(signal, source_ref, "unrooted subtree")
            cursor = parent
        members[key] = issue
    refs = [entry.issue_key for entry in snapshot.supersessions]
    if len(set(refs)) != len(refs) or any(key not in members for key in refs):
        raise _unreadable(signal, source_ref, "ambiguous supersession references")
    return members


def structural_write_uncrosses_milestone(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Detect an unresolved member added to a previously crossed lane's graph.

    Two typed LaneGraphSnapshot readings retain both full membership sets.
    The earlier graph must support the crossing: its fire is completed,
    and every member is completed or canceled with an explicit supersession.
    The fire remains completed. A newly present unresolved member is a
    structural regression; an existing member
    changing only state cannot satisfy this predicate.
    """
    signal = AlarmSignal.STRUCTURAL_WRITE_UNCROSSES_MILESTONE
    try:
        previous, current = readings
    except ValueError as exc:
        raise _unreadable(signal, subject.scope_key, "incomplete readings") from exc
    before = read_alarm_value(previous, GraphEvidence, signal)
    after = read_alarm_value(current, GraphEvidence, signal)
    if (
        subject.kind is not AlarmSubjectKind.LANE
        or subject.lane_key != before.lane_key
        or before.lane_key != after.lane_key
        or before.fire_key != after.fire_key
        or before.milestone != after.milestone
        or previous.source_ref != current.source_ref
    ):
        raise _unreadable(signal, current.source_ref, "graph identities differ")
    old = lane_graph_members(before, source_ref=previous.source_ref)
    new = lane_graph_members(after, source_ref=current.source_ref)
    completed = WorkflowStateKind.COMPLETED
    if old[before.fire_key].state_kind is not completed:
        return None
    if new[after.fire_key].state_kind is not completed:
        return None
    superseded = {entry.issue_key for entry in before.supersessions}
    if any(
        issue.state_kind is not completed
        and not (
            issue.state_kind is WorkflowStateKind.CANCELED
            and issue.issue_key in superseded
        )
        for issue in old.values()
    ):
        return None
    now_superseded = {entry.issue_key for entry in after.supersessions}
    if not any(
        key not in old
        and issue.state_kind is not completed
        and not (
            issue.state_kind is WorkflowStateKind.CANCELED and key in now_superseded
        )
        for key, issue in new.items()
    ):
        return None
    return RunAlarm(
        subject=subject,
        signal=signal,
        readings=readings,
        bound=None,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )
