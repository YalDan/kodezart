"""Everything the organize lane consumes has exactly one definition site.

The lane's convergence machinery is consumed, not re-declared: the finding
shape and its role, the configured convergence bound, the halt report on
exhaustion, the ordered raise sites and the gap computation are each defined
once, and the convergence loop is a named method so that there is a symbol to
key on at all — a ``for`` statement has no definition site a guard can name.

The scanned surface is derived: every shipped source file is walked, and
every binding of a name in it counts as a definition site of that name, at
any depth. The bindings counted are exactly these:

- a ``class`` or ``def`` statement;
- every name stored by an assignment, plain, annotated or augmented,
  including each name a tuple, list or starred target unpacks into;
- the target of a ``:=`` expression;
- the target of a ``for`` statement or a comprehension, the name a ``with``
  item binds with ``as``, and the name an ``except`` handler binds;
- the ``as`` name of an ``import`` or ``from ... import``;
- a ``type`` alias;
- an attribute store, such as ``OrganizeOwner._converge = ...``, which is a
  site of its attribute's name;
- ``setattr(<object>, "<name>", ...)`` with the name written as a literal,
  and a store into ``globals()``, ``vars(<object>)`` or ``<object>.__dict__``
  under a literal key.

A consumed symbol is keyed on its last dotted segment, so a same-named class
nested in a function or in another class, a module-level function beside a
method of that name, or an assignment such as ``X = enum.StrEnum(...)`` is a
second site of it, in whatever module it sits. The one site a symbol may have
is then checked for its module and for the scopes that qualify it. The symbol
list is hand-named, because the point of the guard is that these particular
symbols have one home each; the controls below are hand-written sources, so a
detector arm the shipped tree happens not to exercise still has a witness.

The halt causes the convergence loop raises are named only inside that loop.
That scan reads by object after import: a member of the causes however the
enum is reached (an import alias, a module or local alias, a ``:=`` target,
a qualified module path), a value call, a ``getattr`` or subscript lookup
with a literal member name, a report model validated, constructed or copied
from a literal cause, and a string constant equal to a raised cause's value
outside the causes module unless it is another enum's own member
declaration. The halt builder is read wherever it is read, so a bound method
held for later is a reading too.

Outside every static guard's reach:

- a value handed across a function boundary, where the other function is not
  resolved at this site (returned from a helper, stored on an object and read
  elsewhere, or passed through a container built elsewhere);
- a name built at run time;
- a binding made only when a function runs (``setattr`` or ``globals()``
  inside a function body).

Of the last, a ``setattr`` or ``globals()`` store whose name is written as a
literal is still counted wherever it is written; what stays unseen is a
binding whose name reaches it only while the function runs. Each of these
shapes is held unseen by a committed test below.
"""

import ast
import builtins
import enum
import importlib
import importlib.util
import inspect
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import TypeAliasType, get_args

import pytest
from pydantic import BaseModel, TypeAdapter

from kodezart.types.domain.organize_owner import StageHaltCause

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
#: The one report builder the loop raises through.
HALT = "_halt"
#: The request a round raises for a halt it decided on while it holds its
#: declared set; the loop writes it through ``HALT`` once the set is released.
HALT_REQUEST = "_HaltRequestError"
#: The causes the convergence loop raises, by object, by member name and by
#: value. STAGE_INCOMPLETE is the barrier's too, so it is not among them.
RAISED = (
    StageHaltCause.HUMAN_DECISION,
    StageHaltCause.ADMISSION_EXHAUSTED,
    StageHaltCause.CONVERGENCE_EXHAUSTED,
)
RAISED_NAMES = frozenset(cause.name for cause in RAISED)
RAISED_VALUES = frozenset(cause.value for cause in RAISED)
#: The model methods that validate a mapping or keywords into the model.
MODEL_BUILDERS = ("model_validate", "model_construct")
#: What a name the resolver cannot read resolves to.
UNRESOLVED = object()

#: The statements that open a scope, and so qualify what they enclose.
DEFINITION = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


#: The calls whose result is a namespace a subscript store binds a name in.
NAMESPACE_CALLS = ("globals", "vars")


def _literal_namespace_key(node: ast.Subscript) -> str | None:
    """The literal key of a store into ``globals()``, ``vars(x)`` or ``x.__dict__``."""
    space = node.value
    into_namespace = (
        isinstance(space, ast.Call)
        and isinstance(space.func, ast.Name)
        and space.func.id in NAMESPACE_CALLS
    ) or (isinstance(space, ast.Attribute) and space.attr == "__dict__")
    key = node.slice
    if into_namespace and isinstance(key, ast.Constant) and isinstance(key.value, str):
        return key.value
    return None


def _bound_names(node: ast.AST) -> tuple[str, ...]:
    """The bare names *node* binds, in any of the forms the docstring lists."""
    if isinstance(node, DEFINITION):
        return (node.name,)
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        # Assignment targets at any depth of unpacking, ``:=``, ``for``,
        # comprehension and ``with ... as`` targets, and ``type`` aliases.
        return (node.id,)
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
        return (node.attr,)
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
        key = _literal_namespace_key(node)
        return () if key is None else (key,)
    if isinstance(node, ast.ExceptHandler) and node.name is not None:
        return (node.name,)
    if isinstance(node, ast.alias) and node.asname is not None:
        return (node.asname,)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "setattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return (node.args[1].value,)
    return ()


def _qualifier(node: ast.AST, name: str) -> str:
    """How a binding reads in its scope: the stored attribute keeps its object."""
    if isinstance(node, ast.Attribute):
        return ast.unparse(node)
    return name


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
                local = _qualifier(child, name)
                qualified = f"{prefix}.{local}" if prefix else local
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
    """Every halt the loop decides on, in source order, and all of them in it.

    A halt decided inside a round is raised as a request and written by the
    loop's one handler once the round's declared set is released; the
    exhaustion of the bound is written directly. So the sites are the
    request's constructions and the builder's calls, read together.
    """
    trees = scanned_sources()
    reads = halt_reads(trees)
    # Every reading of the request's name is a construction the loop raises
    # or the handler that writes it: one held in a name, or raised anywhere
    # else, would be a halt site outside the loop.
    requests = halt_request_reads(trees)
    assert {
        (module, scope_of(tree, node), node in called or node in handled)
        for module, tree, node, called, handled in requests
    } == {("services/organize_owner.py", LOOP, True)}, [
        (module, node.lineno) for module, _, node, _, _ in requests
    ]
    # Every reading of the halt builder is a call the loop makes: a bound
    # method held in a name, or read by ``getattr``, is a reading too, and
    # one that is not called where it is read would hide the call site.
    assert {
        (module, scope_of(tree, node), node in called)
        for module, tree, node, called in reads
    } == {("services/organize_owner.py", LOOP, True)}, [
        (module, node.lineno) for module, _, node, _ in reads
    ]
    calls = [
        (module, tree, called[node])
        for module, tree, node, called in reads
        if node in called
    ] + [
        (module, tree, called[node])
        for module, tree, node, called, _ in requests
        if node in called
    ]
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
            "request.cause",
            None,
            "except _HaltRequestError",
        ),
        (
            "StageHaltCause.CONVERGENCE_EXHAUSTED",
            "convergence",
            "else of for range(self._policy.max_convergence_rounds)",
        ),
    ]
    # The three causes the loop raises are the loop's to report and nobody
    # else's. STAGE_INCOMPLETE is deliberately absent: the barrier reports
    # it too, through the one report builder both it and the loop call.
    named = cause_uses(trees, {module: module_namespace(module) for module in trees})
    assert named == {("services/organize_owner.py", LOOP)}, named


def halt_reads(
    trees: dict[str, ast.Module],
) -> list[tuple[str, ast.Module, ast.AST, dict[ast.AST, ast.Call]]]:
    """Every reading of ``_halt``: an attribute read, or ``getattr`` by name.

    Each comes with the map from a read to the call it is the callee of, so
    a caller can tell a call from a bound method held for later.
    """
    found = []
    for module, tree in trees.items():
        called = {
            node.func: node for node in ast.walk(tree) if isinstance(node, ast.Call)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == HALT
                and isinstance(node.ctx, ast.Load)
            ) or (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == HALT
            ):
                found.append((module, tree, node, called))
    return found


def halt_request_reads(
    trees: dict[str, ast.Module],
) -> list[tuple[str, ast.Module, ast.AST, dict[ast.AST, ast.Call], frozenset[ast.AST]]]:
    """Every reading of the halt request's name, with what it is read for.

    Each comes with the map from a read to the call it is the callee of and
    the set of names ``except`` handlers catch, so a caller can tell a raise
    site and the one handler from a reading held for later. The class's own
    definition binds the name and reads nothing, so it is not here.
    """
    found = []
    for module, tree in trees.items():
        called = {
            node.func: node for node in ast.walk(tree) if isinstance(node, ast.Call)
        }
        handled = frozenset(
            node.type
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler) and node.type is not None
        )
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Name)
                and node.id == HALT_REQUEST
                and isinstance(node.ctx, ast.Load)
            ):
                found.append((module, tree, node, called, handled))
    return found


def module_namespace(module: str) -> Mapping[str, object]:
    """The namespace of the shipped module at *module*, after import."""
    parts = Path(module).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return vars(importlib.import_module(".".join(("kodezart", *parts))))


class _Resolver:
    """The names of one module, read as the objects they are bound to.

    A name is read from the module's namespace after import, then from the
    builtins. A name bound only inside a function — a local assignment or a
    ``:=`` target — is read as the object its bound expression resolves to,
    wherever in the module that binding sits. An attribute is read with
    ``inspect.getattr_static`` on the object its value resolves to.
    """

    def __init__(self, namespace: Mapping[str, object], tree: ast.AST) -> None:
        self._namespace = namespace
        self._locals: dict[str, object] = {}
        bindings = [
            (target.id, value)
            for node in ast.walk(tree)
            for target, value in _simple_bindings(node)
            if target.id not in namespace
        ]
        # Each pass can only resolve a binding the last pass left open, so
        # the passes are bounded by the number of bindings.
        for _ in range(len(bindings) + 1):
            opened = {
                name: value
                for name, bound in bindings
                if name not in self._locals
                and (value := self.object_of(bound)) is not UNRESOLVED
            }
            if not opened:
                break
            self._locals.update(opened)

    def object_of(self, node: ast.AST) -> object:
        if isinstance(node, ast.Name):
            if node.id in self._locals:
                return self._locals[node.id]
            if node.id in self._namespace:
                return self._namespace[node.id]
            return vars(builtins).get(node.id, UNRESOLVED)
        if isinstance(node, ast.Attribute):
            base = self.object_of(node.value)
            if base is UNRESOLVED:
                return UNRESOLVED
            try:
                return inspect.getattr_static(base, node.attr)
            except AttributeError:
                return UNRESOLVED
        return UNRESOLVED


def _simple_bindings(node: ast.AST) -> list[tuple[ast.Name, ast.expr]]:
    """The names *node* binds to one whole expression: ``x = e``, ``x := e``."""
    if isinstance(node, ast.Assign):
        return [(t, node.value) for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        if isinstance(node.target, ast.Name):
            return [(node.target, node.value)]
    if isinstance(node, ast.NamedExpr):
        return [(node.target, node.value)]
    return []


def _is_raised_value(value: object) -> bool:
    """Whether *value* equals a raised cause's value, whatever object holds it."""
    return isinstance(value, str) and value in RAISED_VALUES


def _reaches_cause(annotation: object, seen: set[int]) -> bool:
    """Whether a declared type reaches ``StageHaltCause``, through models too."""
    # The enum itself, or one of its members named in a ``Literal[...]``.
    if annotation is StageHaltCause or isinstance(annotation, StageHaltCause):
        return True
    if id(annotation) in seen:
        return False
    seen.add(id(annotation))
    if isinstance(annotation, TypeAliasType):
        return _reaches_cause(annotation.__value__, seen)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return any(
            _reaches_cause(field.annotation, seen)
            for field in annotation.model_fields.values()
        )
    return any(_reaches_cause(arg, seen) for arg in get_args(annotation))


def _carries_literal_cause(resolver: _Resolver, node: ast.AST) -> bool:
    """Whether a ``"cause"`` entry anywhere in *node* is a literal raised cause."""
    for inner in ast.walk(node):
        if isinstance(inner, ast.Dict):
            for key, value in zip(inner.keys, inner.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "cause"
                    and _is_literal_cause(resolver, value)
                ):
                    return True
    return False


def _is_literal_cause(resolver: _Resolver, node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return _is_raised_value(node.value)
    return _is_raised_value(resolver.object_of(node))


def _builds_report(resolver: _Resolver, node: ast.AST) -> bool:
    """A model whose declared fields reach the causes, validated with a literal one.

    ``M.model_validate({...})``, ``M.model_construct(...)``,
    ``TypeAdapter(M).validate_python({...})`` and ``x.model_copy(update=...)``.
    """
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    method = node.func.attr
    carried = [*node.args, *(k.value for k in node.keywords if k.arg != "cause")]
    literal = any(_carries_literal_cause(resolver, arg) for arg in carried) or any(
        k.arg == "cause" and _is_literal_cause(resolver, k.value) for k in node.keywords
    )
    if not literal:
        return False
    if method == "model_copy":
        return True
    owner = node.func.value
    if method == "validate_python" and isinstance(owner, ast.Call) and owner.args:
        if resolver.object_of(owner.func) is TypeAdapter:
            owner = owner.args[0]
    elif method not in MODEL_BUILDERS:
        return False
    return _reaches_cause(resolver.object_of(owner), set())


def _names_raised_cause(resolver: _Resolver, node: ast.AST) -> bool:
    """A raised cause's member, a value call, or a lookup of it by a literal name."""
    if isinstance(node, ast.Attribute):
        return any(resolver.object_of(node) is cause for cause in RAISED)
    if isinstance(node, ast.Call):
        if resolver.object_of(node.func) is StageHaltCause:
            return True
        return (
            resolver.object_of(node.func) is getattr
            and len(node.args) >= 2
            and resolver.object_of(node.args[0]) is StageHaltCause
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in RAISED_NAMES
        )
    if isinstance(node, ast.Subscript):
        return (
            resolver.object_of(node.value) is StageHaltCause
            and isinstance(node.slice, ast.Constant)
            and node.slice.value in RAISED_NAMES
        )
    return False


def _declares_another_enum_member(
    namespace: Mapping[str, object], tree: ast.AST, node: ast.AST
) -> bool:
    """Whether *node* is the value of another enum's own member declaration.

    Read by object: the enclosing class, resolved in the module's namespace,
    is an enum other than the causes, and the assigned name is one of its
    members, whose value is this constant.
    """
    parents = _parents(tree)
    assign = parents.get(node)
    if not (
        isinstance(assign, ast.Assign)
        and assign.value is node
        and len(assign.targets) == 1
        and isinstance(assign.targets[0], ast.Name)
    ):
        return False
    body = parents.get(assign)
    if not isinstance(body, ast.ClassDef):
        return False
    owner: object = namespace
    for part in scope_of(tree, body).split("."):
        try:
            owner = (
                owner[part]
                if isinstance(owner, Mapping)
                else inspect.getattr_static(owner, part)
            )
        except (KeyError, AttributeError):
            return False
    if not isinstance(owner, enum.EnumType) or owner is StageHaltCause:
        return False
    member = owner.__members__.get(assign.targets[0].id)
    return member is not None and member.value == getattr(node, "value", None)


def cause_uses(
    trees: dict[str, ast.Module],
    namespaces: Mapping[str, Mapping[str, object]],
) -> set[tuple[str, str]]:
    """Every naming of a raised cause, as the module and scope it sits in.

    By object: a member of the causes however the enum is reached, a value
    call, a ``getattr`` or subscript lookup with a literal member name, a
    report model validated from a literal cause, and, outside the causes
    module, a string constant equal to a raised cause's value that is not
    another enum's own member declaration. In the causes module a member
    named inside a ``Literal[...]`` annotation of a field is a declaration,
    not a use; anywhere else in that module it is a use.
    """
    resolvers = {
        module: _Resolver(namespaces[module], tree) for module, tree in trees.items()
    }
    members = {
        (module, scope_of(tree, node))
        for module, tree in trees.items()
        for node in ast.walk(tree)
        if _names_raised_cause(resolvers[module], node)
        and not (module == CAUSES and _in_literal(tree, node))
    }
    reports = {
        (module, scope_of(tree, node))
        for module, tree in trees.items()
        for node in ast.walk(tree)
        if _builds_report(resolvers[module], node)
    }
    values = {
        (module, scope_of(tree, node))
        for module, tree in trees.items()
        if module != CAUSES
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and _is_raised_value(node.value)
        and not _declares_another_enum_member(namespaces[module], tree, node)
    }
    return members | reports | values


def _planted_uses(source: str, module: str, tmp_path: Path) -> set[tuple[str, str]]:
    """The cause scan over one planted module, resolved after importing it."""
    path = tmp_path / "planted_module.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("planted_module", path)
    assert spec is not None and spec.loader is not None
    planted = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(planted)
    return cause_uses({module: ast.parse(source)}, {module: vars(planted)})


PLANTED = "services/planted.py"
IMPORT_CAUSE = "from kodezart.types.domain.organize_owner import StageHaltCause\n\n\n"
IMPORT_REPORT = (
    "from pydantic import TypeAdapter\n\n"
    "from kodezart.types.domain.organize import RefusalKind\n"
    "from kodezart.types.domain.organize_owner import (\n"
    "    ConvergenceExhaustedHalt,\n    StageHaltReport,\n)\n\n\n"
)

#: One control per way the cause scan claims to see a raised cause named:
#: the planted module, its source, and the scope the naming is reported in.
CAUSE_CONTROLS = (
    (
        "the member",
        PLANTED,
        IMPORT_CAUSE + "def f():\n    return StageHaltCause.HUMAN_DECISION\n",
        "f",
    ),
    (
        "an import alias",
        PLANTED,
        "from kodezart.types.domain.organize_owner import StageHaltCause as _Cause\n"
        "\n_AGAIN = _Cause.CONVERGENCE_EXHAUSTED\n",
        "",
    ),
    (
        "a module-level alias",
        PLANTED,
        IMPORT_CAUSE + "_C = StageHaltCause\n\n\n"
        "def f():\n    return _C.ADMISSION_EXHAUSTED\n",
        "f",
    ),
    (
        "a local alias",
        PLANTED,
        IMPORT_CAUSE + "def f():\n    cause = StageHaltCause\n"
        "    return cause.HUMAN_DECISION\n",
        "f",
    ),
    (
        "a walrus alias",
        PLANTED,
        IMPORT_CAUSE + "def f():\n    if (cause := StageHaltCause) is not None:\n"
        "        return cause.HUMAN_DECISION\n",
        "f",
    ),
    (
        "a qualified module path",
        PLANTED,
        "import kodezart.types.domain.organize_owner as halts\n\n\n"
        "def f():\n    return halts.StageHaltCause.CONVERGENCE_EXHAUSTED\n",
        "f",
    ),
    (
        "a value call",
        PLANTED,
        IMPORT_CAUSE + "def f(raw):\n    return StageHaltCause(raw)\n",
        "f",
    ),
    (
        "a getattr with a literal member name",
        PLANTED,
        IMPORT_CAUSE
        + "def f():\n    return getattr(StageHaltCause, 'HUMAN_DECISION')\n",
        "f",
    ),
    (
        "a subscript with a literal member name",
        PLANTED,
        IMPORT_CAUSE + "def f():\n    return StageHaltCause['ADMISSION_EXHAUSTED']\n",
        "f",
    ),
    (
        "a string constant equal to a raised value",
        PLANTED,
        "def f():\n    return 'convergence_exhausted'\n",
        "f",
    ),
    (
        "a report validated from a mapping with a literal cause",
        PLANTED,
        IMPORT_REPORT + "def f():\n    return StageHaltReport.model_validate(\n"
        "        {'cause': RefusalKind.HUMAN_DECISION}\n    )\n",
        "f",
    ),
    (
        "a variant constructed with a literal cause",
        PLANTED,
        IMPORT_REPORT
        + "def f():\n    return ConvergenceExhaustedHalt.model_construct(\n"
        "        cause=RefusalKind.HUMAN_DECISION\n    )\n",
        "f",
    ),
    (
        "a report copied with a literal cause",
        PLANTED,
        IMPORT_REPORT + "def f(report):\n"
        "    return report.model_copy(update={'cause': RefusalKind.HUMAN_DECISION})\n",
        "f",
    ),
    (
        "a report validated through a type adapter",
        PLANTED,
        IMPORT_REPORT
        + "def f():\n    return TypeAdapter(StageHaltReport).validate_python(\n"
        "        {'cause': RefusalKind.HUMAN_DECISION}\n    )\n",
        "f",
    ),
    (
        "a member named in the causes module outside an annotation",
        CAUSES,
        IMPORT_CAUSE + "def f():\n"
        "    return X(cause=StageHaltCause.CONVERGENCE_EXHAUSTED)\n",
        "f",
    ),
    (
        "a Literal built as a value in the causes module",
        CAUSES,
        "from typing import Literal, get_args\n\n" + IMPORT_CAUSE + "def f():\n"
        "    return get_args(Literal[StageHaltCause.CONVERGENCE_EXHAUSTED])[0]\n",
        "f",
    ),
    (
        "a plain class attribute equal to a raised value",
        PLANTED,
        "class Kind:\n    HUMAN_DECISION = 'human_decision'\n",
        "Kind",
    ),
)


@pytest.mark.parametrize(
    ("module", "source", "scope"),
    [(module, source, scope) for _, module, source, scope in CAUSE_CONTROLS],
    ids=[shape for shape, _, _, _ in CAUSE_CONTROLS],
)
def test_the_cause_scan_sees_each_naming(
    module: str, source: str, scope: str, tmp_path: Path
) -> None:
    assert _planted_uses(source, module, tmp_path) == {(module, scope)}


#: What the scan exempts, and the stated limit it does not reach: each reads
#: as no naming of a raised cause at all.
UNSEEN_CAUSES = (
    (
        "a Literal annotation of a field in the causes module",
        CAUSES,
        "from typing import Literal\n\n" + IMPORT_CAUSE + "class Halt:\n"
        "    cause: Literal[StageHaltCause.CONVERGENCE_EXHAUSTED]\n",
    ),
    (
        "a raised value declared in the causes module",
        CAUSES,
        "import enum\n\n\nclass Causes(enum.StrEnum):\n"
        "    HUMAN_DECISION = 'human_decision'\n",
    ),
    (
        "another enum's own member declaration",
        PLANTED,
        "import enum\n\n\nclass Kind(enum.StrEnum):\n"
        "    HUMAN_DECISION = 'human_decision'\n",
    ),
    (
        "a value handed across a function boundary",
        PLANTED,
        IMPORT_REPORT + "def f(make):\n"
        "    return StageHaltReport.model_validate({'cause': make()})\n",
    ),
    (
        "a name built at run time",
        PLANTED,
        IMPORT_CAUSE + "def f():\n"
        "    member = getattr(StageHaltCause, 'HUMAN_' + 'DECISION')\n"
        "    return member, 'human_' + 'decision'\n",
    ),
    (
        "a binding made only when a function runs",
        PLANTED,
        IMPORT_CAUSE + "def f():\n    globals()['_C'] = StageHaltCause\n\n\n"
        "def g():\n    return _C.HUMAN_DECISION\n",
    ),
)


@pytest.mark.parametrize(
    ("module", "source"),
    [(module, source) for _, module, source in UNSEEN_CAUSES],
    ids=[shape for shape, _, _ in UNSEEN_CAUSES],
)
def test_the_cause_scan_exempts_declarations_and_does_not_reach_the_limit(
    module: str, source: str, tmp_path: Path
) -> None:
    assert _planted_uses(source, module, tmp_path) == set()


def test_the_convergence_halt_holds_no_bound_method_elsewhere() -> None:
    """The control for the halt reading: a held bound method is a reading."""
    source = (
        "class OrganizeOwner:\n"
        "    async def _converge(self):\n"
        "        await self._halt(cause=None)\n"
        "\n"
        "    async def run(self):\n"
        "        halt_now = self._halt\n"
        "        return getattr(self, '_halt')\n"
    )
    tree = ast.parse(source)
    assert [
        (scope_of(tree, node), node in called, node.lineno)
        for _, _, node, called in sorted(
            halt_reads({"planted.py": tree}), key=lambda read: read[2].lineno
        )
    ] == [
        ("OrganizeOwner._converge", True, 3),
        ("OrganizeOwner.run", False, 6),
        ("OrganizeOwner.run", False, 7),
    ]


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _in_literal(tree: ast.AST, target: ast.AST) -> bool:
    """Whether *target* sits in a ``Literal[...]`` that annotates a field.

    Only the annotation of an annotated assignment counts: the same
    ``Literal[...]`` built as a value, or in a function's signature, is a
    naming of the cause like any other. The ``Literal`` is read by its
    spelling, so a respelled one is reported, never exempted.
    """
    parents = _parents(tree)
    node = target
    in_literal = False
    while node in parents:
        parent = parents[node]
        if (
            isinstance(parent, ast.Subscript)
            and isinstance(parent.value, ast.Name)
            and parent.value.id == "Literal"
        ):
            in_literal = True
        if isinstance(parent, ast.AnnAssign):
            return in_literal and node is parent.annotation
        node = parent
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


#: One control per binding form the collector claims to see: the name, the
#: planted source, and the lines its sites sit on. Hand-written, because the
#: shipped tree has no duplicate to draw them from — which is the whole point
#: of the guard.
CONTROLS = (
    (
        "a second top-level definition",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\ndef organize_gap():\n    ...\n",
        [1, 5],
    ),
    (
        "a re-definition inside a conditional",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\nif True:\n    def organize_gap():\n"
        "        ...\n",
        [1, 6],
    ),
    (
        "a module-level assignment",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\norganize_gap = lambda **kwargs: ()\n",
        [1, 5],
    ),
    (
        "a functional enum assignment",
        "DefectRole",
        "import enum\n\n\nclass DefectRole(enum.StrEnum):\n    INSTANCE = 'instance'\n"
        "\n\nDefectRole = enum.StrEnum('DefectRole', {'INSTANCE': 'instance'})\n",
        [4, 8],
    ),
    (
        "a class nested in a function",
        "DefectRole",
        "class DefectRole:\n    ...\n\n\ndef _legacy_roles():\n"
        "    class DefectRole:\n        INSTANCE = 'instance'\n",
        [1, 6],
    ),
    (
        "a class nested in a class",
        "SpecFinding",
        "class SpecFinding:\n    ...\n\n\nclass _Shadow:\n"
        "    class SpecFinding(SpecFinding):\n        pass\n",
        [1, 6],
    ),
    (
        "an annotated assignment",
        "ConvergenceExhaustedHalt",
        "class ConvergenceExhaustedHalt:\n    ...\n\n\n"
        "ConvergenceExhaustedHalt: type = dict\n",
        [1, 5],
    ),
    (
        "an augmented assignment",
        "organize_gap",
        "organize_gap = ()\norganize_gap += ()\n",
        [1, 2],
    ),
    (
        "a type alias",
        "StageHaltReport",
        "class StageHaltReport:\n    ...\n\n\ntype StageHaltReport = dict\n",
        [1, 5],
    ),
    (
        "a tuple-unpacking assignment",
        "SpecFinding",
        "class SpecFinding:\n    ...\n\n\nSpecFinding, _x = object, object\n",
        [1, 5],
    ),
    (
        "a list-unpacking assignment",
        "DefectRole",
        "class DefectRole:\n    ...\n\n\n[_x, DefectRole] = [object, object]\n",
        [1, 5],
    ),
    (
        "a starred and nested unpacking",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\n_x, (_y, *organize_gap) = 1, (2, 3)\n",
        [1, 5],
    ),
    (
        "a walrus target",
        "DefectRole",
        "class DefectRole:\n    ...\n\n\nif (DefectRole := None) is None:\n    pass\n",
        [1, 5],
    ),
    (
        "a for target",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\nfor organize_gap in ():\n    pass\n",
        [1, 5],
    ),
    (
        "a comprehension target",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\n"
        "_x = [organize_gap for organize_gap in ()]\n",
        [1, 5],
    ),
    (
        "a with target",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\nwith open('x') as organize_gap:\n    pass\n",
        [1, 5],
    ),
    (
        "an except target",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\ntry:\n    pass\n"
        "except Exception as organize_gap:\n    pass\n",
        [1, 7],
    ),
    (
        "an import as",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\nimport os as organize_gap\n",
        [1, 5],
    ),
    (
        "a from-import as",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\n"
        "from kodezart.domain.scope_approval import scope_carries as organize_gap\n",
        [1, 5],
    ),
    (
        "an attribute store",
        "_converge",
        "class OrganizeOwner:\n    def _converge(self):\n        ...\n\n\n"
        "OrganizeOwner._converge = print\n",
        [2, 6],
    ),
    (
        "a setattr with a literal name",
        "_converge",
        "class OrganizeOwner:\n    def _converge(self):\n        ...\n\n\n"
        "setattr(OrganizeOwner, '_converge', print)\n",
        [2, 6],
    ),
    (
        "a setattr with a literal name inside a function body",
        "_converge",
        "class OrganizeOwner:\n    def _converge(self):\n        ...\n\n\n"
        "def _install():\n    setattr(OrganizeOwner, '_converge', print)\n",
        [2, 7],
    ),
    (
        "a globals() store under a literal key",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\nglobals()['organize_gap'] = print\n",
        [1, 5],
    ),
    (
        "a vars() store under a literal key",
        "_converge",
        "class OrganizeOwner:\n    def _converge(self):\n        ...\n\n\n"
        "vars(OrganizeOwner)['_converge'] = print\n",
        [2, 6],
    ),
    (
        "a __dict__ store under a literal key",
        "_converge",
        "class OrganizeOwner:\n    def _converge(self):\n        ...\n\n\n"
        "OrganizeOwner.__dict__['_converge'] = print\n",
        [2, 6],
    ),
)


@pytest.mark.parametrize(
    ("shape", "name", "source", "lines"),
    CONTROLS,
    ids=[shape for shape, _, _, _ in CONTROLS],
)
def test_the_detector_sees_each_duplicate_shape(
    shape: str, name: str, source: str, lines: list[int]
) -> None:
    sites = definition_sites({"planted.py": ast.parse(source)})
    assert sites[name] == [f"planted.py:{line}" for line in lines], sites


#: One source per shape the docstring states is outside the guard's reach.
#: Each binds a consumed name at run time, and the guard does not see it.
LIMITS = (
    (
        "a value handed across a function boundary",
        "def _install(namespace, bindings):\n    namespace.update(bindings)\n\n\n"
        "_install(globals(), {'organize_gap': print})\n",
    ),
    (
        "a name built at run time",
        "setattr(OrganizeOwner, '_con' + 'verge', print)\n"
        "globals()['organize' + '_gap'] = print\n",
    ),
    (
        "a binding made only when a function runs",
        "def _late(name, value):\n    globals()[name] = value\n"
        "    setattr(OrganizeOwner, name, value)\n\n\n_late('organize_gap', print)\n"
        "_late('_converge', print)\n",
    ),
)


@pytest.mark.parametrize(
    ("shape", "source"), LIMITS, ids=[shape for shape, _ in LIMITS]
)
def test_the_stated_limit_is_unseen(shape: str, source: str) -> None:
    sites = definition_sites({"planted.py": ast.parse(source)})
    assert sites["organize_gap"] == [], sites
    assert sites["_converge"] == [], sites


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
