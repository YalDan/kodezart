"""What a module's names denote, read by object after import, for the guards.

A helper beside the guards, the way ``tests/name_resolution.py`` is: it holds
no test and spells no production symbol.  Where that module resolves a
spelling to the name it spells, this one resolves it to the object it names,
so a guard asks "is this callee the adapter class?" or "is this base an
enum?" of the object itself and not of a word that happens to match.

A module of the package whose text is the tree's own is imported and read
through ``vars``.  Any other text (a module a control plants) has its
module-level absolute imports performed, as each statement would perform
it, and nothing else, so a planted import resolves exactly as a production
one does while no planted body ever runs.  Every assignment, annotated
assignment and walrus in the module then binds a name nothing binds yet to
what its value denotes, at any depth and grown to a fixed point, so an
alias made in a function body resolves as a module-level one does; the
first binding of a name that denotes an object is the one read.

Outside this reach, as for every static guard: a value handed across a
function boundary, where the other function is not resolved at this site
(returned from a helper, stored on an object and read elsewhere, or passed
through a container built elsewhere); a name built at run time; and a
binding made only when a function runs (``setattr`` or ``globals()`` inside
a function body).
"""

import ast
import importlib
import inspect
from collections.abc import Mapping
from pathlib import Path

from tests.name_resolution import SOURCE_ROOT

#: What an expression denotes when it denotes nothing this module can read.
UNBOUND = object()


def dotted_module(relative: str) -> str:
    """The import name of the package module at *relative*."""
    dotted = ".".join(("kodezart", *Path(relative).with_suffix("").parts))
    return dotted.removesuffix(".__init__")


def _imported(module: str, name: str) -> object:
    """What ``from module import name`` binds: an attribute, or a submodule."""
    owner = importlib.import_module(module)
    try:
        return inspect.getattr_static(owner, name)
    except AttributeError:
        return importlib.import_module(f"{module}.{name}")


def imported_namespace(tree: ast.Module) -> dict[str, object]:
    """What a module's module-level absolute imports bind, and nothing else.

    Each import is performed as the statement would perform it; a relative
    import, or one that cannot be performed, binds nothing.
    """
    namespace: dict[str, object] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                try:
                    module = importlib.import_module(alias.name)
                except ImportError:
                    continue
                if alias.asname is not None:
                    namespace[alias.asname] = module
                else:
                    top = alias.name.partition(".")[0]
                    namespace[top] = importlib.import_module(top)
        elif (
            isinstance(statement, ast.ImportFrom)
            and statement.level == 0
            and statement.module is not None
        ):
            for alias in statement.names:
                try:
                    namespace[alias.asname or alias.name] = _imported(
                        statement.module, alias.name
                    )
                except ImportError:
                    continue
    return namespace


def module_namespace(
    relative: str, text: str, root: Path = SOURCE_ROOT
) -> dict[str, object]:
    """What a module's module-level names denote after import."""
    path = SOURCE_ROOT / relative
    if (
        root.resolve() == SOURCE_ROOT.resolve()
        and path.is_file()
        and path.read_text() == text
    ):
        return dict(vars(importlib.import_module(dotted_module(relative))))
    return imported_namespace(ast.parse(text))


def denoted(node: ast.expr, names: Mapping[str, object]) -> object:
    """The object *node* denotes through *names*, or ``UNBOUND``.

    A name through *names*; an attribute through the object its receiver
    denotes, read with ``inspect.getattr_static`` so no descriptor runs.
    """
    if isinstance(node, ast.Name):
        return names.get(node.id, UNBOUND)
    if isinstance(node, ast.Attribute):
        owner = denoted(node.value, names)
        if owner is UNBOUND:
            return UNBOUND
        try:
            return inspect.getattr_static(owner, node.attr)
        except AttributeError:
            return UNBOUND
    return UNBOUND


def _bindings(tree: ast.AST) -> list[tuple[str, ast.expr]]:
    """Every single-name binding of the tree, at any depth, with its value."""
    found: list[tuple[str, ast.expr]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            found.append((node.targets[0].id, node.value))
        elif (
            isinstance(node, ast.AnnAssign | ast.NamedExpr)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            found.append((node.target.id, node.value))
    return found


def denotations(tree: ast.AST, namespace: Mapping[str, object]) -> dict[str, object]:
    """*namespace* grown by every name the tree binds to a denoted object."""
    names = dict(namespace)
    pending = _bindings(tree)
    # Each pass binds at least one name or stops, and a name is bound once,
    # so the walk ends within one pass per binding.
    for _ in range(len(pending) + 1):
        bound = [
            (target, found)
            for target, value in pending
            if target not in names and (found := denoted(value, names)) is not UNBOUND
        ]
        if not bound:
            break
        for target, found in bound:
            names.setdefault(target, found)
    return names


def names_of(relative: str, text: str, root: Path = SOURCE_ROOT) -> dict[str, object]:
    """Everything a module's names denote: its namespace and its aliases."""
    return denotations(ast.parse(text), module_namespace(relative, text, root))


def names_of_tree(tree: ast.Module) -> dict[str, object]:
    """Everything a planted tree's names denote, through its imports alone."""
    return denotations(tree, imported_namespace(tree))
