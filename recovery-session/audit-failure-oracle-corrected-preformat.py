"""Independent operational versus programmer failure probes at real boundaries."""
import asyncio

import pytest

from kodezart.domain.errors import AuditRunIncompleteError
from kodezart.types.domain.agent import AUDIT_CLAIM_SCHEMA
from kodezart.types.domain.audit_runtime import AuditForgePublication, AuditPublishedArtifact
from tests.tracker.test_audit_requests import CHILD, ROOT
from tests.integration.test_audit_runtime_native import native_audit, repository, server
from tests.tracker.conftest import FIXTURE_NOW

__all__ = ["native_audit", "repository", "server"]


@pytest.mark.parametrize("failure_kind", ["programmer", "cancel", "outage"])
async def test_native_lane_discovery_keeps_failure_kind(native_audit, monkeypatch, failure_kind):
    audit, executor, backend, tracker, git, workspace, repository = native_audit
    original = backend.call_tool
    error = RuntimeError("independent adapter implementation defect") if failure_kind == "programmer" else asyncio.CancelledError()
    reached = False

    async def call(*, name, arguments):
        nonlocal reached
        if name == "list_comments":
            reached = True
            if failure_kind == "outage":
                backend._tool_errors["list_comments"] = "independent unavailable service"
            else:
                raise error
        return await original(name=name, arguments=arguments)

    monkeypatch.setattr(backend, "call_tool", call)
    if failure_kind == "outage":
        with pytest.raises(AuditRunIncompleteError) as caught:
            await audit.run(FIXTURE_NOW)
        assert caught.value.report.scopes[0].status == "incomplete"
        assert any("independent unavailable" in row.reason for row in caught.value.report.scopes[0].unavailable)
    else:
        with pytest.raises(type(error)) as caught:
            await audit.run(FIXTURE_NOW)
        assert caught.value is error
    assert reached
    assert not [row for row in backend.comments if row.body.startswith("[native-audit:")]
    assert not executor.calls
    assert not workspace._workspaces


async def test_actual_git_remote_loss_is_scope_unavailability(native_audit):
    audit, executor, backend, tracker, git, workspace, repository = native_audit
    remote = repository[0]
    initial_refs = {row.id for row in backend.comments}
    unavailable = remote.with_name(remote.name + "-unavailable")
    reached = False

    async def during(kwargs):
        nonlocal reached
        if kwargs["output_format"]["schema"] == AUDIT_CLAIM_SCHEMA and not reached:
            reached = True
            remote.rename(unavailable)

    executor.during = during
    try:
        with pytest.raises(AuditRunIncompleteError) as caught:
            await audit.run(FIXTURE_NOW)
        assert caught.value.report.scopes[0].status == "incomplete"
        assert caught.value.report.scopes[0].unavailable
        assert reached
        scope = caught.value.report.scopes[0]
        assert not scope.writes
        assert all(row.kind == "forge" for row in scope.observations)
        records = [row for row in backend.comments if row.body.startswith("[native-audit:")]
        assert len(records) == 1
        assert records[0].issue_id == CHILD
        published = AuditPublishedArtifact.model_validate_json(records[0].body.partition("\n")[2])
        assert isinstance(published.publication, AuditForgePublication)
        forge = published.publication
        assert forge.graded_sha == repository[4]
        assert forge.report.claim.head_sha == repository[4]
        assert forge.report.claim.judgment.criterion_key == CHILD
        assert forge.report.claim.judgment.verdict.value == "holds"
        assert forge.report.claim.record_ref in initial_refs
        assert not published.escalation_refs
        assert backend.issues[ROOT].status == "In Review"
        assert backend.issues[CHILD].status == "Done"
        assert not workspace._workspaces
    finally:
        if unavailable.exists():
            unavailable.rename(remote)
