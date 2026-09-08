"""Over-claim fixtures read actual immutable files through a fresh workspace."""

import json
from pathlib import Path

import pytest

from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.adapters.subprocess_git_source_reader import SubprocessGitSourceReader
from kodezart.domain.criterion_evidence import render_evidence_field
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_overclaim import OverclaimKind
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from tests.tracker import test_audit_overclaim as fixtures
from tests.tracker.test_audit_evidence_git import command

setup = fixtures.setup
claim_setup = fixtures.claim_setup
server = fixtures.server


@pytest.mark.parametrize("kind", list(OverclaimKind))
@pytest.mark.parametrize("defective", [True, False])
async def test_actual_revisions_exercise_each_overclaim_and_its_clean_pair(
    setup, tracker, tracker_writes, tmp_path, monkeypatch, kind, defective
):
    remote = tmp_path / "remote"
    author = tmp_path / "author"
    remote.mkdir()
    author.mkdir()
    command(remote, "init", "--bare", "-q")
    command(author, "init", "-q", "-b", "ordinary-name")
    command(author, "config", "user.name", "Fixture")
    command(author, "config", "user.email", "fixture@example.invalid")
    (author / "members.json").write_text(json.dumps(["one", "two", "three"]))
    (author / "claim.txt").write_text("3")
    (author / "roster.json").write_text(json.dumps(["completed-task"]))
    (author / "source.md").write_text("A café\nAll required topics are covered.\n")
    (author / "adopted.md").write_bytes((author / "source.md").read_bytes())
    (author / "charter.txt").write_text(
        "Every task decision is computed, never judged.\nTask suitability requires evaluating the specification, not its word count.\n"
    )
    (author / "dispatch.py").write_text(
        "def choose(task, evaluate):\n    return evaluate(task)\n"
    )
    command(author, "add", "--all")
    command(author, "commit", "-qm", "graded source")
    graded = command(author, "rev-parse", "HEAD")
    if defective:
        if kind is OverclaimKind.AGGREGATE:
            (author / "claim.txt").write_text("4")
        elif kind is OverclaimKind.COMPLETENESS:
            (author / "roster.json").unlink()
        elif kind is OverclaimKind.ADOPTION:
            # Same rendered meaning and complete topic coverage, different bytes.
            (author / "adopted.md").write_text(
                "A cafe\u0301\nAll required topics are covered.\n"
            )
        else:
            (author / "dispatch.py").write_text(
                "def choose(task, evaluate):\n    return len(task.split()) > 20\n"
            )
    (author / "revision.txt").write_text("current revision")
    command(author, "add", "--all")
    command(author, "commit", "-qm", "current source")
    head = command(author, "rev-parse", "HEAD")
    command(author, "remote", "add", "configured-remote", str(remote))
    command(author, "push", "-q", "configured-remote", "ordinary-name")
    check = {
        OverclaimKind.AGGREGATE: "The member count in claim.txt matches members.json.",
        OverclaimKind.COMPLETENESS: "All tasks were handled, witnessed by the independently enumerable roster.json.",
        OverclaimKind.ADOPTION: "adopted.md adopts source.md verbatim, with all topics covered.",
        OverclaimKind.SELF_RULE: "The dispatch.py artifact complies with charter.txt, including its own unscoped computed, never judged claim.",
    }[kind]
    await tracker.update_issue(
        issue_key=fixtures.fixtures.CHILD,
        body=f"**Check:** {check}\n**Do:** AUTHOR_REASONING\n"
        + render_evidence_field(
            CriterionEvidence(graded_sha=graded, test="OLD_VERDICT_IS_NOT_A_WITNESS")
        ),
    )
    native = SubprocessGitService(remote="configured-remote")
    _, runner, git, _, _, workspace, *_ = setup
    for method in (
        "fetch",
        "remote_branch_sha",
        "is_ancestor",
        "current_sha",
        "has_changes",
    ):
        monkeypatch.setattr(git, method, getattr(native, method))
    verification = tmp_path / "verification"
    acquired, released = [], []

    async def acquire(**kwargs):
        assert kwargs["ref"] == head and kwargs["create_branch"] is False
        acquired.append(head)
        await native.create_worktree(
            kwargs["repo_path"], head, str(verification), create_branch=False
        )
        return str(verification)

    async def release(path):
        await native.remove_worktree(str(author), path)
        released.append(path)

    monkeypatch.setattr(workspace, "acquire", acquire)
    # remove_worktree needs the actual cache repository, retained at acquisition.
    repositories = []
    original_acquire = acquire

    async def owned_acquire(**kwargs):
        repositories.append(kwargs["repo_path"])
        return await original_acquire(**kwargs)

    async def owned_release(path):
        await native.remove_worktree(repositories[-1], path)
        released.append(path)

    monkeypatch.setattr(workspace, "acquire", owned_acquire)
    monkeypatch.setattr(workspace, "release", owned_release)
    observations = []

    async def during():
        path = Path(runner.arguments["workspace_path"])
        assert command(path, "rev-parse", "HEAD") == head
        assert check in runner.arguments["prompt"]
        assert graded in runner.arguments["prompt"]
        assert "AUTHOR_REASONING" not in runner.arguments["prompt"]
        assert "OLD_VERDICT_IS_NOT_A_WITNESS" not in runner.arguments["prompt"]
        output = fixtures.payload()
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
                    evidence="Current Git checkout has no named roster witness.",
                )
            else:
                assert json.loads((path / "roster.json").read_text())
        elif kind is OverclaimKind.ADOPTION:
            # Deliberately optimistic external judgment: the harness must find
            # differing actual Git bytes despite the complete-coverage claim.
            row["evidence"] = "Every source topic is covered."
            output["bytePairs"] = [
                dict(
                    sourceSha=graded, sourcePath="source.md", artifactPath="adopted.md"
                )
            ]
        else:
            assert "computed, never judged" in (path / "charter.txt").read_text()
            code = (path / "dispatch.py").read_text()
            if "len(task.split())" in code:
                row.update(
                    verdict="refuted",
                    evidence="dispatch.py substitutes word count for the specification evaluation its own charter requires.",
                )
        observations.append(output)
        fixtures.answer(runner, output)

    runner.during = during
    source = SubprocessGitSourceReader()
    audit = fixtures.build(setup, tracker, source=source)
    audit._sources._source = source
    audit._sources._cache = LocalBareRepoCache(
        git=native, base_dir=str(tmp_path / "cache")
    )
    request = fixtures.fixtures.REQUEST.model_copy(update={"repo_url": remote.as_uri()})
    before = tracker_writes()
    result = await audit.observe(request)
    expected = (
        AuditVerdict.UNVERIFIABLE
        if defective and kind is OverclaimKind.COMPLETENESS
        else AuditVerdict.REFUTED
        if defective
        else AuditVerdict.HOLDS
    )
    assert result.judgment.verdict is expected
    row = next(item for item in result.judgment.checks if item.kind is kind)
    if defective and kind is OverclaimKind.AGGREGATE:
        assert row.recomputed_value == "3"
    if defective and kind is OverclaimKind.ADOPTION:
        assert "differs byte-wise" in row.evidence
        assert (
            next(
                item for item in observations[0]["checks"] if item["kind"] == "adoption"
            )["verdict"]
            == "holds"
        )
    assert result.graded_sha == graded and result.head_sha == head
    assert acquired == [head] and released == [str(verification)]
    assert not verification.exists() and tracker_writes() == before
