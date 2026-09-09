"""The tracked queue submission is the one identity every fire stage carries."""

import pytest

from kodezart.adapters.asyncio_job_queue import AsyncioJobQueue
from kodezart.chains.remediation import RemediationChain
from kodezart.composition.engine import OriginRoutedWorkflowEngine
from kodezart.core.errors import NoStructuredOutputError
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import AssistantTextEvent, WorkflowCompleteEvent
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.session import PermissionMode, SessionType
from kodezart.types.domain.workflow import WorkflowSubmission
from tests.chains.test_ralph_loop import _make_loop as quality_loop
from tests.chains.test_ralph_loop import _run_kwargs as quality_kwargs
from tests.chains.test_ralph_workflow import _make_engine
from tests.chains.test_remediation import _request
from tests.chains.test_ticket_generation import _make_loop as ticket_loop
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeQualityGate,
    FakeWorkspaceProvider,
    make_passing_evaluation_of_fake_criteria,
    make_prompt_provider,
)
from tests.services.test_run_recorder import _record


@pytest.mark.parametrize("tracked", [True, False])
async def test_real_queue_router_workflow_and_ticket_sessions_share_submission_identity(
    tracked,
):
    executor = FakeAgentExecutor(events=[])
    quality = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation_of_fake_criteria(),
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(
        executor=executor,
        quality_gate=quality,
        ticket_generator=ticket_loop(executor=executor),
    )
    router = OriginRoutedWorkflowEngine(
        forge_arm=engine,
        forge_less_arm=engine,
    )
    queue = AsyncioJobQueue(
        engine=router,
        max_concurrent_runs_per_lane=1,
        max_depth_per_lane=2,
        terminal_retention_seconds=60,
        event_buffer_retention_seconds=60,
        event_buffer_capacity=100,
    )
    await queue.start()
    try:
        job = await queue.submit(
            lane="fixture",
            request=WorkflowSubmission(
                prompt="Implement the fixture",
                issue_key="EX-42" if tracked else None,
                repo_path="/tmp/fake",
                repo_url=None,
                base_spec=trunk_base("main"),
                implied_base=None,
                scope=None,
                permission_mode=PermissionMode.UNATTENDED,
                allowed_tools=[],
            ),
        )
        events = [event async for event in queue.attach(job_id=job.job_id)]
    finally:
        await queue.stop()
    assert any(isinstance(event, WorkflowCompleteEvent) for event in events)
    identity = (
        RunIdentity(kind=RunKind.FIRE, name="EX-42", started_at=job.submitted_at)
        if tracked
        else None
    )
    fire_calls = [
        call
        for call in executor.calls
        if call["session_type"] is SessionType.TICKET_FIRE
    ]
    assert len(fire_calls) >= 5  # branch, ticket, review, criteria, verification
    assert all(call["run_identity"] == identity for call in fire_calls)
    assert quality.calls[0]["run_identity"] == identity
    if identity is not None:
        assert identity.title() != _record(RunKind.FIRE).title()


async def test_real_quality_loop_propagates_identity_to_execution_and_evaluation():
    executor = FakeAgentExecutor(
        events=[AssistantTextEvent(text="observed", model="fixture")]
    )
    loop = quality_loop(executor=executor, max_iterations=1)
    identity = _record(RunKind.FIRE).identity()
    with pytest.raises(
        NoStructuredOutputError, match="Evaluator produced no structured output"
    ):
        _ = [
            event async for event in loop.run(**quality_kwargs(), run_identity=identity)
        ]
    assert len(executor.calls) >= 2
    assert all(call["run_identity"] == identity for call in executor.calls)


async def test_real_remediation_session_retains_the_original_fire_identity():
    executor = FakeAgentExecutor(events=[])
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        git_base_url="https://forge.invalid",
    )
    chain = RemediationChain(
        service, prompts=make_prompt_provider(), skills=SUPPRESS_ALL_SKILLS
    )
    identity = _record(RunKind.FIRE).identity()
    _ = [
        event
        async for event in chain.run(
            _request(),
            repo_path="/tmp/fake",
            repo_url=None,
            cache_key="same-job",
            run_identity=identity,
        )
    ]
    assert len(executor.calls) == 1
    assert executor.calls[0]["run_identity"] == identity
