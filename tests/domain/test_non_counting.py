"""One spelling of the non-counting pair, and one module that names Duplicate.

A criterion the board Canceled or closed as a Duplicate counts for nothing
and refuses nothing (KOD-794).  Two readers that each decide that for
themselves can part, so every criterion reader asks the one predicate.  What
the scan below pins to one module is the Duplicate kind: no module outside
the enum's own names it, in any spelling.  Canceled is not scanned, because
it keeps readers of its own outside the criterion reading.  The scan derives
the modules it reads rather than listing them.
"""

import ast
from pathlib import Path

import pytest

from kodezart.types.domain.tracker import WorkflowStateKind, is_non_counting, is_open

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "src" / "kodezart"

#: The module allowed to name the kind, as its own source path states it.
HOME = "types/domain/tracker.py"

#: The kinds the pair covers, and the kinds that close a criterion, each
#: authored here as a literal so the predicates are read against the table
#: rather than against either of them restated.
NON_COUNTING = frozenset({WorkflowStateKind.CANCELED, WorkflowStateKind.DUPLICATE})
CLOSED = NON_COUNTING | {WorkflowStateKind.COMPLETED}


@pytest.mark.parametrize("kind", list(WorkflowStateKind))
def test_is_non_counting_classifies_every_state_kind(kind):
    """Every member of the enum, so a kind added tomorrow is classified."""
    assert is_non_counting(kind) is (kind in NON_COUNTING)
    assert is_open(kind) is (kind not in CLOSED)


def enum_aliases(tree: ast.AST) -> frozenset[str]:
    """Every other local name a from-import binds the enum to."""
    return frozenset(
        alias.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == WorkflowStateKind.__name__ and alias.asname is not None
    )


def names_the_enum(value: ast.expr, aliases: frozenset[str]) -> bool:
    """Whether *value* spells the enum, bare, aliased or module-qualified."""
    if isinstance(value, ast.Name):
        return value.id.endswith(WorkflowStateKind.__name__) or value.id in aliases
    return isinstance(value, ast.Attribute) and value.attr == WorkflowStateKind.__name__


def duplicate_kind_sites(source: str) -> int:
    """How many times *source* names the Duplicate kind, in the shapes below.

    Four shapes: an attribute ``DUPLICATE`` on the enum, and a subscript of
    the enum by the member's name, whether the enum is spelled by its own
    name, by a name a from-import aliased it to, or as an attribute of a
    module; and a string constant that is exactly the kind's value, which is
    the same member to a string enum.
    """
    tree = ast.parse(source)
    aliases = enum_aliases(tree)
    return sum(
        1
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Attribute)
            and node.attr == WorkflowStateKind.DUPLICATE.name
            and names_the_enum(node.value, aliases)
        )
        or (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == WorkflowStateKind.DUPLICATE.name
            and names_the_enum(node.value, aliases)
        )
        or (
            isinstance(node, ast.Constant)
            and node.value == WorkflowStateKind.DUPLICATE.value
        )
    )


def test_the_duplicate_kind_is_named_in_one_module():
    """Exactly one module in the source names the kind at all."""
    naming = {
        path.relative_to(SOURCE_ROOT).as_posix()
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if duplicate_kind_sites(path.read_text())
    }
    assert naming == {HOME}


#: The reading this scan retired: the pre-change ``open_criteria`` body,
#: which named the pair itself instead of asking the one predicate.  It is
#: the planted control that keeps the scan above able to fail.
RETIRED_READING = """
def open_criteria(criteria, *, ref):
    unresolved = tuple(
        issue.issue_key
        for issue in criteria
        if issue.state_kind in {WorkflowStateKind.CANCELED, WorkflowStateKind.DUPLICATE}
    )
    if unresolved:
        raise ScopeSupersessionReadError(ref=ref, criterion_keys=unresolved)
    return compute_gap(criteria=criteria, supersession_refs={})
"""


#: One planted source per spelling the scan must see besides the bare enum:
#: the kind's string value, the enum reached through a module alias, the
#: enum imported under another name, and the enum subscripted by the
#: member's name.
RESPELLINGS = {
    "string-value": (
        "counting = [c for c in criteria\n"
        "            if c.state_kind not in {'canceled', 'duplicate'}]\n"
    ),
    "module-alias": (
        "from kodezart.types.domain import tracker as _t\n"
        "PAIR = {_t.WorkflowStateKind.CANCELED, _t.WorkflowStateKind.DUPLICATE}\n"
    ),
    "enum-alias": (
        "from kodezart.types.domain.tracker import WorkflowStateKind as Kind\n"
        "PAIR = {Kind.CANCELED, Kind.DUPLICATE}\n"
    ),
    "member-subscript": (
        "counting = [c for c in criteria if c.state_kind not in\n"
        "            {WorkflowStateKind.CANCELED, WorkflowStateKind['DUPLICATE']}]\n"
    ),
}


def test_the_scan_reports_a_module_that_names_the_kind_itself():
    assert duplicate_kind_sites(RETIRED_READING) == 1
    for spelling, source in RESPELLINGS.items():
        assert duplicate_kind_sites(source) == 1, spelling
    assert duplicate_kind_sites("def f():\n    return DUPLICATE\n") == 0
    assert duplicate_kind_sites((SOURCE_ROOT / HOME).read_text()) >= 1
