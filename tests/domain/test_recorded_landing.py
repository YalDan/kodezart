"""Every explicit landing read preserves the recorded three-state value."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2] / "src" / "kodezart"
READ_OWNERS = {
    ("types/domain/branch.py", "identity"),
    ("adapters/linear_markers.py", "work_ref_body"),
    ("services/base_resolver.py", "_input_for"),
}


def landing_read_violations(source, module):
    tree = ast.parse(source)
    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }

    def owner(node):
        while id(node) in parents:
            node = parents[id(node)]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
        return None

    def permitted(node):
        pair = (module, owner(node))
        parent = parents[id(node)]
        if pair not in READ_OWNERS:
            return False
        if pair == ("types/domain/branch.py", "identity"):
            return isinstance(parent, ast.Tuple) and isinstance(
                parents[id(parent)], ast.Return
            )
        if pair == ("adapters/linear_markers.py", "work_ref_body"):
            return (
                isinstance(parent, ast.Attribute)
                and parent.attr == "value"
                and isinstance(parents[id(parent)], ast.FormattedValue)
            )
        if isinstance(parent, ast.Match) and parent.subject is node:
            patterns = [ast.unparse(case.pattern) for case in parent.cases]
            return patterns == [
                "WorkRefLanding.LANDED",
                "WorkRefLanding.NOT_LANDED",
                "WorkRefLanding.UNKNOWN",
                "_",
            ] and all(case.guard is None for case in parent.cases)
        return (
            isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Name)
            and parent.func.id == "assert_never"
            and parent.args == [node]
            and not parent.keywords
        )

    failures = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "landing":
            if not isinstance(node.ctx, ast.Load) or not permitted(node):
                failures.append(node.lineno)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) > 1
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "landing"
        ) or (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "landing"
        ):
            failures.append(node.lineno)
    return tuple(failures)


def test_all_actual_landing_reads_have_their_three_state_owners():
    sources = {
        p.relative_to(ROOT).as_posix(): p.read_text() for p in ROOT.rglob("*.py")
    }
    assert {
        path: errors
        for path, source in sources.items()
        if (errors := landing_read_violations(source, path))
    } == {}
    readers = {
        path
        for path, source in sources.items()
        if any(
            isinstance(n, ast.Attribute) and n.attr == "landing"
            for n in ast.walk(ast.parse(source))
        )
    }
    assert readers == {path for path, _ in READ_OWNERS}


@pytest.mark.parametrize(
    "statement",
    [
        "if ref.landing: return None",
        "if not ref.landing: return None",
        "return bool(ref.landing)",
        "value = ref.landing\n    return value",
        "return ref.landing or WorkRefLanding.NOT_LANDED",
        "return getattr(ref, 'landing')",
        "return ref.model_dump()['landing']",
    ],
)
def test_direct_boolean_alias_and_reflective_reads_fail(statement):
    assert landing_read_violations(
        "def _input_for(ref):\n    " + statement, "services/base_resolver.py"
    )


def test_known_owner_does_not_permit_boolean_wire_or_identity_coercion():
    assert landing_read_violations(
        "def work_ref_body(ref):\n    return f'{bool(ref.landing.value)}'",
        "adapters/linear_markers.py",
    )
    assert landing_read_violations(
        "def identity(self):\n    return (bool(self.landing),)",
        "types/domain/branch.py",
    )


def test_unknown_is_its_own_case_and_an_extra_reader_refuses():
    source = (ROOT / "services/base_resolver.py").read_text()
    assert not landing_read_violations(source, "services/base_resolver.py")
    combined = source.replace(
        "case WorkRefLanding.NOT_LANDED:\n                pass\n"
        "            case WorkRefLanding.UNKNOWN:",
        "case WorkRefLanding.NOT_LANDED | WorkRefLanding.UNKNOWN:",
    )
    assert combined != source
    assert landing_read_violations(combined, "services/base_resolver.py")
    assert landing_read_violations(source, "new_consumer.py")
