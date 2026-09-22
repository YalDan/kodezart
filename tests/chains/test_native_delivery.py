"""The production native constructor runs the actual fire/delivery graph."""

import json
from types import SimpleNamespace

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from kodezart.chains import native_delivery, ralph_workflow
from kodezart.chains.criteria import TrackerCriteria, require_current_native_snapshot
from kodezart.chains.lane_delivery import LaneDeliveryCoordinator
from kodezart.chains.native_delivery import NativeLaneWorkflow
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.composition.delivery import build_native_lane_workflow
from kodezart.config.app import AppConfig
from kodezart.domain.agent import best_iteration_ref
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.domain.errors import (
    FireSpecEntryError,
    ForgeAPIError,
    PersistedCriterionSetError,
    PRStateReadError,
    TransientAPIError,
)
from kodezart.domain.stall_report import DO_NOT_MERGE_PREFIX
from kodezart.types.domain.agent import (
    AcceptanceCriteriaOutput,
    WorkflowCompleteEvent,
)
from kodezart.types.domain.check_observation import ObservedChecks
from kodezart.types.domain.consolidation import (
    ConsolidationOutcome,
    ConsolidationStatus,
)
from kodezart.types.domain.delivery import (
    CheckRedClass,
    LaneDelivery,
    classify_lane_delivery,
)
from kodezart.types.domain.gating import OutboundDestination, RepoVisibility
from kodezart.types.domain.native_delivery import (
    CompletedLaneDelivery,
    LaneDeliveryEvent,
    PendingLaneDelivery,
    SkippedLaneDelivery,
)
from kodezart.types.domain.operation import LifecycleStage
from kodezart.types.domain.outcome import WorkflowOutcome
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
    callers_of,
    change_tracker,
    engine,
    native_evaluation,
    node_of,
    persisted_artifact,
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
):
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
    lane_state = RecordingLaneState()
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
    """
    lane, state, config, wire, forge, executor, _, lane_state = composed(
        loop=stalled_loop()
    )
    try:
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
        assert executor.remediation_prompts == []
        assert all(
            DO_NOT_MERGE_PREFIX not in wire.creates[0][key]
            for key in ("title", "body", "head", "base")
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
#: shipped tree: every function that calls it, however it is spelled.  A
#: node that goes through a helper is recorded as the helper, which is what
#: the other nodes call (KOD-652).
SNAPSHOT_GATED_NODES = callers_of(require_current_native_snapshot)

#: The lane's open pull request, as a delivery that opened it records it.
GATED_PR = LanePR(url="https://github.com/owner/repo/pull/17", number=17, state="open")


def work_defect_delivery() -> LaneDelivery:
    """A completed delivery whose red checks await one work-defect round."""
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
        "remediation_pending": True,
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


#: How each gated node is driven, one hand-written line per node.  Which
#: nodes exist is read off the tree; requiring the two to agree is what
#: makes the refusal below a statement about every gated node.
SNAPSHOT_GATED_REACH = {
    node_of(RalphWorkflowEngine._merge_to_feature): lambda at: (
        at.lane.fire._merge_to_feature(at.state, at.config)
    ),
    node_of(RalphWorkflowEngine._land_best_iteration): lambda at: (
        at.lane.fire._land_best_iteration(at.state, at.config)
    ),
    node_of(RalphWorkflowEngine._complete_node): lambda at: at.lane.fire._complete_node(
        at.state, at.config
    ),
    node_of(NativeLaneWorkflow._deliver): lambda at: at.lane._deliver(
        at.state, at.config
    ),
    node_of(NativeLaneWorkflow._remediate): lambda at: at.lane._remediate(
        {**at.state, "delivery": CompletedLaneDelivery(result=work_defect_delivery())},
        at.config,
    ),
    node_of(NativeLaneWorkflow._complete): lambda at: at.lane._complete(
        {
            **at.state,
            "delivery": SkippedLaneDelivery(
                outcome=WorkflowOutcome.zero_commit_no_pr,
                reason="The fire stopped before delivery",
            ),
        },
        at.config,
    ),
    node_of(LaneDeliveryCoordinator.deliver): lambda at: at.lane._delivery.deliver(
        state=at.state,
        context=at.context,
        stalled=False,
        remediation_available=True,
    ),
    node_of(LaneDeliveryCoordinator._require_current): lambda at: (
        at.lane._delivery._require_current(at.state, at.context, GATED_PR)
    ),
    node_of(LaneDeliveryCoordinator._open_pr): lambda at: at.lane._delivery._open_pr(
        at.state, at.context
    ),
}


def test_every_derived_gated_node_has_a_reach_and_every_reach_a_node():
    """The derived gated nodes are the reach table's keys, and never empty.

    Not parametrised: a derivation that found nothing would collect no case
    at all, and the refusal below would then be stated over no node while
    reporting green.  Here an empty list fails outright (KOD-652).
    """
    assert SNAPSHOT_GATED_NODES, "the tree derived no snapshot-gated node"
    assert set(SNAPSHOT_GATED_NODES) == set(SNAPSHOT_GATED_REACH)


@pytest.mark.parametrize("node", sorted(SNAPSHOT_GATED_REACH), ids=":".join)
async def test_a_persisted_set_is_refused_at_every_snapshot_gated_node(
    node, monkeypatch
):
    """No gated node lands, delivers or writes on a carried-in set (KOD-652).

    Each node is driven with the lane's own prepared state, holding the
    subject's tracker spec and, as its criterion set, a persisted criteria
    document. The node raises the persisted-set refusal, and nothing outward
    has happened by then: no consolidation or landed ref, no forge request,
    no lane record, no remediation draft, no terminal event, and neither a
    tracker read nor a tracker write.
    """
    lane, state, config, wire, forge, executor, tracker, lane_state = composed(rounds=1)
    try:
        spec, _ = await lane.fire.criteria.read_entry(issue_key=SUBJECT)
        state = {
            **state,
            "issue_key": SUBJECT,
            "fire_spec": spec,
            "feature_tip_sha": SHA,
            "criterion_set": persisted_artifact(),
        }
        events = []
        for module in (native_delivery, ralph_workflow):
            monkeypatch.setattr(module, "get_stream_writer", lambda: events.append)
        issues = dict(tracker.issues)
        reads = (tracker.spec_reads, tracker.subtree_reads)
        at = SimpleNamespace(
            lane=lane,
            state=state,
            config=config,
            context=ExecutionContext.from_configurable(config),
        )

        with pytest.raises(PersistedCriterionSetError):
            await SNAPSHOT_GATED_REACH[node](at)

        assert lane.fire.consolidation._merger.calls == []
        assert lane.fire.consolidation._ref_publisher.calls == []
        assert wire.requests == []
        assert lane._delivery._git.calls == []
        assert lane_state.pull_requests == []
        assert executor.remediation_prompts == []
        assert events == []
        assert tracker.issues == issues
        assert (tracker.spec_reads, tracker.subtree_reads) == reads
    finally:
        await forge.close()
