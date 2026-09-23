"""The dispatch decision does not move when a size moves (KOD-731).

The static guard beside this one (``test_dispatch_rank_inputs.py``) pins the
rank's code as it stands: the ordering key texts and the bodies of the
definitions on the dispatch path. This one pins the decision itself, which
no respelling of that code can reach around, in both flows that decide what
fires.

The dispatch pass. The dispatcher is built the way the composition root
builds it — through ``build_dispatch_runtime``, the function ``main.py``
calls, over the fake tracker and a scripted queue — and its passes are run
until the board is drained. What is recorded is the sequence of issues
claimed and the sequence of fires enqueued, which is what the hand-off to
``launch`` produces.

The scope flow. In a scope deployment the lane that fires is chosen by
``ScopeWorkflowEngine``, which ``build_workflow_engine`` (the function
``main.py`` calls) builds through ``build_scope_runtime``. It is built that
way here, over a scope board whose lanes are the board's issues with their
priorities and ages, on an origin with a forge behind it, and one walk is
run to its end with fires that return. Every lane owes two criteria. The
scripted agent boundary passes exactly the lane's own criterion when a
grading asks about it and nothing otherwise, so a lane's first fire closes
one of what it owed and the tick after it reads that as progress and offers
the lane again; its second fire, asked only about what is left, closes
nothing, and that tick's reading rests the lane by the plateau rule and
puts its issue back. Every lane is therefore fired twice and rested once,
whatever its gap holds, and the walk ends when none is left. Two more
lanes owe nothing: every criterion under them is done and nothing records
a branch or a pull request for them. The origin delivers, so they take the
delivery-only turn first, in the order the scope lists them; each turn's
entry finds nothing to deliver and the lane rests. What is recorded is the
whole sequence: the lanes fired in order, re-fires included, the lanes
rested in order, closed lanes included, every contained failure, and the
number of ticks the walk took. The scope flow's own eligibility inputs are
registered apart (``SCOPE_ELIGIBILITY_REASONS``), and its varied fields are
derived from the row model the same way.

The row is partitioned three ways, off the row model itself. ``RANK_INPUTS``
are the two fields a rank is made from. ``ELIGIBILITY_INPUTS`` are the fields
the eligibility clauses read at head, each with its reason; no size, body or
count field is among them. Every other field of ``TrackerIssue`` is varied,
so a field added to the row is varied as soon as it exists, and a field whose
range this module does not know fails loudly. Beyond the row, what the port
answers about an issue's subtree is varied too: its sub-issues, its
criterion sub-issues and its comments. In the scope flow these are the
lane's gap: many open criteria under it, and many deliverable children, each
owing a criterion of its own. So is the work already done: many criteria
under a heavy issue in a completed state, beside its open ones, so its gap
and its eligibility stay what they were while the fraction of its work
still open moves. So are the edge kinds no eligibility clause
reads: the blocker clause reads blocked-by edges alone, so the issue's other
edges (blocks, related, duplicate) are a count like any other.
Each varied input is set to its extremes — nothing, and a great deal — one
input at a time, then all of them together, over several heavy sets: the
lanes at each parity (the closed lanes take the parity of their place after
the board, so each is heavy in one and light in the other), the older
member of each equal-priority pair and then the younger one, and each
issue alone. The board's equal-priority pair sits at opposite parities, and
a plain test checks that every such pair is split both ways, so a size that
reaches the rank only below the priority — as a tie-break, or folded into
the age — moves the decision here. The decision must equal the base
decision every time.

Reach: in both flows, the decision as the composition root composes it is
invariant under every non-rank, non-eligibility input the board holds, at
the extremes above. So a size read off the board anywhere between the read
and the fire — a rebinding at boot, a subclass built at the root, a
validator on the row, an eligibility clause, a table consulted after the
selection, a hook in the rank value, a tie-break among equal priorities, an
age shifted by a size, a count over the edges no clause reads, a lane
skipped or chosen by its gap or by the fraction of its work still open (a
count over an eligibility input, the criteria's state), a fired lane rested
or re-offered by a size
rather than by what its fire closed, a closed lane passed over or reordered
by a size on the delivery-only turn — fails here whatever it is spelled, as
soon as it moves the decision for a board holding those extremes.

Limit: a size taken from outside the board (for example, from the
repository) is not varied here, so it would have to be read in a path body
the static guard pins to be seen; the scope flow's lane selector
(``ScopeWorkflowEngine._select``), its re-fire reading (``_settle``) and the
dispatch pass's hand-off after the selection are not among those bodies. A
size rule that moves nothing at these extremes — a threshold beyond them —
is not seen; in the scope flow the subtree's great deal is ``SCOPE_MANY``
per part, because the ready read re-reads every lane's subtree on every
tick. In the scope flow a lane whose body and title are both empty has no
subject and is refused before its graph launches, so there the everything
case leaves the title alone and the title is varied on its own. What a
fire does inside its sessions is scripted, so which criteria a fire closes
is not decided here: the rule is the same for every lane and every size,
and what is held is what the walk decides from it. The pass runs ungated
(``dispatch_pass_gate_signals`` empty, a legal deployment), so the gate's
own reading of the board is outside this test; the gate decides whether a
pass runs, not which issue it claims.
"""

import asyncio
import re
import types
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Union, get_args, get_origin

import pytest
import structlog.testing

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.composition.passes import build_dispatch_runtime
from kodezart.composition.tracker import DialledTracker
from kodezart.config.app import AppConfig
from kodezart.core.logging import get_logger
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.operation import QueueState
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope_runtime import ScopeWalkEvent
from kodezart.types.domain.tracker import (
    IssuePriority,
    IssueQuery,
    IssueRelation,
    IssueRelationKind,
    TrackerComment,
    TrackerIssue,
    WorkflowStateKind,
)
from tests.adapters.test_github_api import _make_client
from tests.fakes import (
    FIXTURE_EPOCH,
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
    FakeDeliveryProbe,
    FakeGitService,
    FakeJobQueue,
    FakeRepoCache,
    FakeScopeStatusWriter,
    FakeTrackerPort,
    FakeWorkspaceProvider,
    ManagedFakeLinearMcpServer,
    PassThroughGate,
    make_prompt_provider,
    make_tracker_issue,
)
from tests.integration.test_scope_runtime import (
    FORGE_ORIGIN,
    SCOPE,
    ObservedNativeExecutor,
    WalkRepos,
    drive,
    resumable,
)
from tests.integration.test_scope_runtime import board as scope_board
from tests.lane_fixture import ScopeForgeWire, criteria_echo
from tests.services.test_dispatch_pass import APPROVER, operation_config

#: The row fields a rank is made from: ``rank_key`` reads these two.
RANK_INPUTS = frozenset({"priority", "created_at"})

#: Exact. The row fields the eligibility clauses read at head, one reason
#: each. No size, body or count field belongs here.
ELIGIBILITY_REASONS: dict[str, str] = {
    "issue_key": "every clause reads the board and the dispatcher's memory by it",
    "team_key": "the team clause and the scope clause read the issue's board",
    "project": "the scope clause matches the project's display name",
    "project_id": "the scope clause matches the project id and its initiatives",
    "queue_states": "the approval clause, and the scan's approved query",
    "state_kind": "the open clause, over the issue and over each blocker",
    "relations": "the blocker clause reads the issue's blocked-by edges; the "
    "other kinds are varied (UNREAD_EDGE_KINDS)",
    "updated_at": "the memory clause re-admits an issue that moved",
}
ELIGIBILITY_INPUTS = frozenset(ELIGIBILITY_REASONS)

#: Every other field of the row, derived from the model.
VARIED = frozenset(TrackerIssue.model_fields) - RANK_INPUTS - ELIGIBILITY_INPUTS

#: Exact. The row fields the scope flow reads to decide which lanes it may
#: fire, one reason each: the ready read, the scope's entry and approval.
#: No size, body or count field belongs here.
SCOPE_ELIGIBILITY_REASONS: dict[str, str] = {
    "issue_key": "the scope's membership and every per-lane read are keyed by it",
    "issue_labels": "the ready read sets criteria and record issues apart by "
    "label, and the entry requires every member's organize stage markers",
    "state_kind": "the subtree closure reads which criteria are open, and a lane "
    "owing none is closed",
    "relations": "the topology reads the lane's blocked-by edges; the other kinds "
    "are varied (UNREAD_EDGE_KINDS)",
    "parent_key": "the ready read roots each lane's subtree by parentage, and "
    "approval is inherited up it",
    "project": "approval refuses a project member with no canonical project key",
    "project_id": "approval is inherited from the lane's project",
    "milestone_key": "approval refuses a milestone member with no owning project",
}
SCOPE_ELIGIBILITY_INPUTS = frozenset(SCOPE_ELIGIBILITY_REASONS)

#: Every other field of a scope lane's row, derived from the model.
SCOPE_VARIED = (
    frozenset(TrackerIssue.model_fields) - RANK_INPUTS - SCOPE_ELIGIBILITY_INPUTS
)

#: The field the scope flow's everything case leaves alone: a lane's subject
#: is its body, or its title where the body is empty, and a lane with
#: neither is refused before its graph launches.
SCOPE_SUBJECT_FALLBACK = "title"

#: What the port answers about an issue's subtree, beyond the row: its
#: deliverable children, its open criteria, its comments, and the criteria
#: under it that are already done.
SUBTREE = ("sub_issues", "criteria", "comments", "done")

#: Exact. The edge kinds an eligibility clause reads, one reason each.
READ_EDGE_KINDS: dict[IssueRelationKind, str] = {
    IssueRelationKind.BLOCKED_BY: "blocker_keys keeps these edges alone",
}

#: Every other edge kind, derived from the enum: a count over them is a
#: size, so heavy issues carry many of each.
UNREAD_EDGE_KINDS = tuple(
    kind for kind in IssueRelationKind if kind not in READ_EDGE_KINDS
)

#: "A great deal": a text of thousands of lines, and a collection of hundreds.
LONG_TEXT = "\n".join(f"line {number} of a very long text" for number in range(5000))
MANY = 200
MANY_LABELS = frozenset(f"label-{number}" for number in range(MANY))
CRITERION_LABEL = "criterion"
#: A great deal of time: an age of centuries.
LONG_TIME = timedelta(days=100 * 365)
#: The scope flow's subtree great deal, per part and per lane: the ready read
#: re-reads every lane's subtree several times a tick, one port read each.
SCOPE_MANY = 12

HOUR = timedelta(hours=1)

#: The board: distinct ages, priorities that differ except for one pair,
#: so the age decides that pair, and neither order agrees with the board's.
#: The pair, K-4 and K-6, sits at indexes 3 and 4, opposite parities.
BOARD: tuple[TrackerIssue, ...] = (
    make_tracker_issue(
        "K-1", priority=IssuePriority.LOW, created_at=FIXTURE_EPOCH + HOUR
    ),
    make_tracker_issue(
        "K-2", priority=IssuePriority.URGENT, created_at=FIXTURE_EPOCH + 4 * HOUR
    ),
    make_tracker_issue("K-3", priority=IssuePriority.NONE, created_at=FIXTURE_EPOCH),
    make_tracker_issue(
        "K-4", priority=IssuePriority.HIGH, created_at=FIXTURE_EPOCH + 5 * HOUR
    ),
    make_tracker_issue(
        "K-6", priority=IssuePriority.HIGH, created_at=FIXTURE_EPOCH + 2 * HOUR
    ),
    make_tracker_issue(
        "K-5", priority=IssuePriority.MEDIUM, created_at=FIXTURE_EPOCH + 3 * HOUR
    ),
)

#: Every pair of board issues of equal priority, older member first.
EQUAL_PRIORITY_PAIRS: tuple[tuple[str, str], ...] = tuple(
    (older.issue_key, younger.issue_key)
    for older in BOARD
    for younger in BOARD
    if older.priority is younger.priority and older.created_at < younger.created_at
)

#: The scope board's closed lanes: members owing nothing, with no record
#: and no pull request. On a delivering origin they take the delivery-only
#: turn, in the order the scope lists them, and their entry finds nothing
#: to deliver, so each rests after its one turn.
SCOPE_CLOSED = ("K-7", "K-8")

#: The scope board's lanes in the order the scope lists them: the board's
#: issues, then the closed lanes.
LANES: tuple[str, ...] = (*(issue.issue_key for issue in BOARD), *SCOPE_CLOSED)

#: The heavy sets: which issues take the great-deal end of a variation.
#: Each parity of the lanes (so each closed lane is heavy in one and light
#: in the other), the older member of every equal-priority pair and then
#: the younger one, and each board issue alone.
HEAVY_SETS: dict[str, frozenset[str]] = {
    "first": frozenset(LANES[0::2]),
    "second": frozenset(LANES[1::2]),
    "older": frozenset(older for older, _ in EQUAL_PRIORITY_PAIRS),
    "younger": frozenset(younger for _, younger in EQUAL_PRIORITY_PAIRS),
    **{f"alone-{issue.issue_key}": frozenset({issue.issue_key}) for issue in BOARD},
}

#: Exact. The decision over the base board: every issue claimed and enqueued
#: once, in rank order.
BASE_DECISION = (
    ("K-2", "K-6", "K-4", "K-5", "K-1", "K-3"),
    ("K-2", "K-6", "K-4", "K-5", "K-1", "K-3"),
)

#: The rank order of the board's issues, which is the order the scope flow
#: fires its lanes in.
RANK_ORDER = ("K-2", "K-6", "K-4", "K-5", "K-1", "K-3")

#: The two criteria every scope lane owes: one its first fire closes, and
#: one no fire closes. The first is the lane's own criterion, which the
#: scope board names ``<lane>/check``.
SCOPE_CHECKS = ("check", "second")

#: How a grading's prompt names the criteria it asks about: between these
#: two markers of the evaluator's template, one criterion per line, its
#: key first. Every key the scope board mints carries a slash.
CRITERIA_SECTION = re.compile(
    r"── ACCEPTANCE CRITERIA TO EVALUATE ──(.*?)── CHANGESET TO EVALUATE ──",
    re.DOTALL,
)
CRITERION_LINE = re.compile(r"^(\S+/\S+) ", re.MULTILINE)

#: The walk's bound. The heaviest variation walks in about 24 s on its own;
#: the machine the suite runs on shares its cores with several test runs.
SCOPE_WALK_BOUND_SECONDS = 300

#: Exact. The scope flow's decision over the same lanes: every ready lane
#: fired twice in rank order — its first fire closes one criterion and the
#: lane is offered again, its second closes nothing and the lane rests —
#: after the closed lanes took their delivery-only turn and rested, in the
#: order the scope lists them; no lane failed; one tick per fire, one per
#: closed lane, and the tick that finds nothing to offer.
SCOPE_BASE_DECISION = (
    tuple(key for key in RANK_ORDER for _ in range(2)),
    (*SCOPE_CLOSED, *RANK_ORDER),
    (),
    2 * len(RANK_ORDER) + len(SCOPE_CLOSED) + 1,
)


def extremes(name: str) -> tuple[object, object]:
    """The two ends of a varied field's range: nothing, and a great deal."""
    annotation = TrackerIssue.model_fields[name].annotation
    if annotation is str:
        return "", LONG_TEXT
    if get_origin(annotation) in {Union, types.UnionType} and set(
        get_args(annotation)
    ) == {str, type(None)}:
        return None, LONG_TEXT
    if get_origin(annotation) is frozenset and get_args(annotation) == (str,):
        return frozenset(), MANY_LABELS
    if (
        get_origin(annotation) is frozenset
        and isinstance(get_args(annotation)[0], type)
        and issubclass(get_args(annotation)[0], StrEnum)
    ):
        return frozenset(), frozenset(get_args(annotation)[0])
    if annotation is int:
        return 0, 10**9
    if annotation is datetime:
        return FIXTURE_EPOCH, FIXTURE_EPOCH + LONG_TIME
    pytest.fail(f"no extremes are known for {name}: {annotation}")


def unread_edges(issue: TrackerIssue) -> tuple[IssueRelation, ...]:
    """*issue*'s own edges, and many of every kind no clause reads.

    Each points at a key off the board, so nothing the dispatcher could
    look up behind it is on the board either.
    """
    return (
        *issue.relations,
        *(
            IssueRelation(kind=kind, issue_key=f"{issue.issue_key}-{kind}-{number}")
            for kind in UNREAD_EDGE_KINDS
            for number in range(MANY)
        ),
    )


def rebuilt(issue: TrackerIssue, **update: object) -> TrackerIssue:
    """*issue* with *update*, validated the way an adapter's row is."""
    return TrackerIssue.model_validate({**issue.model_dump(), **update})


@dataclass(frozen=True)
class Variation:
    """One varied board: row fields per issue, and a subtree per issue."""

    #: The row fields varied: each takes its great-deal end on the heavy
    #: issues and its nothing end on the others.
    fields: tuple[str, ...]
    #: The subtree parts given many entries under the heavy issues.
    subtree: tuple[str, ...]
    #: Whether the heavy issues carry many edges of the unread kinds.
    edges: bool
    #: The issues that take the great-deal end.
    heavy: frozenset[str]

    def board(self) -> tuple[TrackerIssue, ...]:
        return self.rows(BOARD)

    def rows(self, base: Sequence[TrackerIssue]) -> tuple[TrackerIssue, ...]:
        """*base* with this variation's fields and edges set, issue by issue."""
        rows = []
        for issue in base:
            heavy = issue.issue_key in self.heavy
            update: dict[str, object] = {
                name: extremes(name)[1 if heavy else 0] for name in self.fields
            }
            if self.edges and heavy:
                update["relations"] = [
                    edge.model_dump() for edge in unread_edges(issue)
                ]
            rows.append(rebuilt(issue, **update))
        return tuple(rows)

    def heavy_keys(self) -> tuple[str, ...]:
        return tuple(
            issue.issue_key for issue in BOARD if issue.issue_key in self.heavy
        )


def done(issue: TrackerIssue) -> TrackerIssue:
    """*issue* as a board holds it when somebody finished it elsewhere."""
    return issue.model_copy(
        update={"state_name": "Done", "state_kind": WorkflowStateKind.COMPLETED}
    )


def subtree_of(
    parents: Sequence[str], parts: Sequence[str]
) -> tuple[tuple[TrackerIssue, ...], tuple[TrackerComment, ...]]:
    """Many children, criteria, done criteria and comments under each of *parents*.

    The children carry no queue state, so no scan finds them: they are what
    the port answers about their parent, never candidates of their own.
    """
    children = [
        done(child) if part == "done" else child
        for parent in parents
        for part in parts
        if part != "comments"
        for number in range(MANY)
        for child in (
            make_tracker_issue(
                f"{parent}-{part}-{number}",
                parent_key=parent,
                queue_states=(),
                issue_labels=frozenset({CRITERION_LABEL})
                if part in {"criteria", "done"}
                else frozenset(),
            ),
        )
    ]
    comments = [
        TrackerComment(
            comment_key=f"{parent}-comment-{number}",
            issue_key=parent,
            author_key=APPROVER,
            body=LONG_TEXT,
            created_at=FIXTURE_EPOCH,
        )
        for parent in parents
        if "comments" in parts
        for number in range(MANY)
    ]
    return tuple(children), tuple(comments)


class HeldJobQueue(FakeJobQueue):
    """A scripted queue whose every run is still going until released.

    A fire the dispatcher enqueued stays live for the whole drain, so the
    next pass excludes it as in flight, exactly as a running fire is.
    """

    def __init__(self) -> None:
        super().__init__()
        self.released = asyncio.Event()

    def attach(self, *, job_id: str) -> AsyncGenerator[AgentEvent, None]:
        self.attached.append(job_id)
        released = self.released

        async def _held() -> AsyncGenerator[AgentEvent, None]:
            await released.wait()
            for event in ():
                yield event

        return _held()


async def decision(
    board: Sequence[TrackerIssue],
    *,
    children: Sequence[TrackerIssue] = (),
    comments: Sequence[TrackerComment] = (),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Issues claimed and fires enqueued, pass by pass, until the board drains.

    Built through the composition root's own function. Bounded: a board of
    N issues drains in at most N passes, and one more pass must enqueue
    nothing.
    """
    tracker = FakeTrackerPort(issues=[*board, *children])
    tracker.comments.extend(comments)
    queue = HeldJobQueue()
    operation = operation_config()
    runtime = await build_dispatch_runtime(
        workspace=FakeWorkspaceProvider(),
        config=AppConfig(_env_file=None, dispatch_pass_gate_signals=[]),
        operation=operation,
        dialled=DialledTracker(
            tracker=tracker,
            ledger=tracker.self_writes,
            caller=ManagedFakeLinearMcpServer(),
            operation=operation,
            status=FakeScopeStatusWriter(),
        ),
        github_api=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        prompts=make_prompt_provider(),
        runner=FakeAgentRunner(events=[]),
        skills=SUPPRESS_ALL_SKILLS,
        recorder=RunRecorder(records={}, sinks={}),
        log=get_logger(__name__),
    )
    try:
        (tick,) = [
            entry
            for entry in runtime.scheduler.passes
            if entry.name.startswith("dispatch:")
        ]
        for _ in range(len(board) + 1):
            enqueued = len(queue.submissions)
            await tick.run(FIXTURE_EPOCH)
            if len(queue.submissions) == enqueued:
                break
        else:
            pytest.fail("the board did not drain within one pass per issue")
        return (
            tuple(tracker.claim_writes),
            tuple(request.issue_key for _, request in queue.submissions),
        )
    finally:
        queue.released.set()
        if runtime.lifecycle is not None:
            await runtime.lifecycle.drain()


def variations(
    varied: frozenset[str], together: frozenset[str]
) -> dict[str, Variation]:
    """Each varied input alone, then *together* at once, per heavy set."""
    cases: dict[str, Variation] = {}
    for side, heavy in HEAVY_SETS.items():
        for name in sorted(varied):
            cases[f"{name}-{side}"] = Variation((name,), (), False, heavy)
        for part in SUBTREE:
            cases[f"{part}-{side}"] = Variation((), (part,), False, heavy)
        cases[f"unread_edges-{side}"] = Variation((), (), True, heavy)
        cases[f"everything-{side}"] = Variation(
            tuple(sorted(together)), SUBTREE, True, heavy
        )
    return cases


VARIATIONS = variations(VARIED, VARIED)
SCOPE_VARIATIONS = variations(SCOPE_VARIED, SCOPE_VARIED - {SCOPE_SUBJECT_FALLBACK})


def own_criterion(lane: str) -> str:
    """The one criterion of *lane* a fire closes: the lane's own, by name."""
    return f"{lane}/{SCOPE_CHECKS[0]}"


def asked_about(prompt: str) -> str:
    """The section of a grading's *prompt* that lists the criteria it asks about."""
    (section,) = CRITERIA_SECTION.findall(prompt)
    return section


class ClosingExecutor(ObservedNativeExecutor):
    """An agent boundary whose fires return, each closing at most one criterion.

    Every grading passes exactly the lane's own criterion when the fire was
    asked about it, and nothing otherwise; the roster it answers is the one
    the prompt names, so no answer is invented and none is missing. A lane's
    first fire is asked about everything it owes and closes its own
    criterion; its second is asked only about what is left and closes
    nothing. The rule reads no size and is the same for every lane, so what
    the walk decides from it is the walk's own.
    """

    def __init__(self) -> None:
        super().__init__([])
        #: Every grading: the lane, the criteria asked about, those passed.
        self.gradings: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []

    async def stream(self, **kwargs: object) -> AsyncGenerator[AgentEvent, None]:
        output_format = kwargs.get("output_format")
        schema = output_format.get("schema") if isinstance(output_format, dict) else {}
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        if "criteriaResults" in properties:
            identity = kwargs.get("run_identity")
            assert isinstance(identity, RunIdentity), "a grading names its fire"
            lane = identity.name
            asked = tuple(CRITERION_LINE.findall(asked_about(str(kwargs["prompt"]))))
            passed = tuple(key for key in asked if key == own_criterion(lane))
            self.gradings.append((lane, asked, passed))
            self.evaluations.append(criteria_echo(keys=asked, passed=set(passed)))
        async for event in super().stream(**kwargs):
            yield event


def scope_lanes(variation: Variation | None) -> FakeTrackerPort:
    """The scope board: one lane per board issue, varied by *variation*.

    Each lane carries its issue's priority and age, the stage markers the
    scope board gives every lane, and its two criteria. The closed lanes
    follow, finished by hand: both criteria done and the lane's own issue
    done, and nothing anywhere recording a branch or a pull request for
    them. Heavy lanes are given the variation's subtree: ``SCOPE_MANY``
    criteria, as many deliverable children each with a criterion of its
    own, as many long comments, and as many criteria already done; under a
    ready lane the first two parts' criteria are open, under a closed lane
    they are done, so a closed lane stays closed.
    """
    port = scope_board(
        lanes=LANES,
        priorities={issue.issue_key: issue.priority for issue in BOARD},
        checks=dict.fromkeys(LANES, SCOPE_CHECKS),
    )
    for key in SCOPE_CLOSED:
        for issue_key in (key, *(f"{key}/{name}" for name in SCOPE_CHECKS)):
            port.issues[issue_key] = done(port.issues[issue_key])
    aged = tuple(
        rebuilt(
            port.issues[issue.issue_key],
            created_at=issue.created_at,
            updated_at=issue.created_at,
        )
        for issue in BOARD
    ) + tuple(port.issues[key] for key in SCOPE_CLOSED)
    lanes = aged if variation is None else variation.rows(aged)
    for lane in lanes:
        port.issues[lane.issue_key] = lane
    parts = () if variation is None else variation.subtree
    for lane in lanes:
        if variation is None or lane.issue_key not in variation.heavy:
            continue
        closed = lane.issue_key in SCOPE_CLOSED
        for number in range(SCOPE_MANY):
            owed = []
            if "done" in parts:
                key = f"{lane.issue_key}/done-{number}"
                port.issues[key] = done(
                    make_tracker_issue(
                        key,
                        parent_key=lane.issue_key,
                        queue_states=(),
                        issue_labels=frozenset({CRITERION_LABEL}),
                        body=f"**Check:** {key} held\n**Evidence:** —",
                    )
                )
            if "criteria" in parts:
                owed.append((f"{lane.issue_key}/many-{number}", lane.issue_key))
            if "sub_issues" in parts:
                child = make_tracker_issue(
                    f"{lane.issue_key}-child-{number}",
                    parent_key=lane.issue_key,
                    queue_states=(),
                    issue_labels=lane.issue_labels,
                )
                port.issues[child.issue_key] = child
                owed.append((f"{child.issue_key}/check", child.issue_key))
            for key, parent in owed:
                criterion = make_tracker_issue(
                    key,
                    parent_key=parent,
                    queue_states=(),
                    issue_labels=frozenset({CRITERION_LABEL}),
                    body=f"**Check:** {key} holds\n**Evidence:** —",
                )
                port.issues[key] = done(criterion) if closed else criterion
            if "comments" in parts:
                port.comments.append(
                    TrackerComment(
                        comment_key=f"{lane.issue_key}-comment-{number}",
                        issue_key=lane.issue_key,
                        author_key=APPROVER,
                        body=LONG_TEXT,
                        created_at=FIXTURE_EPOCH,
                    )
                )
    return port


ScopeDecision = tuple[
    tuple[str, ...], tuple[str, ...], tuple[tuple[str, str, str], ...], int
]


async def scope_walk(
    port: FakeTrackerPort,
) -> tuple[ScopeDecision, ClosingExecutor]:
    """One whole scope walk over *port*, and the agent boundary it ran through.

    Built through ``build_workflow_engine``, the function ``main.py``
    calls, which composes the scope flow through ``build_scope_runtime``,
    on an origin with a forge behind it and over repositories that commit
    as a lane's do, so every fire enters, records and delivers the way a
    deployed one does. Bounded by ``SCOPE_WALK_BOUND_SECONDS``. The log is
    captured rather than rendered.

    What comes back is the decision: the lanes fired in order, the lanes
    rested in order, every contained lane failure, and the tick count.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    executor = ClosingExecutor()
    try:
        harness = resumable(
            port=port,
            repos=repos,
            lanes=LANES,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            executor=executor,
        )
        with structlog.testing.capture_logs():
            async with asyncio.timeout(SCOPE_WALK_BOUND_SECONDS):
                events = [
                    event
                    async for event in drive(
                        harness, job="decision-job", origin=FORGE_ORIGIN
                    )
                ]
    finally:
        await forge.close()
    ticks = [event.observation for event in events if isinstance(event, ScopeWalkEvent)]
    last = ticks[-1]
    decided: ScopeDecision = (
        last.dispatched,
        last.rested_lanes,
        tuple(
            (failure.issue_key, failure.error.error_kind, failure.error.error)
            for failure in last.failed_lanes
        ),
        len(ticks),
    )
    return decided, executor


async def scope_decision(port: FakeTrackerPort) -> ScopeDecision:
    """The decision one scope walk over *port* makes."""
    decided, _ = await scope_walk(port)
    return decided


def test_the_row_is_partitioned_into_rank_eligibility_and_varied_fields() -> None:
    """Three disjoint sets that together are the row, and a non-empty third.

    ``VARIED`` is derived, so a field added to ``TrackerIssue`` is varied
    unless it is registered as a rank or eligibility input with a reason.
    Each varied field has a known range; one without fails here.
    """
    fields = set(TrackerIssue.model_fields)
    assert RANK_INPUTS | ELIGIBILITY_INPUTS | VARIED == fields
    assert not RANK_INPUTS & ELIGIBILITY_INPUTS
    assert not RANK_INPUTS & VARIED
    assert not ELIGIBILITY_INPUTS & VARIED
    assert VARIED
    assert all(reason.strip() for reason in ELIGIBILITY_REASONS.values())
    for name in VARIED:
        low, high = extremes(name)
        assert low != high, name
    scope_fields = SCOPE_ELIGIBILITY_INPUTS | SCOPE_VARIED
    assert RANK_INPUTS | scope_fields == fields
    assert not RANK_INPUTS & SCOPE_ELIGIBILITY_INPUTS
    assert not RANK_INPUTS & SCOPE_VARIED
    assert not SCOPE_ELIGIBILITY_INPUTS & SCOPE_VARIED
    assert SCOPE_VARIED
    assert SCOPE_SUBJECT_FALLBACK in SCOPE_VARIED
    assert all(reason.strip() for reason in SCOPE_ELIGIBILITY_REASONS.values())
    for name in SCOPE_VARIED:
        low, high = extremes(name)
        assert low != high, name
    assert set(READ_EDGE_KINDS) | set(UNREAD_EDGE_KINDS) == set(IssueRelationKind)
    assert not set(READ_EDGE_KINDS) & set(UNREAD_EDGE_KINDS)
    assert UNREAD_EDGE_KINDS
    assert all(reason.strip() for reason in READ_EDGE_KINDS.values())


def test_every_equal_priority_pair_is_split_both_ways() -> None:
    """A size used below the priority can move the decision on this board.

    The age, and any tie-break, decides only between issues of equal
    priority. So for every such pair on the board there is a variation in
    which its older member is heavy and its younger light, and one the
    other way round; a size folded into the age or used as a tie-break then
    moves the order of that pair in one of them.
    """
    assert EQUAL_PRIORITY_PAIRS
    for cases in (VARIATIONS, SCOPE_VARIATIONS):
        for older, younger in EQUAL_PRIORITY_PAIRS:
            splits = {
                (older in variation.heavy, younger in variation.heavy)
                for variation in cases.values()
            }
            assert {(True, False), (False, True)} <= splits, (older, younger)


async def test_the_composed_dispatcher_drains_the_board_in_rank_order() -> None:
    """The base decision: priority first, age second, every issue once.

    The literal is written out, so a recorder that saw nothing, or a
    dispatcher composed some other way, fails here before any variation is
    compared with it.
    """
    assert await decision(BOARD) == BASE_DECISION


async def test_the_port_answers_the_varied_subtree() -> None:
    """The control for the subtree variation: the port does hold it.

    A subtree built for one issue is read back through the port methods a
    consumer asks — the criteria, open and done, the comments, and the
    scan, which must not find the children as candidates of their own.
    """
    (parent,) = BOARD[:1]
    children, comments = subtree_of((parent.issue_key,), SUBTREE)
    tracker = FakeTrackerPort(issues=[parent, *children])
    tracker.comments.extend(comments)
    criteria = await tracker.read_criteria(issue_key=parent.issue_key)
    listed = await tracker.list_comments(issue_key=parent.issue_key)
    scanned = await tracker.scan_issues(
        query=IssueQuery(queue_state=QueueState.APPROVED, page_size=10 * MANY),
    )
    assert len(children) == 3 * MANY
    assert len(criteria) == 2 * MANY
    assert [
        issue.state_kind is WorkflowStateKind.COMPLETED for issue in criteria
    ].count(True) == MANY
    assert len(listed) == MANY
    assert [issue.issue_key for issue in scanned] == [parent.issue_key]


@pytest.mark.parametrize("case", sorted(VARIATIONS))
async def test_the_decision_does_not_move_when_a_size_moves(case: str) -> None:
    """The same claims and the same fires, whatever the varied inputs hold.

    The variation is checked to reach the board first, so a variation that
    changed nothing cannot pass for one the decision withstood.
    """
    variation = VARIATIONS[case]
    board = variation.board()
    children, comments = subtree_of(variation.heavy_keys(), variation.subtree)
    assert board != BOARD or children or comments, case
    assert await decision(board, children=children, comments=comments) == (
        BASE_DECISION
    )


async def test_the_composed_scope_flow_fires_its_lanes_in_rank_order() -> None:
    """The scope flow's base decision, and the control for its fires.

    The literal is written out, so a walk that fired nothing, or a scope
    flow composed some other way, fails here before any variation is
    compared with it. And the fires did what the walk's decision is read
    from: each lane's first fire was asked about both its criteria and
    closed its own, its second was asked about the one left and closed
    nothing, so on the board after the walk every lane's own criterion is
    done and its other one is still owed.
    """
    port = scope_lanes(None)
    decided, executor = await scope_walk(port)
    assert decided == SCOPE_BASE_DECISION
    asked_by_lane: dict[str, list[tuple[tuple[str, ...], tuple[str, ...]]]] = {}
    for lane, asked, passed in executor.gradings:
        asked_by_lane.setdefault(lane, []).append((asked, passed))
    assert set(asked_by_lane) == set(RANK_ORDER)
    for lane, gradings in asked_by_lane.items():
        both = frozenset(f"{lane}/{name}" for name in SCOPE_CHECKS)
        first = [(asked, passed) for asked, passed in gradings if set(asked) == both]
        second = [(asked, passed) for asked, passed in gradings if set(asked) != both]
        assert first and all(passed == (own_criterion(lane),) for _, passed in first)
        assert second and all(
            asked == (f"{lane}/{SCOPE_CHECKS[1]}",) and passed == ()
            for asked, passed in second
        )
        assert port.issues[own_criterion(lane)].state_kind is (
            WorkflowStateKind.COMPLETED
        )
        assert port.issues[f"{lane}/{SCOPE_CHECKS[1]}"].state_kind is (
            WorkflowStateKind.UNSTARTED
        )


def test_each_closed_lane_is_heavy_in_one_variation_and_light_in_another() -> None:
    """The closed lanes' sizes are varied too, and against each other.

    The delivery-only turn offers the closed lanes in the order the scope
    lists them, so a size rule there is caught only if the two differ in
    size: there is a variation in which the first is heavy and the second
    light, and one the other way round.
    """
    first, second = SCOPE_CLOSED
    splits = {
        (first in variation.heavy, second in variation.heavy)
        for variation in SCOPE_VARIATIONS.values()
    }
    assert {(True, False), (False, True)} <= splits


async def test_the_ready_read_answers_the_varied_gap() -> None:
    """The control for the scope subtree: the lane selector is handed it.

    A lane given the whole subtree is read back through the ready read the
    walk selects from: its gap is its own two criteria, the ``SCOPE_MANY``
    criteria under it and one criterion under each of its ``SCOPE_MANY``
    children, and a light lane's gap is its own two criteria alone. Its
    roster is that gap and the ``SCOPE_MANY`` criteria already done, so the
    part of its work still open is what the variation changed. The closed
    lanes are read back as closed, whatever sits under them: a heavy closed
    lane's subtree holds as many criteria, all done.
    """
    heavy = BOARD[0].issue_key
    closed = SCOPE_CLOSED[0]
    variation = Variation((), SUBTREE, False, frozenset({heavy, closed}))
    port = scope_lanes(variation)
    ready = await read_scope_ready(ref=SCOPE, tracker=port)
    gaps = {lane.issue.issue_key: len(lane.gap) for lane in ready.ready}
    rosters = {lane.issue.issue_key: len(lane.criteria) for lane in ready.ready}
    own = len(SCOPE_CHECKS)
    assert gaps == {
        issue.issue_key: own + 2 * SCOPE_MANY if issue.issue_key == heavy else own
        for issue in BOARD
    }
    assert rosters == {
        key: gap + SCOPE_MANY if key == heavy else gap for key, gap in gaps.items()
    }
    assert tuple(issue.issue_key for issue in ready.closed) == SCOPE_CLOSED
    under_closed = [
        issue
        for issue in port.issues.values()
        if issue.issue_key.startswith(closed) and CRITERION_LABEL in issue.issue_labels
    ]
    assert len(under_closed) == own + 3 * SCOPE_MANY
    assert all(
        issue.state_kind is WorkflowStateKind.COMPLETED for issue in under_closed
    )
    assert len(await port.list_comments(issue_key=heavy)) == SCOPE_MANY


@pytest.mark.parametrize("case", sorted(SCOPE_VARIATIONS))
async def test_the_scope_flow_does_not_move_when_a_size_moves(case: str) -> None:
    """The same lanes fired, re-fired and rested in the same order, whatever
    the varied inputs hold.

    The variation is checked to reach the scope board first, so a variation
    that changed nothing cannot pass for one the walk withstood.
    """
    variation = SCOPE_VARIATIONS[case]
    port = scope_lanes(variation)
    assert port.issues != scope_lanes(None).issues or port.comments, case
    assert await scope_decision(port) == SCOPE_BASE_DECISION
