"""Capture a tracker subject and its own criterion identities without I/O."""

import re
from collections.abc import Mapping, Sequence
from typing import Literal

from kodezart.domain.errors import (
    EmptyFireCriteriaError,
    FireSpecEntryError,
    InvalidFireCriterionError,
)
from kodezart.types.domain.fire_spec import CriterionRef, IssueRef, TrackerSpec
from kodezart.types.domain.operation import OperationMemberAbsentError
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


def criterion_field_bodies(
    body: str, *, field: CriterionField
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


def replace_criterion_fields(
    body: str, *, replacements: Mapping[CriterionField, str]
) -> str:
    """Replace addressed visible fields while preserving all other body bytes.

    The same fence/comment rules as the reader identify rows. Ambiguous rows
    refuse; absent fields are appended. The caller archives the original body
    before retiring evidence, and no authored criterion artifact uses this edit.
    """
    rows: list[tuple[str, int]] = []
    fence: tuple[str, int] | None = None
    comment = False
    offset = 0
    for original in body.splitlines(keepends=True):
        line = original.rstrip("\r\n")
        delimiter = _FENCE.match(line)
        if fence is not None:
            if (
                delimiter is not None
                and delimiter[1][0] == fence[0]
                and len(delimiter[1]) >= fence[1]
                and not delimiter[2].strip()
            ):
                fence = None
        else:
            line, comment = _without_comments(line, comment=comment)
            delimiter = _FENCE.match(line)
            if delimiter is not None:
                fence = delimiter[1][0], len(delimiter[1])
            else:
                row = _CRITERION_ROW.match(line)
                if row is not None:
                    rows.append((row[1], offset))
        offset += len(original)
    names = [name for name, _ in rows]
    if len(names) != len(set(names)):
        raise ValueError("criterion amendment requires unambiguous template rows")
    result = body
    for index in range(len(rows) - 1, -1, -1):
        name, begin = rows[index]
        end = rows[index + 1][1] if index + 1 < len(rows) else len(body)
        for field, replacement in replacements.items():
            if field == name:
                result = (
                    result[:begin]
                    + f"**{field}:** {replacement}\n\n"
                    + result[end:]
                )
    for field, replacement in replacements.items():
        if field not in names:
            result = result.rstrip("\n") + f"\n\n**{field}:** {replacement}\n"
        if criterion_field_bodies(result, field=field) != (replacement.strip(),):
            raise ValueError("amended criterion fields must remain distinct")
    return result


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


def require_fire_entry(
    *, subject: TrackerIssue, approved: bool, criteria_stage_label_key: str | None
) -> None:
    """Require the configured phase completion and the live approval fact."""
    if criteria_stage_label_key is None:
        raise OperationMemberAbsentError(
            missing="organize_mandates criteria terminal_marker_key",
            stops="cannot establish criteria-stage completion at fire entry",
        )
    if criteria_stage_label_key not in subject.issue_labels:
        raise FireSpecEntryError(
            issue_key=subject.issue_key, reason="criteria-stage completion is absent"
        )
    if not approved:
        raise FireSpecEntryError(
            issue_key=subject.issue_key, reason="execution approval is absent"
        )
