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
claimed, the sequence of fires enqueued and the lane each fire was enqueued
on, which is what the hand-off to ``launch`` produces.

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
number of ticks the walk took.

The row is partitioned three ways, off the row model itself. ``RANK_INPUTS``
are the two fields a rank is made from. ``ELIGIBILITY_INPUTS`` are the fields
the dispatch pass's eligibility clauses read at head, each with its reason;
no size, body or count field is among them. The scope flow's eligibility
inputs are read off its code instead: every package function its selection
reaches — the ready read, the lane selector, the approval reading the
tracker's approval question runs and the entry requirement its fire-subject
read runs — found by what their reads resolve to, and every row field those
functions read as an attribute or by a literal name, less the rank inputs
(``SCOPE_ELIGIBILITY_INPUTS``). Among them is the row's labels, which the
flow reads by membership alone: the criterion label and the record kinds,
read off the same functions, and the stage markers the operation names
(``SCOPE_READ_LABELS``). Every other label is a count, and in the scope
flow the count of labels beside those is varied, up to ``MANY``, as the
whole field is in the dispatch pass. Every other field of ``TrackerIssue``
is varied, so a field added to the row is varied as soon as it exists, and
a field whose range this module does not know fails loudly. Beyond the row,
what the port answers about an issue's subtree is varied too: its
sub-issues, its criterion sub-issues and its comments. In the scope flow
these are the lane's gap: many open criteria under it, and many deliverable
children, each owing a criterion of its own. So is the work already done:
many criteria under a heavy issue in a completed state, beside its open
ones, so its gap and its eligibility stay what they were while the fraction
of its work still open moves. So are the edge kinds no eligibility clause
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
selection, an enqueue lane chosen after it, a hook in the rank value, a
tie-break among equal priorities, an
age shifted by a size, a count over the edges no clause reads, a count over
the labels no clause reads, a lane skipped or chosen by its gap or by the
fraction of its work still open (a count over an eligibility input, the
criteria's state), a fired lane rested or re-offered by a size rather than
by what its fire closed, a closed lane passed over or reordered by a size
on the delivery-only turn — fails here whatever it is spelled, as soon as
it moves the decision for a board holding those extremes.

Limit: the scope register is read off the functions its selection reaches
by object, so a field read under a name built at run time, or in a function
that walk does not reach — behind a port method, or a callable handed in —
is not registered; it is then varied, and a read of it that moves the
decision fails here for that reason rather than passing. The labels the
flow reads are registered by literal, by the set a name resolves to, and
from operation config for the marker the entry requirement takes as a
parameter; a label tested under a name built at run time is a count like
the rest. A size taken from outside the board (for example, from the
repository) is not varied here, so it would have to be read in a path body
the static guard pins to be seen; the scope flow's lane selector
(``ScopeWorkflowEngine._select``), its re-fire reading (``_settle``) and the
dispatch pass's hand-off after the selection are not among those bodies. A
size rule that moves nothing at these extremes — a threshold beyond them —
is not seen, and the extremes are these numbers: a text of 5000 lines, a
collection of 200 (labels, edges of each unread kind, children, criteria,
comments), an integer of a thousand million, an age of a hundred years; in
the scope flow the subtree's great deal is ``SCOPE_MANY`` = 12 per part,
because the ready read re-reads every lane's subtree on every tick, so the
largest gap a lane owes is ``SCOPE_GAP_BOUND`` = 26 open criteria (its two
own, twelve under it and one under each of twelve children), on a roster of
38. A rule passing over a lane that owes more than 26 is not seen, and a
test holds it unseen; the same rule at 25 is caught, and a test holds that
too. In the scope flow a lane whose body and title are both empty has no
subject and is refused before its graph launches, so there the everything
case leaves the title alone and the title is varied on its own. What a
fire does inside its sessions is scripted, so which criteria a fire closes
is not decided here: the rule is the same for every lane and every size,
and what is held is what the walk decides from it. The pass runs ungated
(``dispatch_pass_gate_signals`` empty, a legal deployment), so the gate's
own reading of the board is outside this test; the gate decides whether a
pass runs, not which issue it claims.
"""

import ast
import asyncio
import builtins
import re
import types
from collections.abc import AsyncGenerator, Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Union, get_args, get_origin

import pytest
import structlog.testing

from kodezart.adapters.linear.tracker import LinearMcpTracker
from kodezart.chains.scope_walker import read_scope_ready
from kodezart.composition.passes import build_dispatch_runtime
from kodezart.composition.tracker import DialledTracker, criteria_stage_label_key
from kodezart.config.app import AppConfig
from kodezart.core.logging import get_logger
from kodezart.domain.fire_spec import require_fire_entry
from kodezart.domain.issue_tree import RECORD_KINDS
from kodezart.domain.organize import stage_rows
from kodezart.domain.scope_approval import resolve_execution_approval
from kodezart.services.run_recorder import RunRecorder
from kodezart.services.scope_runtime import ScopeWorkflowEngine
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.operation import OperationConfig, QueueState
from kodezart.types.domain.organize import split_label_key
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope_ready import ScopeReadySet
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
from tests.chains.test_native_fire import native_operation
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
    STAGED,
    ObservedNativeExecutor,
    WalkRepos,
    drive,
    resumable,
)
from tests.integration.test_scope_runtime import board as scope_board
from tests.lane_fixture import ScopeForgeWire, criteria_echo
from tests.name_resolution import (
    compiled_def,
    home,
    in_package,
    live_references,
    unwrapped,
    written_methods,
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

#: Where the scope flow decides what it may fire, as live objects: the ready
#: read the walk selects from, the lane selector, the approval reading the
#: port's approval question runs, and the entry requirement a fire's subject
#: is read under. What each of these reads, and what every package function
#: they reach reads, is the scope flow's eligibility register.
SCOPE_SELECTION: tuple[types.FunctionType, ...] = (
    read_scope_ready,
    ScopeWorkflowEngine._select,
    resolve_execution_approval,
    require_fire_entry,
)


def reached(starts: Iterable[types.FunctionType]) -> tuple[types.FunctionType, ...]:
    """Every package function *starts* reach, by what their reads resolve to.

    A function joins when a definition already on the path references it —
    through a global, a module attribute, an import inside the definition,
    a ``functools.partial`` or a bound method — and so does every method
    written in a package class a definition references. Bounded by the
    package's finite functions: the walk stops at its fixed point.
    """
    path = {id(function): function for function in starts}
    while True:
        before = len(path)
        for function in tuple(path.values()):
            for value in map(unwrapped, live_references(function).values()):
                if isinstance(value, types.FunctionType) and in_package(value):
                    path.setdefault(id(value), value)
                elif isinstance(value, type) and in_package(value):
                    for method in written_methods(value):
                        path.setdefault(id(method), method)
        if len(path) == before:
            return tuple(path.values())


def _literal(node: ast.AST) -> str | None:
    """The string *node* spells, when it is a string constant."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def field_reads(
    functions: Iterable[types.FunctionType], fields: Iterable[str]
) -> dict[str, frozenset[str]]:
    """Each of *fields* read in *functions*, and the homes of the reads.

    A read is an attribute of that name on any receiver, or ``getattr`` of
    a receiver with the name as a string literal. A name built at run time
    is not a read here, and neither is one made in a function the walk does
    not reach — behind a port method, or a callable handed in.
    """
    known = frozenset(fields)
    homes: dict[str, set[str]] = {}
    for function in functions:
        resolved = live_references(function)
        for node in ast.walk(compiled_def(function)):
            name = None
            if isinstance(node, ast.Attribute):
                name = node.attr
            elif (
                isinstance(node, ast.Call)
                and resolved.get(ast.unparse(node.func)) is builtins.getattr
                and len(node.args) >= 2
            ):
                name = _literal(node.args[1])
            if name in known:
                homes.setdefault(name, set()).add(home(function))
    return {name: frozenset(found) for name, found in sorted(homes.items())}


def label_reads(
    functions: Iterable[types.FunctionType], field: str
) -> dict[str, frozenset[str]]:
    """Each label *functions* test *field* for, and the homes of the reads.

    A read is a membership test of a string literal in the field, or the
    intersection of the field with a set of strings a name resolves to. A
    label read from a parameter — the stage marker the entry requirement
    takes from operation config — spells no literal and is registered from
    that config instead (``SCOPE_STAGE_MARKERS``).
    """

    def reads_field(node: ast.AST) -> bool:
        return isinstance(node, ast.Attribute) and node.attr == field

    homes: dict[str, set[str]] = {}
    for function in functions:
        resolved = live_references(function)
        for node in ast.walk(compiled_def(function)):
            labels: set[str] = set()
            if isinstance(node, ast.Compare) and any(
                map(reads_field, node.comparators)
            ):
                literal = _literal(node.left)
                if literal is not None:
                    labels.add(literal)
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitAnd):
                sides = (node.left, node.right)
                if any(map(reads_field, sides)):
                    for side in sides:
                        value = resolved.get(ast.unparse(side))
                        if isinstance(value, frozenset | set) and all(
                            isinstance(member, str) for member in value
                        ):
                            labels.update(value)
            for label in labels:
                homes.setdefault(label, set()).add(home(function))
    return {name: frozenset(found) for name, found in sorted(homes.items())}


def stage_markers(operation: OperationConfig) -> frozenset[str]:
    """The stage-marker labels *operation* names, which every lane must carry.

    The terminal marker of every organize stage that runs under approval,
    or, where the operation declares no such table, the one criteria-stage
    key the scope board carries and the port reads under (``STAGED``): the
    same reading the scope board fixture makes of its operation.
    """
    markers = frozenset(
        split_label_key(row.spec.terminal_marker_key)[1]
        for row in stage_rows(
            operation.resolve_organize_mandates(), under_approval=True
        )
    )
    return markers or frozenset({criteria_stage_label_key(operation) or STAGED})


#: Every package function the scope selection reaches.
SCOPE_PATH = reached(SCOPE_SELECTION)

#: Derived. Each row field the scope selection reads, and where.
SCOPE_FIELD_READS = field_reads(SCOPE_PATH, TrackerIssue.model_fields)

#: Derived. The row fields the scope flow reads to decide which lanes it may
#: fire: every field read on the path that is not a rank input.
SCOPE_ELIGIBILITY_INPUTS = frozenset(SCOPE_FIELD_READS) - RANK_INPUTS

#: Every other field of a scope lane's row, derived from the model.
SCOPE_VARIED = (
    frozenset(TrackerIssue.model_fields) - RANK_INPUTS - SCOPE_ELIGIBILITY_INPUTS
)

#: Derived. Each label the scope selection tests a row's labels for, and
#: where: the criterion label and the record kinds.
SCOPE_LABEL_READS = label_reads(SCOPE_PATH, "issue_labels")

#: The stage markers the walk's operation names, which the entry
#: requirement reads off the row by the key the operation gives it.
SCOPE_STAGE_MARKERS = stage_markers(native_operation())

#: The labels the scope flow reads, by membership. Any other label on a
#: lane is a count like any other, and is varied beside these.
SCOPE_READ_LABELS = frozenset(SCOPE_LABEL_READS) | SCOPE_STAGE_MARKERS

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

#: The subtree parts that add open criteria under a heavy ready lane: its
#: own many criteria, and one under each of its many children.
SCOPE_OPEN_PARTS = ("criteria", "sub_issues")

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

#: The rank order of the board's issues, which is the order the scope flow
#: fires its lanes in.
RANK_ORDER = ("K-2", "K-6", "K-4", "K-5", "K-1", "K-3")

#: The two criteria every scope lane owes: one its first fire closes, and
#: one no fire closes. The first is the lane's own criterion, which the
#: scope board names ``<lane>/check``.
SCOPE_CHECKS = ("check", "second")

#: The largest gap any scope variation gives a lane: its own criteria and
#: ``SCOPE_MANY`` under each open part. A lane owing more is never seen
#: here, so a threshold rule above this number is not caught; two tests
#: hold that bound as a fact.
SCOPE_GAP_BOUND = len(SCOPE_CHECKS) + SCOPE_MANY * len(SCOPE_OPEN_PARTS)

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
SCOPE_VARIATIONS = variations(
    SCOPE_VARIED, SCOPE_VARIED - {SCOPE_SUBJECT_FALLBACK}, labels=True
)


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
    for name in SCOPE_VARIED:
        low, high = extremes(name)
        assert low != high, name
    assert SCOPE_READ_LABELS
    assert not SCOPE_READ_LABELS & MANY_LABELS
    assert set(READ_EDGE_KINDS) | set(UNREAD_EDGE_KINDS) == set(IssueRelationKind)
    assert not set(READ_EDGE_KINDS) & set(UNREAD_EDGE_KINDS)
    assert UNREAD_EDGE_KINDS
    assert all(reason.strip() for reason in READ_EDGE_KINDS.values())


#: A set of labels a planted reader intersects a row's labels with.
PLANTED_KINDS = frozenset({"planted-record"})


def _reads_a_text(issue: TrackerIssue) -> int:
    return len(issue.body)


def _reads_by_literal_name(issue: TrackerIssue) -> object:
    return getattr(issue, "url", None)


def _reads_by_built_name(issue: TrackerIssue, name: str) -> object:
    return getattr(issue, name)


def _reads_a_label(issue: TrackerIssue) -> bool:
    return "planted-stage" in issue.issue_labels


def _reads_label_kinds(issue: TrackerIssue) -> bool:
    return bool(issue.issue_labels & PLANTED_KINDS)


def test_the_scope_register_is_read_from_the_code() -> None:
    """The scope flow's eligibility register is what its selection reads.

    The selection's starts are the objects the flow runs: the walk reads
    the ready set through ``read_scope_ready`` and selects through its own
    ``_select``, and the tracker's approval read runs the approval reading
    and its fire-subject read the entry requirement. The path they reach
    reads the two rank inputs — the topology orders by them — and every
    field the register holds; the labels it tests for are the criterion
    label and the record kinds, by the object that names them, and the
    stage markers are the ones the walk's operation names and the base
    board's lanes carry.

    Controls for every form the readers follow: an attribute read and a
    ``getattr`` with a literal name are field reads; a literal tested for
    membership and a set intersected with the labels are label reads. A
    name built at run time is not a read, which is the stated limit.
    """
    assert set(SCOPE_SELECTION) <= set(SCOPE_PATH)
    assert read_scope_ready in map(
        unwrapped, live_references(ScopeWorkflowEngine.run).values()
    )
    assert ScopeWorkflowEngine._select in written_methods(ScopeWorkflowEngine)
    assert resolve_execution_approval in map(
        unwrapped, live_references(LinearMcpTracker._read_execution_approval).values()
    )
    assert require_fire_entry in map(
        unwrapped, live_references(LinearMcpTracker.read_fire_subject).values()
    )
    assert RANK_INPUTS <= set(SCOPE_FIELD_READS)
    assert SCOPE_ELIGIBILITY_INPUTS
    assert all(SCOPE_FIELD_READS[name] for name in SCOPE_ELIGIBILITY_INPUTS)
    assert frozenset(SCOPE_LABEL_READS) == frozenset({CRITERION_LABEL}) | RECORD_KINDS
    assert all(SCOPE_LABEL_READS.values())
    assert SCOPE_STAGE_MARKERS
    for key in LANES:
        assert scope_lanes(None).issues[key].issue_labels == SCOPE_STAGE_MARKERS, key
    planted = (
        _reads_a_text,
        _reads_by_literal_name,
        _reads_by_built_name,
        _reads_a_label,
        _reads_label_kinds,
    )
    assert field_reads(planted, TrackerIssue.model_fields) == {
        "body": frozenset({home(_reads_a_text)}),
        "issue_labels": frozenset({home(_reads_a_label), home(_reads_label_kinds)}),
        "url": frozenset({home(_reads_by_literal_name)}),
    }
    assert label_reads(planted, "issue_labels") == {
        "planted-record": frozenset({home(_reads_label_kinds)}),
        "planted-stage": frozenset({home(_reads_a_label)}),
    }


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
    variation = Variation((), SUBTREE, False, False, frozenset({heavy, closed}))
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


def passing_over_gaps_above(
    threshold: int,
) -> Callable[..., object]:
    """``_select`` with a threshold rule planted in front of it.

    A lane owing more than *threshold* criteria is passed over: the ready
    set the selector is handed is the read one less those lanes. The rule
    reads the gap alone, so what it moves, it moves by size.
    """
    original = ScopeWorkflowEngine._select

    def _select(
        self: ScopeWorkflowEngine,
        *,
        ready: ScopeReadySet,
        delivers: bool,
        rested: Sequence[str],
    ) -> object:
        trimmed = replace(
            ready,
            ready=tuple(row for row in ready.ready if len(row.gap) <= threshold),
        )
        return original(self, ready=trimmed, delivers=delivers, rested=rested)

    return _select


#: The variation whose lanes owe the most: every subtree part under the
#: first-parity lanes.
SCOPE_HEAVIEST = "everything-first"


async def test_a_threshold_above_the_gap_bound_is_unseen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stated limit, held: no lane here owes more than the bound.

    The heaviest scope board's largest gap is exactly ``SCOPE_GAP_BOUND``,
    and a rule passing over any lane owing more than that moves nothing:
    the walk decides what the base walk decides. So a threshold rule above
    the bound is not seen by these variations, which is what the Limit
    paragraph says.
    """
    variation = SCOPE_VARIATIONS[SCOPE_HEAVIEST]
    assert variation.subtree == SUBTREE and set(SCOPE_OPEN_PARTS) <= set(SUBTREE)
    port = scope_lanes(variation)
    ready = await read_scope_ready(ref=SCOPE, tracker=port)
    assert max(len(lane.gap) for lane in ready.ready) == SCOPE_GAP_BOUND
    monkeypatch.setattr(
        ScopeWorkflowEngine, "_select", passing_over_gaps_above(SCOPE_GAP_BOUND)
    )
    assert await scope_decision(port) == SCOPE_BASE_DECISION


async def test_the_same_threshold_at_the_gap_bound_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The positive control: one below the bound, the same rule is caught.

    Passing over a lane owing more than ``SCOPE_GAP_BOUND - 1`` criteria
    leaves every heavy lane of the heaviest board unfired, so the walk's
    decision is not the base decision, and the variation that holds this
    board reds on it.
    """
    variation = SCOPE_VARIATIONS[SCOPE_HEAVIEST]
    port = scope_lanes(variation)
    monkeypatch.setattr(
        ScopeWorkflowEngine, "_select", passing_over_gaps_above(SCOPE_GAP_BOUND - 1)
    )
    decided = await scope_decision(port)
    assert decided != SCOPE_BASE_DECISION
    fired, _, failures, _ = decided
    assert set(fired) == set(RANK_ORDER) - variation.heavy
    assert failures == ()
