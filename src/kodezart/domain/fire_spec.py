"""Capture a tracker subject and its own criterion identities without I/O."""

import re
from collections.abc import Sequence

from kodezart.domain.errors import EmptyFireCriteriaError, InvalidFireCriterionError
from kodezart.types.domain.fire_spec import CriterionRef, IssueRef, TrackerSpec
from kodezart.types.domain.tracker import TrackerIssue

_CRITERION_ROW = re.compile(r"^ {0,3}\*\*(Check|Do|Evidence|Class):\*\*(.*)$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _check_bodies(body: str) -> tuple[str, ...]:
    """Read the template's Check field, excluding quoted code and HTML comments."""
    checks: list[str] = []
    lines: list[str] = []
    active = False
    fence: tuple[str, int] | None = None
    comment = False
    for line in body.splitlines():
        if comment:
            comment = "-->" not in line
            continue
        if fence is None and line.lstrip().startswith("<!--"):
            comment = "-->" not in line
            continue
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
        if delimiter is not None:
            fence = delimiter[1][0], len(delimiter[1])
            if active:
                lines.append(line)
            continue
        row = _CRITERION_ROW.match(line)
        if row is not None:
            if active:
                checks.append("\n".join(lines).strip())
            active = row[1] == "Check"
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
        checks = _check_bodies(criterion.body)
        if len(checks) != 1 or not checks[0]:
            raise InvalidFireCriterionError(
                issue_key=subject.issue_key,
                criterion_key=criterion.issue_key,
                reason="one nonempty Check field is required",
            )
    return TrackerSpec(
        subject=IssueRef(subject.issue_key),
        body=subject.body,
        criteria=tuple(CriterionRef(criterion.issue_key) for criterion in criteria),
        read_at_version=subject.updated_at.isoformat(),
    )
