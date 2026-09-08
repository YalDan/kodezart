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
            (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) > 1
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "landing"
            )
            or (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "landing"
            )
            or (
                isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == "landing"
            )
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


def landing_derivation_violations(source, module):
    tree = ast.parse(source)
    failures = []
    wire_read = ast.dump(
        ast.parse(
            "WorkRefLanding.UNKNOWN if landing is None else WorkRefLanding(landing)",
            mode="eval",
        ).body
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "landing":
            if (
                module != "adapters/linear_mcp_tracker.py"
                or ast.dump(node.value) != wire_read
            ):
                failures.append(node.lineno)
        elif (
            isinstance(node, ast.AnnAssign)
            and ast.unparse(node.annotation) == "WorkRefLanding"
        ):
            if (
                module != "types/domain/branch.py"
                or ast.unparse(node.target) != "landing"
                or node.value is None
                or ast.unparse(node.value) != "WorkRefLanding.UNKNOWN"
            ):
                failures.append(node.lineno)
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_input_for"
        ):
            allowed_calls = {
                "self._nearest_deliverable_ref",
                "self._tracker.read_issue",
                "is_open",
                "self._log.ainfo",
                "BaseResolutionError",
                "assert_never",
                "BaseInput",
            }
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and ast.unparse(call.func) not in allowed_calls
                ):
                    failures.append(call.lineno)
    return tuple(failures)


def test_landing_can_only_arrive_from_the_existing_native_record():
    assert {
        path.relative_to(ROOT).as_posix(): failures
        for path in ROOT.rglob("*.py")
        if (
            failures := landing_derivation_violations(
                path.read_text(), path.relative_to(ROOT).as_posix()
            )
        )
    } == {}


@pytest.mark.parametrize(
    "detector",
    [
        "self._git.is_ancestor('repo', 'lane', 'trunk')",
        "self._git.diff_refs('repo', 'lane', 'trunk')",
        "self._forge.get_merge_state('lane')",
        "derive_from_another_module(ref)",
    ],
)
def test_topology_forge_and_delegated_detectors_fail_before_they_can_run(detector):
    source = f"async def _input_for(ref):\n    return {detector}\n"
    assert landing_derivation_violations(source, "services/base_resolver.py")


@pytest.mark.parametrize(
    "expression",
    [
        "WorkRefLanding.LANDED",
        "'landed' if ancestry else 'unknown'",
        "WorkRefLanding.LANDED if diff_empty else WorkRefLanding.UNKNOWN",
        "WorkRefLanding(merge_state)",
        "bool(ref)",
    ],
)
def test_a_constructor_cannot_derive_or_assume_a_landing(expression):
    assert landing_derivation_violations(
        f"WorkRef(landing={expression})", "services/base_resolver.py"
    )
    assert landing_derivation_violations(
        f"WorkRef(landing={expression})", "adapters/linear_mcp_tracker.py"
    )


def test_a_second_landing_carrier_and_changed_absence_default_fail():
    assert landing_derivation_violations(
        "class OtherRecord:\n    landing: WorkRefLanding = WorkRefLanding.UNKNOWN",
        "types/domain/other.py",
    )
    assert landing_derivation_violations(
        "class WorkRef:\n    landing: WorkRefLanding = WorkRefLanding.NOT_LANDED",
        "types/domain/branch.py",
    )


def test_mapping_get_cannot_collapse_landing_to_a_boolean():
    assert landing_read_violations(
        "def new_read(ref):\n    return bool(ref.model_dump().get('landing'))",
        "new_reader.py",
    )
