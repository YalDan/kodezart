"""One scheduled tick: the gate, then the real dispatcher behind it.

Nothing is stubbed between the tick and the queue.  ``GatedDispatchPass``
is driven over the shipped ``PassGate`` and the shipped ``FireDispatcher``,
so "the pass ran" is observed as a job on the queue and "the pass was
skipped" as an untouched dispatcher — not as a call count on a double
standing in for the thing under test.

The second half drives ``build_dispatch_passes`` — the composition root's
own builder — and asserts that what boot hands the scheduler is this same
object, one per declared repository, carrying the configured cadence.
"""

import asyncio
import inspect
import io
import re
import tokenize
from collections.abc import Callable
from types import ModuleType
from typing import Final

import pytest
import structlog.testing

from kodezart.adapters.no_forge_delivery import NoForgeDeliveryProbe
from kodezart.composition.passes import (
    build_dispatch_passes,
    delivery_probe_for,
    fire_report,
)
from kodezart.core.config import AppConfig
from kodezart.core.errors import McpCredentialRefusedError
from kodezart.domain.git_url import extract_owner_repo
from kodezart.services import dispatch_pass as dispatch_pass_module
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.claim_heartbeat import ClaimHeartbeat
from kodezart.services.dispatch_pass import GatedDispatchPass
from kodezart.services.fire_context import FireContextAssembler
from kodezart.services.fire_dispatcher import FireDispatcher
from kodezart.services.lifecycle_watcher import LifecycleWatcher
from kodezart.services.pass_gate import PassGate
from kodezart.services.run_recorder import RunRecorder
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.agent import (
    AssistantTextEvent,
    ErrorEvent,
    WorkflowCompleteEvent,
)
from kodezart.types.domain.dispatch import (
    DispatchOutcome,
    DispatchReport,
    ExclusionClause,
    PassRun,
    PassSignal,
)
from kodezart.types.domain.gating import OutboundDestination, RepoVisibility
from kodezart.types.domain.job import JobState
from kodezart.types.domain.operation import (
    CheckStep,
    DocumentEntry,
    DocumentSystem,
    Initiative,
    LifecycleStage,
    OperationConfig,
    Principal,
    PrincipalRole,
    QueueState,
    RecordDestination,
    RepoEntry,
    TeamEntry,
)
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_records import RunOutcome
from kodezart.types.domain.tracker import IssuePriority
from tests.fakes import (
    FakeDeliveryProbe,
    FakeFireReport,
    FakeGitService,
    FakeJobQueue,
    FakeRepoCache,
    FakeTrackerPort,
    PassThroughGate,
    make_tracker_issue,
)

APPROVER = "the-approver"
PRIMARY_REPO = "https://example.invalid/owner/primary"
SECOND_REPO = "https://example.invalid/owner/second"
#: A local bare repository — the sanctioned smoke origin, and the one the
#: first live run's dispatch tick died on every interval (KOD-145).
FILE_ORIGIN = "file:///tmp/fixture-origin.git"
LANE = "tracker"
TRUNK = "trunk"
REMOTE = "fixture-remote"
INTEGRATION_DIR = "/tmp/fixture-integration"
HOLDER = "pass-a"
LEASE_SECONDS = 600.0
PAGE_SIZE = 50
RATE_LIMIT_COOLDOWN_SECONDS = 1800.0
ASSET_MAX_COUNT = 20
ASSET_MAX_BYTES = 262144
ASSET_FETCH_TIMEOUT_SECONDS = 30.0
RENEWAL_FRACTION = 0.25
SETTLE_TRIES = 500
SETTLE_DELAY_SECONDS = 0.01


async def settled(condition: Callable[[], bool]) -> None:
    """Wait until *condition* holds, then let the assertions do the reporting.

    The delay is real. The lifecycle write-back awaits a logger whose
    underlying call completes on a thread-pool executor, and an
    executor-backed future is not advanced by ``asyncio.sleep(0)`` — a bare
    yield only reschedules the loop, so a busy machine reaches the
    assertions before the writes they read.
    """
    for _ in range(SETTLE_TRIES):
        if condition():
            return
        await asyncio.sleep(SETTLE_DELAY_SECONDS)


#: One board per repository, in declaration order. The first key is the one
#: ``make_tracker_issue`` puts on every fixture issue.
TEAM_KEYS: tuple[str, ...] = ("engineering", "design")
#: Ticket bodies that say which board an enqueued fire came from.
ENGINEERING_BODY = "the engineering board's work"
DESIGN_BODY = "the design board's work"


def teams_for(repos: tuple[str, ...]) -> dict[str, TeamEntry]:
    """A board per declared repository, bound to it (KOD-157).

    A single-repository operation declares no binding at all — every team
    binds to the only candidate implicitly — so the fixture exercises both
    shapes off the same argument.
    """
    return {
        key: TeamEntry(
            name=f"fixture-{key}",
            key=key[:3].upper(),
            repository=None if len(repos) == 1 else url,
        )
        for key, url in zip(TEAM_KEYS[: len(repos)], repos, strict=True)
    }


def operation_config(
    *,
    repos: tuple[str, ...] = (PRIMARY_REPO,),
    teams: dict[str, TeamEntry] | None = None,
) -> OperationConfig:
    return OperationConfig(
        operation_name="fixture",
        workspace="fixture-workspace",
        principals=[
            Principal(
                tracker_user=APPROVER,
                roles=frozenset(
                    {
                        PrincipalRole.APPROVER,
                        PrincipalRole.PRINCIPAL,
                        PrincipalRole.ASSIGNEE,
                    },
                ),
                handle="@approver",
            ),
        ],
        agent_identities=[],
        teams=teams_for(repos) if teams is None else teams,
        queue_states={member.value: f"queue:{member.value}" for member in QueueState},
        workflow_states={
            LifecycleStage.IN_PROGRESS: "In Progress",
            LifecycleStage.IN_REVIEW: "In Review",
            LifecycleStage.DONE: "Done",
        },
        repos=[
            RepoEntry(
                url=url,
                trunk=TRUNK,
                checks=[CheckStep(name="check", command="make check")],
            )
            for url in repos
        ],
        documents={
            "checkpoint": DocumentEntry(
                system=DocumentSystem.TRACKER,
                name="checkpoint",
                id="doc-1",
            ),
        },
        records={
            "fire_prep": RecordDestination(
                system=DocumentSystem.KNOWLEDGE,
                name="Run log",
                id="record-1",
                append_only=True,
            ),
        },
        knowledge={},
        endpoints={},
        initiatives=[Initiative(id="init-1")],
    )


def fire_dispatcher(
    tracker: FakeTrackerPort,
    queue: FakeJobQueue,
    *,
    dispatcher_class: type[FireDispatcher] = FireDispatcher,
) -> FireDispatcher:
    """The shipped dispatcher's wiring, over *tracker* and *queue*.

    ``dispatcher_class`` is what lets a case build the same dispatcher with
    one hop replaced: the report hop's containment is about a dispatcher
    that RAISES, and no state a real one can be driven into produces that.
    """
    return dispatcher_class(
        tracker=tracker,
        queue=queue,
        registry=queue,
        delivery=FakeDeliveryProbe(),
        operation=operation_config(),
        repo_url=PRIMARY_REPO,
        lane=LANE,
        holder=HOLDER,
        claim_lease_seconds=LEASE_SECONDS,
        query_page_size=PAGE_SIZE,
        rate_limit_cooldown_seconds=RATE_LIMIT_COOLDOWN_SECONDS,
        assembler=FireContextAssembler(
            tracker=tracker,
            gate=PassThroughGate(),
            max_count=ASSET_MAX_COUNT,
            max_bytes=ASSET_MAX_BYTES,
            fetch_timeout_seconds=ASSET_FETCH_TIMEOUT_SECONDS,
        ),
        resolver=BaseResolver(
            tracker=tracker,
            git=FakeGitService(),
            remote=REMOTE,
        ),
        cache=FakeRepoCache(),
        trunk=TRUNK,
        integration_workspace_dir=INTEGRATION_DIR,
    )


def tick(tracker: FakeTrackerPort) -> tuple[GatedDispatchPass, FakeJobQueue]:
    """The shipped tick over the shipped gate and the shipped dispatcher."""
    queue = FakeJobQueue()
    pass_ = GatedDispatchPass(
        lifecycle=LifecycleWatcher(
            recorder=RunRecorder(records={}, sinks={}),
            queue=queue,
            registry=queue,
            writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
            heartbeat=ClaimHeartbeat(
                tracker=tracker,
                holder=HOLDER,
                lease_seconds=LEASE_SECONDS,
                renewal_fraction=RENEWAL_FRACTION,
            ),
            report=FakeFireReport(),
        ),
        gate=PassGate(
            tracker=tracker,
            ledger=tracker.self_writes,
            signals=[PassSignal.approved_changed],
            team_keys=operation_config().team_keys_for_repo(PRIMARY_REPO),
            repo_urls=[PRIMARY_REPO],
            page_size=PAGE_SIZE,
        ),
        dispatcher=fire_dispatcher(tracker, queue),
    )
    return pass_, queue


async def test_a_delta_runs_the_pass_and_the_work_reaches_the_queue() -> None:
    """AC-19: something moved, so the expensive half runs and produces a job.

    The tick says it RAN: its driver tells a run from a skip by the return
    (KOD-176).
    """
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue("K-1")],
    )
    pass_, queue = tick(tracker)

    assert await pass_.run() is PassRun.RAN

    assert [lane for lane, _ in queue.submissions] == [LANE]
    assert tracker.claims["K-1"].holder == HOLDER


async def test_a_quiet_board_never_wakes_the_dispatcher() -> None:
    """AC-19: the gate is the whole cost of a tick over a board at rest.

    The tick says it SKIPPED, so its driver records no run for it (KOD-176).
    """
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue("K-1", queue_states=[QueueState.TRIAGE])],
    )
    pass_, queue = tick(tracker)

    assert await pass_.run() is PassRun.SKIPPED

    assert queue.submissions == []
    assert tracker.claims == {}
    # One scan: the gate's. The dispatcher's own query never happened.
    assert len(tracker.scans) == 1


class ReportOnlyDispatcher:
    """Answers with one prepared report and touches nothing.

    The one stand-in this module admits, and only because the shape it
    produces is one the shipped ``FireDispatcher`` cannot: the claimed
    key, the job id and the pre-claim state are written together at the
    enqueue, so no real pass can report ``fire_enqueued`` without them.
    """

    def __init__(self, report: DispatchReport) -> None:
        self._report = report

    async def run_pass(self) -> DispatchReport:
        return self._report


@pytest.mark.parametrize(
    "absent_field",
    ["claimed_issue_key", "job_id", "claimed_state_name"],
)
async def test_an_enqueue_reporting_nothing_enqueued_raises(absent_field: str) -> None:
    """The impossible shape aborts the tick instead of returning quietly.

    Returning left a fire running with no watch on it: nothing renews
    its claim and nothing puts the issue back when the run reaches no
    terminal outcome, and the tick that dropped it said nothing.  The
    three fields are optional on the report because two of the three
    outcomes claim nothing — under ``fire_enqueued`` they are not.
    """
    claimed: dict[str, str | None] = {
        "claimed_issue_key": "K-1",
        "job_id": "job-1",
        "claimed_state_name": "In Progress",
    }
    claimed[absent_field] = None
    tracker = FakeTrackerPort()
    queue = FakeJobQueue()
    lifecycle = LifecycleWatcher(
        recorder=RunRecorder(records={}, sinks={}),
        queue=queue,
        registry=queue,
        writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
        heartbeat=ClaimHeartbeat(
            tracker=tracker,
            holder=HOLDER,
            lease_seconds=LEASE_SECONDS,
            renewal_fraction=RENEWAL_FRACTION,
        ),
        report=FakeFireReport(),
    )
    pass_ = GatedDispatchPass(
        gate=None,
        dispatcher=ReportOnlyDispatcher(
            DispatchReport(
                outcome=DispatchOutcome.fire_enqueued,
                snapshot=(),
                exclusions=(),
                eligible=("K-1",),
                **claimed,
            ),
        ),
        lifecycle=lifecycle,
    )

    with pytest.raises(RuntimeError, match=absent_field):
        await pass_.run()

    assert lifecycle.following == frozenset()


class _FailingDispatcher:
    """A dispatcher whose pass raises before it writes anything.

    The clean-miss window the re-arm exists for: the delivery probe
    raising inside the exclusion sweep, ahead of the first claim. Every
    other failure arm leaves a tracker write behind that moves an
    ``updated_at`` and re-opens the delta on its own; this one leaves the
    board exactly as the gate found it.
    """

    def __init__(
        self,
        *,
        block: asyncio.Event | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: int = 0
        self._block: asyncio.Event | None = block
        self._error: Exception = (
            TimeoutError("the delivery probe could not be reached")
            if error is None
            else error
        )

    async def run_pass(self) -> DispatchReport:
        self.calls += 1
        if self._block is not None:
            await self._block.wait()
        raise self._error


def failing_tick(
    tracker: FakeTrackerPort,
    *,
    block: asyncio.Event | None = None,
    error: Exception | None = None,
) -> tuple[GatedDispatchPass, PassGate, _FailingDispatcher]:
    """The shipped tick and gate, over a pass that cannot finish."""
    queue = FakeJobQueue()
    guard = PassGate(
        tracker=tracker,
        ledger=tracker.self_writes,
        signals=[PassSignal.approved_changed],
        team_keys=operation_config().team_keys_for_repo(PRIMARY_REPO),
        repo_urls=[PRIMARY_REPO],
        page_size=PAGE_SIZE,
    )
    dispatcher = _FailingDispatcher(block=block, error=error)
    return (
        GatedDispatchPass(
            lifecycle=LifecycleWatcher(
                recorder=RunRecorder(records={}, sinks={}),
                queue=queue,
                registry=queue,
                writer=TrackerLifecycleWriter(
                    tracker=tracker,
                    gate=PassThroughGate(),
                ),
                heartbeat=ClaimHeartbeat(
                    tracker=tracker,
                    holder=HOLDER,
                    lease_seconds=LEASE_SECONDS,
                    renewal_fraction=RENEWAL_FRACTION,
                ),
                report=FakeFireReport(),
            ),
            gate=guard,
            dispatcher=dispatcher,
        ),
        guard,
        dispatcher,
    )


class TestAFailedPassGivesTheWakeUpBack:
    """A window the gate opened and the pass never read is re-read (KOD-164).

    At the shipped ``dispatch_pass_gate_signals`` default the gate carries
    one signal, so a burned mark is the whole wake-up: every later tick
    reports what a quiet board reports, and the approved issue waits for
    something else to touch it.
    """

    async def test_a_pass_that_raises_leaves_every_mark_at_its_pre_tick_value(
        self,
    ) -> None:
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
        pass_, guard, _ = failing_tick(tracker)

        with pytest.raises(TimeoutError):
            await pass_.run()

        assert guard.mark(PassSignal.approved_changed, container=TEAM_KEYS[0]) is None

    async def test_the_next_tick_reports_the_same_delta(self) -> None:
        """Re-armed means re-asked: the second query opens the same window."""
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
        pass_, _, dispatcher = failing_tick(tracker)

        for _ in range(2):
            with pytest.raises(TimeoutError):
                await pass_.run()

        assert dispatcher.calls == 2, "the second tick reached the pass again"
        assert [scan.updated_since for scan in tracker.scans] == [None, None]

    async def test_a_refused_credential_gives_the_wake_up_back_too(self) -> None:
        """A refused credential is a failed pass, and the window comes back.

        A credential refusal beneath the gate is a pass that read nothing,
        so the window it was woken for is owed back exactly as a transport
        timeout's is — and the refusal itself still leaves the tick, where
        the scheduler names it.
        """
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
        pass_, guard, _ = failing_tick(
            tracker,
            error=McpCredentialRefusedError(
                "the server refused the credential",
                server_name="linear",
                tool_name="get_issue",
            ),
        )

        with pytest.raises(McpCredentialRefusedError):
            await pass_.run()

        assert guard.mark(PassSignal.approved_changed, container=TEAM_KEYS[0]) is None

    async def test_a_tick_that_completed_still_advances_its_mark(self) -> None:
        """The paired positive: only a failure gives the window back."""
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
        pass_, queue = tick(tracker)

        await pass_.run()

        assert len(queue.submissions) == 1
        assert tracker.scans[-1].updated_since is None
        await pass_.run()
        assert tracker.scans[-1].updated_since is not None

    async def test_a_tick_cancelled_on_its_budget_keeps_its_window_too(
        self,
    ) -> None:
        """A timed-out pass may not eat the wake-up either.

        Driven as a real cancellation — the scheduler abandons a tick that
        outran its timeout — rather than as a raised ``CancelledError``,
        because what has to survive is the unwind and not an exception
        type.
        """
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
        pass_, guard, dispatcher = failing_tick(tracker, block=asyncio.Event())

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(pass_.run(), timeout=SETTLE_DELAY_SECONDS)

        assert dispatcher.calls == 1, "the pass was entered and then abandoned"
        assert guard.mark(PassSignal.approved_changed, container=TEAM_KEYS[0]) is None


async def test_a_second_tick_over_an_unchanged_board_costs_one_query() -> None:
    """The mark carries between ticks, so a settled board stops waking."""
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue("K-1")],
    )
    pass_, queue = tick(tracker)
    await pass_.run()
    scans_after_work = len(tracker.scans)

    await pass_.run()

    assert len(tracker.scans) == scans_after_work + 1
    assert len(queue.submissions) == 1


async def test_the_root_builds_one_gated_pass_per_declared_repository() -> None:
    """AC-20: every repository the operation acts on gets its own pass."""
    tracker = FakeTrackerPort()
    queue = FakeJobQueue()
    built = await build_dispatch_passes(
        recorder=RunRecorder(records={}, sinks={}),
        config=AppConfig(),
        operation=operation_config(repos=(PRIMARY_REPO, SECOND_REPO)),
        tracker=tracker,
        ledger=tracker.self_writes,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    assert [entry.name for entry in built.passes] == [
        f"dispatch:{PRIMARY_REPO}",
        f"dispatch:{SECOND_REPO}",
    ]
    assert all(
        isinstance(entry.run.__self__, GatedDispatchPass) for entry in built.passes
    )


async def test_an_issue_only_fires_into_the_repository_its_team_is_bound_to() -> None:
    """KOD-157: two teams, two repositories, and no crossing.

    The failure this asserts against is the shape as shipped: every pass
    scanned EVERY declared team, so the pool each repository's tick chose
    from was the union of both boards and an issue went wherever the
    first tick reached it.  ``K-DES`` outranks ``K-ENG``, so under that
    shape the primary repository's tick claims the design board's issue
    outright — a fact, not a draw.
    """
    tracker = FakeTrackerPort(
        issues=[
            make_tracker_issue(
                "K-ENG",
                team_key="engineering",
                body=ENGINEERING_BODY,
            ),
            make_tracker_issue(
                "K-DES",
                team_key="design",
                body=DESIGN_BODY,
                priority=IssuePriority.URGENT,
            ),
        ],
    )
    queue = FakeJobQueue()
    built = await build_dispatch_passes(
        recorder=RunRecorder(records={}, sinks={}),
        config=AppConfig(),
        operation=operation_config(repos=(PRIMARY_REPO, SECOND_REPO)),
        tracker=tracker,
        ledger=tracker.self_writes,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    await built.passes[0].run()
    await built.passes[1].run()

    # The body is what identifies the issue inside the fire: it is the one
    # part of the request that came from the ticket the pass claimed.
    fired = [
        (request.repo_url, ENGINEERING_BODY in request.prompt)
        for _, request in queue.submissions
    ]
    assert fired == [(PRIMARY_REPO, True), (SECOND_REPO, False)]
    assert DESIGN_BODY in queue.submissions[1][1].prompt


async def test_a_repository_no_team_is_bound_to_gets_a_named_skip() -> None:
    """The other arm: no pass, and the log says which repository, once.

    A tick over a repository nobody fires into scans nothing every
    interval forever, which is noise rather than coverage — but a
    schedule that is silently one pass short is worse, so the state is
    named at build.
    """
    tracker = FakeTrackerPort()
    queue = FakeJobQueue()
    with structlog.testing.capture_logs() as logs:
        built = await build_dispatch_passes(
            recorder=RunRecorder(records={}, sinks={}),
            config=AppConfig(),
            operation=operation_config(
                repos=(PRIMARY_REPO, SECOND_REPO),
                teams={
                    "engineering": TeamEntry(
                        name="fixture-engineering",
                        key="ENG",
                        repository=PRIMARY_REPO,
                    ),
                },
            ),
            tracker=tracker,
            ledger=tracker.self_writes,
            delivery=FakeDeliveryProbe(),
            queue=queue,
            registry=queue,
            gate=PassThroughGate(),
            git=FakeGitService(),
            cache=FakeRepoCache(),
            integration_workspace_dir=INTEGRATION_DIR,
        )

    assert [entry.name for entry in built.passes] == [f"dispatch:{PRIMARY_REPO}"]
    assert [
        entry["repo_url"]
        for entry in logs
        if entry["event"] == "dispatch_pass_unbound_repository"
    ] == [SECOND_REPO]


async def test_the_root_gives_every_pass_the_configured_cadence() -> None:
    """AC-20: ``dispatch_pass_interval_seconds`` has a real consumer."""
    unusual = 41.0
    config = AppConfig(dispatch_pass_interval_seconds=unusual)
    assert (
        config.dispatch_pass_interval_seconds
        != AppConfig().dispatch_pass_interval_seconds
    )

    tracker = FakeTrackerPort()
    queue = FakeJobQueue()
    built = await build_dispatch_passes(
        recorder=RunRecorder(records={}, sinks={}),
        config=config,
        operation=operation_config(repos=(PRIMARY_REPO, SECOND_REPO)),
        tracker=tracker,
        ledger=tracker.self_writes,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    assert [entry.interval_seconds for entry in built.passes] == [unusual, unusual]


async def test_the_root_gives_every_pass_the_configured_budget() -> None:
    """``dispatch_pass_timeout_seconds`` has a real consumer.

    Every dispatch row, not one of them: an unbounded tick anywhere in
    the schedule is a loop that can stall forever, and the value is
    unlike the default and unlike the cadence beside it, so a row wired
    to either would fail here.
    """
    unusual = 37.0
    config = AppConfig(dispatch_pass_timeout_seconds=unusual)
    assert (
        config.dispatch_pass_timeout_seconds
        != AppConfig().dispatch_pass_timeout_seconds
    )
    assert config.dispatch_pass_timeout_seconds != (
        config.dispatch_pass_interval_seconds
    )

    tracker = FakeTrackerPort()
    queue = FakeJobQueue()
    built = await build_dispatch_passes(
        recorder=RunRecorder(records={}, sinks={}),
        config=config,
        operation=operation_config(repos=(PRIMARY_REPO, SECOND_REPO)),
        tracker=tracker,
        ledger=tracker.self_writes,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    assert [entry.timeout_seconds for entry in built.passes] == [unusual, unusual]


async def test_a_pass_the_root_built_dispatches_the_repository_it_names() -> None:
    """AC-20: the built object is wired, not merely shaped like one."""
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue("K-1")],
    )
    queue = FakeJobQueue()
    built = await build_dispatch_passes(
        recorder=RunRecorder(records={}, sinks={}),
        config=AppConfig(),
        operation=operation_config(repos=(SECOND_REPO,)),
        tracker=tracker,
        ledger=tracker.self_writes,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    await built.passes[0].run()

    assert len(queue.submissions) == 1
    lane, request = queue.submissions[0]
    assert lane == AppConfig().dispatch_lane
    assert request.repo_url == SECOND_REPO
    assert tracker.claims["K-1"].holder == AppConfig().dispatch_holder


async def test_a_pass_the_root_built_follows_the_run_it_enqueued() -> None:
    """The lifecycle write-back is reached from the composition root.

    Nothing here calls ``TrackerLifecycleWriter``.  The only input is a
    dispatch pass built by ``build_dispatch_passes`` running over a queue
    whose job emits a completed run, and the observable is the transition
    on the tracker — so a root that constructs the writer without wiring
    it fails this.
    """
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue("K-1")],
    )
    queue = FakeJobQueue(
        events=[
            WorkflowCompleteEvent(
                feature_branch="feature",
                ralph_branch="ralph",
                total_iterations=1,
                accepted=True,
                outcome=WorkflowOutcome.ci_passed,
                merged=True,
            ),
        ],
    )
    built = await build_dispatch_passes(
        recorder=RunRecorder(records={}, sinks={}),
        config=AppConfig(),
        operation=operation_config(),
        tracker=tracker,
        ledger=tracker.self_writes,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    await built.passes[0].run()
    # The write-back runs in a background watch, so the test waits for the
    # terminal chain it asserts on: the DONE transition, then the comment
    # that ``LifecycleWatcher`` posts after it.
    await settled(
        lambda: (
            ("K-1", LifecycleStage.DONE) in tracker.workflow_writes
            and bool(tracker.comments)
        ),
    )

    assert queue.attached == ["job-0001"]
    assert tracker.workflow_writes == [
        ("K-1", LifecycleStage.IN_PROGRESS),
        ("K-1", LifecycleStage.DONE),
    ]
    assert tracker.queue_writes == [("K-1", QueueState.DONE)]
    assert [comment.issue_key for comment in tracker.comments] == ["K-1"]


async def test_a_run_that_died_is_reported_into_the_pass_that_fired_it() -> None:
    """The watch-to-dispatcher fan-out is reached from the composition root.

    Nothing here calls ``record_run_outcome``.  The only input is a
    dispatch pass built by ``build_dispatch_passes`` over a queue whose job
    dies on a rate-limit rejection, and the observable is the NEXT tick:
    the issue excluded under the failed-run clause carrying the class the
    run died of, and no second fire (KOD-174).  A root that built the
    watcher without its report, or the dispatchers after it, fires the
    issue again here.

    The pass is UNGATED — the shipped empty-signals configuration — so the
    second tick is the pass deciding, not the gate finding a quiet board
    and skipping it.  The job is terminal and the claim released by then,
    exactly as after a real run, so the live-run clause has nothing to
    say and the exclusion can only be the remembered one.
    """
    tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
    queue = FakeJobQueue(
        events=[
            AssistantTextEvent(text="working", model="fixture-model"),
            ErrorEvent(
                error="rate limited",
                error_kind="RateLimitedSoftFailureError",
                raise_site="acceptance_criteria",
            ),
        ],
    )
    built = await build_dispatch_passes(
        recorder=RunRecorder(records={}, sinks={}),
        config=AppConfig(dispatch_pass_gate_signals=[]),
        operation=operation_config(),
        tracker=tracker,
        ledger=tracker.self_writes,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    await built.passes[0].run()
    with structlog.testing.capture_logs() as logs:
        await built.lifecycle.drain()
        queue.mark("job-0001", JobState.TERMINAL)
        await built.passes[0].run()

    assert queue.attached == ["job-0001"]
    assert len(queue.submissions) == 1, "the whole run is not fired again"
    assert [
        (entry["issue_key"], entry["outcome"], entry["failure_class"])
        for entry in logs
        if entry["event"] == "dispatch_run_failed_remembered"
    ] == [("K-1", RunOutcome.FAILED.value, "RateLimitedSoftFailureError")]
    assert [
        entry["exclusions"]
        for entry in logs
        if entry["event"] == "dispatch_empty_eligible_set"
    ] == [[{"issueKey": "K-1", "clause": ExclusionClause.RUN_FAILED.value}]]


async def test_the_pass_threads_the_claimed_boards_posture_to_the_watch() -> None:
    """KOD-157: the writer gates under the posture of the winner's board.

    Asserted at the watcher boundary the way ``pre_claim_state`` is, and
    through the write the watch actually makes rather than a call count on
    a double standing in for it: the dispatcher is the only component that
    knows the claimed issue's team, and the gated write that needs its
    posture is three hops downstream.
    """
    tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
    queue = FakeJobQueue(
        events=[
            WorkflowCompleteEvent(
                feature_branch="feature",
                ralph_branch="ralph",
                total_iterations=1,
                accepted=True,
                outcome=WorkflowOutcome.ci_passed,
                merged=True,
            ),
        ],
    )
    gate = PassThroughGate()
    built = await build_dispatch_passes(
        recorder=RunRecorder(records={}, sinks={}),
        config=AppConfig(),
        operation=operation_config(
            teams={
                "engineering": TeamEntry(
                    name="fixture-engineering",
                    key="ENG",
                    visibility=RepoVisibility.PRIVATE,
                ),
            },
        ),
        tracker=tracker,
        ledger=tracker.self_writes,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=gate,
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    await built.passes[0].run()
    await settled(lambda: bool(tracker.comments))

    assert [
        visibility
        for (_, visibility, _), destination in zip(
            gate.calls,
            gate.destinations,
            strict=True,
        )
        if destination is OutboundDestination.TRACKER_COMMENT
    ] == [RepoVisibility.PRIVATE]


class ForgeOnlyDeliveryProbe:
    """A probe that parses the URL first, exactly as the forge client does.

    ``GitHubAPIClient.open_delivery_exists`` opens with
    ``extract_owner_repo(repo_url)``, so this stands in for it by calling
    the SAME function rather than by counting calls: a composition that
    hands a forge-less origin to the forge probe fails here by raising the
    error the first live run crash-looped on, not by an assertion about
    what was wired (KOD-145).
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def open_delivery_exists(self, *, repo_url: str, issue_key: str) -> bool:
        extract_owner_repo(repo_url)
        self.calls.append(issue_key)
        return False


async def test_a_pass_over_a_forge_less_origin_completes_its_tick() -> None:
    """Boot 25 as a fixture: the crash-loop, reproduced and no longer fatal.

    The scheduler ticked every 300 seconds for half an hour and every tick
    died identically at the eligibility phase — ``ValueError: Cannot
    extract owner/repo from file:// URL`` — before any claim was
    attempted.  The service stayed healthy while its one purpose
    crash-looped.

    A local bare origin is the sanctioned smoke shape, so the issue is
    ELIGIBLE here: no open pull request delivers it, because on this
    origin none can exist.  The forge probe is never asked (KOD-145).
    """
    tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
    queue = FakeJobQueue()
    forge = ForgeOnlyDeliveryProbe()
    built = await build_dispatch_passes(
        recorder=RunRecorder(records={}, sinks={}),
        config=AppConfig(),
        operation=operation_config(repos=(FILE_ORIGIN,)),
        tracker=tracker,
        ledger=tracker.self_writes,
        delivery=forge,
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    await built.passes[0].run()

    assert len(queue.submissions) == 1
    _, request = queue.submissions[0]
    assert request.repo_url == FILE_ORIGIN
    assert tracker.claims["K-1"].holder == AppConfig().dispatch_holder
    assert forge.calls == []


async def test_a_pass_over_a_forge_shaped_origin_still_asks_the_forge() -> None:
    """The other arm: selection, not removal. A forge origin keeps its probe."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
    queue = FakeJobQueue()
    forge = ForgeOnlyDeliveryProbe()
    built = await build_dispatch_passes(
        recorder=RunRecorder(records={}, sinks={}),
        config=AppConfig(),
        operation=operation_config(repos=(PRIMARY_REPO,)),
        tracker=tracker,
        ledger=tracker.self_writes,
        delivery=forge,
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    await built.passes[0].run()

    assert forge.calls == ["K-1"]
    assert len(queue.submissions) == 1


def test_each_repository_gets_the_probe_its_own_origin_can_answer() -> None:
    """One operation, two origins, two probes — the selection is per repo."""
    forge = ForgeOnlyDeliveryProbe()

    assert delivery_probe_for(PRIMARY_REPO, forge=forge) is forge
    assert isinstance(
        delivery_probe_for(FILE_ORIGIN, forge=forge),
        NoForgeDeliveryProbe,
    )


class RefusingDispatcher(FireDispatcher):
    """A dispatcher whose report hop raises, the tracker behind it refusing.

    ``record_run_outcome``'s first act is a tracker read, and a live boot
    met a server answering every read with a refused credential (KOD-171).
    Under the fan-out that is one dispatcher raising inside a loop over
    all of them.
    """

    async def record_run_outcome(
        self,
        issue_key: str,
        outcome: RunOutcome,
        failure_class: str | None,
    ) -> None:
        raise McpCredentialRefusedError(
            "unauthorized",
            server_name="linear",
            tool_name="get_issue",
        )


async def test_one_dispatcher_refusing_the_report_still_reaches_the_others() -> None:
    """The fan-out is contained per dispatcher (KOD-276).

    The refusing dispatcher is FIRST in the mapping, so an uncontained
    loop never reaches the one that actually fired the run — and that one
    is the only dispatcher whose memory decides whether the issue may be
    selected again.  Its next pass is the observable: an excluded issue
    means the news arrived, and a re-fire means it did not.
    """
    tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
    queue = FakeJobQueue()
    refusing = fire_dispatcher(tracker, queue, dispatcher_class=RefusingDispatcher)
    hearing = fire_dispatcher(tracker, queue)
    first = await hearing.run_pass()
    assert first.outcome is DispatchOutcome.fire_enqueued
    report = fire_report({"repo-refusing": refusing, "repo-hearing": hearing})

    with structlog.testing.capture_logs() as logs:
        await report("K-1", RunOutcome.FAILED, "RateLimitedSoftFailureError")

    assert [
        (entry["dispatcher"], entry["issue_key"], entry["error_type"])
        for entry in logs
        if entry["event"] == "fire_report_failed"
    ] == [("repo-refusing", "K-1", "McpCredentialRefusedError")]
    second = await hearing.run_pass()
    assert second.outcome is DispatchOutcome.empty_eligible_set
    assert [
        (item.issue_key, item.clause, item.detail) for item in second.exclusions
    ] == [("K-1", ExclusionClause.RUN_FAILED, "RateLimitedSoftFailureError")]
    assert len(queue.submissions) == 1, "the whole run is not fired again"


async def test_a_fan_out_no_dispatcher_refuses_names_no_failure() -> None:
    """The paired positive: containment is not a silence."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
    queue = FakeJobQueue()
    hearing = fire_dispatcher(tracker, queue)
    await hearing.run_pass()
    report = fire_report({"repo-hearing": hearing})

    with structlog.testing.capture_logs() as logs:
        await report("K-1", RunOutcome.FAILED, "RateLimitedSoftFailureError")

    assert [entry for entry in logs if entry["event"] == "fire_report_failed"] == []
    second = await hearing.run_pass()
    assert [item.clause for item in second.exclusions] == [ExclusionClause.RUN_FAILED]


#: Every cardinal that could name a member roster's size in prose.  ``one``
#: is absent on purpose: a comment saying a pass leaves ONE run running is
#: a statement about the outcome, not a count of the outcomes.
ROSTER_CARDINALS: Final[frozenset[str]] = frozenset(
    {
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
    },
)


def _comment_words(module: ModuleType) -> frozenset[str]:
    """Every word appearing in *module*'s ``#`` comments, lowercased."""
    source = io.StringIO(inspect.getsource(module))
    return frozenset(
        word
        for token in tokenize.generate_tokens(source.readline)
        if token.type is tokenize.COMMENT
        for word in re.findall(r"[a-z]+", token.string.lower())
    )


def test_the_dispatch_pass_comments_count_no_outcome_members() -> None:
    """The comment states the deciding property, never the roster (KOD-279).

    Measured: the watch-start comment said ``fire_enqueued`` was "the only
    outcome of the four", and the enum had carried five members since
    ``winner_blocked`` was added (KOD-173).  A count of another
    declaration's members is a fact this module does not own — it goes
    stale silently, and only a sweep sees it, because no behaviour changes
    when it does.
    """
    assert _comment_words(dispatch_pass_module) & ROSTER_CARDINALS == frozenset()


def test_the_outcome_enum_counts_its_own_members_nowhere() -> None:
    """The same defect at its source, where the roster is declared.

    The docstring over the members is where a count is most tempting and
    least visible: it reads as a definition, and it is the one place the
    roster is already stated exhaustively by the code beneath it.
    """
    docstring = DispatchOutcome.__doc__
    assert docstring is not None
    words = frozenset(re.findall(r"[a-z]+", docstring.lower()))

    assert words & ROSTER_CARDINALS == frozenset()
