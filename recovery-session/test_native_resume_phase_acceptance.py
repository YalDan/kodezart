"""Author-owned observations of actual parent and native child checkpoint cuts."""
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from kodezart.chains.criteria import TrackerCriteria
from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.composition.engine import build_workflow_engine
from kodezart.core.config import AppConfig
from kodezart.core.write_back_settings import WriteBackSettings
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig, RepoEntry
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode, ToolPreset
from kodezart.types.domain.surface import SurfaceKind
from kodezart.types.domain.ticket_review import TicketReviewMode
from tests.chains.test_native_fire import DIRECT_DONE, SUBJECT, tracker
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeArtifactPersister, FakeBranchMerger, FakeRefPublisher, FakeRepoCache, PassThroughGate
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_native_amendments import REPO_URL, Executor, build, cleanup, git, repository

__all__ = ["repository"]

async def actual_fire(repository, executor, port, saver):
    service, _, workspace, _ = await build(repository, executor, port=port)
    source = TrackerCriteria(tracker=port)
    router = build_workflow_engine(
        config=AppConfig(write_back=WriteBackSettings(max_verify_rounds=2), ticket_review_mode=TicketReviewMode.REVIEWED, max_iterations=1, retry_max_attempts=1, retry_initial_interval=0.1),
        operation=OperationConfig(operation_name="fixture", workspace="fixture", marker_prefixes={"ruling": "fixture-pinned", "amendment": "fixture-amendment", "escalation": "fixture-escalation"}, issue_labels={"decision": "decision"}),
        scope_tracker=port, criteria=source, repositories=(RepoEntry(url=REPO_URL, trunk="main"),),
        agent_service=service, git=workspace._git, cache=FakeRepoCache(str(repository[0])), workspace=workspace,
        merger=FakeBranchMerger(), artifact_persister=FakeArtifactPersister(), ref_publisher=FakeRefPublisher(),
        prompts=load_registry(), skills=SUPPRESS_ALL_SKILLS, gate=PassThroughGate(), github_api=None, checkpointer=saver,
    )
    return router.arm_for(None).fire, workspace


def latest_checkpoints(saver):
    latest = {}
    for item in saver.list(None):
        address = (item.config["configurable"]["thread_id"], item.config["configurable"].get("checkpoint_ns", ""))
        if address not in latest:
            channels = item.checkpoint["channel_values"]
            latest[address] = {
                "id": item.checkpoint["id"], "step": item.metadata.get("step"),
                "channels": sorted(channels),
                "judgments": len(channels.get("judgments", ())), "verdicts": len(channels.get("verdicts", ())),
                "amendment_reports": len(channels.get("amendment_reports", ())),
                "pending": [(channel, str(value)) for _, channel, value in item.pending_writes or ()],
            }
    return [{"thread": thread, "namespace": ns, **value} for (thread, ns), value in latest.items()]


@pytest.mark.parametrize("position", ["after_archive", "after_reset", "after_applied_holds", "before_commit"])
async def test_fresh_parent_resumes_original_native_phase(repository, monkeypatch, position):
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
    executor = Executor(reproduced=True, subject={"kind": "criterion", "id": DIRECT_DONE}, mutate=first_answers)
    fire, workspace = await actual_fire(repository, executor, port, saver)
    spec = await fire.criteria.read_spec(issue_key=SUBJECT)
    criteria = await fire.criteria.read_current(spec=spec)
    await git(repository[0], "branch", "native-feature", "main")
    state, config = fire.prepare(prompt="Implement exact native subject", repo_path=str(repository[0]), repo_url=None, base_spec=trunk_base(repository[1]), scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT), permission_mode=PermissionMode.UNATTENDED, allowed_tools=ToolPreset.IMPLEMENTATION, cache_key="actual-parent-job", surface_holder="actual-parent-job")
    state.update(fire_spec=spec, criterion_set=criteria, feature_branch="native-feature", ralph_branch="native-loop", work_base_ref="native-feature", repo_visibility=RepoVisibility.PUBLIC)
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
        native_calls = [call for call in executor.calls if call.get("output_format", {}).get("schema", {}).get("title") == "NativeWriterOutput"]
        assert len(native_calls) == 1
        original_workspace = native_calls[0]["cwd"]
        assert Path(original_workspace).exists()
        assert original_workspace not in workspace.released
        assert not await git(repository[0], "ls-remote", "origin", "refs/heads/native-loop")
        assert len([c for c in port.comments if c.body.startswith("[fixture-amendment:")]) == 1
        monkeypatch.undo()
        async def second_answers(title, payload, kwargs):
            if title == "AcceptanceCriteriaOutput":
                current = await TrackerCriteria(tracker=port).read_current(spec=spec)
                payload.update(criteria_results=[{"criterion_id": c.id, "criterion": c.text, "passed": True, "reasoning": "Actual evaluator output against the current Check."} for c in current.criteria], sherlock_flags=[])
        second_executor = Executor(reproduced=True, subject={"kind": "criterion", "id": DIRECT_DONE}, mutate=second_answers)
        fresh, fresh_workspace = await actual_fire(repository, second_executor, port, saver)
        if position in {"after_reset", "after_applied_holds"}:
            with pytest.raises(NativeWriteRefusalError):
                await fresh.native_graph.ainvoke(None, config=config)
            assert not second_executor.calls
            assert Path(original_workspace).exists()
            assert not await git(repository[0], "ls-remote", "origin", "refs/heads/native-loop")
        else:
            await fresh.native_graph.ainvoke(None, config=config, interrupt_after=["run_ralph_loop"])
            final = await fresh.native_graph.aget_state(config)
            assert final.values["total_iterations"] == 1
            assert final.values["trajectory"] is not None
            assert not Path(original_workspace).exists()
            assert original_workspace in fresh_workspace.released
            assert await git(repository[0], "ls-remote", "origin", "refs/heads/native-loop")
        titles = [call.get("output_format", {}).get("schema", {}).get("title") for call in second_executor.calls]
        assert "NativeWriterOutput" not in titles
        assert "AmendmentJudgment" not in titles
        if position == "before_commit":
            assert titles == ["CommitMessageOutput", "AcceptanceCriteriaOutput"]
        assert len([c for c in port.comments if c.body.startswith("[fixture-amendment:")]) == 1
    finally:
        monkeypatch.undo()
        for owner in (workspace, fresh_workspace):
            if owner is not None:
                for path in tuple(owner._workspaces):
                    if Path(path).exists():
                        await owner.release(path)
                    else:
                        owner._workspaces.pop(path, None)
