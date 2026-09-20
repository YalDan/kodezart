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

import pytest

from kodezart.chains import native_delivery, scope_walker
from kodezart.domain import fire_plateau, issue_tree, lane_entry, topology
from kodezart.domain.errors import ScopeSupersessionReadError
from kodezart.services import lane_entry as lane_entry_reader
from kodezart.services import scope_dispatcher, scope_runtime
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
from kodezart.types.domain.run_records import RunOutcome
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
    """No module on the walk can ask a pull request anything, statically.

    ``scope_runtime`` is on the list because it is the one module of the walk
    that holds a forge probe at all: the carve-out's open-delivery read is
    made there (KOD-721, KOD-777), and the probe it is handed answers merge
    state on the same object. Nothing but a type checker stood between that
    call site and a merge-state read until this list named the module.
    """
    modules = [scope_walker, topology, issue_tree, scope_dispatcher, scope_runtime]
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


def fire_outcome_vocabulary() -> frozenset[str]:
    """The fire-level outcome vocabulary, read off the enums rather than listed.

    A member appended later is covered by the assertion below without the
    assertion being touched, which is the whole point of deriving the set
    from the enums: a hand-written list would only forbid what the author of
    the list happened to know about.

    Two enums and two derivations of one, because a fire's ending is spelled
    in four places: ``WorkflowOutcome`` is how a lane's own run ended,
    ``RunOutcome`` how the scheduler and the watcher say the same thing, and
    ``classify_outcome`` and ``accept_verdict`` are the two readings that turn
    a finished fire's state into either of them.

    The vocabulary's own blind spots are stated with the scanned set, in
    ``dispatchability_predicate_sources``; what belongs here is the one that is
    a property of the words themselves. The match is by SPELLING alone, so a
    member of an unrelated enum spelled like one of these reads as a hit:
    over-inclusion, deliberately, because a scanned unit has no business
    spelling any of these words whatever it means by them.
    """
    return frozenset(
        {WorkflowOutcome.__name__}
        | {member.name for member in WorkflowOutcome}
        | {member.value for member in WorkflowOutcome}
        | {RunOutcome.__name__}
        | {member.name for member in RunOutcome}
        | {member.value for member in RunOutcome}
        | {"classify_outcome", "accept_verdict"}
    )


#: The bare names forbidden in the walker's own decision and nowhere else.
#:
#: They are what a fire's ending is called where it is legitimately held — a
#: state key, a field of the phase a delivery ended in — so forbidding them
#: over a whole module would forbid the one reporting helper of the walker,
#: which reads a delivery phase on purpose and is handed nothing a decision is
#: made from. Anywhere else in the walker, either name is a fire-level outcome
#: reaching the dispatch decision (KOD-725).
DECISION_ONLY_NAMES = frozenset({"outcome", "delivery"})


def outcome_references(
    source: str, *, also: frozenset[str] = frozenset()
) -> frozenset[str]:
    """Every fire-level outcome the parsed *source* names, however it names it.

    Imports, bare names, attribute reads and the wire strings themselves all
    count: a predicate that compared ``report.outcome == "scope_converged"``
    would be reading a fire outcome just as surely as one that imported the
    enum. *also* adds the names forbidden in this one source alone.
    """
    vocabulary = fire_outcome_vocabulary() | also
    found: set[str] = set()
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if isinstance(node, ast.Name) and node.id in vocabulary:
            found.add(node.id)
        if isinstance(node, ast.Attribute) and node.attr in vocabulary:
            found.add(node.attr)
        if isinstance(node, ast.ImportFrom | ast.Import):
            found |= {
                alias.name.rsplit(".", 1)[-1] for alias in node.names
            } & vocabulary
        if isinstance(node, ast.Constant) and node.value in vocabulary:
            found.add(node.value)
    return frozenset(found)


#: The one method of the live walker the scan is allowed to leave out.
#:
#: It is the walker's single reporting helper: it streams one fire and reports
#: how that fire ended by appending to a list of lanes. The lists a dispatch
#: decision is made from are not passed to it, and that its reporting list is
#: not read back into a decision is pinned by a walk and not by this scan
#: (KOD-724, KOD-725).
REPORTING_HELPER = "_fire"

#: The label of the live walker's decision, which is its whole MODULE but that
#: helper.
#:
#: Named apart because this source, and no other scanned here, also forbids
#: ``DECISION_ONLY_NAMES``.
WALK_DECISION_UNIT = f"scope_runtime minus {REPORTING_HELPER}"


def walker_decision_source(*, unit_source: str, helper_source: str) -> str:
    """*unit_source* with the exact text of the reporting helper removed once.

    Subtraction rather than a list of definitions, so that anything ADDED to
    the walker is scanned by construction: the decision is everything the
    module does except the one helper named above, and a new helper reading an
    ending into a decision cannot escape by not being on a list — whether it is
    written as a method or beside the class.

    The occurrence is counted before it is removed, because a subtraction that
    silently removed nothing — a helper renamed, a source reformatted — would
    leave the scan passing over a text it no longer covers the way it claims.
    """
    assert unit_source.count(helper_source) == 1, (
        f"the reporting helper's source occurs {unit_source.count(helper_source)} "
        "times in the text it is subtracted from"
    )
    return unit_source.replace(helper_source, "", 1)


def dispatchability_predicate_sources() -> tuple[tuple[str, str], ...]:
    """The source of everything that decides whether a lane is dispatchable.

    The decision is the ready set and the gap arithmetic under it, plus what
    walks the result: the subtree closure that says what a candidate still
    owes, the topology that partitions candidates into ready and blocked, the
    walker that assembles the ready set from both, the type that carries it,
    and the one pass method that turns it into a launch.
    ``record_run_outcome`` is deliberately absent — it records how a fire
    that already ran ended, which is the one place a run outcome belongs.

    The live walk's own decision is here WHOLE: the walker MODULE minus its one
    reporting helper, plus the entry reading it stands on — a lane's record and
    the branch it names decide how a fire enters, and neither reading may
    consult how the fire before it ended (KOD-724, KOD-725). The module is
    scanned entire rather than method by method because more of it than the
    selection decides: the run loop composes the resting lanes, the lane
    boundary appends to them and the readmission vetoes a fire, so a list of
    methods would leave the parts nobody thought to list unscanned. And the
    module rather than the class alone, because the walker's decision is not
    confined to its class: the criterion identities a fire is measured against
    and the turn the selection returns are built beside it, and a module's own
    imports and bindings are where a read spelled under another name would be
    written. The plateau arithmetic is scanned whole for a related reason: it is
    the quantity the plateau reading reads, and it counts criterion identities
    and nothing else.

    **Blind spots, stated where the scanned set is defined.**

    * Calls are NOT followed. Everything the walker's own module defines is
      scanned — its methods, the helpers beside them, its module-level bindings
      — but a function in another module that a scanned unit calls is not
      scanned here at all.
    * ``DECISION_ONLY_NAMES`` applies to the walker's own text and to no other
      source. Elsewhere those two bare words are what a fire's ending is
      legitimately called — a state key, a field of a delivery phase — so
      forbidding them everywhere would forbid the reporting that has to read
      one. The consequence is real: a bare ``record.outcome`` in the entry
      reading or in the plateau arithmetic is not a hit.
    * A name assembled from parts is missed: ``getattr(last, "out" + "come")``
      names nothing the parse can see.
    * A member bound to a name under another spelling and read by identity is
      read under that spelling and passes, wherever the binding is written.
    * A direct ``import X as Y`` is caught only where the import STATEMENT
      itself is inside the scanned text. For every unit scanned here it is: a
      module's source carries its own imports. A unit scanned method by method,
      or class by class, would not carry them, and the alias would pass there.
    * The match is by spelling, so it over-includes; see
      ``fire_outcome_vocabulary``.
    """
    return (
        ("SubtreeClosure", inspect.getsource(issue_tree.SubtreeClosure)),
        ("plan_topology", inspect.getsource(topology.plan_topology)),
        ("scope_ready", inspect.getsource(scope_ready)),
        ("scope_walker", inspect.getsource(scope_walker)),
        ("run_pass", inspect.getsource(ScopeDispatcher.run_pass)),
        (
            WALK_DECISION_UNIT,
            walker_decision_source(
                unit_source=inspect.getsource(scope_runtime),
                helper_source=inspect.getsource(
                    getattr(scope_runtime.ScopeWorkflowEngine, REPORTING_HELPER)
                ),
            ),
        ),
        ("services/lane_entry", inspect.getsource(lane_entry_reader)),
        ("domain/lane_entry", inspect.getsource(lane_entry)),
        ("fire_plateau", inspect.getsource(fire_plateau)),
    )


#: A class-shaped control's reporting helper, spelled once and shared by both
#: controls below so the text subtracted from either is the exact same text.
CONTROL_HELPER = '''    def _fire(self, lane):
        """Stream the lane and report how its fire ended."""
        final = lane.stream()
        return final["delivery"]
'''

#: The same control's clean method: it decides from the gap and the resting
#: lanes, which is what a walker's decision is allowed to read.
CONTROL_CLEAN = """    def _select(self, ready, rested):
        return [row for row in ready if row.issue_key not in rested]
"""

#: And its unclean one: a SECOND method reading the phase a delivery ended in,
#: which is the reading the scan exists to find wherever it is written.
CONTROL_DECIDING = """    def _readmitted(self, final, rested):
        if final["delivery"] is None:
            rested.append(final)
        return rested
"""

#: The same reading written BESIDE the class instead of inside it, which is
#: where the walker's own extracted helpers live: a module-level function whose
#: answer a decision is made from.
CONTROL_DECIDING_BESIDE = """def _owed(turn, rested):
    if turn.outcome is None:
        rested.append(turn)
    return rested
"""


def test_the_scan_excludes_one_reporting_helper_and_nothing_else():
    """The subtraction removes the named helper and leaves every other text.

    Three module-shaped sources, differing in one definition. In the first the
    phase a delivery ended in is read inside the excluded helper and nowhere
    else, and the scanned remainder is clean. In the second the same kind of
    read also sits in a method that decides; in the third it sits in a function
    BESIDE the class, which is where the walker's own extracted helpers live.
    Both remainders are hits — so the exclusion is by the helper's own text and
    not by the words the helper happens to use, and a definition added to the
    walker's module is scanned whether or not anybody listed it and wherever it
    is written.
    """
    reporting_only = f"class Walker:\n{CONTROL_CLEAN}\n{CONTROL_HELPER}"
    deciding_too = (
        f"class Walker:\n{CONTROL_CLEAN}\n{CONTROL_DECIDING}\n{CONTROL_HELPER}"
    )
    deciding_beside = (
        f"class Walker:\n{CONTROL_CLEAN}\n{CONTROL_HELPER}\n\n{CONTROL_DECIDING_BESIDE}"
    )

    clean = walker_decision_source(
        unit_source=reporting_only, helper_source=CONTROL_HELPER
    )
    unclean = walker_decision_source(
        unit_source=deciding_too, helper_source=CONTROL_HELPER
    )
    beside = walker_decision_source(
        unit_source=deciding_beside, helper_source=CONTROL_HELPER
    )

    # Not vacuous: every remainder still carries the class and its clean
    # method, the helper's own read is gone from each, and the third really
    # does still hold the definition written beside the class.
    assert "_select" in clean and "_select" in unclean and "_select" in beside
    assert '"delivery"' not in clean
    assert "def _owed(" in beside
    assert outcome_references(clean, also=DECISION_ONLY_NAMES) == frozenset()
    assert outcome_references(unclean, also=DECISION_ONLY_NAMES) == {"delivery"}
    assert outcome_references(beside, also=DECISION_ONLY_NAMES) == {"outcome"}
    # Before the whole module is scanned the helper is found in it exactly once,
    # and a text the helper is absent from is refused rather than scanned as if
    # a subtraction had happened.
    with pytest.raises(AssertionError):
        walker_decision_source(
            unit_source=f"class Walker:\n{CONTROL_CLEAN}", helper_source=CONTROL_HELPER
        )


def test_the_detector_flags_a_dispatch_decision_that_reads_a_fire_outcome():
    """The positive control: the same scan over a predicate that does read one.

    One shape per name the scan forbids, because a vocabulary is only as good
    as the reading that consumes it: a detector that missed the scheduler's
    spelling of a fire's ending, or the two readings that produce either
    spelling, would report the same empty set over the real sources below.
    """
    member = next(iter(WorkflowOutcome))
    ran = next(iter(RunOutcome))
    reading_enum = f"""
        from kodezart.types.domain.outcome import WorkflowOutcome

        def dispatchable(lane):
            return lane.last_outcome is not WorkflowOutcome.{member.name}
    """
    reading_wire_string = f"""
        def dispatchable(lane):
            return lane.last_outcome != "{member.value}"
    """
    reading_the_run_enum = f"""
        from kodezart.types.domain.run_records import RunOutcome

        def dispatchable(lane):
            return lane.last_run is not RunOutcome.{ran.name}
    """
    reading_the_run_wire_string = f"""
        def dispatchable(lane):
            return lane.last_run != "{ran.value}"
    """
    reading_a_classification = """
        def dispatchable(lane):
            return classify_outcome(lane.state) is None
    """
    reading_a_verdict = """
        def dispatchable(lane):
            return lane.state["accept_verdict"] is None
    """
    reading_the_bare_names = """
        def dispatchable(lane):
            return lane.outcome is None and lane.delivery is None
    """
    # One control per bare name, so that dropping either from the set fails
    # something: a control that compared the scan's answer with the set itself
    # would pass over any set at all, including the empty one.
    reading_a_bare_outcome = """
        def dispatchable(lane):
            return lane.outcome is None
    """
    reading_a_bare_delivery = """
        def dispatchable(lane):
            return lane.delivery is None
    """
    # The import arm's own control: the enum is imported under another name and
    # then read by SUBSCRIPT, which is the one shape no other arm can see — the
    # alias is not the enum's name and the subscripted key is a value this
    # source never spells.
    reading_an_aliased_import = """
        from kodezart.types.domain.outcome import WorkflowOutcome as _E

        def dispatchable(lane):
            return lane.last is not _E[lane.ending]
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
    assert outcome_references(reading_the_run_enum) >= {
        RunOutcome.__name__,
        ran.name,
    }
    assert outcome_references(reading_the_run_wire_string) >= {ran.value}
    assert outcome_references(reading_a_classification) == {"classify_outcome"}
    assert outcome_references(reading_a_verdict) == {"accept_verdict"}
    assert outcome_references(reading_an_aliased_import) == {WorkflowOutcome.__name__}
    # The two bare names are forbidden where the walk decides and nowhere
    # else, so the scan finds them only when it is asked to: a source that
    # holds a delivery phase outside every decision is not a hit.
    assert outcome_references(reading_the_bare_names) == frozenset()
    # Spelled out rather than compared with the set the scan is given, so that
    # a name dropped from that set fails here instead of passing quietly.
    assert DECISION_ONLY_NAMES == {"outcome", "delivery"}
    assert outcome_references(reading_the_bare_names, also=DECISION_ONLY_NAMES) == {
        "outcome",
        "delivery",
    }
    assert outcome_references(reading_a_bare_outcome, also=DECISION_ONLY_NAMES) == {
        "outcome"
    }
    assert outcome_references(reading_a_bare_delivery, also=DECISION_ONLY_NAMES) == {
        "delivery"
    }
    assert outcome_references(reading_the_gap) == frozenset()
    assert outcome_references(reading_the_gap, also=DECISION_ONLY_NAMES) == frozenset()
    # And alive over real source, not only over the shapes written here: the
    # step that classifies a finished fire reads exactly what the decisions
    # below may not, and the scan says so about the shipped module.
    assert outcome_references(inspect.getsource(native_delivery)) >= {
        "classify_outcome",
        "accept_verdict",
    }


def test_no_fire_outcome_is_read_anywhere_the_dispatch_decision_is_made():
    """A lane's admissibility is decided without any fire's ending being read.

    Whether a lane is dispatchable follows from its gap and its blockers and
    from nothing else, so a run that ended ``loop_not_accepted`` can never be
    read as a lane finished, and one that ended ``shutdown_abandoned`` can
    never be read as a lane abandoned: the arithmetic has no access to either
    fact in the first place.

    The live walk is held to it WHOLE, and not in the three methods somebody
    listed: its selection asks only which lanes the ready read offers and which
    are resting, the reading that decides whether a fired lane is offered again
    asks only which criterion identities its subtree now carries as closed, and
    the loop that composes the resting lanes, the boundary that appends to them
    and the readmission that vetoes a fire are all in the scanned text too — as
    are the helpers written beside the class that build the identities that
    reading measures and the turn the selection returns. A fire that ended
    ``loop_not_accepted`` and a fire that ended ``ci_passed`` reach every one of
    them as the same fact — the gap they left — which is what lets a lane be
    fired twice in one invocation without any state machine over its exits
    (KOD-724, KOD-725).
    """
    scanned = dispatchability_predicate_sources()

    assert {label for label, _ in scanned} == {
        "SubtreeClosure",
        "plan_topology",
        "scope_ready",
        "scope_walker",
        "run_pass",
        WALK_DECISION_UNIT,
        "services/lane_entry",
        "domain/lane_entry",
        "fire_plateau",
    }
    # Every source really carries source: a label whose text came back empty
    # would satisfy the assertion below without scanning anything.
    assert all(source.strip() for _, source in scanned)
    # And the walker's scanned text really is the module less one method: every
    # definition the walk decides in is in it — the ones inside the class and
    # the ones beside it — and the excluded helper is not.
    walker = next(source for label, source in scanned if label == WALK_DECISION_UNIT)
    assert all(
        f"def {name}(" in walker
        for name in (
            "run",
            "_select",
            "_settle",
            "_put_back",
            "_readmitted",
            "_owed_identities",
            "_ready_turn",
        )
    )
    assert f"def {REPORTING_HELPER}(" not in walker
    for label, source in scanned:
        also = DECISION_ONLY_NAMES if label == WALK_DECISION_UNIT else frozenset()
        assert outcome_references(source, also=also) == frozenset(), label


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
