"""One board of lanes, their criterion families and the records their loops left.

Shared by the observer's own tests and the scheduled tick's, so both describe
one board rather than two that happen to agree.
"""

from dataclasses import dataclass, field
from datetime import datetime

import pytest

from kodezart.config.app import AppConfig
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.lane_alarms import stored_alarm
from kodezart.domain.lane_record import RUN_STATE_PURPOSE, render_lane_record
from kodezart.domain.lapse import lapse_escalation_key
from kodezart.domain.run_alarm_record import MARKER_PURPOSE, run_alarm_marker
from kodezart.domain.run_event_stream import RUN_EVENT_PURPOSE
from kodezart.services.alarm_supervisor import AlarmSupervisor
from kodezart.services.escalation_ageing_supervisor import EscalationAgeingSupervisor
from kodezart.services.escalation_records import EscalationRecordReader
from kodezart.services.lane_escalation import LaneEscalationWriter
from kodezart.services.lane_lapse_escalation import lapse_question
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.run_alarm_recorder import RunAlarmRecorder
from kodezart.services.supervisor_pass import supervisor_holder
from kodezart.types.domain.branch import BranchAssociation, BranchRole
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_lifecycle import (
    CriterionCrossOff,
    CrossOffState,
    RederivationClass,
)
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig, ScopeLabel
from kodezart.types.domain.run_alarm import (
    AlarmSignal,
    CriterionSubject,
    EscalationSubject,
    LaneSubject,
)
from kodezart.types.domain.run_state import LaneCommit, LaneEscalation, LaneRunState
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import FakeTrackerPort, PassThroughGate, make_tracker_issue

SCOPE = "scoped-project"
HEAD = "a" * 40
OPERATION_NAME = "fixture"
#: The identity the composed pass leases and records under, taken from the one
#: function the composition takes it from: a board whose ticks were built
#: through that composition leases under its operation's name, so a check keyed
#: on a literal here would go vacuous for exactly those boards.
HOLDER = supervisor_holder(operation_name=OPERATION_NAME)
BOUND = 1
LEASE_SECONDS = 60.0
PREFIXES = {
    "run_state": "fixture-record",
    "run_event": "fixture-runevent",
    "run_alarm": "fixture-runalarm",
    "escalation": "fixture-escalation",
}
#: The job a test's own lapse question is raised under: the raising fire's
#: identity, never the supervisor's.
RAISING_JOB = "raising-fire"
#: The criteria-stage label the board's lanes carry, as the walker's own
#: fixtures spell it, so a scoped read of this board selects them.
STAGED = "criteria-staged"


@dataclass
class Board:
    """One board built through :func:`board`, and what may be written to it.

    *allowed* holds the ``(lane, marker)`` pairs a test names for a writer
    other than the supervisor, or for a supervisor address below a lane's
    direct criteria, so a legitimate write outside the lanes' own set is
    admitted by being declared rather than by widening the set every board is
    checked against.

    *states* and *state_changes* are the board's states as the tick found
    them. A state move that goes around the port's writers leaves no write
    log, so what it moved is only visible as a difference from a baseline.

    *scope_keys* names the scope each lane is a member of, because an alarm
    address is keyed by that scope: a board carrying lanes under two scopes
    has two families of addresses, and reading both against one scope key
    would call the second family a write nobody declared.

    *holder* is the identity whose leases the release check reads. A board
    whose ticks are composed rather than built here leases under its own
    operation's pass identity, so the board states which one it expects.

    *questions* holds the ``(lane, occurrence)`` pairs of the lapse questions
    a test raised on the board, because each one's ageing record is an
    address the tick may write under and no other question's is.

    *classifications* holds the classification writes a test's own raise
    made, in order, because the tick itself writes none.
    """

    port: FakeTrackerPort
    lanes: tuple[str, ...]
    holder: str = HOLDER
    scope_keys: dict[str, str] = field(default_factory=dict)
    allowed: set[tuple[str, str]] = field(default_factory=set)
    questions: set[tuple[str, str]] = field(default_factory=set)
    classifications: list[tuple[str, str]] = field(default_factory=list)
    states: dict[str, tuple[str, WorkflowStateKind]] = field(default_factory=dict)
    state_changes: dict[str, datetime] = field(default_factory=dict)


#: Every board built through this module, with the lanes it was built for, so a
#: surface assertion can be applied to every fixture rather than to the ones
#: that remembered to ask for it.
BOARDS: list[Board] = []


def checks(lane):
    """The two criterion sub-issue keys every lane of this board owes."""
    return (f"{lane}/check", f"{lane}/second")


def subject(lane, *, scope_key=SCOPE):
    return LaneSubject(scope_key=scope_key, lane_key=lane)


def criterion(key, *, lane, closed=False):
    return make_tracker_issue(
        key,
        parent_key=lane,
        issue_labels=frozenset({"criterion"}),
        state_name="Done" if closed else "Todo",
        state_kind=WorkflowStateKind.COMPLETED
        if closed
        else WorkflowStateKind.UNSTARTED,
    )


def subtree(lane, *, closed=()):
    """The lane's whole criterion roster, with *closed* marked finished."""
    return tuple(
        criterion(key, lane=lane, closed=key in closed) for key in checks(lane)
    )


def still_open(lane, *, closed=()):
    return tuple(
        row for row in subtree(lane, closed=closed) if row.issue_key not in closed
    )


def lane_state(lane, *, commits):
    """The record the committing loop leaves on *lane*'s own issue."""
    loop = f"kodezart/{lane}-loop"
    deliverable = f"kodezart/{lane}"
    return LaneRunState(
        lane_key=lane,
        branch=loop,
        branch_url=f"https://forge.invalid/{loop}",
        head_sha=HEAD,
        pushed_head_sha=HEAD,
        commits_ahead=len(commits),
        files_changed=len(commits),
        commits=[
            LaneCommit(sha=sha, subject="feat: one", issue_id=lane) for sha in commits
        ],
        body_digest=None,
        associations=[
            BranchAssociation(
                branch=deliverable,
                role=BranchRole.DELIVERABLE,
                derived_from="trunk",
                run_id="first-job",
            ),
            BranchAssociation(
                branch=loop,
                role=BranchRole.LOOP,
                derived_from=deliverable,
                run_id="first-job",
            ),
        ],
    )


async def board(
    *,
    lanes,
    commits=("sha-one", "sha-two"),
    prefixes=PREFIXES,
    scope=None,
    scopes=None,
    holder=HOLDER,
    extra=(),
):
    """Each lane's issue, its criterion family, and the record its loop left.

    With *scope* the same board is also addressable as that scope: the lanes
    are its members and each is approved, so the walker's own ready read
    answers for it and a composed tick can read this board rather than a
    second one written to agree with it.

    *scopes* says the same for more than one scope at once, as a mapping of
    scope reference to the lanes that are its members, so one board can carry a
    lane under each of several declared scopes. It and *scope* are two
    spellings of the one membership map, so only one of them may be given.

    *extra* holds further issues beneath the lanes, such as a deliverable
    child owing criteria of its own, so a lane's subtree can reach deeper than
    its direct criteria.
    """
    if scope is not None and scopes is not None:
        raise ValueError("a board states its scope memberships once")
    memberships = None
    if scope is not None:
        memberships = {scope: tuple(lanes)}
    elif scopes is not None:
        memberships = {ref: tuple(members) for ref, members in scopes.items()}
    port = FakeTrackerPort(
        issues=[
            row
            for lane in lanes
            for row in (
                make_tracker_issue(
                    lane,
                    issue_labels=frozenset()
                    if memberships is None
                    else frozenset({STAGED}),
                ),
                *subtree(lane),
            )
        ]
        + list(extra),
        marker_prefixes=prefixes,
        scope_memberships=memberships,
        criteria_stage_label_key=None if memberships is None else STAGED,
        scope_label_members=None
        if memberships is None
        else {
            ScopeRef(kind=ScopeKind.ISSUE, key=lane): frozenset({ScopeLabel.APPROVED})
            for lane in lanes
        },
    )
    for lane in lanes:
        await port.post_comment(
            issue_key=lane,
            body=render_lane_record(
                record=lane_state(lane, commits=commits), marker_prefixes=PREFIXES
            ),
        )
    port.comment_writes.clear()
    entry = Board(
        port=port,
        lanes=tuple(lanes),
        holder=holder,
        scope_keys=dict.fromkeys(lanes, SCOPE)
        if memberships is None
        else {
            member: ref.key
            for ref, members in memberships.items()
            for member in members
        },
    )
    _rebase_states(entry)
    BOARDS.append(entry)
    return port


def _rebase_states(entry):
    """Take the board's states as they now stand as the baseline to compare to."""
    entry.states = {
        key: (row.state_name, row.state_kind) for key, row in entry.port.issues.items()
    }
    entry.state_changes = dict(entry.port.issue_state_changes)


def close_criterion(port, key):
    """Finish one criterion issue on the board, as the walk's own closure does.

    Passing a closed roster to ``observe`` says what the tick reads; it does
    not move the issue, so a tick that reset the criterion it just saw close
    would be resetting something still unstarted and the fake would return it
    untouched. Moving it here makes such a reset a real move, which the state
    baseline sees. The baseline is retaken, because this move is the test's
    and not the tick's.
    """
    port.issues[key] = port.issues[key].model_copy(
        update={"state_name": "Done", "state_kind": WorkflowStateKind.COMPLETED}
    )
    _rebase_states(next(row for row in BOARDS if row.port is port))


def allow_foreign_write(port, *, lane, marker):
    """Name one write on *port* outside the lanes' own declared set.

    Either a holder other than the supervisor makes it, or it is the
    supervisor's own at an address the set does not enumerate: a criterion
    under a lane's deliverable child, or one a lane observes under a nested
    member lane.

    The declared-set check reads the tick's own writes, so a write a test
    makes on purpose has to be named before it is made. Naming it keeps the
    set every other board is checked against as narrow as the design's.
    """
    entry = next(row for row in BOARDS if row.port is port)
    entry.allowed.add((lane, marker))


def question_subject(lane, occurrence, *, scope_key=SCOPE):
    """The ageing address of the question *occurrence* on *lane*."""
    return EscalationSubject(
        scope_key=scope_key, member_id=occurrence, lane_key=lane, issue_id=lane
    )


async def raise_lapse_question(port, lane, criterion_key, *, graded_sha):
    """Put the lapse question about *criterion_key* on *lane*, as its writer does.

    The question goes through the production writer, so the board holds what a
    lapsed observation leaves: the question under its own marker and the lane
    classified for decision. The writes are the test's and not the tick's, so
    the comment is named as a foreign write, the classification is recorded as
    the board's own, and the question's ageing address joins the tick's
    declared set.
    """
    occurrence = lapse_escalation_key(criterion_key)
    question = lapse_question(
        lane_key=lane,
        cross_off=CriterionCrossOff(
            criterion=criterion_key,
            state=CrossOffState.passed,
            rederivation_class=RederivationClass.observed,
            exercised_paths=("src/",),
            evidence=CriterionEvidence(
                graded_sha=graded_sha, test=f"observed {criterion_key}"
            ),
        ),
    )
    marker = compose_comment_marker(
        prefixes=port.marker_prefixes,
        purpose="escalation",
        lane=lane,
        occurrence_key=occurrence,
    )
    allow_foreign_write(port, lane=lane, marker=marker)
    entry = next(row for row in BOARDS if row.port is port)
    entry.questions.add((lane, occurrence))
    writer = LaneEscalationWriter(
        tracker=port,
        gate=PassThroughGate(),
        operation=operation(port.marker_prefixes).model_copy(
            update={"issue_labels": {"decision": "decision"}}
        ),
        surface_lease_seconds=LEASE_SECONDS,
    )
    mark = len(port.classification_writes)
    comment = await writer.raise_escalation(
        lane_key=lane,
        job_id=RAISING_JOB,
        escalation=question,
        visibility=RepoVisibility.PUBLIC,
    )
    entry.classifications.extend(port.classification_writes[mark:])
    return comment


async def answer_question(port, lane, question):
    """A decision record replying to *question*, under the decision marker."""
    payload = question.body.partition("\n")[2]
    occurrence = LaneEscalation.model_validate_json(payload).escalation_key
    marker = compose_comment_marker(
        prefixes=port.marker_prefixes,
        purpose="decision",
        lane=lane,
        occurrence_key=occurrence,
    )
    allow_foreign_write(port, lane=lane, marker=marker)
    decision = await port.post_comment(issue_key=lane, body=f"{marker}\nAnswered.")
    port.comments[port.comments.index(decision)] = decision.model_copy(
        update={"reply_to": question.comment_key}
    )


def rewrite_record(port, lane, *, commits):
    """The record is one surface rewritten in place, as its own writer leaves it."""
    body = render_lane_record(
        record=lane_state(lane, commits=commits), marker_prefixes=PREFIXES
    )
    marker = compose_comment_marker(
        prefixes=PREFIXES, purpose=RUN_STATE_PURPOSE, lane=lane
    )
    index = next(
        position
        for position, row in enumerate(port.comments)
        if row.issue_key == lane and row.body.startswith(marker)
    )
    port.comments[index] = port.comments[index].model_copy(update={"body": body})


def operation(prefixes=PREFIXES):
    return OperationConfig(
        operation_name=OPERATION_NAME, workspace="fixture", marker_prefixes=prefixes
    )


#: The signal every address in this module addresses unless one says otherwise.
SIGNAL = AlarmSignal.TALLY_UNMOVED
#: The signals observed at the lane's own addresses on its issue.
LANE_SIGNALS = (AlarmSignal.TALLY_UNMOVED, AlarmSignal.COMPOSITION_SUBSTITUTED)
#: The signals observed at a criterion's own address on its lane's issue.
CRITERION_SIGNALS = (AlarmSignal.TALLY_REGRESSED, AlarmSignal.LAPSE_UNDISCHARGED)


def supervisor(port, *, bound=BOUND, holder=HOLDER):
    return AlarmSupervisor(
        tracker=port,
        records=LaneRecordReader(tracker=port, operation=operation()),
        marker_prefixes=port.marker_prefixes,
        max_commits_without_closure=bound,
        holder=holder,
        lease_seconds=LEASE_SECONDS,
    )


def recorder(port, *, holder=HOLDER):
    """The ageing arm's leased writer over *port*."""
    return RunAlarmRecorder(
        tracker=port,
        marker_prefixes=port.marker_prefixes,
        holder=holder,
        lease_seconds=LEASE_SECONDS,
    )


def ageing(port, *, holder=HOLDER, config=None):
    """The ageing observer over the same board, as the composition builds it."""
    return EscalationAgeingSupervisor(
        sources=port,
        escalations=EscalationRecordReader(tracker=port, operation=operation()),
        records=LaneRecordReader(tracker=port, operation=operation()),
        alarms=recorder(port, holder=holder),
        operation=operation(),
        config=AppConfig(_env_file=None) if config is None else config,
    )


def alarm_marker(port, lane, *, scope_key=SCOPE, signal=SIGNAL):
    return run_alarm_marker(
        subject=subject(lane, scope_key=scope_key),
        signal=signal,
        marker_prefixes=port.marker_prefixes,
    )


def records_on(port, lane):
    return [
        row
        for row in port.comments
        if row.issue_key == lane and row.body.startswith(alarm_marker(port, lane))
    ]


async def events_on(port, lane):
    return list(await port.lane_run_events(issue_key=lane, lane_key=lane))


async def stored_on(port, lane, *, signal=SIGNAL, scope_key=SCOPE):
    """The record at one of *lane*'s addresses, out of the carrier's listing.

    The port answers with every record the carrier holds, so a test asking
    about one address picks it out of that listing the same way the tick does.
    """
    return stored_alarm(
        await port.read_run_alarms(issue_key=lane),
        subject=subject(lane, scope_key=scope_key),
        signal=signal,
    )


def snapshot(port):
    """What the board holds and what was written to it, as one comparable value."""
    return (list(port.comments), list(port.comment_writes), list(port.lease_writes))


def declared_pairs(entry):
    """The ``(lane, marker)`` pairs this board's ticks may write under.

    Per lane: the lane's run-event stream, and the alarm markers of the lane's
    own signals when the board configures an alarm prefix. Each is keyed by the
    scope the lane is a member of, as the board recorded it, so a board
    holding lanes under two scopes declares each lane's own address and neither
    lane's address covers the other. The lane record's own marker is not among
    them — the supervisor holds no surface there — so a write under it is
    outside the set unless a test named it.

    Each of the lane's own criteria has an address per criterion signal on the
    lane's issue, keyed to the lane that owns it, and those are declared one by
    one: a criterion record written under another lane, another parent or
    another signal matches none of them.
    """
    pairs = [
        (
            lane,
            compose_comment_marker(
                prefixes=entry.port.marker_prefixes,
                purpose=RUN_EVENT_PURPOSE,
                lane=lane,
            ),
        )
        for lane in entry.lanes
    ]
    if MARKER_PURPOSE in entry.port.marker_prefixes:
        pairs.extend(
            (
                lane,
                alarm_marker(
                    entry.port, lane, scope_key=entry.scope_keys[lane], signal=signal
                ),
            )
            for lane in entry.lanes
            for signal in LANE_SIGNALS
        )
        pairs.extend(
            (
                lane,
                run_alarm_marker(
                    subject=CriterionSubject(
                        scope_key=entry.scope_keys[lane],
                        issue_id=lane,
                        member_id=key,
                        lane_key=lane,
                    ),
                    signal=signal,
                    marker_prefixes=entry.port.marker_prefixes,
                ),
            )
            for lane in entry.lanes
            for key in checks(lane)
            for signal in CRITERION_SIGNALS
        )
        pairs.extend(
            (
                lane,
                run_alarm_marker(
                    subject=question_subject(
                        lane, occurrence, scope_key=entry.scope_keys[lane]
                    ),
                    signal=AlarmSignal.ESCALATION_AGEING,
                    marker_prefixes=entry.port.marker_prefixes,
                ),
            )
            for lane, occurrence in sorted(entry.questions)
        )
    return pairs


def assert_every_write_is_inside_the_declared_set():
    """Every board built this test: nothing written outside the lane's own set.

    The writes read are the tick's own — ``board()`` clears the write log once
    it has posted the lane records, and the fake appends to it only from its
    post and upsert paths — so the set is the design's rather than one widened
    to admit the fixture's own setup. Each write is checked as a whole address,
    issue key and marker together, because a body under a declared marker
    landing on another issue is a write outside the set too.

    The set is derived from the purposes the board actually configures, so a
    board declaring no alarm prefix is checked against what it does declare
    rather than skipped.

    The board's states are compared to the baseline as well, on every tick
    rather than on the one test that snapshots them: a move made around the
    port's own writers appears in no write log, so only the difference from
    the baseline reports it.
    """
    for entry in BOARDS:
        port = entry.port
        allowed = [*declared_pairs(entry), *sorted(entry.allowed)]
        rows = {row.comment_key: row for row in port.comments}
        for comment_key, body in port.comment_writes:
            row = rows[comment_key]
            written = (row.issue_key, body.split("\n", 1)[0])
            assert any(
                written[0] == lane and written[1].startswith(marker)
                for lane, marker in allowed
            ), (written, allowed)
        assert port.issue_writes == []
        assert port.workflow_writes == []
        assert port.restored_states == []
        assert port.queue_writes == []
        assert port.classification_writes == entry.classifications
        assert port.claim_writes == []
        assert [
            lease for lease in port.leases.values() if lease.holder == entry.holder
        ] == []
        assert {
            key: (row.state_name, row.state_kind) for key, row in port.issues.items()
        } == entry.states
        assert port.issue_state_changes == entry.state_changes


def declared_set_fixture():
    """The autouse fixture every module that builds a board applies.

    Coverage is every board built through :func:`board`, in whichever module
    built it, so a tick that grew a write somewhere else cannot pass by being
    exercised in a module that only asked about something adjacent. A board
    built by hand rather than through the helper is outside it.
    """

    @pytest.fixture(autouse=True)
    def every_write_of_a_tick_is_inside_the_declared_set():
        BOARDS.clear()
        yield
        assert_every_write_is_inside_the_declared_set()

    return every_write_of_a_tick_is_inside_the_declared_set
