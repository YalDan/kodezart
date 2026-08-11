"""Gate lint: no scope surface derives a value from the TEXT of a ref.

KOD-53/AC-26 clause 3, KOD-36 deliverable 5: the name is not the record.
A base obtained by parsing a branch name is forbidden at the scope
surfaces for the same reason it is forbidden at the source — so this
check reads the syntax tree of the four scope-surface modules and asks
the criterion's own question: is a ref-named expression being taken
apart.  Narrow in both directions: unrelated string handling is not an
offender, and a base obtained by index slicing is.

A lint step of ``make check``, beside ``verify-no-origin-literal`` —
deliberately not a test: the behavioural suite pins the value a scope
surface computes with, and a value-preserving parse is invisible to it
by construction.
"""

import ast
import sys
from pathlib import Path

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
    "src/kodezart/types/domain/base_spec.py",
    "src/kodezart/chains/ralph_loop.py",
    "src/kodezart/chains/ralph_workflow.py",
)


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


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    failed = False
    for name in SCOPE_SURFACES:
        source = (root / name).read_text(encoding="utf-8")
        for site in ref_parsing_sites(source):
            print(f"{name}: {site}")
            failed = True
    if failed:
        print("ERROR: a scope surface parses a ref's name (KOD-53/AC-26 clause 3)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
