"""Expected tracker review terminals are checked against actual forge facts."""

from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import httpx
import pytest

from kodezart.core.config import AppConfig
from kodezart.domain.errors import AuditClaimReadError, PRStateReadError
from kodezart.domain.lane_record import render_lane_record
from kodezart.services.audit_terminal import AuditTerminalReader
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_terminal import (
    AuditTerminalRequest,
    TerminalDiscrepancy,
)
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.pr_state import PRLifecycle, PRState
from kodezart.types.domain.run_state import LaneRunState
from tests.adapters.test_github_api import _make_client
from tests.domain.test_lane_record import record_data
from tests.fakes import FakeGitService, FakeMcpIssue, FakePRStateReader, FakeRepoCache
from tests.tracker.conftest import WORKFLOW_STATE_NAMES, fixture_server
from tests.tracker.conftest import tracker as tracker
from tests.tracker.conftest import tracker_writes as tracker_writes

ISSUE = "terminal/issue"
CHILD = "terminal/criterion"
REPO = "https://github.com/example/project"
HEAD = "a" * 40
BRANCH = "ordinary-name"
PREFIXES = {"run_state": "terminal-record"}
OPERATION = OperationConfig(
    operation_name="fixture",
    workspace="fixture",
    workflow_states=WORKFLOW_STATE_NAMES,
    marker_prefixes=PREFIXES,
)
REQUEST = AuditTerminalRequest(issue_key=ISSUE, lane_key="lane:alpha", repo_url=REPO)


def pr_state(**changes):
    return PRState(
        url=f"{REPO}/pull/7",
        number=7,
        head_repo_url=REPO,
        head_branch=BRANCH,
        head_sha=HEAD,
        lifecycle=PRLifecycle.OPEN,
        **changes,
    )


def wire(value):
    return {
        "html_url": value.url,
        "number": value.number,
        "state": "open" if value.lifecycle is PRLifecycle.OPEN else "closed",
        "merged": value.lifecycle is PRLifecycle.MERGED,
        "head": {
            "ref": value.head_branch,
            "sha": value.head_sha,
            "repo": {
                "html_url": value.head_repo_url,
                "full_name": urlsplit(value.head_repo_url).path.removeprefix("/"),
            },
        },
    }


@pytest.fixture
def server():
    server = fixture_server()
    server.issues[ISSUE] = FakeMcpIssue(
        id=ISSUE, status="In Review", status_type="started"
    )
    server.issues[CHILD] = FakeMcpIssue(
        id=CHILD,
        parent_id=ISSUE,
        labels=["acceptance-condition"],
        status="Done",
        status_type="completed",
        description="**Check:** Its output reverses the input.",
    )
    return server


@pytest.fixture(params=["fake", "github"])
async def forge(request):
    records = {(REPO, 7): pr_state()}
    fake = FakePRStateReader(records=records)
    if request.param == "fake":
        yield fake, records, fake.calls
        return
    calls = []

    def handler(req):
        assert req.method == "GET"
        assert req.url.path == "/repos/example/project/pulls/7"
        calls.append((REPO, 7))
        return httpx.Response(200, json=wire(records[(REPO, 7)]))

    client = _make_client(handler)
    try:
        yield client, records, calls
    finally:
        await client.close()


@pytest.fixture
async def setup(tracker, forge):
    port, _, _ = forge
    record = LaneRunState.model_validate(
        {
            **record_data(),
            "pr": {
                "url": f"{REPO}/pull/7",
                "number": 7,
                "state": "irrelevant old state",
            },
        }
    )
    comment = await tracker.post_comment(
        issue_key=ISSUE,
        body=render_lane_record(record=record, marker_prefixes=PREFIXES),
    )
    git = FakeGitService(remote_branch_shas={BRANCH: HEAD})
    reader = AuditTerminalReader(
        tracker=tracker,
        records=LaneRecordReader(tracker=tracker, operation=OPERATION),
        forge=port,
        git=git,
        cache=FakeRepoCache(),
        operation=OPERATION,
        config=AppConfig(git_remote="configured-remote"),
    )
    return reader, git, record, comment


async def test_open_unmerged_review_terminal_has_no_discrepancy(
    setup, tracker_writes, forge
):
    reader, git, _, comment = setup
    before = tracker_writes()
    result = await reader.observe(REQUEST)
    assert result.verdict is AuditVerdict.HOLDS
    assert result.discrepancies == ()
    assert result.pr.lifecycle is PRLifecycle.OPEN
    assert result.branch_head == HEAD and result.record_ref == comment.comment_key
    assert tracker_writes() == before
    assert forge[2] == [(REPO, 7), (REPO, 7)]
    assert {call[0] for call in git.calls} == {"remote_branch_sha"}


async def test_closed_unmerged_pr_is_a_discrepancy_despite_recorded_pr_state(
    setup, forge
):
    reader, *_ = setup
    forge[1][(REPO, 7)] = forge[1][(REPO, 7)].model_copy(
        update={"lifecycle": PRLifecycle.CLOSED}
    )
    result = await reader.observe(REQUEST)
    assert result.verdict is AuditVerdict.REFUTED
    assert result.discrepancies == (TerminalDiscrepancy.CLOSED_UNMERGED_PR,)


async def test_same_name_and_sha_in_foreign_head_repository_never_verify(
    setup, forge, tracker_writes
):
    reader, git, *_ = setup
    original = forge[1][(REPO, 7)]
    forge[1][(REPO, 7)] = original.model_copy(
        update={"head_repo_url": "https://github.com/foreign/fork"}
    )
    assert original.head_branch == BRANCH
    assert original.head_sha == git._remote_branch_shas[BRANCH] == HEAD
    before = tracker_writes()
    with pytest.raises(PRStateReadError, match="another repository"):
        await reader.observe(REQUEST)
    assert tracker_writes() == before


async def test_merged_is_distinct_from_closed_unmerged_but_never_required(setup, forge):
    reader, *_ = setup
    forge[1][(REPO, 7)] = forge[1][(REPO, 7)].model_copy(
        update={"lifecycle": PRLifecycle.MERGED}
    )
    result = await reader.observe(REQUEST)
    assert result.verdict is AuditVerdict.HOLDS
    assert not result.discrepancies


async def test_missing_branch_is_measured_from_remote_not_recorded_sha(setup):
    reader, git, *_ = setup
    git._remote_branch_shas[BRANCH] = None
    result = await reader.observe(REQUEST)
    assert result.verdict is AuditVerdict.REFUTED
    assert TerminalDiscrepancy.NO_BRANCH in result.discrepancies
    assert result.branch_head is None


async def test_pr_head_outside_recorded_associations_is_unresolved(setup, forge):
    reader, git, *_ = setup
    git._remote_branch_shas["unassociated"] = HEAD
    forge[1][(REPO, 7)] = forge[1][(REPO, 7)].model_copy(
        update={"head_branch": "unassociated"}
    )
    result = await reader.observe(REQUEST)
    assert result.discrepancies == (TerminalDiscrepancy.UNRESOLVED_ASSOCIATION,)


async def test_reaped_and_prior_associations_do_not_need_live_refs(setup):
    reader, git, record, _ = setup
    assert "reaped-ref" in {item.branch for item in record.associations}
    result = await reader.observe(REQUEST)
    assert result.verdict is AuditVerdict.HOLDS
    assert all(call[-1] == BRANCH for call in git.calls)


async def test_current_pr_may_use_a_recorded_deliverable_branch(setup, forge):
    reader, git, *_ = setup
    branch = "has-ralph-in-its-name"
    git._remote_branch_shas[branch] = HEAD
    forge[1][(REPO, 7)] = forge[1][(REPO, 7)].model_copy(update={"head_branch": branch})
    assert (await reader.observe(REQUEST)).verdict is AuditVerdict.HOLDS


@pytest.mark.parametrize("damage", ["issue", "criterion", "record", "pr", "branch"])
async def test_changing_terminal_never_returns_healthy_observation(
    setup, tracker, forge, monkeypatch, damage
):
    reader, git, record, comment = setup
    original = reader._head
    count = 0

    async def changing(repository, branch):
        nonlocal count
        count += 1
        if count == 2:
            if damage == "issue":
                await tracker.update_issue(issue_key=ISSUE, body="changed")
            elif damage == "criterion":
                await tracker.update_issue(issue_key=CHILD, body="changed Check")
            elif damage == "record":
                changed = record.model_copy(update={"head_sha": "new-recorded-head"})
                await tracker.upsert_comment(
                    target=ISSUE,
                    marker=comment.body.splitlines()[0],
                    body=render_lane_record(
                        record=changed, marker_prefixes=PREFIXES
                    ).partition("\n")[2],
                )
            elif damage == "pr":
                forge[1][(REPO, 7)] = forge[1][(REPO, 7)].model_copy(
                    update={"lifecycle": PRLifecycle.CLOSED}
                )
            else:
                git._remote_branch_shas[BRANCH] = "b" * 40
        return await original(repository, branch)

    monkeypatch.setattr(reader, "_head", changing)
    with pytest.raises(AuditClaimReadError):
        await reader.observe(REQUEST)


@pytest.mark.parametrize("boundary", ["tracker", "forge", "git"])
async def test_failed_native_read_is_never_a_healthy_terminal(
    setup, tracker, forge, monkeypatch, boundary
):
    reader, git, *_ = setup
    failure = RuntimeError("native resource unavailable")
    target, method = {
        "tracker": (tracker, "read_issue"),
        "forge": (forge[0], "read_pr_state"),
        "git": (git, "remote_branch_sha"),
    }[boundary]
    monkeypatch.setattr(target, method, AsyncMock(side_effect=failure))
    with pytest.raises(RuntimeError, match="unavailable"):
        await reader.observe(REQUEST)


async def test_missing_recorded_pr_is_unresolved_without_forge_lookup(
    setup, tracker, forge
):
    reader, _, record, comment = setup
    changed = record.model_copy(update={"pr": None})
    await tracker.upsert_comment(
        target=ISSUE,
        marker=comment.body.splitlines()[0],
        body=render_lane_record(record=changed, marker_prefixes=PREFIXES).partition(
            "\n"
        )[2],
    )
    result = await reader.observe(REQUEST)
    assert result.verdict is AuditVerdict.REFUTED
    assert result.discrepancies == (TerminalDiscrepancy.UNRESOLVED_ASSOCIATION,)
    assert result.pr is None
    assert forge[2] == []


async def test_current_native_pr_sha_must_equal_its_resolved_branch(setup, forge):
    reader, *_ = setup
    forge[1][(REPO, 7)] = forge[1][(REPO, 7)].model_copy(update={"head_sha": "b" * 40})
    with pytest.raises(AuditClaimReadError, match="do not agree"):
        await reader.observe(REQUEST)


@pytest.mark.parametrize("subject", [ISSUE, CHILD])
async def test_expected_review_requires_parent_state_and_every_completed_criterion(
    setup, tracker, forge, subject
):
    reader, git, *_ = setup
    await tracker.restore_workflow_state(issue_key=subject, state_name="Todo")
    with pytest.raises(AuditClaimReadError, match="expected review terminal"):
        await reader.observe(REQUEST)
    assert not git.calls and not forge[2]
