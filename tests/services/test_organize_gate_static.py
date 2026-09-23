"""The gate that admits a member names no principal and no configured member.

The pinned interim reading is that the gate's member is settable by anybody:
the predicate asks whether the scope carries the row's configured member and
nothing about who put it there. That is an absence, so it is pinned as one —
over a surface the code derives rather than a list kept by hand, because a
hand-listed scan stops speaking for a module somebody adds to the path.

The surface is the predicate's own reach: start at the two methods that decide
admission and follow every call they make that resolves to a function — a
bare name imported from the service or domain packages, a sibling method
called on ``self``, a function called through an imported module, an import
under an ``as`` alias, and a module-level function of the types package — and
repeat. The types package's classes are not followed: a predicate that raises
a domain error has not made a decision in that error's module, which is what
keeps the surface the decisions and not the whole tree.
"""

import ast
import re
from pathlib import Path

import pytest

from kodezart.types.domain.operation import ScopeLabel

SOURCE = Path(__file__).resolve().parents[2] / "src" / "kodezart"
#: Where the walk starts: the one admission predicate and the one gate reading.
ROOTS = (
    ("services/organize_owner.py", "OrganizeOwner._admitted"),
    ("services/organize_owner.py", "OrganizeOwner._carried_members"),
)
#: The packages a decision on this path may live in.
PACKAGES = ("kodezart.services.", "kodezart.domain.")
#: The package whose module-level functions are followed, and not its classes.
TYPES = "kodezart.types."

#: Every word stem a principal or a setter role could be named by. An
#: identifier or a string is split into words — on underscores, on case
#: boundaries and on anything that is not a letter or digit — and a word that
#: starts with a stem is a hit, so ``approvers``, ``gate_approver_role`` and
#: ``"Approver-only gate"`` are each seen.
STEMS = ("approver", "principal", "setter")
#: The role type itself, named in full.
ROLE_TYPE = "PrincipalRole"


def _module_path(module: str, source: Path = SOURCE) -> Path:
    return source / Path(*module.split(".")[1:]).with_suffix(".py")


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imported_names(tree: ast.Module) -> dict[str, tuple[str, str]]:
    """Local name -> the module and the name it was imported as.

    ``from m import f as g`` binds ``g`` to ``(m, f)``; ``import m.n as x``
    binds ``x`` to ``(m.n, "")``, a module rather than a name in one.
    """
    names: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                names[alias.asname or alias.name] = (node.module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname is not None:
                    names[alias.asname] = (alias.name, "")
    return names


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


def _is_function(module: str, name: str, source: Path = SOURCE) -> bool:
    """Whether *name* is a function of *module*, rather than a type it declares."""
    path = _module_path(module, source)
    if not path.exists():
        return False
    node = _functions(_tree(path)).get(name)
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))


def _module_of(target: tuple[str, str], source: Path) -> str | None:
    """The module an imported name refers to, when it is a module at all."""
    module, name = target
    candidate = f"{module}.{name}" if name else module
    return candidate if _module_path(candidate, source).exists() else None


def _callee(
    call: ast.Call,
    *,
    module: str,
    qualified: str,
    imports: dict[str, tuple[str, str]],
    source: Path,
) -> tuple[str, str] | None:
    """The module and qualified name a call resolves to, if the walk follows it."""
    func = call.func
    if isinstance(func, ast.Name):
        target = imports.get(func.id)
        if target is None or not target[1]:
            return None
        origin, name = target
        if origin.startswith(PACKAGES) or origin.startswith(TYPES):
            return origin, name
        return None
    if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
        return None
    owner = func.value.id
    if owner == "self" and "." in qualified:
        return module, f"{qualified.split('.')[0]}.{func.attr}"
    target = imports.get(owner)
    if target is None:
        return None
    origin = _module_of(target, source)
    if origin is not None and origin.startswith((*PACKAGES, TYPES)):
        return origin, func.attr
    return None


def gate_path_modules(
    *, source: Path = SOURCE, roots: tuple[tuple[str, str], ...] = ROOTS
) -> list[str]:
    """The modules the admission decision actually reaches, derived.

    Returned as repository-relative paths under ``src/kodezart``, in the order
    the frontier reached them, so a failure names the module it found.
    """
    start = "kodezart." + roots[0][0].removesuffix(".py").replace("/", ".")
    frontier = [(start, name) for _, name in roots]
    seen_modules = [start]
    visited: set[tuple[str, str]] = set()
    while frontier:
        module, qualified = frontier.pop(0)
        if (module, qualified) in visited:
            continue
        visited.add((module, qualified))
        tree = _tree(_module_path(module, source))
        definition = _functions(tree).get(qualified)
        if definition is None:
            continue
        imports = _imported_names(tree)
        for node in ast.walk(definition):
            if not isinstance(node, ast.Call):
                continue
            callee = _callee(
                node,
                module=module,
                qualified=qualified,
                imports=imports,
                source=source,
            )
            if callee is None:
                continue
            origin, called = callee
            if not _is_function(origin, called, source):
                # A type the decision names, not a decision it delegates to.
                continue
            if origin not in seen_modules:
                seen_modules.append(origin)
            frontier.append((origin, called))
    return [
        _module_path(module, source).relative_to(source).as_posix()
        for module in seen_modules
    ]


def test_the_derivation_reaches_the_decisions_and_not_the_whole_tree() -> None:
    """The control for the derivation itself, both ways.

    The modules that decide admission are reached, the types module whose
    key split both roots call among them. Two modules that legitimately name
    an approver are not, and both are named here rather than merely absent:
    a scan over the whole tree would report the prompt binding that renders
    the approver and the error prose that names one, so a guard written that
    way would be red on the day it landed.
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


#: A restriction the planted decision delegates to, one module per call shape.
PLANTED_POLICY = "def restricted(operation):\n    return operation is None\n"

#: One control per call shape the walk claims to follow: the planted owner's
#: module source, and the module the walk must reach through it.
CALL_CONTROLS = (
    (
        "sibling-method",
        "from kodezart.domain.gate_policy import restricted\n\n\n"
        "class OrganizeOwner:\n"
        "    def _admitted(self):\n"
        "        return self._gate_restricted()\n\n"
        "    def _carried_members(self):\n"
        "        return None\n\n"
        "    def _gate_restricted(self):\n"
        "        return restricted(None)\n",
        "domain/gate_policy.py",
    ),
    (
        "module-qualified",
        "from kodezart.domain import gate_policy\n\n\n"
        "class OrganizeOwner:\n"
        "    def _admitted(self):\n"
        "        return gate_policy.restricted(None)\n\n"
        "    def _carried_members(self):\n"
        "        return None\n",
        "domain/gate_policy.py",
    ),
    (
        "types-function",
        "from kodezart.types.domain.gate_policy import restricted\n\n\n"
        "class OrganizeOwner:\n"
        "    def _admitted(self):\n"
        "        return restricted(None)\n\n"
        "    def _carried_members(self):\n"
        "        return None\n",
        "types/domain/gate_policy.py",
    ),
    (
        "aliased-import",
        "from kodezart.domain.gate_policy import restricted as blocked\n\n\n"
        "class OrganizeOwner:\n"
        "    def _admitted(self):\n"
        "        return blocked(None)\n\n"
        "    def _carried_members(self):\n"
        "        return None\n",
        "domain/gate_policy.py",
    ),
)


@pytest.mark.parametrize(
    ("owner", "reached"),
    [(owner, reached) for _, owner, reached in CALL_CONTROLS],
    ids=[shape for shape, _, _ in CALL_CONTROLS],
)
def test_the_derivation_follows_each_call_shape(
    tmp_path: Path, owner: str, reached: str
) -> None:
    for module, text in (
        ("services/organize_owner.py", owner),
        (reached, PLANTED_POLICY),
    ):
        path = tmp_path / module
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert gate_path_modules(source=tmp_path) == [
        "services/organize_owner.py",
        reached,
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
