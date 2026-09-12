"""Terminal mandate completion uses actual native branch bytes and owns cleanup."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.adapters.subprocess_git_source_reader import SubprocessGitSourceReader
from kodezart.domain.criterion_evidence import render_evidence_field
from kodezart.domain.lane_record import render_lane_record
from kodezart.types.domain.agent import AUDIT_MANDATE_SCHEMA
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_terminal import TerminalDiscrepancy
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.pr_state import PRLifecycle, PRState
from tests.tracker.lease_fixtures import leased_comment
from tests.tracker.test_audit_evidence_git import command
from tests.tracker.test_audit_evidence_git import repository as repository
from tests.tracker.test_audit_requests import PREFIXES
from tests.tracker.test_audit_sweep import CHECK, CHILD, REPO, ROOT
from tests.tracker.test_audit_sweep import server as server
from tests.tracker.test_audit_sweep import setup as setup
from tests.tracker.test_audit_terminal_mandate import terminal_ready


@pytest.mark.parametrize(
    "case", ["closed", "open", "unresolved", "cancel", "replacement"]
)
async def test_native_terminal_mandate_observes_current_head_without_criterion_cast(
    setup, tracker, server, tracker_writes, repository, tmp_path, case
):
    build, executor, _, _, _, forge, _, operation = setup
    remote, author, _, prior, head = repository
    await terminal_ready(tracker, server, forge)
    await tracker.update_issue(
        issue_key=CHILD,
        body=f"**Check:** {CHECK}\n"
        + render_evidence_field(
            CriterionEvidence(graded_sha=head, test="old assertion")
        ),
    )
    native = SubprocessGitService(remote="configured-remote")
    cache = LocalBareRepoCache(git=native, base_dir=str(tmp_path / "terminal-cache"))
    workspace = GitWorktreeProvider(
        git=native,
        cache=cache,
    )
    selected = operation.model_copy(
        update={
            "repos": [operation.repos[0].model_copy(update={"url": remote.as_uri()})]
        }
    )
    forge.records[(remote.as_uri(), 7)] = PRState.model_validate(
        {
            **forge.records[(REPO, 7)].model_dump(),
            "head_sha": head,
            "head_repo_url": remote.as_uri(),
            "base_repo_url": remote.as_uri(),
            "lifecycle": PRLifecycle.OPEN if case == "open" else PRLifecycle.CLOSED,
        }
    )
    sweep = build(
        selected_op=selected,
        selected_git=native,
        selected_cache=cache,
        selected_workspace=workspace,
        selected_source=SubprocessGitSourceReader(),
    )
    source = (await sweep._requests.read(scope=sweep._scope)).targets[1].source
    if case == "unresolved":
        rendered = render_lane_record(
            record=source.record.model_copy(update={"pr": None}),
            marker_prefixes=PREFIXES,
        )
        marker, _, body = rendered.partition("\n")
        updated = await leased_comment(tracker, target=ROOT, marker=marker, body=body)
        assert updated.comment_key == source.comment.comment_key
    paths, mandate_calls = [], []
    active = asyncio.Event()

    async def during(kwargs):
        path = Path(kwargs["cwd"])
        paths.append(path)
        assert command(path, "rev-parse", "HEAD") == head
        assert (
            subprocess.run(
                ["git", "symbolic-ref", "-q", "HEAD"], cwd=path, capture_output=True
            ).returncode
            == 1
        )
        assert (path / "check.txt").read_bytes() == (author / "check.txt").read_bytes()
        assert not await native.has_changes(str(path))
        if kwargs["output_format"]["schema"] != AUDIT_MANDATE_SCHEMA:
            return
        mandate_calls.append(kwargs)
        assert (
            ROOT in kwargs["prompt"] and source.comment.comment_key in kwargs["prompt"]
        )
        assert head in kwargs["prompt"] and "head-full-identity" not in kwargs["prompt"]
        if case == "cancel":
            active.set()
            await asyncio.Future()
        elif case == "replacement":
            command(path, "replace", head, prior)
            command(path, "reset", "--hard", head)
            assert command(path, "rev-parse", "HEAD") == head
            assert not await native.has_changes(str(path))
            assert (path / "check.txt").read_text() == "prior committed contents\n"

    executor.during = during
    before = tracker_writes()
    if case == "cancel":
        task = asyncio.create_task(sweep.run())
        try:
            await asyncio.wait_for(active.wait(), 10)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    else:
        result = await sweep.run()
        child, parent = result.observations
        assert child.claim is not None
        assert parent.terminal.branch_head == head
        assert (
            parent.terminal.issue_key == ROOT
            and parent.terminal.record_ref == source.comment.comment_key
        )
        if case == "replacement":
            assert parent.terminal_report is None
            assert "substitutes" in parent.unavailable_reason
        else:
            assert parent.unavailable_reason is None
            assert parent.terminal_report.observation == parent.terminal
            if case == "open":
                assert parent.terminal.verdict is AuditVerdict.HOLDS
                assert parent.terminal_report.mandate is None
            else:
                assert parent.terminal.verdict is AuditVerdict.REFUTED
                assert parent.terminal_report.mandate.verdict is AuditVerdict.REFUTED
                assert parent.terminal.discrepancies == (
                    (TerminalDiscrepancy.UNRESOLVED_ASSOCIATION,)
                    if case == "unresolved"
                    else (TerminalDiscrepancy.CLOSED_UNMERGED_PR,)
                )
    assert len(mandate_calls) == (0 if case == "open" else 1)
    assert not workspace._workspaces and all(not path.exists() for path in paths)
    assert tracker_writes() == before
