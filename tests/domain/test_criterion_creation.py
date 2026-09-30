"""A checklist a person wrote, and when the criteria stage still owes it.

The parser reads Markdown task-list lines and nothing else; the stage is owed
while no child counts or while some item is stated by no child's Check; and a
child the board Canceled or closed as a Duplicate refuses no creation.
"""

import pytest

from kodezart.domain.criterion_creation import (
    criteria_owed,
    criterion_body,
    existing_criterion,
)
from kodezart.domain.errors import InvalidFireCriterionError
from kodezart.domain.fire_spec import checklist_items
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import make_tracker_issue

PARENT = "parent"
FIRST, SECOND = "The export names its sources.", "The rows are sorted by path."
CHECKLIST_BODY = f"The export.\n\n## Acceptance\n\n- [ ] {FIRST}\n- [x] {SECOND}\n"


def child(key, *, check=FIRST, kind=WorkflowStateKind.UNSTARTED, body=None):
    """A criterion child of the parent, stating *check* unless *body* is given."""
    return make_tracker_issue(
        key,
        parent_key=PARENT,
        issue_labels=frozenset({"criterion"}),
        state_name=kind.value,
        state_kind=kind,
        body=(
            criterion_body(parent_key=PARENT, check=check, do="Read the export.")
            if body is None
            else body
        ),
    )


@pytest.mark.parametrize("marker", ["-", "*", "+"])
@pytest.mark.parametrize("tick", ["[ ]", "[x]", "[X]"])
def test_each_marker_and_each_tick_box_is_an_item(marker, tick):
    assert checklist_items(f"Intro.\n{marker} {tick} {FIRST}\n") == (FIRST,)


def test_an_indented_item_is_an_item_and_its_text_is_stripped():
    body = f"  - [ ]   {FIRST}   \n\t* [x] {SECOND}\n"
    assert checklist_items(body) == (FIRST, SECOND)


@pytest.mark.parametrize(
    "line",
    [
        f"- {FIRST}",
        f"[ ] {FIRST}",
        f"-[ ] {FIRST}",
        f"- [ ]{FIRST}",
        "- [ ] ",
        f"- [y] {FIRST}",
        f"1. [ ] {FIRST}",
        f"The line says - [ ] {FIRST}",
    ],
    ids=[
        "plain-bullet",
        "no-marker",
        "no-space-after-marker",
        "no-space-after-tick",
        "no-text",
        "other-tick",
        "numbered",
        "mid-line",
    ],
)
def test_a_line_that_is_not_a_task_list_item_is_no_item(line):
    assert checklist_items(f"{line}\n") == ()


def test_the_items_come_in_the_order_the_body_states_them():
    assert checklist_items(CHECKLIST_BODY) == (FIRST, SECOND)


def test_the_stage_is_owed_while_no_child_counts():
    assert criteria_owed(body="No checklist.", children=())
    canceled = child("c1", kind=WorkflowStateKind.CANCELED)
    duplicate = child("c2", kind=WorkflowStateKind.DUPLICATE)
    assert criteria_owed(body="No checklist.", children=(canceled, duplicate))
    assert not criteria_owed(body="No checklist.", children=(child("c3"),))


def test_the_stage_is_owed_while_a_checklist_item_has_no_criterion():
    adopted = child("c1")
    assert criteria_owed(body=CHECKLIST_BODY, children=(adopted,))
    both = (adopted, child("c2", check=SECOND))
    assert not criteria_owed(body=CHECKLIST_BODY, children=both)


@pytest.mark.parametrize(
    "kind", [WorkflowStateKind.CANCELED, WorkflowStateKind.DUPLICATE]
)
def test_an_item_a_person_canceled_is_covered_and_not_owed_again(kind):
    """The Check of a child the board closed still states its item."""
    closed = child("c2", check=SECOND, kind=kind)
    assert not criteria_owed(body=CHECKLIST_BODY, children=(child("c1"), closed))
    assert (
        existing_criterion(
            parent_key=PARENT, check=SECOND, children=(child("c1"), closed)
        )
        == closed
    )


@pytest.mark.parametrize(
    "kind", [WorkflowStateKind.CANCELED, WorkflowStateKind.DUPLICATE]
)
def test_a_non_counting_child_with_no_check_refuses_no_creation(kind):
    closed = child("c2", kind=kind, body="**Do:** no check")
    assert (
        existing_criterion(parent_key=PARENT, check=SECOND, children=(closed,)) is None
    )
    adopted = child("c1")
    assert (
        existing_criterion(parent_key=PARENT, check=FIRST, children=(closed, adopted))
        == adopted
    )


def test_a_counting_child_with_no_check_still_refuses():
    broken = child("c2", body="**Do:** no check")
    with pytest.raises(InvalidFireCriterionError):
        existing_criterion(parent_key=PARENT, check=SECOND, children=(broken,))
