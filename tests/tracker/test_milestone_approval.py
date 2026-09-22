"""A milestone scope uses its project approval and has no label level."""

import pytest

from kodezart.domain.errors import ScopeReadError
from kodezart.services.scope_approval import scope_approved, scope_carries
from kodezart.services.scope_resolution import resolve_scope
from kodezart.types.domain.operation import ScopeLabel
from tests.tracker.test_scope_approval import (
    APPROVAL_LABELS,
    PARENT,
    ApprovalFixture,
    approval,
)
from tests.tracker.test_scope_reads import (
    INITIATIVE,
    MILESTONE,
    PROJECT,
    ROOT,
    _container,
)

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

    # The same planted label, asked of the milestone as an addressed scope.
    approval.fake.scope_containers[MILESTONE] = _container(MILESTONE, PROJECT)
    assert await scope_approved(ref=MILESTONE, tracker=approval.tracker) is False
    approval.labels(PROJECT, ScopeLabel.APPROVED)
    assert await scope_approved(ref=MILESTONE, tracker=approval.tracker) is True


async def test_a_milestone_resolves_its_projects_triage_as_it_resolves_its_approval(
    approval: ApprovalFixture,
) -> None:
    """One walk, asked with the member: a milestone's triage is its project's.

    The port method stays an exact-node read — a milestone reports no members
    of its own — so the answer can only have come from the chain above it.
    """
    approval.fake.scope_containers[MILESTONE] = _container(MILESTONE, PROJECT)
    triage = ScopeLabel.TRIAGE

    assert (
        await scope_carries(ref=MILESTONE, member=triage, tracker=approval.tracker)
        is False
    )

    approval.labels(PROJECT, triage)
    assert await approval.tracker.read_scope_labels(ref=MILESTONE) == frozenset()
    assert (
        await scope_carries(ref=MILESTONE, member=triage, tracker=approval.tracker)
        is True
    )
    # The same chain, asked for the approval member instead: absent here.
    assert await scope_approved(ref=MILESTONE, tracker=approval.tracker) is False

    # Planted on the milestone's own backing data, which no adapter call can
    # read or write. The answer does not move.
    approval.labels(PROJECT)
    approval.fake.scope_label_members[MILESTONE] = frozenset({triage})
    approval.server.milestones[PROJECT.key][0]["labels"] = [APPROVAL_LABELS["triage"]]
    assert (
        await scope_carries(ref=MILESTONE, member=triage, tracker=approval.tracker)
        is False
    )

    # Carried on the initiative above the owning project: the walk continues.
    approval.labels(INITIATIVE, triage)
    assert (
        await scope_carries(ref=MILESTONE, member=triage, tracker=approval.tracker)
        is True
    )
    assert not approval.fake.issue_writes


async def test_milestone_membership_without_reported_project_refuses(
    approval: ApprovalFixture,
) -> None:
    approval.server.issue_detail_updates[ROOT.key] = {"projectId": None}
    approval.fake.issues[ROOT.key] = approval.fake.issues[ROOT.key].model_copy(
        update={"project_id": None}
    )
    with pytest.raises(ScopeReadError, match="owning project"):
        await approval.tracker.execution_approved(issue_key=ROOT.key)
