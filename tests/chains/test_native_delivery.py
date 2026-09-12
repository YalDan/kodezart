"""The production native constructor runs the actual fire/delivery graph."""

import json

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from kodezart.chains.criteria import TrackerCriteria
from kodezart.composition.delivery import build_native_lane_workflow
from kodezart.core.config import AppConfig
from kodezart.domain.errors import FireSpecEntryError
from kodezart.types.domain.agent import WorkflowCompleteEvent
from kodezart.types.domain.consolidation import (
    ConsolidationOutcome,
    ConsolidationStatus,
)
from kodezart.types.domain.native_delivery import (
    CompletedLaneDelivery,
    LaneDeliveryEvent,
    PendingLaneDelivery,
    SkippedLaneDelivery,
)
from kodezart.types.domain.outcome import WorkflowOutcome
from tests.adapters.test_ci_watch_evidence import check
from tests.adapters.test_github_api import _make_client
from tests.chains.test_native_fire import (
    CountingTracker,
    NativeExecutor,
    change_tracker,
    engine,
    native_evaluation,
)
from tests.chains.test_native_fresh_boundaries import prepare
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeBranchMerger,
    FakeGitService,
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

    def __call__(self, request):
        self.requests.append(request)
        if request.url.path.endswith("/pulls"):
            if request.method == "POST":
                self.creates.append(json.loads(request.content))
                self.pr = {
                    "html_url": "https://github.com/owner/repo/pull/17",
                    "number": 17,
                    "title": "Native PR",
                }
                return httpx.Response(201, json=self.pr)
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


def composed(*, red=False, rounds=0, saver=None, evaluations=None, forge_present=True):
    tracker = CountingTracker()
    executor = NativeExecutor(
        evaluations or [native_evaluation(), native_evaluation()] * (rounds + 1)
    )
    fire = engine(
        criteria=TrackerCriteria(tracker=tracker),
        executor=executor,
        real_loop=True,
        remediation_rounds=rounds,
        checkpointer=saver,
    )
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
    forge = _make_client(wire) if forge_present else None
    lane = build_native_lane_workflow(
        fire=fire,
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
    return lane, lane.prepare(state), config, wire, forge, executor, tracker


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
    lane, state, config, wire, forge, executor, tracker = composed(
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
        assert len(wire.watches) == (2 if red and rounds else 1)
        assert all(watch == wire.watches[0] for watch in wire.watches)
        assert result.final_commit_sha == (NEXT_SHA if red and rounds else SHA)
        assert len(executor.remediation_prompts) == (1 if red and rounds else 0)
        assert len(
            [event for event in events if isinstance(event, WorkflowCompleteEvent)]
        ) == (2 if red and rounds else 1)
        assert tracker.issue_writes == tracker.workflow_writes == []
        assert final["fire_spec"].subject == result.issue_id
        assert final["criterion_set"].criteria
        assert (
            json.loads(config["metadata"]["scope_lane_request"])["job"]
            == "actual-parent-job"
        )
    finally:
        await forge.close()


async def test_no_forge_reports_explicit_skip_without_fabricating_pr():
    lane, state, config, wire, _, executor, _ = composed(forge_present=False)
    reports, _, final = await run(lane, state, config)
    phase = final["delivery"]
    assert isinstance(phase, SkippedLaneDelivery)
    assert phase.outcome is WorkflowOutcome.review_passed_no_pr_adapter
    assert reports[0].delivery == phase and wire.requests == []
    assert executor.remediation_prompts == []


@pytest.mark.parametrize("changed", [False, True])
async def test_pre_delivery_resume_retains_identity_and_checks_current_criteria(
    changed,
):
    saver = InMemorySaver()
    lane, state, config, wire, forge, executor, tracker = composed(saver=saver)
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
