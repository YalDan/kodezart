"""Everything the organize lane consumes has exactly one definition site.

The lane's convergence machinery is consumed, not re-declared: the finding
shape and its role, the configured convergence bound, the halt report on
exhaustion, the ordered raise sites and the gap computation are each defined
once, and the convergence loop is a named method so that there is a symbol to
key on at all — a ``for`` statement has no definition site a guard can name.

The scanned surface is derived: every shipped source file is walked, and
every binding of a name in it counts as a definition site of that name, at
any depth — a ``class`` or ``def`` statement, a plain or annotated
assignment to the bare name, or a ``type`` alias. A consumed symbol is keyed
on its last dotted segment, so a same-named class nested in a function or in
another class, a module-level function beside a method of that name, or an
assignment such as ``X = enum.StrEnum(...)`` is a second site of it, in
whatever module it sits. The one site a symbol may have is then checked for
its module and for the scopes that qualify it. The symbol list is
hand-named, because the point of the guard is that these particular symbols
have one home each; the controls below are hand-written sources, so a
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

#: The statements that open a scope, and so qualify what they enclose.
DEFINITION = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _bound_names(node: ast.AST) -> tuple[str, ...]:
    """The bare names *node* binds, if it is a definition or an assignment."""
    if isinstance(node, DEFINITION):
        return (node.name,)
    if isinstance(node, ast.Assign):
        return tuple(t.id for t in node.targets if isinstance(t, ast.Name))
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return (node.target.id,)
    if isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
        return (node.name.id,)
    return ()


def definitions(tree: ast.AST) -> list[tuple[str, str, int]]:
    """Every binding in *tree* at any depth: bare name, qualified name, line.

    A class or function body is a scope; an ``if``, ``try`` or loop body is
    not, so a conditional re-definition keeps the qualified name it shadows.
    The bare name is what the sites are counted by; the qualified name is
    what says which scope the one allowed site sits in.
    """
    found: list[tuple[str, str, int]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            for name in _bound_names(child):
                qualified = f"{prefix}.{name}" if prefix else name
                found.append((name, qualified, child.lineno))
            here = prefix
            if isinstance(child, DEFINITION):
                here = f"{prefix}.{child.name}" if prefix else child.name
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
    """Every binding site of each bare name, as ``module:line``."""
    sites: dict[str, list[str]] = defaultdict(list)
    for module, tree in trees.items():
        for name, _, lineno in definitions(tree):
            sites[name].append(f"{module}:{lineno}")
    return sites


def qualified_sites(trees: dict[str, ast.Module], symbol: str) -> list[tuple[str, str]]:
    """Every binding of *symbol*'s bare name, as module and qualified name."""
    bare = symbol.rsplit(".", 1)[-1]
    return [
        (module, qualified)
        for module, tree in trees.items()
        for name, qualified, _ in definitions(tree)
        if name == bare
    ]


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
    """One binding of the bare name in the whole tree, the named one."""
    assert qualified_sites(scanned_sources(), name) == [(module, name)]


def bound_reads(trees: dict[str, ast.Module]) -> list[tuple[str, str, int]]:
    """Every read of the bound: an attribute access, or ``getattr`` by name."""
    return [
        (module, scope_of(tree, node), node.lineno)
        for module, tree in trees.items()
        for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and node.attr == BOUND)
        or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == BOUND
        )
    ]


def test_the_configured_convergence_bound_is_declared_once_and_read_in_the_loop() -> (
    None
):
    """The bound is one field of the policy, and only the loop it bounds reads it."""
    trees = scanned_sources()
    assert qualified_sites(trees, BOUND) == [(CAUSES, f"OrganizePolicy.{BOUND}")], (
        qualified_sites(trees, BOUND)
    )
    reads = bound_reads(trees)
    assert {(module, scope) for module, scope, _ in reads} == {
        ("services/organize_owner.py", LOOP)
    }, reads
    # Two readings, both the loop's own: the bound it iterates to, and the
    # value the exhaustion evidence reports. A third would be a second loop.
    assert len(reads) == 2, reads


def test_the_scope_a_node_sits_in_is_its_innermost_definition() -> None:
    """The control for ``scope_of``: a sibling, a nested function, the module."""
    source = (
        "class OrganizeOwner:\n"
        "    async def _converge(self):\n"
        "        def inner():\n"
        "            return self._policy.max_convergence_rounds\n"
        "\n"
        "    async def _sibling(self):\n"
        "        await self._halt(cause=None)\n"
        "\n"
        "\n"
        "rounds = getattr(policy, 'max_convergence_rounds')\n"
    )
    trees = {"planted.py": ast.parse(source)}
    tree = trees["planted.py"]
    halts = [
        scope_of(tree, node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_halt"
    ]
    assert halts == ["OrganizeOwner._sibling"]
    assert sorted(bound_reads(trees)) == [
        ("planted.py", "", 10),
        ("planted.py", "OrganizeOwner._converge.inner", 4),
    ]


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
            _guard(tree, node),
        )
        for _, tree, node in ordered
    ] == [
        (
            "StageHaltCause.HUMAN_DECISION",
            None,
            "if route is AdmissionRoute.ESCALATE",
        ),
        (
            "StageHaltCause.HUMAN_DECISION",
            None,
            "except OrganizeDecisionRequiredError",
        ),
        (
            "StageHaltCause.ADMISSION_EXHAUSTED",
            "write_back",
            "if verified_write.verdict is not AuditVerdict.HOLDS",
        ),
        (
            "StageHaltCause.ADMISSION_EXHAUSTED",
            "admission",
            "else of for range(self._policy.max_admission_rounds)",
        ),
        (
            "StageHaltCause.CONVERGENCE_EXHAUSTED",
            "convergence",
            "else of for range(self._policy.max_convergence_rounds)",
        ),
    ]
    # The three causes the loop raises are the loop's to report and nobody
    # else's. Their own module declares them, and there the halt variants
    # name them in a ``Literal[...]`` annotation; every other naming of them
    # is a report, and the loop is the only thing that reports one.
    # STAGE_INCOMPLETE is deliberately absent: the barrier reports it too,
    # through the one report builder both it and the loop call.
    raised = {"HUMAN_DECISION", "ADMISSION_EXHAUSTED", "CONVERGENCE_EXHAUSTED"}
    named = {
        (module, scope_of(tree, node))
        for module, tree in trees.items()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in raised
        and isinstance(node.value, ast.Name)
        and node.value.id == "StageHaltCause"
        and not (module == CAUSES and _in_literal(tree, node))
    }
    assert named == {("services/organize_owner.py", LOOP)}, named


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _in_literal(tree: ast.AST, target: ast.AST) -> bool:
    """Whether *target* sits inside a ``Literal[...]`` subscript."""
    parents = _parents(tree)
    node = target
    while node in parents:
        node = parents[node]
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "Literal"
        ):
            return True
    return False


def _guard(tree: ast.AST, target: ast.AST) -> str:
    """The innermost branch that leads to *target*: an ``if``, a handler, an else.

    Two raise sites with one cause and no bound differ in what led to them,
    so it is the branch that tells them apart and fixes their order.
    """
    parents = _parents(tree)
    node = target
    while node in parents:
        parent = parents[node]
        if isinstance(parent, ast.If) and node in parent.body:
            return f"if {ast.unparse(parent.test)}"
        if isinstance(parent, ast.ExceptHandler) and parent.type is not None:
            return f"except {ast.unparse(parent.type)}"
        if isinstance(parent, (ast.For, ast.AsyncFor)) and node in parent.orelse:
            return f"else of for {ast.unparse(parent.iter)}"
        node = parent
    return ""


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


#: One control per duplicate shape the collector claims to see: the name, the
#: planted source, and the number of sites the name has in it. Hand-written,
#: because the shipped tree has no duplicate to draw them from — which is the
#: whole point of the guard.
CONTROLS = (
    (
        "a second top-level definition",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\ndef organize_gap():\n    ...\n",
        2,
    ),
    (
        "a re-definition inside a conditional",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\nif True:\n    def organize_gap():\n"
        "        ...\n",
        2,
    ),
    (
        "a module-level assignment",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\norganize_gap = lambda **kwargs: ()\n",
        2,
    ),
    (
        "a functional enum assignment",
        "DefectRole",
        "import enum\n\n\nclass DefectRole(enum.StrEnum):\n    INSTANCE = 'instance'\n"
        "\n\nDefectRole = enum.StrEnum('DefectRole', {'INSTANCE': 'instance'})\n",
        2,
    ),
    (
        "a class nested in a function",
        "DefectRole",
        "class DefectRole:\n    ...\n\n\ndef _legacy_roles():\n"
        "    class DefectRole:\n        INSTANCE = 'instance'\n",
        2,
    ),
    (
        "a class nested in a class",
        "SpecFinding",
        "class SpecFinding:\n    ...\n\n\nclass _Shadow:\n"
        "    class SpecFinding(SpecFinding):\n        pass\n",
        2,
    ),
    (
        "an annotated assignment",
        "ConvergenceExhaustedHalt",
        "class ConvergenceExhaustedHalt:\n    ...\n\n\n"
        "ConvergenceExhaustedHalt: type = dict\n",
        2,
    ),
    (
        "a type alias",
        "StageHaltReport",
        "class StageHaltReport:\n    ...\n\n\ntype StageHaltReport = dict\n",
        2,
    ),
)


@pytest.mark.parametrize(
    ("shape", "name", "source", "count"),
    CONTROLS,
    ids=[shape for shape, _, _, _ in CONTROLS],
)
def test_the_detector_sees_each_duplicate_shape(
    shape: str, name: str, source: str, count: int
) -> None:
    sites = definition_sites({"planted.py": ast.parse(source)})
    assert len(sites[name]) == count, sites


def test_a_method_and_a_module_function_of_one_name_are_two_sites_of_it() -> None:
    """A module function beside the method is a second ``_converge``."""
    source = (
        "class OrganizeOwner:\n"
        "    def _converge(self):\n"
        "        ...\n"
        "\n"
        "\n"
        "def _converge():\n"
        "    ...\n"
    )
    trees = {"planted.py": ast.parse(source)}
    assert definition_sites(trees)["_converge"] == ["planted.py:2", "planted.py:6"]
    assert qualified_sites(trees, "OrganizeOwner._converge") == [
        ("planted.py", "OrganizeOwner._converge"),
        ("planted.py", "_converge"),
    ]


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
    assert definition_sites(scanned_sources())["organize_gap"] == [
        "domain/organize.py:1",
        "elsewhere.py:1",
    ]
    with pytest.raises(AssertionError):
        test_each_consumed_symbol_is_defined_exactly_once(
            "domain/organize.py", "organize_gap"
        )
