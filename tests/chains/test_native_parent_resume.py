"""Fresh production engine/provider replay over real nested checkpoints and Git."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from kodezart.adapters.job_registry import InMemoryJobRegistry
from kodezart.chains.criteria import TrackerCriteria
from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.composition.engine import build_workflow_engine
from kodezart.config.app import AppConfig
from kodezart.config.write_back import WriteBackSettings
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig, RepoEntry
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode, ToolPreset
from kodezart.types.domain.surface import SurfaceKind
from kodezart.types.domain.ticket_review import TicketReviewMode
from tests.chains.test_native_fire import DIRECT_DONE, SUBJECT, tracker
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeRefPublisher,
    FakeRepoCache,
    FakeScopeStatusWriter,
    PassThroughGate,
)
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_native_amendments import (
    REPO_URL,
    Executor,
    build,
    git,
    repository,
)

__all__ = ["repository"]


async def actual_fire(repository, executor, port, saver, *, held=None):
    service, _, workspace, _ = await build(repository, executor, port=port, held=held)
    source = TrackerCriteria(tracker=port)
    router = build_workflow_engine(
        config=AppConfig(
            write_back=WriteBackSettings(max_verify_rounds=2),
            ticket_review_mode=TicketReviewMode.REVIEWED,
            max_iterations=1,
            retry_max_attempts=1,
            retry_initial_interval=0.1,
        ),
        operation=OperationConfig(
            operation_name="fixture",
            workspace="fixture",
            marker_prefixes={
                "ruling": "fixture-pinned",
                "amendment": "fixture-amendment",
                "escalation": "fixture-escalation",
                "run_state": "fixture-run-state",
                "run_event": "fixture-run-event",
            },
            issue_labels={"decision": "decision"},
        ),
        scope_tracker=port,
        scope_registry=InMemoryJobRegistry(),
        scope_status=FakeScopeStatusWriter(),
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
    return router.arm_for(None).fire, workspace


@pytest.mark.parametrize(
    "position", ["after_archive", "after_reset", "after_applied_holds", "before_commit"]
)
async def test_fresh_parent_resumes_original_native_phase(
    repository, monkeypatch, position
):
    from pathlib import Path

    from kodezart.domain.amendment import NativeWriteRefusalError

    port, saver = tracker(), InMemorySaver()
    failure = RuntimeError(position)
    verifies = 0

    async def first_answers(title, payload, kwargs):
        nonlocal verifies
        if title == "WriteBackFinding":
            verifies += 1
            if position == "after_archive" and verifies == 1:
                raise failure
        if title == "CommitMessageOutput" and position == "before_commit":
            raise failure

    executor = Executor(
        reproduced=True,
        subject={"kind": "criterion", "id": DIRECT_DONE},
        mutate=first_answers,
    )
    fire, workspace = await actual_fire(repository, executor, port, saver)
    spec = await fire.criteria.read_spec(issue_key=SUBJECT)
    criteria = await fire.criteria.read_current(spec=spec)
    await git(repository[0], "branch", "native-feature", "main")
    state, config = fire.prepare(
        prompt="Implement exact native subject",
        repo_path=str(repository[0]),
        repo_url=None,
        base_spec=trunk_base(repository[1]),
        scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT),
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=ToolPreset.IMPLEMENTATION,
        cache_key="actual-parent-job",
        surface_holder="actual-parent-job",
    )
    state.update(
        fire_spec=spec,
        criterion_set=criteria,
        feature_branch="native-feature",
        ralph_branch="native-loop",
        work_base_ref="native-feature",
        repo_visibility=RepoVisibility.PUBLIC,
    )
    await fire.native_graph.aupdate_state(config, state, as_node="revalidate_criteria")
    reset, verifier = port.reset_criterion_pending, WriteBackVerifier.write_back

    async def reset_cut(**kwargs):
        await reset(**kwargs)
        raise failure

    async def holds_cut(self, **kwargs):
        receipt = await verifier(self, **kwargs)
        if receipt.artifact.surface.kind is SurfaceKind.CRITERION_SUB_ISSUE:
            raise failure
        return receipt

    if position == "after_reset":
        monkeypatch.setattr(port, "reset_criterion_pending", reset_cut)
    if position == "after_applied_holds":
        monkeypatch.setattr(WriteBackVerifier, "write_back", holds_cut)
    fresh_workspace = None
    try:
        with pytest.raises(RuntimeError) as caught:
            await fire.native_graph.ainvoke(None, config=config)
        assert caught.value is failure
        saved = await fire.native_graph.aget_state(config, subgraphs=True)
        assert saved.next == ("run_ralph_loop",)
        assert saved.values["total_iterations"] == 0
        assert saved.values["trajectory"] is None
        native_calls = [
            call
            for call in executor.calls
            if call.get("output_format", {}).get("schema", {}).get("title")
            == "NativeWriterOutput"
        ]
        assert len(native_calls) == 1
        original_workspace = native_calls[0]["cwd"]
        assert Path(original_workspace).exists()
        assert original_workspace not in workspace.released
        assert not await git(
            repository[0], "ls-remote", "origin", "refs/heads/native-loop"
        )
        assert (
            len([c for c in port.comments if c.body.startswith("[fixture-amendment:")])
            == 1
        )
        monkeypatch.undo()

        async def second_answers(title, payload, kwargs):
            if title == "AcceptanceCriteriaOutput":
                current = await TrackerCriteria(tracker=port).read_current(spec=spec)
                payload.update(
                    criteria_results=[
                        {
                            "criterion_id": c.id,
                            "criterion": c.text,
                            "passed": True,
                            "reasoning": "Checked current behavior.",
                        }
                        for c in current.criteria
                    ],
                    sherlock_flags=[],
                )

        second_executor = Executor(
            reproduced=True,
            subject={"kind": "criterion", "id": DIRECT_DONE},
            mutate=second_answers,
        )
        fresh, fresh_workspace = await actual_fire(
            repository, second_executor, port, saver
        )
        if position in {"after_reset", "after_applied_holds"}:
            with pytest.raises(NativeWriteRefusalError):
                await fresh.native_graph.ainvoke(None, config=config)
            assert not second_executor.calls
            assert Path(original_workspace).exists()
            assert not await git(
                repository[0], "ls-remote", "origin", "refs/heads/native-loop"
            )
        else:
            await fresh.native_graph.ainvoke(
                None, config=config, interrupt_after=["run_ralph_loop"]
            )
            final = await fresh.native_graph.aget_state(config)
            assert final.values["total_iterations"] == 1
            assert final.values["trajectory"] is not None
            assert not Path(original_workspace).exists()
            assert original_workspace in fresh_workspace.released
            assert await git(
                repository[0], "ls-remote", "origin", "refs/heads/native-loop"
            )
        titles = [
            call.get("output_format", {}).get("schema", {}).get("title")
            for call in second_executor.calls
        ]
        assert "NativeWriterOutput" not in titles
        assert "AmendmentJudgment" not in titles
        if position == "before_commit":
            assert titles == ["CommitMessageOutput", "AcceptanceCriteriaOutput"]
        assert (
            len([c for c in port.comments if c.body.startswith("[fixture-amendment:")])
            == 1
        )
    finally:
        monkeypatch.undo()
        for owner in (workspace, fresh_workspace):
            if owner is not None:
                for path in tuple(owner._workspaces):
                    if Path(path).exists():
                        await owner.release(path)
                    else:
                        owner._workspaces.pop(path, None)


async def prepared_parent(repository, executor, port, saver):
    fire, workspace = await actual_fire(repository, executor, port, saver)
    spec = await fire.criteria.read_spec(issue_key=SUBJECT)
    criteria = await fire.criteria.read_current(spec=spec)
    await git(repository[0], "branch", "native-feature", "main")
    state, config = fire.prepare(
        prompt="Implement exact native subject",
        repo_path=str(repository[0]),
        repo_url=None,
        base_spec=trunk_base(repository[1]),
        scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT),
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=ToolPreset.IMPLEMENTATION,
        cache_key="actual-parent-job",
        surface_holder="actual-parent-job",
    )
    state.update(
        fire_spec=spec,
        criterion_set=criteria,
        feature_branch="native-feature",
        ralph_branch="native-loop",
        work_base_ref="native-feature",
        repo_visibility=RepoVisibility.PUBLIC,
    )
    await fire.native_graph.aupdate_state(config, state, as_node="revalidate_criteria")
    return fire, workspace, spec, config


@pytest.mark.parametrize("change", ["unchanged", "check", "state", "outage", "head"])
@pytest.mark.parametrize("no_change", [False, True])
async def test_completed_loop_replay_uses_actual_evaluation_and_current_ref(
    repository,
    monkeypatch,
    change,
    no_change,
):
    from pathlib import Path

    from kodezart.domain.amendment import NativeWriteRefusalError
    from kodezart.domain.errors import FireSpecEntryError
    from kodezart.types.domain.agent import WorkflowIterationEvent
    from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind
    from tests.chains.test_native_fire import DIRECT_OWED

    port, saver = tracker(), InMemorySaver()
    failure = RuntimeError("after actual loop completion before parent receipt")

    async def answers(title, payload, kwargs):
        if no_change and title == "NativeWriterOutput":
            Path(kwargs["cwd"], "change.py").unlink()
        if title == "AcceptanceCriteriaOutput":
            source = TrackerCriteria(tracker=port)
            spec = await source.read_spec(issue_key=SUBJECT)
            current = await source.read_current(spec=spec)
            payload.update(
                criteria_results=[
                    {
                        "criterion_id": c.id,
                        "criterion": c.text,
                        "passed": True,
                        "reasoning": "Observed exact current behavior.",
                    }
                    for c in current.criteria
                ],
                sherlock_flags=[],
            )

    if no_change:
        await git(repository[0], "push", "origin", "HEAD:refs/heads/native-loop")
    executor = Executor(claim=False, mutate=answers)
    fire, workspace, _spec, config = await prepared_parent(
        repository, executor, port, saver
    )
    actual = fire.implementation.run_quality_gate
    observed = []

    async def cut_after_loop(**kwargs):
        event = await actual(**kwargs)
        observed.append(event)
        raise failure

    monkeypatch.setattr(fire.implementation, "run_quality_gate", cut_after_loop)
    with pytest.raises(RuntimeError) as caught:
        await fire.native_graph.ainvoke(None, config=config)
    assert caught.value is failure
    assert len(observed) == 1 and isinstance(observed[0], WorkflowIterationEvent)
    assert (observed[0].commit_sha is None) is no_change
    assert not workspace._workspaces
    saved = await fire.native_graph.aget_state(config)
    assert saved.values["total_iterations"] == 0
    monkeypatch.undo()
    resumed_executor = Executor(claim=False, mutate=answers)
    # The loop crossed its criteria off, so the replay's wiring reads this
    # run's obligations against the roster the run itself holds.
    fresh, fresh_workspace = await actual_fire(
        repository,
        resumed_executor,
        port,
        saver,
        held=saved.values["criterion_set"],
    )
    before_remote = await git(
        repository[0], "ls-remote", "origin", "refs/heads/native-loop"
    )
    if change == "check":
        prior = port.issues[DIRECT_OWED]
        port.issues[DIRECT_OWED] = TrackerIssue.model_validate(
            {
                **prior.model_dump(),
                "body": "**Check:** changed after grading\n**Do:** current",
            }
        )
    elif change == "state":
        prior = port.issues[DIRECT_OWED]
        port.issues[DIRECT_OWED] = TrackerIssue.model_validate(
            {
                **prior.model_dump(),
                # Neither Todo nor Done: the board took this criterion out of
                # the run's obligation. Done is the state the run's own
                # evaluation step moves a criterion of its roster to, and such
                # a criterion stays inside the set it was judged against.
                "state_name": "In Progress",
                "state_kind": WorkflowStateKind.STARTED,
            }
        )
    elif change == "outage":

        async def unavailable(**kwargs):
            raise ConnectionError("tracker unavailable on completed replay")

        monkeypatch.setattr(port, "scope_issues", unavailable)
    elif change == "head":
        await git(repository[0], "update-ref", "refs/heads/native-loop", repository[1])
    if change == "unchanged":
        await fresh.native_graph.ainvoke(
            None, config=config, interrupt_after=["run_ralph_loop"]
        )
        final = await fresh.native_graph.aget_state(config)
        assert final.values["total_iterations"] == observed[0].iteration
        assert final.values["trajectory"] == observed[0].trajectory
    else:
        with pytest.raises((NativeWriteRefusalError, FireSpecEntryError)):
            await fresh.native_graph.ainvoke(
                None, config=config, interrupt_after=["run_ralph_loop"]
            )
        final = await fresh.native_graph.aget_state(config)
        assert final.values["total_iterations"] == 0
    assert not resumed_executor.calls
    assert not fresh_workspace.acquired
    assert not fresh_workspace._workspaces
    assert (
        await git(repository[0], "ls-remote", "origin", "refs/heads/native-loop")
        == before_remote
    )


@pytest.mark.parametrize("change", ["workspace", "archive", "holder", "outage"])
async def test_reconciled_checkpoint_refuses_invalidated_native_authority(
    repository,
    monkeypatch,
    change,
):
    from datetime import UTC, datetime
    from pathlib import Path

    from kodezart.domain.amendment import NativeWriteRefusalError
    from kodezart.domain.errors import FireSpecEntryError
    from kodezart.types.domain.operation import RunKind
    from kodezart.types.domain.run_records import RunIdentity

    port, saver = tracker(), InMemorySaver()
    failure = RuntimeError("before the harness commit message completes")

    async def cut(title, payload, kwargs):
        if title == "CommitMessageOutput":
            raise failure

    executor = Executor(
        reproduced=True, subject={"kind": "criterion", "id": DIRECT_DONE}, mutate=cut
    )
    fire, workspace, _spec, config = await prepared_parent(
        repository, executor, port, saver
    )
    identity = RunIdentity(
        kind=RunKind.FIRE, name="actual-parent-job", started_at=datetime.now(UTC)
    )
    config["configurable"]["run_identity"] = identity.model_dump(mode="json")
    fresh_workspace = None
    try:
        with pytest.raises(RuntimeError) as caught:
            await fire.native_graph.ainvoke(None, config=config)
        assert caught.value is failure
        path = next(
            call["cwd"]
            for call in executor.calls
            if call.get("output_format", {}).get("schema", {}).get("title")
            == "NativeWriterOutput"
        )
        assert Path(path).exists()
        phase = next(
            item.checkpoint["channel_values"]["execution"]
            for item in saver.list(None)
            if "execution" in item.checkpoint["channel_values"]
        )
        assert phase.phase == "reconciled"
        assert phase.run_identity == identity
        assert len(phase.authority.archives) == 1
        second = Executor(reproduced=True)
        fresh, fresh_workspace = await actual_fire(repository, second, port, saver)
        if change == "workspace":
            Path(path, "change.py").write_text("substituted writer content")
        elif change == "archive":
            port.comments[:] = [
                c for c in port.comments if not c.body.startswith("[fixture-amendment:")
            ]
        elif change == "holder":
            config["configurable"]["surface_holder"] = "another-parent-job"
        else:

            async def unavailable(**kwargs):
                raise ConnectionError("tracker outage on phase replay")

            monkeypatch.setattr(port, "scope_issues", unavailable)
        with pytest.raises((NativeWriteRefusalError, FireSpecEntryError)):
            await fresh.native_graph.ainvoke(None, config=config)
        assert not second.calls
        assert Path(path).exists()
        assert not await git(
            repository[0], "ls-remote", "origin", "refs/heads/native-loop"
        )
    finally:
        monkeypatch.undo()
        for owner in (workspace, fresh_workspace):
            if owner is not None:
                for path in tuple(owner._workspaces):
                    if Path(path).exists():
                        await owner.release(path)
                    else:
                        owner._workspaces.pop(path, None)


async def test_commit_without_saved_receipt_refuses_duplicate_persistence(
    repository,
    monkeypatch,
):
    from pathlib import Path

    from kodezart.domain.amendment import NativeWriteRefusalError

    port, saver = tracker(), InMemorySaver()
    executor = Executor(claim=False)
    fire, workspace, _spec, config = await prepared_parent(
        repository, executor, port, saver
    )
    persister = fire.implementation._quality_gate._service._persister
    persist = persister.persist
    failure = RuntimeError("after actual publish receipt before phase checkpoint")
    receipts = []

    async def cut(**kwargs):
        receipt = await persist(**kwargs)
        receipts.append(receipt)
        raise failure

    monkeypatch.setattr(persister, "persist", cut)
    fresh_workspace = None
    try:
        with pytest.raises(RuntimeError) as caught:
            await fire.native_graph.ainvoke(None, config=config)
        assert caught.value is failure
        assert len(receipts) == 1 and receipts[0] is not None
        original_sha = receipts[0].commit_sha
        path = next(
            call["cwd"]
            for call in executor.calls
            if call.get("output_format", {}).get("schema", {}).get("title")
            == "NativeWriterOutput"
        )
        assert await git(path, "rev-parse", "HEAD") == original_sha
        before = await git(
            repository[0], "ls-remote", "origin", "refs/heads/native-loop"
        )
        assert original_sha in before
        monkeypatch.undo()
        second = Executor(claim=False)
        fresh, fresh_workspace = await actual_fire(repository, second, port, saver)
        with pytest.raises(NativeWriteRefusalError):
            await fresh.native_graph.ainvoke(None, config=config)
        assert not second.calls
        assert Path(path).exists()
        assert await git(path, "rev-parse", "HEAD") == original_sha
        assert (
            await git(repository[0], "ls-remote", "origin", "refs/heads/native-loop")
            == before
        )
    finally:
        monkeypatch.undo()
        for owner in (workspace, fresh_workspace):
            if owner is not None:
                for path in tuple(owner._workspaces):
                    if Path(path).exists():
                        await owner.release(path)
                    else:
                        owner._workspaces.pop(path, None)


async def test_saved_persist_receipt_resumes_without_writer_commit_or_push(
    repository,
    monkeypatch,
):
    from pathlib import Path

    from kodezart.services.native_execution import NativeExecution
    from kodezart.types.domain.native_execution import PersistedNativeExecution

    port, saver = tracker(), InMemorySaver()
    executor = Executor(claim=False)
    fire, workspace, spec, config = await prepared_parent(
        repository, executor, port, saver
    )
    restore = NativeExecution._restore
    failure = RuntimeError("after saved commit receipt before parent consumer")

    async def cut(self, phase):
        if isinstance(phase, PersistedNativeExecution):
            raise failure
        await restore(self, phase)

    monkeypatch.setattr(NativeExecution, "_restore", cut)
    fresh_workspace = None
    try:
        with pytest.raises(RuntimeError) as caught:
            await fire.native_graph.ainvoke(None, config=config)
        assert caught.value is failure
        phase = next(
            item.checkpoint["channel_values"]["execution"]
            for item in saver.list(None)
            if "execution" in item.checkpoint["channel_values"]
        )
        assert isinstance(phase, PersistedNativeExecution)
        path = phase.workspace.workspace_path
        assert Path(path).exists()
        before = await git(
            repository[0], "ls-remote", "origin", "refs/heads/native-loop"
        )
        assert phase.receipt.commit_sha in before
        monkeypatch.undo()

        async def answers(title, payload, kwargs):
            if title == "AcceptanceCriteriaOutput":
                current = await TrackerCriteria(tracker=port).read_current(spec=spec)
                payload.update(
                    criteria_results=[
                        {
                            "criterion_id": c.id,
                            "criterion": c.text,
                            "passed": True,
                            "reasoning": "Observed the pinned result.",
                        }
                        for c in current.criteria
                    ],
                    sherlock_flags=[],
                )

        second = Executor(claim=False, mutate=answers)
        fresh, fresh_workspace = await actual_fire(repository, second, port, saver)
        await fresh.native_graph.ainvoke(
            None, config=config, interrupt_after=["run_ralph_loop"]
        )
        final = await fresh.native_graph.aget_state(config)
        assert final.values["total_iterations"] == 1
        assert [call["output_format"]["schema"]["title"] for call in second.calls] == [
            "AcceptanceCriteriaOutput"
        ]
        assert (
            final.values["trajectory"].records[0].commit_sha == phase.receipt.commit_sha
        )
        assert not Path(path).exists()
        assert path in fresh_workspace.released
        assert (
            await git(repository[0], "ls-remote", "origin", "refs/heads/native-loop")
            == before
        )
    finally:
        monkeypatch.undo()
        for owner in (workspace, fresh_workspace):
            if owner is not None:
                for path in tuple(owner._workspaces):
                    if Path(path).exists():
                        await owner.release(path)
                    else:
                        owner._workspaces.pop(path, None)


async def test_cancelled_incomplete_writer_releases_and_saved_parent_refuses_restart(
    repository,
):
    import asyncio
    from pathlib import Path

    from kodezart.domain.amendment import NativeWriteRefusalError
    from kodezart.types.domain.native_execution import PreparedNativeExecution

    writer_started = asyncio.Event()

    async def cancel_writer(title, payload, kwargs):
        if title == "NativeWriterOutput":
            writer_started.set()
            await asyncio.Event().wait()

    port, saver = tracker(), InMemorySaver()
    executor = Executor(claim=False, mutate=cancel_writer)
    fire, workspace, _spec, config = await prepared_parent(
        repository, executor, port, saver
    )
    invocation = asyncio.create_task(fire.native_graph.ainvoke(None, config=config))
    try:
        await writer_started.wait()
        invocation.cancel("incomplete original writer")
        with pytest.raises(asyncio.CancelledError):
            await invocation
        assert invocation.cancelled()
    finally:
        if not invocation.done():
            invocation.cancel()
            await asyncio.gather(invocation, return_exceptions=True)
    phase = next(
        item.checkpoint["channel_values"]["execution"]
        for item in saver.list(None)
        if "execution" in item.checkpoint["channel_values"]
    )
    assert isinstance(phase, PreparedNativeExecution)
    path = phase.workspace.workspace_path
    assert path in workspace.released
    assert not Path(path).exists()
    assert await git(repository[0], "rev-parse", "native-loop") == phase.start.head_sha
    assert not await git(repository[0], "ls-remote", "origin", "refs/heads/native-loop")
    assert not port.comments

    second = Executor(claim=False)
    fresh, fresh_workspace = await actual_fire(repository, second, port, saver)
    with pytest.raises(NativeWriteRefusalError, match="cannot be resumed"):
        await fresh.native_graph.ainvoke(None, config=config)
    assert not second.calls
    assert not fresh_workspace._workspaces
    assert not port.comments
