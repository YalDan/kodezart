"""Compare protected Python assertion syntax without importing test code."""

import ast
from collections import Counter
from collections.abc import Sequence

from kodezart.types.domain.assertion_drift import (
    AssertionDeviationClaim,
    AssertionSource,
)
from kodezart.types.domain.organize_owner import CriterionProposal


def protected_assertions(
    *, source: bytes, path: str, qualified_name: str
) -> tuple[AssertionSource, ...]:
    """Resolve exactly one declared definition and retain its assertion order.

    Parsing bytes honors Python's declared source encoding. Conditions use
    syntax-tree identity, so comments, formatting and diagnostic messages
    cannot masquerade as changed expectations. This does not evaluate helper
    calls, parameter tables or transitive value dependencies.
    """
    module = ast.parse(source, filename=path)
    statements = module.body
    selected: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | None = None
    for part in qualified_name.split("."):
        candidates = _definitions(statements, part)
        if len(candidates) != 1:
            raise ValueError("the protected definition is missing or ambiguous")
        (selected,) = candidates
        statements = selected.body
    if not isinstance(selected, ast.FunctionDef | ast.AsyncFunctionDef):
        raise ValueError("the protected reference must name a test function")
    assertions = sorted(
        (node for node in ast.walk(selected) if isinstance(node, ast.Assert)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    return tuple(
        AssertionSource(
            line=node.lineno,
            expression=ast.unparse(node.test),
            structural_form=ast.dump(node.test, include_attributes=False),
        )
        for node in assertions
    )


def _definitions(
    statements: list[ast.stmt], name: str
) -> list[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef]:
    matches: list[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if node.name == name:
                matches.append(node)
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in statements:
        visit(statement)
    return matches


def lost_assertions(
    *, before: Sequence[AssertionSource], after: Sequence[AssertionSource]
) -> tuple[AssertionSource, ...]:
    """The earlier assertions the later reading no longer carries, in earlier order.

    A multiset over syntax-tree identity, so an added assertion, a reorder
    and a reformat each lose nothing, a removed or rewritten condition loses
    the old one, and two readings of the same condition lose one when only
    one survives. This is the whole of what "weakens" means here: a
    designated test changes through a claimed departure, never through a
    rewrite someone would call stronger.
    """
    remaining = Counter(row.structural_form for row in after)
    lost: list[AssertionSource] = []
    for row in before:
        if remaining[row.structural_form] > 0:
            remaining[row.structural_form] -= 1
        else:
            lost.append(row)
    return tuple(lost)


def weakening_mark(
    *, claim: AssertionDeviationClaim, lost: tuple[AssertionSource, ...]
) -> CriterionProposal:
    """The criterion a lost designated assertion is carried by.

    The text is rendered from the claim's address and the lost conditions
    alone: no commit identity and no replacement expression. A replay of the
    same loss, and a second loss of the same assertion, therefore render the
    same bytes, and the mint's own identity — exact parent plus current
    Check — answers both with the child that already stands.
    """
    if not lost:
        raise ValueError("a weakening mark names at least one lost assertion")
    reference = claim.protected_test
    conditions = "; ".join(f"`{row.expression}`" for row in lost)
    return CriterionProposal(
        title=f"Designated test {reference.qualified_name} keeps its assertions",
        check=(
            f"The designated test `{reference.path}::{reference.qualified_name}`, "
            f"which pinned record `{reference.source_ref}` designates, still "
            f"asserts at the head of this lane's branch: {conditions}."
        ),
        do=(
            "Restore those assertions; a designated test changes only through a "
            f"departure claimed against pinned record `{reference.source_ref}` "
            "and amended by the amendment judge."
        ),
    )
