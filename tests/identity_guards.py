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
    identities = {"RulingId"}
    aliases: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            identities.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "RulingId"
            )
            for alias in node.names:
                if alias.asname == "RulingId" and alias.name != "RulingId":
                    aliases["RulingId"] = ast.Name(id=alias.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                aliases[node.target.id] = node.value

    def typed(
        node: ast.AST, visited: frozenset[str] = frozenset()
    ) -> tuple[bool, bool]:
        """Return whether an identity is present and every address leaf is typed."""
        if isinstance(node, ast.Name):
            if node.id in aliases and node.id not in visited:
                value = aliases[node.id]
                if (
                    node.id == "RulingId"
                    and isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "NewType"
                    and value.args
                    and isinstance(value.args[0], ast.Constant)
                    and value.args[0].value == "RulingId"
                ):
                    return True, True
                return typed(aliases[node.id], visited | {node.id})
            if node.id in identities and node.id not in aliases:
                return True, True
            return False, False
        if isinstance(node, ast.Attribute):
            return node.attr == "RulingId", node.attr == "RulingId"
        if isinstance(node, ast.Constant):
            if node.value is None or node.value is Ellipsis:
                return False, True
            if isinstance(node.value, str):
                try:
                    return typed(ast.parse(node.value, mode="eval").body, visited)
                except SyntaxError:
                    pass
            return False, False
        if isinstance(node, ast.Subscript):
            name = (
                node.value.id
                if isinstance(node.value, ast.Name)
                else node.value.attr
                if isinstance(node.value, ast.Attribute)
                else None
            )
            if name == "Annotated" and isinstance(node.slice, ast.Tuple):
                return typed(node.slice.elts[0], visited)
            return typed(node.slice, visited)
        children = (
            node.elts
            if isinstance(node, ast.Tuple)
            else [node.left, node.right]
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
            else []
        )
        results = [typed(child, visited) for child in children]
        return any(present for present, _ in results), bool(results) and all(
            valid for _, valid in results
        )

    return tuple(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id in {"ruling_id", "ruling_ids", "ruling_ref", "ruling_refs"}
        and typed(node.annotation) != (True, True)
    )
