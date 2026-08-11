"""The parity checklist is checked, not merely written.

A checklist whose rows nobody verifies is a claim; these assertions make it
a gate.  Every cited test must exist under the name it is cited by, every
evidence cell must be one of the three declared states, and the stated
cutover status must be derivable from the rows — so the gate cannot be
lifted by editing the sentence that states it.

Completeness is measured against the checklist itself.  KOD-60's body says
the KOD-50 checklist IS the definition of parity, so what belongs on it is
a question for that document and its readers, not for a constant
transcribed into this module.
"""

import ast
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


WHY_SECTION = "## Why the undemonstrated rows are undemonstrated"


def _rows() -> list[tuple[str, str, str, str]]:
    """``(obligation, source, behavior, evidence)`` for every table row."""
    rows: list[tuple[str, str, str, str]] = []
    for line in CHECKLIST.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4 or cells[0] in {"Obligation", "---"}:
            continue
        if set(cells[0]) <= {"-", " "}:
            continue
        rows.append((cells[0], cells[1], cells[2], cells[3]))
    return rows


def _cited_tests() -> list[str]:
    return [
        evidence
        for *_, evidence in _rows()
        if evidence != OPEN and not evidence.startswith(NOT_PORTED)
    ]


def _open_obligations() -> list[str]:
    return [obligation for obligation, *_, evidence in _rows() if evidence == OPEN]


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
    rows = _rows()

    assert len(rows) > 5
    assert all(obligation and behavior for obligation, _, behavior, _ in rows)


def test_every_dimension_the_cutover_map_traces_is_on_the_checklist() -> None:
    """Traceability: a dimension the map traces must be on the checklist."""
    named = {obligation for obligation, *_ in _rows()}

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
    for citation in _cited_tests():
        assert citation.startswith("`") and citation.endswith("`"), citation
        path_part, _, name = citation.strip("`").partition("::")
        module = REPO_ROOT / path_part
        assert module.is_file(), citation
        assert name in _defined_names(module), citation


def test_every_evidence_cell_is_a_citation_or_one_of_the_two_open_literals() -> None:
    """Three states, all explicit: demonstrated, openly not, or not ported."""
    for obligation, _, _, evidence in _rows():
        assert (
            evidence == OPEN
            or evidence.startswith(NOT_PORTED)
            or evidence.startswith("`")
        ), (obligation, evidence)


def test_a_not_ported_row_carries_a_reason() -> None:
    """R1's marker is *not ported, because —*; the reason is the whole point."""
    for obligation, _, _, evidence in _rows():
        if not evidence.startswith(NOT_PORTED):
            continue
        assert evidence.removeprefix(NOT_PORTED).strip(), obligation


def test_the_cutover_status_is_derived_from_the_rows_not_asserted() -> None:
    """AC-21: cutover must not be performed while any row is open.

    Derived from the OPEN rows alone.  A ``not ported`` row is a decision
    already taken with its ground recorded, and blocking on it forever would
    make the gate unliftable for a reason nobody could act on.
    """
    text = CHECKLIST.read_text(encoding="utf-8")
    stated = BLOCKED if f"**{BLOCKED}.**" in text else CLEAR
    expected = BLOCKED if _open_obligations() else CLEAR

    assert stated == expected, (stated, _open_obligations())


def test_an_open_row_names_why_it_is_open() -> None:
    """An open row with no account of itself is a row nobody will ever close."""
    if not _open_obligations():
        return
    assert "Why the undemonstrated rows are undemonstrated" in CHECKLIST.read_text(
        encoding="utf-8",
    )


def _why_section() -> str:
    """The prose that accounts for the open rows, and nothing else."""
    body = CHECKLIST.read_text(encoding="utf-8")
    rest = body[body.index(WHY_SECTION) + len(WHY_SECTION) :]
    end = rest.find("\n## ")
    return (rest if end == -1 else rest[:end]).lower()


def test_every_obligation_the_prose_calls_open_is_an_open_row() -> None:
    """The document may not disagree with itself where nothing can see it.

    It did: the prose said the pre-promotion hygiene row stayed open while
    the table gave that row a citation, and no assertion read the prose at
    all.  This derives one from the other, so a row that moves and a
    paragraph that does not are the same failure.
    """
    prose = _why_section()
    contradicted = [
        obligation
        for obligation, _, _, evidence in _rows()
        if obligation.lower() in prose and evidence != OPEN
    ]

    assert contradicted == []
