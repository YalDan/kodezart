"""Independent combined-arm controls over actual Git and native tracker inputs."""

import asyncio
from pathlib import Path

import pytest

from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.adapters.subprocess_git_source_reader import SubprocessGitSourceReader
from kodezart.domain.criterion_evidence import render_evidence_field
from kodezart.domain.errors import AuditClaimReadError
from kodezart.types.domain.agent import (
    AUDIT_MANDATE_SCHEMA,
    AUDIT_OVERCLAIM_SCHEMA,
    DETECTOR_REMOVAL_SCHEMA,
)
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from tests.fakes import FakeCIMonitor
from tests.tracker.test_audit_evidence_git import command
from tests.tracker.test_audit_evidence_git import repository as repository
from tests.tracker.test_audit_forge import NAMES, REPOSITORY
from tests.tracker.test_audit_forge_sweep import verifier
from tests.tracker.test_audit_overclaim_sweep import completed, payload
from tests.tracker.test_audit_sweep import CHECK, CHILD
from tests.tracker.test_audit_sweep import server as server
from tests.tracker.test_audit_sweep import setup as setup
from tests.tracker.test_detector_removal_sweep import payload as removal_payload


@pytest.mark.parametrize("mode", ["complete", "mandate-error", "head-moves", "cancel"])
async def test_combined_native_arms_keep_their_own_revisions_and_lifetimes(
    setup, tracker, server, tracker_writes, repository, tmp_path, monkeypatch, mode
):
    build, executor, _, _, _, _, stored, operation = setup
    remote, author, _, graded, head = repository
    assert graded != head
    await tracker.update_issue(
        issue_key=CHILD,
        body=f"**Check:** {CHECK}\n"
        + render_evidence_field(
            CriterionEvidence(graded_sha=graded, test="graded_test")
        ),
    )
    await completed(tracker, server)
    native = SubprocessGitService(remote="configured-remote")
    cache = LocalBareRepoCache(git=native, base_dir=str(tmp_path / "combined-cache"))
    workspace = GitWorktreeProvider(
        git=native,
        cache=cache,
    )
    op = operation.model_copy(
        update={"repos": [REPOSITORY.model_copy(update={"url": remote.as_uri()})]}
    )
    ci = FakeCIMonitor(
        passed=False,
        failed_names=NAMES,
        check_names=NAMES,
        observed_sha_by_ref={graded: graded},
        rerun_results=[(False, "same historical defect", NAMES)],
    )
    executor.overclaim_output = payload()
    executor.removal_output = removal_payload("holds")
    visited = []
    active = asyncio.Event()

    async def during(kwargs):
        schema = kwargs["output_format"]["schema"]
        path = Path(kwargs["cwd"])
        visited.append((schema, path))
        expected = graded if schema == AUDIT_MANDATE_SCHEMA else head
        assert command(path, "rev-parse", "HEAD") == expected
        assert kwargs["session_id"] is None
        if schema == AUDIT_MANDATE_SCHEMA and mode == "mandate-error":
            raise RuntimeError("historical mandate unavailable")
        if schema == DETECTOR_REMOVAL_SCHEMA and mode == "cancel":
            active.set()
            await asyncio.Future()

    executor.during = during
    if mode == "head-moves":
        original = ci.wait_for_checks
        moved = False

        async def after_current_arms(**kwargs):
            nonlocal moved
            result = await original(**kwargs)
            if not moved:
                moved = True
                (author / "later.txt").write_text("A later current revision.\n")
                command(author, "add", "later.txt")
                command(author, "commit", "-qm", "head after current detectors")
                command(author, "push", "-q", "configured-remote", "ordinary-name")
            return result

        monkeypatch.setattr(ci, "wait_for_checks", after_current_arms)
    sweep = build(
        selected_op=op,
        selected_git=native,
        selected_cache=cache,
        selected_workspace=workspace,
        selected_source=SubprocessGitSourceReader(),
        include_overclaims=True,
        include_removals=True,
        selected_forge=verifier(tracker, op, ci),
    )
    before = tracker_writes()
    if mode == "cancel":
        task = asyncio.create_task(sweep.run())
        try:
            await asyncio.wait_for(active.wait(), 15)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert not ci.calls
    elif mode == "head-moves":
        with pytest.raises(AuditClaimReadError, match="observed branch changed"):
            await sweep.run()
        assert {call["ref"] for call in ci.calls} == {graded}
    else:
        child = (await sweep.run()).observations[0]
        assert child.claim is None and child.evidence.is_lapse
        assert child.evidence.head_sha == head
        assert (
            child.overclaim_unavailable_reason
            is child.removal_unavailable_reason
            is None
        )
        assert child.overclaims.observation.head_sha == head
        assert child.detector_removal.observation.head_sha == head
        assert len(child.overclaims.reports) == 4
        assert len(child.detector_removal.reports) == 1
        assert child.forge.verdict is AuditVerdict.REFUTED
        assert child.forge.recorded_evidence.graded_sha == graded
        if mode == "complete":
            assert child.forge_unavailable_reason is None
            assert child.forge_report.claim.head_sha == graded
            assert child.forge_report.claim.record_ref == stored.comment_key
            assert child.forge_report.mandate is not None
        else:
            assert child.forge_report is None
            assert "historical mandate unavailable" in child.forge_unavailable_reason
    assert [schema for schema, _ in visited] == [
        AUDIT_OVERCLAIM_SCHEMA,
        DETECTOR_REMOVAL_SCHEMA,
    ] + ([] if mode == "cancel" else [AUDIT_MANDATE_SCHEMA])
    assert not workspace._workspaces
    assert all(not path.exists() for _, path in visited)
    assert tracker_writes() == before
