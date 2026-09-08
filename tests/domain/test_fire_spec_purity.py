"""Static boundary on the total ticket formatter's imports and calls."""

import ast
import inspect

from kodezart.domain import ticket


def test_ticket_formatter_has_only_domain_imports_and_pure_calls():
    tree = ast.parse(inspect.getsource(ticket))
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert all(isinstance(node, ast.ImportFrom) for node in imports)
    assert {node.module for node in imports} <= {
        "typing",
        "kodezart.types.domain.agent",
        "kodezart.types.domain.fire_spec",
    }
    calls = [node.func for node in ast.walk(tree) if isinstance(node, ast.Call)]
    for call in calls:
        if isinstance(call, ast.Name):
            assert call.id in {"format_ticket_as_task", "assert_never"}
        else:
            assert isinstance(call, ast.Attribute)
            if call.attr == "append":
                assert isinstance(call.value, ast.Name)
                assert any(
                    isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                    and node.target.id == call.value.id
                    and isinstance(node.value, ast.List)
                    for node in ast.walk(tree)
                )
            else:
                assert call.attr == "join"
                assert isinstance(call.value, ast.Constant)
                assert isinstance(call.value.value, str)
    assert not any(
        isinstance(node, (ast.With, ast.AsyncWith, ast.Await, ast.Global, ast.Nonlocal))
        for node in ast.walk(tree)
    )
