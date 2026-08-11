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
from kodezart.types.domain.agent import (
    AgentEvent,
    WorkflowCompleteEvent,
    WorkflowPREvent,
)
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.outcome import WorkflowOutcome
from tests.fakes import (
    FakeDeliveryProbe,
    FakeGitService,
    FakeRepoCache,
    FakeTrackerPort,
    PassThroughGate,
    approved_by,
    make_tracker_issue,
)
from tests.services.test_dispatch_pass import (
    APPROVER,
    INTEGRATION_DIR,
    operation_config,
)

ISSUE = "K-1"
FEATURE_BRANCH = "kodezart/k-1"


class _MergingEngine:
    """A ``WorkflowEngine`` whose run opens a pull request and merges.

    It records the base it was asked to build on, so the test can assert
    that the base the dispatcher resolved is the base the RUN received.
    """

    def __init__(self) -> None:
        self.base_branches: list[str] = []
        self.base_specs: list[BaseSpec] = []

    def run(self, **kwargs: object) -> AsyncIterator[AgentEvent]:
        spec = kwargs["base_spec"]
        assert isinstance(spec, BaseSpec)
        self.base_specs.append(spec)
        self.base_branches.append(spec.base_branch)
        return self._frames(spec.base_branch)

    async def _frames(self, base: str) -> AsyncIterator[AgentEvent]:
        await asyncio.sleep(0)
        yield WorkflowPREvent(
            pr_url="https://forge.invalid/owner/primary/pull/1",
            pr_number=1,
            feature_branch=FEATURE_BRANCH,
            base_branch=base,
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


async def _until(condition: object, *, tries: int = 500) -> None:
    """Yield to the loop until *condition* holds, or give the test its failure."""
    assert callable(condition)
    for _ in range(tries):
        if condition():
            return
        await asyncio.sleep(0)


async def test_an_approved_issue_walks_the_whole_chain_back_to_its_ticket() -> None:
    config = AppConfig()
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue(ISSUE)],
        provenance=dict([approved_by(ISSUE, APPROVER)]),
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
        passes = build_dispatch_passes(
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

        await passes[0].run()
        await _until(lambda: bool(tracker.comments))

        # 1. the claim was granted, to this deployment's configured holder
        assert tracker.claims[ISSUE].holder == config.dispatch_holder

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
        passes = build_dispatch_passes(
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

        await passes[0].run()
        for _ in range(64):
            await asyncio.sleep(0)

        assert tracker.claims == {}
        assert tracker.comments == []
        assert QueueState.APPROVED not in {state for _, state in tracker.queue_writes}
    finally:
        await queue.stop()
