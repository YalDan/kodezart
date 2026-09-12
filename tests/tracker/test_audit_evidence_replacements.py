"""Native Evidence ancestry cannot be manufactured by replacement objects."""

import subprocess
from unittest.mock import AsyncMock

import pytest

from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.adapters.subprocess_git_source_reader import SubprocessGitSourceReader
from kodezart.domain.errors import AuditEvidenceReadError
from tests.git_read_cancellation import assert_git_read_settles_before_release
from tests.tracker.test_audit_evidence import CHILD, REQUEST, body
from tests.tracker.test_audit_evidence import claim_setup as claim_setup
from tests.tracker.test_audit_evidence import server as server
from tests.tracker.test_audit_evidence import setup as setup
from tests.tracker.test_audit_evidence_git import command
from tests.tracker.test_audit_evidence_git import repository as repository


@pytest.mark.parametrize("phase", ["off-branch", "lapse", "before", "during"])
@pytest.mark.parametrize("namespace", ["default", "configured"])
async def test_actual_evidence_requires_original_head_history(
    setup, tracker, tracker_writes, repository, tmp_path, monkeypatch, phase, namespace
):
    build, runner, _, _, _, workspace, *_ = setup
    remote, author, _, prior, head = repository
    if namespace == "configured":
        monkeypatch.setenv("GIT_REPLACE_REF_BASE", "refs/evidence-replacements/")
    command(author, "checkout", "--orphan", "foreign")
    (author / "check.txt").write_text("foreign grading\n")
    command(author, "add", "--all")
    command(author, "commit", "-qm", "foreign grading")
    graded = command(author, "rev-parse", "HEAD")
    (author / "check.txt").write_text("foreign descendant\n")
    command(author, "commit", "-qam", "substitute ancestry")
    alternate = command(author, "rev-parse", "HEAD")
    command(author, "push", "-q", "configured-remote", "foreign")
    native = SubprocessGitService(remote="configured-remote")
    cache = LocalBareRepoCache(git=native, base_dir=str(tmp_path / "native-cache"))
    repository_path = await cache.ensure_available(remote.as_uri(), "lane-cache")
    assert not await native.is_ancestor(repository_path, graded, head)
    source = SubprocessGitSourceReader()
    original_resolve = source.resolve_commit
    inserted = []

    async def substitute():
        command(repository_path, "replace", head, alternate)
        command(repository_path, "pack-refs", "--all")
        assert await native.is_ancestor(repository_path, graded, head)
        assert (
            subprocess.run(
                [
                    "git",
                    "--no-replace-objects",
                    "merge-base",
                    "--is-ancestor",
                    graded,
                    head,
                ],
                cwd=repository_path,
            ).returncode
            == 1
        )
        inserted.append(head)

    async def resolve(*, cwd, ref):
        value = await original_resolve(cwd=cwd, ref=ref)
        if phase == "during" and ref == graded:
            await substitute()
        return value

    monkeypatch.setattr(source, "resolve_commit", resolve)
    if phase == "before":
        await substitute()
    evidence_sha = prior if phase == "lapse" else graded
    await tracker.update_issue(issue_key=CHILD, body=body(evidence_sha))
    request = REQUEST.model_copy(
        update={"repo_url": remote.as_uri(), "cache_key": "lane-cache"}
    )
    verifier = build(git=native, cache=cache, source=source)
    before = tracker_writes()
    if phase == "lapse":
        observed = await verifier.observe(request)
        assert observed.is_lapse
        assert observed.criterion.issue_key == CHILD
        assert (
            observed.recorded_evidence.graded_sha == prior and observed.head_sha == head
        )
        assert observed.current_claim is None
    else:
        with pytest.raises(
            AuditEvidenceReadError,
            match="branch" if phase == "off-branch" else "substitutes",
        ) as raised:
            await verifier.observe(request)
        assert raised.value.criterion_key == CHILD
    assert inserted == ([head] if phase in {"before", "during"} else [])
    assert not runner.calls and not workspace.calls
    assert tracker_writes() == before


@pytest.mark.parametrize("read_number", [1, 2])
async def test_unreadable_replacement_namespace_cannot_be_reported_as_lapse(
    setup, monkeypatch, tracker_writes, read_number
):
    build, runner, git, _, _, workspace, *_ = setup
    error = RuntimeError("the replacement namespace could not be read")
    monkeypatch.setattr(
        git,
        "has_replace_refs",
        AsyncMock(side_effect=[False] * (read_number - 1) + [error]),
    )
    before = tracker_writes()
    with pytest.raises(AuditEvidenceReadError) as raised:
        await build().observe(REQUEST)
    assert raised.value.criterion_key == CHILD
    assert raised.value.__cause__ is error
    assert not runner.calls and not workspace.calls
    assert tracker_writes() == before


@pytest.mark.parametrize("read_number", [1, 2])
async def test_native_replacement_read_settles_before_canceling_lapse(
    setup, monkeypatch, tmp_path, read_number
):
    build, runner, git, _, _, workspace, *_ = setup
    await assert_git_read_settles_before_release(
        invoke=lambda: build().observe(REQUEST),
        git=git,
        workspace=workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        phase="has_replace_refs",
        read_number=read_number,
        expect_release=False,
    )
    assert not runner.calls and not workspace.calls
