"""Compare protected Python assertion syntax without importing test code."""

import ast

from kodezart.types.domain.assertion_drift import AssertionSource


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
