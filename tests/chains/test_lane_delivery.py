"""Native lane delivery uses real coordinator logic and boundary doubles."""

import asyncio

import pytest
from pydantic import ValidationError

from kodezart.chains.criteria import TrackerCriteria
from kodezart.chains.lane_delivery import LaneDeliveryCoordinator
from kodezart.core.protocols import PRCreator
from kodezart.domain.errors import (
    BaseResolutionError,
    CheckObservationError,
    DeliveryHeadError,
    FireSpecEntryError,
)
from kodezart.types.domain.check_observation import IncompleteChecks
from kodezart.types.domain.delivery import CheckRedClass, LaneDelivery
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
)

SHA = "a" * 40
REPO = "https://github.com/owner/repo.git"
HEAD = "lane-head"
BASE = "blocker-branch"


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
    spec = await criteria.read_spec(issue_key=SUBJECT)
    snapshot = await criteria.read_current(spec=spec)
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
    owner, state, context, *_ = parts
    return await owner.deliver(
        state=state, context=context, stalled=stalled, remediation_available=remediation
    )


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
    owner, state, context, creator, *_ = parts
    owner._pr_state_reader.records[(REPO, 7)] = pr_identity(number=7)
    owner._forge_query = FakeForgeQuery(
        open_prs={(REPO, HEAD): ("https://github.com/owner/repo/pull/7", 7)}
    )
    result = await owner.deliver(
        state=state, context=context, stalled=False, remediation_available=False
    )
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


async def test_watch_bound_cancellation_releases_slot_for_next_lane():
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
    first = asyncio.create_task(deliver(parts))
    await entered.wait()
    second = asyncio.create_task(deliver(parts))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    resume.set()
    assert (await second).outcome is WorkflowOutcome.ci_passed
    assert ci.peak == 1


def test_creator_has_no_merge_or_workflow_state_capability():
    assert {
        name
        for name, method in vars(PRCreator).items()
        if not name.startswith("_") and callable(method)
    } == {"create_pr", "comment_on_pr"}
    assert not hasattr(FakePRCreator(), "merge_pr")


async def test_delivery_refuses_incoherent_wire_outcome():
    result = await deliver(await setup())
    wire = result.model_dump()
    wire["outcome"] = WorkflowOutcome.ci_failed_unclassified
    with pytest.raises(ValidationError, match="outcome must match"):
        LaneDelivery.model_validate(wire)


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
    owner, initial, context, *_ = await setup(monitor=monitor, watches=bound)
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
        async def read_current(self, *, spec):
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
    tasks = [
        asyncio.create_task(
            owner.deliver(
                state=state,
                context=context,
                stalled=False,
                remediation_available=False,
            )
        )
        for state in states
    ]
    await asyncio.wait_for(entered.wait(), timeout=5)
    assert monitor.peak == bound
    release.set()
    results = await asyncio.gather(*tasks)
    assert {result.lane_key for result in results} == set(snapshots)
    assert all(result.outcome is WorkflowOutcome.ci_passed for result in results)
    assert monitor.peak == bound and monitor.active == 0
