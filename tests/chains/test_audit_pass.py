"""Expected tracker review terminals are checked against actual forge facts."""

from inspect import signature
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import httpx
import pytest
from pydantic import ValidationError

from kodezart.chains.audit_evidence import AuditRestampVerifier
from kodezart.config.app import AppConfig
from kodezart.core.errors import TrackerUnavailableError
from kodezart.domain.errors import (
    AuditClaimReadError,
    AuditEvidenceReadError,
    PRStateReadError,
)
from kodezart.domain.lane_record import render_lane_record
from kodezart.domain.run_event_stream import LaneRunEvent
from kodezart.services.audit_terminal import AuditTerminalReader
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.audit import AuditClaimRequest, AuditVerdict
from kodezart.types.domain.audit_evidence import AuditRestampTrace
from kodezart.types.domain.audit_terminal import (
    AuditTerminalRequest,
    TerminalDiscrepancy,
)
from kodezart.types.domain.branch import BranchAssociation, BranchRole
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_lifecycle import UndemonstratedReason
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.pr_state import PRLifecycle, PRState
from kodezart.types.domain.run_event import UNDEMONSTRATED_EVENT_KINDS, RunEventKind
from kodezart.types.domain.run_state import LaneRunState
from tests.adapters.test_github_api import _make_client
from tests.domain.test_lane_record import record_data
from tests.fakes import FakeGitService, FakeMcpIssue, FakePRStateReader, FakeRepoCache
from tests.tracker.conftest import WORKFLOW_STATE_NAMES, fixture_server
from tests.tracker.conftest import clock as clock
from tests.tracker.conftest import tracker as tracker
from tests.tracker.conftest import tracker_writes as tracker_writes
from tests.tracker.lease_fixtures import leased_comment

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
        base_repo_url=REPO,
        base_branch="main",
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
        "base": {
            "ref": value.base_branch,
            "sha": "b" * 40,
            "repo": {
                "html_url": value.base_repo_url,
                "full_name": urlsplit(value.base_repo_url).path.removeprefix("/"),
            },
        },
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
        remote=AppConfig(git={"remote": "configured-remote"}).git.remote,
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
    assert result.verification_head == HEAD
    assert tracker_writes() == before
    assert forge[2] == [(REPO, 7), (REPO, 7)]
    assert {call[0] for call in git.calls} == {"remote_branch_sha"}
    assert {call[2] for call in git.calls} == {"configured-remote"}


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
    assert result.verification_head is None


#: The deliverable branch ``record_data`` associates with the loop branch's run,
#: and the head a consolidation left it at.
DELIVERABLE = "has-ralph-in-its-name"
DELIVERED = "d" * 40


def delivered(git, forge, record, *, contains=True, branch=DELIVERABLE):
    """The lane as consolidation leaves it: loop branch merged and deleted."""
    git._remote_branch_shas[BRANCH] = None
    git._remote_branch_shas[branch] = DELIVERED
    if contains:
        git._ancestor_pairs.add((record.head_sha, DELIVERED))
    forge[1][(REPO, 7)] = forge[1][(REPO, 7)].model_copy(
        update={"head_branch": branch, "head_sha": DELIVERED}
    )


async def rewrite(tracker, comment, record):
    await leased_comment(
        tracker,
        target=ISSUE,
        marker=comment.body.splitlines()[0],
        body=render_lane_record(record=record, marker_prefixes=PREFIXES).partition(
            "\n"
        )[2],
    )


async def test_a_loop_branch_consolidated_into_its_deliverable_is_not_missing(
    setup, forge
):
    reader, git, record, _ = setup
    delivered(git, forge, record)
    result = await reader.observe(REQUEST)
    assert result.verdict is AuditVerdict.HOLDS
    assert result.discrepancies == ()
    assert result.branch_head is None
    # The loop branch is gone, so the audit verifies at the recorded head the
    # deliverable branch holds, never at the deliverable's own head.
    assert result.verification_head == record.head_sha != DELIVERED
    kinds = [call[0] for call in git.calls]
    assert kinds.index("fetch") < kinds.index("is_ancestor")
    assert ("is_ancestor", "/tmp/fake-cache", record.head_sha, DELIVERED) in git.calls


async def test_a_deliverable_without_the_recorded_head_leaves_the_branch_missing(
    setup, forge
):
    reader, git, record, _ = setup
    delivered(git, forge, record, contains=False)
    result = await reader.observe(REQUEST)
    assert result.verdict is AuditVerdict.REFUTED
    assert result.discrepancies == (TerminalDiscrepancy.NO_BRANCH,)
    assert result.verification_head is None


async def test_a_recorded_head_the_cache_does_not_know_leaves_the_branch_missing(
    setup, forge
):
    reader, git, record, _ = setup
    delivered(git, forge, record)
    git.missing_objects.add(record.head_sha)
    result = await reader.observe(REQUEST)
    assert result.verdict is AuditVerdict.REFUTED
    assert result.discrepancies == (TerminalDiscrepancy.NO_BRANCH,)
    assert result.verification_head is None
    kinds = [call[0] for call in git.calls]
    assert kinds.index("fetch") < kinds.index("has_object")
    assert "is_ancestor" not in kinds


async def test_an_absent_deliverable_leaves_the_branch_missing(setup, forge):
    reader, git, record, _ = setup
    delivered(git, forge, record)
    git._remote_branch_shas[DELIVERABLE] = None
    result = await reader.observe(REQUEST)
    assert result.verdict is AuditVerdict.REFUTED
    assert TerminalDiscrepancy.NO_BRANCH in result.discrepancies
    assert not any(call[0] == "is_ancestor" for call in git.calls)


async def test_a_deliverable_of_another_run_never_stands_for_the_loop_branch(
    setup, tracker, forge
):
    reader, git, record, comment = setup
    loop, other = (
        next(item for item in record.associations if item.branch == branch)
        for branch in (BRANCH, "earlier-deliverable")
    )
    assert loop.run_id != other.run_id
    await rewrite(
        tracker, comment, record.model_copy(update={"associations": [loop, other]})
    )
    delivered(git, forge, record, branch=other.branch)
    result = await reader.observe(REQUEST)
    assert result.verdict is AuditVerdict.REFUTED
    assert result.discrepancies == (TerminalDiscrepancy.NO_BRANCH,)


def association(branch, role, run_id, derived_from=None):
    return BranchAssociation(
        branch=branch, role=role, run_id=run_id, derived_from=derived_from
    )


#: The loop branch's own run records no deliverable. Another run, whose LOOP
#: association names another branch, records one that holds the record head.
#: In the second shape that other run also names the recorded branch, under
#: a role that is not LOOP.
OTHER_RUN = {
    "another-loop": [],
    "recorded-branch-in-another-role": [
        association(BRANCH, BranchRole.RECOVERY, "run-earlier", "earlier-loop")
    ],
}


@pytest.mark.parametrize("shape", OTHER_RUN)
async def test_only_the_recorded_loop_branch_run_names_the_deliverable(
    setup, tracker, forge, shape
):
    reader, git, record, comment = setup
    associations = [
        association(BRANCH, BranchRole.LOOP, "run-current", DELIVERABLE),
        association(
            "earlier-loop", BranchRole.LOOP, "run-earlier", "earlier-deliverable"
        ),
        *OTHER_RUN[shape],
        association("earlier-deliverable", BranchRole.DELIVERABLE, "run-earlier"),
    ]
    await rewrite(
        tracker, comment, record.model_copy(update={"associations": associations})
    )
    delivered(git, forge, record, branch="earlier-deliverable")
    result = await reader.observe(REQUEST)
    assert result.verdict is AuditVerdict.REFUTED
    assert result.discrepancies == (TerminalDiscrepancy.NO_BRANCH,)
    assert result.verification_head is None
    # The other run's deliverable is read only as the pull request's head,
    # never tested for the record head.
    assert not {"has_object", "is_ancestor"} & {call[0] for call in git.calls}


async def test_a_loop_branch_of_two_runs_with_two_deliverables_is_unreadable(
    setup, tracker, forge
):
    reader, git, record, comment = setup
    loop = next(item for item in record.associations if item.branch == BRANCH)
    again = loop.model_copy(update={"run_id": "run-earlier"})
    await rewrite(
        tracker,
        comment,
        record.model_copy(update={"associations": [*record.associations, again]}),
    )
    delivered(git, forge, record)
    with pytest.raises(AuditClaimReadError, match="more than one deliverable"):
        await reader.observe(REQUEST)


async def test_a_deliverable_that_moves_during_the_read_is_never_healthy(setup, forge):
    reader, git, record, _ = setup
    delivered(git, forge, record)
    # The pull request stands on another recorded branch, so only the
    # deliverable read and its re-read see the move.
    forge[1][(REPO, 7)] = forge[1][(REPO, 7)].model_copy(
        update={"head_branch": "reaped-ref", "head_sha": HEAD}
    )
    git._remote_branch_shas["reaped-ref"] = HEAD
    git._remote_branch_sha_sequences[DELIVERABLE] = [DELIVERED, "e" * 40]
    with pytest.raises(AuditClaimReadError, match=r"^recorded branch changed"):
        await reader.observe(REQUEST)


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
                await leased_comment(
                    tracker,
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
    await leased_comment(
        tracker,
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


# ---------------------------------------------------------------------------
# Restamp traceability: the Evidence row's commit against the lane's gradings
# ---------------------------------------------------------------------------

LANE = "lane:alpha"
#: Two commits, so a history can name one and a row the other.
RESTAMPED_AT = "c" * 40
LATER_GRADING = "d" * 40
ANOTHER_CRITERION = "terminal/other-criterion"


def restamp_request() -> AuditClaimRequest:
    """The criterion whose Evidence row a trace is read for."""
    return AuditClaimRequest(
        criterion_key=CHILD, lane_issue_key=ISSUE, lane_key=LANE, repo_url=REPO
    )


def evidence_row(graded_sha: str) -> CriterionEvidence:
    return CriterionEvidence(graded_sha=graded_sha, test="tests/example.py::case")


async def record_grading(
    tracker,
    *,
    graded_sha,
    subject_key=CHILD,
    kind=RunEventKind.CRITERION_REFUTED,
):
    """Post one grading through the port's own append, never a hand-built body."""
    return await tracker.post_run_event(
        issue_key=ISSUE,
        event=LaneRunEvent(
            kind=kind,
            lane_key=LANE,
            subject_key=subject_key,
            graded_sha=graded_sha,
        ),
    )


#: One recorded grading, as the arms below name them: the kind the lane posted
#: and the commit it was read at.
REFUTED_LATER = (RunEventKind.CRITERION_REFUTED, LATER_GRADING)
REFUTED_AT_THE_ROW = (RunEventKind.CRITERION_REFUTED, RESTAMPED_AT)
PASSED_AT_THE_ROW = (RunEventKind.CRITERION_PASSED, RESTAMPED_AT)


@pytest.mark.parametrize(
    ("recorded", "verdict"),
    [
        pytest.param((REFUTED_LATER,), AuditVerdict.REFUTED, id="forged-restamp"),
        pytest.param(
            (REFUTED_AT_THE_ROW,), AuditVerdict.HOLDS, id="refuted-at-the-row"
        ),
        pytest.param(
            (REFUTED_LATER, PASSED_AT_THE_ROW),
            AuditVerdict.HOLDS,
            id="refuted-then-passed",
        ),
    ],
)
async def test_a_restamp_holds_only_when_the_last_recorded_grading_names_its_commit(
    tracker, recorded, verdict
):
    """One fixture, three arms: the row is restamped at the same commit in all.

    The first arm reds against an implementation that grades whether the
    restamped verdict happens to be true at head, because nothing here is
    read at head at all — only the stream the lane itself posted. It is also
    the forged restamp: a row naming a commit this lane recorded no grading
    at.

    The third arm is the ordinary lifecycle — refuted at one commit, then
    passed at the one the row now names — which a lane leaves as two entries
    because a passing cross-off records its grading too (KOD-506). An
    implementation reading only refutations answers REFUTED for it, which is
    the very state the first arm pins as forged.
    """
    for kind, graded_sha in recorded:
        await record_grading(tracker, kind=kind, graded_sha=graded_sha)
    trace = await AuditRestampVerifier(events=tracker).observe(
        request=restamp_request(), evidence=evidence_row(RESTAMPED_AT)
    )
    assert trace is not None
    assert trace.verdict is verdict
    assert trace.criterion_key == CHILD
    # The arm names what it answered against, not just what it answered.
    assert trace.history == tuple(graded_sha for _, graded_sha in recorded)


async def test_a_restamp_is_traced_to_the_last_grading_and_not_to_any_earlier_one(
    tracker,
):
    """An entry followed by a later grading is itself later than the restamp."""
    await record_grading(tracker, graded_sha=RESTAMPED_AT)
    await record_grading(tracker, graded_sha=LATER_GRADING)
    trace = await AuditRestampVerifier(events=tracker).observe(
        request=restamp_request(), evidence=evidence_row(RESTAMPED_AT)
    )
    assert trace is not None
    assert trace.history == (RESTAMPED_AT, LATER_GRADING)
    assert trace.verdict is AuditVerdict.REFUTED


@pytest.mark.parametrize("reason", list(UndemonstratedReason))
async def test_an_undemonstrated_reading_after_the_row_is_no_write_of_it(
    tracker, reason
):
    """A reading that wrote no row does not move where the row's history ends.

    The criterion was passed at the commit the row names, and a later attempt
    read it as undemonstrated: that reading posts its own event, keyed to this
    criterion at the later commit, and writes neither the row nor the state.
    The row's history is its writes, so it still ends at the row's commit and
    the restamp holds. Read as an entry, the reading would refute a row that
    nothing rewrote (KOD-506, KOD-610).
    """
    await record_grading(
        tracker, kind=RunEventKind.CRITERION_PASSED, graded_sha=RESTAMPED_AT
    )
    await record_grading(
        tracker, kind=UNDEMONSTRATED_EVENT_KINDS[reason], graded_sha=LATER_GRADING
    )
    trace = await AuditRestampVerifier(events=tracker).observe(
        request=restamp_request(), evidence=evidence_row(RESTAMPED_AT)
    )
    assert trace is not None
    assert trace.history == (RESTAMPED_AT,)
    assert trace.verdict is AuditVerdict.HOLDS


async def test_another_criterions_grading_at_the_same_commit_traces_nothing(tracker):
    """The stream is one lane's, so a subject key is what selects a criterion."""
    await record_grading(
        tracker, graded_sha=RESTAMPED_AT, subject_key=ANOTHER_CRITERION
    )
    assert (
        await AuditRestampVerifier(events=tracker).observe(
            request=restamp_request(), evidence=evidence_row(RESTAMPED_AT)
        )
        is None
    )


async def test_a_criterion_with_no_recorded_grading_is_not_traced(tracker):
    """An empty history is a row no lane write accounts for, not a refusal.

    Every cross-off a lane writes records its grading, so a row with no entry
    at all is one a person moved into the finished state or one whose
    announcement never landed. Reading it as "no entry at this commit" would
    answer for a write this stream never saw.
    """
    assert (
        await AuditRestampVerifier(events=tracker).observe(
            request=restamp_request(), evidence=evidence_row(RESTAMPED_AT)
        )
        is None
    )


async def test_a_grading_naming_no_commit_is_not_a_grading_at_all(tracker):
    """An event that is not about a grading has no commit and cannot be last."""
    await record_grading(tracker, graded_sha=RESTAMPED_AT)
    await tracker.post_run_event(
        issue_key=ISSUE,
        event=LaneRunEvent(
            kind=RunEventKind.LANE_DISPATCHED, lane_key=LANE, subject_key=CHILD
        ),
    )
    trace = await AuditRestampVerifier(events=tracker).observe(
        request=restamp_request(), evidence=evidence_row(RESTAMPED_AT)
    )
    assert trace is not None
    assert trace.history == (RESTAMPED_AT,)
    assert trace.verdict is AuditVerdict.HOLDS


def test_a_restamp_trace_is_read_from_the_stream_and_not_from_a_verdict():
    """A verdict is never an input, so there is no parameter to supply one."""
    parameters = set(signature(AuditRestampVerifier.observe).parameters)
    assert parameters == {"self", "request", "evidence"}


async def test_an_unreadable_grading_stream_is_a_typed_refusal_not_a_silent_absence():
    """The sweep's one translation point needs a raise, not ``None``.

    ``None`` means "nothing was ever recorded", which is a fact about the
    lane. A stream that could not be read is not that fact.
    """

    class UnreadableStream:
        async def lane_run_events(self, *, issue_key: str, lane_key: str):
            raise TrackerUnavailableError("the lane stream could not be read")

    with pytest.raises(AuditEvidenceReadError) as raised:
        await AuditRestampVerifier(events=UnreadableStream()).observe(
            request=restamp_request(), evidence=evidence_row(RESTAMPED_AT)
        )
    assert raised.value.criterion_key == CHILD


@pytest.mark.parametrize(
    ("history", "verdict"),
    [
        # The forge analogue's UNVERIFIABLE cannot be copied here: a trace
        # carrying it does not validate.
        ((RESTAMPED_AT,), AuditVerdict.UNVERIFIABLE),
        # A verdict that disagrees with the history it was read from.
        ((LATER_GRADING,), AuditVerdict.HOLDS),
        ((RESTAMPED_AT,), AuditVerdict.REFUTED),
        # An empty tuple is not a trace, so the model cannot be built on one.
        ((), AuditVerdict.HOLDS),
        ((), AuditVerdict.REFUTED),
    ],
)
def test_a_restamp_trace_cannot_be_built_against_its_own_recorded_history(
    history, verdict
):
    with pytest.raises(ValidationError):
        AuditRestampTrace(
            criterion_key=CHILD,
            recorded_evidence=evidence_row(RESTAMPED_AT),
            history=history,
            verdict=verdict,
            reason="a reading that does not follow what was read",
        )
