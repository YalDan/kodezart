"""The gate that admits a member names no principal and no configured member.

The pinned interim reading is that the gate's member is settable by anybody:
the predicate asks whether the scope carries the row's configured member and
nothing about who put it there. That is an absence, so it is pinned as one —
over a surface the code derives rather than a list kept by hand, because a
hand-listed scan stops speaking for a module somebody adds to the path.

The surface is the predicate's own reach: start at the two methods that decide
admission, follow every module-level function they call that resolves into the
service or domain packages, and repeat. Types are not followed — a predicate
that raises a domain error has not made a decision in that error's module —
which is what keeps the surface the decisions and not the whole tree.
"""

import ast
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

#: Every way a principal or a setter role could be named.
FORBIDDEN = frozenset(
    {"PrincipalRole", "APPROVER", "approver", "principal", "principals", "setter"}
)


def _module_path(module: str) -> Path:
    return SOURCE / Path(*module.split(".")[1:]).with_suffix(".py")


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imported_names(tree: ast.Module) -> dict[str, str]:
    """Local name -> the module it was imported from, for this module's imports."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                names[alias.asname or alias.name] = node.module
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


def _is_function(module: str, name: str) -> bool:
    """Whether *name* is a function of *module*, rather than a type it declares."""
    path = _module_path(module)
    if not path.exists():
        return False
    node = _functions(_tree(path)).get(name)
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))


def gate_path_modules() -> list[str]:
    """The modules the admission decision actually reaches, derived.

    Returned as repository-relative paths under ``src/kodezart``, in the order
    the frontier reached them, so a failure names the module it found.
    """
    frontier = [("kodezart.services.organize_owner", name) for _, name in ROOTS]
    seen_modules = ["kodezart.services.organize_owner"]
    visited: set[tuple[str, str]] = set()
    while frontier:
        module, qualified = frontier.pop(0)
        if (module, qualified) in visited:
            continue
        visited.add((module, qualified))
        tree = _tree(_module_path(module))
        definition = _functions(tree).get(qualified)
        if definition is None:
            continue
        imports = _imported_names(tree)
        for node in ast.walk(definition):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            called = node.func.id
            origin = imports.get(called)
            if origin is None or not origin.startswith(PACKAGES):
                continue
            if not _is_function(origin, called):
                # A type the decision names, not a decision it delegates to.
                continue
            if origin not in seen_modules:
                seen_modules.append(origin)
            frontier.append((origin, called))
    return [
        _module_path(module).relative_to(SOURCE).as_posix() for module in seen_modules
    ]


def test_the_derivation_reaches_the_decisions_and_not_the_whole_tree() -> None:
    """The control for the derivation itself, both ways.

    The three modules that decide admission are reached. Two modules that
    legitimately name an approver are not, and both are named here rather
    than merely absent: a scan over the whole tree would report the prompt
    binding that renders the approver and the error prose that names one, so
    a guard written that way would be red on the day it landed.
    """
    reached = gate_path_modules()
    assert reached == [
        "services/organize_owner.py",
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


def _forbidden_in(tree: ast.Module, *, label: str) -> list[str]:
    """Every site in *tree* that names a principal, a role or a setter."""
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN:
            sites.append(f"{label}:{node.lineno}: .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN:
            sites.append(f"{label}:{node.lineno}: {node.id}")
        elif isinstance(node, ast.keyword) and node.arg in FORBIDDEN:
            sites.append(f"{label}:{node.lineno}: {node.arg}=")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            words = {word.strip(".,:;'\"") for word in node.value.split()}
            sites.extend(
                f"{label}:{node.lineno}: {word!r}" for word in sorted(words & FORBIDDEN)
            )
    return sites


def test_the_dispatch_predicate_names_no_principal_or_role() -> None:
    offenders = {
        module: sites
        for module in gate_path_modules()
        if (sites := _forbidden_in(_tree(SOURCE / module), label=module))
    }
    assert offenders == {}


#: One control per shape a role could hide in, each with the name it hides.
#: A word's plural is the same word to the detector as its singular, so the
#: singular's control witnesses both.
ROLE_CONTROLS = (
    ("role-attribute", "PrincipalRole", "gate = PrincipalRole.APPROVER in roles\n"),
    ("attribute", "approver", "gate = operation.approver is not None\n"),
    ("keyword", "principal", "gate = admitted(principal=caller)\n"),
    ("string", "approver", 'gate = row.get("approver") is None\n'),
    ("field-name", "setter", 'gate = row.get("setter") is None\n'),
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
    assert any(name in site for site in sites), sites


def test_every_forbidden_name_has_a_control() -> None:
    """A name the scan carries with no control is a name it could stop seeing."""
    witnessed = {
        site.split(": ")[1].strip(".='\"")
        for _, _, source in ROLE_CONTROLS
        for site in _forbidden_in(ast.parse(source), label="control")
    }
    assert FORBIDDEN - witnessed == {"principals"}


def _scope_members_named(tree: ast.Module, *, label: str) -> list[str]:
    """Every ``ScopeLabel.<MEMBER>`` attribute access in *tree*."""
    return [
        f"{label}:{node.lineno}: {node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == ScopeLabel.__name__
        and node.attr in {member.name for member in ScopeLabel}
    ]


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


def test_the_detector_sees_a_planted_member() -> None:
    planted = "gate = ScopeLabel.TRIAGE in members\n"
    assert _scope_members_named(ast.parse(planted), label="control") == [
        f"control:1: {ScopeLabel.TRIAGE.name}"
    ]
