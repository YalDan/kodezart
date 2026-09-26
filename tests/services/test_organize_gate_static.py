"""The gate that admits a member names no principal and no configured member.

The pinned interim reading is that the gate's member is settable by anybody:
the predicate asks whether the scope carries the row's configured member and
nothing about who put it there. That is an absence, so it is pinned as one —
over a surface the code derives rather than a list kept by hand, because a
hand-listed scan stops speaking for a module somebody adds to the path.

The surface is the predicate's own reach, derived by object after import:
start at the two methods that decide admission and follow every call that
resolves to a function defined anywhere in the ``kodezart`` package, and
repeat. A call resolves through the module's own namespace, so the walk
follows

- a bare name, whether the module defines it or imports it, under an ``as``
  alias or through a package ``__init__`` re-export;
- a function reached through an imported module, and a static or class
  method reached through an imported class (``GatePolicy.restricted(x)``);
- a sibling method called on ``self``, and one read by ``getattr`` with a
  literal name;
- the constructor and the methods of a class called on its result
  (``GatePolicy(x).blocks()``);
- a method called on, and a property read from, ``self.<attr>``, through the
  attribute's declared type: a class annotation, or the annotation of the
  ``__init__`` parameter the attribute is assigned from;
- a bound method, or a value, held in a local name.

A class the predicate only names or constructs, such as the error it raises,
is not a decision the predicate delegates to, which is what keeps the surface
the decisions and not the whole tree. A method of a port protocol is not
followed either: it is a declaration, and a decision behind it lives in an
adapter below the port, outside the dispatch predicate.

Outside every static guard's reach:

- a value handed across a function boundary, where the other function is not
  resolved at this site (returned from a helper, stored on an object and read
  elsewhere, or passed through a container built elsewhere);
- a name built at run time;
- a binding made only when a function runs (``setattr`` or ``globals()``
  inside a function body).

A parameter of the function being walked is such a value, so a method called
on one is not followed. Each shape the walk follows and each shape of the
limit has a planted control below.
"""

import ast
import builtins
import importlib
import inspect
import re
import sys
import typing
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

from kodezart.types.domain.operation import ScopeLabel

SOURCE = Path(__file__).resolve().parents[2] / "src" / "kodezart"
#: The package whose functions the walk follows, wherever in it they live.
PACKAGE = "kodezart"
#: Where the walk starts: the one admission predicate and the one gate reading.
ROOTS = (
    ("services/organize_owner.py", "OrganizeOwner._admitted"),
    ("services/organize_owner.py", "OrganizeOwner._carried_members"),
)


#: Every word stem a principal or a setter role could be named by. An
#: identifier or a string is split into words — on underscores, on case
#: boundaries and on anything that is not a letter or digit — and a word that
#: starts with a stem is a hit, so ``approvers``, ``gate_approver_role`` and
#: ``"Approver-only gate"`` are each seen.
STEMS = ("approver", "principal", "setter")
#: The role type itself, named in full.
ROLE_TYPE = "PrincipalRole"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _functions(tree: ast.Module) -> dict[str, ast.AST]:
    """Qualified name -> its definition node, for every definition in *tree*."""
    found: dict[str, ast.AST] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            here = prefix
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                here = f"{prefix}.{child.name}" if prefix else child.name
                found[here] = child
            walk(child, here)

    walk(tree, "")
    return found


@dataclass(frozen=True)
class _Instance:
    """A value of *cls*: ``self``, a declared attribute, or a constructed one."""

    cls: type
    constructed: bool = False


#: What a name the walk cannot read resolves to.
_UNREAD = object()
#: A method of a port protocol: a declaration, not a decision.
_PORT = object()


def _module_name(path: str, package: str) -> str:
    parts = Path(path).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join((package, *parts))


def _is_ours(function: object, package: str) -> bool:
    module = getattr(function, "__module__", None) or ""
    return module == package or module.startswith(f"{package}.")


def _definition(function: Callable[..., object]) -> ast.AST | None:
    """The ``def`` of *function* in its module's source, by qualified name."""
    module = sys.modules[function.__module__]
    tree = ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))
    parts = function.__qualname__.split(".")
    node: ast.AST = tree
    for part in parts:
        node = next(
            (
                child
                for child in ast.iter_child_nodes(node)
                if isinstance(
                    child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                )
                and child.name == part
            ),
            None,
        )
        if node is None:
            return None
    return node


def _owner_class(function: Callable[..., object]) -> type | None:
    """The class a method is defined in, read from its qualified name."""
    owner: object = sys.modules[function.__module__]
    parts = function.__qualname__.split(".")[:-1]
    if not parts or "<locals>" in parts:
        return None
    for part in parts:
        owner = inspect.getattr_static(owner, part, None)
    return owner if isinstance(owner, type) else None


def _is_port(cls: type) -> bool:
    return bool(getattr(cls, "_is_protocol", False))


def _classes_in(annotation: object) -> list[type]:
    """The classes a declared type names, through unions and optionals."""
    if isinstance(annotation, type):
        return [annotation]
    return [cls for arg in typing.get_args(annotation) for cls in _classes_in(arg)]


def _hints(target: object) -> dict[str, object]:
    try:
        return typing.get_type_hints(target)
    except (NameError, TypeError):
        return {}


def _declared_attributes(cls: type) -> dict[str, list[type]]:
    """Attribute -> its declared classes: class annotations, then ``__init__``.

    An ``__init__`` assignment ``self.a = p`` (or a tuple of them) gives
    ``a`` the annotation of the parameter ``p``.
    """
    declared = {name: _classes_in(hint) for name, hint in _hints(cls).items()}
    init = inspect.getattr_static(cls, "__init__", None)
    if (
        not inspect.isfunction(init)
        or init.__module__.split(".")[0] != cls.__module__.split(".")[0]
    ):
        return declared
    parameters = _hints(init)
    definition = _definition(init)
    if definition is None:
        return declared
    for node in ast.walk(definition):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            pairs = (
                zip(target.elts, node.value.elts, strict=False)
                if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple)
                else [(target, node.value)]
            )
            for stored, value in pairs:
                if (
                    isinstance(stored, ast.Attribute)
                    and isinstance(stored.value, ast.Name)
                    and stored.value.id == "self"
                    and isinstance(value, ast.Name)
                    and value.id in parameters
                ):
                    declared[stored.attr] = _classes_in(parameters[value.id])
    return declared


class _Reach:
    """What one function's body resolves to, read by object after import."""

    def __init__(self, function: Callable[..., object], package: str) -> None:
        self._package = package
        self._namespace = vars(sys.modules[function.__module__])
        self._self = _owner_class(function)
        self._locals: dict[str, object] = {}
        self.followed: list[Callable[..., object]] = []

    def bind_locals(self, definition: ast.AST) -> None:
        """Names bound in the body to one whole expression, read in turn."""
        bindings = [
            (target.id, node.value)
            for node in ast.walk(definition)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        ] + [
            (node.target.id, node.value)
            for node in ast.walk(definition)
            if isinstance(node, ast.NamedExpr)
        ]
        # Each pass can only read a binding the last one left open, so the
        # passes are bounded by the number of bindings.
        for _ in range(len(bindings) + 1):
            opened = {
                name: value
                for name, bound in bindings
                if name not in self._locals
                and (value := self.read(bound)) is not _UNREAD
            }
            if not opened:
                break
            self._locals.update(opened)

    def _attribute(self, base: object, name: str) -> object:
        if isinstance(base, _Instance):
            if base.constructed:
                self._follow(inspect.getattr_static(base.cls, "__init__", None))
            declared = _declared_attributes(base.cls).get(name)
            if declared:
                return _Instance(declared[0])
            base = base.cls
        if isinstance(base, type) and _is_port(base):
            return _PORT
        if isinstance(base, (type, ModuleType)):
            found = inspect.getattr_static(base, name, _UNREAD)
            if isinstance(found, (staticmethod, classmethod)):
                return found.__func__
            if isinstance(found, property):
                self._follow(found.fget)
            return found
        return _UNREAD

    def read(self, node: ast.AST) -> object:
        """The object *node* names, or what a value of a class it names is."""
        if isinstance(node, ast.Name):
            if node.id == "self" and self._self is not None:
                return _Instance(self._self)
            for scope in (self._locals, self._namespace, vars(builtins)):
                if node.id in scope:
                    return scope[node.id]
            return _UNREAD
        if isinstance(node, ast.Attribute):
            base = self.read(node.value)
            return _UNREAD if base is _UNREAD else self._attribute(base, node.attr)
        if isinstance(node, ast.Call):
            callee = self.read(node.func)
            if callee is getattr and len(node.args) >= 2:
                name = node.args[1]
                base = self.read(node.args[0])
                if isinstance(name, ast.Constant) and isinstance(name.value, str):
                    if base is not _UNREAD:
                        return self._attribute(base, name.value)
                return _UNREAD
            if isinstance(callee, type):
                return _Instance(callee, constructed=True)
            return _UNREAD
        return _UNREAD

    def _follow(self, target: object) -> None:
        if inspect.isfunction(target) and _is_ours(target, self._package):
            self.followed.append(target)

    def calls(self, definition: ast.AST) -> list[Callable[..., object]]:
        """Every function of the package this body calls or reads a property of."""
        for node in ast.walk(definition):
            if isinstance(node, ast.Call):
                self._follow(self.read(node.func))
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                self.read(node)
        return self.followed


def gate_path_modules(
    *,
    source: Path = SOURCE,
    roots: tuple[tuple[str, str], ...] = ROOTS,
    package: str = PACKAGE,
) -> list[str]:
    """The modules the admission decision actually reaches, derived by object.

    Returned as paths under the package's source directory, in the order the
    frontier reached them, so a failure names the module it found.
    """
    frontier: list[Callable[..., object]] = []
    for path, qualified in roots:
        owner: object = importlib.import_module(_module_name(path, package))
        for part in qualified.split("."):
            owner = inspect.getattr_static(owner, part)
        assert inspect.isfunction(owner), qualified
        frontier.append(owner)
    seen_modules: list[str] = []
    visited: set[Callable[..., object]] = set()
    while frontier:
        function = frontier.pop(0)
        if function in visited:
            continue
        visited.add(function)
        if function.__module__ not in seen_modules:
            seen_modules.append(function.__module__)
        definition = _definition(function)
        if definition is None:
            continue
        reach = _Reach(function, package)
        reach.bind_locals(definition)
        frontier.extend(reach.calls(definition))
    return [
        Path(inspect.getfile(sys.modules[module])).relative_to(source).as_posix()
        for module in seen_modules
    ]


def test_the_derivation_reaches_the_decisions_and_not_the_whole_tree() -> None:
    """The control for the derivation itself, both ways.

    The modules that decide admission are reached, the types module whose
    key split both roots call among them. Two modules that legitimately name
    an approver are not, and both are named here rather than merely absent:
    a scan over the whole tree would report the prompt binding that renders
    the approver and the error prose that names one, so a guard written that
    way would be red on the day it landed. Nothing keeps them out but reach.
    """
    reached = gate_path_modules()
    assert reached == [
        "services/organize_owner.py",
        "types/domain/organize.py",
        "services/scope_approval.py",
        "domain/scope_approval.py",
    ], reached
    for legitimate in ("core/prompt_namespaces.py", "domain/errors.py"):
        assert legitimate not in reached
        assert _forbidden_in(_tree(SOURCE / legitimate), label=legitimate)


def test_the_derivation_starts_at_definitions_that_exist() -> None:
    """A root that stopped existing would derive an empty surface and pass."""
    functions = _functions(_tree(SOURCE / "services/organize_owner.py"))
    for _, name in ROOTS:
        assert name in functions, name


#: The planted decision every call shape below delegates to.
PLANTED_POLICY = "def restricted(operation):\n    return operation is None\n"
PLANTED_CLASS = (
    "class GatePolicy:\n"
    "    def __init__(self, operation):\n"
    "        self.operation = operation\n\n"
    "    @staticmethod\n"
    "    def restricted(operation):\n"
    "        return operation is None\n\n"
    "    @classmethod\n"
    "    def closed(cls, operation):\n"
    "        return operation is None\n\n"
    "    def blocks(self):\n"
    "        return self.operation is None\n\n"
    "    @property\n"
    "    def shut(self):\n"
    "        return self.operation is None\n"
)
PLANTED_PORT = (
    "from typing import Protocol\n\n\n"
    "class GatePort(Protocol):\n"
    "    def restricted(self) -> bool: ...\n"
)


def _owner(imports: str, admitted: str, *, init: str = "", extra: str = "") -> str:
    """A planted owner whose ``_admitted`` body is *admitted*, in a template.

    ``{pkg}`` in the text is the planted package's name.
    """
    return (
        f"{imports}\n\n\n{extra}"
        "class OrganizeOwner:\n"
        f"{init}"
        "    def _admitted(self):\n"
        f"{admitted}\n"
        "    def _carried_members(self):\n"
        "        return None\n\n"
        "    def _gate_restricted(self):\n"
        "        return restricted(None)\n"
    )


FROM_POLICY = "from {pkg}.domain.gate_policy import restricted"
FROM_CLASS = "from {pkg}.domain.gate_class import GatePolicy\n" + FROM_POLICY
POLICY_FILES = {"domain/gate_policy.py": PLANTED_POLICY}
CLASS_FILES = {**POLICY_FILES, "domain/gate_class.py": PLANTED_CLASS}
HELD = (
    "    def __init__(self, policy: GatePolicy) -> None:\n"
    "        self._policy = policy\n\n"
)

#: One control per call shape the walk claims to follow: the planted owner's
#: source, the other planted files, and the modules the walk must reach.
CALL_CONTROLS = (
    (
        "sibling-method",
        _owner(FROM_POLICY, "        return self._gate_restricted()\n"),
        POLICY_FILES,
        ["domain/gate_policy.py"],
    ),
    (
        "module-qualified",
        _owner(
            FROM_POLICY + "\nfrom {pkg}.domain import gate_policy",
            "        return gate_policy.restricted(None)\n",
        ),
        POLICY_FILES,
        ["domain/gate_policy.py"],
    ),
    (
        "types-function",
        _owner(
            "from {pkg}.types.domain.gate_policy import restricted",
            "        return restricted(None)\n",
        ),
        {"types/domain/gate_policy.py": PLANTED_POLICY},
        ["types/domain/gate_policy.py"],
    ),
    (
        "aliased-import",
        _owner(
            FROM_POLICY
            + "\nfrom {pkg}.domain.gate_policy import restricted as blocked",
            "        return blocked(None)\n",
        ),
        POLICY_FILES,
        ["domain/gate_policy.py"],
    ),
    (
        "same-module-function",
        _owner(
            FROM_POLICY,
            "        return _gate_closed(None)\n",
            extra=(
                "def _gate_closed(operation):\n    return restricted(operation)\n\n\n"
            ),
        ),
        POLICY_FILES,
        ["domain/gate_policy.py"],
    ),
    (
        "static-method",
        _owner(FROM_CLASS, "        return GatePolicy.restricted(None)\n"),
        CLASS_FILES,
        ["domain/gate_class.py"],
    ),
    (
        "class-method",
        _owner(FROM_CLASS, "        return GatePolicy.closed(None)\n"),
        CLASS_FILES,
        ["domain/gate_class.py"],
    ),
    (
        "constructed-then-called",
        _owner(FROM_CLASS, "        return GatePolicy(None).blocks()\n"),
        CLASS_FILES,
        ["domain/gate_class.py"],
    ),
    (
        "declared-attribute-method",
        _owner(FROM_CLASS, "        return self._policy.blocks()\n", init=HELD),
        CLASS_FILES,
        ["domain/gate_class.py"],
    ),
    (
        "declared-attribute-property",
        _owner(FROM_CLASS, "        return self._policy.shut\n", init=HELD),
        CLASS_FILES,
        ["domain/gate_class.py"],
    ),
    (
        "package-re-export",
        _owner(
            "from {pkg}.domain import restricted", "        return restricted(None)\n"
        ),
        {
            **POLICY_FILES,
            "domain/__init__.py": (
                "from {pkg}.domain.gate_policy import restricted as restricted\n"
            ),
        },
        ["domain/gate_policy.py"],
    ),
    (
        "another-package",
        _owner(
            "from {pkg}.core.gate_rights import restricted",
            "        return restricted(None)\n",
        ),
        {"core/gate_rights.py": PLANTED_POLICY},
        ["core/gate_rights.py"],
    ),
    (
        "bound-method-in-a-local",
        _owner(
            FROM_POLICY,
            "        check = self._gate_restricted\n        return check()\n",
        ),
        POLICY_FILES,
        ["domain/gate_policy.py"],
    ),
    (
        "getattr-with-a-literal-name",
        _owner(FROM_POLICY, "        return getattr(self, '_gate_restricted')()\n"),
        POLICY_FILES,
        ["domain/gate_policy.py"],
    ),
    # What the walk does not follow, each a shape it is handed: a port
    # protocol's method is a declaration, and a decision behind it lives in
    # an adapter below the port.
    (
        "port-protocol-method",
        _owner(
            FROM_POLICY + "\nfrom {pkg}.core.ports import GatePort",
            "        return self._port.restricted()\n",
            init=(
                "    def __init__(self, port: GatePort) -> None:\n"
                "        self._port = port\n\n"
            ),
        ),
        {**POLICY_FILES, "core/ports.py": PLANTED_PORT},
        [],
    ),
    # The stated limit, one shape each.
    (
        "limit-value-across-a-function-boundary",
        _owner(
            FROM_CLASS,
            "        return self._made().blocks()\n\n"
            "    def _made(self):\n"
            "        return GatePolicy(None)\n",
        ),
        CLASS_FILES,
        [],
    ),
    (
        "limit-name-built-at-run-time",
        _owner(
            FROM_POLICY, "        return getattr(self, '_gate_' + 'restricted')()\n"
        ),
        POLICY_FILES,
        [],
    ),
    (
        "limit-binding-made-when-a-function-runs",
        _owner(
            FROM_POLICY,
            "        setattr(type(self), '_late', staticmethod(restricted))\n"
            "        return self._late(None)\n",
        ),
        POLICY_FILES,
        [],
    ),
)


def _plant(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    """Write a planted package *name* under *tmp_path*, every directory a package."""
    package = tmp_path / name
    for path, text in files.items():
        planted = package / path
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_text(text.replace("{pkg}", name), encoding="utf-8")
    for directory in (package, *package.rglob("*")):
        if directory.is_dir() and not (directory / "__init__.py").exists():
            (directory / "__init__.py").write_text("", encoding="utf-8")
    return package


@pytest.mark.parametrize(
    ("shape", "owner", "files", "reached"),
    CALL_CONTROLS,
    ids=[shape for shape, _, _, _ in CALL_CONTROLS],
)
def test_the_derivation_follows_each_call_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
    owner: str,
    files: dict[str, str],
    reached: list[str],
) -> None:
    name = "planted_gate_" + re.sub(r"\W", "_", shape)
    source = _plant(tmp_path, name, {**files, "services/organize_owner.py": owner})
    monkeypatch.syspath_prepend(str(tmp_path))
    assert gate_path_modules(source=source, package=name) == [
        "services/organize_owner.py",
        *reached,
    ]


def _words(text: str) -> list[str]:
    """*text* split on underscores, case boundaries and non-word characters."""
    return [
        word.lower()
        for word in re.split(r"[_\W]+|(?<=[a-z0-9])(?=[A-Z])", text)
        if word
    ]


def _stems_in(text: str) -> list[str]:
    """Every forbidden stem a word of *text* starts with."""
    found = [stem for word in _words(text) for stem in STEMS if word.startswith(stem)]
    if ROLE_TYPE in text:
        found.append(ROLE_TYPE)
    return found


def _forbidden_in(tree: ast.Module, *, label: str) -> list[str]:
    """Every site in *tree* that names a principal, a role or a setter.

    A name bound by ``import ... as`` is read as the name it imports, so an
    alias does not rename a role out of sight, and the imported names are
    read themselves.
    """
    aliases = {
        alias.asname: alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        if alias.asname is not None
    }
    sites = []
    for node in ast.walk(tree):
        texts: list[str] = []
        if isinstance(node, ast.Attribute):
            texts = [node.attr]
        elif isinstance(node, ast.Name):
            texts = [aliases.get(node.id, node.id), node.id]
        elif isinstance(node, ast.keyword) and node.arg is not None:
            texts = [node.arg]
        elif isinstance(node, ast.alias):
            texts = [node.name, node.asname or ""]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            texts = [node.name]
        elif isinstance(node, ast.arg):
            texts = [node.arg]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            texts = [node.value]
        lineno = getattr(node, "lineno", 0)
        sites.extend(
            f"{label}:{lineno}: {stem} in {text!r}"
            for text in texts
            for stem in sorted(set(_stems_in(text)))
        )
    return sites


def test_the_dispatch_predicate_names_no_principal_or_role() -> None:
    offenders = {
        module: sites
        for module in gate_path_modules()
        if (sites := _forbidden_in(_tree(SOURCE / module), label=module))
    }
    assert offenders == {}


#: One control per shape a role could hide in, each with the stem it hides.
#: Every compound form is its own control: a plural, a stem inside a longer
#: identifier, a capitalised word in prose, and a role type under an alias.
ROLE_CONTROLS = (
    ("role-attribute", ROLE_TYPE, "gate = PrincipalRole.APPROVER in roles\n"),
    ("attribute", "approver", "gate = operation.approver is not None\n"),
    ("keyword", "principal", "gate = admitted(principal=caller)\n"),
    ("string", "approver", 'gate = row.get("approver") is None\n'),
    ("plural", "approver", "gate = caller in operation.approvers\n"),
    (
        "compound-identifier",
        "approver",
        "gate = operation.gate_approver_role is None\n",
    ),
    ("compound-setter", "setter", "gate = phase.setter_role is None\n"),
    ("capitalised-string", "approver", 'reason = "Approver-only gate"\n'),
    (
        "aliased-import",
        ROLE_TYPE,
        "from kodezart.types.domain.operation import PrincipalRole as Role\n\n"
        "gate = Role.ASSIGNEE in roles\n",
    ),
)


@pytest.mark.parametrize(
    ("name", "source"),
    [(name, source) for _, name, source in ROLE_CONTROLS],
    ids=[shape for shape, _, _ in ROLE_CONTROLS],
)
def test_the_detector_sees_each_shape_a_role_is_named_by(
    name: str, source: str
) -> None:
    sites = _forbidden_in(ast.parse(source), label="control")
    assert any(f": {name} in " in site for site in sites), sites


def test_an_aliased_role_is_read_as_the_name_it_imports() -> None:
    source = (
        "from kodezart.types.domain.operation import PrincipalRole as Role\n\n"
        "gate = Role.ASSIGNEE in roles\n"
    )
    sites = _forbidden_in(ast.parse(source), label="control")
    assert f"control:3: {ROLE_TYPE} in {ROLE_TYPE!r}" in sites, sites


def test_every_forbidden_name_has_a_control() -> None:
    """A stem the scan carries with no control is a stem it could stop seeing."""
    witnessed = {
        site.split(": ")[1].split(" in ")[0]
        for _, _, source in ROLE_CONTROLS
        for site in _forbidden_in(ast.parse(source), label="control")
    }
    assert witnessed == {*STEMS, ROLE_TYPE}


def _scope_members_named(tree: ast.Module, *, label: str) -> list[str]:
    """Every site in *tree* that names a scope member in the source itself.

    Four shapes: ``ScopeLabel.<MEMBER>`` attribute access, a ``ScopeLabel[...]``
    subscript on a member's name, a string constant equal to a member's value,
    and so a ``ScopeLabel(...)`` call on one — the call is seen through the
    constant it is given. Each site is reported by the member's name,
    whichever shape named it.
    """
    by_value = {member.value: member.name for member in ScopeLabel}
    by_name = {member.name for member in ScopeLabel}
    sites = []
    for node in ast.walk(tree):
        named = None
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == ScopeLabel.__name__
            and node.attr in by_name
        ):
            named = node.attr
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            named = by_value.get(node.value)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == ScopeLabel.__name__
            and isinstance(node.slice, ast.Constant)
            and node.slice.value in by_name
        ):
            named = str(node.slice.value)
        if named is not None:
            sites.append(f"{label}:{node.lineno}: {named}")
    return sites


def test_the_dispatch_path_names_no_scope_member_but_the_approval_cascade() -> None:
    """The gate's member comes from the configured key, never from the source.

    The approval member is the exception and has to be: the cascade is a
    different read from a container's own members, and the predicate has to
    know which one it is being asked for. Every other member is constructed
    from the row's configured key.
    """
    named = {
        module: sites
        for module in gate_path_modules()
        if (sites := _scope_members_named(_tree(SOURCE / module), label=module))
    }
    assert {member.split(": ")[1] for sites in named.values() for member in sites} == {
        ScopeLabel.APPROVED.name
    }, named


#: One control per shape a member could be named by in the source.
MEMBER_CONTROLS = (
    ("attribute", "gate = ScopeLabel.TRIAGE in members\n"),
    ("call", 'gate = ScopeLabel("triage") in members\n'),
    ("subscript", 'gate = ScopeLabel["TRIAGE"] in members\n'),
    ("string", 'gate = key != "triage"\n'),
)


@pytest.mark.parametrize(
    "source",
    [source for _, source in MEMBER_CONTROLS],
    ids=[shape for shape, _ in MEMBER_CONTROLS],
)
def test_the_detector_sees_a_planted_member(source: str) -> None:
    assert _scope_members_named(ast.parse(source), label="control") == [
        f"control:1: {ScopeLabel.TRIAGE.name}"
    ]
