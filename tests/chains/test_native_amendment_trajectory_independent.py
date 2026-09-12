"""An actual upheld departure must not invent an evaluator observation."""

from kodezart.chains.criteria import TrackerCriteria
from kodezart.composition.engine import build_workflow_engine
from kodezart.core.config import AppConfig
from kodezart.domain.criteria_grading import grade_iteration
from kodezart.domain.trajectory import fold_trajectory
from kodezart.types.domain.agent import (
    AcceptanceCriteriaOutput,
    NativeAmendmentEvent,
    WorkflowIterationEvent,
)
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.session import PermissionMode, ToolPreset
from kodezart.types.domain.ticket_review import TicketReviewMode
from kodezart.types.domain.trajectory import IterationRecord
from kodezart.types.domain.workflow import RalphLoopContext
from tests.chains.test_native_fire import SUBJECT, native_evaluation, native_operation
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
    cleanup,
    git,
    repository,
)

__all__ = ["repository"]


async def test_upheld_after_real_grade_preserves_criterion_history_and_plateau(
    repository,
):
    executor = Executor()
    service, _, workspace, port = await build(repository, executor)
    criteria = TrackerCriteria(tracker=port)
    spec = await criteria.read_spec(issue_key=SUBJECT)
    current = await criteria.read_current(spec=spec)
    observed = native_evaluation()
    observed["criteriaResults"][0]["passed"] = False
    previous_grade = grade_iteration(
        current.criteria, AcceptanceCriteriaOutput.model_validate(observed)
    )
    previous_record = IterationRecord(
        iteration=1,
        passed_count=previous_grade.passed_count,
        failing_criterion_ids=[row.criterion_id for row in previous_grade.failures],
        commit_sha=await git(repository[0], "rev-parse", "HEAD"),
    )
    previous_records = [
        previous_record,
        previous_record.model_copy(update={"iteration": 2}),
    ]
    await git(repository[0], "branch", "native-loop", "main")
    router = build_workflow_engine(
        config=AppConfig(
            ticket_review_mode=TicketReviewMode.REVIEWED,
            max_iterations=3,
            loop_plateau_window=2,
            retry_max_attempts=1,
            retry_initial_interval=0.1,
        ),
        operation=native_operation(),
        scope_tracker=port,
        criteria=criteria,
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
        checkpointer=None,
    )
    loop = router.arm_for(None).fire.implementation._quality_gate
    context = RalphLoopContext(
        prompt="Implement the exact native Checks",
        repo_path=str(repository[0]),
        repo_url=REPO_URL,
        cache_key="independent-upheld-history",
        base_spec=trunk_base(repository[1]),
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=ToolPreset.IMPLEMENTATION,
        feature_branch="native-feature",
        ralph_branch="native-loop",
        work_base_ref="main",
        acceptance_criteria=list(current.criteria),
        tracker_spec=spec,
        repo_visibility=RepoVisibility.PUBLIC,
    )
    state = {
        "iteration": 2,
        "verdict": previous_grade.verdict,
        "pending_failures": previous_grade.failures,
        "iteration_records": previous_records,
    }
    events = []
    final = None
    try:
        async for mode, value in loop._compiled.astream(
            state,
            {"configurable": context.model_dump()},
            stream_mode=["custom", "values"],
        ):
            if mode == "custom":
                events.append(value)
            else:
                final = value
        assert final is not None
        assert any(isinstance(event, NativeAmendmentEvent) for event in events)
        assert len(executor.calls) == 2
        assert not await git(
            repository[0], "ls-remote", "origin", "refs/heads/native-loop"
        )
        actual = {
            "records": final["iteration_records"],
            "failures": final["pending_failures"],
            "plateaued": fold_trajectory(
                final["iteration_records"], plateau_window=2
            ).plateaued,
            "new_evaluations": [
                event for event in events if isinstance(event, WorkflowIterationEvent)
            ],
        }
        assert actual == {
            "records": previous_records,
            "failures": previous_grade.failures,
            "plateaued": False,
            "new_evaluations": [],
        }
    finally:
        await cleanup(workspace)
