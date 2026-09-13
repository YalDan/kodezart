"""Readable historical forge evidence cannot impersonate unreadable current work."""

import pytest

from kodezart.domain.criterion_evidence import render_evidence_field
from kodezart.domain.errors import AuditRunIncompleteError
from kodezart.types.domain.audit_runtime import (
    AuditForgePublication,
    AuditPublishedArtifact,
)
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from tests.integration.test_audit_runtime_native import native_audit, repository, server
from tests.tracker.conftest import FIXTURE_NOW
from tests.tracker.test_audit_requests import CHILD, ROOT

__all__ = ["native_audit", "repository", "server"]


async def test_independent_historical_sha_survives_unreadable_current_repository(
    native_audit,
):
    audit, executor, backend, _tracker, _git, workspace, repository = native_audit
    remote, _author, _observer, prior, current = repository
    assert prior != current
    original_body = backend.issues[CHILD].description
    backend.issues[CHILD].description = original_body.partition("**Evidence:**")[
        0
    ] + render_evidence_field(
        CriterionEvidence(graded_sha=prior, test="historical test receipt")
    )
    target = audit._targets[0]
    ci = target.sweep._forge._ci
    ci.observed_sha_by_ref[prior] = prior
    snapshot = await target.sweep.prepare()
    subject = next(row for row in snapshot.targets if row.issue.issue_key == CHILD)
    assert subject.source is not None
    record_ref = subject.source.comment.comment_key
    await audit._cache.ensure_available(remote.as_uri())
    unavailable = remote.with_name(remote.name + "-unavailable")
    remote.rename(unavailable)
    try:
        with pytest.raises(AuditRunIncompleteError) as caught:
            await audit.run(FIXTURE_NOW)
        scope = caught.value.report.scopes[0]
        assert scope.status == "incomplete"
        assert scope.unavailable
        assert scope.writes == ()
        assert len(scope.observations) == 1
        observed = scope.observations[0]
        assert isinstance(observed, AuditForgePublication)
        assert observed.graded_sha == prior
        assert observed.report.claim.head_sha == prior
        assert observed.report.claim.record_ref == record_ref
        assert observed.report.claim.judgment.criterion_key == CHILD
        assert observed.report.claim.judgment.verdict.value == "holds"
        records = [
            row for row in backend.comments if row.body.startswith("[native-audit:")
        ]
        assert len(records) == 1
        assert records[0].issue_id == CHILD
        published = AuditPublishedArtifact.model_validate_json(
            records[0].body.partition("\n")[2]
        )
        assert published.publication == observed
        assert published.escalation_refs == ()
        assert not executor.calls
        assert not workspace._workspaces
        assert not audit._coverage._covered
        assert not audit._coverage._last_tick
        assert not audit._coverage._last_full
        assert backend.issues[ROOT].status == "In Review"
        assert backend.issues[CHILD].status == "Done"
    finally:
        unavailable.rename(remote)
