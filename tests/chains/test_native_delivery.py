"""The production native constructor runs the actual fire/delivery graph."""

import ast
import enum
import functools
import importlib
import inspect
import itertools
import json
import textwrap
import types
import typing
from typing import ClassVar

import httpx
import pytest
import structlog
from langgraph.checkpoint.memory import InMemorySaver
from typing_extensions import is_protocol

from kodezart.chains import (
    fire_consolidation,
    fire_remediation,
    native_delivery,
    ralph_workflow,
)
from kodezart.chains.criteria import TrackerCriteria, require_current_native_snapshot
from kodezart.chains.lane_delivery import LaneDeliveryCoordinator
from kodezart.chains.native_delivery import NativeLaneWorkflow
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.composition.delivery import build_native_lane_workflow
from kodezart.config.app import AppConfig
from kodezart.core.errors import NoStructuredOutputError
from kodezart.core.protocols import (
    AgentRunner,
    CIMonitor,
    FireCriteriaReader,
    FireCriteriaSource,
    ForgeQuery,
    GitService,
    LaneStateWriter,
    OutboundContentGate,
    PRCreator,
    PromptSetProvider,
    PRStateReader,
)
from kodezart.domain.agent import best_iteration_ref
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.domain.errors import (
    BaseResolutionError,
    CheckObservationError,
    DeliveryHeadError,
    FireSpecEntryError,
    ForgeAPIError,
    OutboundContentBlockedError,
    PersistedCriterionSetError,
    PRStateReadError,
    TransientAPIError,
)
from kodezart.domain.stall_report import DO_NOT_MERGE_PREFIX
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.lane_state_writer import TrackerLaneStateWriter
from kodezart.types.domain.agent import (
    AcceptanceCriteriaOutput,
    ResultEvent,
    WorkflowCompleteEvent,
)
from kodezart.types.domain.check_observation import (
    AbsentChecks,
    IncompleteChecks,
    ObservedChecks,
)
from kodezart.types.domain.consolidation import (
    ConsolidationOutcome,
    ConsolidationStatus,
)
from kodezart.types.domain.criteria import TrackerCriterionSet
from kodezart.types.domain.delivery import (
    CheckRedClass,
    LaneDelivery,
    classify_lane_delivery,
)
from kodezart.types.domain.gating import (
    GateDecision,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
)
from kodezart.types.domain.native_delivery import (
    CompletedLaneDelivery,
    LaneDeliveryEvent,
    PendingLaneDelivery,
    SkippedLaneDelivery,
)
from kodezart.types.domain.operation import LifecycleStage
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.pr_state import PRLifecycle, PRState
from kodezart.types.domain.run_state import LanePR
from kodezart.types.domain.workflow import ExecutionContext
from tests.adapters.test_ci_watch_evidence import check
from tests.adapters.test_github_api import _make_client
from tests.chains.test_native_fire import (
    OWED_KEYS,
    SUBJECT,
    CountingTracker,
    NativeExecutor,
    NativeSourceReader,
    bypasses_to,
    change_tracker,
    engine,
    function_at,
    native_evaluation,
    native_operation,
    node_of,
    persisted_artifact,
    reached_through_callers,
    routes_to,
)
from tests.chains.test_native_fresh_boundaries import prepare
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeBranchMerger,
    FakeGitService,
    FakeQualityGate,
    FakeRefPublisher,
    PassThroughGate,
    make_prompt_provider,
)

SHA = "a" * 40
NEXT_SHA = "c" * 40


class ForgeWire:
    def __init__(self, *, red_first=False):
        self.requests = []
        self.creates = []
        self.comments = []
        self.watches = []
        self.red_first = red_first
        self.pr = None
        self.pr_reads = []
        self.identity_damage = None
        self.damage_after_watch = False
        self.head = None
        self.current_sha = lambda: SHA

    def __call__(self, request):
        self.requests.append(request)
        if request.url.path.endswith("/pulls/17"):
            self.pr_reads.append(request)
            damage = (
                self.identity_damage
                if (not self.damage_after_watch or self.watches)
                else None
            )
            if damage == "unavailable":
                raise httpx.ConnectError("PR state unavailable", request=request)
            sha = self.current_sha()
            data = {
                "number": 17,
                "html_url": "https://github.com/owner/repo/pull/17",
                "state": "closed" if damage == "closed" else "open",
                "merged": False,
                "head": {
                    "ref": "different-head" if damage == "head" else self.head,
                    "sha": "b" * 40 if damage == "sha" else sha,
                    "repo": {
                        "html_url": "https://github.com/owner/repo",
                        "full_name": "owner/repo",
                    },
                },
                "base": {
                    "ref": "wrong-base" if damage == "base" else "main",
                    "sha": "b" * 40,
                    "repo": {
                        "html_url": "https://github.com/owner/repo",
                        "full_name": "owner/repo",
                    },
                },
            }
            if damage == "malformed":
                del data["base"]
            return httpx.Response(200, json=data)
        if request.url.path.endswith("/pulls"):
            if request.method == "POST":
                self.creates.append(json.loads(request.content))
                self.head = self.creates[-1]["head"]
                self.pr = {
                    "html_url": "https://github.com/owner/repo/pull/17",
                    "number": 17,
                    "title": "Native PR",
                }
                return httpx.Response(201, json=self.pr)
            self.head = request.url.params["head"].split(":", 1)[1]
            return httpx.Response(200, json=[] if self.pr is None else [self.pr])
        if request.url.path.endswith("/check-runs"):
            self.watches.append(request.url.path)
            sha = SHA if len(self.watches) == 1 else NEXT_SHA
            return httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "check_runs": [
                        check(
                            sha=sha,
                            passed=not (self.red_first and len(self.watches) == 1),
                        )
                    ],
                },
            )
        if request.url.path.endswith("/comments"):
            self.comments.append(json.loads(request.content))
            return httpx.Response(201, json={})
        raise AssertionError(
            f"Unexpected forge capability: {request.method} {request.url}"
        )


class PublishedGit(FakeGitService):
    def __init__(self, merger):
        super().__init__()
        self.merger = merger

    async def remote_branch_sha(self, cwd, remote, branch):
        self.calls.append(("remote_branch_sha", cwd, remote, branch))
        if branch == "main":
            return "b" * 40
        return NEXT_SHA if len(self.merger.calls) > 1 else SHA


class RecordingLaneState:
    """The lane's record writer, as the delivering step reaches it.

    Only the one call that step makes: what it records is what the graph asked
    for, and the tracker this fixture drives stays untouched by it, so the
    coordinator's own "no tracker write" statements keep their meaning.
    """

    def __init__(self) -> None:
        self.pull_requests: list[tuple[str, LanePR, RepoVisibility]] = []

    async def record_pull_request(
        self, *, lane_key: str, pr: LanePR, visibility: RepoVisibility
    ):
        self.pull_requests.append((lane_key, pr, visibility))
        return None


class PrivateRepository:
    """A visibility resolver that answers PRIVATE for every repository.

    The run's resolved visibility is then something other than the UNKNOWN
    every un-resolved run lands on, so a delivery write that names the
    resolved value is told apart from one that names the default.
    """

    async def resolve_visibility(self, *, repo_url: str) -> RepoVisibility:
        return RepoVisibility.PRIVATE


def stalled_loop(*, committed: bool = True) -> FakeQualityGate:
    """A loop that graded every owed Check as failed, with or without a commit.

    The scripted counterpart of the engine's default passing loop: the same
    reconciled Check texts, so every roster barrier after it holds; one
    commit at SHA, so the stall exit has a best iteration to land — or none,
    so it has nothing to land and no pull request follows.
    """
    return FakeQualityGate(
        events=[],
        evaluation=AcceptanceCriteriaOutput.model_validate(
            native_evaluation(failed=True, reconciled=True)
        ),
        last_commit_sha=SHA if committed else None,
    )


def composed(
    *,
    red=False,
    rounds=0,
    saver=None,
    evaluations=None,
    forge_present=True,
    loop=None,
    record_writer=False,
):
    """The native lane graph over one fire, as composition assembles it.

    *record_writer* hands the delivering step the fire's own record writer,
    the one composition hands both seats, instead of the recording double.
    """
    tracker = CountingTracker()
    executor = NativeExecutor(
        evaluations or [native_evaluation(), native_evaluation()] * (rounds + 1)
    )
    fire = engine(
        criteria=TrackerCriteria(tracker=tracker),
        executor=executor,
        real_loop=loop is None,
        quality_gate=loop,
        remediation_rounds=rounds,
        checkpointer=saver,
    )
    fire.specification._visibility_resolver = PrivateRepository()
    if forge_present:
        # The consolidation is handed a ref publisher exactly when the origin
        # has a forge, as composition/engine.py:382 does; the stall exit lands
        # its best iteration through it and an accepted fire never reaches it.
        fire.consolidation._ref_publisher = FakeRefPublisher()
    merger = FakeBranchMerger(
        consolidation_outcomes=[
            ConsolidationOutcome(
                status=ConsolidationStatus.FAST_FORWARDED, feature_tip_sha=sha
            )
            for sha in (SHA, NEXT_SHA)
        ]
    )
    fire.consolidation._merger = merger
    wire = ForgeWire(red_first=red)
    wire.current_sha = lambda: NEXT_SHA if len(merger.calls) > 1 else SHA
    forge = _make_client(wire) if forge_present else None
    lane_state = fire._lane_state if record_writer else RecordingLaneState()
    lane = build_native_lane_workflow(
        fire=fire,
        lane_state=lane_state,
        config=AppConfig(delivery_red_rerun_max_attempts=0),
        service=fire.specification._service,
        git=PublishedGit(merger),
        forge=forge,
        prompts=make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
        repositories=[],
    )
    state, config = prepare(fire, "parent-job/lane-checkpoint")
    config["metadata"] = {
        "scope_lane_request": json.dumps(
            {"job": "actual-parent-job", "issue": state["issue_key"]}, sort_keys=True
        )
    }
    return lane, lane.prepare(state), config, wire, forge, executor, tracker, lane_state


async def run(lane, state, config, **kwargs):
    reports, events, final = [], [], None
    async for _, mode, payload in lane.graph.astream(
        state, config, stream_mode=["custom", "values"], subgraphs=True, **kwargs
    ):
        if mode == "custom":
            events.append(payload)
            if isinstance(payload, LaneDeliveryEvent):
                reports.append(payload)
        elif "delivery" in payload:
            final = payload
    return reports, events, final


@pytest.mark.parametrize(
    "red,rounds,outcome",
    [
        (False, 0, WorkflowOutcome.ci_passed),
        (True, 0, WorkflowOutcome.ci_failed_fix_budget_exhausted),
        (True, 1, WorkflowOutcome.ci_passed),
    ],
)
async def test_actual_native_graph_delivers_and_only_work_defect_reenters_fire(
    red, rounds, outcome
):
    lane, state, config, wire, forge, executor, tracker, lane_state = composed(
        red=red, rounds=rounds
    )
    try:
        assert isinstance(state["delivery"], PendingLaneDelivery)
        reports, events, final = await run(lane, state, config)
        assert len(reports) == 1
        assert isinstance(final["delivery"], CompletedLaneDelivery)
        result = reports[0].delivery.result
        assert result.outcome is outcome and not result.remediation_pending
        assert len(wire.creates) == 1
        assert wire.creates[0]["base"] == "main"
        assert wire.creates[0]["head"] == result.head_branch
        assert result.pr.number == 17 and result.pr.state == "open"
        # The delivering step put that pull request on the lane's record, once
        # per delivery, under the lane the result names and under the
        # visibility this run resolved — the same one the commit write of that
        # record body was gated under (KOD-843). The run resolved PRIVATE, so
        # a delivery write gated under the UNKNOWN default would not match.
        assert final["repo_visibility"] is RepoVisibility.PRIVATE
        assert lane_state.pull_requests == [
            (result.lane_key, result.pr, final["repo_visibility"])
            for _ in range(2 if red and rounds else 1)
        ]
        assert len(wire.watches) == (2 if red and rounds else 1)
        assert all(watch == wire.watches[0] for watch in wire.watches)
        assert result.final_commit_sha == (NEXT_SHA if red and rounds else SHA)
        assert len(executor.remediation_prompts) == (1 if red and rounds else 0)
        assert len(
            [event for event in events if isinstance(event, WorkflowCompleteEvent)]
        ) == (2 if red and rounds else 1)
        # Every write the graph made names a criterion sub-issue of this
        # lane: the evaluator crossed each one off at the sha it graded, and
        # nothing wrote the subject that owns them.
        assert {key for key, _, _ in tracker.issue_writes} == set(OWED_KEYS)
        assert tracker.workflow_writes == [
            (key, LifecycleStage.DONE) for key in sorted(OWED_KEYS)
        ]
        # The sha the loop branch stands at, as the repository double answers
        # it, rather than a value spelled here.
        assert {
            parse_criterion_evidence(tracker.issues[key].body).graded_sha
            for key in OWED_KEYS
        } == {
            await NativeSourceReader().resolve_commit(cwd="", ref=final["ralph_branch"])
        }
        assert SUBJECT not in {key for key, _, _ in tracker.issue_writes} | {
            key for key, _ in tracker.workflow_writes
        }
        assert final["fire_spec"].subject == result.issue_id
        assert final["criterion_set"].criteria
        assert (
            json.loads(config["metadata"]["scope_lane_request"])["job"]
            == "actual-parent-job"
        )
    finally:
        await forge.close()


async def test_a_stalled_lane_opens_and_watches_its_pull_request_like_any_other():
    """A stalled fire reaches delivery through the graph, on the one route.

    The fire itself did not hand off — its terminal is a loop exit — so what
    admitted this lane to delivery is the stalled predicate and nothing else.
    From there the lane opens one pull request and watches its checks exactly
    as an accepted lane does: one create, one watch, one record, and nothing
    the forge saw carries the authored arm's marker (KOD-327).

    The scripted loop records no commit, so this lane has no record on its
    board when the stall exit lands its best iteration. Such a lane has
    nothing to re-enter from, so the landing act is skipped and says so in a
    log line naming the lane and the landed sha; refusing the landing would
    have refused the delivery with it, and the work would never reach a pull
    request (KOD-705).
    """
    lane, state, config, wire, forge, executor, tracker, lane_state = composed(
        loop=stalled_loop()
    )
    try:
        with structlog.testing.capture_logs() as logs:
            reports, events, final = await run(lane, state, config)
        terminal = next(
            event for event in events if isinstance(event, WorkflowCompleteEvent)
        )
        assert terminal.accepted is False
        assert terminal.outcome is WorkflowOutcome.loop_not_accepted
        assert len(reports) == 1
        assert isinstance(final["delivery"], CompletedLaneDelivery)
        result = reports[0].delivery.result
        assert result.outcome is WorkflowOutcome.stalled_pr_opened
        assert result.stalled is True
        assert result.remediation_pending is False
        assert len(wire.creates) == 1
        assert wire.creates[0]["head"] == result.head_branch == final["feature_branch"]
        assert wire.creates[0]["base"] == "main"
        assert result.pr.number == 17 and result.pr.state == "open"
        assert len(wire.watches) == 1
        assert wire.comments == []
        # The head delivered is the landed best iteration, read off the code's
        # own publication rather than spelled a second time here.
        publisher = lane.fire.consolidation._ref_publisher
        assert result.final_commit_sha == SHA == publisher.calls[0]["commit_sha"]
        assert publisher.calls[0]["ref"] == best_iteration_ref(final["feature_branch"])
        assert lane_state.pull_requests == [
            (result.lane_key, result.pr, RepoVisibility.PRIVATE)
        ]
        # The landing act was skipped, once, for this lane at the landed sha,
        # and no first record was composed out of it.
        assert [
            (entry["lane"], entry["landed_sha"])
            for entry in logs
            if entry["event"] == "lane_landing_not_recorded"
        ] == [(SUBJECT, result.final_commit_sha)]
        assert (
            await LaneRecordReader(tracker=tracker, operation=native_operation()).find(
                issue_key=SUBJECT, lane_key=SUBJECT
            )
            is None
        )
        assert executor.remediation_prompts == []
        assert all(
            DO_NOT_MERGE_PREFIX not in wire.creates[0][key]
            for key in ("title", "body", "head", "base")
        )
    finally:
        await forge.close()


async def test_a_stalled_lane_with_no_record_delivers_through_the_record_writer():
    """The delivery write on a lane with no record is skipped like the landing.

    Composition hands one record writer to the fire and to the lane, so the
    delivering step here writes through the same writer the landing did. The
    lane has no record: the landing act is skipped and logged, the pull
    request is opened and watched, and the pull-request write is skipped and
    logged the same way, naming the lane and the pull request. The delivery
    completes and no first record is composed out of either (KOD-705).
    """
    lane, state, config, wire, forge, _, tracker, lane_state = composed(
        loop=stalled_loop(), record_writer=True
    )
    try:
        assert lane_state is lane.fire._lane_state
        assert isinstance(lane_state, TrackerLaneStateWriter)
        with structlog.testing.capture_logs() as logs:
            reports, _, final = await run(lane, state, config)
        assert len(reports) == 1
        assert isinstance(final["delivery"], CompletedLaneDelivery)
        result = reports[0].delivery.result
        assert result.outcome is WorkflowOutcome.stalled_pr_opened
        assert len(wire.creates) == 1
        assert len(wire.watches) == 1
        assert [
            (entry["lane"], entry["landed_sha"])
            for entry in logs
            if entry["event"] == "lane_landing_not_recorded"
        ] == [(SUBJECT, result.final_commit_sha)]
        assert [
            (entry["lane"], entry["pull_request"])
            for entry in logs
            if entry["event"] == "lane_pull_request_not_recorded"
        ] == [(result.lane_key, result.pr.url)]
        assert result.lane_key == SUBJECT
        assert (
            await LaneRecordReader(tracker=tracker, operation=native_operation()).find(
                issue_key=SUBJECT, lane_key=SUBJECT
            )
            is None
        )
    finally:
        await forge.close()


async def test_a_loop_exit_with_nothing_to_land_opens_no_pull_request():
    """The other half of the partition: a loop exit that committed nothing.

    Without a best iteration there is nothing to land, so the stall exit
    publishes no ref, the lane is not stalled by the predicate's own reading,
    and the step skips instead of opening a pull request for an empty head.
    """
    lane, state, config, wire, forge, _, _, lane_state = composed(
        loop=stalled_loop(committed=False)
    )
    try:
        _, _, final = await run(lane, state, config)
        assert isinstance(final["delivery"], SkippedLaneDelivery)
        assert final["delivery"].outcome is WorkflowOutcome.zero_commit_no_pr
        assert wire.requests == []
        assert lane_state.pull_requests == []
        assert lane.fire.consolidation._ref_publisher.calls == []
    finally:
        await forge.close()


async def test_no_forge_reports_explicit_skip_without_fabricating_pr():
    lane, state, config, wire, _, executor, _, _ = composed(forge_present=False)
    reports, _, final = await run(lane, state, config)
    phase = final["delivery"]
    assert isinstance(phase, SkippedLaneDelivery)
    assert phase.outcome is WorkflowOutcome.review_passed_no_pr_adapter
    assert reports[0].delivery == phase and wire.requests == []
    assert executor.remediation_prompts == []


@pytest.mark.parametrize("forge_present", [True, False])
async def test_a_lane_states_whether_it_can_deliver_at_all(forge_present):
    """The origin's one capability, stated publicly and read-only.

    The composition decides it once, from whether there is a forge behind the
    origin, and the coordinator it built or did not build is private. A caller
    choosing what to dispatch reads the fact here rather than working the
    origin out a second time, and cannot set it.
    """
    lane, _, _, _, forge, *_ = composed(forge_present=forge_present)
    try:
        assert lane.delivers is forge_present
        # The composition's decision and nothing beside it.
        assert lane.delivers is (lane._delivery is not None)
        with pytest.raises(AttributeError):
            lane.delivers = not forge_present
    finally:
        if forge is not None:
            await forge.close()


async def test_a_delivering_lane_without_its_record_writer_refuses_at_construction():
    """A lane that can deliver can record where it delivered to, or is not built.

    Where a delivery is retained is the lane's record, so the graph that holds
    a delivery coordinator and no writer of that record is refused while it is
    being composed — not at the one delivery that would have been dropped
    (KOD-843).
    """
    lane, _, _, _, forge, *_ = composed()
    try:
        # Not vacuous: this is the coordinator a composed forge lane holds.
        assert lane._delivery is not None

        with pytest.raises(ValueError, match="lane state writer"):
            NativeLaneWorkflow(fire=lane.fire, delivery=lane._delivery, lane_state=None)
    finally:
        await forge.close()


@pytest.mark.parametrize("changed", [False, True])
async def test_pre_delivery_resume_retains_identity_and_checks_current_criteria(
    changed,
):
    saver = InMemorySaver()
    lane, state, config, wire, forge, executor, tracker, _ = composed(saver=saver)
    try:
        reports, _, _ = await run(lane, state, config, interrupt_before=["deliver"])
        assert reports == [] and wire.requests == []
        paused = lane.graph.get_state(config)
        assert paused.next == ("deliver",)
        assert (
            paused.metadata["scope_lane_request"]
            == config["metadata"]["scope_lane_request"]
        )
        if changed:
            change_tracker(tracker, "changed-check")
            with pytest.raises(FireSpecEntryError):
                await run(lane, None, config)
            assert wire.requests == []
        else:
            resumed, _, final = await run(lane, None, config)
            assert len(resumed) == len(wire.creates) == 1
            assert final["delivery"].result.outcome is WorkflowOutcome.ci_passed
            assert len(executor.execution_prompts) == 1
    finally:
        await forge.close()


@pytest.mark.parametrize("damage", ["base", "head", "sha", "closed"])
@pytest.mark.parametrize("after_watch", [False, True])
async def test_actual_pr_identity_refuses_reuse_or_drift(damage, after_watch):
    lane, state, config, wire, forge, _, _, _ = composed()
    wire.pr = {
        "html_url": "https://github.com/owner/repo/pull/17",
        "number": 17,
        "title": "Existing native PR",
    }
    wire.identity_damage = damage
    wire.damage_after_watch = after_watch
    try:
        with pytest.raises(PRStateReadError):
            await run(lane, state, config)
        assert wire.creates == wire.comments == []
        assert len(wire.watches) == (1 if after_watch else 0)
    finally:
        await forge.close()


async def test_current_check_change_during_comment_gate_refuses_before_post():
    lane, state, config, wire, forge, _, tracker, _ = composed(red=True)

    class ChangedCriterion(PassThroughGate):
        async def gate(self, **kwargs):
            if kwargs["destination"] is OutboundDestination.PR_COMMENT:
                change_tracker(tracker, "changed-check")
            return await super().gate(**kwargs)

    lane._delivery._gate = ChangedCriterion()
    try:
        with pytest.raises(FireSpecEntryError):
            await run(lane, state, config)
        assert wire.comments == []
    finally:
        await forge.close()


@pytest.mark.parametrize(
    "damage", ["base", "head", "sha", "closed", "malformed", "unavailable", None]
)
async def test_paused_terminal_requires_current_pr_without_repeating_delivery(damage):
    lane, state, config, wire, forge, executor, _, _ = composed(saver=InMemorySaver())
    try:
        reports, _, _ = await run(lane, state, config, interrupt_before=["complete"])
        assert reports == []
        assert lane.graph.get_state(config).next == ("complete",)
        before = (
            len(wire.creates),
            len(wire.watches),
            len(wire.comments),
            len(executor.execution_prompts),
            len(executor.evaluation_prompts),
        )
        previous_reads = len(wire.pr_reads)
        wire.identity_damage = damage
        if damage is None:
            resumed, _, final = await run(lane, None, config)
            assert len(resumed) == 1
            assert final["delivery"].result.outcome is WorkflowOutcome.ci_passed
        else:
            error = (
                ForgeAPIError
                if damage == "malformed"
                else TransientAPIError
                if damage == "unavailable"
                else PRStateReadError
            )
            with pytest.raises(error):
                await run(lane, None, config)
        assert len(wire.pr_reads) > previous_reads
        assert before == (
            len(wire.creates),
            len(wire.watches),
            len(wire.comments),
            len(executor.execution_prompts),
            len(executor.evaluation_prompts),
        )
    finally:
        await forge.close()


# ---------------------------------------------------------------------------
# Every node that permits a judgment's effect goes through the snapshot
# barrier, and a persisted criteria document reaching one is refused there.
# ---------------------------------------------------------------------------

#: Every node whose effect waits on the snapshot barrier, derived from the
#: shipped tree: every function that calls it, however it is spelled, and
#: every function that calls one of those.  A node that reaches the barrier
#: through a helper is a gated node too, beside the helper (KOD-652).
SNAPSHOT_GATED_NODES = reached_through_callers(require_current_native_snapshot)

#: How many routes each gated node has to the barrier: one per call that
#: reaches it -- to the barrier itself, or to another gated node -- and per
#: arm of the node that reaches that call, the skip side of each guard
#: with no else around that call among them.  Each arm into a call is a
#: way a carried-in set can meet the barrier, so each needs its own reach.
SNAPSHOT_GATED_ROUTES = routes_to(
    require_current_native_snapshot,
    *filter(None, map(function_at, SNAPSHOT_GATED_NODES)),
)

#: How many of those routes are skip sides, on which the call is not made:
#: no arrival can meet the barrier there, so a skip side has no reach, and
#: the drive below, which runs every input, takes both sides of every such
#: guard.
SNAPSHOT_GATED_BYPASSES = bypasses_to(
    require_current_native_snapshot,
    *filter(None, map(function_at, SNAPSHOT_GATED_NODES)),
)

#: The lane's open pull request, as a delivery that opened it records it.
GATED_PR = LanePR(url="https://github.com/owner/repo/pull/17", number=17, state="open")


def work_defect_delivery(*, remediation_pending: bool = True) -> LaneDelivery:
    """A completed delivery whose red checks are a work defect.

    Pending one round by default; without one it is the terminal a lane
    whose rounds are spent completes with.
    """
    observation = ObservedChecks(
        commit_sha=SHA,
        checks_passed=False,
        check_names=frozenset({"test"}),
        failed_check_names=frozenset({"test"}),
        summary="test failed on the lane head",
    )
    facts = {
        "observation": observation,
        "red_class": CheckRedClass.WORK_DEFECT,
        "no_run_at_ref": False,
        "stalled": False,
        "remediation_pending": remediation_pending,
    }
    return LaneDelivery(
        lane_key=SUBJECT,
        issue_id=SUBJECT,
        head_branch="kodezart/fire-subject",
        base_branch="main",
        final_commit_sha=SHA,
        pr=GATED_PR,
        checks_passed=False,
        checks_summary=observation.summary,
        outcome=classify_lane_delivery(**facts),
        **facts,
    )


class GatedLane:
    """One composed lane, and the moment a persisted set reaches its state.

    The state starts on the tracker's own roster, so every barrier a route
    passes before the set arrives holds.  ``arrive`` puts the persisted
    document in its place and records everything outward so far; the
    refusal is then checked against that record, so nothing outward may
    happen between the arrival and the refusal.
    """

    def __init__(self, *, lane, state, config, wire, executor, tracker, lane_state):
        self.lane, self.state, self.config = lane, state, config
        self.context = ExecutionContext.from_configurable(config)
        self.wire, self.executor = wire, executor
        self.tracker, self.lane_state = tracker, lane_state
        self.events = []
        self.arrived = None

    def outward(self):
        """Every effect beyond the node, as far as the fixture observes it."""
        consolidation = self.lane.fire.consolidation
        return {
            "consolidations": list(consolidation._merger.calls),
            "landed refs": list(consolidation._ref_publisher.calls),
            "forge requests": list(self.wire.requests),
            "remote git reads": list(self.lane._delivery._git.calls),
            "lane records": list(self.lane_state.pull_requests),
            "remediation drafts": list(self.executor.remediation_prompts),
            "stream events": list(self.events),
            "tracker issues": dict(self.tracker.issues),
            "tracker reads": (self.tracker.spec_reads, self.tracker.subtree_reads),
        }

    def arrive(self):
        """The persisted set reaches the state, here and now."""
        self.state["criterion_set"] = persisted_artifact()
        self.arrived = self.outward()

    def entered(self):
        """The state as it enters a node that holds the set from the start."""
        self.arrive()
        return self.state


class ArrivingGate(PassThroughGate):
    """The content gate, as the persisted set arrives on clearing *destination*."""

    def __init__(self, at: GatedLane, destination: OutboundDestination) -> None:
        super().__init__()
        self.at, self.destination = at, destination

    async def gate(self, **kwargs):
        cleared = await super().gate(**kwargs)
        if kwargs["destination"] is self.destination:
            self.at.arrive()
        return cleared


def deliver_through(at, *, remediation_available=True):
    """The coordinator's delivery of the lane state *at* holds."""
    return at.lane._delivery.deliver(
        state=at.state,
        context=at.context,
        stalled=False,
        remediation_available=remediation_available,
    )


def deliver_entering(at):
    """The set is in the state from the start of the delivery."""
    at.entered()
    return deliver_through(at)


def deliver_opening_the_pr(at):
    """The set arrives as the new PR's body clears, before it is created."""
    at.lane._delivery._gate = ArrivingGate(at, OutboundDestination.PR_BODY)
    return deliver_through(at)


def deliver_commenting_on_red_checks(at):
    """Red checks with no round left: the set arrives as the comment clears."""
    at.wire.red_first = True
    at.lane._delivery._gate = ArrivingGate(at, OutboundDestination.PR_COMMENT)
    return deliver_through(at, remediation_available=False)


def deliver_returning_after_the_comment(at):
    """Red checks with no round left: the set arrives once the comment posts."""
    at.wire.red_first = True
    creator = at.lane._delivery._pr_creator
    comment = creator.comment_on_pr

    async def commented(**kwargs):
        posted = await comment(**kwargs)
        at.arrive()
        return posted

    creator.comment_on_pr = commented
    return deliver_through(at, remediation_available=False)


def deliver_returning_green_checks(at):
    """Green checks: the set arrives once they are observed, before the return."""
    ci = at.lane._delivery._ci
    watch = ci.wait_for_checks

    async def watched(**kwargs):
        observed = await watch(**kwargs)
        at.arrive()
        return observed

    ci.wait_for_checks = watched
    return deliver_through(at)


def reusing_the_pr(at):
    """The lane's pull request is already open, so delivery reuses it.

    Opening one on this arm is a failure of the reach itself, not a refusal.
    """
    at.wire.pr = {
        "html_url": GATED_PR.url,
        "number": GATED_PR.number,
        "title": "Native PR",
    }

    async def opened(*args, **kwargs):
        raise AssertionError("the reuse arm opened a pull request")

    at.lane._delivery._open_pr = opened


def no_run_at_the_ref(at):
    """No check run appears at the lane head although checks are declared."""
    ci = at.lane._delivery._ci

    async def absent(**kwargs):
        return AbsentChecks(summary="No check run appeared at the lane head")

    async def declared(**kwargs):
        return True

    ci.wait_for_checks = absent
    ci.checks_declared = declared


def deliver_commenting_on_red_checks_of_the_reused_pr(at):
    """The comment on red checks, on a pull request delivery reused."""
    reusing_the_pr(at)
    return deliver_commenting_on_red_checks(at)


def deliver_commenting_on_no_run(at):
    """No run at the ref: the set arrives as the comment clears."""
    no_run_at_the_ref(at)
    at.lane._delivery._gate = ArrivingGate(at, OutboundDestination.PR_COMMENT)
    return deliver_through(at, remediation_available=False)


def deliver_returning_after_the_comment_on_no_run(at):
    """No run at the ref: the set arrives once the comment posts."""
    no_run_at_the_ref(at)
    creator = at.lane._delivery._pr_creator
    comment = creator.comment_on_pr

    async def commented(**kwargs):
        posted = await comment(**kwargs)
        at.arrive()
        return posted

    creator.comment_on_pr = commented
    return deliver_through(at, remediation_available=False)


def deliver_returning_green_checks_of_the_reused_pr(at):
    """Green checks on a reused pull request: the set arrives once observed."""
    reusing_the_pr(at)
    return deliver_returning_green_checks(at)


def deliver_step_handing_to_the_coordinator(at):
    """A reviewed, merged fire: the set arrives as the step hands it over."""
    coordinator = at.lane._delivery
    deliver = coordinator.deliver

    async def handed(**kwargs):
        at.arrive()
        return await deliver(**kwargs)

    coordinator.deliver = handed
    at.state.update(merged=True, review_passed=True)
    return at.lane._deliver(at.state, at.config)


#: How each gated node is driven, one hand-written entry per route into a
#: call.  Which nodes exist and how many routes each has are read off the
#: tree; requiring the reach table to agree with both is what makes the
#: refusal below a statement about every route of every gated node.  The
#: skip side of a guard around a call is the one route with no entry here:
#: the call is not made on it, and the drive further below takes it.
#:
#: The routes were found by reading each node: every call in it that reaches
#: the barrier, directly or through another gated node, and each arm of the
#: node that reaches that call.  In the coordinator's delivery, the pull
#: request is opened or reused, and the comment is entered on red checks or
#: on no run at the ref: the comment's re-check is reached on red checks of
#: an opened or a reused pull request and on no run, and the final re-check
#: on green checks of an opened or a reused pull request and after either
#: comment posts.  A route whose call comes first is driven with the set in
#: the state from the start; a later one is driven through the node's
#: earlier barriers on the tracker's roster, with the set arriving just
#: before that call.
SNAPSHOT_GATED_REACH = {
    node_of(RalphWorkflowEngine._merge_to_feature): {
        "entry": lambda at: at.lane.fire._merge_to_feature(at.entered(), at.config),
    },
    node_of(RalphWorkflowEngine._land_best_iteration): {
        "entry": lambda at: at.lane.fire._land_best_iteration(at.entered(), at.config),
    },
    node_of(RalphWorkflowEngine._complete_node): {
        "entry": lambda at: at.lane.fire._complete_node(at.entered(), at.config),
    },
    node_of(NativeLaneWorkflow._deliver): {
        "entry": lambda at: at.lane._deliver(at.entered(), at.config),
        "coordinator": deliver_step_handing_to_the_coordinator,
    },
    node_of(NativeLaneWorkflow._remediate): {
        "entry": lambda at: at.lane._remediate(
            {
                **at.entered(),
                "delivery": CompletedLaneDelivery(result=work_defect_delivery()),
            },
            at.config,
        ),
    },
    node_of(NativeLaneWorkflow._complete): {
        "completed": lambda at: at.lane._complete(
            {
                **at.entered(),
                "delivery": CompletedLaneDelivery(
                    result=work_defect_delivery(remediation_pending=False)
                ),
            },
            at.config,
        ),
        "skipped": lambda at: at.lane._complete(
            {
                **at.entered(),
                "delivery": SkippedLaneDelivery(
                    outcome=WorkflowOutcome.zero_commit_no_pr,
                    reason="The fire stopped before delivery",
                ),
            },
            at.config,
        ),
    },
    node_of(LaneDeliveryCoordinator.deliver): {
        "entry": deliver_entering,
        "opening the PR": deliver_opening_the_pr,
        "comment on red checks": deliver_commenting_on_red_checks,
        "comment on red checks of the reused PR": (
            deliver_commenting_on_red_checks_of_the_reused_pr
        ),
        "comment on no run at the ref": deliver_commenting_on_no_run,
        "return after the comment on red checks": deliver_returning_after_the_comment,
        "return after the comment on no run at the ref": (
            deliver_returning_after_the_comment_on_no_run
        ),
        "return on green checks": deliver_returning_green_checks,
        "return on green checks of the reused PR": (
            deliver_returning_green_checks_of_the_reused_pr
        ),
    },
    node_of(LaneDeliveryCoordinator._require_current): {
        "entry": lambda at: at.lane._delivery._require_current(
            at.entered(), at.context, GATED_PR
        ),
    },
    node_of(LaneDeliveryCoordinator._open_pr): {
        "entry": lambda at: at.lane._delivery._open_pr(at.entered(), at.context),
    },
}


def test_every_derived_gated_node_has_a_reach_and_every_reach_a_node():
    """The derived gated nodes are the reach table's keys, and never empty.

    Not parametrised: a derivation that found nothing would collect no case
    at all, and the refusal below would then be stated over no node while
    reporting green.  Here an empty list fails outright (KOD-652).
    """
    assert SNAPSHOT_GATED_NODES, "the tree derived no snapshot-gated node"
    assert set(SNAPSHOT_GATED_NODES) == set(SNAPSHOT_GATED_REACH)


def test_every_route_to_the_barrier_has_its_own_reach():
    """Each gated node has one reach per route of its into the barrier.

    A route is a call that reaches the barrier, directly or through a
    helper, on one arm of the node that reaches that call, or the skip side
    of a guard with no else around that call.  A new call, or a new arm
    into an existing call, needs its own entry; one that stops reaching the
    barrier leaves one behind.  A skip side is no way to meet the barrier,
    so it has no entry: it is counted apart, and the drive below takes it
    (KOD-652).
    """
    assert set(SNAPSHOT_GATED_BYPASSES) <= set(SNAPSHOT_GATED_ROUTES)
    assert {
        node: len(routes) + SNAPSHOT_GATED_BYPASSES.get(node, 0)
        for node, routes in SNAPSHOT_GATED_REACH.items()
    } == SNAPSHOT_GATED_ROUTES


@pytest.mark.parametrize(
    "route",
    [
        (*node, arm)
        for node in sorted(SNAPSHOT_GATED_REACH)
        for arm in SNAPSHOT_GATED_REACH[node]
    ],
    ids=":".join,
)
async def test_a_persisted_set_is_refused_at_every_snapshot_gated_node(
    route, monkeypatch
):
    """No gated node lands, delivers or writes on a carried-in set (KOD-652).

    Each route is driven with the lane's own prepared state, holding the
    subject's tracker spec and the tracker's own roster, until a persisted
    criteria document takes the roster's place.  The node raises the
    persisted-set refusal, and nothing outward has happened since the set
    arrived: no consolidation or landed ref, no forge request, no remote git
    read, no lane record, no remediation draft, no stream event, and neither
    a tracker read nor a tracker write.
    """
    lane, state, config, wire, forge, executor, tracker, lane_state = composed(rounds=1)
    try:
        spec, roster = await lane.fire.criteria.read_entry(issue_key=SUBJECT)
        at = GatedLane(
            lane=lane,
            state={
                **state,
                "issue_key": SUBJECT,
                "fire_spec": spec,
                "feature_tip_sha": SHA,
                "criterion_set": roster,
            },
            config=config,
            wire=wire,
            executor=executor,
            tracker=tracker,
            lane_state=lane_state,
        )
        for module in (native_delivery, ralph_workflow):
            monkeypatch.setattr(module, "get_stream_writer", lambda: at.events.append)

        with pytest.raises(PersistedCriterionSetError):
            await SNAPSHOT_GATED_REACH[route[:2]][route[2]](at)

        assert at.arrived is not None, "the persisted set never reached the node"
        assert at.outward() == at.arrived
    finally:
        await forge.close()


# ---------------------------------------------------------------------------
# The behavioural pin: every input a gated node branches on, and the set
# arriving as each of its awaits completes.  The reaches above drive one
# arrival per route the tree derives; this drives every path the enumerated
# inputs reach, whatever a guard around a route is spelled as (KOD-652).
# ---------------------------------------------------------------------------


def _members_of(annotation):
    """The classes an annotation names: itself, or each side of a union."""
    if typing.get_origin(annotation) in (types.UnionType, typing.Union):
        return [
            member for member in typing.get_args(annotation) if isinstance(member, type)
        ]
    return [annotation] if isinstance(annotation, type) else []


def class_of(node):
    """The class a ``(module, qualified name)`` node is a method of, or nothing."""
    module, qualname = node
    holder = getattr(importlib.import_module(module), qualname.split(".")[0], None)
    return holder if isinstance(holder, type) else None


#: The classes the gated nodes are methods of, read off the derived nodes.
GATED_CLASSES = frozenset(filter(None, map(class_of, SNAPSHOT_GATED_NODES)))


def own_ports(cls):
    """Each port *cls* takes, by the constructor parameter's name.

    A port is a constructor parameter whose annotation, or one side of its
    ``| None``, IS a Protocol.
    """
    return {
        name: member
        for name, annotation in typing.get_type_hints(cls.__init__).items()
        for member in _members_of(annotation)
        if is_protocol(member)
    }


def ports_of(cls, seen=frozenset()):
    """Every port a node of *cls* can await, by object.

    The ports the constructor takes, and those of every gated class it
    holds: the lane workflow holds the fire engine and the delivery
    coordinator, and its nodes await through both.  Each class is read
    once, so the walk is bounded by the gated classes.
    """
    found = set(own_ports(cls).values())
    for annotation in typing.get_type_hints(cls.__init__).values():
        for member in _members_of(annotation):
            if member in GATED_CLASSES and member not in seen | {cls}:
                found |= ports_of(member, seen | {cls})
    return frozenset(found)


def own_inputs(function):
    """Each bool or enum parameter of *function*, with every value it can take."""
    found = {}
    for name, annotation in typing.get_type_hints(function).items():
        if name == "return":
            continue
        if annotation is bool:
            found[name] = (False, True)
        elif isinstance(annotation, type) and issubclass(annotation, enum.Enum):
            found[name] = tuple(annotation)
    return found


def _awaited(method):
    """*method*, an async port method, bracketed as one await of the drive."""

    @functools.wraps(method)
    async def awaited(self, *args, **kwargs):
        index = self.at.begin(type(self), method.__name__)
        try:
            return await method(self, *args, **kwargs)
        finally:
            self.at.end(index)

    return awaited


class DrivePort:
    """A port of the drive, holding one of the outcomes it can give a node.

    ``outcomes`` maps each outcome the fake can give to the error the node
    refuses it with, or ``None`` where the node goes on; ``raises`` maps
    the outcomes the fake gives by raising to what it raises, so what the
    product holds of a node's ``except`` clauses is read off the fakes.
    Every async method is one await the drive counts, and one the set may
    arrive at.
    """

    outcomes: ClassVar[dict[str, type[BaseException] | None]] = {}
    raises: ClassVar[dict[str, type[BaseException]]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for name, value in list(vars(cls).items()):
            if inspect.iscoroutinefunction(value):
                setattr(cls, name, _awaited(value))

    def __init__(self, at, outcome):
        if outcome not in self.outcomes:
            raise ValueError(f"{type(self).__name__} cannot give {outcome!r}")
        self.at, self.outcome = at, outcome


class DriveCriteria(DrivePort):
    """The criteria reader: the roster the node holds is current, or changed."""

    outcomes: ClassVar = {"current": None, "changed": FireSpecEntryError}

    async def read_current(self, *, spec, held):
        if self.outcome == "changed":
            return TrackerCriterionSet(
                criteria=[
                    criterion.model_copy(update={"text": "a changed live Check"})
                    for criterion in held.criteria
                ]
            )
        return held

    async def read_entry(self, *, issue_key, delivering=False):
        raise AssertionError("a gated node entered the fire")


class DriveChecks(DrivePort):
    """The check watch: what one watch of the lane head answers.

    Every red set is a work defect here.  The classifier tells a flake or
    an unclassified red apart only by re-observing the sha, and the drive
    watches with reruns off, as the composed lane above does; each class a
    rerun could return joins a path the outcomes here drive already, green
    or red with no round.
    """

    outcomes: ClassVar = {
        "green": None,
        "red": None,
        "no run": None,
        "absent": None,
        "incomplete": CheckObservationError,
        "another head": CheckObservationError,
    }

    def __init__(self, at, outcome):
        super().__init__(at, outcome)
        self.reruns = []

    async def wait_for_checks(self, *, repo_url, ref):
        if self.outcome in ("no run", "absent"):
            return AbsentChecks(summary="No check run appeared at the lane head")
        if self.outcome == "incomplete":
            return IncompleteChecks(
                commit_shas=frozenset({SHA}),
                check_names=frozenset({"test"}),
                failed_check_names=frozenset(),
                observed_count=0,
                expected_count=1,
                summary="The watch expired before the check set completed",
            )
        green = self.outcome == "green"
        return ObservedChecks(
            commit_sha=NEXT_SHA if self.outcome == "another head" else SHA,
            checks_passed=green,
            check_names=frozenset({"test"}),
            failed_check_names=frozenset() if green else frozenset({"test"}),
            summary="test passed" if green else "test failed on the lane head",
        )

    async def checks_declared(self, *, repo_url):
        return self.outcome == "no run"

    async def rerun_checks(self, *, repo_url, ref):
        self.reruns.append((repo_url, ref))


class DriveForgeQuery(DrivePort):
    """The forge's answer to what is open on the lane head."""

    outcomes: ClassVar = {"no pull request": None, "an open pull request": None}

    async def open_pr_for_head(self, *, repo_url, head):
        if self.outcome == "no pull request":
            return None
        return (GATED_PR.url, GATED_PR.number)

    def branch_web_url(self, *, repo_url, branch):
        return f"{repo_url}/tree/{branch}"


class DrivePullRequests(DrivePort):
    """The pull request writer: it posts, or the forge refuses the comment."""

    outcomes: ClassVar = {"posts": None, "the forge refuses the comment": None}
    raises: ClassVar = {"the forge refuses the comment": ForgeAPIError}

    def __init__(self, at, outcome):
        super().__init__(at, outcome)
        self.created, self.comments = [], []

    async def create_pr(self, *, repo_url, title, body, head, base):
        self.created.append((repo_url, title, body, head, base))
        return (GATED_PR.url, GATED_PR.number)

    async def comment_on_pr(self, *, repo_url, pr_number, body):
        self.comments.append((repo_url, pr_number, body))
        if self.outcome == "the forge refuses the comment":
            raise ForgeAPIError(
                "the forge refused the comment",
                status_code=422,
                detail=f"POST {repo_url}/pulls/{pr_number}/comments",
            )


class DrivePRState(DrivePort):
    """The pull request's identity as the forge reports it."""

    outcomes: ClassVar = {"the open head": None, "another head": PRStateReadError}

    async def read_pr_state(self, *, repo_url, pr_number):
        return PRState(
            url=GATED_PR.url,
            number=pr_number,
            head_repo_url=repo_url,
            head_branch=self.at.state["feature_branch"],
            head_sha=NEXT_SHA if self.outcome == "another head" else SHA,
            base_repo_url=repo_url,
            base_branch="main",
            lifecycle=PRLifecycle.OPEN,
        )


class DriveGit(DrivePort):
    """The remote refs: published as captured, the head moved, or no base."""

    outcomes: ClassVar = {
        "published": None,
        "the head moved": DeliveryHeadError,
        "the base is absent": BaseResolutionError,
    }

    async def remote_branch_sha(self, cwd, remote, branch):
        if branch == self.at.state["feature_branch"]:
            return NEXT_SHA if self.outcome == "the head moved" else SHA
        return None if self.outcome == "the base is absent" else "b" * 40


class DriveRunner(DrivePort):
    """The agent runner: the description session answers, or has no output.

    The session is the one await ``stream`` opens, counted when the drain
    starts reading it and complete when the last event has been read.
    """

    outcomes: ClassVar = {"a description": None, "no output": NoStructuredOutputError}

    def stream(self, **kwargs):
        return self._session()

    async def _session(self):
        index = self.at.begin(type(self), "stream")
        try:
            yield ResultEvent(
                subtype="success",
                duration_ms=0,
                duration_api_ms=0,
                is_error=False,
                num_turns=1,
                session_id="pr-description",
                structured_output=None
                if self.outcome == "no output"
                else {"title": "Native PR", "description": "What changed, and why."},
            )
        finally:
            self.at.end(index)


class DriveGate(DrivePort):
    """The content gate: it clears the content, or blocks it."""

    outcomes: ClassVar = {"clears": None, "blocks": OutboundContentBlockedError}

    async def gate(self, *, content, **kwargs):
        verdict = GateVerdict.BLOCKED if self.outcome == "blocks" else GateVerdict.CLEAN
        return GateDecision(verdict=verdict, content=content)


class DrivePrompts(DrivePort):
    """The prompt registry the fixtures share; it answers nothing asynchronously."""

    outcomes: ClassVar = {"renders": None}

    def __getattr__(self, name):
        return getattr(self.at.prompts, name)


class DriveLaneState(DrivePort):
    """The lane's record writer, as the delivering step reaches it."""

    outcomes: ClassVar = {"records": None}

    def __init__(self, at, outcome):
        super().__init__(at, outcome)
        self.pull_requests = []

    async def record_pull_request(self, *, lane_key, pr, visibility):
        self.pull_requests.append((lane_key, pr, visibility))


#: The fake behind each port, keyed by the port the constructors name by
#: object.  Both criteria ports are the one reader: the composition hands
#: the engine's criteria source to the coordinator as its reader.
PORT_FAKES = {
    AgentRunner: DriveRunner,
    GitService: DriveGit,
    PRCreator: DrivePullRequests,
    ForgeQuery: DriveForgeQuery,
    CIMonitor: DriveChecks,
    FireCriteriaReader: DriveCriteria,
    FireCriteriaSource: DriveCriteria,
    PRStateReader: DrivePRState,
    PromptSetProvider: DrivePrompts,
    OutboundContentGate: DriveGate,
    LaneStateWriter: DriveLaneState,
}

#: The fakes in one order, each at its first outcome unless a drive varies it.
DRIVE_FAKES = tuple(dict.fromkeys(PORT_FAKES.values()))

#: The configuration the composed lane above is built from, read for what
#: the coordinator takes beside its ports.
DRIVE_CONFIG = AppConfig(delivery_red_rerun_max_attempts=0)

#: What the coordinator takes beside its ports, as the composition passes
#: it: the skills selection every fixture suppresses, no repository
#: declarations, and the configuration's own base URL and bounds.
COORDINATOR_SETTINGS = {
    "skills": SUPPRESS_ALL_SKILLS,
    "repositories": (),
    "git_base_url": DRIVE_CONFIG.git.base_url,
    "max_concurrent_watches": DRIVE_CONFIG.delivery_max_concurrent_watches,
    "red_rerun_max_attempts": DRIVE_CONFIG.delivery_red_rerun_max_attempts,
}


class DrivenLane:
    """One composed lane, its ports the drive's, run once per input.

    ``reset`` gives a run its own ports, each holding one outcome, and a
    fresh copy of the base state; ``begin`` and ``end`` bracket each await
    a port serves, recording what had been written before and after it.
    The set arrives as the await ``arrive_at`` completes, or before the
    node runs when that is -1, and never when it is ``None``.
    """

    def __init__(self, *, lane, base, config, executor, tracker):
        self.lane, self.base, self.config = lane, base, config
        self.context = ExecutionContext.from_configurable(config)
        self.executor, self.tracker = executor, tracker
        self.prompts = make_prompt_provider()
        self.events, self.ports, self.state = [], {}, dict(base)
        self.awaits, self.before, self.after = [], [], []
        self.arrive_at, self.arrived, self.initial = None, None, None

    def reset(self, outcomes, arrive_at):
        """Fresh ports at *outcomes*, a fresh state, and nothing written yet."""
        self.ports = {fake: fake(self, outcome) for fake, outcome in outcomes.items()}
        by_port = {port: self.ports[fake] for port, fake in PORT_FAKES.items()}
        self.lane._delivery = LaneDeliveryCoordinator(
            **{
                name: by_port[port]
                for name, port in own_ports(LaneDeliveryCoordinator).items()
            },
            **COORDINATOR_SETTINGS,
        )
        self.lane._lane_state = by_port[LaneStateWriter]
        self.lane.fire.criteria = by_port[FireCriteriaSource]
        consolidation = self.lane.fire.consolidation
        for ledger in (
            consolidation._merger.calls,
            consolidation._ref_publisher.calls,
            consolidation._git.calls,
            self.executor.remediation_prompts,
            self.tracker.issue_writes,
            self.tracker.workflow_writes,
            self.events,
        ):
            ledger.clear()
        self.state = dict(self.base)
        self.awaits, self.before, self.after = [], [], []
        self.arrive_at, self.arrived = arrive_at, None
        self.initial = self.written()
        if arrive_at == -1:
            self.arrive()

    def written(self):
        """Everything written beyond the node, as far as the drive observes it."""
        consolidation = self.lane.fire.consolidation
        return {
            "pull requests": list(self.ports[DrivePullRequests].created),
            "comments": list(self.ports[DrivePullRequests].comments),
            "reruns": list(self.ports[DriveChecks].reruns),
            "lane records": list(self.ports[DriveLaneState].pull_requests),
            "consolidations": list(consolidation._merger.calls),
            "landed refs": list(consolidation._ref_publisher.calls),
            "git calls": list(consolidation._git.calls),
            "remediation drafts": list(self.executor.remediation_prompts),
            "stream events": list(self.events),
            "tracker issue writes": list(self.tracker.issue_writes),
            "tracker workflow writes": list(self.tracker.workflow_writes),
        }

    def arrive(self):
        """The persisted set reaches the state, here and now."""
        self.state["criterion_set"] = persisted_artifact()
        self.arrived = self.written()

    def begin(self, port, method):
        """An await on *port* starts; its index in the run."""
        self.awaits.append((port, method))
        self.before.append(self.written())
        self.after.append(None)
        return len(self.awaits) - 1

    def end(self, index):
        """The await *index* completes; the set arrives if it was to arrive here."""
        self.after[index] = self.written()
        if index == self.arrive_at:
            self.arrive()


class Run(typing.NamedTuple):
    """One run of a node: its awaits, what was written around each, its error."""

    awaits: tuple
    before: tuple
    after: tuple
    initial: dict
    final: dict
    error: BaseException | None


class Combination(typing.NamedTuple):
    """One input of a node's drive and its run with the set never arriving."""

    own: dict
    outcomes: dict
    reference: Run


def drive_handed_off(at):
    """The delivering step on a reviewed, merged fire."""
    at.state.update(merged=True, review_passed=True)
    return at.lane._deliver(at.state, at.config)


def drive_remediating(at):
    """The remediating step after a work-defect delivery with a round left."""
    at.state["delivery"] = CompletedLaneDelivery(result=work_defect_delivery())
    return at.lane._remediate(at.state, at.config)


def drive_completing(at):
    """The terminal step after a completed delivery."""
    at.state["delivery"] = CompletedLaneDelivery(
        result=work_defect_delivery(remediation_pending=False)
    )
    return at.lane._complete(at.state, at.config)


#: How each gated node is driven: the call, on the lane state the drive
#: holds, with the node's own bool and enum parameters as keywords.  A node
#: that branches on the state it is handed is driven in the shape its
#: first reach above gives it; the other shapes stay with the reaches.
GATED_DRIVES = {
    node_of(RalphWorkflowEngine._merge_to_feature): lambda at: (
        at.lane.fire._merge_to_feature(at.state, at.config)
    ),
    node_of(RalphWorkflowEngine._land_best_iteration): lambda at: (
        at.lane.fire._land_best_iteration(at.state, at.config)
    ),
    node_of(RalphWorkflowEngine._complete_node): lambda at: at.lane.fire._complete_node(
        at.state, at.config
    ),
    node_of(NativeLaneWorkflow._deliver): drive_handed_off,
    node_of(NativeLaneWorkflow._remediate): drive_remediating,
    node_of(NativeLaneWorkflow._complete): drive_completing,
    node_of(LaneDeliveryCoordinator.deliver): lambda at, **own: (
        at.lane._delivery.deliver(state=at.state, context=at.context, **own)
    ),
    node_of(LaneDeliveryCoordinator._require_current): lambda at: (
        at.lane._delivery._require_current(at.state, at.context, GATED_PR)
    ),
    node_of(LaneDeliveryCoordinator._open_pr): lambda at: at.lane._delivery._open_pr(
        at.state, at.context
    ),
}


async def driven_once(at, drive, own, outcomes, *, arrive_at):
    """One run of *drive* at *own* and *outcomes*, the set arriving at *arrive_at*."""
    at.reset(outcomes, arrive_at)
    error = None
    try:
        await drive(at, **own)
    except AssertionError:
        raise
    except Exception as exc:
        error = exc
    return Run(
        awaits=tuple(at.awaits),
        before=tuple(at.before),
        after=tuple(at.after),
        initial=at.initial,
        final=at.written(),
        error=error,
    )


async def driven(at, drive, inputs):
    """Every combination of a node's inputs, each run with the set never arriving.

    The inputs are the product of the node's own parameters, *inputs*, and
    of the outcomes of every port the node awaits.  Which ports those are
    is found by running: the first pass holds every port at its first
    outcome, each port a run awaits joins the product, and the passes end
    when no run awaits a new port.  Each pass adds a port or ends the loop,
    so it runs at most once per fake.
    """
    varied, found = [], {}
    while True:
        pending = []
        for values in itertools.product(*inputs.values()):
            own = dict(zip(inputs, values, strict=True))
            for chosen in itertools.product(*(list(fake.outcomes) for fake in varied)):
                outcomes = {fake: next(iter(fake.outcomes)) for fake in DRIVE_FAKES}
                outcomes.update(zip(varied, chosen, strict=True))
                key = (tuple(own.items()), tuple(outcomes.items()))
                if key not in found:
                    pending.append((key, own, outcomes))
        if not pending:
            return list(found.values())
        for key, own, outcomes in pending:
            reference = await driven_once(at, drive, own, outcomes, arrive_at=None)
            found[key] = Combination(own, outcomes, reference)
            for port, _ in reference.awaits:
                if port not in varied:
                    varied.append(port)


def kinds_of(run):
    """What each await of *run* is.

    The snapshot read; a write, during which something was written; a
    check, on a port that can only pass or refuse (one outcome the node
    goes on from); or an observation, on a port whose answer the node
    branches on.
    """
    kinds = []
    for index, (port, _) in enumerate(run.awaits):
        if port is DriveCriteria:
            kinds.append("read")
        elif run.after[index] != run.before[index]:
            kinds.append("write")
        elif sum(error is None for error in port.outcomes.values()) == 1:
            kinds.append("check")
        else:
            kinds.append("observation")
    return kinds


def gaps_of(run):
    """Whether something was written between the awaits of *run*.

    One entry per gap: before the first await, between each two, and after
    the last -- where a collaborator, not a port, writes.
    """
    marks = [
        run.initial,
        *(mark for pair in zip(run.before, run.after, strict=True) for mark in pair),
        run.final,
    ]
    return [marks[2 * i] != marks[2 * i + 1] for i in range(len(run.awaits) + 1)]


def uncovered_writes(run):
    """Each write of *run* that no snapshot read covers.

    A read covers the writes that follow it until anything but a check
    comes between: an observation the node acts on, or another write.
    """
    kinds, gaps = kinds_of(run), gaps_of(run)
    since, uncovered = None, []
    for index in range(len(run.awaits) + 1):
        covered = since is not None and all(kind == "check" for kind in since)
        if gaps[index] and not covered:
            uncovered.append(f"what was written before await {index}")
        if index == len(run.awaits):
            return uncovered
        kind = kinds[index]
        if kind == "read":
            since = []
            continue
        if kind == "write" and not covered:
            uncovered.append(f"await {index}, {run.awaits[index][1]}")
        if since is not None:
            since.append(kind)
    return uncovered


def ends_with_the_barrier(run):
    """Whether checks alone follow the last snapshot read of *run*."""
    kinds, gaps = kinds_of(run), gaps_of(run)
    reads = [index for index, kind in enumerate(kinds) if kind == "read"]
    if not reads:
        return False
    last = reads[-1]
    return all(kind == "check" for kind in kinds[last + 1 :]) and not any(
        gaps[last + 1 :]
    )


def sizes(written):
    """How much each ledger of *written* holds."""
    return {ledger: len(entries) for ledger, entries in written.items()}


def describe(own, outcomes):
    """One combination, named by its inputs."""
    return ", ".join(
        [
            *(f"{name}={value}" for name, value in own.items()),
            *(f"{fake.__name__} {outcome!r}" for fake, outcome in outcomes.items()),
        ]
    )


def test_every_port_a_gated_class_takes_has_a_fake_and_no_other():
    """The drive's port table is the gated classes' constructors, by object.

    Not parametrised: a derivation that found no gated class or no port
    would leave the drive below over no port while reporting green.  Here
    an empty set fails outright (KOD-652).
    """
    assert GATED_CLASSES, "the tree derived no gated class"
    ports = frozenset().union(*map(ports_of, GATED_CLASSES))
    assert ports, "the gated classes take no port"
    assert set(PORT_FAKES) == ports


def test_every_derived_gated_node_has_a_drive_and_every_drive_a_node():
    """The derived gated nodes are the drive table's keys, and never empty."""
    assert SNAPSHOT_GATED_ROUTES, "the tree derived no snapshot-gated node"
    assert set(GATED_DRIVES) == set(SNAPSHOT_GATED_ROUTES)


def handled_by(function):
    """The exception classes the ``except`` clauses of *function* name.

    Read off the function's own source and resolved in its module's
    namespace after import, by object; a clause naming a tuple names each
    member.
    """
    namespace = vars(importlib.import_module(function.__module__))
    found = set()
    for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(function)))):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            named = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            found |= {
                namespace[name.id]
                for name in named
                if isinstance(name, ast.Name)
                and isinstance(namespace.get(name.id), type)
            }
    return found


def test_a_branch_on_an_exception_no_fake_raises_is_not_driven():
    """The drive's one general limit, held as a fact: an input no fake enumerates.

    The delivery coordinator catches two forge errors around the comment it
    posts, and the pull request fake raises one of them.  The other,
    TransientAPIError, is an input no fake gives, so the branch that catches
    it is a path the drive does not reach.  The product shows it: every
    outcome a fake gives by raising is in its outcome table, and none of
    them raises that error (KOD-652).
    """
    for fake in DRIVE_FAKES:
        assert set(fake.raises) <= set(fake.outcomes), fake.__name__
    raised = {error for fake in DRIVE_FAKES for error in fake.raises.values()}
    handled = set().union(*(handled_by(function_at(node)) for node in GATED_DRIVES))

    assert raised == {ForgeAPIError}
    assert handled == {ForgeAPIError, TransientAPIError}
    assert handled - raised == {TransientAPIError}


@pytest.mark.parametrize("node", sorted(GATED_DRIVES), ids=":".join)
async def test_every_input_of_a_gated_node_meets_the_set_at_every_await(
    node, monkeypatch
):
    """Every input a node branches on, and the set arriving at every await.

    The inputs are the product of the node's own bool and enum parameters
    and of the outcomes every port it awaits can give: the ports read off
    the gated classes' constructors by object, the outcomes off the fakes.
    Each combination runs once with the set never arriving, which counts
    the node's awaits on its ports and on the criteria reader; then once
    entering with the set, and once per await with the persisted set
    arriving as that await completes -- the generalisation of the arriving
    gate above, which arrives once its own await has cleared.

    Every run holds one of two things.  A snapshot read after the arrival
    refuses the set by type, and the node does nothing after the refusal:
    its awaits are the reference run's up to that read, and what is
    written is what the reference had written before it.  Or no snapshot
    read follows the arrival, and the run goes the reference run's way:
    the same awaits, the same error or none, and each ledger grown by as
    much -- not byte for byte, since a node reads the set it was handed
    after the barrier, as the remediation request does.

    Two things hold of every reference run.  Every write is covered: a
    snapshot read precedes it with nothing between them but checks, the
    ports that can only pass or refuse (the remote refs before a pull
    request is created, the pull request's identity before a comment), and
    never an observation the node acts on or another write.  And a node
    either revalidates before it returns or acts after its check, on every
    path alike: its completing runs agree on whether checks alone follow
    its last snapshot read.  A guard that skips a re-check on one path, a
    return before it on another, or a re-check moved ahead of the write it
    covered, breaks one of the two.

    A covered write after the arrival is the design's own shape, not a
    refusal missed: the barrier reads the set once, before it asks the
    reader, and a set arriving during the verification that follows is
    met at the next barrier (KOD-652).

    The reach, stated once for the resolver and the drive: the static
    resolver, ``callers_of``, follows the forms its docstring states, each
    held by a control over ``RESOLVER_PROBE``; the behavioural drive,
    ``test_every_input_of_a_gated_node_meets_the_set_at_every_await``,
    covers every path the enumerated inputs reach, whatever a guard or a
    call is spelled as; and the one general limit is an input no fake
    enumerates -- a port raising an exception the fakes do not raise, or a
    value outside the enum a node branches on -- so a node branch on such
    an input is not driven, which the drive's derived product shows.
    """
    lane, state, config, _, forge, executor, tracker, _ = composed(rounds=1)
    try:
        spec, roster = await lane.fire.criteria.read_entry(issue_key=SUBJECT)
        at = DrivenLane(
            lane=lane,
            base={
                **state,
                "issue_key": SUBJECT,
                "fire_spec": spec,
                "feature_tip_sha": SHA,
                "criterion_set": roster,
            },
            config=config,
            executor=executor,
            tracker=tracker,
        )
        for module in (
            native_delivery,
            ralph_workflow,
            fire_consolidation,
            fire_remediation,
        ):
            monkeypatch.setattr(module, "get_stream_writer", lambda: at.events.append)
        drive, inputs = GATED_DRIVES[node], own_inputs(function_at(node))

        combinations = await driven(at, drive, inputs)

        assert combinations, "the node was driven on no input"
        terminals = {}
        for own, outcomes, reference in combinations:
            name = describe(own, outcomes)
            # The fakes' outcome table against what the node did: the run
            # ends at an await whose outcome refuses, with the error that
            # outcome names, and completes when its last await went on.
            assert reference.awaits, name
            last = reference.awaits[-1][0]
            refusal = last.outcomes[outcomes[last]]
            if refusal is None:
                assert reference.error is None, (name, reference.error)
            else:
                assert isinstance(reference.error, refusal), (name, reference.error)
            assert not uncovered_writes(reference), (name, uncovered_writes(reference))
            reads = [
                index
                for index, kind in enumerate(kinds_of(reference))
                if kind == "read"
            ]
            if reference.error is None:
                assert reads, (name, "the node completed without reading the snapshot")
                terminals.setdefault(ends_with_the_barrier(reference), name)
            for arrive_at in range(-1, len(reference.awaits)):
                run = await driven_once(at, drive, own, outcomes, arrive_at=arrive_at)
                where = f"{name}; the set arriving " + (
                    "before the node" if arrive_at < 0 else f"at await {arrive_at}"
                )
                refusal = next((index for index in reads if index > arrive_at), None)
                if refusal is None:
                    assert not isinstance(run.error, PersistedCriterionSetError), where
                    assert type(run.error) is type(reference.error), where
                    assert run.awaits == reference.awaits, where
                    assert sizes(run.final) == sizes(reference.final), where
                else:
                    assert isinstance(run.error, PersistedCriterionSetError), where
                    assert run.awaits == reference.awaits[:refusal], where
                    assert run.final == reference.before[refusal], where
        assert len(terminals) <= 1, terminals
    finally:
        await forge.close()
