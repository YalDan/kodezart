"""Shared static checks for explicit identity construction and model addresses."""

import ast
from collections.abc import Iterator

from tests.name_resolution import _module_routes

#: The model methods that make a value without naming its class.  A guard
#: counting only the class call would miss every one of them, and the copy
#: is the house idiom, so it is the shape a second writer would take.
BUILDING_METHODS = frozenset({"model_construct", "model_copy"})
#: The model methods that make a value out of serialized bytes or a mapping.
PARSING_METHODS = frozenset(
    {"model_validate", "model_validate_json", "model_validate_strings"}
)
#: A receiver whose type nothing states.  Held beside the types a name is
#: given so that an assignment from an unannotated source cannot excuse it:
#: silence about a receiver is not a statement that it is something else.
UNSTATED = "<unstated>"

#: The field names that address a ruling, and so may never be annotated as a
#: bare string. Stated here rather than inline in the scan below because the
#: case that proves each name is acted on parametrises over this same set: two
#: copies would mean a sixth name silently arriving with no arm behind it.
RULING_ADDRESS_NAMES = frozenset(
    {"ruling_id", "ruling_ids", "ruling_ref", "ruling_refs", "supersedes"},
)


def _constructor_names(tree: ast.AST, identity: str) -> set[str]:
    """Every local name that resolves to *identity*, aliases included."""
    constructors = {identity}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            constructors.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == identity
            )

    def names(node: ast.AST) -> bool:
        return (isinstance(node, ast.Name) and node.id in constructors) or (
            isinstance(node, ast.Attribute) and node.attr == identity
        )

    # Assignment aliases can precede their source alias in another function.
    # Resolve the finite set before counting calls; never execute source.
    changed = True
    while changed:
        previous = set(constructors)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and names(node.value):
                constructors.update(
                    target.id for target in node.targets if isinstance(target, ast.Name)
                )
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and isinstance(node.target, ast.Name)
                and names(node.value)
            ):
                constructors.add(node.target.id)
        changed = constructors != previous
    return constructors


def construction_sites(source: str, *, identity: str = "RulingId") -> tuple[int, ...]:
    constructors = _constructor_names(tree := ast.parse(source), identity)

    def is_constructor(node: ast.AST) -> bool:
        return (isinstance(node, ast.Name) and node.id in constructors) or (
            isinstance(node, ast.Attribute) and node.attr == identity
        )

    return tuple(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and is_constructor(node.func)
    )


def _parameters(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.arg, ...]:
    """Every parameter of *function*, in one sequence."""
    return tuple(
        argument
        for argument in (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
            function.args.vararg,
            function.args.kwarg,
        )
        if argument is not None
    )


def _mentions(annotation: ast.expr | None, names: set[str]) -> bool:
    """Whether *annotation* names one of *names* anywhere inside itself."""
    if annotation is None:
        return False
    return any(
        (isinstance(node, ast.Name) and node.id in names)
        or (isinstance(node, ast.Attribute) and node.attr in names)
        or (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in names
        )
        for node in ast.walk(annotation)
    )


def _carriers(tree: ast.AST, names: set[str]) -> set[str]:
    """Every name in *tree* whose own annotations hand one of *names* around.

    A function whose parameter or return names the value carries it; so does
    a class declaring a field of it, and so does the class a carrying method
    belongs to, because the class is the name another module imports.
    """
    carriers: set[str] = set()
    owners = {
        id(statement): node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        for statement in node.body
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if any(
                _mentions(annotation, names)
                for annotation in (
                    node.returns,
                    *(argument.annotation for argument in _parameters(node)),
                )
            ):
                carriers.add(node.name)
                owner = owners.get(id(node))
                if owner is not None:
                    carriers.add(owner.name)
        elif isinstance(node, ast.ClassDef) and any(
            isinstance(statement, ast.AnnAssign)
            and _mentions(statement.annotation, names)
            for statement in node.body
        ):
            carriers.add(node.name)
    return carriers


def value_holders(sources: dict[str, str], *, identity: str) -> dict[str, ast.Module]:
    """Every module that can hold one of *identity*'s values, as a fixed point.

    A module holds the value when it imports or declares the identity, or
    reaches the IDENTITY as an attribute of a module it imports.  It also
    holds the value when it imports a CARRIER — a function, method, class or
    field whose own annotation mentions the identity, or mentions a carrier —
    since a caller handed the value back holds it without ever naming its
    type.  Carriers and holders are grown together until neither changes, so
    the scanned surface is derived from the tree and never listed here.

    A carrier is counted by two routes.  By name, when a ``from`` import binds
    it.  By its module, when an import binds as a module one that declares a
    carrier or the identity at its own top level: ``import a.b [as m]`` and
    ``from a import b`` where ``a.b`` is a module of the tree, resolved by the
    shared name resolver, so ``m.AuthoredSpec.model_validate(...)`` holds the
    value whatever the local word is.

    Not counted, each a module this walk does not scan: a carrier reached as
    an attribute when the import itself routes to no carrier's module —
    ``import kodezart``, a package's ``__init__`` or a relative import, which
    the resolver maps to no module of the tree — and a carrier a module only
    re-exports by importing it, because re-exporting is not declaring.  The
    attribute leg below matches the identity's own name only; matching every
    carrier's name there would also scan modules that spell a carrier method
    as an attribute of an unrelated value.
    """
    trees = {path: ast.parse(source) for path, source in sources.items()}
    routed = {
        path: {
            name
            for home in _module_routes(tree, trees).values()
            for name in _declared(trees[home])
        }
        for path, tree in trees.items()
    }
    carried = {identity}
    holders: dict[str, ast.Module] = {}
    changed = True
    while changed:
        changed = False
        for path, tree in trees.items():
            if path not in holders and _holds(
                tree, carried, identity=identity, routed=routed[path]
            ):
                holders[path] = tree
                changed = True
        for tree in holders.values():
            grown = _carriers(tree, carried | _constructor_names(tree, identity))
            if grown - carried:
                carried |= grown
                changed = True
    return holders


def _declared(tree: ast.Module) -> set[str]:
    """The names a module declares at its own top level: what ``m.name`` reaches."""
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _holds(
    tree: ast.AST, carried: set[str], *, identity: str, routed: set[str]
) -> bool:
    """Whether *tree* can hold the value, given what its module imports declare.

    *routed* is every name declared by a module this one imports as a module;
    one of them being carried is the module route to a carrier.
    """
    return not routed.isdisjoint(carried) or any(
        (
            isinstance(node, ast.ImportFrom)
            and any(alias.name in carried for alias in node.names)
        )
        or (isinstance(node, ast.ClassDef) and node.name == identity)
        or (isinstance(node, ast.Attribute) and node.attr == identity)
        for node in ast.walk(tree)
    )


def model_value_sites(
    sources: dict[str, str], *, identity: str
) -> dict[str, tuple[str, ...]]:
    """Where the tree builds one of *identity*'s values, and where it parses one.

    Every module that can hold the value is scanned, and inside one a
    receiver is excused only where its ENCLOSING function states another
    type for it — its parameters, its own annotated assignments, its class's
    annotations for a ``self`` receiver, and what a plain assignment
    inherits from those.  An unannotated receiver is reported, because a
    guard that trusted silence would be answered by dropping the
    annotation.  Each site is named by the module and the function holding
    it, so the assertion reads as the surface rather than as line numbers.
    """
    sites: dict[str, list[str]] = {"build": [], "parse": []}
    for path, tree in sorted(value_holders(sources, identity=identity).items()):
        for form, function in _module_sites(tree, identity=identity):
            sites[form].append(f"{path}::{function}")
    return {name: tuple(sorted(found)) for name, found in sites.items()}


def _module_sites(tree: ast.Module, *, identity: str) -> list[tuple[str, str]]:
    """Each construction in one module, as its form and the function holding it."""
    constructors = _constructor_names(tree, identity)
    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    returns = {
        node.name: node.returns
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.returns is not None
    }
    # A name this module imports, or declares as a class, addresses that type:
    # the import or the declaration says what it is as plainly as an
    # annotation would, and the identity's own names are resolved before it.
    addressed = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    } | {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}

    def owner_of(node: ast.AST, kinds: type | tuple[type, ...]) -> ast.AST | None:
        while id(node) in parents:
            node = parents[id(node)]
            if isinstance(node, kinds):
                return node
        return None

    def enclosing(node: ast.AST) -> str:
        function = owner_of(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            return function.name
        return "<module>"

    def is_constructor(node: ast.AST) -> bool:
        return (isinstance(node, ast.Name) and node.id in constructors) or (
            isinstance(node, ast.Attribute) and node.attr == identity
        )

    def excused(receiver: ast.expr, node: ast.AST) -> bool:
        function = owner_of(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        stated = (
            {}
            if function is None
            else _stated_types(function, returns, owner_of(function, ast.ClassDef))
        )
        name = ast.unparse(receiver)
        types = stated.get(name)
        if types is None:
            return name in addressed
        return UNSTATED not in types and all(
            identity not in stated_type for stated_type in types
        )

    def call_form(node: ast.Call) -> str | None:
        """Which form, if any, this call makes one of the value's own by."""
        if is_constructor(node.func):
            return "build"
        if any(
            is_constructor(argument)
            for argument in (*node.args, *(word.value for word in node.keywords))
        ):
            # An adapter or a partial application built around the class makes
            # values of it from wherever the result is called.
            return "build"
        if isinstance(node.func, ast.Call) and _names(node.func.func) == "type":
            return "build"
        if not isinstance(node.func, ast.Attribute):
            return None
        if node.func.attr == "__class__":
            return "build"
        if is_constructor(node.func.value) or excused(node.func.value, node):
            # The first is already counted at the attribute itself; the second
            # is a receiver its own function states another type for.
            return None
        if node.func.attr in BUILDING_METHODS:
            return "build"
        return "parse" if node.func.attr in PARSING_METHODS else None

    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
            is_constructor(base) for base in node.bases
        ):
            # A subclass of the value is another way to make one of its own.
            found.append(("build", enclosing(node)))
        elif isinstance(node, ast.Attribute) and is_constructor(node.value):
            # Counted whether or not it is called here: the same attribute
            # bound to a name is the call site this walk would not see.
            if node.attr in BUILDING_METHODS:
                found.append(("build", enclosing(node)))
            elif node.attr in PARSING_METHODS:
                found.append(("parse", enclosing(node)))
        elif isinstance(node, ast.Call) and (form := call_form(node)) is not None:
            found.append((form, enclosing(node)))
    return found


def _names(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else None


def _own_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Every node inside *scope*, entering no function, lambda or class of its own.

    A nested definition is its own scope, so the names it states are its
    names; read as the outer function's they would answer for a receiver the
    outer function never annotated.
    """
    for child in ast.iter_child_nodes(scope):
        if isinstance(
            child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef
        ):
            continue
        yield child
        yield from _own_nodes(child)


def _stated_types(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    returns: dict[str, ast.expr],
    owner: ast.AST | None,
) -> dict[str, set[str]]:
    """Every type *function* itself states for a name it uses.

    Scoped to this function because a name is a receiver in the function
    that uses it: another function annotating the same word says nothing
    about this one, and read module-wide it would excuse a receiver here on
    the strength of an annotation somewhere else.  A plain assignment
    inherits what its source states, and inherits ``UNSTATED`` where the
    source states nothing.
    """
    stated: dict[str, set[str]] = {}

    def note(name: str, annotation: ast.expr | None) -> None:
        stated.setdefault(name, set()).add(
            UNSTATED if annotation is None else ast.unparse(annotation)
        )

    if isinstance(owner, ast.ClassDef):
        for statement in owner.body:
            if isinstance(statement, ast.AnnAssign) and isinstance(
                statement.target, ast.Name
            ):
                note(f"self.{statement.target.id}", statement.annotation)
    for argument in _parameters(function):
        note(argument.arg, argument.annotation)
    body = [
        node
        for node in _own_nodes(function)
        if isinstance(node, ast.AnnAssign | ast.Assign)
    ]
    for node in body:
        if isinstance(node, ast.AnnAssign):
            note(ast.unparse(node.target), node.annotation)
    changed = True
    while changed:
        changed = False
        for node in body:
            if not isinstance(node, ast.Assign):
                continue
            inherited = _value_types(node.value, stated, returns)
            for target in node.targets:
                name = ast.unparse(target)
                if not inherited <= stated.get(name, set()):
                    stated.setdefault(name, set()).update(inherited)
                    changed = True
    return stated


def _value_types(
    value: ast.expr, stated: dict[str, set[str]], returns: dict[str, ast.expr]
) -> set[str]:
    """What an assignment's right-hand side states about the name it binds."""
    if isinstance(value, ast.Name | ast.Attribute):
        return set(stated.get(ast.unparse(value), {UNSTATED}))
    called = _names(value.func) if isinstance(value, ast.Call) else None
    if called in returns:
        return {ast.unparse(returns[called])}
    return {UNSTATED}


def invalid_ruling_fields(source: str) -> tuple[int, ...]:
    tree = ast.parse(source)
    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }

    def scope_of(node: ast.AST) -> tuple[str, ...]:
        scope = []
        while id(node) in parents:
            node = parents[id(node)]
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                scope.append(node.name)
        return tuple(reversed(scope))

    identities = {"RulingId"}
    aliases: dict[str, tuple[ast.expr, tuple[str, ...]]] = {}

    def qualified(name: str, scope: tuple[str, ...]) -> str:
        return ".".join((*scope, name))

    for node in ast.walk(tree):
        scope = scope_of(node)
        if isinstance(node, ast.ImportFrom):
            identities.update(
                qualified(alias.asname or alias.name, scope)
                for alias in node.names
                if alias.name == "RulingId"
            )
            for alias in node.names:
                if alias.asname == "RulingId" and alias.name != "RulingId":
                    aliases[qualified("RulingId", scope)] = (
                        ast.Name(id=alias.name),
                        scope,
                    )
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases[qualified(target.id, scope)] = node.value, scope
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                aliases[qualified(node.target.id, scope)] = node.value, scope

    def lookup(name: str, scope: tuple[str, ...]) -> str | None:
        for length in range(len(scope), -1, -1):
            key = qualified(name, scope[:length])
            if key in aliases or key in identities:
                return key
        return None

    def typed(
        node: ast.AST,
        scope: tuple[str, ...],
        visited: frozenset[str] = frozenset(),
    ) -> tuple[bool, bool]:
        """Return whether an identity is present and every address leaf is typed."""
        if isinstance(node, (ast.Name, ast.Attribute)):
            if isinstance(node, ast.Attribute):
                owner = lookup(ast.unparse(node.value), scope)
                if owner is not None and owner in aliases:
                    if owner in visited:
                        return False, False
                    value, binding_scope = aliases[owner]
                    return typed(
                        ast.Attribute(value=value, attr=node.attr),
                        binding_scope,
                        visited | {owner},
                    )
            name = ast.unparse(node)
            key = lookup(name, scope)
            if key is not None and key in aliases and key not in visited:
                value, binding_scope = aliases[key]
                if (
                    key == "RulingId"
                    and isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "NewType"
                    and value.args
                    and isinstance(value.args[0], ast.Constant)
                    and value.args[0].value == "RulingId"
                ):
                    return True, True
                return typed(value, binding_scope, visited | {key})
            if key in identities and key not in aliases:
                return True, True
            if (
                key is None
                and isinstance(node, ast.Attribute)
                and node.attr == "RulingId"
            ):
                return True, True
            return False, False
        if isinstance(node, ast.Constant):
            if node.value is None or node.value is Ellipsis:
                return False, True
            if isinstance(node.value, str):
                try:
                    return typed(
                        ast.parse(node.value, mode="eval").body, scope, visited
                    )
                except SyntaxError:
                    pass
            return False, False
        if isinstance(node, ast.Subscript):
            container_name = (
                node.value.id
                if isinstance(node.value, ast.Name)
                else node.value.attr
                if isinstance(node.value, ast.Attribute)
                else None
            )
            if container_name == "Annotated" and isinstance(node.slice, ast.Tuple):
                return typed(node.slice.elts[0], scope, visited)
            return typed(node.slice, scope, visited)
        children = (
            node.elts
            if isinstance(node, ast.Tuple)
            else [node.left, node.right]
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
            else []
        )
        results = [typed(child, scope, visited) for child in children]
        return any(present for present, _ in results), bool(results) and all(
            valid for _, valid in results
        )

    return tuple(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id in RULING_ADDRESS_NAMES
        and typed(node.annotation, scope_of(node)) != (True, True)
    )
