"""Escalations land before the report step, and the report is never a first writer.

Ordering is read off the workspace's own tool call log rather than off the
order the run report happens to list its writes in.
"""

import json

import pytest

from kodezart.core.errors import McpTransportError
from kodezart.services.audit_runtime import AuditRunIncompleteError
from kodezart.types.domain.dispatch import PassRun
from tests.fakes import PassThroughGate
from tests.integration.test_audit_runtime_native import (
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

SUMMARY_PREFIX = "[native-audit:"
ESCALATION_PREFIX = "[native-audit-escalation:"


def summary_writes(server):
    """Every (index, body) at which the scope summary was written."""
    return [
        (index, str(arguments["body"]))
        for index, (name, arguments) in enumerate(server.calls)
        if name == "save_comment"
        and str(arguments.get("body", "")).startswith(SUMMARY_PREFIX)
        and arguments.get("issueId") == APPROVED_ISSUE
    ]


@pytest.fixture
async def instructed(repository, server, tmp_path):
    """An instructed refutation over a scope whose owner is still in progress."""
    built = await build_native_audit(
        repository, server, tmp_path, gate=PassThroughGate()
    )
    _audit, executor, fake, *_ = built
    fake.issues[ROOT].status = "In Progress"
    fake.issues[ROOT].status_type = "started"
    executor.claim_verdicts = {CHILD: "refuted"}
    executor.instruction = True
    return built


async def test_escalation_writes_complete_before_the_report_step(instructed):
    audit, _executor, server, *_ = instructed

    assert await audit.run(FIXTURE_NOW) is PassRun.RAN

    escalation = next(
        row for row in server.comments if row.body.startswith(ESCALATION_PREFIX)
    )
    reported = summary_writes(server)
    assert len(reported) == 1
    report_at = reported[0][0]
    assert landed(server, "save_comment", body=escalation.body) < report_at
    assert (
        landed(server, "save_issue", id=CHILD, addLabels=["needs-decision"]) < report_at
    )


async def test_every_summary_claim_resolves_to_a_record_at_report_time(instructed):
    audit, _executor, server, *_ = instructed
    resolved: list[tuple[str, str]] = []
    original = server.call_tool

    async def observing(*, name, arguments):
        if name == "save_comment" and str(arguments.get("body", "")).startswith(
            SUMMARY_PREFIX
        ):
            payload = json.loads(str(arguments["body"]).partition("\n")[2])
            if "record_refs" in payload:
                # Read every claimed record BACK, at the moment of the write.
                for ref, record in zip(
                    payload["record_refs"], payload["records"], strict=True
                ):
                    kind = record["surface"]["kind"]
                    if kind == "marker_comment":
                        found = next(row for row in server.comments if row.id == ref)
                        assert found.body == record["content"]
                    else:
                        # The classification record addresses the criterion
                        # itself rather than a comment.
                        assert ref in server.issues
                    resolved.append((ref, kind))
        return await original(name=name, arguments=arguments)

    server.call_tool = observing

    assert await audit.run(FIXTURE_NOW) is PassRun.RAN

    escalation = next(
        row for row in server.comments if row.body.startswith(ESCALATION_PREFIX)
    )
    refutation = next(
        row
        for row in server.comments
        if row.issue_id == CHILD and '"detector":"current_check"' in row.body
    )
    assert {ref for ref, _kind in resolved} >= {escalation.id, refutation.id}
    # The label record addresses the criterion rather than a comment, and it
    # resolves to that criterion at report time.
    assert "criterion_sub_issue" in {kind for _ref, kind in resolved}


async def test_a_report_step_that_raises_leaves_every_escalation_recorded(instructed):
    audit, _executor, server, *_ = instructed
    original = server.call_tool

    async def failing(*, name, arguments):
        if name == "save_comment" and str(arguments.get("body", "")).startswith(
            SUMMARY_PREFIX
        ):
            payload = str(arguments["body"]).partition("\n")[2]
            if '"record_refs"' in payload:
                raise McpTransportError(
                    "fixture report destination outage",
                    server_name="fake-linear",
                    tool_name=name,
                )
        return await original(name=name, arguments=arguments)

    server.call_tool = failing

    with pytest.raises(AuditRunIncompleteError) as raised:
        await audit.run(FIXTURE_NOW)
    scope = raised.value.report.scopes[0]

    assert [row.subject for row in scope.unavailable] == [scope.scope]
    assert summary_writes(server) == []
    assert any(row.body.startswith(ESCALATION_PREFIX) for row in server.comments)
    assert "needs-decision" in server.issues[CHILD].labels
    # The reopen does not depend on the report step.
    assert state_writes(server) == [{"id": CHILD, "state": unstarted_state(server)}]
    assert server.issues[CHILD].status == "Todo"
