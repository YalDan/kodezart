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
async def test_actual_parent_saved_resume_after_native_external_effect(repository, monkeypatch, position):
    port, saver = tracker(), InMemorySaver()
    failure, stopped = RuntimeError(position), RuntimeError("stop after resumed writer before further effects")
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
    assert (await fire.native_graph.aget_state(config)).next == ("run_ralph_loop",)
    reset = port.reset_criterion_pending
    verifier = WriteBackVerifier.write_back
    async def reset_cut(**kwargs):
        await reset(**kwargs)
        raise failure
    async def holds_cut(self, **kwargs):
        receipt = await verifier(self, **kwargs)
        if receipt.artifact.surface.kind is SurfaceKind.CRITERION_SUB_ISSUE:
            assert receipt.verdict.value == "holds"
            raise failure
        return receipt
    if position == "after_reset":
        monkeypatch.setattr(port, "reset_criterion_pending", reset_cut)
    if position == "after_applied_holds":
        monkeypatch.setattr(WriteBackVerifier, "write_back", holds_cut)
    try:
        with pytest.raises(RuntimeError) as caught:
            await fire.native_graph.ainvoke(None, config=config)
        assert caught.value is failure
        saved = await fire.native_graph.aget_state(config, subgraphs=True)
        assert saved.next == ("run_ralph_loop",)
        assert saved.values["total_iterations"] == 0
        assert saved.values["trajectory"] is None
        assert saved.tasks[0].name == "run_ralph_loop"
        before = latest_checkpoints(saver)
        print("CHECKPOINT_CUT", json.dumps({"position": position, "parent_tasks": [{"name": task.name, "error": task.error, "state": str(task.state)} for task in saved.tasks], "checkpoints": before}, sort_keys=True))
        assert len([row for row in before if "judgments" in row["channels"]]) == 1
        child = next(row for row in before if "judgments" in row["channels"])
        assert child["judgments"] == 1
        assert child["verdicts"] == int(position == "before_commit")
        assert not await git(repository[0], "ls-remote", "origin", "refs/heads/native-loop")
        assert len([c for c in port.comments if c.body.startswith("[fixture-amendment:")]) == 1
    finally:
        monkeypatch.undo()
        await cleanup(workspace)

    async def second_answers(title, payload, kwargs):
        if title == "NativeWriterOutput":
            raise stopped
    second_executor = Executor(reproduced=True, subject={"kind": "criterion", "id": DIRECT_DONE}, mutate=second_answers)
    fresh, fresh_workspace = await actual_fire(repository, second_executor, port, saver)
    try:
        with pytest.raises(RuntimeError) as caught:
            await fresh.native_graph.ainvoke(None, config=config)
        assert caught.value is stopped
        assert [call["output_format"]["schema"]["title"] for call in second_executor.calls] == ["NativeWriterOutput"]
        after = latest_checkpoints(saver)
        print("PARENT_NONE_RESUME", json.dumps({"position": position, "checkpoints": after}, sort_keys=True))
        ralph_before = next(row for row in before if row["thread"] == "actual-parent-job-ralph" and not row["namespace"])
        ralph_after = next(row for row in after if row["thread"] == "actual-parent-job-ralph" and not row["namespace"])
        assert ralph_before["id"] != ralph_after["id"]
        assert ralph_after["step"] > ralph_before["step"]
        assert ralph_after["amendment_reports"] == 0
        old_child = next(row for row in after if row["namespace"] == child["namespace"])
        assert old_child["id"] == child["id"]
    finally:
        await cleanup(fresh_workspace)
