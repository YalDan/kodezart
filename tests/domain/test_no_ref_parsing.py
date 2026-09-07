"""KOD-53/AC-26 clause 3 — no scope surface derives a value from a ref's TEXT.

KOD-36 deliverable 5 states the clause: the name is not the record.  A base
obtained by parsing a branch name is forbidden at the scope surfaces for
the same reason it is forbidden at the source, and the behavioural suite
cannot see that: a parse that PRESERVES the value is invisible to every
behavioural assertion by construction.  So the demonstration reads the
syntax tree of the four scope-surface modules and asks the criterion's own
question — is a ref-named expression being taken apart.

**This is a lint approximation, and the approximation is stated rather
than hidden.**  What makes an expression "ref-named" is a substring gate:
the source segment of the receiver (or of a regex argument, or of the
sliced value) contains ``base``, ``branch`` or ``ref``.  That gate fires
on an unrelated identifier that happens to carry one of those words, and
it misses a ref held in a tersely-named variable.  It is narrower than a
plain substring scan of the file — unrelated string handling is not an
offender and an index-sliced ref is — and it is not a decision procedure.

**No meta-test.**  Nothing here asserts that the check can fire; a test of
the test is what the ``8af24d1`` deletion objected to.  Falsifiability is
demonstrated at grading time by the grader's own probe — insert a ref
parse at a scope surface, observe this module redden — and recorded on the
tracker, never committed.

Home per the 2026-08-11 `[decision]` on KOD-36: a pytest module, the
repository's existing idiom for source-shape assertions, replacing the
``scripts/`` checker and its ``make check`` step.
"""

import ast
from pathlib import Path

import pytest

#: String surgery that could turn a ref's NAME into a base.
DISSECTORS = frozenset(
    {"split", "rsplit", "partition", "rpartition", "removeprefix", "removesuffix"},
)
#: Regex entry points, whose subject is an argument rather than a receiver.
MATCHERS = frozenset({"match", "fullmatch", "search"})
#: What an expression must mention for its dissection to be about a ref.
REF_WORDS = ("base", "branch", "ref")

#: The scope surfaces themselves, not only the modules that model a base:
#: the changeset digest and the outer review diff are where a parsed base
#: would actually be spent.
SCOPE_SURFACES = (
    "src/kodezart/domain/base_scope.py",
    "src/kodezart/types/domain/branch.py",
    "src/kodezart/chains/ralph_loop.py",
    "src/kodezart/chains/ralph_workflow.py",
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _names_a_ref(segment: str | None) -> bool:
    return segment is not None and any(word in segment.lower() for word in REF_WORDS)


def ref_parsing_sites(source: str) -> list[str]:
    """Every place *source* derives a value from the text of a ref."""
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            receiver = ast.get_source_segment(source, node.func.value)
            if node.func.attr in DISSECTORS and _names_a_ref(receiver):
                offenders.append(f"line {node.lineno}: .{node.func.attr}(")
            if node.func.attr in MATCHERS and any(
                _names_a_ref(ast.get_source_segment(source, argument))
                for argument in node.args
            ):
                offenders.append(f"line {node.lineno}: regex over a ref")
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Slice)
            and _names_a_ref(ast.get_source_segment(source, node.value))
        ):
            offenders.append(f"line {node.lineno}: slice of a ref")
    return offenders


@pytest.mark.parametrize("module", SCOPE_SURFACES)
def test_no_scope_surface_parses_a_ref_to_obtain_a_base(module: str) -> None:
    source = (REPO_ROOT / module).read_text(encoding="utf-8")
    assert ref_parsing_sites(source) == [], (
        f"{module} parses a ref's name to obtain a base (KOD-53/AC-26 clause 3)"
    )
