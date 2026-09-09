"""The actual fire graph terminates before any delivery operation."""

import json

import httpx
import pytest
from pydantic import ValidationError

from kodezart.core.protocols import QualityGate
from kodezart.domain.outcome import classify_outcome
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AuthoredWorkflowCompleteEvent,
    WorkflowCIEvent,
    WorkflowCompleteEvent,
    WorkflowPREvent,
)
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.ci import CIStatus
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.session import PermissionMode
from tests.adapters.test_github_api import _completed_run, _empty_runs, _make_client
from tests.chains.test_ralph_workflow import (
    _make_engine,
    _SequentialReviewExecutor,
)
from tests.domain.test_outcome import _state
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeChangePersister,
    FakeCIMonitor,
    FakeGitService,
    FakePRCreator,
    FakeQualityGate,
    FakeRepoCache,
    FakeTicketGenerator,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_passing_evaluation_of_fake_criteria,
    make_prompt_provider,
    no_delay_floor,
)
from tests.workflow_factory import make_fire_workflow

DELIVERY_FIELDS = {"pr_url", "pr_number", "ci_status", "ci_summary", "ci_passed"}


def fire(*, artifacts=None, executor=None, quality: QualityGate | None = None):
    return make_fire_workflow(
        service=AgentService(
            git_base_url="https://github.com",
            executor=executor or FakeAgentExecutor(events=[]),
            workspace=FakeWorkspaceProvider(),
            persister=FakeChangePersister(),
        ),
        quality_gate=quality
        or FakeQualityGate(
            events=[],
            evaluation=make_passing_evaluation_of_fake_criteria(),
            last_commit_sha="a" * 40,
        ),
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        prompts=make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
        retry_max_attempts=1,
        retry_initial_interval=0,
        delay_floor_for=no_delay_floor,
        remediation_max_rounds=0,
        criteria_max_regeneration_rounds=1,
        fan_in_max_attempts=2,
        artifact_persister=artifacts,
    )


def request(*, issue_key=None):
    return {
        "prompt": "Implement the requested behavior",
        "issue_key": issue_key,
        "repo_path": "/tmp/fire",
        "repo_url": "https://github.com/owner/repo",
        "base_spec": trunk_base("main"),
        "scope": None,
        "permission_mode": PermissionMode.UNATTENDED,
        "allowed_tools": ["Bash"],
        "cache_key": "fire-extraction",
    }


@pytest.mark.parametrize("issue_key", [None, "native/criterion-42"])
@pytest.mark.parametrize("passed", [True, None])
async def test_actual_native_authored_delivery_runs_after_the_fire_and_clean(
    issue_key, passed
):
    requests = []
    created = []
    artifacts = FakeArtifactPersister()

    def handler(message):
        requests.append(message)
        if message.url.path == "/repos/owner/repo/pulls":
            assert message.method == "POST" and not created
            assert len(artifacts.clean_calls) == 1
            created.append(json.loads(message.content))
            return httpx.Response(
                201,
                json={"html_url": "https://github.com/owner/repo/pull/7", "number": 7},
            )
        if message.url.path.endswith("/check-runs"):
            assert created and created[0]["head"] in message.url.path
            return _completed_run() if passed is True else _empty_runs()
        if message.url.path.endswith("/actions/workflows"):
            return httpx.Response(200, json={"total_count": 0, "workflows": []})
        raise AssertionError((message.method, message.url.path))

    client = _make_client(handler, ci_no_workflows_grace_polls=1)
    engine = _make_engine(
        pr_creator=client, ci_monitor=client, artifact_persister=artifacts
    )
    try:
        events = [event async for event in engine.run(**request(issue_key=issue_key))]
    finally:
        await client.close()
    (terminal,) = [
        event for event in events if isinstance(event, WorkflowCompleteEvent)
    ]
    assert type(terminal) is AuthoredWorkflowCompleteEvent
    assert terminal.outcome is (
        WorkflowOutcome.ci_passed if passed else WorkflowOutcome.ci_not_configured
    )
    assert terminal.pr_number == 7 and terminal.pr_url.endswith("/pull/7")
    assert len(created) == 1
    assert created[0]["base"] == "main"
    if issue_key is None:
        assert "Tracker issue:" not in created[0]["body"]
    else:
        assert created[0]["body"].endswith(f"Tracker issue: {issue_key}")
    assert [message.method for message in requests if message.method != "GET"] == [
        "POST"
    ]


@pytest.mark.parametrize("issue_key", [None, "native/criterion-42"])
async def test_clean_fire_hands_off_without_delivery_fields_or_operations(issue_key):
    artifacts = FakeArtifactPersister()
    engine = fire(artifacts=artifacts)
    events = [event async for event in engine.run(**request(issue_key=issue_key))]
    (terminal,) = [
        event for event in events if isinstance(event, WorkflowCompleteEvent)
    ]
    assert type(terminal) is WorkflowCompleteEvent
    assert terminal.outcome is WorkflowOutcome.handed_off_for_delivery
    assert terminal.accepted and terminal.merged
    assert terminal.final_commit_sha == "m" * 40
    assert not DELIVERY_FIELDS.intersection(terminal.model_dump())
    assert not any(
        isinstance(event, (WorkflowPREvent, WorkflowCIEvent)) for event in events
    )
    assert artifacts.persist_calls
    assert not artifacts.clean_calls


async def test_review_failure_is_a_fire_terminal_without_a_pr_comment():
    executor = _SequentialReviewExecutor(
        review_results=[
            {
                "criteriaResults": [
                    {
                        "criterionId": "AC-1",
                        "criterion": "Tests pass",
                        "passed": False,
                        "reasoning": "The requested check fails.",
                    },
                    {
                        "criterionId": "AC-2",
                        "criterion": "No lint errors",
                        "passed": False,
                        "reasoning": "The requested check fails.",
                    },
                ],
            }
        ]
    )
    events = [event async for event in fire(executor=executor).run(**request())]
    (terminal,) = [
        event for event in events if isinstance(event, WorkflowCompleteEvent)
    ]
    assert terminal.outcome is WorkflowOutcome.review_failed_fix_budget_exhausted
    assert not any(
        isinstance(event, (WorkflowPREvent, WorkflowCIEvent)) for event in events
    )
    assert not DELIVERY_FIELDS.intersection(terminal.model_dump())


@pytest.mark.parametrize("field", sorted(DELIVERY_FIELDS))
def test_fire_terminal_refuses_delivery_facts(field):
    with pytest.raises(ValidationError):
        WorkflowCompleteEvent(
            feature_branch="feature",
            ralph_branch="ralph",
            total_iterations=1,
            accepted=True,
            outcome=WorkflowOutcome.handed_off_for_delivery,
            **{field: None},
        )


def test_fire_classifier_is_independent_of_external_delivery():
    from kodezart.types.domain.accept import AcceptVerdict

    legacy = _state(verdict=AcceptVerdict.accepted, merged=True, review_passed=True)
    state = {key: value for key, value in legacy.items() if key not in DELIVERY_FIELDS}
    assert classify_outcome(state) is WorkflowOutcome.handed_off_for_delivery
    state["merged"] = False
    with pytest.raises(ValueError, match="Unclassifiable terminal state"):
        classify_outcome(state)


@pytest.mark.parametrize("issue_key", [None, "native/criterion-42"])
async def test_authored_delivery_preserves_one_public_terminal_and_wire(issue_key):
    forge = FakePRCreator()
    checks = FakeCIMonitor(passed=True, summary="checks passed")
    artifacts = FakeArtifactPersister()
    engine = _make_engine(
        pr_creator=forge, ci_monitor=checks, artifact_persister=artifacts
    )
    events = [event async for event in engine.run(**request(issue_key=issue_key))]
    (terminal,) = [
        event for event in events if isinstance(event, WorkflowCompleteEvent)
    ]
    assert type(terminal) is AuthoredWorkflowCompleteEvent
    assert terminal.outcome is WorkflowOutcome.ci_passed
    assert terminal.ci_status is CIStatus.passed
    assert terminal.pr_url and terminal.pr_number
    assert len(forge.calls) == 1
    assert len(checks.calls) == 1
    assert len(artifacts.clean_calls) == 1
    body = forge.calls[0]["body"]
    if issue_key is None:
        assert "Tracker issue:" not in body
    else:
        assert body.splitlines()[-1] == f"Tracker issue: {issue_key}"
    assert {"prUrl", "prNumber", "ciStatus"} <= terminal.model_dump(
        by_alias=True
    ).keys()
    assert engine._delivery_compiled.get_graph().nodes["fire"].data is engine.fire.graph
