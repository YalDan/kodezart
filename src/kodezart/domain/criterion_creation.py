"""Exact native criterion creation identity and its initial empty evidence."""

from collections.abc import Sequence
from typing import Literal

from kodezart.domain.errors import CriterionReadError
from kodezart.domain.fire_spec import criterion_check, criterion_field_bodies
from kodezart.types.domain.tracker import TrackerIssue


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


def existing_criterion(
    *, parent_key: str, check: str, children: Sequence[TrackerIssue]
) -> TrackerIssue | None:
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
        if criterion_check(criterion=child, issue_key=parent_key) == check.strip():
            matches.append(child)
    if len(matches) > 1:
        raise CriterionReadError(
            issue_key=parent_key, reason="duplicate current Check identities"
        )
    return matches[0] if matches else None
