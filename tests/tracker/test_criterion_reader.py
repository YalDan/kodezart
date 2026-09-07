"""Criterion membership is current sub-issue data, never parent-body syntax."""

import pytest

from kodezart.domain.errors import CriterionReadError
from kodezart.types.domain.tracker import EnsureAction, MappingKind, MappingRef
from tests.fakes import FakeMcpIssue, FakeTrackerPort
from tests.tracker.conftest import fixture_server

PARENT = "parent/42"
FIRST = "condition/alpha"
SECOND = "condition/beta"
LABEL = "acceptance-condition"
EVIDENCE = "**Evidence:** sha abc123 · independent verification"


@pytest.fixture
def server():
    server = fixture_server()
    issues = [
        FakeMcpIssue(id=PARENT, description="No checklist is required here."),
        FakeMcpIssue(
            id=FIRST,
            title="A criterion with no AC token",
            parent_id=PARENT,
            labels=[LABEL],
            description=f"**Check:** behavior one\n\n{EVIDENCE}",
            status="Done",
            status_type="completed",
        ),
        FakeMcpIssue(
            id=SECOND,
            title="An ordinary title",
            parent_id=PARENT,
            labels=[LABEL],
            description="**Check:** behavior two\n\n**Evidence:** —",
        ),
        FakeMcpIssue(id="ordinary/1", parent_id=PARENT, labels=["criterion"]),
        FakeMcpIssue(id="grandchild/1", parent_id=FIRST, labels=[LABEL]),
        FakeMcpIssue(id="other-parent/1", labels=[LABEL]),
    ]
    server.issues.update({issue.id: issue for issue in issues})
    return server


async def test_exactly_direct_configured_criterion_children_are_read(
    tracker, tracker_writes
):
    writes = tracker_writes()
    criteria = await tracker.read_criteria(issue_key=PARENT)
    assert [criterion.issue_key for criterion in criteria] == [FIRST, SECOND]
    assert criteria[0].state_name == "Done"
    assert criteria[0].body.endswith(EVIDENCE)
    assert criteria[0].issue_labels == frozenset({"criterion"})
    assert tracker_writes() == writes
    for criterion in criteria:
        assert await tracker.read_issue(issue_key=criterion.issue_key) == criterion


async def test_successful_empty_is_distinct_from_a_failed_parent_read(tracker):
    assert tuple(await tracker.read_criteria(issue_key=SECOND)) == ()
    with pytest.raises(CriterionReadError) as raised:
        await tracker.read_criteria(issue_key="absent/1")
    assert raised.value.issue_key == "absent/1"


async def test_parent_text_cannot_mint_criterion_membership(tracker):
    await tracker.update_issue(
        issue_key=SECOND,
        body="- [x] parent/42-AC-1 (hard) · This looks like a checked criterion.",
    )
    assert tuple(await tracker.read_criteria(issue_key=SECOND)) == ()


async def test_each_read_uses_current_labels_and_parentage(tracker, server):
    assert len(await tracker.read_criteria(issue_key=PARENT)) == 2
    if isinstance(tracker, FakeTrackerPort):
        tracker.issues[FIRST] = tracker.issues[FIRST].model_copy(
            update={"issue_labels": frozenset()}
        )
        tracker.issues[SECOND] = tracker.issues[SECOND].model_copy(
            update={"parent_key": FIRST}
        )
        tracker.issues["ordinary/1"] = tracker.issues["ordinary/1"].model_copy(
            update={"issue_labels": frozenset({"criterion"})}
        )
    else:
        server.issues[FIRST].labels = []
        server.issues[SECOND].parent_id = FIRST
        server.issues["ordinary/1"].labels = [LABEL]
    assert [
        criterion.issue_key
        for criterion in await tracker.read_criteria(issue_key=PARENT)
    ] == ["ordinary/1"]


async def test_owned_issue_label_can_be_instated_and_read_back(tracker):
    ref = MappingRef(
        kind=MappingKind.ISSUE_LABEL,
        name="criterion",
        identifier="Verification requirement",
        scope="fixture-team",
    )
    first = await tracker.ensure_mappings(refs=[ref])
    assert first[0].action is EnsureAction.CREATED
    assert first[0].identifier == ref.identifier
    second = await tracker.ensure_mappings(refs=[ref])
    assert second[0].action is EnsureAction.ADOPTED
    assert tuple(await tracker.resolve_mappings(refs=[ref])) == ()
