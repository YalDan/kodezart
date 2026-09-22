"""Native lane delivery uses real coordinator logic and boundary doubles."""

import ast
import asyncio
import copy
import inspect
import signal
from types import UnionType
from typing import Union, get_args, get_origin

import pytest
from pydantic import ValidationError

from kodezart.chains.criteria import TrackerCriteria
from kodezart.chains.lane_delivery import LaneDeliveryCoordinator
from kodezart.chains.native_delivery import NativeLaneWorkflow
from kodezart.core.protocols import (
    FireCriteriaReader,
    LaneStateWriter,
    PRCreator,
    TrackerPort,
)
from kodezart.domain import stall_report
from kodezart.domain.errors import (
    BaseResolutionError,
    CheckObservationError,
    DeliveryHeadError,
    FireSpecEntryError,
    PRStateReadError,
)
from kodezart.types.domain.check_observation import IncompleteChecks
from kodezart.types.domain.delivery import CheckRedClass, LaneDelivery
from kodezart.types.domain.gating import OutboundDestination
from kodezart.types.domain.native_delivery import (
    CompletedLaneDelivery,
    LaneDeliveryEvent,
)
from kodezart.types.domain.operation import CheckPrerequisite, CheckStep, RepoEntry
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.pr_state import PRLifecycle, PRState
from kodezart.types.domain.workflow import ExecutionContext
from tests.chains.test_native_fire import (
    SUBJECT,
    CountingTracker,
    change_tracker,
    engine,
)
from tests.chains.test_native_fresh_boundaries import prepare
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeCIMonitor,
    FakeForgeQuery,
    FakeGitService,
    FakePRCreator,
    FakePRStateReader,
    PassThroughGate,
    make_prompt_provider,
    nothing_written,
)

SHA = "a" * 40
REPO = "https://github.com/owner/repo.git"
HEAD = "lane-head"
BASE = "blocker-branch"
#: Seconds every delivery driven in this module is given: the shared
#: ``deliver()`` driver below applies it to each one, so every fixture inherits
#: it by driving the coordinator at all, and the concurrency fixtures give
#: their event waits the same number.  It is a WALL-CLOCK DEADLINE, not a
#: budget for the work: a delivery here reaches its check watch within tens
#: of milliseconds, so what it fails is a coordinator that stops making
#: progress — the fixture that drove it reds on a TimeoutError instead
#: of hanging the module run.  The driver reads it when it is called, so the
#: one fixture that shows the deadline fires can shorten it for itself.
WAIT_BOUND = 5
#: Seconds any one test in this module may run before it is STOPPED, whatever
#: it is awaiting.  The driver's deadline above is a ``wait_for``, and a
#: ``wait_for`` ends by cancelling what it waits on: a coordinator that
#: catches that cancellation and waits again defeats it, and the fixture
#: would hang the module run after all.  This stop depends on no
#: cancellation — a real-time interval timer interrupts the event loop from
#: outside.  Twice the driver's deadline, so a delivery that merely stalls is
#: reported by the driver's TimeoutError as before, including the one fixture
#: that runs a second bounded lane after a first one has timed out.
HARD_STOP = 2 * WAIT_BOUND


class HardStopError(BaseException):
    """A test in this module outlived its hard stop.

    A ``BaseException``, so the ``except Exception`` a coordinator may carry
    does not take it for one of its own failures and carry on.
    """


@pytest.fixture(autouse=True)
def hard_stop(request):
    """Stop the test on a timer no swallowed cancellation can outlast.

    Set for each test and cleared after it, with the handler that was
    installed before put back, so nothing outside this module is timed.
    """

    def stop(signum, frame):
        raise HardStopError(
            f"{request.node.nodeid} ran past its {HARD_STOP}s hard stop"
        )

    previous = signal.signal(signal.SIGALRM, stop)
    signal.setitimer(signal.ITIMER_REAL, HARD_STOP)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _branch_tests(tree):
    """Every expression a control-flow branch in *tree* is taken on."""
    for node in ast.walk(tree):
        if isinstance(node, ast.If | ast.IfExp | ast.While):
            yield node.test
        elif isinstance(node, ast.Match):
            yield node.subject
        elif isinstance(node, ast.match_case) and node.guard is not None:
            yield node.guard


def _names(node) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
    }


def _public_surface(instance) -> set[str]:
    """Every public name *instance* offers, whoever put it there.

    Two readings, unioned, because each answers about a placement the other
    can be talked out of.  The class line is walked namespace by namespace —
    ``object`` left out, since its names are every class's and say nothing
    about this one — so a public method a base contributes is counted even
    when a ``__dir__`` override leaves it out of ``dir()``.  And the OBJECT a
    caller holds is asked through ``dir()``, which resolves the instance's
    own attributes too, so an attribute ``__init__`` assigns is counted
    although no namespace in the line holds it.  A name the class answers
    only through ``__getattr__`` is in neither; that is refused outright by
    the pin, which is what keeps this union the whole surface.  Dunders are
    left out, and a name is counted whether or not it is callable, because
    ``callable()`` answers False for a property and for a classmethod.
    """
    declared = {
        name
        for base in type(instance).__mro__
        if base is not object
        for name in vars(base)
        if not name.startswith("_")
    }
    resolved = {name for name in dir(instance) if not name.startswith("_")}
    return declared | resolved


#: The hooks through which a class can answer for a name no namespace
#: declares, or hide one it does from ``dir()``.
_DYNAMIC_LOOKUPS = frozenset({"__getattr__", "__getattribute__", "__dir__"})


def _dynamic_lookups(klass: type) -> set[str]:
    """Each lookup hook a class in *klass*'s line, other than ``object``, defines."""
    return {
        name
        for base in klass.__mro__
        if base is not object
        for name in vars(base)
        if name in _DYNAMIC_LOOKUPS
    }


def _roles_in(annotation) -> set[object]:
    """*annotation* together with every member a union of it is built from.

    ``TrackerPort | None`` is a union object rather than the port, so asking
    whether a set of raw annotations contains ``TrackerPort`` answers no for
    a constructor that takes one optionally.  The members are walked out
    instead — recursively, so a union nested inside a union is no hiding
    place either — and the walk is bounded by the annotation's own depth.
    """
    roles = {annotation}
    if get_origin(annotation) in (Union, UnionType):
        for member in get_args(annotation):
            roles |= _roles_in(member)
    return roles


def pr_identity(*, head=HEAD, number=1, base=BASE):
    return PRState(
        url=f"https://github.com/owner/repo/pull/{number}",
        number=number,
        head_repo_url=REPO.removesuffix(".git"),
        head_branch=head,
        head_sha=SHA,
        base_repo_url=REPO.removesuffix(".git"),
        base_branch=base,
        lifecycle=PRLifecycle.OPEN,
    )


async def setup(*, monitor=None, git=None, repositories=(), bound=1, watches=2):
    tracker = CountingTracker()
    criteria = TrackerCriteria(tracker=tracker)
    fire = engine(criteria=criteria)
    state, config = prepare(fire, "real-job-id")
    spec, snapshot = await criteria.read_entry(issue_key=SUBJECT)
    state.update(
        fire_spec=spec, criterion_set=snapshot, feature_branch=HEAD, feature_tip_sha=SHA
    )
    context = ExecutionContext.from_configurable(config)
    context = context.model_copy(
        update={"base_spec": context.base_spec.model_copy(update={"base_branch": BASE})}
    )
    creator = FakePRCreator(pr_url=pr_identity().url)
    pr_reader = FakePRStateReader(records={(REPO, 1): pr_identity()})
    monitor = monitor or FakeCIMonitor()
    query = FakeForgeQuery()
    owner = LaneDeliveryCoordinator(
        service=fire.specification._service,
        git=git or FakeGitService(remote_branch_shas={HEAD: SHA, BASE: "b" * 40}),
        pr_creator=creator,
        pr_state_reader=pr_reader,
        forge_query=query,
        ci=monitor,
        criteria_reader=criteria,
        prompts=make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
        repositories=repositories,
        git_base_url="https://github.com",
        max_concurrent_watches=watches,
        red_rerun_max_attempts=bound,
    )
    return owner, state, context, creator, monitor, query, tracker


async def deliver(parts, *, stalled=False, remediation=False):
    """Drive one delivery and hold it to its write set and its hand-off (KOD-326).

    The await is BOUNDED here, in the one place every fixture's delivery goes
    through, and not in the fixtures that happened to need it: WAIT_BOUND is
    a wall-clock deadline, and a coordinator that stops making progress — at
    the check watch, at a slot it never gets, after either — fails the
    fixture that drove it with a TimeoutError instead of hanging the module
    run at the first bare await.  Every fixture inherits the bound the same
    way it inherits the two claims below: by driving the coordinator at all.

    The projection is taken before the call and compared after it on every
    way out — a result, a refusal, a cancellation, the deadline itself — so
    each fixture in this module makes the claim by driving the coordinator at
    all.

    The state dict it is handed is claimed the same way (KOD-313): the branch
    and the final sha are READ off that state, and no delivery fact goes back
    into it.  Pinning the fire's own annotations instead would only say which
    names the state DECLARES; a key written into the dict at run time carries
    no annotation and would pass that. Here it reds every fixture.

    The snapshot is DEEP, because the state carries mutable values — the
    criteria and the flagged items are lists, and one of them is handed on to
    a collaborator — and a shallow copy shares each of them with the live
    state, so a fact appended to a list ON the state compares equal to
    itself.  Deep, the claim covers a key written onto the dict and a value
    reached through it alike.
    """
    owner, state, context, _, _, _, tracker = parts
    unwritten = nothing_written(tracker)
    handed = copy.deepcopy(state)
    try:
        return await asyncio.wait_for(
            owner.deliver(
                state=state,
                context=context,
                stalled=stalled,
                remediation_available=remediation,
            ),
            timeout=WAIT_BOUND,
        )
    finally:
        assert unwritten(), "the coordinator wrote to the tracker"
        assert state == handed, "the coordinator wrote to the state it was handed"


async def test_green_opens_on_actual_head_and_resolved_base_and_round_trips():
    parts = await setup()
    result = await deliver(parts)
    _, _, _, creator, ci, query, tracker = parts
    assert result.outcome is WorkflowOutcome.ci_passed
    assert result.pr.state == "open"
    assert result.final_commit_sha == SHA
    assert result.issue_id == result.lane_key == SUBJECT
    assert creator.calls[0]["head"] == HEAD
    assert creator.calls[0]["base"] == BASE
    assert ci.calls == [{"repo_url": REPO, "ref": HEAD}]
    assert len(query.lookups) == 1
    assert tracker.issue_writes == tracker.workflow_writes == []
    event = LaneDeliveryEvent(delivery=CompletedLaneDelivery(result=result))
    assert LaneDeliveryEvent.model_validate_json(event.model_dump_json()) == event
    wire = result.model_dump(by_alias=True)
    assert wire["baseBranch"] == BASE and wire["checksPassed"] is True
    with pytest.raises(ValidationError):
        result.head_branch = "other"


@pytest.mark.parametrize("observed", [None, "f" * 40])
async def test_missing_or_changed_remote_head_refuses_before_pr(observed):
    parts = await setup(
        git=FakeGitService(remote_branch_shas={HEAD: observed, BASE: SHA})
    )
    with pytest.raises(DeliveryHeadError) as caught:
        await deliver(parts)
    assert caught.value.observed_sha == observed
    assert caught.value.expected_sha == SHA
    assert parts[3].calls == [] and parts[4].calls == []


async def test_missing_resolved_base_never_falls_back_to_trunk():
    parts = await setup(
        git=FakeGitService(remote_branch_shas={HEAD: SHA, BASE: None, "main": SHA})
    )
    with pytest.raises(BaseResolutionError) as caught:
        await deliver(parts)
    assert caught.value.branches == (BASE,)
    assert parts[3].calls == [] and parts[4].calls == []


async def test_open_pr_replay_reuses_native_head_lookup_without_generation():
    parts = await setup()
    owner, _, _, creator, *_ = parts
    owner._pr_state_reader.records[(REPO, 7)] = pr_identity(number=7)
    owner._forge_query = FakeForgeQuery(
        open_prs={(REPO, HEAD): ("https://github.com/owner/repo/pull/7", 7)}
    )
    result = await deliver(parts)
    assert result.pr.number == 7 and creator.calls == []


@pytest.mark.parametrize(
    "declared,exempt,outcome",
    [
        (False, False, WorkflowOutcome.ci_not_configured),
        (True, False, WorkflowOutcome.ci_no_run_at_ref),
        (True, True, WorkflowOutcome.ci_not_configured),
    ],
)
async def test_absent_checks_keep_the_declaration_and_exemption_arms(
    declared, exempt, outcome
):
    parts = await setup(
        monitor=FakeCIMonitor(passed=None, declared=declared),
        repositories=[RepoEntry(trunk="main", url=REPO, forge_exempt=exempt)],
    )
    result = await deliver(parts, remediation=True)
    assert result.outcome is outcome
    assert result.checks_passed is None and not result.remediation_pending
    assert not parts[4].rerun_calls


@pytest.mark.parametrize("summary", ["Runner unavailable", ""])
@pytest.mark.parametrize("kind", ["environment", "flake", "work", "unclassified"])
async def test_only_reproduced_work_defect_can_request_remediation(kind, summary):
    repository = RepoEntry(trunk="main", url=REPO)
    reruns = []
    if kind == "environment":
        repository = RepoEntry(
            trunk="main",
            url=REPO,
            checks=[
                CheckStep(
                    name="unit",
                    command="pytest",
                    forge_check="unit",
                    requires=frozenset({CheckPrerequisite.REPOSITORY_HISTORY}),
                )
            ],
            runner_environment={CheckPrerequisite.REPOSITORY_HISTORY: False},
        )
    elif kind == "flake":
        reruns = [(True, summary, frozenset())]
    elif kind == "unclassified":
        reruns = [(False, summary, frozenset({"lint"}))]
    monitor = FakeCIMonitor(
        passed=False,
        summary=summary,
        failed_names=frozenset({"unit"}),
        rerun_results=reruns,
    )
    parts = await setup(monitor=monitor, repositories=[repository])
    result = await deliver(parts, remediation=True)
    expected = {
        "environment": CheckRedClass.ENVIRONMENT_PREREQUISITE_UNMET,
        "flake": CheckRedClass.RUNNER_FLAKE,
        "work": CheckRedClass.WORK_DEFECT,
        "unclassified": CheckRedClass.UNCLASSIFIED,
    }[kind]
    assert result.red_class is expected
    assert result.remediation_pending == (kind == "work")
    assert len(monitor.rerun_calls) == (0 if kind == "environment" else 1)
    if result.remediation_pending:
        with pytest.raises(ValidationError, match="pending remediation"):
            LaneDeliveryEvent(delivery=CompletedLaneDelivery(result=result))


async def test_zero_rerun_bound_spends_no_probe_and_exhausted_budget_comments():
    parts = await setup(monitor=FakeCIMonitor(passed=False), bound=0)
    result = await deliver(parts)
    assert result.outcome is WorkflowOutcome.ci_failed_fix_budget_exhausted
    assert result.red_class is CheckRedClass.WORK_DEFECT
    assert parts[4].rerun_calls == []
    assert (
        len([call for call in parts[3].calls if call["method"] == "comment_on_pr"]) == 1
    )


async def test_the_only_failure_path_write_is_the_forge_s_pull_request_comment():
    """The one write an exhausted budget makes goes to the forge, not the board.

    It is ``PRCreator.comment_on_pr``, so the tracker write set is empty on
    this path exactly as it is on the green one: the coordinator holds no
    tracker port to write through.  The projection is taken here rather than
    read off the driver so the claim is legible at the call site.
    """
    parts = await setup(monitor=FakeCIMonitor(passed=False), bound=0)
    tracker = parts[6]
    unwritten = nothing_written(tracker)

    result = await deliver(parts)

    assert result.outcome is WorkflowOutcome.ci_failed_fix_budget_exhausted
    assert (
        len([call for call in parts[3].calls if call["method"] == "comment_on_pr"]) == 1
    )
    assert tracker.comment_writes == []
    assert unwritten()


async def test_a_coordinator_write_through_any_collaborator_reds_every_fixture():
    """The driver's claim is live, and it reaches below the coordinator's code.

    A write made by a collaborator while the coordinator is driving it lands
    in a journal the projection covers, so the driver reds — which is what
    makes every other fixture in this module an assertion rather than a hope.
    """
    parts = await setup()
    owner, tracker = parts[0], parts[6]
    inner = owner._criteria_reader

    class Writing:
        async def read_current(self, *, spec, held=None):
            await tracker.post_comment(
                issue_key=SUBJECT, body="a write made under the coordinator"
            )
            return await inner.read_current(spec=spec, held=held)

    owner._criteria_reader = Writing()
    with pytest.raises(AssertionError, match="wrote to the tracker"):
        await deliver(parts)


async def test_an_unlock_write_through_any_collaborator_reds_every_fixture():
    """The same claim for the unlock half, which leaves no trace on the board.

    Releasing a claim and a surface set on a board that holds neither moves
    the locks not at all, so a check that read the locks back would agree
    with the release it exists to catch.  The double journals the attempt,
    the projection covers that journal, and the driver reds — which is what
    makes "any unlock write fails the fixture" an assertion here.
    """
    parts = await setup()
    owner, tracker = parts[0], parts[6]
    inner = owner._criteria_reader
    assert tracker.claims == {} and tracker.leases == {}

    class Writing:
        async def read_current(self, *, spec, held=None):
            await tracker.release_claim(issue_key=SUBJECT, holder="lane")
            await tracker.release_surfaces(surfaces=frozenset(), holder="lane")
            return await inner.read_current(spec=spec, held=held)

    owner._criteria_reader = Writing()
    with pytest.raises(AssertionError, match="wrote to the tracker"):
        await deliver(parts)


async def test_stalled_lane_uses_the_same_open_watch_path_without_a_fix():
    parts = await setup(monitor=FakeCIMonitor(passed=False), bound=0)
    result = await deliver(parts, stalled=True, remediation=True)
    assert result.outcome is WorkflowOutcome.stalled_pr_opened
    assert (
        len([call for call in parts[3].calls if call["method"] == "create_pr"])
        == len(parts[4].calls)
        == 1
    )
    assert not result.remediation_pending


async def test_changed_observed_sha_and_incomplete_checks_do_not_become_red():
    parts = await setup(monitor=FakeCIMonitor(observed_sha_by_ref={HEAD: "b" * 40}))
    with pytest.raises(CheckObservationError):
        await deliver(parts)
    assert parts[4].rerun_calls == []

    class Pending(FakeCIMonitor):
        async def wait_for_checks(self, *, repo_url, ref):
            return IncompleteChecks(
                commit_shas=frozenset({SHA}),
                check_names=frozenset({"unit"}),
                failed_check_names=frozenset(),
                observed_count=1,
                expected_count=2,
                summary="Still running",
            )

    parts = await setup(monitor=Pending())
    with pytest.raises(CheckObservationError, match="Still running"):
        await deliver(parts)
    assert parts[4].rerun_calls == []


async def test_current_checks_are_revalidated_after_the_awaited_description():
    parts = await setup()
    owner, _, _, creator, _, _, tracker = parts

    class ChangingGate(PassThroughGate):
        async def gate(self, **kwargs):
            change_tracker(tracker, "changed-check")
            return await super().gate(**kwargs)

    owner._gate = ChangingGate()
    with pytest.raises(FireSpecEntryError):
        await deliver(parts)
    assert creator.calls == []


async def test_watch_bound_cancellation_releases_slot_for_next_lane(monkeypatch):
    """A lane the driver's deadline cancels at the watch gives its slot back.

    The first lane blocks at the check watch until the driver's deadline
    cancels it, and must end in that deadline's TimeoutError — not in a
    cancellation this fixture sent it — and the second lane, queued on the
    one watch slot, must then get the slot and pass.  The first lane's
    deadline is shortened to one second for that lane alone (the driver reads
    it when the lane first runs), so the second lane, driven on the module's
    full deadline, has the rest of it to take the slot and finish.  A slot
    the cancelled lane never gives back leaves the second lane waiting on
    acquisition until its own deadline fails it; a coordinator that absorbs
    the cancellation and keeps its slot hangs past the driver's deadline and
    is ended by the module's hard stop.
    """
    entered = asyncio.Event()
    resume = asyncio.Event()

    class Blocking(FakeCIMonitor):
        def __init__(self):
            super().__init__()
            self.active = self.peak = 0

        async def wait_for_checks(self, *, repo_url, ref):
            self.active += 1
            self.peak = max(self.peak, self.active)
            entered.set()
            try:
                await resume.wait()
                return await super().wait_for_checks(repo_url=repo_url, ref=ref)
            finally:
                self.active -= 1

    ci = Blocking()
    parts = await setup(monitor=ci, watches=1)
    full = WAIT_BOUND
    monkeypatch.setitem(globals(), "WAIT_BOUND", 1)
    first = asyncio.create_task(deliver(parts))
    # The event is the one await the driver does not make, so it carries the
    # module's deadline itself; by the time it is set the first lane has read
    # its shortened deadline, and the second lane is driven on the full one.
    await asyncio.wait_for(entered.wait(), timeout=full)
    monkeypatch.setitem(globals(), "WAIT_BOUND", full)
    second = asyncio.create_task(deliver(parts))
    with pytest.raises(TimeoutError):
        await first
    resume.set()
    assert (await second).outcome is WorkflowOutcome.ci_passed
    assert ci.peak == 1


async def test_the_driver_s_deadline_fails_a_delivery_that_stops_progressing(
    monkeypatch,
):
    """The driver's bound is shown to FIRE, on the shape it exists for.

    A coordinator that reaches the check watch and never comes back is the
    defect the deadline answers; without it the stall is indistinguishable
    from slow work, and the fixture that drove it takes the module run with
    it.  Here the watch never returns, and the drive — the same bare
    ``deliver(parts)`` every other fixture makes — raises instead.

    The module's deadline is shortened for this fixture alone, so that it
    costs one second and not five: what is asked is that the driver applies
    WAIT_BOUND to the delivery at all, not how many seconds it is set to.
    The drive runs under the module's own deadline as an outer bound, so a
    driver that lost its bound fails this fixture on that outer bound instead
    of hanging it — the fixture that shows the bound fires is not a hang
    itself.
    """
    entered = asyncio.Event()

    class Stalling(FakeCIMonitor):
        async def wait_for_checks(self, *, repo_url, ref):
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("a stalled watch never returns")

    parts = await setup(monitor=Stalling())
    outer = WAIT_BOUND
    monkeypatch.setitem(globals(), "WAIT_BOUND", 1)
    async with asyncio.timeout(outer):
        with pytest.raises(TimeoutError):
            await deliver(parts)
    assert entered.is_set(), "the deadline fired before the delivery reached the watch"


def test_creator_has_no_merge_or_workflow_state_capability():
    assert {
        name
        for name, method in vars(PRCreator).items()
        if not name.startswith("_") and callable(method)
    } == {"create_pr", "comment_on_pr"}
    assert not hasattr(FakePRCreator(), "merge_pr")


async def test_the_coordinator_holds_no_tracker_port_and_no_lane_state_writer():
    """No board-writing role is asked for, and none is held.

    The constructor's roles are the coordinator's whole declared reach: the
    one tracker-facing role among them is a reader, so the two negatives are
    not vacuous.  Each annotation is walked out through its union members
    first, so a role offered as ``TrackerPort | None`` is refused by the same
    two negatives rather than slipping past them, and every parameter is
    required, so there is no board-writing role a caller may fill that the
    composition root leaves out.  The instance is then checked as it stands,
    one level deep; a write made through a collaborator's own tracker is the
    driver's catch.
    """
    parameters = [
        parameter
        for parameter in inspect.signature(
            LaneDeliveryCoordinator.__init__
        ).parameters.values()
        if parameter.name != "self"
    ]
    roles = {
        role for parameter in parameters for role in _roles_in(parameter.annotation)
    }

    # The walk is shown on the very shape it exists for, so the two
    # negatives below are read against a union rather than trusted to be.
    assert TrackerPort in _roles_in(TrackerPort | None)

    assert TrackerPort not in roles
    assert LaneStateWriter not in roles
    assert FireCriteriaReader in roles
    assert [
        parameter.name
        for parameter in parameters
        if parameter.default is not inspect.Parameter.empty
    ] == []

    owner, *_, tracker = await setup()

    assert isinstance(tracker, TrackerPort)
    assert not any(isinstance(held, TrackerPort) for held in vars(owner).values())


def test_the_coordinator_branches_on_no_stalled_fact_and_names_no_do_not_merge_symbol():
    """Nothing in the lane's delivery is conditioned on a stalled lane.

    The two modules are derived from the classes that make up the lane's
    delivery, and the forbidden names are derived from the stall report's own
    public surface, so neither surface is a hand-kept list. The coordinator
    takes the fact and carries it into the frozen result and the classifier
    call — it is read, so the scan is not vacuous — but no branch is taken on
    it. Its one use as a conjunct is not a branch test, and what that conjunct
    withholds is pinned by the stalled open-watch fixture above. The lane step
    does branch on the fact, which is where the fact is established, so the
    branch half is asked of the coordinator alone; the import, name and
    literal half is asked of both (KOD-327).
    """
    coordinator = inspect.getmodule(LaneDeliveryCoordinator)
    step = inspect.getmodule(NativeLaneWorkflow)
    forbidden = {
        name
        for name, value in vars(stall_report).items()
        if not name.startswith("_")
        and (
            isinstance(value, str)
            or getattr(value, "__module__", None) == stall_report.__name__
        )
    }
    assert {"DO_NOT_MERGE_PREFIX", "stall_pr_title"} <= forbidden

    own = ast.parse(inspect.getsource(coordinator))
    assert "stalled" in _names(own)
    assert all("stalled" not in _names(test) for test in _branch_tests(own))

    for module in (coordinator, step):
        source = inspect.getsource(module)
        tree = ast.parse(source)
        assert not [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == stall_report.__name__
        ]
        assert not [
            alias
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name == stall_report.__name__
        ]
        assert _names(tree) & forbidden == set()
        assert not [
            spelling
            for spelling in ("do-not-merge", "do_not_merge", "DO_NOT_MERGE")
            if spelling in source
        ]

    # Each detector sees the shape it is asked to forbid, on a parsed string
    # rather than on the authored module, so no clause here depends on the
    # authored arm's own use of the stall report surviving.
    assert any(
        "stalled" in _names(test)
        for test in _branch_tests(ast.parse("if stalled:\n    pass\n"))
    )
    assert any(
        "stalled" in _names(test)
        for test in _branch_tests(ast.parse("x = 1 if stalled else 2\n"))
    )
    # A name is seen where it is USED: an import binds through an alias node,
    # which is what the import clause above reads instead.
    assert _names(ast.parse("title = stall_pr_title(ticket)\n")) & forbidden
    assert [
        node
        for node in ast.walk(
            ast.parse("from kodezart.domain.stall_report import stall_pr_title\n")
        )
        if isinstance(node, ast.ImportFrom) and node.module == stall_report.__name__
    ]


async def test_coordinator_exposes_only_the_delivery_entry_point():
    """One entry point, and the signature that keeps it one.

    Asked of a CONSTRUCTED coordinator, so every public name it offers is
    counted whatever placement carries it — a name a base declares, a name
    its own body declares, an attribute its constructor assigns — and not
    only the callable ones, since ``callable()`` answers False for a property
    and for a classmethod.  No class in its line may define ``__getattr__``,
    ``__getattribute__`` or ``__dir__``: through the first two a name is
    offered that no namespace declares and ``dir()`` does not list, and the
    third decides what ``dir()`` lists.

    The signature is the other half. What it must not contain is the point:
    the branch and the final sha are read off the state the coordinator is
    handed, so a parameter for either would move that reading back out to
    every caller, and the pin above would still pass.
    """
    owner, *_ = await setup()
    assert _public_surface(owner) == {"deliver"}
    assert _dynamic_lookups(LaneDeliveryCoordinator) == set()

    # The walk is shown on the shapes it exists for: a public name that only
    # a base declares, which the class's own namespace does not hold; one a
    # constructor assigns, which no namespace in the line holds; and one a
    # ``__dir__`` override leaves out of ``dir()``.
    class _Base:
        def publish(self):
            """A name on the surface of everything below it."""

    class _Inheriting(_Base):
        def __init__(self):
            self.destination = "a name no class body declares"

    class _Hiding(_Base):
        def __dir__(self):
            return ["deliver"]

    assert "publish" not in vars(_Inheriting)
    assert "destination" not in vars(_Inheriting) | vars(_Base)
    assert _public_surface(_Inheriting()) == {"publish", "destination"}
    assert "publish" not in dir(_Hiding())
    assert _public_surface(_Hiding()) == {"publish", "deliver"}

    # And the refusal is shown to see a lookup hook a base defines.
    class _Answering:
        def __getattr__(self, name):
            raise AttributeError(name)

    class _Below(_Answering):
        pass

    assert _dynamic_lookups(_Below) == {"__getattr__"}
    assert _dynamic_lookups(_Hiding) == {"__dir__"}

    signature = inspect.signature(LaneDeliveryCoordinator.deliver)
    assert [
        (name, parameter.kind) for name, parameter in signature.parameters.items()
    ] == [
        ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ("state", inspect.Parameter.KEYWORD_ONLY),
        ("context", inspect.Parameter.KEYWORD_ONLY),
        ("stalled", inspect.Parameter.KEYWORD_ONLY),
        ("remediation_available", inspect.Parameter.KEYWORD_ONLY),
    ]
    assert signature.return_annotation is LaneDelivery


async def test_delivery_refuses_incoherent_wire_outcome():
    result = await deliver(await setup())
    wire = result.model_dump()
    wire["outcome"] = WorkflowOutcome.ci_failed_unclassified
    with pytest.raises(ValidationError, match="outcome must match"):
        LaneDelivery.model_validate(wire)


@pytest.mark.parametrize(
    "field,value",
    [("state", "closed"), ("url", ""), ("url", " "), ("number", 0), ("number", -1)],
)
async def test_delivery_wire_requires_an_addressed_open_pr(field, value):
    result = await deliver(await setup())
    wire = result.model_dump(mode="json")
    wire["pr"][field] = value
    with pytest.raises(ValidationError, match="addressed open PR"):
        LaneDelivery.model_validate(wire)


@pytest.mark.parametrize(
    "field,value",
    [
        ("base_branch", "unrelated-base"),
        ("head_branch", "other-head"),
        ("head_sha", "b" * 40),
        ("lifecycle", PRLifecycle.CLOSED),
    ],
)
async def test_pr_changed_during_failure_comment_gate_refuses_before_post(field, value):
    parts = await setup(monitor=FakeCIMonitor(passed=False), bound=0)
    owner, _, _, creator, *_ = parts

    class ChangedPR(PassThroughGate):
        async def gate(self, **kwargs):
            if kwargs["destination"] is OutboundDestination.PR_COMMENT:
                current = owner._pr_state_reader.records[(REPO, 1)]
                wire = current.model_dump()
                wire[field] = value
                owner._pr_state_reader.records[(REPO, 1)] = PRState.model_validate(wire)
            return await super().gate(**kwargs)

    owner._gate = ChangedPR()
    with pytest.raises(PRStateReadError):
        await deliver(parts)
    assert not [call for call in creator.calls if call["method"] == "comment_on_pr"]


@pytest.mark.parametrize("branch", [HEAD, BASE])
async def test_ref_removed_while_pr_content_is_gated_refuses_publication(branch):
    parts = await setup()
    owner, _, _, creator, *_ = parts

    class RefRemoved(PassThroughGate):
        async def gate(self, **kwargs):
            owner._git._remote_branch_shas[branch] = None
            return await super().gate(**kwargs)

    owner._gate = RefRemoved()
    error = DeliveryHeadError if branch == HEAD else BaseResolutionError
    with pytest.raises(error):
        await deliver(parts)
    assert creator.calls == []


@pytest.mark.parametrize("bound", [1, 2, 3])
async def test_n_plus_one_lanes_share_the_configured_watch_bound(bound):
    from kodezart.types.domain.criteria import TrackerCriterionSet

    entered = asyncio.Event()
    release = asyncio.Event()

    class Overlapping(FakeCIMonitor):
        def __init__(self):
            super().__init__()
            self.active = self.peak = 0

        async def wait_for_checks(self, *, repo_url, ref):
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active == bound:
                entered.set()
            try:
                await release.wait()
                return await super().wait_for_checks(repo_url=repo_url, ref=ref)
            finally:
                self.active -= 1

    monitor = Overlapping()
    parts = await setup(monitor=monitor, watches=bound)
    owner, initial, context, *_ = parts
    snapshots = {}
    states = []
    for i in range(bound + 1):
        key = f"lane/{i}"
        criterion = (
            initial["criterion_set"]
            .criteria[0]
            .model_copy(update={"id": f"{key}/criterion"})
        )
        snapshot = TrackerCriterionSet(criteria=[criterion])
        snapshots[key] = snapshot
        state = dict(initial)
        state.update(
            issue_key=key,
            feature_branch=key,
            fire_spec=initial["fire_spec"].model_copy(
                update={"subject": key, "criteria": (criterion.id,)}
            ),
            criterion_set=snapshot,
        )
        owner._git._remote_branch_shas[key] = SHA
        states.append(state)

    class CurrentCriteria:
        async def read_current(self, *, spec, held=None):
            return snapshots[spec.subject]

    owner._criteria_reader = CurrentCriteria()

    class UniquePRs(FakePRCreator):
        async def create_pr(self, **kwargs):
            await super().create_pr(**kwargs)
            number = len(self.calls)
            record = pr_identity(head=kwargs["head"], number=number)
            owner._pr_state_reader.records[(REPO, number)] = record
            return record.url, number

    owner._pr_creator = UniquePRs()
    # These lanes are the subject, and each is driven through the shared
    # driver with its own state, so each carries the driver's deadline — a
    # lane that reaches the watch and then stalls reds here rather than
    # hanging the module run — and the driver's claims; the write-set claim
    # is made once around them all as well.
    unwritten = nothing_written(parts[6])
    tasks = [
        asyncio.create_task(deliver((owner, state, context, *parts[3:])))
        for state in states
    ]
    await asyncio.wait_for(entered.wait(), timeout=WAIT_BOUND)
    assert monitor.peak == bound
    release.set()
    results = await asyncio.gather(*tasks)
    assert {result.lane_key for result in results} == set(snapshots)
    assert all(result.outcome is WorkflowOutcome.ci_passed for result in results)
    assert monitor.peak == bound and monitor.active == 0
    assert unwritten()
