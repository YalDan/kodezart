"""The parity checklist is checked, not merely written.

A checklist whose rows nobody verifies is a claim; these assertions make it
a gate.  Every cited test must exist under the name it is cited by, every
evidence cell must be one of the three declared states, and the stated
cutover status — the verdict and both row counts — must be derivable from
the rows.  So an obligation cannot leave the gate by an edit to the sentence
that states it, nor by being re-pointed to *not ported* in silence.

Completeness is measured against the checklist itself.  KOD-60's body says
the KOD-50 checklist IS the definition of parity, so what belongs on it is
a question for that document and its readers, not for a constant
transcribed into this module.
"""

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKLIST = REPO_ROOT / "docs" / "parity_checklist.md"
CUTOVER_MAP = REPO_ROOT / "docs" / "cutover_mapping.md"

#: The literal an open row carries.  Exact, so "partially demonstrated" or
#: any other softening is a parse failure rather than a passing row.
OPEN = "not yet demonstrated"

#: R1's marker for a clause deliberately not ported.  A third state, and the
#: only one that is neither evidence nor a blocker: a dropped clause with a
#: recorded reason is an accepted loss a reader can audit.
NOT_PORTED = "not ported, because "

BLOCKED = "BLOCKED"
CLEAR = "CLEAR"

#: The stated cutover status: a verdict and the two counts behind it.  The
#: counts are part of the status because the verdict alone cannot see a
#: dropped clause — re-pointing a row to NOT_PORTED shortens the blocking
#: set, and with a verdict-only status nothing moved when it did.
STATUS = re.compile(
    rf"\*\*(?P<verdict>{BLOCKED}|{CLEAR})\*\* — "
    rf"`{OPEN}`: (?P<open>\d+) · `not ported`: (?P<dropped>\d+)",
)

WHY_SECTION = "## Why the undemonstrated rows are undemonstrated"


def _checklist() -> str:
    return CHECKLIST.read_text(encoding="utf-8")


def _rows(text: str) -> list[tuple[str, str, str, str]]:
    """``(obligation, source, behavior, evidence)`` for every table row."""
    rows: list[tuple[str, str, str, str]] = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4 or cells[0] in {"Obligation", "---"}:
            continue
        if set(cells[0]) <= {"-", " "}:
            continue
        rows.append((cells[0], cells[1], cells[2], cells[3]))
    return rows


def _cited_tests(text: str) -> list[str]:
    return [
        evidence
        for *_, evidence in _rows(text)
        if evidence != OPEN and not evidence.startswith(NOT_PORTED)
    ]


def _open_obligations(text: str) -> list[str]:
    return [obligation for obligation, *_, evidence in _rows(text) if evidence == OPEN]


def _dropped_obligations(text: str) -> list[str]:
    """The rows re-pointed to ``not ported`` — obligations off the gate."""
    return [
        obligation
        for obligation, *_, evidence in _rows(text)
        if evidence.startswith(NOT_PORTED)
    ]


def _derived_status(text: str) -> tuple[str, int, int]:
    """The status the rows themselves say: verdict, open count, dropped count."""
    open_rows = _open_obligations(text)
    return (
        BLOCKED if open_rows else CLEAR,
        len(open_rows),
        len(_dropped_obligations(text)),
    )


def _stated_status(text: str) -> tuple[str, int, int]:
    """The status the document asserts in the line a reader sees."""
    stated = STATUS.search(text)
    assert stated is not None, "no parseable cutover status line"
    return (stated["verdict"], int(stated["open"]), int(stated["dropped"]))


def _mapped_dimensions() -> set[str]:
    """The dimensions the cutover map traces to a template section."""
    section = CUTOVER_MAP.read_text(encoding="utf-8").split(
        "## Behavior-parity dimensions",
    )[1]
    dimensions: set[str] = set()
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3 or cells[0] in {"Dimension", "---"}:
            continue
        if set(cells[0]) <= {"-", " "}:
            continue
        dimensions.add(cells[0])
    return dimensions


def test_the_checklist_has_rows_at_all() -> None:
    """Guards every assertion below: an unparsed table would pass them all."""
    rows = _rows(_checklist())

    assert len(rows) > 5
    assert all(obligation and behavior for obligation, _, behavior, _ in rows)


def test_every_dimension_the_cutover_map_traces_is_on_the_checklist() -> None:
    """Traceability: a dimension the map traces must be on the checklist."""
    named = {obligation for obligation, *_ in _rows(_checklist())}

    assert _mapped_dimensions() <= named, _mapped_dimensions() - named


def _defined_names(module: Path) -> set[str]:
    """Every ``test_x`` and ``Class::test_x`` the module actually defines.

    Built from the syntax tree rather than by searching for the leaf name,
    because a leaf search resolves a citation whose CLASS does not exist —
    which is how a citation naming ``TestClaims`` read as evidence while
    the class was called something else.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.update(
                f"{node.name}::{child.name}"
                for child in node.body
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
            )
    return names


def test_every_cited_test_exists_under_the_name_it_is_cited_by() -> None:
    """A citation pointing at nothing is worse than an open row.

    The whole ``::`` path is resolved — class included — so a citation is
    evidence only when the thing it names can be run under that name.
    """
    for citation in _cited_tests(_checklist()):
        assert citation.startswith("`") and citation.endswith("`"), citation
        path_part, _, name = citation.strip("`").partition("::")
        module = REPO_ROOT / path_part
        assert module.is_file(), citation
        assert name in _defined_names(module), citation


def test_every_evidence_cell_is_a_citation_or_one_of_the_two_open_literals() -> None:
    """Three states, all explicit: demonstrated, openly not, or not ported."""
    for obligation, _, _, evidence in _rows(_checklist()):
        assert (
            evidence == OPEN
            or evidence.startswith(NOT_PORTED)
            or evidence.startswith("`")
        ), (obligation, evidence)


def test_a_not_ported_row_carries_a_reason() -> None:
    """R1's marker is *not ported, because —*; the reason is the whole point."""
    for obligation, _, _, evidence in _rows(_checklist()):
        if not evidence.startswith(NOT_PORTED):
            continue
        assert evidence.removeprefix(NOT_PORTED).strip(), obligation


def test_the_cutover_status_is_derived_from_the_rows_not_asserted() -> None:
    """AC-21: cutover must not be performed while any row is open.

    The verdict still turns on the OPEN rows alone — a ``not ported`` row is
    a decision already taken with its ground recorded, and blocking on it
    forever would make the gate unliftable for a reason nobody could act on.
    Both counts are stated beside it, so a row leaving the blocking set by
    being dropped moves the status as visibly as one leaving by being
    demonstrated.
    """
    text = _checklist()

    assert _stated_status(text) == _derived_status(text), (
        _stated_status(text),
        _derived_status(text),
        _open_obligations(text),
        _dropped_obligations(text),
    )


def test_a_not_ported_row_cannot_be_added_without_the_status_moving() -> None:
    """The escape hatch, closed: dropping a clause is visible in the status.

    Re-pointing a row to ``not ported`` removes an obligation from the gate
    without demonstrating anything.  A status derived from the OPEN rows
    alone cannot see that, so this adds a dropped row to a copy of the
    document — which leaves the open count and the verdict untouched — and
    requires the stated status to disagree with the rows.
    """
    text = _checklist()
    smuggled = text + (
        "\n| smuggled obligation | — | An obligation lifted off the gate. |"
        f" {NOT_PORTED}a row said so |\n"
    )

    assert _open_obligations(smuggled) == _open_obligations(text)
    assert _derived_status(smuggled) != _derived_status(text)
    assert _stated_status(smuggled) != _derived_status(smuggled)


def test_an_open_row_names_why_it_is_open() -> None:
    """An open row with no account of itself is a row nobody will ever close."""
    if not _open_obligations(_checklist()):
        return
    assert "Why the undemonstrated rows are undemonstrated" in _checklist()


def _why_section() -> str:
    """The prose that accounts for the open rows, and nothing else."""
    body = _checklist()
    rest = body[body.index(WHY_SECTION) + len(WHY_SECTION) :]
    end = rest.find("\n## ")
    return (rest if end == -1 else rest[:end]).lower()


def test_every_obligation_the_prose_calls_open_is_an_open_row() -> None:
    """The document may not disagree with itself where nothing can see it.

    It did: the prose called a row open while the table gave that row a
    citation, and no assertion read the prose at all.  This derives one from
    the other, so a row that moves and a paragraph that does not are the
    same failure.
    """
    prose = _why_section()
    contradicted = [
        obligation
        for obligation, _, _, evidence in _rows(_checklist())
        if obligation.lower() in prose and evidence != OPEN
    ]

    assert contradicted == []
