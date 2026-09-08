"""Native scope assembly carries real Git counterexamples into mandate reports."""

import json
import subprocess
from pathlib import Path

import pytest

from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.adapters.subprocess_git_source_reader import SubprocessGitSourceReader
from kodezart.domain.criterion_evidence import render_evidence_field
from kodezart.types.domain.agent import AUDIT_OVERCLAIM_SCHEMA
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_overclaim import OverclaimKind
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from tests.tracker.test_audit_evidence_git import command
from tests.tracker.test_audit_evidence_git import repository as repository
from tests.tracker.test_audit_overclaim_sweep import completed, payload
from tests.tracker.test_audit_sweep import CHILD
from tests.tracker.test_audit_sweep import server as server
from tests.tracker.test_audit_sweep import setup as setup


@pytest.mark.parametrize("kind", list(OverclaimKind))
@pytest.mark.parametrize("defective", [True, False])
async def test_actual_native_sweep_reports_each_counterexample_and_clean_control(
    setup, tracker, server, tracker_writes, repository, tmp_path, kind, defective
):
    build, executor, _, _, _, _, _, operation = setup
    remote, author, *_ = repository
    (author / "members.json").write_text(json.dumps(["one", "two", "three"]))
    (author / "claim.txt").write_text("3")
    (author / "roster.json").write_text(json.dumps(["one", "two", "three"]))
    (author / "source.md").write_text("A café\nEvery source topic is covered.\n")
    (author / "adopted.md").write_bytes((author / "source.md").read_bytes())
    (author / "charter.txt").write_text(
        "Member counts are computed, never judged.\n"
        "Task suitability requires evaluating the specification.\n"
    )
    (author / "dispatch.py").write_text(
        "def count(members):\n    return len(members)\n\n"
        "def choose(task, evaluate):\n    return evaluate(task)\n"
    )
    command(author, "add", "--all")
    command(author, "commit", "-qm", "enumerable graded sources")
    graded = command(author, "rev-parse", "HEAD")
    if defective:
        if kind is OverclaimKind.AGGREGATE:
            (author / "claim.txt").write_text("4")
        elif kind is OverclaimKind.COMPLETENESS:
            (author / "roster.json").unlink()
        elif kind is OverclaimKind.ADOPTION:
            (author / "adopted.md").write_text(
                "A cafe\u0301\nEvery source topic is covered.\n"
            )
        else:
            (author / "charter.txt").write_text(
                "Every task decision is computed, never judged.\n"
                "Task suitability requires evaluating the specification.\n"
            )
            (author / "dispatch.py").write_text(
                "def choose(task, evaluate):\n    return len(task.split()) > 20\n"
            )
    (author / "revision.txt").write_text("current revision")
    command(author, "add", "--all")
    command(author, "commit", "-qm", "current audited source")
    head = command(author, "rev-parse", "HEAD")
    command(author, "push", "-q", "configured-remote", "ordinary-name")
    check = (
        "The current claims in claim.txt, roster.json, adopted.md and charter.txt hold."
    )
    await tracker.update_issue(
        issue_key=CHILD,
        body=f"**Check:** {check}\n**Do:** AUTHOR_REASONING\n"
        + render_evidence_field(
            CriterionEvidence(graded_sha=graded, test="OLD_RECORDED_VERDICT")
        ),
    )
    await completed(tracker, server)
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
    paths = []

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
        if kwargs["output_format"]["schema"] != AUDIT_OVERCLAIM_SCHEMA:
            return
        assert check in kwargs["prompt"] and graded in kwargs["prompt"]
        assert "OLD_RECORDED_VERDICT" not in kwargs["prompt"]
        assert "AUTHOR_REASONING" not in kwargs["prompt"]
        output = payload()
        row = next(item for item in output["checks"] if item["kind"] == kind.value)
        if kind is OverclaimKind.AGGREGATE:
            count = len(json.loads((path / "members.json").read_text()))
            claimed = int((path / "claim.txt").read_text())
            row["evidence"] = f"Read {count} members; claim.txt states {claimed}."
            if count != claimed:
                row.update(verdict="refuted", recomputedValue=str(count))
        elif kind is OverclaimKind.COMPLETENESS:
            if not (path / "roster.json").exists():
                row.update(
                    verdict="unverifiable",
                    missingArtifact="independently enumerable roster.json",
                    evidence="The current repository has no claimed roster witness.",
                )
            else:
                assert len(json.loads((path / "roster.json").read_text())) == 3
        elif kind is OverclaimKind.ADOPTION:
            row["evidence"] = "Every source topic is covered."
            # The external judgment deliberately claims holds. Native comparison
            # must override it when normalized-looking files have different bytes.
            output["bytePairs"] = [
                {
                    "sourceSha": graded,
                    "sourcePath": "source.md",
                    "artifactPath": "adopted.md",
                }
            ]
        else:
            charter = (path / "charter.txt").read_text()
            code = (path / "dispatch.py").read_text()
            assert "Task suitability requires evaluating the specification." in charter
            unscoped = "Every task decision is computed, never judged." in charter
            if unscoped and "len(task.split())" in code:
                row.update(
                    verdict="refuted",
                    evidence=(
                        "dispatch.py substitutes a word count for required evaluation."
                    ),
                )
            else:
                assert "Member counts are computed, never judged." in charter
                assert "return len(members)" in code and "return evaluate(task)" in code
        executor.overclaim_output = output

    executor.during = during
    sweep = build(
        include_overclaims=True,
        selected_op=selected_op,
        selected_git=native,
        selected_cache=cache,
        selected_workspace=workspace,
        selected_source=SubprocessGitSourceReader(),
    )
    before = tracker_writes()
    child = (await sweep.run()).observations[0]
    assert child.unavailable_reason is child.overclaim_unavailable_reason is None
    # The ordinary completed claim has lapsed, but this independent detector
    # still observes current repository facts and completes its own refutation.
    assert child.evidence.is_lapse and child.claim is None
    report = child.overclaims
    assert report.observation.head_sha == head
    assert report.observation.graded_sha == graded
    assert report.observation.record_ref == child.target.source.comment.comment_key
    selected = next(row for row in report.reports if row.kind is kind).report
    expected = (
        AuditVerdict.UNVERIFIABLE
        if defective and kind is OverclaimKind.COMPLETENESS
        else AuditVerdict.REFUTED
        if defective
        else AuditVerdict.HOLDS
    )
    assert selected.claim.judgment.verdict is expected
    assert (selected.mandate is not None) is (expected is AuditVerdict.REFUTED)
    observed = next(
        row for row in report.observation.judgment.checks if row.kind is kind
    )
    if defective and kind is OverclaimKind.AGGREGATE:
        assert observed.recomputed_value == "3"
    if defective and kind is OverclaimKind.COMPLETENESS:
        assert observed.missing_artifact == "independently enumerable roster.json"
    if kind is OverclaimKind.ADOPTION:
        assert report.observation.judgment.byte_pairs[0].source_sha == graded
        if defective:
            assert "differs byte-wise" in selected.claim.judgment.evidence
            assert "differs byte-wise" in executor.calls[-1]["prompt"]
    assert len(paths) == (2 if expected is AuditVerdict.REFUTED else 1)
    assert not workspace._workspaces and all(not path.exists() for path in paths)
    assert tracker_writes() == before
