"""Shared static checks for explicit identity construction and model addresses."""

import ast


def construction_sites(source: str, *, identity: str = "RulingId") -> tuple[int, ...]:
    tree = ast.parse(source)
    constructors = {identity}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            constructors.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == identity
            )

    def is_constructor(node: ast.AST) -> bool:
        return (isinstance(node, ast.Name) and node.id in constructors) or (
            isinstance(node, ast.Attribute) and node.attr == identity
        )

    # Assignment aliases can precede their source alias in another function.
    # Resolve the finite set before counting calls; never execute source.
    changed = True
    while changed:
        previous = set(constructors)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and is_constructor(node.value):
                constructors.update(
                    target.id for target in node.targets if isinstance(target, ast.Name)
                )
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and isinstance(node.target, ast.Name)
                and is_constructor(node.value)
            ):
                constructors.add(node.target.id)
        changed = constructors != previous
    return tuple(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and is_constructor(node.func)
    )


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
