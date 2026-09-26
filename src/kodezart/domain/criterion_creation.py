"""Exact native criterion creation identity and its initial empty evidence."""

from collections.abc import Sequence
from typing import Literal

from kodezart.domain.errors import CriterionReadError, InvalidFireCriterionError
from kodezart.domain.fire_spec import (
    checklist_items,
    criterion_check,
    criterion_field_bodies,
)
from kodezart.types.domain.tracker import TrackerIssue, is_non_counting


def criterion_body(*, parent_key: str, check: str, do: str) -> str:
    if not check.strip() or not do.strip():
        raise CriterionReadError(
            issue_key=parent_key, reason="Check and Do must be nonempty"
        )
    body = f"**Check:** {check}\n\n**Do:** {do}\n\n**Evidence:**\n"
    fields: tuple[tuple[Literal["Check", "Do", "Evidence"], str], ...] = (
        ("Check", check.strip()),
        ("Do", do.strip()),
        ("Evidence", ""),
    )
    if any(
        criterion_field_bodies(body, field=field) != (expected,)
        for field, expected in fields
    ):
        raise CriterionReadError(
            issue_key=parent_key, reason="criterion fields must remain distinct"
        )
    return body


def criteria_owed(*, body: str, children: Sequence[TrackerIssue]) -> bool:
    """Whether the criteria stage is owed under a parent with *body* and *children*.

    Owed while no child is a counting criterion, or while some checklist item
    of the body is stated by no child's Check, compared with outer whitespace
    stripped, whatever that child's state: a criterion a person Canceled or
    closed as a Duplicate still covers the item its Check states, so the item
    is not minted again.  A child whose one Check cannot be read states
    nothing here.
    """
    if all(is_non_counting(child.state_kind) for child in children):
        return True
    stated: set[str] = set()
    for child in children:
        checks = criterion_field_bodies(child.body, field="Check")
        if len(checks) == 1 and checks[0]:
            stated.add(checks[0].strip())
    return any(item not in stated for item in checklist_items(body))


def existing_criterion(
    *, parent_key: str, check: str, children: Sequence[TrackerIssue]
) -> TrackerIssue | None:
    """The one child whose Check is *check*, stripped, or None.

    A child that counts and whose one Check cannot be read refuses; one the
    board Canceled or closed as a Duplicate is skipped instead, because it
    refuses nothing (KOD-794).  A non-counting child with a legible Check
    still matches, so an item a person canceled is not minted again.
    """
    keys: set[str] = set()
    matches: list[TrackerIssue] = []
    for child in children:
        if (
            child.issue_key in keys
            or child.parent_key != parent_key
            or "criterion" not in child.issue_labels
        ):
            raise CriterionReadError(
                issue_key=parent_key,
                reason="criterion identity, parent or classification differs",
            )
        keys.add(child.issue_key)
        try:
            stated = criterion_check(criterion=child, issue_key=parent_key)
        except InvalidFireCriterionError:
            if is_non_counting(child.state_kind):
                continue
            raise
        if stated == check.strip():
            matches.append(child)
    if len(matches) > 1:
        raise CriterionReadError(
            issue_key=parent_key, reason="duplicate current Check identities"
        )
    return matches[0] if matches else None
