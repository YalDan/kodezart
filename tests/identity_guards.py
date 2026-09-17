"""Shared static checks for explicit identity construction and model addresses."""

import ast

#: The model methods that make a value without naming its class.  A guard
#: counting only the class call would miss every one of them, and the copy
#: is the house idiom, so it is the shape a second writer would take.
BUILDING_METHODS = frozenset({"model_construct", "model_copy"})
#: The model methods that make a value out of serialized bytes or a mapping.
PARSING_METHODS = frozenset({"model_validate", "model_validate_json"})


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


def _stated_types(tree: ast.AST) -> dict[str, set[str]]:
    """Every annotation each name is declared with, by the name it addresses."""
    stated: dict[str, set[str]] = {}

    def note(name: str, annotation: ast.expr | None) -> None:
        if annotation is not None:
            stated.setdefault(name, set()).add(ast.unparse(annotation))

    returns: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.returns is not None:
                returns[node.name] = node.returns
            for argument in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
                node.args.vararg,
                node.args.kwarg,
            ):
                if argument is not None:
                    note(argument.arg, argument.annotation)
        elif isinstance(node, ast.AnnAssign):
            note(ast.unparse(node.target), node.annotation)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id in returns
        ):
            for target in node.targets:
                note(ast.unparse(target), returns[node.value.func.id])
    return stated


def model_value_sites(source: str, *, identity: str) -> dict[str, tuple[str, ...]]:
    """Where a module holding *identity* builds one of its values, and parses one.

    Derived from the source rather than from a list of names: a module
    counts when it imports or declares the identity, and inside such a
    module a receiver is excused only where its own annotation states a
    different type.  An unannotated receiver is reported, because a guard
    that trusted silence would be answered by dropping the annotation.
    Each site is named by the function that encloses it, so the assertion
    reads as the surface rather than as line numbers.
    """
    tree = ast.parse(source)
    if not any(
        (
            isinstance(node, ast.ImportFrom)
            and any(alias.name == identity for alias in node.names)
        )
        or (isinstance(node, ast.ClassDef) and node.name == identity)
        for node in ast.walk(tree)
    ):
        return {"build": (), "parse": ()}
    constructors = _constructor_names(tree, identity)
    stated = _stated_types(tree)
    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }

    def enclosing(node: ast.AST) -> str:
        while id(node) in parents:
            node = parents[id(node)]
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                return node.name
        return "<module>"

    def is_constructor(node: ast.AST) -> bool:
        return (isinstance(node, ast.Name) and node.id in constructors) or (
            isinstance(node, ast.Attribute) and node.attr == identity
        )

    def holds_another_type(node: ast.expr) -> bool:
        annotations = stated.get(ast.unparse(node))
        return annotations is not None and all(
            identity not in annotation for annotation in annotations
        )

    def form(node: ast.Call) -> str | None:
        if is_constructor(node.func):
            return "build"
        if (
            isinstance(node.func, ast.Call)
            and isinstance(node.func.func, ast.Name)
            and node.func.func.id == "type"
        ):
            return "build"
        if isinstance(node.func, ast.Attribute) and not holds_another_type(
            node.func.value
        ):
            if node.func.attr in BUILDING_METHODS:
                return "build"
            if node.func.attr in PARSING_METHODS:
                return "parse"
        return None

    sites: dict[str, list[str]] = {"build": [], "parse": []}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (found := form(node)) is not None:
            sites[found].append(enclosing(node))
    return {name: tuple(found) for name, found in sites.items()}


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
        and node.target.id in {"ruling_id", "ruling_ids", "ruling_ref", "ruling_refs"}
        and typed(node.annotation, scope_of(node)) != (True, True)
    )
