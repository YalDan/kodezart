"""A tick over a scope still in progress completes and names what it deferred."""

import json

import pytest

from kodezart.chains.audit_forge import AuditForgeVerifier
from kodezart.domain.errors import AgentSDKError, AuditEvidenceReadError
from kodezart.services.audit_runtime import AuditRunIncompleteError
from kodezart.types.domain.agent import AUDIT_CLAIM_SCHEMA, DETECTOR_REMOVAL_SCHEMA
from kodezart.types.domain.audit_evidence import AuditEvidenceObservation
from kodezart.types.domain.dispatch import PassRun
from tests.fakes import PassThroughGate
from tests.integration.test_audit_runtime_native import (
    BASE_CHECK,
    add_criterion,
    build_native_audit,
    state_writes,
)
from tests.tracker.conftest import APPROVED_ISSUE, FIXTURE_NOW
from tests.tracker.test_audit_evidence_git import repository as repository
from tests.tracker.test_audit_requests import CHILD, ROOT
from tests.tracker.test_state_history import server as server

__all__ = ["repository", "server"]

OPEN = "audit/open-criterion"
LAPSED = "audit/lapsed-criterion"
OPEN_CHECK = "The open criterion names a file nobody has written yet."
LAPSED_CHECK = "The lapsed criterion was graded against an earlier commit."


def prompts(executor, schema):
    return [
        call["prompt"]
        for call in executor.calls
        if call["output_format"]["schema"] == schema
    ]


def audit_comments(server):
    return [row for row in server.comments if row.body.startswith("[native-audit:")]


def summary_payload(server, report_issue_key):
    bodies = [
        row.body for row in audit_comments(server) if row.issue_id == report_issue_key
    ]
    assert len(bodies) == 1, bodies
    return json.loads(bodies[0].partition("\n")[2])


@pytest.fixture
async def in_progress(repository, server, tmp_path):
    _remote, _author, _observer, prior, _head = repository
    built = await build_native_audit(
        repository, server, tmp_path, gate=PassThroughGate()
    )
    _audit, executor, fake, *_ = built
    fake.issues[ROOT].status = "In Progress"
    fake.issues[ROOT].status_type = "started"
    add_criterion(
        fake,
        OPEN,
        status="Todo",
        status_type="unstarted",
        graded_sha=prior,
        check=OPEN_CHECK,
    )
    add_criterion(
        fake,
        LAPSED,
        status="Done",
        status_type="completed",
        graded_sha=prior,
        check=LAPSED_CHECK,
    )
    executor.checks.update({OPEN: OPEN_CHECK, LAPSED: LAPSED_CHECK})
    return built


async def test_a_tick_over_a_scope_in_progress_completes_and_names_what_it_deferred(
    in_progress,
):
    audit, executor, server, _tracker, _git, workspace, repository = in_progress
    _remote, _author, _observer, prior, head = repository

    assert await audit.run(FIXTURE_NOW) is PassRun.RAN
    scope = audit.last_report.scopes[0]
    assert scope.status == "complete", scope.model_dump_json()
    assert {(row.subject.key, row.reason.value) for row in scope.deferred} == {
        (ROOT, "terminal_not_reached"),
        (OPEN, "claim_not_made"),
        (LAPSED, "graded_behind_head"),
    }

    # The one Done claim standing at the head is the only one re-judged.
    claims = prompts(executor, AUDIT_CLAIM_SCHEMA)
    assert len(claims) == 1
    assert BASE_CHECK in claims[0]
    assert not any(OPEN_CHECK in call["prompt"] for call in executor.calls)

    # The lapse stays a reported observation naming its two commits.
    lapses = [
        row
        for row in scope.raw_observations
        if isinstance(row, AuditEvidenceObservation)
        and row.criterion.issue_key == LAPSED
    ]
    assert [row.recorded_evidence.graded_sha for row in lapses] == [prior]
    assert [row.head_sha for row in lapses] == [head]
    assert [row.verdict.value for row in lapses] == ["unverifiable"]
    assert all(row.is_lapse for row in lapses)

    # Nothing was moved and nothing was written about a deferred member.
    assert state_writes(server) == []
    assert {row.issue_id for row in audit_comments(server)} == {
        CHILD,
        APPROVED_ISSUE,
    }
    summary = summary_payload(server, APPROVED_ISSUE)
    assert {(row["subject"]["key"], row["reason"]) for row in summary["deferred"]} == {
        (ROOT, "terminal_not_reached"),
        (OPEN, "claim_not_made"),
        (LAPSED, "graded_behind_head"),
    }
    assert not workspace._workspaces


@pytest.mark.parametrize("arm", ["removal", "forge"])
async def test_a_lapsed_criterion_whose_side_arm_fails_is_still_deferred(
    in_progress, monkeypatch, arm
):
    """A grading behind the head is decided before the side arms are read.

    The lapsed criterion's detector-removal reading, or its forge read,
    suffers a declared outage, so that arm has an unavailable reason to
    report and no reading beside it. The lapse is still a deferral and the
    tick still completes: were the reasons read first, the scope would be
    refused over a reading it discards anyway.
    """
    audit, executor, server, _tracker, _git, _workspace, _repository = in_progress
    failed = []
    if arm == "removal":

        async def during(kwargs):
            if (
                kwargs["output_format"]["schema"] == DETECTOR_REMOVAL_SCHEMA
                and LAPSED_CHECK in kwargs["prompt"]
            ):
                failed.append(kwargs["output_format"]["schema"])
                raise AgentSDKError(
                    "provider unavailable", error_kind="fixture-provider"
                )

        executor.during = during
    else:
        observe = AuditForgeVerifier.observe

        async def unavailable(self, request):
            if request.criterion_key == LAPSED:
                failed.append(request.criterion_key)
                raise AuditEvidenceReadError(
                    criterion_key=request.criterion_key, reason="forge unavailable"
                )
            return await observe(self, request)

        monkeypatch.setattr(AuditForgeVerifier, "observe", unavailable)

    assert await audit.run(FIXTURE_NOW) is PassRun.RAN
    assert len(failed) == 1
    scope = audit.last_report.scopes[0]
    assert scope.status == "complete", scope.model_dump_json()
    assert (LAPSED, "graded_behind_head") in {
        (row.subject.key, row.reason.value) for row in scope.deferred
    }
    assert state_writes(server) == []


@pytest.mark.parametrize("claim", ["todo", "done"])
async def test_a_member_that_never_fired_is_deferred_and_a_done_claim_is_not(
    repository, server, tmp_path, claim
):
    built = await build_native_audit(
        repository, server, tmp_path, gate=PassThroughGate()
    )
    audit, executor, fake, _tracker, _git, workspace, _repository = built
    fake.comments.clear()
    fake.issues[ROOT].status = "Todo"
    fake.issues[ROOT].status_type = "unstarted"
    if claim == "todo":
        fake.issues[CHILD].status = "Todo"
        fake.issues[CHILD].status_type = "unstarted"

    if claim == "todo":
        assert await audit.run(FIXTURE_NOW) is PassRun.RAN
        scope = audit.last_report.scopes[0]
        assert scope.status == "complete", scope.model_dump_json()
        assert {(row.subject.key, row.reason.value) for row in scope.deferred} == {
            (ROOT, "terminal_not_reached"),
            (CHILD, "claim_not_made"),
        }
        # Only the summary's own write-back judge ran.
        assert prompts(executor, AUDIT_CLAIM_SCHEMA) == []
        assert state_writes(fake) == []
    else:
        with pytest.raises(AuditRunIncompleteError) as raised:
            await audit.run(FIXTURE_NOW)
        scope = raised.value.report.scopes[0]
        assert [row.subject.key for row in scope.deferred] == [ROOT]
        assert any(
            row.subject.key == CHILD and "no unique native lane record" in row.reason
            for row in scope.unavailable
        ), scope.model_dump_json()
        assert state_writes(fake) == []
    assert not workspace._workspaces
