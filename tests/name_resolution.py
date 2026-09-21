"""Import-, alias- and binding-aware name resolution for the static guards.

A helper beside the guards, the way ``tests/identity_guards.py`` is: it holds
no test and belongs to no one criterion, so a criterion whose fixtures must
live in one test module still has one.  A guard asks it what a local spelling
denotes, which names a module reaches, where a name is called, which local
names are bound to a value of interest, and which parameter a call hands such
a value to; every name a guard cares about is passed in, so nothing here
spells a production symbol.

``parameters_of``, the annotation walk inside ``annotated_parameters`` and
``definitions`` restate private helpers that live beside three guards today
(``tests/identity_guards.py``'s ``_parameters`` and ``_mentions``, and
``tests/domain/test_criterion_cross_off.py``'s ``qualified_names``).  The
duplication is the seam the piece that owns those files collapses onto this
module; it is named here rather than closed here because four test modules'
exact assertions sit behind those helpers.

Construction-form detection is not restated: ``identity_guards``'s
``model_value_sites`` already answers it with its own controls, and a guard
that needs it imports it from there.

Blind spots, stated once: a tuple-unpacking target binds nothing here, a
starred argument lands on no parameter, and a string constant is a value,
never a route to a name.
"""

import ast
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

import kodezart

#: The production package, read off the installed module rather than counted
#: back from this file's own path, so the tree a guard walks is the tree the
#: suite imports.
SOURCE_ROOT: Path = Path(kodezart.__file__).parent


def source_tree(root: Path = SOURCE_ROOT) -> dict[str, str]:
    """Every module under *root*, keyed by its posix path relative to it."""
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.py"))
    }


def parsed(sources: Mapping[str, str]) -> dict[str, ast.Module]:
    """The same map of modules, parsed once."""
    return {relative: ast.parse(source) for relative, source in sources.items()}


def _spelling(node: ast.expr) -> str | None:
    """The dotted word an expression spells, when it spells one."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        receiver = _spelling(node.value)
        return None if receiver is None else f"{receiver}.{node.attr}"
    return None


@dataclass(frozen=True)
class Resolution:
    """What one module's local spellings denote."""

    #: Local spelling -> the name it denotes.
    names: Mapping[str, str]
    #: Local spelling -> the dotted module it denotes.
    modules: Mapping[str, str]

    def denotes(self, node: ast.expr) -> str | None:
        """The name an expression denotes here, or ``None``.

        A ``Name`` through ``names``.  An ``Attribute`` whose receiver spells
        a module in ``modules`` (``m.compute_gap``,
        ``kodezart.domain.gap.compute_gap``) through its own attribute, when
        that attribute is a spelling this module resolves.
        """
        if isinstance(node, ast.Name):
            return self.names.get(node.id)
        if isinstance(node, ast.Attribute):
            receiver = _spelling(node.value)
            if receiver is not None and receiver in self.modules:
                return self.names.get(node.attr)
        return None


def resolve(
    tree: ast.Module, *, names: Collection[str], from_module: str | None = None
) -> Resolution:
    """Every local spelling of *names* this module binds.

    A from-import binds ``asname or name``; ``import a.b [as m]`` binds the
    module, so its attributes route; an assignment of a bound spelling binds
    its target too, grown to a fixed point because an alias can be written
    before its source in another function.  Without *from_module* a name is
    also denoted by its own word — a module that defines it declares what it
    is as plainly as an import would.  With *from_module* only an import of
    that module binds the names, so a local definition of the same word is
    not mistaken for the imported one.
    """
    wanted = frozenset(names)
    resolved: dict[str, str] = (
        {} if from_module is not None else {name: name for name in wanted}
    )
    modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if from_module is not None and node.module != from_module:
                continue
            for alias in node.names:
                if alias.name in wanted:
                    resolved[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if from_module is not None and alias.name != from_module:
                    continue
                modules[alias.asname or alias.name] = alias.name
    while True:
        grown = False
        for node in ast.walk(tree):
            targets, value = _binding(node)
            if value is None:
                continue
            denoted = Resolution(names=resolved, modules=modules).denotes(value)
            if denoted is None:
                continue
            for target in targets:
                if resolved.get(target) != denoted:
                    resolved[target] = denoted
                    grown = True
        if not grown:
            return Resolution(names=resolved, modules=modules)


def _binding(node: ast.AST) -> tuple[tuple[str, ...], ast.expr | None]:
    """The names a statement binds and the value it binds them to."""
    if isinstance(node, ast.Assign):
        return (
            tuple(target.id for target in node.targets if isinstance(target, ast.Name)),
            node.value,
        )
    if isinstance(node, ast.AnnAssign | ast.NamedExpr):
        if node.value is not None and isinstance(node.target, ast.Name):
            return (node.target.id,), node.value
        return (), None
    if isinstance(node, ast.For | ast.AsyncFor) and isinstance(node.target, ast.Name):
        return (node.target.id,), node.iter
    if isinstance(node, ast.withitem) and isinstance(node.optional_vars, ast.Name):
        return (node.optional_vars.id,), node.context_expr
    return (), None


def reaches(tree: ast.Module, *, names: Collection[str]) -> frozenset[str]:
    """Every one of *names* this module reaches, by any route.

    A resolved spelling, a definition of the name, a bare word, or an
    attribute spelling it.  An import on its own reaches nothing, and a
    string constant is not a route: a vocabulary member that happens to
    spell a name names no module.
    """
    wanted = frozenset(names)
    resolution = resolve(tree, names=wanted)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name | ast.Attribute):
            denoted = resolution.denotes(node)
            if denoted is not None:
                found.add(denoted)
            elif isinstance(node, ast.Attribute) and node.attr in wanted:
                found.add(node.attr)
        elif (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and node.name in wanted
        ):
            found.add(node.name)
    return frozenset(found)


@dataclass(frozen=True)
class Site:
    """One call, named the way a guard's register names it."""

    module: str
    line: int
    name: str
    definition: str


def definitions(tree: ast.Module) -> dict[int, str]:
    """``id(node)`` -> the dotted definition it sits inside.

    ``"<module>"`` where a node sits at the top level, so a register naming
    a site reads as the surface rather than as a line number.
    """
    named: dict[int, str] = {}

    def walk(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                inner = (*scope, child.name)
            named[id(child)] = ".".join(inner) if inner else "<module>"
            walk(child, inner)

    walk(tree, ())
    return named


def call_sites(
    trees: Mapping[str, ast.Module],
    *,
    names: Collection[str],
    methods: Collection[str] = (),
) -> tuple[Site, ...]:
    """Every call of one of *names* in *trees*.

    A callee that denotes one of the names, or that spells one as an
    attribute.  With *methods*, a ``receiver.method(...)`` whose receiver
    denotes one of the names and whose method is named is a site of that
    name too.  An import renames but does not call, so an import alone is no
    site.
    """
    wanted = frozenset(names)
    called = frozenset(methods)
    sites: list[Site] = []
    for module, tree in sorted(trees.items()):
        resolution = resolve(tree, names=wanted)
        where = definitions(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            name = resolution.denotes(callee)
            if (
                name is None
                and isinstance(callee, ast.Attribute)
                and callee.attr in wanted
            ):
                name = callee.attr
            if name is None and isinstance(callee, ast.Attribute):
                if callee.attr in called:
                    name = resolution.denotes(callee.value)
            if name is not None:
                sites.append(
                    Site(
                        module=module,
                        line=node.lineno,
                        name=name,
                        definition=where.get(id(node), "<module>"),
                    )
                )
    return tuple(sites)


def parameters_of(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.arg, ...]:
    """Every parameter of *function* in one sequence, vararg and kwarg included."""
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


def _mentions(annotation: ast.expr | None, names: Collection[str]) -> bool:
    """Whether *annotation* names one of *names* anywhere inside itself."""
    if annotation is None:
        return False
    wanted = frozenset(names)
    return any(
        (isinstance(node, ast.Name) and node.id in wanted)
        or (isinstance(node, ast.Attribute) and node.attr in wanted)
        or (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in wanted
        )
        for node in ast.walk(annotation)
    )


def annotated_parameters(
    tree: ast.Module, *, mentioning: Collection[str]
) -> frozenset[str]:
    """Parameter names whose own annotation names one of *mentioning*.

    The annotation is read whole, so a union, a container or a forward
    reference written as a string states the type as plainly as the bare
    name does.
    """
    return frozenset(
        argument.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        for argument in parameters_of(node)
        if _mentions(argument.annotation, mentioning)
    )


def _handed(
    call: ast.Call,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    through_receiver: bool,
) -> dict[str, ast.expr]:
    """Parameter name -> the argument *call* hands it.

    Positional arguments by index, keywords by name.  A method reached
    through a receiver is offset by the parameter that receiver fills.
    Starred arguments and ``**kwargs`` land on no parameter.
    """
    positional = [*function.args.posonlyargs, *function.args.args]
    offset = (
        1
        if through_receiver and positional and positional[0].arg in {"self", "cls"}
        else 0
    )
    named = {argument.arg for argument in parameters_of(function)}
    handed: dict[str, ast.expr] = {}
    for index, argument in enumerate(call.args):
        if isinstance(argument, ast.Starred):
            continue
        position = index + offset
        if position < len(positional):
            handed[positional[position].arg] = argument
    for keyword in call.keywords:
        if keyword.arg is not None and keyword.arg in named:
            handed[keyword.arg] = keyword.value
    return handed


def _module_path(dotted: str) -> str:
    """The posix path a dotted package module is keyed by in a source tree."""
    parts = dotted.split(".")
    return "/".join(parts[1:]) + ".py" if parts[0] == SOURCE_ROOT.name else ""


def _module_routes(tree: ast.Module, trees: Mapping[str, ast.Module]) -> dict[str, str]:
    """Local spelling -> the module of *trees* that spelling routes to.

    ``import a.b [as m]`` binds the module itself, so its attributes route
    through the local spelling.  ``from a import b`` binds a submodule
    whenever ``a.b`` is a module of the tree rather than a name defined
    inside ``a``, so it routes the same way.  A spelling that names no module
    of *trees* is no route: the receiver is a value, not a module.
    """
    routes: dict[str, str] = {}
    for local, dotted in resolve(tree, names=()).modules.items():
        path = _module_path(dotted)
        if path in trees:
            routes[local] = path
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        for alias in node.names:
            path = _module_path(f"{node.module}.{alias.name}")
            if path in trees:
                routes[alias.asname or alias.name] = path
    return routes


def _definitions_by_name(
    trees: Mapping[str, ast.Module],
) -> tuple[
    dict[str, dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]]],
    dict[str, dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]]],
    dict[str, list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]],
]:
    """Every function by module and name, the top-level ones, and the methods.

    The top-level map is what a module route reaches: ``m.helper(...)`` can
    only land on a definition ``m`` itself states, never on a method of some
    class inside it.
    """
    own: dict[str, dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]]] = {}
    top: dict[str, dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]]] = {}
    methods: dict[str, list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]] = {}
    for module, tree in trees.items():
        inside = {
            id(statement)
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            for statement in node.body
        }
        for statement in tree.body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                top.setdefault(module, {}).setdefault(statement.name, []).append(
                    statement
                )
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            own.setdefault(module, {}).setdefault(node.name, []).append(node)
            if id(node) in inside:
                methods.setdefault(node.name, []).append((module, node))
    return own, top, methods


def parameters_receiving(
    trees: Mapping[str, ast.Module],
    *,
    yields: Callable[[str, ast.expr], bool],
) -> dict[tuple[str, str], frozenset[str]]:
    """Which parameters a call in the package hands a value of interest to.

    ``(module, function name)`` -> the parameter names some call hands an
    argument to for which ``yields(calling_module, argument)`` is true.

    A bare callee is the caller's own definition of that word when it has
    one, else the definition the caller from-imports, under the name the
    home module gave it: an aliased from-import is looked up as the imported
    word, not as the local spelling.  A callee reached through a receiver
    that spells a module of *trees* — ``import a.b [as m]`` or ``from a
    import b`` — is that module's own top-level definition of the attribute,
    and a module fills no parameter, so nothing is offset.  Any other
    receiver is a value whose type is not resolved here, so the callee is
    every method of that name in the package and the parameter the receiver
    fills is skipped.

    One call edge, never deeper: a value handed on from the callee is the
    callee's own site to answer for.
    """
    own, top, methods = _definitions_by_name(trees)
    received: dict[tuple[str, str], set[str]] = {}
    for module, tree in sorted(trees.items()):
        imported = {
            alias.asname or alias.name: (_module_path(node.module), alias.name)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
            for alias in node.names
        }
        routes = _module_routes(tree, trees)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            targets: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
            through_receiver = False
            if isinstance(callee, ast.Name):
                here = own.get(module, {}).get(callee.id)
                if here is not None:
                    targets = [(module, function) for function in here]
                else:
                    home, original = imported.get(callee.id, ("", callee.id))
                    targets = [
                        (home, function)
                        for function in own.get(home, {}).get(original, [])
                    ]
            elif isinstance(callee, ast.Attribute):
                receiver = _spelling(callee.value)
                routed = None if receiver is None else routes.get(receiver)
                if routed is not None:
                    targets = [
                        (routed, function)
                        for function in top.get(routed, {}).get(callee.attr, [])
                    ]
                else:
                    targets = list(methods.get(callee.attr, ()))
                    through_receiver = True
            for home, function in targets:
                handed = _handed(node, function, through_receiver=through_receiver)
                for parameter, argument in handed.items():
                    if yields(module, argument):
                        received.setdefault((home, function.name), set()).add(parameter)
    return {key: frozenset(found) for key, found in received.items()}


def bound_names(
    tree: ast.Module,
    *,
    yields: Callable[[ast.expr, frozenset[str]], bool],
    seeds: Collection[str] = (),
) -> frozenset[str]:
    """Every local name bound to a value *yields* recognises, from *seeds*.

    Over an assignment, an annotated assignment, a walrus, a ``for`` target
    and a ``with ... as`` name, grown to a fixed point because a name can be
    bound from another that is bound further down.  Module-wide: a word
    bound to the value anywhere in the module is that value wherever the
    module reads it.  A tuple-unpacking target binds nothing here.
    """
    names = frozenset(seeds)
    while True:
        grown = set(names)
        for node in ast.walk(tree):
            targets, value = _binding(node)
            if value is None or not targets:
                continue
            if yields(value, names):
                grown.update(targets)
        if grown == set(names):
            return names
        names = frozenset(grown)
