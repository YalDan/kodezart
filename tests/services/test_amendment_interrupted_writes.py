"""Actual partial native writes survive refusal and a fresh same-branch entry."""

import json

import pytest

from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.domain.fire_spec import criterion_field_bodies
from kodezart.types.domain.agent import NativeAmendmentEvent, ResultEvent
from kodezart.types.domain.amendment_write import AmendmentRecord
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.session import PermissionMode, SessionType, ToolPreset
from kodezart.types.domain.surface import SurfaceKind
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.chains.test_native_fire import DIRECT_DONE, tracker
from tests.fakes import SUPPRESS_ALL_SKILLS
from tests.lane_fixture import RecordingAfterPublish
from tests.services.test_native_amendments import (
    Executor,
    build,
    cleanup,
    drive,
    git,
    repository,
)

__all__ = ["repository"]


@pytest.mark.parametrize(
    "position", ["after_archive", "after_reset", "after_applied_holds", "before_commit"]
)
async def test_interrupted_amendment_preserves_history_across_fresh_reentry(
    repository, monkeypatch, position
):
    port = tracker()
    original = port.issues[DIRECT_DONE]
    failure = RuntimeError(f"injected interruption {position}")
    verifies = 0
    verifier = WriteBackVerifier.write_back

    async def answers(title, payload, kwargs):
        nonlocal verifies
        if title == "WriteBackFinding":
            verifies += 1
            if position == "after_archive" and verifies == 1:
                raise failure
        elif position == "before_commit" and title == "CommitMessageOutput":
            raise failure

    executor = Executor(
        reproduced=True,
        subject={"kind": "criterion", "id": DIRECT_DONE},
        mutate=answers,
    )
    service, guard, workspace, _ = await build(repository, executor, port=port)
    reset = port.reset_criterion_pending

    async def reset_then_interrupt(**kwargs):
        await reset(**kwargs)
        raise failure

    async def verified_then_interrupt(self, **kwargs):
        actual = await verifier(self, **kwargs)
        if actual.artifact.surface.kind is SurfaceKind.CRITERION_SUB_ISSUE:
            assert actual.verdict.value == "holds"
            raise failure
        return actual

    if position == "after_reset":
        monkeypatch.setattr(port, "reset_criterion_pending", reset_then_interrupt)
    if position == "after_applied_holds":
        # Execute the real writer, artifact reread and independent judge first.
        # Interrupt at its actual completed HOLDS receipt, before parent return.
        monkeypatch.setattr(WriteBackVerifier, "write_back", verified_then_interrupt)
    try:
        with pytest.raises(RuntimeError) as caught:
            await drive(service, guard, repository)
        assert caught.value is failure
        assert not await git(
            repository[0], "ls-remote", "origin", "refs/heads/native-test"
        )
        assert (
            await git(repository[0], "log", "native-test", "--format=%s", "-1")
            == "newer writer starting point"
        )
        archives = [
            c for c in port.comments if c.body.startswith("[fixture-amendment:")
        ]
        assert len(archives) == 1
        original_archive = archives[0]
        record = AmendmentRecord.model_validate_json(
            original_archive.body.partition("\n")[2]
        )
        assert json.loads(record.prior.content)[0]["body"] == original.body
        already_amended = position in {"after_applied_holds", "before_commit"}
        if position == "after_archive":
            assert port.issues[DIRECT_DONE].body == original.body
            assert port.issues[DIRECT_DONE].state_kind is WorkflowStateKind.COMPLETED
        else:
            assert port.issues[DIRECT_DONE].state_kind is WorkflowStateKind.UNSTARTED
            assert criterion_field_bodies(
                port.issues[DIRECT_DONE].body, field="Evidence"
            ) == ("",)
        assert (
            "the amended observable Check" in port.issues[DIRECT_DONE].body
        ) is already_amended
    finally:
        monkeypatch.undo()
        await cleanup(workspace)

    fresh_executor = Executor(
        claim=not already_amended,
        reproduced=True,
        subject={"kind": "criterion", "id": DIRECT_DONE},
    )
    fresh_service, fresh_guard, fresh_workspace, _ = await build(
        repository, fresh_executor, port=port, frozen_spec=guard._spec
    )
    try:
        events = [
            e
            async for e in fresh_service.stream_workflow(
                prompt="Continue under the actual current native Checks.",
                repo_path=str(repository[0]),
                base_branch="native-test",
                branch_name="native-test",
                ralph_branch="native-test",
                create_branch=False,
                permission_mode=PermissionMode.UNATTENDED,
                allowed_tools=ToolPreset.IMPLEMENTATION,
                skills=SUPPRESS_ALL_SKILLS,
                session_type=SessionType.TICKET_FIRE,
                visibility=RepoVisibility.PUBLIC,
                native_guard=fresh_guard,
                after_publish=RecordingAfterPublish(),
            )
        ]
        assert any(isinstance(e, ResultEvent) and e.commit_sha for e in events)
        assert port.issues[DIRECT_DONE].state_kind is WorkflowStateKind.UNSTARTED
        assert "the amended observable Check" in port.issues[DIRECT_DONE].body
        assert (
            next(
                c
                for c in port.comments
                if c.comment_key == original_archive.comment_key
            )
            == original_archive
        )
        archived = [
            c for c in port.comments if c.body.startswith("[fixture-amendment:")
        ]
        # The partially reset source is a different observed prior artifact.
        # Both records remain: this is recovery, not an exactly-once transaction.
        assert len(archived) == (2 if position == "after_reset" else 1)
        report = next(e.report for e in events if isinstance(e, NativeAmendmentEvent))
        assert bool(report.verdicts) is (not already_amended)
    finally:
        await cleanup(fresh_workspace)
