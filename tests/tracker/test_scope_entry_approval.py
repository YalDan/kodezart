"""The approval question a scope run's entry asks, on every implementation.

The entry addresses a scope, not a member, so the answer has to follow the
label cascade upward from the addressed node. These cases pin the three
readings the composed question has — an issue scope, a container scope, and
a milestone, which has no label level of its own.
"""

import pytest

from kodezart.core.errors import TrackerUnavailableError
from kodezart.domain.errors import ScopeNotApprovedError, ScopeReadError
from kodezart.services.scope_approval import scope_approved
from kodezart.types.domain.operation import OperationMemberAbsentError, ScopeLabel
from kodezart.types.domain.scope import ScopeRef
from tests.tracker.conftest import linear_over_fake_mcp
from tests.tracker.test_scope_approval import (
    APPROVAL_LABELS,
    CHILD,
    ApprovalFixture,
    approval,
)
from tests.tracker.test_scope_reads import (
    INITIATIVE,
    MILESTONE,
    PROJECT,
    ROOT,
    ScopeMcpServer,
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


async def test_a_project_scope_is_approved_by_its_initiative(
    approval: ApprovalFixture,
) -> None:
    """A container scope reads its own labels and then walks upward.

    The milestone case reaches the initiative through two edges, so it
    cannot tell a walk that stops at the project from one that continues.
    This one addresses the project itself.
    """
    assert await scope_approved(ref=PROJECT, tracker=approval.tracker) is False

    approval.labels(INITIATIVE, ScopeLabel.APPROVED)
    assert await scope_approved(ref=PROJECT, tracker=approval.tracker) is True

    approval.labels(INITIATIVE)
    assert await scope_approved(ref=PROJECT, tracker=approval.tracker) is False


@pytest.mark.parametrize("ref", [PROJECT, INITIATIVE])
async def test_a_missing_container_propagates_as_a_read_error(
    approval: ApprovalFixture, ref: ScopeRef
) -> None:
    """A container the tracker cannot read is never read as "not approved".

    Answering False for an unreadable node would admit nothing and name a
    withheld approval as the cause, when the cause is a container that is
    gone. The read error reaches the caller instead.
    """
    approval.fake.scope_containers.pop(ref, None)
    approval.server.projects.pop(ref.key, None)
    approval.server.initiatives.pop(ref.key, None)

    with pytest.raises((ScopeReadError, TrackerUnavailableError)) as caught:
        await scope_approved(ref=ref, tracker=approval.tracker)

    assert not isinstance(caught.value, ScopeNotApprovedError)


@pytest.mark.parametrize("ref", [CHILD, PROJECT])
async def test_an_unmapped_approval_label_refuses_a_scope_question_before_any_read(
    ref: ScopeRef,
) -> None:
    """An operation with no approved label names no scope as unapproved.

    Both label readings the composed question uses — the per-issue one and
    the container one — answer the unanswerable question with a typed
    refusal, ahead of the backend. Swallowing it would read as "not
    approved", which admits nothing and names no cause (KOD-382).
    """
    server = ScopeMcpServer()
    tracker = linear_over_fake_mcp(server, scope_labels={})

    with pytest.raises(OperationMemberAbsentError, match=r"scope_labels\.approved"):
        await scope_approved(ref=ref, tracker=tracker)

    assert server.calls == []


async def test_an_unmapped_approval_label_refuses_a_milestone_scope_question() -> None:
    """A milestone adds no label level, so the refusal arrives from its project.

    The milestone's own metadata read is the general container read every
    surface uses, not a label reading, so it is not the place the approval
    vocabulary is required; the first label reading in the walk is its
    project's, and that one refuses.
    """
    server = ScopeMcpServer()
    tracker = linear_over_fake_mcp(server, scope_labels={})

    with pytest.raises(OperationMemberAbsentError, match=r"scope_labels\.approved"):
        await scope_approved(ref=MILESTONE, tracker=tracker)
