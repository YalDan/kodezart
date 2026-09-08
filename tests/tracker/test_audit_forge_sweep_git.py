"""Historical forge evidence keeps its actual SHA through a native mandate hunt."""

from pathlib import Path

import pytest

from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.adapters.subprocess_git_source_reader import SubprocessGitSourceReader
from kodezart.domain.criterion_evidence import render_evidence_field
from kodezart.types.domain.agent import AUDIT_MANDATE_SCHEMA
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from tests.fakes import FakeCIMonitor, FakeCIObservationReader
from tests.tracker.test_audit_evidence_git import command
from tests.tracker.test_audit_evidence_git import repository as repository
from tests.tracker.test_audit_forge import NAMES, REPOSITORY
from tests.tracker.test_audit_forge_sweep import verifier
from tests.tracker.test_audit_overclaim_sweep import completed
from tests.tracker.test_audit_sweep import CHECK, CHILD
from tests.tracker.test_audit_sweep import server as server
from tests.tracker.test_audit_sweep import setup as setup


@pytest.mark.parametrize("red", [False, True])
async def test_historical_forge_sha_is_not_restamped_to_current_head(
    setup, tracker, server, tracker_writes, repository, tmp_path, red
):
    build, executor, _, _, _, _, stored, operation = setup
    remote, _, _, graded, head = repository
    assert graded != head
    await tracker.update_issue(
        issue_key=CHILD,
        body=f"**Check:** {CHECK}\n**Do:** AUTHOR_REASONING\n"
        + render_evidence_field(CriterionEvidence(graded_sha=graded, test="old_test")),
    )
    await completed(tracker, server)
    native = SubprocessGitService(remote="configured-remote")
    cache = LocalBareRepoCache(git=native, base_dir=str(tmp_path / "native-cache"))
    workspace = GitWorktreeProvider(
        git=native,
        cache=cache,
    )
    op = operation.model_copy(
        update={"repos": [REPOSITORY.model_copy(update={"url": remote.as_uri()})]}
    )
    reader = FakeCIObservationReader()
    ci = FakeCIMonitor(
        passed=not red,
        failed_names=NAMES if red else frozenset(),
        check_names=NAMES,
        observed_sha_by_ref={graded: graded},
        observation_reader=reader,
        rerun_results=[(False, "reproduced graded failure", NAMES)],
    )
    paths = []

    async def during(kwargs):
        assert kwargs["output_format"]["schema"] == AUDIT_MANDATE_SCHEMA
        path = Path(kwargs["cwd"])
        paths.append(path)
        assert command(path, "rev-parse", "HEAD") == graded
        assert (path / "check.txt").read_text() == "prior committed contents\n"
        assert graded in kwargs["prompt"] and head not in kwargs["prompt"]
        assert "same-SHA checks reproduce red" in kwargs["prompt"]

    executor.during = during
    before = tracker_writes()
    child = (
        await build(
            selected_op=op,
            selected_git=native,
            selected_cache=cache,
            selected_workspace=workspace,
            selected_source=SubprocessGitSourceReader(),
            selected_forge=verifier(tracker, op, ci, reader),
        ).run()
    ).observations[0]
    assert child.unavailable_reason is child.forge_unavailable_reason is None
    assert child.claim is None and child.evidence.is_lapse
    assert child.evidence.head_sha == head
    assert child.forge.recorded_evidence.graded_sha == graded
    assert child.forge.checks is not None, child.forge.reason
    assert child.forge.checks.commit_sha == graded
    assert child.forge_report.claim.head_sha == graded
    assert child.forge_report.claim.record_ref == stored.comment_key
    assert child.forge_report.claim.check == CHECK
    expected = AuditVerdict.REFUTED if red else AuditVerdict.HOLDS
    assert child.forge.verdict is expected
    assert child.forge_report.claim.judgment.verdict is expected
    assert (child.forge_report.mandate is not None) is red
    assert len(paths) == int(red)
    assert not workspace._workspaces and all(not path.exists() for path in paths)
    assert {row["ref"] for row in ci.calls} == {graded}
    assert ci.rerun_calls == ([(remote.as_uri(), graded)] if red else [])
    assert tracker_writes() == before
