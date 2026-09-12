"""The production constructor drives real native guard/report consumers."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from kodezart.chains.criteria import TrackerCriteria
from kodezart.composition.engine import build_workflow_engine
from kodezart.core.config import AppConfig
from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.domain.thread_id import ralph_thread_id
from kodezart.types.domain.agent import NativeAmendmentEvent, WorkflowIterationEvent
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig, RepoEntry
from kodezart.types.domain.session import PermissionMode, ToolPreset
from kodezart.types.domain.ticket_review import TicketReviewMode
from tests.chains.test_native_fire import SUBJECT
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeRefPublisher,
    FakeRepoCache,
    PassThroughGate,
)
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_native_amendments import (
    REPO_URL,
    Executor,
    build,
    repository,
)

__all__ = ["repository"]


@pytest.mark.parametrize("configured", [True, False])
async def test_native_builder_retains_reports_and_requires_the_actual_owner(
    repository,
    configured,
):
    executor = Executor()
    service, _, workspace, port = await build(repository, executor)
    source = TrackerCriteria(tracker=port)
    spec = await source.read_spec(issue_key=SUBJECT)
    current = await source.read_current(spec=spec)
    saver = InMemorySaver()
    operation = OperationConfig(
        operation_name="fixture",
        workspace="fixture",
        marker_prefixes={"ruling": "fixture-pinned"},
    )
    router = build_workflow_engine(
        config=AppConfig(
            ticket_review_mode=TicketReviewMode.REVIEWED,
            max_iterations=2,
            retry_max_attempts=1,
            retry_initial_interval=0.1,
        ),
        operation=operation if configured else None,
        scope_tracker=port,
        criteria=source,
        repositories=(RepoEntry(url=REPO_URL, trunk="main"),),
        agent_service=service,
        git=workspace._git,
        cache=FakeRepoCache(str(repository[0])),
        workspace=workspace,
        merger=FakeBranchMerger(),
        artifact_persister=FakeArtifactPersister(),
        ref_publisher=FakeRefPublisher(),
        prompts=load_registry(),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
        github_api=None,
        checkpointer=saver,
    )
    loop = router.arm_for(None).fire.implementation._quality_gate

    async def run():
        return [
            event
            async for event in loop.run(
                prompt="Implement the native subject",
                repo_path=str(repository[0]),
                repo_url=REPO_URL,
                feature_branch="native-feature",
                ralph_branch="native-loop",
                base_spec=trunk_base(repository[1]),
                work_base_ref="main",
                permission_mode=PermissionMode.UNATTENDED,
                allowed_tools=ToolPreset.IMPLEMENTATION,
                acceptance_criteria=list(current.criteria),
                tracker_spec=spec,
                cache_key="semantic-checkpoint",
                repo_visibility=RepoVisibility.PUBLIC,
            )
        ]

    if not configured:
        with pytest.raises(NativeWriteRefusalError, match="precommit amendment owner"):
            await run()
        assert executor.calls == []
        return
    events = await run()
    reports = [event for event in events if isinstance(event, NativeAmendmentEvent)]
    assert len(reports) == 2
    assert reports[0].repeated == ()
    assert reports[1].repeated[0].count == 2
    assert (
        len([event for event in events if isinstance(event, WorkflowIterationEvent)])
        == 2
    )
    assert all(
        "not committed or evaluated" in event.evaluation.criteria_results[0].reasoning
        for event in events
        if isinstance(event, WorkflowIterationEvent)
    )
    state = await loop._compiled.aget_state(
        {
            "configurable": {
                "thread_id": ralph_thread_id("semantic-checkpoint"),
            }
        }
    )
    assert state.values["amendment_reports"] == [event.report for event in reports]
    assert len(executor.calls) == 4
    assert all(call["session_id"] is None for call in executor.calls)
