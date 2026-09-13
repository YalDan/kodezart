from tests.chains.test_native_fire import *

async def test_actual_production_router_reaches_native_scope(
    monkeypatch,
):
    from kodezart.composition.engine import build_workflow_engine
    from kodezart.core.config import AppConfig
    from tests.fakes import FakeRefPublisher

    port = CountingTracker()
    source = TrackerCriteria(tracker=port)
    executor = NativeExecutor([native_evaluation(), native_evaluation()])
    workspace = FakeWorkspaceProvider()
    service = AgentService(
        git_base_url="https://github.com",
        executor=executor,
        workspace=workspace,
        persister=FakeChangePersister(),
    )
    artifacts = FakeArtifactPersister()
    router = build_workflow_engine(
        config=AppConfig(
            ticket_review_mode=TicketReviewMode.REVIEWED,
            max_iterations=1,
            retry_max_attempts=1,
            retry_initial_interval=0.1,
        ),
        repositories=(),
        agent_service=service,
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        workspace=workspace,
        merger=FakeBranchMerger(),
        artifact_persister=artifacts,
        ref_publisher=FakeRefPublisher(),
        prompts=make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
        github_api=None,
        checkpointer=InMemorySaver(),
        criteria=source,
    )
    monkeypatch.setattr(TicketDraftOutput, "__init__", no_authored_ticket)
    # Direct fire proves constructor capability only. The outer public scope
    # router still refuses scoped jobs until its separate production slice.
    fire = router
    events = await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))
    terminal = next(
        event for event in events if isinstance(event, WorkflowCompleteEvent)
    )
    assert terminal.outcome is WorkflowOutcome.handed_off_for_delivery
    assert port.spec_reads == 1
    assert artifacts.persist_calls == []
