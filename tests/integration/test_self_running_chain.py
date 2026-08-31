"""AC-23 (b): the self-running chain, composed, end to end, in one test.

The chain the README's smoke test asks an operator to watch:

    approved state present -> claim -> enqueue -> the run fires
    -> lifecycle write-back -> terminal outcome comment

Nothing between the tracker and the tracker is a stand-in for the thing
under test.  The passes come from ``build_dispatch_passes`` — the
composition root's own builder — the queue is the shipped
``AsyncioJobQueue``, and the lifecycle writes are observed on the tracker
the port writes to.  Two things are doubles and both are named: the
tracker itself (no live workspace in CI, ever) and the workflow engine,
because running a real agent is not what this test is about.

The approver flip is FIXTURE STATE and is never performed by the system
under test.  Per KOD-62 R1(c) that is the whole reason this test is the
weaker half of AC-23: a test that performed the one act the system must
never perform would be a contradiction with a green tick, not evidence.
The live half stays an operator run.
"""

import asyncio
from collections.abc import AsyncIterator

from kodezart.adapters.asyncio_job_queue import AsyncioJobQueue
from kodezart.composition.passes import build_dispatch_passes
from kodezart.core.config import AppConfig
from kodezart.core.errors import NoStructuredOutputError
from kodezart.types.domain.agent import (
    AgentEvent,
    AssistantTextEvent,
    WorkflowCompleteEvent,
    WorkflowPREvent,
)
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import (
    FakeDeliveryProbe,
    FakeGitService,
    FakeRepoCache,
    FakeTrackerPort,
    PassThroughGate,
    make_tracker_issue,
)
from tests.services.test_dispatch_pass import (
    INTEGRATION_DIR,
    operation_config,
)

ISSUE = "K-1"
FEATURE_BRANCH = "kodezart/k-1"
FEATURE_TIP_SHA = "a" * 40
#: A state the operation's ``workflow_states`` mapping never names — the
#: shape a claimed issue really has, and the one no ``LifecycleStage``
#: could put back.
PRE_CLAIM_STATE = "Backlog"
CREATOR_FAILURE = "Creator produced no structured output."

#: Generous: every wait here is on a condition, so this only ever bounds
#: a genuine hang.
SETTLE_TIMEOUT = 5.0


class _MergingEngine:
    """A ``WorkflowEngine`` whose run opens a pull request and merges.

    It records the base it was asked to build on, so the test can assert
    that the base the dispatcher resolved is the base the RUN received.
    """

    def __init__(self) -> None:
        self.base_branches: list[str] = []

    def run(self, **kwargs: object) -> AsyncIterator[AgentEvent]:
        base = kwargs["base_branch"]
        assert isinstance(base, str)
        self.base_branches.append(base)
        return self._frames(base)

    async def _frames(self, base: str) -> AsyncIterator[AgentEvent]:
        await asyncio.sleep(0)
        yield WorkflowPREvent(
            pr_url="https://forge.invalid/owner/primary/pull/1",
            pr_number=1,
            feature_branch=FEATURE_BRANCH,
            base_branch=base,
            feature_tip_sha=FEATURE_TIP_SHA,
        )
        yield WorkflowCompleteEvent(
            feature_branch=FEATURE_BRANCH,
            ralph_branch="kodezart/k-1-ralph",
            total_iterations=1,
            accepted=True,
            outcome=WorkflowOutcome.ci_passed,
            merged=True,
            pr_url="https://forge.invalid/owner/primary/pull/1",
            pr_number=1,
            ci_passed=True,
        )


async def _until(condition: object, *, timeout: float = SETTLE_TIMEOUT) -> None:
    """Yield to the loop until *condition* holds, or fail on a real clock.

    Bounded by wall-clock rather than by a count of event-loop yields.
    Every lifecycle write is awaited through structlog's executor, so the
    number of turns one costs is a property of the machine and not of the
    code: a fixed count held here and under-ran on a slower runner, which
    is the same lesson ``tests/services/test_pass_scheduler`` records
    about its own settle.  Exhaustion now fails loudly rather than
    returning quietly into a downstream assertion.
    """
    assert callable(condition)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() >= deadline:
            msg = "condition never became true"
            raise AssertionError(msg)
        await asyncio.sleep(0)


async def test_an_approved_issue_walks_the_whole_chain_back_to_its_ticket() -> None:
    config = AppConfig()
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue(ISSUE)],
    )
    engine = _MergingEngine()
    queue = AsyncioJobQueue(
        engine=engine,
        max_concurrent_runs_per_lane=config.queue_max_concurrent_runs_per_lane,
        max_depth_per_lane=config.queue_max_depth_per_lane,
        terminal_retention_seconds=config.queue_terminal_retention_seconds,
        event_buffer_retention_seconds=config.queue_event_buffer_retention_seconds,
        event_buffer_capacity=config.queue_event_buffer_capacity,
    )
    await queue.start()
    try:
        built = await build_dispatch_passes(
            config=config,
            operation=operation_config(),
            tracker=tracker,
            delivery=FakeDeliveryProbe(),
            queue=queue,
            registry=queue,
            gate=PassThroughGate(),
            git=FakeGitService(),
            cache=FakeRepoCache(),
            integration_workspace_dir=INTEGRATION_DIR,
        )

        await built.passes[0].run()

        # 1. the claim was granted, to this deployment's configured holder.
        #    Read while the run is in flight, which is the whole window the
        #    claim is meant to cover: it is handed back at the end of it.
        assert tracker.claims[ISSUE].holder == config.dispatch_holder

        await _until(lambda: bool(tracker.comments))

        # 2. the fire reached the queue, on the base the graph implied
        assert engine.base_branches == [operation_config().repos[0].trunk]

        # 3. the lifecycle walked, in the run's own order
        assert tracker.workflow_writes == [
            (ISSUE, LifecycleStage.IN_PROGRESS),
            (ISSUE, LifecycleStage.IN_REVIEW),
            (ISSUE, LifecycleStage.DONE),
        ]
        assert tracker.queue_writes == [(ISSUE, QueueState.DONE)]

        # 4. the terminal outcome is on the ticket, naming the run's outcome
        (comment,) = tracker.comments
        assert comment.issue_key == ISSUE
        assert WorkflowOutcome.ci_passed.value in comment.body

        # 5. and the issue is claimable again the moment the run ends: the
        #    claim is handed back rather than leased out to a finished job
        await built.lifecycle.drain()
        assert await tracker.active_claim(issue_key=ISSUE) is None
    finally:
        await queue.stop()


async def test_the_chain_never_sets_the_approved_state_itself() -> None:
    """Approval is the only human act, and this asserts the system's half.

    An unapproved issue is not merely skipped — the run must be incapable of
    supplying the approval that would let it fire. Every queue-state write
    the whole chain makes is recorded, so a write of APPROVED anywhere would
    show up here.
    """
    config = AppConfig()
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue(ISSUE, queue_states=[QueueState.PROPOSED])],
    )
    queue = AsyncioJobQueue(
        engine=_MergingEngine(),
        max_concurrent_runs_per_lane=config.queue_max_concurrent_runs_per_lane,
        max_depth_per_lane=config.queue_max_depth_per_lane,
        terminal_retention_seconds=config.queue_terminal_retention_seconds,
        event_buffer_retention_seconds=config.queue_event_buffer_retention_seconds,
        event_buffer_capacity=config.queue_event_buffer_capacity,
    )
    await queue.start()
    try:
        built = await build_dispatch_passes(
            config=config,
            operation=operation_config(),
            tracker=tracker,
            delivery=FakeDeliveryProbe(),
            queue=queue,
            registry=queue,
            gate=PassThroughGate(),
            git=FakeGitService(),
            cache=FakeRepoCache(),
            integration_workspace_dir=INTEGRATION_DIR,
        )

        await built.passes[0].run()
        for _ in range(64):
            await asyncio.sleep(0)

        assert tracker.claims == {}
        assert tracker.comments == []
        assert QueueState.APPROVED not in {state for _, state in tracker.queue_writes}
    finally:
        await queue.stop()


class _CrashingEngine:
    """A ``WorkflowEngine`` that dies where the first live fire died.

    The measured shape: the run raises inside ticket generation, two
    minutes in, having produced frames but no ``WorkflowCompleteEvent``.
    """

    def run(self, **kwargs: object) -> AsyncIterator[AgentEvent]:
        return self._frames()

    async def _frames(self) -> AsyncIterator[AgentEvent]:
        await asyncio.sleep(0)
        yield AssistantTextEvent(text="drafting the ticket", model="fixture-model")
        raise NoStructuredOutputError(
            CREATOR_FAILURE,
            raise_site="ticket_creator",
            result_event=None,
        )


async def test_a_fire_that_crashes_puts_its_issue_back_and_says_why() -> None:
    """The whole chain's failure arm, over the shipped queue (KOD-146).

    Before this, the arc ended at ``lifecycle_in_progress``: the run died
    and the board asserted a run that was not running, indefinitely, with
    no record that anything had been attempted.
    """
    config = AppConfig()
    tracker = FakeTrackerPort(
        issues=[
            make_tracker_issue(
                ISSUE,
                state_name=PRE_CLAIM_STATE,
                state_kind=WorkflowStateKind.BACKLOG,
            ),
        ],
    )
    queue = AsyncioJobQueue(
        engine=_CrashingEngine(),
        max_concurrent_runs_per_lane=config.queue_max_concurrent_runs_per_lane,
        max_depth_per_lane=config.queue_max_depth_per_lane,
        terminal_retention_seconds=config.queue_terminal_retention_seconds,
        event_buffer_retention_seconds=config.queue_event_buffer_retention_seconds,
        event_buffer_capacity=config.queue_event_buffer_capacity,
    )
    await queue.start()
    try:
        built = await build_dispatch_passes(
            config=config,
            operation=operation_config(),
            tracker=tracker,
            delivery=FakeDeliveryProbe(),
            queue=queue,
            registry=queue,
            gate=PassThroughGate(),
            git=FakeGitService(),
            cache=FakeRepoCache(),
            integration_workspace_dir=INTEGRATION_DIR,
        )

        await built.passes[0].run()
        await _until(lambda: bool(tracker.comments))

        # 1. the lifecycle still walked forward while the run was live
        assert tracker.workflow_writes == [(ISSUE, LifecycleStage.IN_PROGRESS)]

        # 2. and then the issue went back to the state the pass found it in
        assert tracker.restored_states == [(ISSUE, PRE_CLAIM_STATE)]
        assert tracker.issues[ISSUE].state_name == PRE_CLAIM_STATE

        # 3. the failure is named on the ticket: class, step, job id
        (comment,) = tracker.comments
        assert comment.issue_key == ISSUE
        assert "NoStructuredOutputError" in comment.body
        assert "ticket_creator" in comment.body

        # 4. and nothing was reported as delivered
        assert tracker.queue_writes == []
        assert (ISSUE, LifecycleStage.DONE) not in tracker.workflow_writes
    finally:
        await queue.stop()
