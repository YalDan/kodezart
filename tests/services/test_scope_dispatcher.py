"""The walker as a dispatch producer: one fire per pass over a live ready set.

Nothing is stubbed between the walk and the queue.  ``ScopeDispatcher`` is
driven over the shipped ``read_scope_ready`` and the shipped
``FireDispatcher``, wired exactly as the unscoped tick wires it, so "the
lane dispatched" is observed as a job on the queue and "the lane was held"
as a claim that was never spent.
"""

import ast
import importlib
import inspect
import textwrap
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

import pytest

from kodezart.chains import scope_walker
from kodezart.domain import dispatch, issue_tree, topology
from kodezart.domain.errors import ScopeSupersessionReadError
from kodezart.services import fire_dispatcher as fire_dispatcher_module
from kodezart.services import scope_dispatcher, scope_planning
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.claim_heartbeat import ClaimHeartbeat
from kodezart.services.dispatch_pass import GatedDispatchPass
from kodezart.services.fire_context import FireContextAssembler
from kodezart.services.fire_dispatcher import FireDispatcher, LaneCooldown
from kodezart.services.lifecycle_watcher import LifecycleWatcher
from kodezart.services.pass_gate import PassGate
from kodezart.services.run_recorder import RunRecorder
from kodezart.services.scope_dispatcher import ScopeDispatcher
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain import scope_ready
from kodezart.types.domain import tracker as tracker_module
from kodezart.types.domain.branch import WorkRef, WorkRefRole
from kodezart.types.domain.dispatch import (
    DispatchOutcome,
    ExclusionClause,
    IssueExclusion,
    PassRun,
    PassSignal,
)
from kodezart.types.domain.job import JobState
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.tracker import (
    IssuePriority,
    TrackerIssue,
    WorkflowStateKind,
)
from tests.fakes import (
    FIXTURE_EPOCH,
    FakeDeliveryProbe,
    FakeGitService,
    FakeJobQueue,
    FakeRepoCache,
    FakeTrackerPort,
    PassThroughGate,
    make_tracker_issue,
)
from tests.services.test_dispatch_pass import (
    ASSET_FETCH_TIMEOUT_SECONDS,
    ASSET_MAX_BYTES,
    ASSET_MAX_COUNT,
    HOLDER,
    INTEGRATION_DIR,
    LANE,
    LEASE_SECONDS,
    PAGE_SIZE,
    PRIMARY_REPO,
    RATE_LIMIT_COOLDOWN_SECONDS,
    REMOTE,
    RENEWAL_FRACTION,
    TICK_STARTED_AT,
    TRUNK,
    fire_dispatcher,
    operation_config,
)

PROJECT = ScopeRef(kind=ScopeKind.PROJECT, key="fixture-project")
#: A container the walk was not pointed at; its issues are outside the
#: filter and reachable only through an in-filter ancestor's subtree.
OUTSIDE_PROJECT = "outside-project"
CRITERION = frozenset({"criterion"})
#: The workflow name each criterion state is spelled with on this board.
STATE_NAMES = {
    WorkflowStateKind.UNSTARTED: "Todo",
    WorkflowStateKind.COMPLETED: "Done",
    WorkflowStateKind.CANCELED: "Canceled",
}
#: The pushed head of every lane branch the fake remote carries.
DELIVERED_SHA = "a" * 40


def lane_issue(
    key: str,
    *,
    priority: IssuePriority = IssuePriority.NONE,
    blocked_by: tuple[str, ...] = (),
    state_kind: WorkflowStateKind = WorkflowStateKind.UNSTARTED,
    state_name: str = "Todo",
    parent_key: str | None = None,
    created_at: datetime = FIXTURE_EPOCH,
    project_id: str = PROJECT.key,
):
    """A deliverable the walk may select: approved, in *project_id*."""
    return make_tracker_issue(
        key,
        priority=priority,
        blocked_by=blocked_by,
        state_kind=state_kind,
        state_name=state_name,
        parent_key=parent_key,
        created_at=created_at,
        project_id=project_id,
    )


def criterion(
    key: str,
    *,
    parent: str,
    met: bool = False,
    state_kind: WorkflowStateKind | None = None,
    project_id: str = PROJECT.key,
):
    """A criterion sub-issue — never a scan candidate, always a gap member."""
    kind = (
        (WorkflowStateKind.COMPLETED if met else WorkflowStateKind.UNSTARTED)
        if state_kind is None
        else state_kind
    )
    return make_tracker_issue(
        key,
        parent_key=parent,
        issue_labels=CRITERION,
        queue_states=(),
        state_kind=kind,
        state_name=STATE_NAMES[kind],
        project_id=project_id,
    )


def board(*issues, approved=(PROJECT,), **kwargs):
    """A tracker whose project holds *issues*, the deliverables its members."""
    return FakeTrackerPort(
        issues=issues,
        scope_containers=[
            ScopeContainer(
                ref=PROJECT,
                name="fixture project",
                description="",
                url="https://tracker.invalid/p",
            ),
        ],
        scope_memberships={
            PROJECT: [
                issue.issue_key
                for issue in issues
                if "criterion" not in issue.issue_labels
                and issue.project_id == PROJECT.key
            ],
        },
        scope_label_members={ref: frozenset({ScopeLabel.APPROVED}) for ref in approved},
        **kwargs,
    )


def reach_board(*, child_state: WorkflowStateKind, out_of_filter: bool):
    """A lane whose own criteria are graded, parenting one child deliverable.

    The lane owes whatever its subtree owes.  With *out_of_filter* the child
    sits in another project, so the scope's own filter never carries it and
    the walk can reach it only through the lane it hangs under.
    """
    child_project = OUTSIDE_PROJECT if out_of_filter else PROJECT.key
    return board(
        lane_issue("lane"),
        criterion("lane-check", parent="lane", met=True),
        lane_issue(
            "child",
            parent_key="lane",
            priority=IssuePriority.HIGH,
            project_id=child_project,
        ),
        criterion(
            "child-check",
            parent="child",
            state_kind=child_state,
            project_id=child_project,
        ),
    )


def remote_of(tracker):
    """A fake remote carrying one lane branch per issue on the board."""
    return FakeGitService(
        remote_branch_shas={branch_of(key): DELIVERED_SHA for key in tracker.issues},
    )


def branch_of(issue_key):
    return f"lane/{issue_key}"


def walk(tracker, *, delivered=(), git=None, ref=PROJECT):
    """The shipped walker over the shipped dispatcher's own wiring."""
    queue = FakeJobQueue()
    probe = FakeDeliveryProbe(delivered=delivered)
    dispatcher = FireDispatcher(
        tracker=tracker,
        queue=queue,
        registry=queue,
        delivery=probe,
        operation=operation_config(),
        repo_url=PRIMARY_REPO,
        lane=LANE,
        holder=HOLDER,
        claim_lease_seconds=LEASE_SECONDS,
        query_page_size=PAGE_SIZE,
        cooldown=LaneCooldown(cooldown_seconds=RATE_LIMIT_COOLDOWN_SECONDS),
        assembler=FireContextAssembler(
            tracker=tracker,
            gate=PassThroughGate(),
            max_count=ASSET_MAX_COUNT,
            max_bytes=ASSET_MAX_BYTES,
            fetch_timeout_seconds=ASSET_FETCH_TIMEOUT_SECONDS,
        ),
        resolver=BaseResolver(
            tracker=tracker,
            git=remote_of(tracker) if git is None else git,
            remote=REMOTE,
        ),
        cache=FakeRepoCache(),
        trunk=TRUNK,
        integration_workspace_dir=INTEGRATION_DIR,
    )
    walker = ScopeDispatcher(ref=ref, tracker=tracker, dispatcher=dispatcher)
    return walker, queue, probe


def close(tracker, criterion_key):
    """Grade a criterion, exactly as a graded fire's write-back leaves it."""
    tracker.issues[criterion_key] = tracker.issues[criterion_key].model_copy(
        update={"state_kind": WorkflowStateKind.COMPLETED, "state_name": "Done"},
    )


def deliver(tracker, issue_key):
    """Record the branch a finished fire pushed, as its writer would."""
    tracker.recorded_work_refs[issue_key] = [
        WorkRef(
            issue_id=issue_key,
            role=WorkRefRole.DELIVERABLE,
            branch=branch_of(issue_key),
            pushed_head_sha=DELIVERED_SHA,
            recorded_at=FIXTURE_EPOCH,
        ),
    ]


def finish(tracker, queue, report):
    """Release what a finished fire released: the claim and the job."""
    tracker.claims.pop(report.claimed_issue_key)
    queue.mark(report.job_id, JobState.TERMINAL)


def enqueued(queue):
    return [request.issue_key for _, request in queue.submissions]


def chain():
    """A <- B <- C: each lane's premise is the one before it."""
    return board(
        lane_issue("A"),
        criterion("A-check", parent="A"),
        lane_issue("B", blocked_by=("A",)),
        criterion("B-check", parent="B"),
        lane_issue("C", blocked_by=("B",)),
        criterion("C-check", parent="C"),
    )


async def walk_chain(tracker, walker, queue, probe, *, open_prs=False):
    """Tick until the scope is at rest, finishing each lane the walk launches.

    Exactly what a graded fire leaves behind: the criterion closed, the
    branch recorded as the lane's deliverable ref, the claim released and
    the job terminal.  With *open_prs* the delivery is opened too and never
    merged, which is the steady state of every finished lane on the
    founder's boards.
    """
    reports = []
    for _ in range(len(tracker.scope_memberships[PROJECT]) + 1):
        report = await walker.run_pass()
        reports.append(report)
        if report.claimed_issue_key is None:
            break
        close(tracker, f"{report.claimed_issue_key}-check")
        deliver(tracker, report.claimed_issue_key)
        if open_prs:
            probe.delivered.add(report.claimed_issue_key)
        finish(tracker, queue, report)
    return reports


async def test_a_blocked_lane_is_held_across_ticks_until_its_blockers_subtree_closes():
    """The lane waits on the blocker's CRITERIA, not on the blocker's state."""
    tracker = board(
        lane_issue("blocker"),
        criterion("blocker-check", parent="blocker"),
        lane_issue("lane", blocked_by=("blocker",)),
        criterion("lane-check", parent="lane"),
    )
    walker, queue, _ = walk(tracker)

    first = await walker.run_pass()

    assert enqueued(queue) == ["blocker"]
    assert (
        IssueExclusion(
            issue_key="lane",
            clause=ExclusionClause.LIVE_BLOCKER,
            detail="blocker",
        )
        in first.exclusions
    )

    close(tracker, "blocker-check")
    deliver(tracker, "blocker")
    finish(tracker, queue, first)
    second = await walker.run_pass()

    assert enqueued(queue) == ["blocker", "lane"]
    assert second.claimed_issue_key == "lane"
    assert second.criterion_keys == ("lane-check",)


async def test_a_done_blocker_parent_with_an_open_criterion_still_blocks_its_lane():
    """A Done deliverable with an open criterion is a blocker that stands."""
    tracker = board(
        lane_issue("blocker"),
        criterion("blocker-check", parent="blocker"),
        lane_issue("lane", blocked_by=("blocker",)),
        criterion("lane-check", parent="lane"),
    )
    walker, queue, _ = walk(tracker)

    first = await walker.run_pass()
    assert enqueued(queue) == ["blocker"]

    tracker.issues["blocker"] = tracker.issues["blocker"].model_copy(
        update={"state_kind": WorkflowStateKind.COMPLETED, "state_name": "Done"},
    )
    deliver(tracker, "blocker")
    finish(tracker, queue, first)
    second = await walker.run_pass()

    assert enqueued(queue) == ["blocker", "blocker"]
    assert "lane" not in tracker.claims
    assert (
        IssueExclusion(
            issue_key="lane",
            clause=ExclusionClause.LIVE_BLOCKER,
            detail="blocker",
        )
        in second.exclusions
    )


async def test_the_gated_pass_runs_the_scope_dispatcher_and_follows_the_fire():
    """The tick composes with the walker exactly as it does with the scan."""
    tracker = board(
        lane_issue("lane"),
        criterion("lane-check", parent="lane"),
    )
    walker, queue, _ = walk(tracker)
    lifecycle = LifecycleWatcher(
        recorder=RunRecorder(records={}, sinks={}),
        queue=queue,
        registry=queue,
        writer=TrackerLifecycleWriter(
            marker_prefixes={"run_outcome": "fixture-outcome"},
            surface_lease_seconds=900,
            tracker=tracker,
            gate=PassThroughGate(),
        ),
        heartbeat=ClaimHeartbeat(
            tracker=tracker,
            holder=HOLDER,
            lease_seconds=LEASE_SECONDS,
            renewal_fraction=RENEWAL_FRACTION,
        ),
        report=walker.record_run_outcome,
    )
    pass_ = GatedDispatchPass(
        lifecycle=lifecycle,
        gate=PassGate(
            tracker=tracker,
            ledger=tracker.self_writes,
            signals=[PassSignal.approved_changed],
            team_keys=operation_config().team_keys_for_repo(PRIMARY_REPO),
            repo_urls=[PRIMARY_REPO],
            page_size=PAGE_SIZE,
        ),
        dispatcher=walker,
    )

    assert await pass_.run(TICK_STARTED_AT) is PassRun.RAN

    assert enqueued(queue) == ["lane"]
    assert tracker.claims["lane"].holder == HOLDER
    assert len(lifecycle.following) == 1


async def test_a_scope_with_no_ready_lane_enqueues_nothing_and_reports_empty():
    """Every criterion met is a scope at rest, not a pass with nothing to say."""
    tracker = board(
        lane_issue("lane"),
        criterion("lane-check", parent="lane", met=True),
        lane_issue("other"),
        criterion("other-check", parent="other", met=True),
    )
    walker, queue, _ = walk(tracker)

    report = await walker.run_pass()

    assert report.outcome is DispatchOutcome.empty_eligible_set
    assert report.eligible == ()
    assert queue.submissions == []
    assert tracker.claims == {}


async def test_ready_selection_makes_no_writes_before_the_claim():
    """The walk is a read; the claim is the first mark it leaves anywhere."""
    tracker = board(
        lane_issue("lane"),
        criterion("lane-check", parent="lane"),
        lane_issue("held", blocked_by=("lane",)),
        criterion("held-check", parent="held"),
    )
    walker, _, _ = walk(tracker)

    await walker.run_pass()

    assert tracker.issue_writes == []
    assert tracker.comment_writes == []
    assert tracker.queue_writes == []
    assert tracker.lease_writes == []
    assert tracker.claim_writes == ["lane"]


async def test_a_full_walk_reads_no_pull_request_merge_state():
    """The whole graph walks with the merge-state boundary standing idle.

    The forge the walk is handed answers merge state as readily as it
    answers deliveries — it is one object, as the native client is — so the
    empty call list is a fact about the walk and not about a double nothing
    could have reached.
    """
    tracker = chain()
    walker, queue, probe = walk(tracker)

    reports = await walk_chain(tracker, walker, queue, probe)

    assert [report.claimed_issue_key for report in reports] == ["A", "B", "C", None]
    assert enqueued(queue) == ["A", "B", "C"]
    assert set(probe.calls) == {"A", "B", "C"}
    assert probe.merge_state.calls == []
    assert reports[-1].outcome is DispatchOutcome.empty_eligible_set


def walk_import_closure() -> tuple[str, ...]:
    """Every kodezart module the walk can reach, computed from its imports.

    A hand-written module list is exactly the narrowness this criterion is
    about: it names what the author remembered.  This follows the import
    edges out of the producer instead, so a merge-state read added anywhere
    the walk can reach is in scope of the assertion below.
    """
    seen: set[str] = set()
    pending = [scope_dispatcher.__name__]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        tree = ast.parse(inspect.getsource(importlib.import_module(name)))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                if node.module.startswith("kodezart"):
                    pending.append(node.module)
            if isinstance(node, ast.Import):
                pending.extend(
                    alias.name
                    for alias in node.names
                    if alias.name.startswith("kodezart")
                )
    return tuple(sorted(seen))


def test_no_module_the_walk_can_reach_holds_a_merge_state_call_site():
    """The whole reachable closure, computed: nobody on it asks a PR anything."""
    closure = walk_import_closure()

    assert scope_walker.__name__ in closure
    assert "kodezart.services.fire_dispatcher" in closure
    assert "kodezart.services.audit_terminal" not in closure
    for name in closure:
        tree = ast.parse(inspect.getsource(importlib.import_module(name)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr != "read_pr_state", name
            if isinstance(node, ast.ImportFrom):
                held = {alias.name for alias in node.names}
                assert held & {"PRStateReader", "PRLifecycle"} == set(), name


def test_ready_set_and_walker_modules_hold_no_merge_state_call_site():
    """No module on the walk can ask a pull request anything, statically."""
    modules = [scope_walker, topology, issue_tree, scope_dispatcher]
    forbidden_names = {"PRStateReader", "PRState", "PRLifecycle"}
    for module in modules:
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "kodezart.types.domain.pr_state"
                assert {alias.name for alias in node.names} & forbidden_names == set()
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"read_pr_state", "lifecycle", "merged"}

    imported = {
        node.module
        for node in ast.walk(ast.parse(inspect.getsource(scope_dispatcher)))
        if isinstance(node, ast.ImportFrom)
    }
    assert imported == {
        "kodezart.chains.scope_walker",
        "kodezart.core.logging",
        "kodezart.core.protocols",
        "kodezart.services.fire_dispatcher",
        "kodezart.types.domain.dispatch",
        "kodezart.types.domain.run_records",
        "kodezart.types.domain.scope",
    }


OUTCOME_MODULE = "kodezart.types.domain.outcome"


def enum_bound_names(tree):
    """Every local name the parsed module binds to the fire-outcome enum.

    The enum's own spelling always counts, and so does whatever an import
    renamed it to here: a clause that imported it ``as Outcome`` reads a
    fire outcome exactly as loudly as one that did not.
    """
    bound = {WorkflowOutcome.__name__}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != OUTCOME_MODULE:
            continue
        bound |= {
            alias.asname or alias.name
            for alias in node.names
            if alias.name == WorkflowOutcome.__name__
        }
    return bound


def names_the_enum(node, bound):
    """Whether the parsed attribute base is one of the enum's local names."""
    if isinstance(node, ast.Name):
        return node.id in bound
    if isinstance(node, ast.Attribute):
        return node.attr in bound
    return False


def names_an_enum(node):
    """Whether the parsed class base names an enumeration."""
    if isinstance(node, ast.Name):
        return node.id.endswith(Enum.__name__)
    if isinstance(node, ast.Attribute):
        return node.attr.endswith(Enum.__name__)
    return False


def foreign_enum_members(tree):
    """Every string another enum's own member table binds, by node identity.

    An enum spells its own values, and two of them may spell the same
    thing: ``RunEventKind`` calls the moment a pull request opened
    ``pr_opened``, and so does ``WorkflowOutcome``.  A class DEFINING its
    own vocabulary is not a predicate READING the fire outcome, and
    counting it as one would make every verdict this scan returns noise.
    The fire-outcome enum's own table is never exempted: a predicate that
    reached it would be reading exactly what the Check forbids.
    """
    bound = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name == WorkflowOutcome.__name__:
            continue
        if not any(names_an_enum(base) for base in node.bases):
            continue
        bound |= {
            id(statement.value)
            for statement in node.body
            if isinstance(statement, ast.Assign)
            and isinstance(statement.value, ast.Constant)
        }
    return bound


def outcome_references(source: str) -> frozenset[str]:
    """Every fire-level outcome the parsed *source* names, however it names it.

    The vocabulary is read off the enum rather than listed, so a member
    appended later is covered without this helper being touched — a
    hand-written list would only forbid what its author happened to know
    about — and each way of naming one is recognised: the import, the
    enum's own name, a member read off it, and the wire string itself,
    because a clause comparing ``report.outcome == "scope_converged"``
    reads a fire outcome just as surely as one that imported the enum.

    What the vocabulary alone cannot do is tell whose member it is.  A
    member is counted only when it is read off the fire-outcome enum — off
    its own name or off whatever an import renamed it to — so a member of
    another enum that happens to share a spelling is not reported as a
    fire-outcome read, and neither is the line where that other enum binds
    it.  ``RunEventKind`` spells the moment a pull request opened
    ``pr_opened`` and so does ``WorkflowOutcome``; the predicate may read
    the event vocabulary all it likes.  A bare name counts only when it was
    imported from the outcome module, and a bare string when it is a
    member's wire value, which is the one form that carries no base to
    resolve.
    """
    tree = ast.parse(textwrap.dedent(source))
    members = {member.name for member in WorkflowOutcome}
    values = {member.value for member in WorkflowOutcome}
    bound = enum_bound_names(tree)
    defined = foreign_enum_members(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == OUTCOME_MODULE:
            found |= {alias.name for alias in node.names} & (
                members | {WorkflowOutcome.__name__}
            )
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in bound:
                found.add(WorkflowOutcome.__name__)
        if isinstance(node, ast.Attribute) and node.attr in members | values:
            if names_the_enum(node.value, bound):
                found.add(node.attr)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in values and id(node) not in defined:
                found.add(node.value)
    return frozenset(found)


PREDICATE_PACKAGES = ("services", "chains", "domain", "types/domain")

SRC = Path(__file__).resolve().parents[2] / "src" / "kodezart"


def predicate_source_tree():
    """Every module the decision could reach, by its path relative to the root."""
    return {
        path.relative_to(SRC).as_posix(): path.read_text(encoding="utf-8")
        for package in PREDICATE_PACKAGES
        for path in sorted((SRC / package).rglob("*.py"))
    }


def module_relative(module):
    """The supplied module's path, spelled the way the source map keys it."""
    return Path(module.__file__).resolve().relative_to(SRC).as_posix()


def imported_relatives(tree, sources):
    """Every supplied module the parsed module imports, by relative path."""
    found = set()
    for node in ast.walk(tree):
        dotted = []
        if isinstance(node, ast.Import):
            dotted.extend(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            dotted.append(node.module)
            dotted.extend(f"{node.module}.{alias.name}" for alias in node.names)
        for name in dotted:
            if not name.startswith("kodezart."):
                continue
            relative = name[len("kodezart.") :].replace(".", "/") + ".py"
            if relative in sources:
                found.add(relative)
    return found


def reachable_modules(sources, *, start):
    """Every supplied module reachable from *start* by following its imports."""
    found = set()
    frontier = [start]
    while frontier:
        relative = frontier.pop()
        if relative in found or relative not in sources:
            continue
        found.add(relative)
        frontier.extend(imported_relatives(ast.parse(sources[relative]), sources))
    return found


def defined_units(sources, modules):
    """Every function, method and class the named modules define, by its name."""
    units = {}
    for relative in sorted(modules):
        for node in ast.walk(ast.parse(sources[relative])):
            if not isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ):
                continue
            units.setdefault(node.name, []).append((f"{relative}::{node.name}", node))
    return units


def referenced_names(node):
    """Every name and attribute the parsed unit reads, by the spelling it reads.

    Calls are not the only way a unit reaches code: a property is read as a
    plain attribute, a class is named in an annotation, a helper is passed
    by name to be called elsewhere.  Every load counts, so every one of
    those is resolved.
    """
    found = set()
    for read in ast.walk(node):
        if isinstance(read, ast.Name) and isinstance(read.ctx, ast.Load):
            found.add(read.id)
        if isinstance(read, ast.Attribute):
            found.add(read.attr)
    return found


def dispatchability_predicate_sources(sources):
    """The source of everything that decides whether a lane is dispatchable.

    Derived from the supplied code rather than listed by hand: start at the
    one pass method that turns a ready set into a launch, resolve every
    name and attribute it reads against the functions, methods and classes
    defined by the modules its own imports reach, and repeat until nothing
    new is found.  Resolution is by name and covers reads rather than
    calls alone, so the walk over-reaches — a distinct unit that merely
    shares a name with something read is scanned too — and that is the
    safe direction: the verdict is about what the predicate CANNOT touch,
    so scanning too much can only make the guard stricter, while missing a
    property read off a type the predicate receives would let the very
    read the Check forbids pass green.
    """
    entry = module_relative(scope_dispatcher)
    units = defined_units(sources, reachable_modules(sources, start=entry))
    scanned = {}
    frontier = [
        unit
        for unit in units[ScopeDispatcher.run_pass.__name__]
        if unit[0] == f"{entry}::{ScopeDispatcher.run_pass.__name__}"
    ]
    while frontier:
        label, node = frontier.pop()
        if label in scanned:
            continue
        scanned[label] = ast.unparse(node)
        for name in referenced_names(node):
            frontier.extend(
                unit for unit in units.get(name, ()) if unit[0] not in scanned
            )
    return tuple(sorted(scanned.items()))


def units_reading_a_fire_outcome(sources):
    """The units of the derived predicate that name a fire outcome at all."""
    return {
        label: outcome_references(source)
        for label, source in dispatchability_predicate_sources(sources)
        if outcome_references(source)
    }


class UnitBodyPlanted(ast.NodeTransformer):
    """Puts the supplied statements at the head of the named unit's body."""

    def __init__(self, *, unit, statements):
        self._unit = unit
        self._statements = statements

    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        if node.name == self._unit:
            node.body = [*self._statements, *node.body]
        return node

    def visit_AsyncFunctionDef(self, node):
        return self.visit_FunctionDef(node)


class ClassBodyPlanted(ast.NodeTransformer):
    """Puts the supplied members at the foot of the named class's body."""

    def __init__(self, *, name, statements):
        self._name = name
        self._statements = statements

    def visit_ClassDef(self, node):
        self.generic_visit(node)
        if node.name == self._name:
            node.body = [*node.body, *self._statements]
        return node


def planted_in_unit(source, *, unit, statements):
    """*source* with *statements* planted at the head of the named unit.

    Planted through the tree rather than into the text: a positive control
    anchored on two verbatim lines stops proving anything the moment the
    unit it aims at is reformatted, and stops loudly only if the anchor is
    also asserted.  The unit is named, so the plant lands wherever it moved
    to.
    """
    return ast.unparse(
        UnitBodyPlanted(
            unit=unit, statements=ast.parse(textwrap.dedent(statements)).body
        ).visit(ast.parse(source))
    )


def planted_in_class(source, *, name, statements):
    """*source* with *statements* planted at the foot of the named class."""
    return ast.unparse(
        ClassBodyPlanted(
            name=name, statements=ast.parse(textwrap.dedent(statements)).body
        ).visit(ast.parse(source))
    )


def test_the_detector_flags_a_dispatch_decision_that_reads_a_fire_outcome():
    """The positive control: the same scan over a predicate that does read one."""
    member = next(iter(WorkflowOutcome))
    reading_enum = f"""
        from kodezart.types.domain.outcome import WorkflowOutcome

        def dispatchable(lane):
            return lane.last_outcome is not WorkflowOutcome.{member.name}
    """
    reading_wire_string = f"""
        def dispatchable(lane):
            return lane.last_outcome != "{member.value}"
    """

    reading_the_gap = """
        def dispatchable(lane):
            return not lane.gap and not lane.blocker_keys
    """

    assert outcome_references(reading_enum) >= {
        WorkflowOutcome.__name__,
        member.name,
    }
    assert outcome_references(reading_wire_string) >= {member.value}
    assert outcome_references(reading_the_gap) == frozenset()


def test_the_detector_reads_no_fire_outcome_in_another_enum_of_the_same_spelling():
    """A shared spelling is not a shared meaning, and the scan says so.

    ``RunEventKind`` and ``WorkflowOutcome`` both spell the moment a pull
    request opened ``pr_opened``: one as an event this run emitted, the
    other as the disposition that run ended in.  A detector matching the
    spelling alone reports the event vocabulary — which the predicate is
    entitled to read — as a forbidden outcome read, and a guard that is
    red over code that is fine is a guard nobody can act on.  Neither the
    other enum's member table nor a member read off the other enum's own
    name is a fire-outcome read here.
    """
    member = next(iter(WorkflowOutcome))
    shared = {kind.value for kind in RunEventKind} & {
        outcome.value for outcome in WorkflowOutcome
    }
    assert shared

    another_enums_table = Path(RunEventKind.__module__.replace(".", "/") + ".py")
    reading_another_enum = f"""
        from kodezart.types.domain.run_event import RunEventKind

        def dispatchable(lane):
            return lane.last_event is not RunEventKind.{RunEventKind.PR_OPENED.name}
    """
    reading_a_lookalike_attribute = f"""
        def dispatchable(lane):
            return lane.last_event is not RunEventKind.{member.name}
    """

    assert outcome_references((SRC.parent / another_enums_table).read_text()) == (
        frozenset()
    )
    assert outcome_references(reading_another_enum) == frozenset()
    assert outcome_references(reading_a_lookalike_attribute) == frozenset()


def test_no_fire_outcome_is_read_anywhere_the_dispatch_decision_is_made():
    """A lane's admissibility is decided without any fire's ending being read.

    Whether a lane is dispatchable follows from its gap and its blockers and
    from nothing else, so a run that ended ``loop_not_accepted`` can never be
    read as a lane finished, and one that ended ``shutdown_abandoned`` can
    never be read as a lane abandoned: the arithmetic has no access to either
    fact in the first place.  The scanned surface is derived from the pass
    itself, so the clauses it reaches — the standing exclusions, the launch,
    the plan read, the blocker edge — are covered as surely as the ready-set
    arithmetic is, and so is everything they in turn read.
    """
    sources = predicate_source_tree()
    scanned = dispatchability_predicate_sources(sources)
    labels = {label for label, _ in scanned}
    clauses = module_relative(fire_dispatcher_module)

    assert {
        f"{module_relative(issue_tree)}::SubtreeClosure",
        f"{module_relative(topology)}::plan_topology",
        f"{module_relative(scope_dispatcher)}::run_pass",
        f"{clauses}::standing_exclusion",
        f"{clauses}::launch",
        f"{clauses}::_team_exclusion",
        f"{clauses}::_route_exclusion",
        f"{clauses}::_memory_exclusion",
        f"{clauses}::_backoff_exclusion",
        f"{clauses}::_in_flight_exclusion",
        f"{clauses}::_delivery_exclusion",
        f"{module_relative(scope_planning)}::read_scope_plan",
        f"{module_relative(dispatch)}::blocker_keys",
        f"{module_relative(tracker_module)}::TrackerIssue",
    } <= labels
    for module in (scope_ready, scope_walker):
        assert {
            f"{module_relative(module)}::{node.name}"
            for node in ast.walk(ast.parse(sources[module_relative(module)]))
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        } <= labels
    for label, source in scanned:
        assert outcome_references(source) == frozenset(), label


def test_the_guard_reddens_when_a_reached_exclusion_clause_reads_a_fire_outcome():
    """The derived scan is only worth its verdict if a plant inside it goes red.

    The plant sits in a clause the pass reaches rather than in the pass
    itself — the exact place a hand-listed scan would have missed — and the
    scan is run over the planted sources, so what is proved is that the
    derivation reaches that far.
    """
    member = next(iter(WorkflowOutcome))
    relative = module_relative(fire_dispatcher_module)
    sources = predicate_source_tree()
    assert units_reading_a_fire_outcome(sources) == {}

    sources[relative] = planted_in_unit(
        sources[relative],
        unit="standing_exclusion",
        statements=f"""
            if issue.last_outcome is WorkflowOutcome.{member.name}:
                return None
        """,
    )

    reading = units_reading_a_fire_outcome(sources)
    assert reading[f"{relative}::standing_exclusion"] == frozenset(
        {WorkflowOutcome.__name__, member.name}
    )


def test_the_guard_reddens_when_a_property_the_clauses_read_reads_a_fire_outcome():
    """The escape a call-following scan leaves open, closed and proved closed.

    Nothing is called here: a property is planted on the very type the
    clauses receive, it compares the issue's workflow state against a fire
    outcome's wire value, and a clause reads it as a plain attribute.  A
    derivation that followed call targets alone would never leave
    ``_memory_exclusion`` for the property, and the forbidden read would
    sit in the predicate's reach with the guard still green.
    """
    outcome = WorkflowOutcome.scope_converged
    clauses = module_relative(fire_dispatcher_module)
    types = module_relative(tracker_module)
    sources = predicate_source_tree()
    assert units_reading_a_fire_outcome(sources) == {}

    sources[types] = planted_in_class(
        sources[types],
        name=TrackerIssue.__name__,
        statements=f"""
            @property
            def finished(self) -> bool:
                return self.state_name == "{outcome.value}"
        """,
    )
    sources[clauses] = planted_in_unit(
        sources[clauses],
        unit="_memory_exclusion",
        statements="""
            if issue.finished:
                return None
        """,
    )

    assert units_reading_a_fire_outcome(sources) == {
        f"{types}::{TrackerIssue.__name__}": frozenset({outcome.value}),
        f"{types}::finished": frozenset({outcome.value}),
    }


def re_entry_board():
    """Two independent lanes; the higher-priority one crashed mid-run."""
    return board(
        lane_issue("crashed", priority=IssuePriority.HIGH),
        criterion("crashed-check", parent="crashed"),
        lane_issue("fresh", priority=IssuePriority.LOW),
        criterion("fresh-check", parent="fresh"),
    )


async def test_a_lane_with_an_open_pull_request_is_excluded_as_delivered_in_review():
    """A lane whose delivery is open is in review, not waiting to be re-fired."""
    tracker = re_entry_board()
    walker, queue, probe = walk(tracker, delivered=("crashed",))

    report = await walker.run_pass()

    assert enqueued(queue) == ["fresh"]
    assert (
        IssueExclusion(issue_key="crashed", clause=ExclusionClause.OPEN_DELIVERY)
        in report.exclusions
    )
    assert "crashed" in probe.calls
    assert "crashed" not in tracker.claims


async def test_a_lane_with_a_live_run_is_excluded_as_in_flight():
    """A released claim is not the whole answer: the run itself is still live."""
    tracker = re_entry_board()
    walker, queue, _ = walk(tracker)

    first = await walker.run_pass()
    assert first.claimed_issue_key == "crashed"

    queue.mark(first.job_id, JobState.RUNNING)
    tracker.claims.pop("crashed")
    second = await walker.run_pass()

    assert enqueued(queue) == ["crashed", "fresh"]
    assert (
        IssueExclusion(
            issue_key="crashed",
            clause=ExclusionClause.CLAIMED_OR_IN_FLIGHT,
        )
        in second.exclusions
    )


async def test_a_lane_with_no_pull_request_and_no_live_run_is_eligible():
    """The crashed lane comes straight back: nothing about it is outstanding."""
    tracker = re_entry_board()
    walker, queue, _ = walk(tracker)

    first = await walker.run_pass()
    finish(tracker, queue, first)
    second = await walker.run_pass()

    assert enqueued(queue) == ["crashed", "crashed"]
    assert second.claimed_issue_key == "crashed"
    assert second.criterion_keys == ("crashed-check",)


async def test_re_entry_eligibility_reads_no_merge_state_and_no_body(monkeypatch):
    """The excluded lane is decided over facts, never assembled or measured.

    The hook stands over every issue the pass touches rather than the
    excluded one alone, and comes off the moment a fire launches: what a
    fire reads once it owns a lane is the fire's business, what re-entry
    eligibility reads is this one's.  The forge is asked about both lanes'
    deliveries and about neither lane's merge state.
    """
    tracker = re_entry_board()
    walker, queue, probe = walk(tracker, delivered=("crashed",))
    body_reads = []
    original = TrackerIssue.__getattribute__

    def checked(issue, name):
        if name == "body":
            body_reads.append(original(issue, "issue_key"))
            raise AssertionError("a description was read to decide eligibility")
        return original(issue, name)

    launch = FireDispatcher.launch

    async def restoring(self, *args, **kwargs):
        monkeypatch.setattr(TrackerIssue, "__getattribute__", original)
        return await launch(self, *args, **kwargs)

    monkeypatch.setattr(FireDispatcher, "launch", restoring)
    monkeypatch.setattr(TrackerIssue, "__getattribute__", checked)
    report = await walker.run_pass()

    assert body_reads == []
    assert probe.calls == ["crashed", "fresh"]
    assert probe.merge_state.calls == []
    assert report.claimed_issue_key == "fresh"
    assert enqueued(queue) == ["fresh"]


async def test_a_scope_of_open_unmerged_pull_requests_walks_to_completion():
    """Every lane's delivery opened and never merged; the graph still finishes."""
    tracker = chain()
    walker, queue, probe = walk(tracker)

    reports = await walk_chain(tracker, walker, queue, probe, open_prs=True)

    assert [report.claimed_issue_key for report in reports] == ["A", "B", "C", None]
    assert [report.base.base_branch for report in reports[:3]] == [
        TRUNK,
        branch_of("A"),
        branch_of("B"),
    ]
    assert probe.delivered == {"A", "B", "C"}
    assert reports[-1].outcome is DispatchOutcome.empty_eligible_set
    assert reports[-1].exclusions == ()
    assert tracker.claims == {}


async def test_a_blocker_done_with_no_pull_request_unlocks_on_the_same_tick():
    """A premise with no delivery at all unlocks its dependent identically."""
    tracker = chain()
    walker, queue, probe = walk(tracker)
    without_deliveries = await walk_chain(tracker, walker, queue, probe)

    open_tracker = chain()
    open_walker, open_queue, open_probe = walk(open_tracker)
    with_deliveries = await walk_chain(
        open_tracker,
        open_walker,
        open_queue,
        open_probe,
        open_prs=True,
    )

    assert [report.claimed_issue_key for report in without_deliveries] == [
        "A",
        "B",
        "C",
        None,
    ]
    assert probe.delivered == set()
    assert [report.base.base_branch for report in without_deliveries[:3]] == [
        TRUNK,
        branch_of("A"),
        branch_of("B"),
    ]
    assert len(without_deliveries) == len(with_deliveries)


async def test_an_open_criterion_holds_a_dependent_whatever_its_refs_say():
    """A recorded deliverable ref is not a closed subtree and never unlocks."""
    tracker = chain()
    deliver(tracker, "A")
    walker, queue, _ = walk(tracker)

    report = await walker.run_pass()

    assert enqueued(queue) == ["A"]
    assert (
        IssueExclusion(
            issue_key="B",
            clause=ExclusionClause.LIVE_BLOCKER,
            detail="A",
        )
        in report.exclusions
    )


async def test_a_one_issue_scope_dispatches_exactly_as_the_unscoped_pass_does():
    """A scope of one degenerates onto the authored fire path, field for field."""
    single = (
        lane_issue("K-1"),
        criterion("K-1-check", parent="K-1"),
    )
    unscoped_tracker = board(*single)
    unscoped_queue = FakeJobQueue()
    scoped_tracker = board(*single)

    unscoped = await fire_dispatcher(unscoped_tracker, unscoped_queue).run_pass()
    walker, scoped_queue, _ = walk(
        scoped_tracker,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key="K-1"),
    )
    scoped = await walker.run_pass()

    assert unscoped.outcome is DispatchOutcome.fire_enqueued
    assert scoped.outcome is DispatchOutcome.fire_enqueued
    assert unscoped_queue.submissions == scoped_queue.submissions
    assert [request.scope for _, request in scoped_queue.submissions] == [None]
    assert unscoped_tracker.recorded_base_specs == scoped_tracker.recorded_base_specs
    assert unscoped_tracker.claims["K-1"].holder == scoped_tracker.claims["K-1"].holder
    assert unscoped.claimed_state_name == scoped.claimed_state_name
    assert unscoped.claimed_visibility == scoped.claimed_visibility
    assert unscoped.base == scoped.base
    assert unscoped.criterion_keys == ()
    assert scoped.criterion_keys == ("K-1-check",)


def two_roots(approved=(PROJECT,)):
    """Two unrelated roots in one container; neither is the other's parent."""
    return board(
        lane_issue("alpha", priority=IssuePriority.LOW),
        criterion("alpha-check", parent="alpha"),
        lane_issue(
            "beta",
            priority=IssuePriority.HIGH,
            created_at=FIXTURE_EPOCH + timedelta(days=1),
        ),
        criterion("beta-check", parent="beta"),
        approved=approved,
    )


async def test_a_container_of_two_root_members_resolves_the_dispatch_target_by_key():
    """Two parentless members resolve unambiguously, by identity and rank."""
    tracker = two_roots()
    walker, queue, _ = walk(tracker)

    report = await walker.run_pass()

    assert report.claimed_issue_key == "beta"
    assert queue.submissions[0][1].issue_key == "beta"
    assert report.eligible == ("beta", "alpha")


async def test_an_unapproved_member_never_reaches_the_walk():
    """Approval is per issue when the container carries none of its own."""
    tracker = two_roots(approved=(ScopeRef(kind=ScopeKind.ISSUE, key="alpha"),))
    walker, queue, _ = walk(tracker)

    report = await walker.run_pass()

    assert report.eligible == ("alpha",)
    assert enqueued(queue) == ["alpha"]
    assert "beta" not in tracker.claims
    assert [item.issue_key for item in report.snapshot] == ["alpha"]


async def test_dispatch_target_resolution_reads_no_description_text(monkeypatch):
    """The target is an identity; prose is read only once a fire is launched."""
    tracker = two_roots()
    walker, queue, _ = walk(tracker)
    body_reads = []
    original = TrackerIssue.__getattribute__

    def checked(issue, name):
        if name == "body":
            body_reads.append(original(issue, "issue_key"))
            raise AssertionError("description text was read to resolve a target")
        return original(issue, name)

    launch = FireDispatcher.launch

    async def restoring(self, *args, **kwargs):
        monkeypatch.setattr(TrackerIssue, "__getattribute__", original)
        return await launch(self, *args, **kwargs)

    monkeypatch.setattr(FireDispatcher, "launch", restoring)
    monkeypatch.setattr(TrackerIssue, "__getattribute__", checked)
    report = await walker.run_pass()

    assert body_reads == []
    assert report.claimed_issue_key == "beta"
    assert enqueued(queue) == ["beta"]


def test_walker_modules_resolve_no_lane_marker_and_parse_no_prose():
    """Neither module can reach a marker vocabulary or a text parser at all."""
    forbidden_modules = {
        "kodezart.domain.comment_markers",
        "kodezart.domain.lane_record",
        "kodezart.services.lane_records",
        "re",
    }
    for module in (scope_dispatcher, scope_walker):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in forbidden_modules
            if isinstance(node, ast.Import):
                assert {alias.name for alias in node.names} & forbidden_modules == set()
            if isinstance(node, ast.Attribute):
                assert node.attr not in {
                    "body",
                    "description",
                    "title",
                    "list_comments",
                }


async def test_a_lane_is_dispatched_for_the_criterion_its_filter_cannot_reach():
    """The lane's own criteria are all graded; the walk still fires it.

    The one open criterion under it belongs to a deliverable in another
    project, so the scope's filter never carries it.  It is the lane's work
    all the same, and the report names it as what the fire is FOR.
    """
    tracker = reach_board(child_state=WorkflowStateKind.UNSTARTED, out_of_filter=True)
    walker, queue, _ = walk(tracker)

    report = await walker.run_pass()

    assert report.claimed_issue_key == "lane"
    assert report.criterion_keys == ("child-check",)
    assert enqueued(queue) == ["lane"]
    assert [item.issue_key for item in report.snapshot] == ["lane"]


async def test_the_same_lane_reads_at_rest_once_that_criterion_is_graded():
    """Nothing else changed: the one open criterion moved to Done."""
    tracker = reach_board(child_state=WorkflowStateKind.COMPLETED, out_of_filter=True)
    walker, queue, _ = walk(tracker)

    report = await walker.run_pass()

    assert report.outcome is DispatchOutcome.empty_eligible_set
    assert report.eligible == ()
    assert report.exclusions == ()
    assert queue.submissions == []
    assert tracker.claims == {}


async def test_a_cancelled_unreachable_criterion_refuses_rather_than_resting():
    """A cancellation with no supersession on record is not a closure.

    Neither arithmetic finds anything OPEN in this shape, so at-rest alone
    cannot tell a subtree reading from a filtered-member one.  This is the
    reading that can: a walk that never left its own filter reports the
    same restful nothing it reports for a graded criterion.
    """
    tracker = reach_board(child_state=WorkflowStateKind.CANCELED, out_of_filter=True)
    walker, queue, _ = walk(tracker)

    with pytest.raises(ScopeSupersessionReadError) as caught:
        await walker.run_pass()

    assert caught.value.criterion_keys == ("child-check",)
    assert queue.submissions == []
    assert tracker.claims == {}


async def test_the_identical_shape_inside_the_filter_dispatches_the_child_itself():
    """The control arm: a child the filter carries walks in its own right."""
    tracker = reach_board(child_state=WorkflowStateKind.UNSTARTED, out_of_filter=False)
    walker, queue, _ = walk(tracker)

    report = await walker.run_pass()

    assert report.eligible == ("child", "lane")
    assert report.claimed_issue_key == "child"
    assert enqueued(queue) == ["child"]
    assert report.criterion_keys == ("child-check",)


MILESTONE = ScopeRef(kind=ScopeKind.MILESTONE, key="fixture-milestone")


def milestone_reach_board(child_milestone):
    """The same lane shape under a milestone reference, the child out of it.

    *child_milestone* is the child subtree's own milestone — another one,
    or none at all, which are the two ways out of a milestone filter.
    """

    def on(issue, key):
        return issue.model_copy(update={"milestone_key": key})

    issues = (
        on(lane_issue("lane"), MILESTONE.key),
        on(criterion("lane-check", parent="lane", met=True), MILESTONE.key),
        on(lane_issue("child", parent_key="lane"), child_milestone),
        on(criterion("child-check", parent="child"), child_milestone),
    )
    return FakeTrackerPort(
        issues=issues,
        scope_containers=[
            ScopeContainer(
                ref=MILESTONE,
                name="fixture milestone",
                description="",
                url="https://tracker.invalid/m",
            ),
        ],
        scope_memberships={MILESTONE: ["lane"]},
        scope_label_members={
            ScopeRef(kind=ScopeKind.ISSUE, key="lane"): frozenset(
                {ScopeLabel.APPROVED}
            ),
        },
    )


async def test_the_report_names_the_open_criterion_the_filter_cannot_reach():
    """The out-of-filter arm: key and reason, in the filter's own terms."""
    tracker = reach_board(child_state=WorkflowStateKind.UNSTARTED, out_of_filter=True)
    walker, queue, _ = walk(tracker)

    report = await walker.run_pass()

    assert (
        IssueExclusion(
            issue_key="child-check",
            clause=ExclusionClause.OUT_OF_SCOPE,
            detail=OUTSIDE_PROJECT,
        )
        in report.exclusions
    )
    assert report.claimed_issue_key == "lane"
    assert enqueued(queue) == ["lane"]


@pytest.mark.parametrize(
    ("child_milestone", "detail"),
    [
        ("other-milestone", "other-milestone"),
        (None, "the issue belongs to no milestone"),
    ],
)
async def test_an_out_of_milestone_criterion_is_named_in_the_milestones_own_terms(
    child_milestone, detail
):
    """A milestone reference states the reason as a milestone, never a project.

    Both lanes and both their criteria sit in the same project throughout,
    so a reason read off the project field cannot tell the two rows apart
    and cannot answer either of them.
    """
    tracker = milestone_reach_board(child_milestone)
    walker, queue, _ = walk(tracker, ref=MILESTONE)

    report = await walker.run_pass()

    assert (
        IssueExclusion(
            issue_key="child-check",
            clause=ExclusionClause.OUT_OF_SCOPE,
            detail=detail,
        )
        in report.exclusions
    )
    assert report.claimed_issue_key == "lane"
    assert enqueued(queue) == ["lane"]


async def test_the_same_shape_inside_the_filter_names_no_unreachable_criterion():
    """The in-filter arm: the child is a member and walks in its own right."""
    tracker = reach_board(child_state=WorkflowStateKind.UNSTARTED, out_of_filter=False)
    walker, queue, _ = walk(tracker)

    report = await walker.run_pass()

    assert [
        exclusion
        for exclusion in report.exclusions
        if exclusion.clause is ExclusionClause.OUT_OF_SCOPE
    ] == []
    assert report.claimed_issue_key == "child"
    assert enqueued(queue) == ["child"]


async def test_a_pass_with_no_eligible_lane_still_names_the_unreachable_criterion():
    """The arm every held lane lands on: an empty set is not an empty report.

    The one ready lane is held by its own open delivery, so this pass fires
    nothing.  The criterion its filter cannot reach is open all the same,
    and a report that fell silent here would say exactly what a scope with
    no work left says.
    """
    tracker = reach_board(child_state=WorkflowStateKind.UNSTARTED, out_of_filter=True)
    walker, queue, probe = walk(tracker, delivered=("lane",))

    report = await walker.run_pass()

    assert report.outcome is DispatchOutcome.empty_eligible_set
    assert queue.submissions == []
    assert tracker.claims == {}
    assert "lane" in probe.calls
    assert (
        IssueExclusion(
            issue_key="child-check",
            clause=ExclusionClause.OUT_OF_SCOPE,
            detail=OUTSIDE_PROJECT,
        )
        in report.exclusions
    )
    assert (
        IssueExclusion(issue_key="lane", clause=ExclusionClause.OPEN_DELIVERY)
        in report.exclusions
    )
