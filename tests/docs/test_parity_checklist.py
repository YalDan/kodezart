"""The parity checklist is checked, not merely written.

A checklist whose rows nobody verifies is a claim; these assertions make it
a gate.  Every cited test must exist under the name it is cited by, every
dimension the cutover map names must appear, and the stated cutover status
must be derivable from the rows — so the gate cannot be lifted by editing
the sentence that states it.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKLIST = REPO_ROOT / "docs" / "parity_checklist.md"
CUTOVER_MAP = REPO_ROOT / "docs" / "cutover_mapping.md"

#: The literal an open row carries.  Exact, so "partially demonstrated" or
#: any other softening is a parse failure rather than a passing row.
OPEN = "not yet demonstrated"

BLOCKED = "BLOCKED"
CLEAR = "CLEAR"


def _rows() -> list[tuple[str, str, str]]:
    """``(dimension, behavior, evidence)`` for every checklist table row."""
    rows: list[tuple[str, str, str]] = []
    for line in CHECKLIST.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3 or cells[0] in {"Dimension", "---"}:
            continue
        if set(cells[0]) <= {"-", " "}:
            continue
        rows.append((cells[0], cells[1], cells[2]))
    return rows


def _cited_tests() -> list[str]:
    return [evidence for _, _, evidence in _rows() if evidence != OPEN]


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
    assert all(dimension and behavior for dimension, behavior, _ in rows)


def test_every_dimension_the_cutover_map_traces_is_on_the_checklist() -> None:
    """AC-21: the map says where a behavior lives; this says whether it holds."""
    named = {dimension for dimension, _, _ in _rows()}

    assert _mapped_dimensions() <= named, _mapped_dimensions() - named


def test_every_cited_test_exists_under_the_name_it_is_cited_by() -> None:
    """A citation pointing at nothing is worse than an open row."""
    for citation in _cited_tests():
        assert citation.startswith("`") and citation.endswith("`"), citation
        path_part, _, name = citation.strip("`").partition("::")
        module = REPO_ROOT / path_part
        assert module.is_file(), citation
        leaf = name.rsplit("::", maxsplit=1)[-1]
        assert re.search(rf"def {re.escape(leaf)}\b", module.read_text("utf-8")), (
            citation
        )


def test_every_evidence_cell_is_a_citation_or_the_exact_open_literal() -> None:
    """No third state: a row is demonstrated or it is openly not."""
    for dimension, _, evidence in _rows():
        assert evidence == OPEN or evidence.startswith("`"), (dimension, evidence)


def test_the_cutover_status_is_derived_from_the_rows_not_asserted() -> None:
    """AC-21: cutover must not be performed while any row is open."""
    text = CHECKLIST.read_text(encoding="utf-8")
    stated = BLOCKED if f"**{BLOCKED}.**" in text else CLEAR
    open_rows = [dimension for dimension, _, evidence in _rows() if evidence == OPEN]
    expected = BLOCKED if open_rows else CLEAR

    assert stated == expected, (stated, open_rows)


def test_an_open_row_names_why_it_is_open() -> None:
    """An open row with no account of itself is a row nobody will ever close."""
    if not [dimension for dimension, _, evidence in _rows() if evidence == OPEN]:
        return
    assert "Why the undemonstrated rows are undemonstrated" in CHECKLIST.read_text(
        encoding="utf-8",
    )
