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

Blind spots, stated once: a tuple-unpacking target binds nothing in
``resolve`` or ``bound_names`` (``unpacking_bindings`` lists what it binds, for
a guard that wants it), a starred argument lands on no parameter, and a string
constant is a value, never a route to a name (``spelled_sites`` alone reads an
identifier inside one as a spelling, for a guard that wants every route).  The
receiver offset applies when the first parameter is spelled ``self`` or
``cls``, and assumes the receiver fills it, so an unbound method called with an
explicit instance —
``Reader._own_text(reader, spec)`` — hands that instance to the parameter
after the receiver's own and every later argument lands one place late, on the
parameter after its own, or off the end.  Only an absolute ``kodezart.``
import names a module of the tree: a relative import is neither a route nor a
home, so a call to a bare name it binds reaches no definition, and a relative
module receiver — ``from . import b``, then ``b._own_text(spec)`` — spells no
module of the tree and takes the every-method rule; no module under the
package writes either form.
"""

import ast
import functools
import importlib
import inspect
import re
import typing
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, ModuleType

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
    """The names a statement binds and the value it binds them to.

    A loop target and a comprehension's own target are bound to the iterable
    they walk, so a name taken one at a time out of a collection of values of
    interest is one of those values; the two forms are the same binding
    written two ways, and a comprehension covers the list, set, dict and
    generator spellings alike.
    """
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
    if isinstance(node, ast.comprehension) and isinstance(node.target, ast.Name):
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

    Only a ``def`` is a definition here, so a value handed to a lambda's
    parameter — inline, bound by an assignment, or handed as a sort key — and
    a value bound into a ``functools.partial`` land on no parameter at all.

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


def _target_positions(target: ast.expr) -> tuple[tuple[str, int | None], ...]:
    """Each name an unpacking target binds, with its index when it has one.

    A name standing directly in a flat tuple or list target, before any
    starred name, is bound to that element; a nested or starred name, or one
    after a star, has no fixed index.  An attribute or subscript target
    stores into a value rather than binding a name, so it binds nothing.
    """
    if not isinstance(target, ast.Tuple | ast.List):
        return ()
    found: list[tuple[str, int | None]] = []
    fixed = True
    for index, one in enumerate(target.elts):
        if isinstance(one, ast.Starred):
            fixed = False
        if isinstance(one, ast.Name):
            found.append((one.id, index if fixed else None))
        else:
            found.extend((name, None) for name in _stored_names(one))
    return tuple(found)


def _stored_names(target: ast.expr) -> tuple[str, ...]:
    """Every name a target binds, however deeply it nests."""
    if isinstance(target, ast.Name):
        return (target.id,)
    if isinstance(target, ast.Starred):
        return _stored_names(target.value)
    if isinstance(target, ast.Tuple | ast.List):
        return tuple(name for one in target.elts for name in _stored_names(one))
    return ()


@dataclass(frozen=True)
class Unpacked:
    """One name an unpacking target binds, and where its value comes from."""

    name: str
    #: The value unpacked: the assigned value, or the iterable a loop or a
    #: comprehension walks, or the context a ``with`` enters.
    value: ast.expr
    #: The element's index in what is unpacked, or ``None`` when no index is
    #: fixed (nested, starred, or after a star).
    index: int | None
    #: True when each element of ``value`` is unpacked (a loop or a
    #: comprehension), False when ``value`` itself is.
    walks: bool


def unpacking_bindings(tree: ast.Module) -> tuple[Unpacked, ...]:
    """Every name an unpacking target binds.

    The targets ``_binding`` leaves out: a tuple, list or starred target of
    an assignment, a loop, a comprehension or a ``with``.  ``a, b = pair``
    binds ``a`` at index 0 of ``pair``; ``for key, one in items`` binds
    ``one`` at index 1 of each element of ``items``.  A name with no fixed
    index comes with ``None``: which element lands on it is decided at run
    time, so a guard reads it as the whole value.
    """
    found: list[Unpacked] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        walks = isinstance(node, ast.For | ast.AsyncFor | ast.comprehension)
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
            targets, value = [node.target], node.iter
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            targets, value = [node.optional_vars], node.context_expr
        if value is None:
            continue
        for target in targets:
            for name, index in _target_positions(target):
                found.append(Unpacked(name, value, index, walks))
    return tuple(found)


@dataclass(frozen=True)
class PatternStep:
    """One step from a match subject toward the value a capture binds.

    ``"class"``: the value at this position is matched against one of
    ``classes`` (an ``|`` of class patterns under one ``as`` states several).
    ``"field"``: the value is the position's attribute or mapping entry named
    ``field``.  ``"part"``: the value is reached from the position without a
    field name: a sequence item or starred rest, a mapping value under a key
    that is no string, a mapping's ``**rest``, or a class pattern's positional
    sub-pattern.
    """

    kind: str
    field: str | None = None
    classes: tuple[ast.expr, ...] = ()


def _asserted_classes(pattern: ast.pattern | None) -> tuple[ast.expr, ...]:
    """The classes a pattern matches the value at its own position against."""
    if isinstance(pattern, ast.MatchClass):
        return (pattern.cls,)
    if isinstance(pattern, ast.MatchAs):
        return _asserted_classes(pattern.pattern)
    if isinstance(pattern, ast.MatchOr):
        return tuple(cls for one in pattern.patterns for cls in _asserted_classes(one))
    return ()


def pattern_captures(
    pattern: ast.pattern,
) -> tuple[tuple[str, tuple[PatternStep, ...]], ...]:
    """Every name a case pattern binds, with the steps from the subject to it.

    Every pattern form is descended: ``as`` captures and bare words, ``|``
    alternatives, a class pattern's keyword and positional sub-patterns, a
    sequence's items and starred rest, and a mapping's values and ``**rest``.
    A name is paired with the path its value takes from the subject, so a
    guard decides what the name holds from where it stands rather than from
    which pattern node spelled it.  A value or singleton pattern binds
    nothing.
    """
    found: list[tuple[str, tuple[PatternStep, ...]]] = []

    def walk(node: ast.pattern, path: tuple[PatternStep, ...]) -> None:
        if isinstance(node, ast.MatchAs):
            if node.pattern is not None:
                walk(node.pattern, path)
            if node.name is not None:
                asserted = _asserted_classes(node.pattern)
                found.append(
                    (
                        node.name,
                        (*path, PatternStep("class", classes=asserted))
                        if asserted
                        else path,
                    )
                )
        elif isinstance(node, ast.MatchStar):
            if node.name is not None:
                found.append((node.name, path))
        elif isinstance(node, ast.MatchOr):
            for one in node.patterns:
                walk(one, path)
        elif isinstance(node, ast.MatchClass):
            here = (*path, PatternStep("class", classes=(node.cls,)))
            for sub in node.patterns:
                walk(sub, (*here, PatternStep("part")))
            for field, sub in zip(node.kwd_attrs, node.kwd_patterns, strict=True):
                walk(sub, (*here, PatternStep("field", field=field)))
        elif isinstance(node, ast.MatchSequence):
            for sub in node.patterns:
                walk(sub, (*path, PatternStep("part")))
        elif isinstance(node, ast.MatchMapping):
            for key, sub in zip(node.keys, node.patterns, strict=True):
                named = isinstance(key, ast.Constant) and isinstance(key.value, str)
                step = (
                    PatternStep("field", field=key.value)
                    if named and isinstance(key, ast.Constant)
                    else PatternStep("part")
                )
                walk(sub, (*path, step))
            if node.rest is not None:
                found.append((node.rest, (*path, PatternStep("part"))))

    walk(pattern, ())
    return tuple(found)


def through_partials(trees: Mapping[str, ast.Module]) -> dict[str, ast.Module]:
    """The same modules, each ``functools.partial(f, ...)`` also written as a call.

    ``partial(f, a, k=b)`` hands ``a`` and ``b`` to ``f``'s parameters exactly
    as ``f(a, k=b)`` would, so a walk that follows a value into the parameter
    a call hands it to (``parameters_receiving``) follows it through the
    partial too.  ``partial`` is resolved through ``functools`` itself — a
    from-import under any alias, or the attribute of a module alias — so a
    local definition of the same word is not it.  The original nodes are
    reused, so a guard's own reading of an argument answers for the call it
    stands in.
    """
    rewritten: dict[str, ast.Module] = {}
    for path, tree in trees.items():
        resolution = resolve(tree, names={"partial"}, from_module="functools")

        def is_partial(func: ast.expr, resolution: Resolution = resolution) -> bool:
            return resolution.denotes(func) == "partial" or (
                isinstance(func, ast.Attribute)
                and func.attr == "partial"
                and _spelling(func.value) in resolution.modules
            )

        calls = [
            ast.Expr(
                value=ast.Call(
                    func=node.args[0], args=node.args[1:], keywords=node.keywords
                )
            )
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and node.args and is_partial(node.func)
        ]
        rewritten[path] = (
            ast.Module(body=[*tree.body, *calls], type_ignores=[]) if calls else tree
        )
    return rewritten


def _dotted(path: str) -> str:
    """The dotted module a posix path of the tree is imported as."""
    parts = [SOURCE_ROOT.name, *path.removesuffix(".py").split("/")]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def package_modules(sources: Collection[str]) -> tuple[ModuleType, ...]:
    """Each module of the tree, imported: the objects a guard can follow."""
    return tuple(importlib.import_module(_dotted(path)) for path in sorted(sources))


def _ours(value: object) -> bool:
    """Whether *value* is defined in the package, by its own ``__module__``."""
    module = getattr(value, "__module__", None)
    return isinstance(module, str) and module.partition(".")[0] == SOURCE_ROOT.name


def _mentioned(
    annotation: object, scope: Mapping[str, object], known: Mapping[str, object]
) -> Iterator[object]:
    """The objects an annotation names, a string one read rather than run.

    A string annotation, or a forward reference inside one, is parsed and
    each word it spells is looked up in its own module first and then in
    every module of the package, so a name imported only for the type check
    is still found where it is defined.
    """
    if isinstance(annotation, typing.ForwardRef):
        annotation = annotation.__forward_arg__
    if not isinstance(annotation, str):
        yield annotation
        return
    try:
        parsed_annotation = ast.parse(annotation, mode="eval")
    except SyntaxError:
        return
    for node in ast.walk(parsed_annotation):
        word = _spelling(node) if isinstance(node, ast.Name | ast.Attribute) else None
        if word is None:
            continue
        last = word.rpartition(".")[2]
        for found in (scope.get(last), known.get(last)):
            if found is not None:
                yield found


def _produces(
    value: object,
    scopes: Mapping[str, Mapping[str, object]],
    known: Mapping[str, object],
) -> Iterator[object]:
    """What *value* can hand back when it is used: called, built, validated.

    A type form hands back its arguments and origin, an alias its value, a
    ``NewType`` its supertype, a type variable its bound, a partial its
    function, a descriptor its function, and a collection its members.  A
    package class hands back what its own fields and members state, and those
    of every package class it inherits from; a package function or method
    hands back its return annotation, never a parameter's.  Any
    other instance hands back its type and its own attributes, which is how
    a ``TypeAdapter`` built at module level hands back the type it adapts.
    """
    yield from typing.get_args(value)
    origin = typing.get_origin(value)
    if origin is not None:
        yield origin
    if isinstance(value, typing.TypeAliasType):
        yield value.__value__
    elif isinstance(value, typing.NewType):
        yield value.__supertype__
    elif isinstance(value, typing.TypeVar):
        yield value.__bound__
        yield from value.__constraints__
    elif isinstance(value, functools.partial):
        yield value.func
    elif isinstance(value, staticmethod | classmethod):
        yield value.__func__
    elif isinstance(value, property):
        yield value.fget
    elif isinstance(value, tuple | list | set | frozenset):
        yield from value
    elif isinstance(value, dict | MappingProxyType):
        yield from value.values()
    elif inspect.isclass(value):
        for klass in value.__mro__ if _ours(value) else ():
            if not _ours(klass):
                continue
            scope = scopes.get(klass.__module__, {})
            for annotation in inspect.get_annotations(klass).values():
                yield from _mentioned(annotation, scope, known)
            yield from (
                member
                for name, member in vars(klass).items()
                if not name.startswith("__")
            )
    elif inspect.isroutine(value):
        if _ours(value):
            scope = getattr(value, "__globals__", {})
            returned = inspect.get_annotations(value).get("return")
            yield from _mentioned(returned, scope, known)
    elif not isinstance(value, ModuleType):
        yield type(value)
        yield from getattr(value, "__dict__", {}).values()


def names_reaching(target: object, *, modules: Iterable[ModuleType]) -> frozenset[str]:
    """Every word *modules* bind to an object from which *target* can be had.

    A module-level name is one when using its object — calling it,
    building it, validating through it, reading it — can hand back *target*,
    directly or through a chain of what each object hands back
    (``_produces``), to a fixed point.  So a class holding the target in a
    field is one, a union or an alias over such a class is one, a function
    returning one is one, and an adapter built over one is one.  The name of
    a method of a package class whose return can hand back *target* is one
    too, because a method is reached by that word on any receiver.

    Keyed on the objects, not on how their source spells them, so an
    assignment, a type alias, an import under another name and a re-export
    all bind the same object.  A dunder name is never one.
    """
    loaded = tuple(modules)
    scopes = {module.__name__: vars(module) for module in loaded}
    known: dict[str, object] = {}
    for module in loaded:
        for name, value in vars(module).items():
            known.setdefault(name, value)
    graph: dict[int, list[int]] = {}
    kept: dict[int, object] = {}
    stack: list[object] = [
        value
        for module in loaded
        for name, value in vars(module).items()
        if not name.startswith("__")
    ]
    while stack:
        value = stack.pop()
        if id(value) in graph:
            continue
        kept[id(value)] = value
        produced = list(_produces(value, scopes, known))
        graph[id(value)] = [id(one) for one in produced]
        stack.extend(produced)
    back: dict[int, set[int]] = {}
    for source, heads in graph.items():
        for head in heads:
            back.setdefault(head, set()).add(source)
    reaching = {id(target)}
    frontier = [id(target)]
    while frontier:
        for source in back.get(frontier.pop(), ()):
            if source not in reaching:
                reaching.add(source)
                frontier.append(source)
    words = {
        name
        for module in loaded
        for name, value in vars(module).items()
        if not name.startswith("__") and id(value) in reaching
    }
    for value in kept.values():
        if inspect.isclass(value) and _ours(value):
            words.update(
                name
                for name, member in vars(value).items()
                if not name.startswith("__")
                and not inspect.isclass(member)
                and id(member) in reaching
            )
    return frozenset(words)


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _unspelled(tree: ast.Module) -> set[int]:
    """``id`` of every node inside an annotation or a docstring.

    An annotation states a type for the type check and a docstring states
    prose: neither is a use of the value it names.
    """
    skipped: set[int] = set()
    for node in ast.walk(tree):
        annotations: list[ast.expr | None] = []
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            annotations.append(node.returns)
            annotations.extend(one.annotation for one in parameters_of(node))
        elif isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
        for annotation in annotations:
            if annotation is not None:
                skipped.update(id(one) for one in ast.walk(annotation))
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                skipped.add(id(first.value))
    return skipped


def spelled_sites(
    tree: ast.Module, *, words: Collection[str]
) -> tuple[tuple[str, str], ...]:
    """Every place *tree* spells one of *words*, as ``(definition, word)``.

    A name, an attribute, an import's imported or bound name, and an
    identifier inside a string literal all spell the word; an annotation and
    a docstring do not.  However a module reaches an object — a from-import,
    a module attribute, ``importlib`` or ``sys.modules`` and an attribute
    off what they return, a package ``__init__``, a relative import, a
    re-export, ``getattr`` or a mapping lookup with a literal — the word is
    spelled at the use.  A word built at run time is not.
    """
    wanted = frozenset(words)
    skipped = _unspelled(tree)
    where = definitions(tree)
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        spelled: list[str] = []
        if isinstance(node, ast.Name):
            spelled.append(node.id)
        elif isinstance(node, ast.Attribute):
            spelled.append(node.attr)
        elif isinstance(node, ast.alias):
            spelled.extend(_IDENTIFIER.findall(node.name))
            if node.asname is not None:
                spelled.append(node.asname)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            spelled.extend(_IDENTIFIER.findall(node.value))
        found.extend(
            (where.get(id(node), "<module>"), word)
            for word in spelled
            if word in wanted
        )
    return tuple(sorted(found))


def bound_names(
    tree: ast.Module,
    *,
    yields: Callable[[ast.expr, frozenset[str]], bool],
    seeds: Collection[str] = (),
) -> frozenset[str]:
    """Every local name bound to a value *yields* recognises, from *seeds*.

    Over an assignment, an annotated assignment, a walrus, a ``for`` target,
    a comprehension's own target and a ``with ... as`` name, grown to a fixed
    point because a name can be bound from another that is bound further
    down.  Module-wide: a word
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
