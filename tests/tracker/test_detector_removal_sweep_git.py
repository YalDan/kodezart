"""The native scope sweep tests the real removal/detector counterfactual pair."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.adapters.subprocess_git_source_reader import SubprocessGitSourceReader
from kodezart.domain.criterion_evidence import render_evidence_field
from kodezart.types.domain.agent import AUDIT_MANDATE_SCHEMA, DETECTOR_REMOVAL_SCHEMA
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.tracker.test_audit_detection_removal import (
    GUARD,
    MECHANISM,
    OTHER,
    SMOKE,
    execute,
    suite,
)
from tests.tracker.test_audit_evidence_git import command
from tests.tracker.test_audit_evidence_git import repository as repository
from tests.tracker.test_audit_sweep import CHILD, state
from tests.tracker.test_audit_sweep import server as server
from tests.tracker.test_audit_sweep import setup as setup


@pytest.mark.parametrize(
    "case", ["removed", "retained", "replacement", "unchanged", "cancel"]
)
async def test_actual_scope_reports_lost_detection_but_not_a_retained_guard(
    setup, tracker, server, tracker_writes, repository, tmp_path, case
):
    build, executor, _, _, _, _, _, operation = setup
    remote, author, *_ = repository
    (author / "mechanism.py").write_text(MECHANISM + OTHER)
    (author / "test_guard.py").write_text(GUARD)
    (author / "test_unrelated.py").write_text(SMOKE)
    assert suite(author).returncode == 0
    command(author, "add", "--all")
    command(author, "commit", "-qm", "protected mechanism and effective detector")
    graded = command(author, "rev-parse", "HEAD")
    if case != "unchanged":
        (author / "mechanism.py").write_text(OTHER)
    if case in {"removed", "replacement", "cancel"}:
        (author / "test_guard.py").unlink()
    if case == "replacement":
        (author / "test_replacement.py").write_text(
            GUARD.replace("test_guard", "test_replacement")
        )
    command(author, "add", "--all")
    command(
        author, "commit", "--allow-empty", "-qm", "current mechanism and detector state"
    )
    head = command(author, "rev-parse", "HEAD")
    command(author, "push", "-q", "configured-remote", "ordinary-name")
    check = "The protected mechanism remains available."
    await tracker.update_issue(
        issue_key=CHILD,
        body=f"**Check:** {check}\n**Do:** AUTHOR_REASONING\n"
        + render_evidence_field(
            CriterionEvidence(graded_sha=graded, test="OLD_RECORDED_VERDICT")
        ),
    )
    await state(tracker, server, CHILD, "Done", WorkflowStateKind.COMPLETED)
    native = SubprocessGitService(remote="configured-remote")
    cache = LocalBareRepoCache(git=native, base_dir=str(tmp_path / "sweep-cache"))
    workspace = GitWorktreeProvider(
        git=native,
        cache=cache,
        committer_name="Fixture",
        committer_email="fixture@example.invalid",
    )
    selected_op = operation.model_copy(
        update={
            "repos": [operation.repos[0].model_copy(update={"url": remote.as_uri()})]
        }
    )
    paths, observations = [], []
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
        if kwargs["output_format"]["schema"] == AUDIT_MANDATE_SCHEMA:
            assert (
                "The current suite is green; the old guard fails importing"
                in kwargs["prompt"]
            )
            assert (
                "mechanism.py" in kwargs["prompt"]
                and "test_guard.py" in kwargs["prompt"]
            )
            return
        assert kwargs["output_format"]["schema"] == DETECTOR_REMOVAL_SCHEMA
        assert graded in kwargs["prompt"] and head in kwargs["prompt"]
        assert "AUTHOR_REASONING" not in kwargs["prompt"]
        assert "OLD_RECORDED_VERDICT" not in kwargs["prompt"]
        before = command(path, "show", f"{graded}:mechanism.py") + "\n"
        guard = command(path, "show", f"{graded}:test_guard.py") + "\n"
        assert MECHANISM in before and guard == GUARD
        current = (path / "mechanism.py").read_text()
        observed = suite(path)
        counterfactual = execute(path, "-c", guard + "\ntest_guard()\n")
        observations.append((observed.returncode, counterfactual.returncode))
        lost = (
            MECHANISM not in current
            and observed.returncode == 0
            and counterfactual.returncode != 0
        )
        executor.removal_output = {
            "criterionKey": CHILD,
            "verdict": "refuted" if lost else "holds",
            "evidence": observed.stdout + observed.stderr + counterfactual.stderr,
            "findings": [
                {
                    "mechanism": {"path": "mechanism.py", "line": 1, "text": MECHANISM},
                    "detector": {"path": "test_guard.py", "line": 1, "text": GUARD},
                    "absenceDemonstration": (
                        "The current suite is green; the old guard fails importing "
                        "the removed function at this same current head."
                    ),
                }
            ]
            if lost
            else [],
        }
        if case == "cancel":
            active.set()
            await asyncio.Future()

    executor.during = during
    sweep = build(
        include_removals=True,
        selected_op=selected_op,
        selected_git=native,
        selected_cache=cache,
        selected_workspace=workspace,
        selected_source=SubprocessGitSourceReader(),
    )
    before = tracker_writes()
    if case == "cancel":
        task = asyncio.create_task(sweep.run())
        try:
            await asyncio.wait_for(active.wait(), 10)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 10)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    else:
        child = (await sweep.run()).observations[0]
        assert child.unavailable_reason is child.removal_unavailable_reason is None
        assert child.evidence.is_lapse and child.claim is None
        report = child.detector_removal
        assert (
            report.observation.head_sha == head
            and report.observation.graded_sha == graded
        )
        assert report.observation.record_ref == child.target.source.comment.comment_key
        assert report.observation.judgment.criterion_key == CHILD
        (entry,) = report.reports
        assert entry.report.claim.judgment.verdict is (
            AuditVerdict.REFUTED if case == "removed" else AuditVerdict.HOLDS
        )
        if case == "removed":
            assert entry.finding.mechanism.text == MECHANISM
            assert entry.finding.detector.text == GUARD
            assert (
                entry.finding.absence_demonstration
                in entry.report.claim.judgment.evidence
            )
            assert entry.report.mandate.verdict is AuditVerdict.REFUTED
        else:
            assert entry.finding is entry.report.mandate is None
        assert len(paths) == (2 if case == "removed" else 1)
        assert observations == [
            (
                0 if case in {"removed", "unchanged"} else 2,
                0 if case == "unchanged" else 1,
            )
        ]
    assert not workspace._workspaces and all(not path.exists() for path in paths)
    assert tracker_writes() == before
