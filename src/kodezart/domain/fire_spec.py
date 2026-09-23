"""Capture a tracker subject and its own criterion identities without I/O."""

import re
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Literal

from kodezart.domain.errors import (
    EmptyFireCriteriaError,
    FireSpecEntryError,
    InvalidFireCriterionError,
)
from kodezart.types.domain.fire_spec import CriterionRef, IssueRef, TrackerSpec
from kodezart.types.domain.operation import OperationMemberAbsentError
from kodezart.types.domain.tracker import TrackerIssue, is_non_counting

_CRITERION_ROW = re.compile(r"^ {0,3}\*\*(Check|Do|Evidence|Class):\*\*(.*)$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST_ITEM = re.compile(r"^ {0,3}(?:[-*+]|\d+[.)])\s+(.*)$")
_CHECKLIST_ITEM = re.compile(r"^[ \t]*[-*+][ \t]+\[[ xX]\][ \t]+(.*\S)[ \t]*$")
CriterionField = Literal["Check", "Do", "Evidence", "Class"]

#: The heading whose section states what the subject's own text commits to
#: building. Matched on its exact text, so a section that states something
#: else is a different section.
DELIVERABLES_SECTION = "Deliverables"


def body_digest(body: str) -> str:
    """The sha256 hex of an issue body, as every reader of one pins it.

    The one place in the source that turns a body into a digest: a lane's
    record pins the subject it entered on, a revision stamps the body it
    read, and an organize snapshot carries the body it judged. One rule, so
    two readings of the same bytes cannot disagree by construction.

    Arithmetic over the bytes that were read: two processes that read the
    same body agree on it without either of them asking anything.
    """
    return sha256(body.encode("utf-8")).hexdigest()


def checklist_items(body: str) -> tuple[str, ...]:
    """The items of the Markdown task list a person wrote in *body*, in order.

    An item is a line holding a ``-``, ``*`` or ``+`` marker, then a ``[ ]``,
    ``[x]`` or ``[X]`` tick box, then text, at any indentation.  Each item is
    its text alone, stripped, without its marker or tick box: exactly what a
    criterion adopting it states as its Check.  No other line is an item.

    This reader's own fence and HTML-comment rules decide what is visible, as
    they do for the template rows: a task-list line inside a fenced block or
    behind a comment is an example or a note, not an item, and a comment on
    an item's line is no part of its text.
    """
    items: list[str] = []
    fence: tuple[str, int] | None = None
    comment = False
    for original in body.splitlines():
        delimiter = _FENCE.match(original)
        if fence is not None:
            if (
                delimiter is not None
                and delimiter[1][0] == fence[0]
                and len(delimiter[1]) >= fence[1]
                and not delimiter[2].strip()
            ):
                fence = None
            continue
        line, comment = _without_comments(original, comment=comment)
        delimiter = _FENCE.match(line)
        if delimiter is not None:
            fence = delimiter[1][0], len(delimiter[1])
            continue
        match = _CHECKLIST_ITEM.match(line)
        if match is not None:
            items.append(match[1].strip())
    return tuple(items)


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


def deliverables_section(body: str) -> tuple[str, ...]:
    """Every item the subject's own ``Deliverables`` section names, in body order.

    This reader's own fence and HTML-comment rules decide what is visible, so
    a heading inside a fenced block or behind a comment is no heading and a
    list item inside one is no item. That is why the section is read here
    rather than in a module of its own: those two rules are this module's, and
    a second statement of the body grammar would be free to disagree with it.

    The section runs from a visible heading whose text is exactly
    ``Deliverables`` to the next visible heading of any level, or the end of
    the body. Items are the visible list items inside it, each stripped; prose
    lines inside it are not items.

    A body with no such section names nothing, and that is the same answer as
    a section with no items: ``()``. One rule, no special case — nothing is
    stated, so an answer that names a deliverable exceeds what is stated. Two
    such headings contribute both their item lists: nothing here edits the
    body, so an ambiguous section is not a row that has to be addressed.
    """
    items: list[str] = []
    active = False
    fence: tuple[str, int] | None = None
    comment = False
    for original in body.splitlines():
        delimiter = _FENCE.match(original)
        if fence is not None:
            if (
                delimiter is not None
                and delimiter[1][0] == fence[0]
                and len(delimiter[1]) >= fence[1]
                and not delimiter[2].strip()
            ):
                fence = None
            continue
        line, comment = _without_comments(original, comment=comment)
        delimiter = _FENCE.match(line)
        if delimiter is not None:
            fence = delimiter[1][0], len(delimiter[1])
            continue
        heading = _HEADING.match(line)
        if heading is not None:
            active = heading[2].strip() == DELIVERABLES_SECTION
            continue
        item = _LIST_ITEM.match(line) if active else None
        if item is not None and item[1].strip():
            items.append(item[1].strip())
    return tuple(items)


def _criterion_rows(body: str) -> tuple[tuple[str, int], ...]:
    """Every visible template row in *body*, as its label and where it begins.

    The reader's own fence and comment rules decide what is visible, so a
    row label inside a fenced block or behind an HTML comment is no row.
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
    return tuple(rows)


def _duplicated(names: Sequence[str]) -> tuple[str, ...]:
    """The labels *names* holds more than once, over rows already read."""
    return tuple(sorted({name for name in names if names.count(name) > 1}))


def duplicated_row_labels(body: str) -> tuple[str, ...]:
    """The template row labels *body* carries more than one row for.

    One rule with two readers. The edit below addresses a field by its
    single row and cannot say which of two it means; a writer that is about
    to make that edit asks the same question first, so such a body refuses
    before the write instead of raising out of this codec once the work it
    would record has already been done. The edit itself has the rows in
    hand and asks the inner form, so one body is never scanned twice.
    """
    return _duplicated([name for name, _ in _criterion_rows(body)])


def replace_criterion_fields(
    body: str, *, replacements: Mapping[CriterionField, str]
) -> str:
    """Replace addressed visible fields while preserving all other body bytes.

    The same fence/comment rules as the reader identify rows. Ambiguous rows
    refuse; absent fields are appended. The caller archives the original body
    before retiring evidence, and no authored criterion artifact uses this edit.
    """
    rows = _criterion_rows(body)
    names = [name for name, _ in rows]
    if _duplicated(names):
        raise ValueError("criterion amendment requires unambiguous template rows")
    result = body
    for index in range(len(rows) - 1, -1, -1):
        name, begin = rows[index]
        end = rows[index + 1][1] if index + 1 < len(rows) else len(body)
        for field, replacement in replacements.items():
            if field == name:
                result = (
                    result[:begin] + f"**{field}:** {replacement}\n\n" + result[end:]
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
    """Capture the subject version; a successful empty query is unfireable.

    Three steps, in this order. The non-counting criteria are dropped first:
    one the board Canceled or closed as a Duplicate neither joins the
    specification nor refuses this read (KOD-794). Emptiness is judged over
    what is left, so a subtree whose every criterion was abandoned is refused
    as empty (KOD-710/KOD-398) rather than composed. Only then is each
    remaining criterion's Check validated, so a counting criterion carrying no
    single nonempty Check still refuses here and never deeper.
    """
    counting = [
        criterion for criterion in criteria if not is_non_counting(criterion.state_kind)
    ]
    if not counting:
        raise EmptyFireCriteriaError(issue_key=subject.issue_key)
    for criterion in counting:
        criterion_check(criterion=criterion, issue_key=subject.issue_key)
    return TrackerSpec(
        subject=IssueRef(subject.issue_key),
        body=subject.body,
        criteria=tuple(criterion_ref(criterion.issue_key) for criterion in counting),
        read_at_version=subject.updated_at.isoformat(),
    )


def criterion_ref(key: str) -> CriterionRef:
    """Address one native criterion by the key the tracker reports for it.

    The only place the source mints this identity. Identity and provenance
    are the same value on this arm — the criterion sub-issue's own key — so
    every later reading of one criterion addresses it through here rather
    than minting a second identity that could disagree.
    """
    return CriterionRef(key)


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
