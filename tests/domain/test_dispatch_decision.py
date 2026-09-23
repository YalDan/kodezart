"""The dispatch decision does not move when a size moves (KOD-731).

The static guard beside this one (``test_dispatch_rank_inputs.py``) pins the
rank's code as it stands: the ordering key texts and the bodies of the
definitions on the dispatch path. This one pins the decision itself, which
no respelling of that code can reach around. The dispatcher is built the way
the composition root builds it — through ``build_dispatch_runtime``, the
function ``main.py`` calls, over the fake tracker and a scripted queue — and
its passes are run until the board is drained. What is recorded is the
sequence of issues claimed and the sequence of fires enqueued, which is what
the hand-off to ``launch`` produces.

The row is partitioned three ways, off the row model itself. ``RANK_INPUTS``
are the two fields a rank is made from. ``ELIGIBILITY_INPUTS`` are the fields
the eligibility clauses read at head, each with its reason; no size, body or
count field is among them. Every other field of ``TrackerIssue`` is varied,
so a field added to the row is varied as soon as it exists, and a field whose
range this module does not know fails loudly. Beyond the row, what the port
answers about an issue's subtree is varied too: its sub-issues, its
criterion sub-issues and its comments. Each varied input is set to its
extremes — nothing, and a great deal — on alternate issues of the board, one
input at a time, then all of them together, and the decision must equal the
base decision every time.

Reach: the decision as the composition root composes it is invariant under
every non-rank, non-eligibility input the board holds, at the extremes
above. So a size read off the board anywhere between the scan and the
enqueue — a rebinding at boot, a subclass built at the root, a validator on
the row, an eligibility clause, a table consulted after the selection, a
hook in the rank value — fails here whatever it is spelled, as soon as it
moves the decision for a board holding those extremes.

Limit: a size taken from outside the board (for example, from the
repository) is not varied here. It would have to be read in one of the path
bodies the static guard pins, or after the selection, where only the
board-derived variation reaches it. A size rule that moves nothing at these
extremes — a threshold beyond them — is not seen. The pass runs ungated
(``dispatch_pass_gate_signals`` empty, a legal deployment), so the gate's
own reading of the board is outside this test; the gate decides whether a
pass runs, not which issue it claims.
"""

import asyncio
import types
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from datetime import timedelta
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
    TrackerComment,
    TrackerIssue,
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
    "relations": "the blocker clause reads the issue's edges",
    "updated_at": "the memory clause re-admits an issue that moved",
}
ELIGIBILITY_INPUTS = frozenset(ELIGIBILITY_REASONS)

#: Every other field of the row, derived from the model.
VARIED = frozenset(TrackerIssue.model_fields) - RANK_INPUTS - ELIGIBILITY_INPUTS

#: What the port answers about an issue's subtree, beyond the row.
SUBTREE = ("sub_issues", "criteria", "comments")

#: "A great deal": a text of thousands of lines, and a collection of hundreds.
LONG_TEXT = "\n".join(f"line {number} of a very long text" for number in range(5000))
MANY = 200
MANY_LABELS = frozenset(f"label-{number}" for number in range(MANY))
CRITERION_LABEL = "criterion"

HOUR = timedelta(hours=1)

#: The board: distinct ages, priorities that differ except for one pair,
#: so the age decides that pair, and neither order agrees with the board's.
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
        "K-5", priority=IssuePriority.MEDIUM, created_at=FIXTURE_EPOCH + 3 * HOUR
    ),
    make_tracker_issue(
        "K-6", priority=IssuePriority.HIGH, created_at=FIXTURE_EPOCH + 2 * HOUR
    ),
)

#: Exact. The decision over the base board: every issue claimed and enqueued
#: once, in rank order.
BASE_DECISION = (
    ("K-2", "K-6", "K-4", "K-5", "K-1", "K-3"),
    ("K-2", "K-6", "K-4", "K-5", "K-1", "K-3"),
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
    if annotation is int:
        return 0, 10**9
    pytest.fail(f"no extremes are known for {name}: {annotation}")


def rebuilt(issue: TrackerIssue, **update: object) -> TrackerIssue:
    """*issue* with *update*, validated the way an adapter's row is."""
    return TrackerIssue.model_validate({**issue.model_dump(), **update})


@dataclass(frozen=True)
class Variation:
    """One varied board: row fields per issue, and a subtree per issue."""

    #: The row fields varied: each takes its great-deal end on the issues at
    #: the chosen parity and its nothing end on the others.
    fields: tuple[str, ...]
    #: The subtree parts given many entries under the issues at that parity.
    subtree: tuple[str, ...]
    #: Whether that parity starts at the first issue or the second.
    first: bool

    def board(self) -> tuple[TrackerIssue, ...]:
        rows = []
        for index, issue in enumerate(BOARD):
            heavy = (index % 2 == 0) == self.first
            update = {name: extremes(name)[1 if heavy else 0] for name in self.fields}
            rows.append(rebuilt(issue, **update))
        return tuple(rows)

    def heavy_keys(self) -> tuple[str, ...]:
        return tuple(
            issue.issue_key
            for index, issue in enumerate(BOARD)
            if (index % 2 == 0) == self.first
        )


def subtree_of(
    parents: Sequence[str], parts: Sequence[str]
) -> tuple[tuple[TrackerIssue, ...], tuple[TrackerComment, ...]]:
    """Many children, criteria and comments under each of *parents*.

    The children carry no queue state, so no scan finds them: they are what
    the port answers about their parent, never candidates of their own.
    """
    children = [
        make_tracker_issue(
            f"{parent}-{part}-{number}",
            parent_key=parent,
            queue_states=(),
            issue_labels=frozenset({CRITERION_LABEL})
            if part == "criteria"
            else frozenset(),
        )
        for parent in parents
        for part in parts
        if part != "comments"
        for number in range(MANY)
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


def variations() -> dict[str, Variation]:
    """Each varied input alone at both parities, then all of them together."""
    cases: dict[str, Variation] = {}
    for first in (True, False):
        side = "first" if first else "second"
        for name in sorted(VARIED):
            cases[f"{name}-{side}"] = Variation((name,), (), first)
        for part in SUBTREE:
            cases[f"{part}-{side}"] = Variation((), (part,), first)
        cases[f"everything-{side}"] = Variation(tuple(sorted(VARIED)), SUBTREE, first)
    return cases


VARIATIONS = variations()


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
    consumer asks — the criteria, the comments, and the scan, which must
    not find the children as candidates of their own.
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
    assert len(children) == 2 * MANY
    assert len(criteria) == MANY
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
