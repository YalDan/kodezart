"""The composed authored graph classifies CI before spending a fix round."""

import asyncio
import uuid

import httpx
import pytest

from kodezart.domain.errors import CheckObservationError
from kodezart.types.domain.agent import AuthoredWorkflowCompleteEvent
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.operation import CheckPrerequisite, CheckStep, RepoEntry
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.session import PermissionMode
from tests.adapters.test_ci_rerun import ActionsAPI
from tests.adapters.test_github_api import _make_client
from tests.chains.test_ralph_workflow import _make_engine, _stalled_gate
from tests.fakes import FakeCIMonitor, FakePRCreator, FakeRemediator

REPO = "https://github.com/example/project.git"
SHA = "a" * 40
CLEAN_SHA = "c" * 40


def repository(**changes):
    return RepoEntry(url=REPO, trunk="main", **changes)


def engine(ci, **changes):
    remediator = FakeRemediator()
    workflow = _make_engine(
        ci_monitor=ci,
        pr_creator=FakePRCreator(),
        remediator=remediator,
        red_rerun_max_attempts=1,
        **changes,
    )
    return workflow, remediator


async def finish(workflow):
    events = [
        event
        async for event in workflow.run(
            scope=None,
            prompt="Fix the recorded condition.",
            repo_path="/fixture",
            repo_url=REPO,
            base_spec=trunk_base("main"),
            permission_mode=PermissionMode.UNATTENDED,
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]
    return next(
        event
        for event in reversed(events)
        if isinstance(event, AuthoredWorkflowCompleteEvent)
    )


@pytest.mark.parametrize("passed", [True, None])
@pytest.mark.parametrize("summary", ["", "history unavailable and a flaky network"])
async def test_recovered_same_sha_never_enters_remediation(passed, summary):
    ci = FakeCIMonitor(
        passed=False,
        summary=summary,
        failed_names=frozenset({"lint"}),
        declared=False,
        rerun_results=[(passed, summary, frozenset())],
    )
    workflow, fixes = engine(ci)
    result = await finish(workflow)
    assert result.outcome is (
        WorkflowOutcome.ci_passed if passed else WorkflowOutcome.ci_not_configured
    )
    assert fixes.calls == []
    assert ci.rerun_calls == [(REPO, SHA)]
    assert ci.calls[-1] == {"repo_url": REPO, "ref": SHA}


async def test_zero_operator_rerun_bound_spends_one_fix_round_instead():
    ci = FakeCIMonitor(passed=False, failed_names=frozenset({"lint"}))
    fixes = FakeRemediator()
    workflow = _make_engine(
        ci_monitor=ci,
        pr_creator=FakePRCreator(),
        remediator=fixes,
        red_rerun_max_attempts=0,
    )
    result = await finish(workflow)
    assert result.outcome is WorkflowOutcome.ci_failed_fix_budget_exhausted
    assert len(fixes.calls) == 1
    assert ci.rerun_calls == []


@pytest.mark.parametrize("kind", ["environment", "unclassified"])
async def test_positive_nonwork_red_terminates_without_a_fix(kind):
    ci = FakeCIMonitor(
        passed=False,
        failed_names=frozenset({"lint"}),
        rerun_results=[(False, "Still red", frozenset({"different"}))],
    )
    declarations = ()
    if kind == "environment":
        declarations = (
            repository(
                checks=(
                    CheckStep(
                        name="gate",
                        command="check",
                        forge_check="lint",
                        requires=(CheckPrerequisite.REPOSITORY_HISTORY,),
                    ),
                ),
                runner_environment={CheckPrerequisite.REPOSITORY_HISTORY: False},
            ),
        )
    workflow, fixes = engine(ci, repositories=declarations)
    result = await finish(workflow)
    assert result.outcome is (
        WorkflowOutcome.ci_failed_environment_prerequisite
        if kind == "environment"
        else WorkflowOutcome.ci_failed_unclassified
    )
    assert fixes.calls == []
    assert len(ci.rerun_calls) == (0 if kind == "environment" else 1)


@pytest.mark.parametrize(
    "mapped,provided", [(False, False), (True, None), (True, True)]
)
async def test_only_explicit_matching_environment_declarations_preempt_retry(
    mapped, provided
):
    ci = FakeCIMonitor(
        passed=False,
        failed_names=frozenset({"lint"}),
        rerun_results=[(True, "", frozenset())],
    )
    workflow, fixes = engine(
        ci,
        repositories=(
            repository(
                checks=(
                    CheckStep(
                        name="gate",
                        command="check",
                        forge_check="lint" if mapped else None,
                        requires=(CheckPrerequisite.REPOSITORY_HISTORY,),
                    ),
                ),
                runner_environment={}
                if provided is None
                else {CheckPrerequisite.REPOSITORY_HISTORY: provided},
            ),
        ),
    )
    assert (await finish(workflow)).outcome is WorkflowOutcome.ci_passed
    assert ci.rerun_calls == [(REPO, SHA)]
    assert fixes.calls == []


@pytest.mark.parametrize(
    "declared,exempt,expected",
    [
        (False, False, WorkflowOutcome.ci_not_configured),
        (True, False, WorkflowOutcome.ci_no_run_at_ref),
        (True, True, WorkflowOutcome.ci_not_configured),
    ],
)
async def test_absent_run_uses_the_actual_declaration(declared, exempt, expected):
    ci = FakeCIMonitor(passed=None, declared=declared)
    workflow, fixes = engine(ci, repositories=(repository(forge_exempt=exempt),))
    assert (await finish(workflow)).outcome is expected
    assert ci.declaration_calls == [REPO]
    assert fixes.calls == ci.rerun_calls == []


async def test_native_branch_watch_retries_the_observed_cleanup_commit():
    from tests.fakes import FakeArtifactPersister, FakeBranchMerger

    native = ActionsAPI(sha=SHA)

    class Cleanup(FakeArtifactPersister):
        async def clean(self, **kwargs):
            await super().clean(**kwargs)
            native.sha = CLEAN_SHA

    cleanup = Cleanup()

    def handler(request):
        if (
            request.url.path.endswith("/check-runs")
            and CLEAN_SHA not in request.url.path
        ):
            native.requests.append(request)
            checks = [
                {**job, "check_suite": {"id": 1101}} for job in native.jobs(101, 1)
            ]
            return httpx.Response(
                200, json={"total_count": len(checks), "check_runs": checks}
            )
        return native(request)

    client = _make_client(handler)
    try:
        workflow, fixes = engine(
            client,
            observations=client,
            artifact_persister=cleanup,
            merger=FakeBranchMerger(merge_sha=SHA),
        )
        result = await finish(workflow)
        assert result.outcome is WorkflowOutcome.ci_passed
        assert result.final_commit_sha == SHA
        assert len(cleanup.clean_calls) == 1
        assert native.sha == CLEAN_SHA
        assert fixes.calls == []
        assert len(native.writes) == 1
        assert any(
            "/attempts/2/jobs" in request.url.path for request in native.requests
        )
        assert all(SHA not in request.url.path for request in native.requests)
    finally:
        await client.close()


@pytest.mark.parametrize("bound", [1, 2])
@pytest.mark.parametrize("composed", [False, True])
async def test_configured_watch_bound_covers_every_parallel_public_run(
    bound, composed, monkeypatch
):
    from langchain_core.runnables import RunnableConfig

    from kodezart.chains.authored_checks import AuthoredChecks

    original = AuthoredChecks.monitor_ci
    all_arrived = asyncio.Event()
    arrivals = 0

    async def entered(self, state, config: RunnableConfig):
        nonlocal arrivals
        arrivals += 1
        if arrivals == bound + 1:
            all_arrived.set()
        return await original(self, state, config)

    monkeypatch.setattr(AuthoredChecks, "monitor_ci", entered)

    class HeldChecks(FakeCIMonitor):
        def __init__(self):
            super().__init__()
            self.active = self.peak = 0
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def wait_for_checks(self, **kwargs):
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active == bound:
                self.entered.set()
            try:
                await self.release.wait()
                return await super().wait_for_checks(**kwargs)
            finally:
                self.active -= 1

    ci = HeldChecks()
    if composed:
        from kodezart.core.config import AppConfig
        from kodezart.types.domain.ticket_review import TicketReviewMode

        monkeypatch.setenv("KODEZART_DELIVERY_MAX_CONCURRENT_WATCHES", str(bound))
        workflow, fixes = built_workflow(
            monkeypatch,
            ci,
            config=AppConfig(
                ticket_review_mode=TicketReviewMode.REVIEWED,
            ),
        )
    else:
        workflow, fixes = engine(ci, max_concurrent_watches=bound)
    tasks = [asyncio.create_task(finish(workflow)) for _ in range(bound + 1)]
    try:
        await asyncio.wait_for(all_arrived.wait(), 5)
        await asyncio.wait_for(ci.entered.wait(), 5)
        assert ci.peak == bound
        ci.release.set()
        results = await asyncio.gather(*tasks)
        assert all(result.outcome is WorkflowOutcome.ci_passed for result in results)
        assert len(ci.calls) == bound + 1
        assert fixes.calls == []
    finally:
        ci.release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_unreadable_completed_watch_cannot_rerun_or_remediate():
    ci = FakeCIMonitor(
        passed=False, failed_names=frozenset({"lint"}), observed_sha_by_ref={}
    )
    workflow, fixes = engine(ci)
    with pytest.raises(CheckObservationError):
        await finish(workflow)
    assert fixes.calls == ci.rerun_calls == []


@pytest.mark.parametrize("passed", [True, None, False])
async def test_stalled_pr_uses_the_same_watch_and_recovery_path(passed):
    ci = FakeCIMonitor(
        passed=passed,
        declared=False,
        rerun_results=[(True, "Recovered", frozenset())],
    )
    workflow, fixes = engine(ci, quality_gate=_stalled_gate(), remediation_max_rounds=0)
    result = await finish(workflow)
    assert result.outcome is WorkflowOutcome.stalled_pr_opened
    assert result.accepted is False
    assert ci.calls[0]["ref"] == result.feature_branch
    assert len(ci.calls) == (2 if passed is False else 1)
    assert fixes.calls == []


def built_workflow(monkeypatch, ci, *, config, repositories=()):
    """Use the actual composition entry, with its downstream execution ports faked."""
    from kodezart.composition import engine as composition
    from kodezart.services.agent_service import AgentService
    from tests.fakes import (
        SUPPRESS_ALL_SKILLS,
        FakeAgentExecutor,
        FakeArtifactPersister,
        FakeBranchMerger,
        FakeChangePersister,
        FakeGitService,
        FakeQualityGate,
        FakeRefPublisher,
        FakeRepoCache,
        FakeTicketGenerator,
        FakeWorkspaceProvider,
        PassThroughGate,
        make_passing_evaluation,
        make_prompt_provider,
    )

    fixes = FakeRemediator()
    quality = FakeQualityGate(
        events=[], evaluation=make_passing_evaluation(), last_commit_sha=SHA
    )
    monkeypatch.setattr(composition, "RalphLoop", lambda **_kwargs: quality)
    monkeypatch.setattr(
        composition, "TicketGenerationLoop", lambda **_kwargs: FakeTicketGenerator()
    )
    monkeypatch.setattr(composition, "RemediationChain", lambda **_kwargs: fixes)
    workspace = FakeWorkspaceProvider()
    creator = FakePRCreator()
    # The concrete forge client satisfies all these separate ports at composition.
    ci.create_pr = creator.create_pr
    ci.comment_on_pr = creator.comment_on_pr
    ci.observed_checks = ci.observation_reader.observed_checks

    async def visibility(**kwargs):
        from kodezart.types.domain.gating import RepoVisibility

        return RepoVisibility.PUBLIC

    ci.resolve_visibility = visibility
    router = composition.build_workflow_engine(
        config=config,
        repositories=repositories,
        agent_service=AgentService(
            git_base_url=config.git_base_url,
            executor=FakeAgentExecutor(events=[]),
            workspace=workspace,
            persister=FakeChangePersister(),
        ),
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        workspace=workspace,
        merger=FakeBranchMerger(),
        artifact_persister=FakeArtifactPersister(),
        ref_publisher=FakeRefPublisher(),
        prompts=make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
        github_api=ci,
        checkpointer=None,
    )
    return router, fixes


@pytest.mark.parametrize("bound", [0, 2])
async def test_operator_override_reaches_the_actual_builder_and_public_graph(
    monkeypatch, bound
):
    from kodezart.core.config import AppConfig
    from kodezart.types.domain.ticket_review import TicketReviewMode

    monkeypatch.setenv("KODEZART_DELIVERY_RED_RERUN_MAX_ATTEMPTS", str(bound))
    ci = FakeCIMonitor(
        passed=False,
        rerun_results=[
            (False, "", frozenset({"test"})),
            (True, "", frozenset()),
        ],
    )
    router, fixes = built_workflow(
        monkeypatch,
        ci,
        config=AppConfig(
            ticket_review_mode=TicketReviewMode.REVIEWED,
            remediation_max_rounds=1,
        ),
    )
    result = await finish(router)
    assert result.outcome is (
        WorkflowOutcome.ci_passed
        if bound == 2
        else WorkflowOutcome.ci_failed_fix_budget_exhausted
    )
    assert len(ci.rerun_calls) == bound
    assert len(fixes.calls) == (0 if bound == 2 else 1)


async def test_configured_declarations_reach_the_actual_builder(monkeypatch):
    from kodezart.core.config import AppConfig
    from kodezart.types.domain.ticket_review import TicketReviewMode

    ci = FakeCIMonitor(passed=False, failed_names=frozenset({"lint"}))
    router, fixes = built_workflow(
        monkeypatch,
        ci,
        config=AppConfig(
            ticket_review_mode=TicketReviewMode.REVIEWED,
        ),
        repositories=(
            repository(
                checks=(
                    CheckStep(
                        name="gate",
                        command="check",
                        forge_check="lint",
                        requires=(CheckPrerequisite.CREDENTIALS,),
                    ),
                ),
                runner_environment={CheckPrerequisite.CREDENTIALS: False},
            ),
        ),
    )
    assert (
        await finish(router)
    ).outcome is WorkflowOutcome.ci_failed_environment_prerequisite
    assert ci.rerun_calls == fixes.calls == []


@pytest.mark.parametrize("cancel_during", ["original", "rerun"])
async def test_cancelled_watch_releases_its_slot_for_the_next_public_run(cancel_during):
    class CancellableChecks(FakeCIMonitor):
        def __init__(self):
            super().__init__(
                passed=cancel_during != "rerun", rerun_results=[(True, "", frozenset())]
            )
            self.entered = asyncio.Event()
            self.cancelled = asyncio.Event()
            self.hold = True

        async def wait_for_checks(self, **kwargs):
            target = kwargs["ref"] == SHA if cancel_during == "rerun" else True
            if self.hold and target:
                self.entered.set()
                try:
                    await asyncio.Future()
                finally:
                    self.cancelled.set()
            return await super().wait_for_checks(**kwargs)

    ci = CancellableChecks()
    workflow, fixes = engine(ci, max_concurrent_watches=1)
    task = asyncio.create_task(finish(workflow))
    await asyncio.wait_for(ci.entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ci.cancelled.is_set()
    ci.hold = False
    ci._passed = True
    ci._failed_names = frozenset()
    assert (
        await asyncio.wait_for(finish(workflow), 5)
    ).outcome is WorkflowOutcome.ci_passed
    assert fixes.calls == []


def test_active_pr_write_port_exposes_no_merge_capability():
    from kodezart.core.protocols import PRCreator

    for owner in (PRCreator, FakePRCreator):
        assert {
            name
            for name, value in vars(owner).items()
            if callable(value) and not name.startswith("_")
        } == {
            "create_pr",
            "comment_on_pr",
        }


async def test_one_watch_slot_remains_owned_during_same_sha_reruns(monkeypatch):
    from langchain_core.runnables import RunnableConfig

    from kodezart.chains.authored_checks import AuthoredChecks

    original = AuthoredChecks.monitor_ci
    all_arrived = asyncio.Event()
    arrivals = 0

    async def entered(self, state, config: RunnableConfig):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            all_arrived.set()
        return await original(self, state, config)

    monkeypatch.setattr(AuthoredChecks, "monitor_ci", entered)

    class HeldRetry(FakeCIMonitor):
        def __init__(self):
            super().__init__(passed=False, rerun_results=[(True, "", frozenset())] * 2)
            self.waited_refs = []
            self.retry_entered = asyncio.Event()
            self.release = asyncio.Event()

        async def wait_for_checks(self, **kwargs):
            self.waited_refs.append(kwargs["ref"])
            if kwargs["ref"] == SHA:
                self.retry_entered.set()
                await self.release.wait()
            return await super().wait_for_checks(**kwargs)

    ci = HeldRetry()
    workflow, fixes = engine(ci, max_concurrent_watches=1)
    first = asyncio.create_task(finish(workflow))
    await asyncio.wait_for(ci.retry_entered.wait(), 5)
    second = asyncio.create_task(finish(workflow))
    try:
        await asyncio.wait_for(all_arrived.wait(), 5)
        assert len(ci.waited_refs) == 2
        ci.release.set()
        results = await asyncio.gather(first, second)
        assert all(result.outcome is WorkflowOutcome.ci_passed for result in results)
        assert len(ci.rerun_calls) == 2
        assert fixes.calls == []
    finally:
        ci.release.set()
        for task in (first, second):
            task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
