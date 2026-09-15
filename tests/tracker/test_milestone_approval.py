"""A milestone scope uses its project approval and has no label level."""

import pytest

from kodezart.domain.errors import ScopeReadError
from kodezart.services.scope_resolution import resolve_scope
from kodezart.types.domain.operation import ScopeLabel
from tests.tracker.test_scope_approval import (
    APPROVAL_LABELS,
    PARENT,
    ApprovalFixture,
    approval,
)
from tests.tracker.test_scope_reads import MILESTONE, PROJECT, ROOT, _container

__all__ = ["approval"]


async def test_actual_milestone_scope_uses_current_project_approval(
    approval: ApprovalFixture,
) -> None:
    approval.fake.scope_containers[MILESTONE] = _container(MILESTONE, PROJECT)
    approval.fake.scope_memberships[MILESTONE] = (ROOT.key, PARENT.key)
    resolved = await resolve_scope(ref=MILESTONE, tracker=approval.tracker)
    assert resolved.ref == MILESTONE
    assert {issue.issue_key for issue in resolved.issues} == {ROOT.key, PARENT.key}

    approval.server.calls.clear()
    for expected in (False, True, False):
        approval.labels(PROJECT, *([ScopeLabel.APPROVED] if expected else []))
        for issue in resolved.issues:
            assert (
                await approval.tracker.execution_approved(issue_key=issue.issue_key)
                is expected
            )
    assert all("milestone" not in tool for tool, _ in approval.server.calls)
    assert not approval.fake.issue_writes


async def test_a_milestone_label_cannot_approve_its_project_or_members(
    approval: ApprovalFixture,
) -> None:
    # Deliberately inject a label into the fake's backing data. There is no
    # adapter call that can read or write a label on this kind of object.
    approval.fake.scope_label_members[MILESTONE] = frozenset({ScopeLabel.APPROVED})
    approval.server.milestones[PROJECT.key][0]["labels"] = [APPROVAL_LABELS["approved"]]
    assert await approval.tracker.execution_approved(issue_key=ROOT.key) is False
    assert all("milestone" not in tool for tool, _ in approval.server.calls)


async def test_milestone_membership_without_reported_project_refuses(
    approval: ApprovalFixture,
) -> None:
    approval.server.issue_detail_updates[ROOT.key] = {"projectId": None}
    approval.fake.issues[ROOT.key] = approval.fake.issues[ROOT.key].model_copy(
        update={"project_id": None}
    )
    with pytest.raises(ScopeReadError, match="owning project"):
        await approval.tracker.execution_approved(issue_key=ROOT.key)
