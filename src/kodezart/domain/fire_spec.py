"""Capture a tracker subject and its own criterion identities without I/O."""

import re
from collections.abc import Sequence
from typing import Literal

from kodezart.domain.errors import EmptyFireCriteriaError, InvalidFireCriterionError
from kodezart.types.domain.fire_spec import CriterionRef, IssueRef, TrackerSpec
from kodezart.types.domain.tracker import TrackerIssue

_CRITERION_ROW = re.compile(r"^ {0,3}\*\*(Check|Do|Evidence|Class):\*\*(.*)$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _without_comments(line: str, *, comment: bool) -> tuple[str, bool]:
    """Keep visible text around inline or continued HTML comments."""
    parts: list[str] = []
    cursor = 0
    while cursor < len(line):
        if comment:
            closing = line.find("-->", cursor)
            if closing < 0:
                return "".join(parts), True
            cursor = closing + len("-->")
            comment = False
        else:
            opening = line.find("<!--", cursor)
            if opening < 0:
                parts.append(line[cursor:])
                break
            parts.append(line[cursor:opening])
            cursor = opening + len("<!--")
            comment = True
    return "".join(parts), comment


def criterion_field_bodies(
    body: str, *, field: Literal["Check", "Do", "Evidence", "Class"]
) -> tuple[str, ...]:
    """Read one template field, excluding quoted row labels and HTML comments."""
    checks: list[str] = []
    lines: list[str] = []
    active = False
    fence: tuple[str, int] | None = None
    comment = False
    for line in body.splitlines():
        delimiter = _FENCE.match(line)
        if fence is not None:
            if active:
                lines.append(line)
            if (
                delimiter is not None
                and delimiter[1][0] == fence[0]
                and len(delimiter[1]) >= fence[1]
                and not delimiter[2].strip()
            ):
                fence = None
            continue
        line, comment = _without_comments(line, comment=comment)
        delimiter = _FENCE.match(line)
        if delimiter is not None:
            fence = delimiter[1][0], len(delimiter[1])
            if active:
                lines.append(line)
            continue
        row = _CRITERION_ROW.match(line)
        if row is not None:
            if active:
                checks.append("\n".join(lines).strip())
            active = row[1] == field
            lines = [row[2]] if active else []
        elif active:
            lines.append(line)
    if active:
        checks.append("\n".join(lines).strip())
    return tuple(checks)


def tracker_spec_from_issues(
    *, subject: TrackerIssue, criteria: Sequence[TrackerIssue]
) -> TrackerSpec:
    """Capture the subject version; a successful empty query is unfireable."""
    if not criteria:
        raise EmptyFireCriteriaError(issue_key=subject.issue_key)
    for criterion in criteria:
        criterion_check(criterion=criterion, issue_key=subject.issue_key)
    return TrackerSpec(
        subject=IssueRef(subject.issue_key),
        body=subject.body,
        criteria=tuple(CriterionRef(criterion.issue_key) for criterion in criteria),
        read_at_version=subject.updated_at.isoformat(),
    )


def criterion_check(*, criterion: TrackerIssue, issue_key: str) -> str:
    """Return only the one current Check, excluding the recorded Evidence."""
    checks = criterion_field_bodies(criterion.body, field="Check")
    if len(checks) != 1 or not checks[0]:
        raise InvalidFireCriterionError(
            issue_key=issue_key,
            criterion_key=criterion.issue_key,
            reason="one nonempty Check field is required",
        )
    return checks[0]
