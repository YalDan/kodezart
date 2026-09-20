"""A refuted finished claim goes back to unstarted once, beside its evidence."""

import json
from copy import deepcopy

import pytest

from kodezart.services.audit_runtime import AuditRunIncompleteError
from kodezart.types.domain.dispatch import PassRun
from tests.fakes import PassThroughGate
from tests.integration.test_audit_runtime_native import (
    add_criterion,
    build_native_audit,
    landed,
    state_writes,
    unstarted_state,
)
from tests.tracker.conftest import APPROVED_ISSUE, FIXTURE_NOW
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
    second = "audit/second-criterion"
    second_check = "The second criterion also reads the committed contents."
    add_criterion(
        server,
        second,
        status="Done",
        status_type="completed",
        graded_sha=head,
        check=second_check,
    )
    executor.checks[second] = second_check
    executor.claim_verdicts = {CHILD: "refuted", second: "unverifiable"}

    with pytest.raises(AuditRunIncompleteError) as raised:
        await audit.run(FIXTURE_NOW)
    scope = raised.value.report.scopes[0]

    assert any(
        row.subject.key == second and "unverifiable claim" in row.reason
        for row in scope.unavailable
    ), scope.model_dump_json()
    # No summary: coverage is incomplete. The reopen happens anyway.
    assert audit_comments(server, APPROVED_ISSUE) == []
    assert state_writes(server) == [{"id": CHILD, "state": unstarted_state(server)}]
    assert server.issues[CHILD].status == "Todo"
    assert server.issues[second].status == "Done"
    refutation = next(
        row
        for row in audit_comments(server, CHILD)
        if published(row).get("detector") == "current_check"
    )
    assert landed(server, "save_comment", body=refutation.body) < landed(
        server, "save_issue", id=CHILD, state=unstarted_state(server)
    )
    assert not workspace._workspaces
