"""The parity checklist is checked, not merely written.

A checklist whose rows nobody verifies is a claim; these assertions make it
a gate.  Every cited test must exist under the name it is cited by, every
obligation the ruling floors must appear, and the stated cutover status must
be derivable from the rows — so the gate cannot be lifted by editing the
sentence that states it.

**What completeness is measured against, and why it changed.**  It used to
be measured against ``docs/cutover_mapping.md``'s six-row traceability table,
which KOD-60 R1 excludes in as many words: *"Six rows is a traceability
artifact; it is not a behavior-parity checklist."*  Anchored there, the guard
passed over a thirteen-row checklist while at least a dozen floored
obligations had no row at all — a guard positioned so it could not match the
defect it names.  KOD-60 R6 re-anchors it on R1's floor, transcribed below.
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

#: Every obligation KOD-60 R1 floors, as the checklist names it.
#:
#: This is a TRANSCRIPTION of a ruling that lives on the tracker, because CI
#: cannot read a tracker comment.  Stated plainly rather than oversold: it
#: catches a row deleted from the checklist and an obligation that never got
#: one, and it cannot catch an obligation missing from this constant.  That
#: residue is real; it is still strictly better than measuring completeness
#: against a table the ruling excludes.
R1_FLOOR: frozenset[str] = frozenset(
    {
        # Fire-prep
        "scan-window checkpointing",
        "checkpoint write ordering",
        "bootstrap window",
        "bootstrap one-time sweep",
        "three-stream work set",
        "per-issue comment pulls",
        "reviews as an object class",
        "response-set test",
        "bundle-first grouping",
        "four shape decisions",
        "frontier rule",
        "fire-body format",
        "pre-promotion hygiene",
        "queue-state transitions",
        "approval boundary",
        "reply criteria",
        "five reply-routing rules",
        "run digest",
        "exit-silently condition",
        # Grooming
        "build for real",
        "gate-vs-cascade",
        "sandbox-vs-project",
        "stack-head grounding",
        "commit-PR-issue reconciliation",
        "terminal done label",
        "graph and supersession hygiene",
        "mention scan window",
        "deadline flagging",
        "status-update cadence",
        "health mapping",
    },
)


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


def test_every_obligation_the_ruling_floors_has_a_row() -> None:
    """AC-21: the completeness half, anchored on R1 rather than on the map.

    R1 says of its floor that *a checklist missing any of them is incomplete
    on its face*.  Thirteen rows against it was incomplete; measuring against
    the six-dimension traceability table could not see that.
    """
    named = {obligation for obligation, *_ in _rows()}

    assert R1_FLOOR - named == set(), R1_FLOOR - named


def test_every_dimension_the_cutover_map_traces_is_on_the_checklist() -> None:
    """Traceability, kept — but this is no longer what completeness means."""
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
