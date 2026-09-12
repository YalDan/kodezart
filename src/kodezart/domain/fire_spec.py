"""Read exact native criterion template fields without I/O."""

import re
from typing import Literal

from kodezart.domain.errors import InvalidFireCriterionError
from kodezart.types.domain.tracker import TrackerIssue

_CRITERION_ROW = re.compile(r"^ {0,3}\*\*(Check|Do|Evidence|Class):\*\*(.*)$")


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


CriterionField = Literal["Check", "Do", "Evidence", "Class"]


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


def criterion_field_bodies(body: str, *, field: CriterionField) -> tuple[str, ...]:
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
