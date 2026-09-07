"""The real delivery entry uses typed run inputs and gated authored content."""

import inspect
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.chains.delivery_coordinator import DeliveryCoordinator
from kodezart.core.config import AppConfig
from kodezart.core.errors import NoStructuredOutputError
from kodezart.domain.errors import (
    BaseResolutionError,
    DeliveryContextError,
    DeliveryRouteUnavailableError,
    OutboundContentBlockedError,
)
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.accept import FlaggedItem
from kodezart.types.domain.agent import PR_DESCRIPTION_SCHEMA, ResultEvent
from kodezart.types.domain.branch import BaseInput, BaseSpec, WorkRefRole, trunk_base
from kodezart.types.domain.delivery import DeliveryContext, LaneDelivery, LaneDispatch
from kodezart.types.domain.fire_spec import AuthoredSpec
from kodezart.types.domain.gating import (
    ContentClass,
    GateDecision,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
    ScanFailureKind,
)
from kodezart.types.domain.operation import OperationConfig, RepoEntry, RunKind
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.workflow import ExecutionContext
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
    FakeArtifactPersister,
    FakeCIMonitor,
    FakeCIObservationReader,
    FakeForgeQuery,
    FakeGitService,
    FakePRContentEditor,
    FakePRCreator,
    FakeRepoCache,
    FakeWorkspaceProvider,
    PassThroughGate,
    RecordingPromptProvider,
    make_criteria,
    make_ticket_draft,
)
from tests.prompts.sets import OPUS_SET, V5_SET
from tests.prompts.test_prompt_wiring import load_registry

REPOSITORY = "https://github.com/example/project"
HEAD = "lane/subject"
SHA = "a" * 40
BASE = "selected-base"


def dispatch(**changes):
    values = {
        "lane_key": "lane/subject",
        "issue_id": "subject/42",
        "head_branch": HEAD,
        "resolved_base": trunk_base(BASE),
        **changes,
    }
    return LaneDispatch.model_validate(values)


def context(**changes):
    return DeliveryContext.model_validate(
        {
            "execution": ExecutionContext(
                prompt="The original dispatched prompt.",
                repo_path="/checkout",
                repo_url=REPOSITORY,
                cache_key="original-job",
                run_identity=RunIdentity(
                    kind=RunKind.FIRE,
                    name="subject/42",
                    started_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
                base_spec=trunk_base(BASE),
                permission_mode="acceptEdits",
                allowed_tools=["Read"],
            ),
            "fire_outcome": WorkflowOutcome.handed_off_for_delivery,
            "spec": AuthoredSpec(ticket=make_ticket_draft(title="Recorded work")),
            "criteria": tuple(make_criteria("Recorded criterion")),
            "total_iterations": 7,
            "flagged_items": (FlaggedItem(summary="Recorded caveat"),),
            "visibility": RepoVisibility.PUBLIC,
            **changes,
        }
    )


def description(**changes):
    return ResultEvent.model_validate(
        {
            "result": "Prose is not the structured description.",
            "session_id": "new-description-session",
            "subtype": "result",
            "duration_ms": 1,
            "duration_api_ms": 1,
            "is_error": False,
            "num_turns": 1,
            "structured_output": {"title": "Authored title", "description": "Body."},
            **changes,
        }
    )


@dataclass
class Setup:
    coordinator: DeliveryCoordinator
    runner: FakeAgentRunner
    forge: FakePRCreator
    query: FakeForgeQuery
    editor: FakePRContentEditor
    monitor: FakeCIMonitor
    gate: PassThroughGate
    prompts: RecordingPromptProvider


def setup(
    *,
    runner=None,
    forge=None,
    query=None,
    editor=None,
    monitor=None,
    gate=None,
    cleaner=None,
    family=V5_SET,
    git=None,
    cache=None,
    config=None,
    observations=None,
    operation=None,
):
    runner = runner if runner is not None else FakeAgentRunner([description()])
    editor = editor if editor is not None else FakePRContentEditor()
    forge = forge if forge is not None else FakePRCreator(content_store=editor.records)
    query = query if query is not None else FakeForgeQuery()
    monitor = monitor if monitor is not None else FakeCIMonitor()
    observations = (
        observations if observations is not None else FakeCIObservationReader()
    )
    if isinstance(monitor, FakeCIMonitor):
        monitor.observation_reader = observations
        monitor.observed_sha_by_ref.setdefault(HEAD, SHA)
    gate = gate if gate is not None else PassThroughGate()
    prompts = RecordingPromptProvider(load_registry(default_set=family))
    return Setup(
        coordinator=DeliveryCoordinator(
            runner=runner,
            prompts=prompts,
            skills=SUPPRESS_ALL_SKILLS,
            gate=gate,
            pr_creator=forge,
            forge_query=query,
            pr_editor=editor,
            ci=monitor,
            ci_observations=observations,
            operation=operation
            if operation is not None
            else OperationConfig(
                operation_name="fixture",
                workspace="fixture",
                repos=[RepoEntry(url=REPOSITORY, trunk=BASE)],
            ),
            git=git
            if git is not None
            else FakeGitService(remote_branch_shas={HEAD: SHA, BASE: "b" * 40}),
            cache=cache if cache is not None else FakeRepoCache(),
            git_remote="upstream",
            config=config if config is not None else AppConfig(),
            artifact_persister=cleaner,
        ),
        runner=runner,
        forge=forge,
        query=query,
        editor=editor,
        monitor=monitor,
        gate=gate,
        prompts=prompts,
    )


async def deliver(coordinator, *, record=None, facts=None, branch=HEAD, sha=SHA):
    return await coordinator.deliver(
        record if record is not None else dispatch(),
        feature_branch=branch,
        final_commit_sha=sha,
        context=facts if facts is not None else context(),
    )


def test_entry_has_only_one_public_method_and_four_dispatch_fields():
    assert {
        key
        for key, value in vars(DeliveryCoordinator).items()
        if callable(value) and not key.startswith("_")
    } == {"deliver"}
    assert set(LaneDispatch.model_fields) == {
        "lane_key",
        "issue_id",
        "head_branch",
        "resolved_base",
    }
    signature = inspect.signature(DeliveryCoordinator.deliver)
    assert signature.return_annotation is LaneDelivery
    assert {"dispatch", "feature_branch", "final_commit_sha"} <= set(
        signature.parameters
    )


@pytest.mark.parametrize("field", ["lane_key", "issue_id", "head_branch"])
def test_empty_dispatch_identity_refuses(field):
    with pytest.raises(ValidationError):
        dispatch(**{field: ""})


@pytest.mark.parametrize("field", list(DeliveryContext.model_fields))
def test_context_requires_observed_facts(field):
    values = context().model_dump()
    del values[field]
    with pytest.raises(ValidationError):
        DeliveryContext.model_validate(values)


@pytest.mark.parametrize("change", ["branch", "base", "sha", "repository"])
async def test_conflicting_handoff_refuses_before_side_effects(change):
    fixture = setup()
    facts = context()
    branch = HEAD
    sha = SHA
    if change == "branch":
        branch = "different-head"
    elif change == "sha":
        sha = " "
    elif change == "base":
        facts = context(
            execution=facts.execution.model_copy(
                update={"base_spec": trunk_base("other")}
            )
        )
    else:
        facts = context(execution=facts.execution.model_copy(update={"repo_url": None}))
    with pytest.raises(DeliveryContextError):
        await deliver(fixture.coordinator, facts=facts, branch=branch, sha=sha)
    assert fixture.forge.calls == fixture.runner.calls == fixture.monitor.calls == []


@pytest.mark.parametrize(
    "outcome", [WorkflowOutcome.stalled_pr_opened, WorkflowOutcome.merge_divergent]
)
async def test_unconnected_fire_outcome_does_not_silently_become_green(outcome):
    fixture = setup()
    with pytest.raises(DeliveryRouteUnavailableError) as error:
        await deliver(fixture.coordinator, facts=context(fire_outcome=outcome))
    assert error.value.pr_url is None
    assert fixture.forge.calls == fixture.runner.calls == []


@pytest.mark.parametrize("change", ["missing", "kind", "name"])
async def test_delivery_requires_the_same_issue_fire_identity_before_side_effects(
    change,
):
    facts = context()
    identity = facts.execution.run_identity
    if change == "missing":
        identity = None
    elif change == "kind":
        identity = identity.model_copy(update={"kind": RunKind.FIRE_PREP})
    else:
        identity = identity.model_copy(update={"name": "other/99"})
    facts = context(
        execution=facts.execution.model_copy(update={"run_identity": identity})
    )
    git = FakeGitService()
    cleaner = FakeArtifactPersister()
    fixture = setup(git=git, cleaner=cleaner)
    with pytest.raises(DeliveryContextError, match="FIRE run identity"):
        await deliver(fixture.coordinator, facts=facts)
    assert git.calls == cleaner.clean_calls == fixture.runner.calls == []
    assert fixture.forge.calls == fixture.monitor.calls == fixture.gate.calls == []


@pytest.mark.parametrize("family", [V5_SET, OPUS_SET])
async def test_description_uses_original_typed_inputs_and_gates_final_body(family):
    fixture = setup(family=family)
    await deliver(fixture.coordinator)
    variables = fixture.prompts.variables_for(PromptKey.PR_DESCRIPTION)
    assert len(variables) == 1
    assert variables[0]["acceptance_criteria"] == list(context().criteria)
    assert variables[0]["total_iterations"] == 7
    assert "Recorded work" in variables[0]["task_md"]
    assert fixture.gate.destinations == [
        OutboundDestination.PR_TITLE,
        OutboundDestination.PR_BODY,
    ]
    assert fixture.gate.content_classes == [
        ContentClass.AUTHORED,
        ContentClass.AUTHORED,
    ]
    assert all(item[1] is RepoVisibility.PUBLIC for item in fixture.gate.calls)
    assert "Recorded caveat" in fixture.gate.calls[1][0]
    assert "Tracker issue: subject/42" in fixture.gate.calls[1][0]
    assert fixture.forge.calls[0]["body"] == fixture.gate.calls[1][0]


class BlockingGate(PassThroughGate):
    async def gate(self, **kwargs):
        await super().gate(**kwargs)
        return GateDecision(
            verdict=GateVerdict.BLOCKED,
            content=kwargs["content"],
            failure=ScanFailureKind.TRANSPORT_ERROR,
        )


async def test_blocked_authored_content_never_creates_a_pr():
    fixture = setup(gate=BlockingGate())
    with pytest.raises(OutboundContentBlockedError):
        await deliver(fixture.coordinator)
    assert fixture.forge.calls == fixture.monitor.calls == []


@pytest.mark.parametrize(
    "events", [[], [description(structured_output=None)], [description(is_error=True)]]
)
async def test_unsuccessful_description_does_not_create_a_pr(events):
    fixture = setup(runner=FakeAgentRunner(events))
    with pytest.raises(NoStructuredOutputError):
        await deliver(fixture.coordinator)
    assert fixture.forge.calls == fixture.monitor.calls == []


async def test_clean_is_invoked_on_the_delivered_branch():
    cleaner = FakeArtifactPersister()
    fixture = setup(cleaner=cleaner)
    await deliver(fixture.coordinator)
    assert cleaner.clean_calls == [("/checkout", REPOSITORY, HEAD)]


class StrictExecutor:
    def __init__(self):
        self.calls = []

    async def stream(
        self,
        *,
        prompt,
        cwd,
        permission_mode,
        allowed_tools,
        skills,
        session_type,
        agents,
        session_policy,
        session_id=None,
        output_format=None,
        run_identity=None,
    ):
        self.calls.append(locals())
        yield description()


async def test_real_agent_service_receives_fresh_read_only_description_contract():
    executor = StrictExecutor()
    workspace = FakeWorkspaceProvider()
    runner = AgentService(
        executor=executor, workspace=workspace, git_base_url="https://github.com"
    )
    fixture = setup(runner=runner)
    await deliver(fixture.coordinator)
    assert len(executor.calls) == 1
    call = executor.calls[0]
    assert call["session_id"] is None
    assert call["allowed_tools"] == []
    assert call["session_type"] is SessionType.TICKET_FIRE
    assert call["run_identity"] == context().execution.run_identity
    assert call["output_format"] == {
        "type": "json_schema",
        "schema": PR_DESCRIPTION_SCHEMA,
    }


@pytest.mark.parametrize("missing", [HEAD, BASE])
async def test_missing_remote_branch_refuses_before_session_and_pr(missing):
    git = FakeGitService(remote_branch_shas={HEAD: SHA, BASE: "b" * 40, missing: None})
    fixture = setup(git=git)
    with pytest.raises(BaseResolutionError) as error:
        await deliver(fixture.coordinator)
    assert error.value.branches == (missing,)
    assert error.value.issue_id == "subject/42"
    assert fixture.forge.calls == fixture.runner.calls == fixture.monitor.calls == []
    assert {call[-1] for call in git.calls} == {HEAD, BASE}


async def test_changed_remote_head_refuses_the_stale_fire_handoff():
    fixture = setup(
        git=FakeGitService(remote_branch_shas={HEAD: "new-tip", BASE: "b" * 40})
    )
    with pytest.raises(DeliveryContextError, match="final commit SHA"):
        await deliver(fixture.coordinator)
    assert fixture.forge.calls == fixture.runner.calls == []


async def test_dependent_lane_needs_no_blocker_pr_to_open():
    base = BaseSpec(
        inputs=(
            BaseInput(
                blocker_issue_id="blocker/17", branch="blocker-head", sha="b" * 40
            ),
        ),
        base_branch="blocker-head",
        base_role=WorkRefRole.DELIVERABLE,
    )
    git = FakeGitService(remote_branch_shas={HEAD: SHA, "blocker-head": "b" * 40})
    fixture = setup(git=git)
    facts = context(
        execution=context().execution.model_copy(update={"base_spec": base})
    )
    result = await deliver(
        fixture.coordinator, record=dispatch(resolved_base=base), facts=facts
    )
    assert result.base_branch == "blocker-head"
    assert fixture.forge.calls[0]["base"] == "blocker-head"
    assert [call["method"] for call in fixture.forge.calls] == ["create_pr"]


async def test_remote_only_execution_uses_the_addressed_cache():
    cache = FakeRepoCache(repo_path="/addressed-cache")
    git = FakeGitService(remote_branch_shas={HEAD: SHA, BASE: "b" * 40})
    fixture = setup(git=git, cache=cache)
    facts = context(
        execution=context().execution.model_copy(update={"repo_path": None})
    )
    await deliver(fixture.coordinator, facts=facts)
    assert cache.calls == [{"url": REPOSITORY, "cache_key": "original-job"}]
    assert all(call[1:3] == ("/addressed-cache", "upstream") for call in git.calls)


async def test_cleanup_may_advance_head_without_rewriting_fire_sha():
    git = FakeGitService(
        remote_branch_shas={BASE: "b" * 40},
        remote_branch_sha_sequences={HEAD: [SHA, "cleanup-tip"]},
    )
    fixture = setup(git=git, cleaner=FakeArtifactPersister())
    await deliver(fixture.coordinator)
    assert [call[-1] for call in git.calls] == [HEAD, BASE, HEAD, BASE]
    assert fixture.monitor.calls[0]["ref"] == HEAD


async def test_a_branch_removed_during_cleanup_is_not_opened():
    git = FakeGitService(
        remote_branch_shas={BASE: "b" * 40},
        remote_branch_sha_sequences={HEAD: [SHA, None]},
    )
    fixture = setup(git=git, cleaner=FakeArtifactPersister())
    with pytest.raises(BaseResolutionError):
        await deliver(fixture.coordinator)
    assert fixture.forge.calls == fixture.runner.calls == []


async def test_real_git_remote_reads_drive_delivery_preflight(tmp_path):
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    subprocess.run(["git", "init", str(checkout)], check=True, capture_output=True)

    def git(*arguments):
        return subprocess.run(
            ["git", *arguments],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "fixture",
    )
    git("branch", HEAD)
    git("branch", BASE)
    git("remote", "add", "upstream", str(remote))
    git("push", "upstream", HEAD, BASE)
    sha = git("rev-parse", HEAD)
    fixture = setup(git=SubprocessGitService(remote="upstream"))
    facts = context(
        execution=context().execution.model_copy(update={"repo_path": str(checkout)})
    )
    result = await deliver(fixture.coordinator, facts=facts, sha=sha)
    assert result.head_branch == HEAD
    git("push", "upstream", "--delete", BASE)
    with pytest.raises(BaseResolutionError):
        await deliver(fixture.coordinator, facts=facts, sha=sha)
    assert len(fixture.forge.calls) == 1


async def test_no_checks_and_no_declarations_preserves_explicit_none():
    monitor = FakeCIMonitor(passed=None, declared=False, summary="No configured CI.")
    fixture = setup(monitor=monitor)
    result = await deliver(fixture.coordinator)
    assert result.outcome is WorkflowOutcome.ci_not_configured
    assert result.checks_passed is None
    assert result.model_dump()["checks_passed"] is None
    assert monitor.declaration_calls == [REPOSITORY]
    assert monitor.rerun_calls == monitor.failed_name_calls == []


@pytest.mark.parametrize("passed", [False, None])
async def test_unconnected_check_routes_refuse_with_observed_pr_facts(passed):
    monitor = FakeCIMonitor(
        passed=passed,
        declared=True,
        summary="Observed evidence.",
        failed_names=frozenset({"test"}) if passed is False else frozenset(),
    )
    fixture = setup(
        monitor=monitor, config=AppConfig(delivery_red_rerun_max_attempts=0)
    )
    with pytest.raises(DeliveryRouteUnavailableError) as error:
        await deliver(fixture.coordinator)
    assert error.value.pr_url == "https://github.com/o/r/pull/1"
    assert error.value.pr_number == 1
    assert error.value.checks_passed is passed
    assert error.value.checks_summary == "Observed evidence."
    assert monitor.rerun_calls == []
    assert monitor.failed_name_calls == (
        [(REPOSITORY, HEAD)] if passed is False else []
    )
    assert [call["method"] for call in fixture.forge.calls] == ["create_pr"]
    assert monitor.declaration_calls == ([REPOSITORY] if passed is None else [])


async def test_unreadable_declarations_never_mean_no_ci():
    from kodezart.domain.errors import ForgeAPIError

    refusal = ForgeAPIError("unreadable declaration", status_code=None, detail="test")

    class UnreadableDeclaration(FakeCIMonitor):
        async def checks_declared(self, *, repo_url):
            raise refusal

    fixture = setup(monitor=UnreadableDeclaration(passed=None))
    with pytest.raises(ForgeAPIError) as error:
        await deliver(fixture.coordinator)
    assert error.value is refusal
