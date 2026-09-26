"""The dispatch decision does not move when a size moves (KOD-731).

The static guard beside this one (``test_dispatch_rank_inputs.py``) pins the
rank's code as it stands: the ordering key texts and the bodies of the
definitions on the dispatch path. This one pins the decision itself, which
no respelling of that code can reach around.

The dispatch pass. The dispatcher is built the way the composition root
builds it — through ``build_dispatch_runtime``, the function ``main.py``
calls, over the fake tracker and a scripted queue — and its passes are run
until the board is drained. What is recorded is the sequence of issues
claimed, the sequence of fires enqueued and the lane each fire was enqueued
on, which is what the hand-off to ``launch`` produces.

The row is partitioned three ways, off the row model itself. ``RANK_INPUTS``
are the two fields a rank is made from. ``ELIGIBILITY_INPUTS`` are the fields
the dispatch pass's eligibility clauses read at head, each with its reason;
no size, body or count field is among them. Every other field of
``TrackerIssue`` is varied, so a field added to the row is varied as soon as
it exists, and a field whose range this module does not know fails loudly.
Beyond the row, what the port answers about an issue's subtree is varied too:
its sub-issues, its criterion sub-issues and its comments, and many criteria
under a heavy issue in a completed state. So are the edge kinds no
eligibility clause reads: the blocker clause reads blocked-by edges alone, so
the issue's other edges (blocks, related, duplicate) are a count like any
other. Each varied input is set to its extremes — nothing, and a great deal —
one input at a time, then all of them together, over several heavy sets: the
issues at each parity of the board, the older member of each equal-priority
pair and then the younger one, and each issue alone. The board's
equal-priority pair sits at opposite parities, and a plain test checks that
every such pair is split both ways, so a size that reaches the rank only
below the priority — as a tie-break, or folded into the age — moves the
decision here. The decision must equal the base decision every time.

Reach: the decision as the composition root composes it is invariant under
every non-rank, non-eligibility input the board holds, at the extremes above.
So a size read off the board anywhere between the read and the fire — a
rebinding at boot, a subclass built at the root, a
validator on the row, an eligibility clause, a table consulted after the
selection, an enqueue lane chosen after it, a hook in the rank value, a
tie-break among equal priorities, an
age shifted by a size, a count over the edges no clause reads — fails here
whatever it is spelled, as soon as it moves the decision for a board holding
those extremes.

Limit: a size taken from outside the board (for example, from the
repository) is not varied here, so it would have to be read in a path body
the static guard pins to be seen; the dispatch pass's hand-off after the
selection is not among those bodies. A size rule that moves nothing at these
extremes — a threshold beyond them — is not seen, and the extremes are these
numbers: a text of 5000 lines, a collection of 200 (labels, edges of each
unread kind, children, criteria, comments), an integer of a thousand
million, an age of a hundred years. The pass runs ungated
(``dispatch_pass_gate_signals`` empty, a legal deployment), so the gate's
own reading of the board is outside this test; the gate decides whether a
pass runs, not which issue it claims.
"""

import asyncio
import types
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Union, get_args, get_origin

import pytest

from kodezart.composition.passes import build_dispatch_runtime
from kodezart.composition.tracker import DialledTracker
from kodezart.config.app import AppConfig
from kodezart.core.logging import get_logger
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.operation import QueueState
from kodezart.types.domain.tracker import (
    IssuePriority,
    IssueQuery,
    IssueRelation,
    IssueRelationKind,
    TrackerComment,
    TrackerIssue,
    WorkflowStateKind,
)
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


#: The board's issues in board order.
LANES: tuple[str, ...] = tuple(issue.issue_key for issue in BOARD)

#: The heavy sets: which issues take the great-deal end of a variation.
#: Each parity of the board, the older member of every equal-priority pair
#: and then the younger one, and each board issue alone.
HEAVY_SETS: dict[str, frozenset[str]] = {
    "first": frozenset(LANES[0::2]),
    "second": frozenset(LANES[1::2]),
    "older": frozenset(older for older, _ in EQUAL_PRIORITY_PAIRS),
    "younger": frozenset(younger for _, younger in EQUAL_PRIORITY_PAIRS),
    **{f"alone-{issue.issue_key}": frozenset({issue.issue_key}) for issue in BOARD},
}

#: The fire-queue lane the composed dispatcher enqueues on: the
#: configuration's default, which the pass here runs under.
DISPATCH_LANE = "tracker"

#: Exact. The decision over the base board: every issue claimed and enqueued
#: once, in rank order, every fire on the one dispatch lane.
BASE_DECISION = (
    ("K-2", "K-6", "K-4", "K-5", "K-1", "K-3"),
    ("K-2", "K-6", "K-4", "K-5", "K-1", "K-3"),
    (DISPATCH_LANE,) * 6,
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
    #: Whether the heavy issues carry many labels beside the ones they have.
    labels: bool
    #: The issues that take the great-deal end.
    heavy: frozenset[str]

    def board(self) -> tuple[TrackerIssue, ...]:
        return self.rows(BOARD)

    def rows(self, base: Sequence[TrackerIssue]) -> tuple[TrackerIssue, ...]:
        """*base* with this variation's fields, edges and labels set, issue by issue."""
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
            if self.labels and heavy:
                update["issue_labels"] = issue.issue_labels | MANY_LABELS
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
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Issues claimed, fires enqueued and their lanes, until the board drains.

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
        config=AppConfig(
            _env_file=None,
            dispatch_pass_gate_signals=[],
            dispatch_pass_interval_seconds=300.0,
            dispatch_pass_timeout_seconds=240.0,
        ),
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
            tuple(lane for lane, _ in queue.submissions),
        )
    finally:
        queue.released.set()
        if runtime.lifecycle is not None:
            await runtime.lifecycle.drain()


def variations(
    varied: frozenset[str], together: frozenset[str], *, labels: bool
) -> dict[str, Variation]:
    """Each varied input alone, then *together* at once, per heavy set.

    With *labels*, the count of labels beside the ones a row has is an
    input of its own: the flow reads some labels by membership, so the
    field is not varied whole there, and what is varied is the rest.
    """
    cases: dict[str, Variation] = {}
    for side, heavy in HEAVY_SETS.items():
        for name in sorted(varied):
            cases[f"{name}-{side}"] = Variation((name,), (), False, False, heavy)
        for part in SUBTREE:
            cases[f"{part}-{side}"] = Variation((), (part,), False, False, heavy)
        cases[f"unread_edges-{side}"] = Variation((), (), True, False, heavy)
        if labels:
            cases[f"labels-{side}"] = Variation((), (), False, True, heavy)
        cases[f"everything-{side}"] = Variation(
            tuple(sorted(together)), SUBTREE, True, labels, heavy
        )
    return cases


VARIATIONS = variations(VARIED, VARIED, labels=False)


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
    for older, younger in EQUAL_PRIORITY_PAIRS:
        splits = {
            (older in variation.heavy, younger in variation.heavy)
            for variation in VARIATIONS.values()
        }
        assert {(True, False), (False, True)} <= splits, (older, younger)


async def test_the_composed_dispatcher_drains_the_board_in_rank_order() -> None:
    """The base decision: priority first, age second, every issue once, one lane.

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
