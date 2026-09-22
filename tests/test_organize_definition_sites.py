"""Everything the organize lane consumes has exactly one definition site.

The lane's convergence machinery is consumed, not re-declared: the finding
shape and its role, the configured convergence bound, the halt report on
exhaustion, the ordered raise sites and the gap computation are each defined
once, and the convergence loop is a named method so that there is a symbol to
key on at all — a ``for`` statement has no definition site a guard can name.

The scanned surface is derived: every shipped source file is walked and every
definition in it is qualified by the scopes that enclose it, so a duplicate
hidden in a nested scope or in a second module is still two names. The symbol
list is hand-named, because the point of the guard is that these particular
symbols have one home each; the controls below are hand-written sources, so a
detector arm the shipped tree happens not to exercise still has a witness.
"""

import ast
from collections import defaultdict
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "kodezart"

#: module path -> the qualified names that module is the one home of.
CONSUMED = {
    "types/domain/organize.py": ("SpecFinding", "DefectRole"),
    "types/domain/organize_owner.py": (
        "OrganizePolicy",
        "OrganizeBoundEvidence",
        "ConvergenceExhaustedHalt",
        "StageHaltReport",
    ),
    "services/organize_owner.py": (
        "OrganizeOwner._converge",
        "OrganizeOwner._halt",
        "OrganizeOwner._stage_incomplete",
    ),
    "domain/organize.py": ("organize_gap",),
}

#: The one method the convergence rounds live in, and the one policy field that
#: bounds them.
LOOP = "OrganizeOwner._converge"
BOUND = "max_convergence_rounds"
#: Where the halt causes are declared, and so where the halt variants name them.
CAUSES = "types/domain/organize_owner.py"

DEFINITION = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def definitions(tree: ast.AST) -> list[tuple[str, int]]:
    """Every definition in *tree*, qualified by the scopes enclosing it.

    A class or function body is a scope; an ``if``, ``try`` or loop body is
    not, so a conditional re-definition keeps the qualified name it shadows
    and is reported as the second definition it is.
    """
    found: list[tuple[str, int]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            here = prefix
            if isinstance(child, DEFINITION):
                here = f"{prefix}.{child.name}" if prefix else child.name
                found.append((here, child.lineno))
            walk(child, here)

    walk(tree, "")
    return found


def scanned_sources() -> dict[str, ast.Module]:
    """Every shipped source file, derived from the tree rather than listed."""
    return {
        path.relative_to(SOURCE).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(SOURCE.rglob("*.py"))
    }


def definition_sites(trees: dict[str, ast.Module]) -> dict[str, list[str]]:
    sites: dict[str, list[str]] = defaultdict(list)
    for module, tree in trees.items():
        for name, lineno in definitions(tree):
            sites[name].append(f"{module}:{lineno}")
    return sites


def scope_of(tree: ast.AST, target: ast.AST) -> str:
    """The qualified definition the *target* node is lexically inside."""
    found = ""

    def walk(node: ast.AST, prefix: str) -> None:
        nonlocal found
        for child in ast.iter_child_nodes(node):
            here = prefix
            if isinstance(child, DEFINITION):
                here = f"{prefix}.{child.name}" if prefix else child.name
            if child is target:
                found = here
            walk(child, here)

    walk(tree, "")
    return found


CONSUMED_SYMBOLS = [
    (module, name) for module, names in CONSUMED.items() for name in names
]


@pytest.mark.parametrize(
    ("module", "name"), CONSUMED_SYMBOLS, ids=[n for _, n in CONSUMED_SYMBOLS]
)
def test_each_consumed_symbol_is_defined_exactly_once(module: str, name: str) -> None:
    sites = definition_sites(scanned_sources())
    assert [site.split(":")[0] for site in sites[name]] == [module], sites[name]


def test_the_configured_convergence_bound_is_declared_once_and_read_in_the_loop() -> (
    None
):
    """The bound is one annotated field, and only the loop it bounds reads it."""
    trees = scanned_sources()
    annotations = [
        f"{module}:{node.lineno}"
        for module, tree in trees.items()
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == BOUND
    ]
    assert annotations == ["types/domain/organize_owner.py:159"], annotations
    reads = [
        (module, scope_of(tree, node), node.lineno)
        for module, tree in trees.items()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == BOUND
    ]
    assert {(module, scope) for module, scope, _ in reads} == {
        ("services/organize_owner.py", LOOP)
    }, reads
    # Two readings, both the loop's own: the bound it iterates to, and the
    # value the exhaustion evidence reports. A third would be a second loop.
    assert len(reads) == 2, reads


def test_the_halt_sites_live_in_the_convergence_loop_in_source_order() -> None:
    trees = scanned_sources()
    calls = [
        (module, tree, node)
        for module, tree in trees.items()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_halt"
    ]
    assert {(module, scope_of(tree, node)) for module, tree, node in calls} == {
        ("services/organize_owner.py", LOOP)
    }, [(module, scope_of(tree, node)) for module, tree, node in calls]
    ordered = sorted(calls, key=lambda call: call[2].lineno)
    assert [
        (
            _keyword_name(node, "cause"),
            _bound_loop(node),
        )
        for _, _, node in ordered
    ] == [
        ("StageHaltCause.HUMAN_DECISION", None),
        ("StageHaltCause.HUMAN_DECISION", None),
        ("StageHaltCause.ADMISSION_EXHAUSTED", "write_back"),
        ("StageHaltCause.ADMISSION_EXHAUSTED", "admission"),
        ("StageHaltCause.CONVERGENCE_EXHAUSTED", "convergence"),
    ]
    # The two exhaustion causes are the loop's to report and nobody else's.
    # Their own module is where the halt variants declare them, which is the
    # one definition site; every other naming of them is a report, and the
    # loop is the only thing that reports one. STAGE_INCOMPLETE is
    # deliberately absent: the barrier reports it too, through the one report
    # builder both it and the loop call.
    exhaustion = {"ADMISSION_EXHAUSTED", "CONVERGENCE_EXHAUSTED"}
    named = {
        (module, scope_of(tree, node))
        for module, tree in trees.items()
        if module != CAUSES
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in exhaustion
        and isinstance(node.value, ast.Name)
        and node.value.id == "StageHaltCause"
    }
    assert named == {("services/organize_owner.py", LOOP)}, named


def _keyword_name(call: ast.Call, arg: str) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == arg and isinstance(keyword.value, ast.Attribute):
            value = keyword.value
            if isinstance(value.value, ast.Name):
                return f"{value.value.id}.{value.attr}"
    return None


def _bound_loop(call: ast.Call) -> str | None:
    """The ``loop=`` literal of the bound evidence this halt carries, if any."""
    for keyword in call.keywords:
        if keyword.arg == "bound" and isinstance(keyword.value, ast.Call):
            for inner in keyword.value.keywords:
                if inner.arg == "loop" and isinstance(inner.value, ast.Constant):
                    assert isinstance(inner.value.value, str)
                    return inner.value.value
    return None


#: One control per duplicate shape the collector claims to see, plus the shape
#: it must not report. Hand-written, because the shipped tree has no duplicate
#: to draw them from — which is the whole point of the guard.
CONTROLS = (
    (
        "a second top-level definition",
        "def organize_gap():\n    ...\n\n\ndef organize_gap():\n    ...\n",
        2,
    ),
    (
        "a re-definition inside a conditional",
        "def organize_gap():\n    ...\n\n\nif True:\n    def organize_gap():\n"
        "        ...\n",
        2,
    ),
)


@pytest.mark.parametrize(
    ("shape", "source", "count"), CONTROLS, ids=[shape for shape, _, _ in CONTROLS]
)
def test_the_detector_sees_each_duplicate_shape(
    shape: str, source: str, count: int
) -> None:
    sites = definition_sites({"planted.py": ast.parse(source)})
    assert len(sites["organize_gap"]) == count, sites


def test_a_method_and_a_module_function_of_one_name_are_two_symbols() -> None:
    """Qualification is what makes the method's name the method's alone."""
    source = (
        "class OrganizeOwner:\n"
        "    def _converge(self):\n"
        "        ...\n"
        "\n"
        "\n"
        "def _converge():\n"
        "    ...\n"
    )
    sites = definition_sites({"planted.py": ast.parse(source)})
    assert len(sites["OrganizeOwner._converge"]) == 1, sites
    assert len(sites["_converge"]) == 1, sites


def test_the_guard_reddens_when_a_consumed_symbol_gains_a_second_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The scanned surface is the tree, so a new module is scanned unasked."""
    second = tmp_path / "kodezart"
    second.mkdir()
    (second / "elsewhere.py").write_text("def organize_gap():\n    ...\n")
    (second / "domain").mkdir()
    (second / "domain" / "organize.py").write_text("def organize_gap():\n    ...\n")
    monkeypatch.setattr("tests.test_organize_definition_sites.SOURCE", second)
    with pytest.raises(AssertionError):
        test_each_consumed_symbol_is_defined_exactly_once(
            "domain/organize.py", "organize_gap"
        )
