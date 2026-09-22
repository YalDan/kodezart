"""One spelling of the non-counting pair, and one module that names it.

A criterion the board Canceled or closed as a Duplicate counts for nothing
and refuses nothing (KOD-794).  Two readers that each decide that for
themselves can part, so the pair is named in exactly one module and every
other reader asks that module.  The scan below is what turns "exactly one"
into a fact, and it derives the modules it reads rather than listing them.
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


def duplicate_kind_sites(source: str) -> int:
    """How many times *source* names ``WorkflowStateKind.DUPLICATE``.

    An attribute access whose attribute is ``DUPLICATE`` and whose value
    spells the enum: the one shape a module has to write to branch on the
    kind, under whatever name it imported the enum by.
    """
    return sum(
        1
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute)
        and node.attr == WorkflowStateKind.DUPLICATE.name
        and isinstance(node.value, ast.Name)
        and node.value.id.endswith(WorkflowStateKind.__name__)
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


def test_the_scan_reports_a_module_that_names_the_kind_itself():
    assert duplicate_kind_sites(RETIRED_READING) == 1
    assert duplicate_kind_sites("def f():\n    return DUPLICATE\n") == 0
    assert duplicate_kind_sites((SOURCE_ROOT / HOME).read_text()) >= 1
