"""A refuted finished claim goes back to unstarted once, beside its evidence."""

import json
from copy import deepcopy
from datetime import timedelta

import pytest

from kodezart.services.audit_runtime import AuditRunIncompleteError
from kodezart.types.domain.agent import (
    AUDIT_CLAIM_SCHEMA,
    AUDIT_MANDATE_SCHEMA,
    WRITE_BACK_SCHEMA,
)
from kodezart.types.domain.audit_evidence import AuditEvidenceObservation
from kodezart.types.domain.dispatch import PassRun
from tests.fakes import PassThroughGate
from tests.integration.test_audit_runtime_native import (
    BASE_CHECK,
    add_criterion,
    build_native_audit,
    criterion_body,
    landed,
    state_writes,
    unstarted_state,
)
from tests.tracker.conftest import APPROVED_ISSUE, FIXTURE_NOW
from tests.tracker.test_audit_evidence_git import command
from tests.tracker.test_audit_evidence_git import repository as repository
from tests.tracker.test_audit_requests import CHILD, ROOT
from tests.tracker.test_state_history import server as server

__all__ = ["repository", "server"]


def audit_comments(server, issue_key=None):
    return [
        row
        for row in server.comments
        if row.body.startswith("[native-audit:")
        and (issue_key is None or row.issue_id == issue_key)
    ]


def published(comment):
    return json.loads(comment.body.partition("\n")[2])["publication"]


def sessions(executor, schema):
    return len(
        [call for call in executor.calls if call["output_format"]["schema"] == schema]
    )


#: Two more criteria a case can put under the same owner, each with its own
#: Check sentence so a session's subject is unambiguous.
SECOND = "audit/second-criterion"
THIRD = "audit/third-criterion"
SECOND_CHECK = "The second criterion also reads the committed contents."
THIRD_CHECK = "The third criterion reads the committed contents as well."


@pytest.fixture
async def working_scope(repository, server, tmp_path):
    """The composed audit over a scope whose owner is still in progress."""
    built = await build_native_audit(
        repository, server, tmp_path, gate=PassThroughGate()
    )
    _audit, _executor, fake, *_ = built
    fake.issues[ROOT].status = "In Progress"
    fake.issues[ROOT].status_type = "started"
    return built


async def test_a_refuted_done_claim_goes_back_once_with_its_evidence(working_scope):
    audit, executor, server, _tracker, _git, workspace, repository = working_scope
    _remote, _author, _observer, _prior, head = repository
    executor.claim_verdicts = {CHILD: "refuted"}
    owner_before = deepcopy(server.issues[ROOT])
    body_before = server.issues[CHILD].description

    assert await audit.run(FIXTURE_NOW) is PassRun.RAN
    scope = audit.last_report.scopes[0]
    assert scope.status == "complete", scope.model_dump_json()

    # Exactly one state write, naming nothing but the criterion and the state.
    assert state_writes(server) == [{"id": CHILD, "state": unstarted_state(server)}]
    assert server.issues[CHILD].status == "Todo"
    assert server.issues[CHILD].status_type == "unstarted"

    # Its evidence is the refutation comment, published and verified first.
    refutations = [
        row
        for row in audit_comments(server, CHILD)
        if published(row).get("detector") == "current_check"
    ]
    assert len(refutations) == 1
    report = published(refutations[0])["report"]
    assert report["claim"]["judgment"]["verdict"] == "refuted"
    assert report["claim"]["head_sha"] == head
    assert report["mandate"]["verdict"] == "refuted"
    assert landed(server, "save_comment", body=refutations[0].body) < landed(
        server, "save_issue", id=CHILD, state=unstarted_state(server)
    )

    # The reopen is the scope's last verified write, after the summary.
    assert scope.writes[-1].artifact.surface.kind.value == "criterion_sub_issue"
    assert scope.writes[-1].artifact.surface.ref.key == CHILD
    assert scope.writes[-1].verdict.value == "holds"
    assert '"state_kind": "unstarted"' in scope.writes[-1].artifact.content
    assert scope.writes[-2].artifact.surface.ref.key == APPROVED_ISSUE

    # The owner follows by the rollup read, never by a second write.
    assert not any(row.get("id") == ROOT for row in state_writes(server))
    after = deepcopy(server.issues[ROOT])
    after.updated_at = owner_before.updated_at
    assert after == owner_before
    # No Evidence row and no body is written by the audit.
    assert server.issues[CHILD].description == body_before
    assert not workspace._workspaces


@pytest.mark.parametrize("arm", ["unverifiable", "overclaim_refuted"])
async def test_an_unverifiable_or_a_side_arm_refutation_moves_nothing(
    working_scope, arm
):
    audit, executor, server, _tracker, _git, workspace, _repository = working_scope
    if arm == "unverifiable":
        executor.claim_verdicts = {CHILD: "unverifiable"}
    else:
        executor.overclaim_verdict = "refuted"

    with pytest.raises(AuditRunIncompleteError) as raised:
        await audit.run(FIXTURE_NOW)
    scope = raised.value.report.scopes[0]

    assert state_writes(server) == []
    assert server.issues[CHILD].status == "Done"
    assert audit_comments(server, APPROVED_ISSUE) == []
    reasons = [row.reason for row in scope.unavailable]
    if arm == "unverifiable":
        assert any(
            "unverifiable claim supplies no authorized" in row for row in reasons
        )
        assert any(
            row.kind == "claim"
            and row.report.claim.judgment.verdict.value == "unverifiable"
            for row in scope.observations
        )
    else:
        # Only a refuted current-Check claim reopens; every other refuted
        # kind keeps the refusal it has always had.
        assert any("workflow-state authority" in row for row in reasons)
        assert any(
            row.kind == "overclaim"
            and row.report.claim.judgment.verdict.value == "refuted"
            for row in scope.observations
        )
    assert not workspace._workspaces


async def test_an_unrelated_refusal_does_not_block_the_reopen(working_scope):
    audit, executor, server, _tracker, _git, workspace, repository = working_scope
    _remote, _author, _observer, _prior, head = repository
    add_criterion(
        server,
        SECOND,
        status="Done",
        status_type="completed",
        graded_sha=head,
        check=SECOND_CHECK,
    )
    executor.checks[SECOND] = SECOND_CHECK
    executor.claim_verdicts = {CHILD: "refuted", SECOND: "unverifiable"}

    with pytest.raises(AuditRunIncompleteError) as raised:
        await audit.run(FIXTURE_NOW)
    scope = raised.value.report.scopes[0]

    assert any(
        row.subject.key == SECOND and "unverifiable claim" in row.reason
        for row in scope.unavailable
    ), scope.model_dump_json()
    # No summary: coverage is incomplete. The reopen happens anyway.
    assert audit_comments(server, APPROVED_ISSUE) == []
    assert state_writes(server) == [{"id": CHILD, "state": unstarted_state(server)}]
    assert server.issues[CHILD].status == "Todo"
    assert server.issues[SECOND].status == "Done"
    refutation = next(
        row
        for row in audit_comments(server, CHILD)
        if published(row).get("detector") == "current_check"
    )
    assert landed(server, "save_comment", body=refutation.body) < landed(
        server, "save_issue", id=CHILD, state=unstarted_state(server)
    )
    assert not workspace._workspaces


async def test_a_lapse_is_reported_and_left_done_then_the_same_criterion_is_refuted(
    working_scope,
):
    """Both arms of KOD-520 in one fixture: behind head, then failing at head."""
    audit, executor, server, _tracker, _git, workspace, repository = working_scope
    _remote, _author, _observer, prior, head = repository
    server.issues[CHILD].description = criterion_body(
        check=BASE_CHECK, graded_sha=prior
    )

    # Tick one: the recorded grading is behind the head.
    assert await audit.run(FIXTURE_NOW) is PassRun.RAN
    first = audit.last_report.scopes[0]
    assert first.status == "complete", first.model_dump_json()
    assert [(row.subject.key, row.reason.value) for row in first.deferred] == [
        (ROOT, "terminal_not_reached"),
        (CHILD, "graded_behind_head"),
    ]
    lapses = [
        row
        for row in first.raw_observations
        if isinstance(row, AuditEvidenceObservation)
        and row.criterion.issue_key == CHILD
    ]
    assert [row.recorded_evidence.graded_sha for row in lapses] == [prior]
    assert [row.head_sha for row in lapses] == [head]
    assert [row.verdict.value for row in lapses] == ["unverifiable"]
    # No mandate verdict is owed, because no claim was judged at all.
    assert [row.current_claim for row in lapses] == [None]
    assert not any(row.kind == "claim" for row in first.observations)
    assert sessions(executor, AUDIT_CLAIM_SCHEMA) == 0
    # No session of any kind is spent on it: a mandate hunt has nothing to
    # hunt for when no claim was judged.
    assert sessions(executor, AUDIT_MANDATE_SCHEMA) == 0
    # It is reported, not reopened.
    assert state_writes(server) == []
    assert server.issues[CHILD].status == "Done"

    # Tick two: the lane re-graded the same criterion at the head, and the
    # Check fails there. A body edit moves no state stamp, so this is the
    # periodic full sweep rather than a delta one.
    server.issues[CHILD].description = criterion_body(check=BASE_CHECK, graded_sha=head)
    executor.claim_verdicts = {CHILD: "refuted"}

    assert await audit.run(FIXTURE_NOW + timedelta(seconds=120)) is PassRun.RAN
    second = audit.last_report.scopes[0]
    assert second.status == "complete", second.model_dump_json()
    assert second.coverage.full
    refutation = next(
        row
        for row in audit_comments(server, CHILD)
        if published(row).get("detector") == "current_check"
    )
    report = published(refutation)["report"]
    assert report["claim"]["judgment"]["verdict"] == "refuted"
    assert report["claim"]["head_sha"] == head
    assert report["mandate"]["verdict"] == "refuted"
    assert state_writes(server) == [{"id": CHILD, "state": unstarted_state(server)}]
    assert landed(server, "save_comment", body=refutation.body) < landed(
        server, "save_issue", id=CHILD, state=unstarted_state(server)
    )
    assert server.issues[CHILD].status == "Todo"
    assert server.issues[CHILD].status_type == "unstarted"
    assert not workspace._workspaces


async def test_a_reopen_its_judge_refutes_is_reported_and_the_next_one_still_moves(
    working_scope,
):
    """A move its own verification cannot settle is a refusal, not a crash.

    Two refuted criteria are owed a reopen. Every round over the first one's
    move is refuted by the write-back judge, so that move exhausts its budget:
    the scope reports the criterion in the words of the refusal and stays
    incomplete, while the next owed criterion is still moved and verified.
    Unrefused, the run would instead build a completed coverage carrying an
    unverified write and fail its own validator on the way out.
    """
    audit, executor, server, _tracker, _git, workspace, repository = working_scope
    _remote, _author, _observer, _prior, head = repository
    add_criterion(
        server,
        SECOND,
        status="Done",
        status_type="completed",
        graded_sha=head,
        check=SECOND_CHECK,
    )
    executor.checks[SECOND] = SECOND_CHECK
    executor.claim_verdicts = {CHILD: "refuted", SECOND: "refuted"}
    executor.refute_reopen_of = CHILD

    with pytest.raises(AuditRunIncompleteError) as raised:
        await audit.run(FIXTURE_NOW)
    scope = raised.value.report.scopes[0]

    assert any(
        row.subject.key == CHILD
        and "the reopen exhausted canonical write verification" in row.reason
        for row in scope.unavailable
    ), scope.model_dump_json()
    # The first move landed, its repair round replayed it without a second
    # write, and the criterion the refusal does not name still moved.
    assert [row["id"] for row in state_writes(server)] == [CHILD, SECOND]
    assert server.issues[SECOND].status == "Todo"
    reopens = [
        write
        for write in scope.writes
        if write.artifact.surface.kind.value == "criterion_sub_issue"
    ]
    assert [row.artifact.surface.ref.key for row in reopens] == [CHILD, SECOND]
    assert reopens[0].verdict.value != "holds"
    assert reopens[1].verdict.value == "holds"
    assert not workspace._workspaces


async def test_a_branch_that_moves_before_the_batch_keeps_the_reopen_from_landing(
    working_scope,
):
    """One head re-read stands between the report step and the state moves.

    The scope summary's own write-back session pushes a commit to the branch
    every observation of this tick was graded against. The re-read refuses the
    scope and the owed reopen never runs: a move landing here would say a
    criterion was judged at a commit nothing in this tick read.
    """
    audit, executor, server, _tracker, _git, workspace, repository = working_scope
    _remote, author, _observer, _prior, _head = repository
    executor.claim_verdicts = {CHILD: "refuted"}
    pushed = []

    async def during(kwargs):
        if kwargs["output_format"]["schema"] != WRITE_BACK_SCHEMA or pushed:
            return
        artifact = json.loads(executor.tagged(kwargs["prompt"], "written_artifact"))
        if "record_refs" not in artifact["content"]:
            return
        # Another file than the one every session reads, so the commit moves
        # the branch head without changing what a session sees at its own ref.
        (author / "notes.txt").write_text("a commit this tick never read\n")
        command(author, "add", "--all")
        command(author, "commit", "-qm", "unobserved")
        command(author, "push", "-q", "configured-remote", "ordinary-name")
        pushed.append(kwargs["prompt"])

    executor.during = during

    with pytest.raises(AuditRunIncompleteError) as raised:
        await audit.run(FIXTURE_NOW)
    scope = raised.value.report.scopes[0]

    assert len(pushed) == 1
    assert any(
        row.subject == scope.scope
        and "observed branch changed during the read sweep" in row.reason
        for row in scope.unavailable
    ), scope.model_dump_json()
    assert state_writes(server) == []
    assert server.issues[CHILD].status == "Done"
    assert not workspace._workspaces


async def test_a_refutation_of_a_strict_subset_moves_exactly_those_criteria(
    working_scope,
):
    """Reopening is per sub-issue: the named subset moves, the rest is untouched."""
    audit, executor, server, _tracker, _git, workspace, repository = working_scope
    _remote, _author, _observer, _prior, head = repository
    for key, check in ((SECOND, SECOND_CHECK), (THIRD, THIRD_CHECK)):
        add_criterion(
            server,
            key,
            status="Done",
            status_type="completed",
            graded_sha=head,
            check=check,
        )
        executor.checks[key] = check
    executor.claim_verdicts = {CHILD: "refuted", SECOND: "refuted"}
    before = {key: deepcopy(server.issues[key]) for key in (ROOT, CHILD, SECOND, THIRD)}

    assert await audit.run(FIXTURE_NOW) is PassRun.RAN
    scope = audit.last_report.scopes[0]
    assert scope.status == "complete", scope.model_dump_json()

    # Exactly the two named criteria move, once each.
    moved = state_writes(server)
    assert sorted(row["id"] for row in moved) == sorted([CHILD, SECOND])
    assert {row["state"] for row in moved} == {unstarted_state(server)}
    assert [server.issues[key].status for key in (CHILD, SECOND)] == ["Todo", "Todo"]

    # Each of them stands on its own verified refutation, published before its
    # own move: a criterion reopened on a sibling's evidence would be a move a
    # reader cannot link to anything about it.
    for key in (CHILD, SECOND):
        refutations = [
            row
            for row in audit_comments(server, key)
            if published(row).get("detector") == "current_check"
        ]
        assert len(refutations) == 1, key
        report = published(refutations[0])["report"]
        assert report["claim"]["judgment"]["verdict"] == "refuted", key
        assert report["claim"]["head_sha"] == head, key
        assert landed(server, "save_comment", body=refutations[0].body) < landed(
            server, "save_issue", id=key, state=unstarted_state(server)
        ), key
        assert [
            write.verdict.value
            for write in scope.writes
            if write.artifact.surface.kind.value == "criterion_sub_issue"
            and write.artifact.surface.ref.key == key
        ] == ["holds"], key

    # The untouched sibling and the owner are byte-identical apart from the
    # activity stamp the audit's own holds comment on the sibling moved.
    for key in (THIRD, ROOT):
        after = deepcopy(server.issues[key])
        assert after.state_changed_at == before[key].state_changed_at
        assert after.status == before[key].status
        assert after.status_type == before[key].status_type
        after.updated_at = before[key].updated_at
        assert after == before[key], key
    # No body in the scope is written by the audit, reopened or not.
    assert {key: server.issues[key].description for key in before} == {
        key: row.description for key, row in before.items()
    }
    assert not workspace._workspaces


# ---------------------------------------------------------------------------
# KOD-832 clause 8 — one audit tick re-verifies at least one finished claim
# against the branch and reopens a planted false one with evidence.
# ---------------------------------------------------------------------------


async def test_one_audit_tick_reverifies_a_finished_claim_and_reopens_the_planted_false(
    working_scope,
):
    """The clause in process, on the shape the scope path leaves behind.

    An owner still In Progress carrying two finished criteria graded at the
    head of the branch its lane record names. One of them still holds at that
    head; the other is the planted false claim. The tick is a single ``run``,
    and a second one over the same board re-judges nothing.
    """
    audit, executor, server, _tracker, _git, workspace, repository = working_scope
    _remote, _author, _observer, _prior, head = repository
    add_criterion(
        server,
        SECOND,
        status="Done",
        status_type="completed",
        graded_sha=head,
        check=SECOND_CHECK,
    )
    executor.checks[SECOND] = SECOND_CHECK
    executor.claim_verdicts = {CHILD: "refuted", SECOND: "holds"}
    bodies_before = {
        key: server.issues[key].description for key in (CHILD, SECOND, ROOT)
    }
    calls_before = len(server.calls)

    assert await audit.run(FIXTURE_NOW) is PassRun.RAN
    scope = audit.last_report.scopes[0]
    assert scope.status == "complete", scope.model_dump_json()

    # Both finished claims were re-judged against the branch, each in a fresh
    # session carrying its own Check and that branch's head.
    judged = [
        call
        for call in executor.calls
        if call["output_format"]["schema"] == AUDIT_CLAIM_SCHEMA
    ]
    assert len(judged) == 2
    assert {BASE_CHECK, SECOND_CHECK} == {
        check
        for check in (BASE_CHECK, SECOND_CHECK)
        if any(check in call["prompt"] for call in judged)
    }
    assert all(head in call["prompt"] for call in judged)
    assert all(call["session_id"] is None for call in judged)

    # The claim that still holds keeps its state and its verdict on the board.
    assert server.issues[SECOND].status == "Done"
    holding = next(
        row
        for row in audit_comments(server, SECOND)
        if published(row).get("detector") == "current_check"
    )
    assert published(holding)["report"]["claim"]["judgment"]["verdict"] == "holds"

    # The planted false claim is refuted with linkable evidence, and that
    # evidence lands before the one state write that reopens it.
    refutation = next(
        row
        for row in audit_comments(server, CHILD)
        if published(row).get("detector") == "current_check"
    )
    report = published(refutation)["report"]
    assert report["claim"]["judgment"]["verdict"] == "refuted"
    assert report["claim"]["head_sha"] == head
    assert report["claim"]["check"] == BASE_CHECK
    assert report["claim"]["judgment"]["evidence"].strip()
    assert report["mandate"]["verdict"] == "refuted"
    assert landed(server, "save_comment", body=refutation.body) < landed(
        server, "save_issue", id=CHILD, state=unstarted_state(server)
    )
    assert state_writes(server) == [{"id": CHILD, "state": unstarted_state(server)}]
    assert server.issues[CHILD].status == "Todo"
    assert server.issues[CHILD].status_type == "unstarted"

    # The three artifacts a reader links: the refutation on the criterion, the
    # criterion's state history, and the scope summary on the report issue.
    assert [row[:2] for row in server.issues[CHILD].previous_states] == [
        ("Done", "completed")
    ]
    summary = json.loads(
        audit_comments(server, APPROVED_ISSUE)[0].body.partition("\n")[2]
    )
    assert refutation.id in summary["record_refs"]
    assert holding.id in summary["record_refs"]
    # Nothing was written into a body, and the owner was never written.
    assert {
        key: server.issues[key].description for key in bodies_before
    } == bodies_before
    assert not any(row["id"] == ROOT for row in state_writes(server))
    # The owner is reported deferred for not having reached a terminal state,
    # and no write of any kind names it — not a state, not a comment.
    assert [(row.subject.key, row.reason.value) for row in scope.deferred] == [
        (ROOT, "terminal_not_reached")
    ]
    assert [
        (name, dict(arguments))
        for name, arguments in server.calls[calls_before:]
        if name.startswith("save_")
        and ROOT in {arguments.get("id"), arguments.get("issueId")}
    ] == []

    # A second tick over the same board judges nothing again: the criterion the
    # first one reopened is unstarted, so it has made no claim to re-judge.
    claims = sessions(executor, AUDIT_CLAIM_SCHEMA)
    assert await audit.run(FIXTURE_NOW + timedelta(seconds=60)) is PassRun.RAN
    second = audit.last_report.scopes[0]
    assert second.status == "complete", second.model_dump_json()
    assert [(row.subject.key, row.reason.value) for row in second.deferred] == [
        (CHILD, "claim_not_made")
    ]
    assert sessions(executor, AUDIT_CLAIM_SCHEMA) == claims
    assert state_writes(server) == [{"id": CHILD, "state": unstarted_state(server)}]
    assert not workspace._workspaces
