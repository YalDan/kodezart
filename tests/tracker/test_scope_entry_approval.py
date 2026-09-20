"""The approval question a scope run's entry asks, on every implementation.

The entry addresses a scope, not a member, so the answer has to follow the
label cascade upward from the addressed node. These cases pin the three
readings the composed question has — an issue scope, a container scope, and
a milestone, which has no label level of its own.
"""

from kodezart.services.scope_approval import scope_approved
from kodezart.types.domain.operation import ScopeLabel
from tests.tracker.test_scope_approval import (
    APPROVAL_LABELS,
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

#: The two reads that can carry a configured label on a container.
LABEL_READS = ("get_project", "get_initiative")


def _place_milestone(approval: ApprovalFixture) -> None:
    """Give the domain double the milestone the native fixture already has."""
    approval.fake.scope_containers[MILESTONE] = _container(MILESTONE, PROJECT)


async def test_a_milestone_ref_reports_no_scope_label_on_every_implementation(
    approval: ApprovalFixture,
) -> None:
    _place_milestone(approval)
    # Plant a label in each backing store. Neither reading may surface it:
    # no native object of this kind carries a configured label.
    approval.fake.scope_label_members[MILESTONE] = frozenset({ScopeLabel.APPROVED})
    approval.server.milestones[PROJECT.key][0]["labels"] = [APPROVAL_LABELS["approved"]]

    assert await approval.tracker.read_scope_labels(ref=MILESTONE) == frozenset()


async def test_a_milestone_scope_is_approved_by_its_project(
    approval: ApprovalFixture,
) -> None:
    _place_milestone(approval)
    assert await scope_approved(ref=MILESTONE, tracker=approval.tracker) is False

    approval.labels(PROJECT, ScopeLabel.APPROVED)
    assert await scope_approved(ref=MILESTONE, tracker=approval.tracker) is True

    approval.labels(PROJECT)
    approval.labels(INITIATIVE, ScopeLabel.APPROVED)
    assert await scope_approved(ref=MILESTONE, tracker=approval.tracker) is True

    approval.labels(INITIATIVE)
    approval.fake.scope_label_members[MILESTONE] = frozenset({ScopeLabel.APPROVED})
    approval.server.milestones[PROJECT.key][0]["labels"] = [APPROVAL_LABELS["approved"]]
    approval.server.calls.clear()
    assert await scope_approved(ref=MILESTONE, tracker=approval.tracker) is False
    assert not [
        arguments
        for tool, arguments in approval.server.calls
        if tool in LABEL_READS and arguments.get("query") == MILESTONE.key
    ]


async def test_approval_on_the_project_admits_an_issue_scope_under_it(
    approval: ApprovalFixture,
) -> None:
    """The addressed scope is one member issue; only its project is labelled."""
    assert await scope_approved(ref=ROOT, tracker=approval.tracker) is False

    approval.labels(PROJECT, ScopeLabel.APPROVED)
    assert await scope_approved(ref=ROOT, tracker=approval.tracker) is True
