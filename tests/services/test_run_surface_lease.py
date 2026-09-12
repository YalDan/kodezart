"""Run-owned leases through production composition and the real tracker adapter."""

import asyncio
from datetime import timedelta

import pytest

from kodezart.composition.jobs import build_job_queue
from kodezart.composition.passes import build_dispatch_passes
from kodezart.core.config import AppConfig
from kodezart.domain.errors import (
    OutboundContentBlockedError,
    SurfaceLeaseError,
    SurfaceLeaseLostError,
    SurfaceWriteAttributionError,
)
from kodezart.services.run_recorder import RunRecorder
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.agent import WorkflowCompleteEvent
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.dispatch import SelfWriteLedger
from kodezart.types.domain.gating import ContentClass, GateDecision, GateVerdict
from kodezart.types.domain.operation import OperationMemberAbsentError
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.workflow import WorkflowSubmission
from tests.fakes import (
    FakeDeliveryProbe,
    FakeGitService,
    FakeRepoCache,
    PassThroughGate,
)
from tests.services.test_dispatch_pass import INTEGRATION_DIR, operation_config
from tests.tracker.conftest import CLAIMED_ISSUE, FIXTURE_NOW, fixture_server
from tests.tracker.test_linear_mcp_tracker import tracker_over
from tests.tracker.test_ownership_arbitration import _WroteTogether

DURATION = 321.5
JOB = "actual-queue-job-id"
PREFIXES = {"run_outcome": "fixture-outcome"}
OUTCOME = WorkflowOutcome.review_passed_no_pr_adapter


def surface(marker="A", issue_key=CLAIMED_ISSUE):
    return WritableSurface(
        kind=SurfaceKind.MARKER_COMMENT,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=issue_key),
        marker=marker,
    )


class _Board:
    def __init__(self):
        self.now = FIXTURE_NOW
        self.server = fixture_server(clock=lambda: self.now + timedelta(seconds=0.5))
        self.calls = []
        self.holds_at_write = []
        self.pause = None
        self.reached = asyncio.Event()
        self.resume = asyncio.Event()
        self.after = False
        self.ledger = SelfWriteLedger()

    async def call_tool(self, *, name, arguments):
        self.calls.append((name, dict(arguments)))
        selected = self.pause is not None and self.pause(name, arguments)
        if selected and not self.after:
            self.reached.set()
            await self.resume.wait()
        if name == "save_comment" and str(arguments.get("body", "")).startswith("["):
            self.holds_at_write.append(
                tuple(c.body for c in self.server.comments if "kind: lease\n" in c.body)
            )
        result = await self.server.call_tool(name=name, arguments=arguments)
        if selected and self.after:
            self.reached.set()
            await self.resume.wait()
        return result

    def tracker(self):
        return tracker_over(
            self.server, caller=self, clock=lambda: self.now, ledger=self.ledger
        )

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)

    def grants(self):
        return [c for c in self.server.comments if "kind: lease\n" in c.body]

    def outcome_comments(self):
        return [
            c for c in self.server.comments if c.body.startswith("[fixture-outcome:")
        ]

    def lease_creations(self):
        return [
            args
            for name, args in self.calls
            if name == "save_comment"
            and "id" not in args
            and "kind: lease\n" in str(args.get("body", ""))
        ]


def writer(board, gate=None, prefixes=PREFIXES):
    return TrackerLifecycleWriter(
        tracker=board.tracker(),
        gate=PassThroughGate() if gate is None else gate,
        marker_prefixes=prefixes,
        surface_lease_seconds=DURATION,
    )


async def outcome(board, *, gate=None):
    await writer(board, gate).on_terminal_outcome(
        issue_key=CLAIMED_ISSUE, job_id=JOB, outcome=OUTCOME
    )


class _CompletedEngine:
    async def run(self, **kwargs):
        self.job_id = kwargs["cache_key"]
        yield WorkflowCompleteEvent(
            feature_branch="feature",
            ralph_branch="ralph",
            total_iterations=1,
            accepted=True,
            outcome=OUTCOME,
        )


async def test_composed_outcome_comment_is_held_by_the_actual_queue_job_id(monkeypatch):
    monkeypatch.setenv("KODEZART_TRACKER__SURFACE_LEASE_SECONDS", str(DURATION))
    monkeypatch.setenv("KODEZART_DISPATCH_HOLDER", "separate-deployment")
    config = AppConfig()
    engine = _CompletedEngine()
    queue = build_job_queue(settings=config.queue, workflow_engine=engine)
    board = _Board()
    tracker = board.tracker()
    gate = PassThroughGate()
    built = await build_dispatch_passes(
        recorder=RunRecorder(records={}, sinks={}),
        config=config,
        operation=operation_config(),
        tracker=tracker,
        ledger=board.ledger,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=gate,
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )
    await queue.start()
    try:
        record = await queue.submit(
            lane="fixture",
            request=WorkflowSubmission(
                prompt="Run the fixture",
                issue_key=CLAIMED_ISSUE,
                repo_path=None,
                repo_url=None,
                base_spec=BaseSpec(inputs=(), base_branch="trunk"),
                implied_base=None,
                scope=None,
                permission_mode=PermissionMode.INTERACTIVE,
                allowed_tools=[],
            ),
        )
        await built.lifecycle.watch(
            issue_key=CLAIMED_ISSUE,
            job_id=record.job_id,
            pre_claim_state="Backlog",
        )
        assert len(engine.job_id) == 32
        assert record.job_id == engine.job_id
        (creation,) = board.lease_creations()
        assert f"holder: {record.job_id}\n" in creation["body"]
        assert f"lease: {DURATION}\n" in creation["body"]
        renewals = [
            args["body"]
            for name, args in board.calls
            if name == "save_comment" and "since:" in str(args.get("body", ""))
        ]
        assert len(renewals) == 1
        assert f"lease: {DURATION}\n" in renewals[0]
        assert f"holder: {record.job_id}\n" in renewals[0]
        assert "separate-deployment" not in creation["body"]
        assert board.holds_at_write and all(board.holds_at_write)
        assert all(
            "state: held\n" in h for holds in board.holds_at_write for h in holds
        )
        assert record.job_id in board.outcome_comments()[0].body
        assert gate.content_classes == [ContentClass.DERIVED]
        assert not board.grants()
    finally:
        await queue.stop()
        await built.lifecycle.drain()


async def test_terminal_outcome_replay_updates_the_same_comment_and_releases():
    board = _Board()
    await outcome(board)
    first = board.outcome_comments()[0].id
    await outcome(board)
    assert [c.id for c in board.outcome_comments()] == [first]
    assert not board.grants()


async def test_missing_marker_purpose_refuses_before_any_tracker_mutation():
    board = _Board()
    with pytest.raises(OperationMemberAbsentError, match="run_outcome"):
        await writer(board, prefixes={}).on_terminal_outcome(
            issue_key=CLAIMED_ISSUE, job_id=JOB, outcome=OUTCOME
        )
    assert not board.calls


@pytest.mark.parametrize("holder", [None, "job-with-no-lease"])
async def test_unheld_marker_write_refuses_with_absent_owner(holder):
    board = _Board()
    with pytest.raises(SurfaceLeaseError) as caught:
        await board.tracker().upsert_comment(
            target=CLAIMED_ISSUE, marker="A", body="attempt", holder=holder
        )
    assert caught.value.current_holder is None
    assert caught.value.marker == "A"
    assert not board.holds_at_write


async def test_two_disjoint_sets_both_acquire_and_both_write():
    board = _Board()
    tracker = board.tracker()
    first, second = surface("A"), surface("B")
    async with (
        RunSurfaceLease(
            tracker=tracker,
            job_id="one",
            surfaces=frozenset({first}),
            lease_seconds=DURATION,
        ),
        RunSurfaceLease(
            tracker=tracker,
            job_id="two",
            surfaces=frozenset({second}),
            lease_seconds=DURATION,
        ),
    ):
        await asyncio.gather(
            tracker.upsert_comment(
                target=CLAIMED_ISSUE, marker="A", body="a", holder="one"
            ),
            tracker.upsert_comment(
                target=CLAIMED_ISSUE, marker="B", body="b", holder="two"
            ),
        )
        with pytest.raises(SurfaceLeaseError) as caught:
            await tracker.upsert_comment(
                target=CLAIMED_ISSUE, marker="A", body="intrusion", holder="two"
            )
        assert caught.value.current_holder == "one"
        with pytest.raises(SurfaceLeaseError) as caught:
            await tracker.upsert_comment(
                target=CLAIMED_ISSUE, marker="C", body="undeclared", holder="one"
            )
        assert caught.value.current_holder is None
    assert {c.body for c in board.server.comments} == {"A\na", "B\nb"}


async def test_conflicting_whole_set_has_no_partial_hold_and_does_not_retry():
    board = _Board()
    tracker = board.tracker()
    first, second = surface("A"), surface("B")
    async with RunSurfaceLease(
        tracker=tracker,
        job_id="one",
        surfaces=frozenset({first}),
        lease_seconds=DURATION,
    ):
        with pytest.raises(SurfaceLeaseError) as caught:
            async with RunSurfaceLease(
                tracker=tracker,
                job_id="two",
                surfaces=frozenset({first, second}),
                lease_seconds=DURATION,
            ):
                raise AssertionError("a conflicting writer entered")
        assert caught.value.current_holder == "one"
        async with RunSurfaceLease(
            tracker=tracker,
            job_id="three",
            surfaces=frozenset({second}),
            lease_seconds=DURATION,
        ):
            await tracker.upsert_comment(
                target=CLAIMED_ISSUE, marker="B", body="free", holder="three"
            )
    assert len(board.lease_creations()) == 3
    assert not board.grants()


async def test_expired_write_refuses_and_loss_never_reacquires():
    board = _Board()
    tracker = board.tracker()
    surfaces = frozenset({surface()})
    async with RunSurfaceLease(
        tracker=tracker, job_id=JOB, surfaces=surfaces, lease_seconds=DURATION
    ) as held:
        board.advance(DURATION + 1)
        with pytest.raises(SurfaceLeaseError) as caught:
            await tracker.upsert_comment(
                target=CLAIMED_ISSUE, marker="A", body="late", holder=JOB
            )
        assert caught.value.current_holder is None
        await tracker.acquire_surfaces(
            surfaces=surfaces, holder="successor", lease_seconds=DURATION
        )
        for _ in range(2):
            with pytest.raises(SurfaceLeaseLostError) as lost:
                await held.renew()
            assert lost.value.job_id == JOB
            assert lost.value.surfaces == surfaces
            assert not hasattr(lost.value, "current_holder")
        await tracker.upsert_comment(
            target=CLAIMED_ISSUE, marker="A", body="successor", holder="successor"
        )
    assert len(board.lease_creations()) == 2
    assert all("holder: successor\n" in c.body for c in board.grants())
    await tracker.release_surfaces(surfaces=surfaces, holder="successor")


async def test_delayed_renewal_cannot_take_back_a_successors_surface():
    board = _Board()
    tracker = board.tracker()
    surfaces = frozenset({surface()})
    async with RunSurfaceLease(
        tracker=tracker, job_id=JOB, surfaces=surfaces, lease_seconds=DURATION
    ) as held:
        board.pause = lambda name, args: (
            name == "save_comment" and "since:" in str(args.get("body", ""))
        )
        renewal = asyncio.create_task(held.renew())
        async with asyncio.timeout(10):
            await board.reached.wait()
            board.advance(DURATION + 1)
            await tracker.acquire_surfaces(
                surfaces=surfaces, holder="successor", lease_seconds=DURATION
            )
            board.resume.set()
            with pytest.raises(SurfaceLeaseLostError):
                await renewal
        await tracker.upsert_comment(
            target=CLAIMED_ISSUE, marker="A", body="successor", holder="successor"
        )
    assert len(board.lease_creations()) == 2
    assert all("holder: successor\n" in c.body for c in board.grants())
    await tracker.release_surfaces(surfaces=surfaces, holder="successor")


async def test_overlapping_acquisitions_do_not_admit_two_writers():
    board = _Board()
    tracker = tracker_over(
        board.server,
        caller=_WroteTogether(board.server, writers=2),
        clock=lambda: board.now,
    )
    attempts = asyncio.Queue()
    finish = asyncio.Event()

    async def write_as(job_id):
        try:
            async with RunSurfaceLease(
                tracker=tracker,
                job_id=job_id,
                surfaces=frozenset({surface()}),
                lease_seconds=DURATION,
            ):
                await tracker.upsert_comment(
                    target=CLAIMED_ISSUE, marker="A", body=job_id, holder=job_id
                )
                await attempts.put("held")
                await finish.wait()
        except SurfaceLeaseError:
            await attempts.put("refused")

    tasks = [asyncio.create_task(write_as(job_id)) for job_id in ("one", "two")]
    try:
        async with asyncio.timeout(10):
            observed = [await attempts.get(), await attempts.get()]
        assert observed.count("held") <= 1
    finally:
        finish.set()
        await asyncio.gather(*tasks)
    assert not board.grants()


class _ExpiredGate(PassThroughGate):
    def __init__(self, board):
        super().__init__()
        self.board = board

    async def gate(self, **kwargs):
        self.board.advance(DURATION + 1)
        return await super().gate(**kwargs)


async def test_terminal_comment_renews_after_gate_and_stops_on_loss():
    board = _Board()
    with pytest.raises(SurfaceLeaseLostError):
        await outcome(board, gate=_ExpiredGate(board))
    assert not board.outcome_comments()
    assert not board.grants()
    assert len(board.lease_creations()) == 1


class _BlockedGate:
    async def gate(self, **kwargs):
        return GateDecision(verdict=GateVerdict.BLOCKED, content="")


async def test_blocked_derived_comment_still_releases_its_lease():
    board = _Board()
    with pytest.raises(OutboundContentBlockedError):
        await outcome(board, gate=_BlockedGate())
    assert len(board.lease_creations()) == 1
    assert not board.outcome_comments()
    assert not board.grants()


@pytest.mark.parametrize("phase", ["acquire", "renew", "write", "release"])
async def test_repeated_cancellation_settles_owned_requests_before_release(phase):
    board = _Board()
    if phase == "acquire":
        board.after = True
        board.pause = lambda name, args: (
            name == "save_comment"
            and "state: held\n" in str(args.get("body", ""))
            and "since:" not in str(args.get("body", ""))
        )
    elif phase == "renew":
        board.after = True
        board.pause = lambda name, args: (
            name == "save_comment" and "since:" in str(args.get("body", ""))
        )
    elif phase == "write":
        board.pause = lambda name, args: (
            name == "save_comment"
            and str(args.get("body", "")).startswith("[fixture-outcome:")
        )
    else:
        board.pause = lambda name, _args: name == "delete_comment"
    task = asyncio.create_task(outcome(board))
    async with asyncio.timeout(10):
        await board.reached.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        board.resume.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not board.grants()
    assert bool(board.outcome_comments()) is (phase in {"write", "release"})
    count = len(board.calls)
    await asyncio.sleep(0)
    assert len(board.calls) == count


@pytest.mark.parametrize("author", ["principal", None])
async def test_a_lease_does_not_allow_replacing_another_authors_comment(author):
    board = _Board()
    await outcome(board)
    comment = board.outcome_comments()[0]
    comment.author = author
    comment.body = comment.body.split("\n", 1)[0] + "\nprincipal correction"
    original = comment.body
    with pytest.raises(SurfaceWriteAttributionError) as caught:
        await outcome(board)
    assert caught.value.author == author
    assert comment.body == original
    assert not board.grants()
