"""Exact native criterion creation identity and its initial unfilled evidence."""

from collections.abc import Sequence
from typing import Literal

from kodezart.domain.criterion_evidence import unfilled_evidence_body
from kodezart.domain.errors import CriterionReadError
from kodezart.domain.fire_spec import criterion_check, criterion_field_bodies
from kodezart.types.domain.tracker import TrackerIssue


def criterion_body(*, parent_key: str, check: str, do: str, demonstration: str) -> str:
    """The body a new criterion is created with, demonstration included.

    The Evidence row stays unfilled — nothing has graded this criterion —
    but it names what will grade it, so a reader of the child alone can see
    what it is to be judged by.
    """
    if not check.strip() or not do.strip() or not demonstration.strip():
        raise CriterionReadError(
            issue_key=parent_key,
            reason="Check, Do and the demonstration must be nonempty",
        )
    evidence = unfilled_evidence_body(demonstration=demonstration.strip())
    body = f"**Check:** {check}\n\n**Do:** {do}\n\n**Evidence:**\n{evidence}"
    fields: tuple[tuple[Literal["Check", "Do", "Evidence"], str], ...] = (
        ("Check", check.strip()),
        ("Do", do.strip()),
        ("Evidence", evidence),
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
