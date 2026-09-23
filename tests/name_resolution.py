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
never a route to a name.  The receiver offset applies when the first parameter
is spelled ``self`` or ``cls``, and assumes the receiver fills it, so an
unbound method called with an explicit instance —
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
import sys
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


# ---------------------------------------------------------------------------
# Resolution by identity: which definition a spelling IS, across the modules.
#
# Separate from the functions above and with limits of its own, stated in
# each function's docstring: unlike them, it resolves a relative import, a
# star import and an import written inside a function, and it reads a
# tuple-unpacking target as binding each name.
# ---------------------------------------------------------------------------

#: A definition of the tree, by the module it is written in and its dotted
#: name there: ``("domain/fire_spec.py", "criterion_ref")``, or
#: ``("core/protocols.py", "TrackerPort.create_criterion_if_absent")`` for a
#: method.  A module-level binding is a definition too, whatever its value.
Key = tuple[str, str]


def object_key(obj: Any) -> Key:
    """The definition a shipped object is, read off the object itself."""
    module = sys.modules[obj.__module__]
    path = Path(module.__file__ or "").resolve()
    return path.relative_to(SOURCE_ROOT.resolve()).as_posix(), obj.__qualname__


def _module_home(dotted: str, trees: Mapping[str, ast.Module]) -> str | None:
    """The key of the module *dotted* names in *trees*, a package or a file."""
    parts = dotted.split(".")
    if parts[0] != SOURCE_ROOT.name:
        return None
    inner = "/".join(parts[1:])
    for path in (f"{inner}.py", f"{inner}/__init__.py") if inner else ("__init__.py",):
        if path in trees:
            return path
    return None


def _dotted_module(path: str) -> str:
    """The dotted name the module keyed *path* is imported under."""
    parts = path.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join((SOURCE_ROOT.name, *parts))


def _imported_from(path: str, node: ast.ImportFrom) -> str | None:
    """The dotted module a from-import in *path* names, relative ones resolved."""
    if not node.level:
        return node.module
    package = _dotted_module(path).split(".")
    if not path.endswith("__init__.py"):
        package = package[:-1]
    package = package[: len(package) - (node.level - 1)]
    return ".".join((*package, *((node.module,) if node.module else ())))


def _module_statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Every statement run at import time, through ``if``/``try``/``with``."""
    for statement in body:
        yield statement
        if isinstance(statement, ast.If | ast.Try | ast.TryStar | ast.With):
            for block in (
                getattr(statement, "body", []),
                getattr(statement, "orelse", []),
                getattr(statement, "finalbody", []),
                *(handler.body for handler in getattr(statement, "handlers", [])),
            ):
                yield from _module_statements(block)


def _target_words(target: ast.expr) -> Iterator[str]:
    """Every name a binding target binds, through tuple and list unpacking."""
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, ast.Tuple | ast.List):
        for element in target.elts:
            yield from _target_words(element)
    elif isinstance(target, ast.Starred):
        yield from _target_words(target.value)


@dataclass(frozen=True)
class IdentityIndex:
    """Every definition of a map of modules, and what each spelling denotes."""

    trees: Mapping[str, ast.Module]
    #: Every definition by its key, to the statement that makes it.
    units: Mapping[Key, ast.AST]
    #: ``id(statement)`` -> the definitions that statement makes.
    starts: Mapping[int, tuple[Key, ...]]
    #: Module -> each top-level word it defines itself.
    defined: Mapping[str, frozenset[str]]
    #: Module -> each spelling one of its imports binds: a module key, or the
    #: ``(module, word)`` it imports.
    imported: Mapping[str, Mapping[str, str | Key]]
    #: Module -> the modules it star-imports.
    starred: Mapping[str, tuple[str, ...]]
    #: Method name -> every method of that name.
    methods: Mapping[str, frozenset[Key]]


def identity_index(trees: Mapping[str, ast.Module]) -> IdentityIndex:
    """Index *trees* once for every identity walk below.

    A definition is a top-level ``def``, ``class`` or bound name — through
    ``if``, ``try`` and ``with`` blocks and tuple unpacking — and each method
    a top-level class states.  An import binds its spelling wherever in the
    module it is written, inside a function included, and a relative import
    is resolved against the module's own package.
    """
    units: dict[Key, ast.AST] = {}
    starts: dict[int, tuple[Key, ...]] = {}
    defined: dict[str, frozenset[str]] = {}
    imported: dict[str, dict[str, str | Key]] = {}
    starred: dict[str, tuple[str, ...]] = {}
    methods: dict[str, set[Key]] = {}
    for module, tree in trees.items():
        words: set[str] = set()
        for statement in _module_statements(tree.body):
            made: list[Key] = []
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                made.append((module, statement.name))
            elif isinstance(statement, ast.ClassDef):
                made.append((module, statement.name))
                for member in statement.body:
                    if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                        key = (module, f"{statement.name}.{member.name}")
                        units[key] = member
                        starts[id(member)] = (key,)
                        methods.setdefault(member.name, set()).add(key)
            elif isinstance(statement, ast.Assign):
                made.extend(
                    (module, word)
                    for target in statement.targets
                    for word in _target_words(target)
                )
            elif isinstance(statement, ast.AnnAssign | ast.AugAssign):
                made.extend((module, word) for word in _target_words(statement.target))
            elif isinstance(statement, ast.TypeAlias):
                made.extend((module, word) for word in _target_words(statement.name))
            for key in made:
                units[key] = statement
                words.add(key[1])
            if made:
                starts[id(statement)] = tuple(made)
        defined[module] = frozenset(words)
        bound: dict[str, str | Key] = {}
        stars: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    spelled = alias.name if alias.asname else alias.name.split(".")[0]
                    home = _module_home(spelled, trees)
                    if home is not None:
                        bound[alias.asname or spelled] = home
            elif isinstance(node, ast.ImportFrom):
                dotted = _imported_from(module, node)
                source = None if dotted is None else _module_home(dotted, trees)
                if dotted is None or source is None:
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        stars.append(source)
                        continue
                    submodule = _module_home(f"{dotted}.{alias.name}", trees)
                    bound[alias.asname or alias.name] = (
                        submodule if submodule is not None else (source, alias.name)
                    )
        imported[module] = bound
        starred[module] = tuple(stars)
    return IdentityIndex(
        trees=trees,
        units=units,
        starts=starts,
        defined=defined,
        imported=imported,
        starred=starred,
        methods={name: frozenset(keys) for name, keys in methods.items()},
    )


def _lookup(
    index: IdentityIndex, module: str, word: str, seen: frozenset[Key] = frozenset()
) -> tuple[frozenset[Key], str | None]:
    """What *word* denotes in *module*: definitions, or a module key.

    Its own definition first, then the import that binds it, followed into
    the module it came from, then a star import.  *seen* bounds a cycle of
    re-exports.
    """
    if (module, word) in seen:
        return frozenset(), None
    seen = seen | {(module, word)}
    if word in index.defined.get(module, frozenset()):
        return frozenset({(module, word)}), None
    bound = index.imported.get(module, {}).get(word)
    if isinstance(bound, str):
        return frozenset(), bound
    if bound is not None:
        return _lookup(index, bound[0], bound[1], seen)
    for source in index.starred.get(module, ()):
        found = _lookup(index, source, word, seen)
        if found != (frozenset(), None):
            return found
    return frozenset(), None


def _reflected(node: ast.Call) -> tuple[ast.expr, str] | None:
    """``getattr(receiver, "word")`` as the attribute it reaches."""
    if (
        isinstance(node.func, ast.Name)
        and node.func.id == getattr.__name__
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return node.args[0], node.args[1].value
    return None


def denoted(
    index: IdentityIndex, module: str, node: ast.expr
) -> tuple[frozenset[Key], str | None]:
    """The definitions an expression in *module* IS, or the module it is.

    A word through its module's own definitions and imports; an attribute of
    a module through that module's, a submodule included; an attribute of a
    class through the class's own method; ``getattr`` with a literal word as
    that attribute; ``importlib.import_module`` with a literal name as that
    module.  A value of any other origin — a parameter, an instance, a
    subscript, a call's result — denotes nothing here.
    """
    if isinstance(node, ast.Name):
        return _lookup(index, module, node.id)
    receiver: ast.expr | None = None
    word = ""
    if isinstance(node, ast.Attribute):
        receiver, word = node.value, node.attr
    elif isinstance(node, ast.Call):
        reflected = _reflected(node)
        if reflected is not None:
            receiver, word = reflected
        elif (
            _spelling(node.func) in {"importlib.import_module", "import_module"}
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return frozenset(), _module_home(node.args[0].value, index.trees)
    if receiver is None:
        return frozenset(), None
    keys, route = denoted(index, module, receiver)
    if route is not None:
        submodule = _module_home(f"{_dotted_module(route)}.{word}", index.trees)
        if submodule is not None:
            return frozenset(), submodule
        return _lookup(index, route, word)
    return frozenset(
        (home, f"{name}.{word}")
        for home, name in keys
        if (home, f"{name}.{word}") in index.units
    ), None


@dataclass(frozen=True)
class Reference:
    """One place a module names a definition, by identity."""

    module: str
    line: int
    key: Key
    #: The dotted definition the reference sits inside, ``"<module>"`` at top.
    definition: str
    #: The definitions whose own text holds it: a top-level one and, inside
    #: a method, that method as well.
    within: tuple[Key, ...]
    #: Whether it is the callee of a call rather than a value handed on.
    called: bool
    #: ``"value"``, ``"annotation"`` for a parameter or variable annotation,
    #: or ``"return"`` for a declared return.
    position: str


def references(
    index: IdentityIndex, *, members: Collection[str] = ()
) -> tuple[Reference, ...]:
    """Every place in the indexed modules that names a definition of them.

    Each name, attribute, ``getattr`` and from-import is resolved by
    :func:`denoted` — so an alias, a re-export, a module alias or a local
    import names the definition it binds, and a word that merely spells the
    same name does not.  An attribute on a value whose origin is not
    resolved names nothing, except an attribute spelled like one of
    *members*, which names every method of that name, as does a string
    constant spelling one of them: a member is reached through the instance
    that holds it, so its spelling is all there is to key on.
    """
    wanted = frozenset(members)
    found: list[Reference] = []
    for module, tree in sorted(index.trees.items()):
        stack: list[tuple[ast.AST, tuple[str, ...], tuple[Key, ...], bool, str]] = [
            (tree, (), (), False, "value")
        ]
        while stack:
            node, scope, within, called, position = stack.pop()
            within = (*within, *index.starts.get(id(node), ()))
            keys: Collection[Key] = ()
            if isinstance(node, ast.Name | ast.Attribute | ast.Call):
                keys, _ = denoted(index, module, node)
                word = node.attr if isinstance(node, ast.Attribute) else None
                if not keys and word in wanted:
                    keys = index.methods.get(word or "", frozenset())
            elif isinstance(node, ast.Constant) and node.value in wanted:
                keys = index.methods.get(str(node.value), frozenset())
            elif isinstance(node, ast.ImportFrom):
                dotted = _imported_from(module, node)
                source = None if dotted is None else _module_home(dotted, index.trees)
                if source is not None:
                    keys = frozenset().union(
                        *(_lookup(index, source, alias.name)[0] for alias in node.names)
                    )
            found.extend(
                Reference(
                    module=module,
                    line=getattr(node, "lineno", 0),
                    key=key,
                    definition=".".join(scope) if scope else "<module>",
                    within=within,
                    called=called,
                    position=position,
                )
                for key in sorted(keys)
            )
            inner = scope
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                inner = (*scope, node.name)
            for field, value in ast.iter_fields(node):
                here = position
                if field == "annotation" and isinstance(node, ast.arg | ast.AnnAssign):
                    here = "annotation"
                elif field == "returns":
                    here = "return"
                for child in value if isinstance(value, list) else (value,):
                    if isinstance(child, ast.AST):
                        stack.append(
                            (
                                child,
                                inner,
                                within,
                                isinstance(node, ast.Call) and child is node.func,
                                here,
                            )
                        )
    return tuple(found)


def reaching(
    index: IdentityIndex,
    seeds: Collection[Key],
    *,
    found: Collection[Reference] | None = None,
) -> frozenset[Key]:
    """*seeds*, and every definition that reaches one, to a fixed point.

    A definition reaches one when its own text names it anywhere but in an
    annotation — called or handed on, in a body, a default, a decorator, a
    binding's value — or when its declared return names it.  A class reaches
    what any of its methods does.  Bounded by the definition count: every
    round adds one or stops.
    """
    body: dict[Key, set[Key]] = {}
    for reference in references(index) if found is None else found:
        if reference.position == "annotation":
            continue
        for owner in reference.within:
            body.setdefault(owner, set()).add(reference.key)
    reached = frozenset(seeds)
    for _ in range(len(index.units) + 1):
        grown = reached | {
            owner for owner, named in body.items() if not named.isdisjoint(reached)
        }
        if grown == reached:
            break
        reached = grown
    return reached


#: Where a value can be held: a name or parameter of a definition, a declared
#: field ``self.<word>`` of a class a call constructs, or an attribute
#: ``self.<word>`` assigned on any receiver — keyed ``("", "")``, because
#: the receiver's class is not resolved, so it stands for every class and a
#: subclass in another module reads it as its base wrote it.
Holder = tuple[Key, str]


@dataclass(frozen=True)
class Carried:
    """Where the values of some definitions are handed on to."""

    holders: frozenset[Holder]
    #: Every definition that returns one of the values.
    returners: frozenset[Key]


#: The scope a module's own top-level statements run in.
_TOP = "<module>"

#: The statements and expressions a value can be handed on at.
_BINDING_SITES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.NamedExpr,
    ast.For,
    ast.AsyncFor,
    ast.Return,
    ast.Call,
)

#: The calls that hand back one of their own arguments, by the position of
#: that argument: a ``partial`` is the function it binds, a ``cast`` is the
#: value it casts.
_WRAPPED = {"partial": 0, "partialmethod": 0, "cast": 1}


def _scope_of(key: Key) -> Key:
    """The class a method's ``self`` belongs to, or the key itself."""
    module, name = key
    return (module, name.split(".")[0]) if "." in name else key


def carried(index: IdentityIndex, keys: Collection[Key]) -> Carried:
    """Every holder a value of *keys* is handed to, to a fixed point.

    The value itself, not what calling it returns: ``mint = criterion_ref``
    hands the mint on, ``key = criterion_ref(row)`` hands on an identity.  A
    value is handed on by an assignment, annotated assignment, ``for``
    target or walrus to a name or an attribute; by a positional or keyword
    argument to the parameter it lands on — ``def``, a class's ``__init__``,
    or a class's declared fields when it has none; by a parameter default;
    and by ``return``, which makes every call of that definition a value of
    it.  An expression carries a value when it is one, or is a conditional,
    boolean operation, tuple, list, set or dict display, subscript, starred
    value, walrus or ``await`` over one that does, or ``partial`` or
    ``cast`` of one, or a call of a definition that returns one; a lambda or
    nested ``def`` whose body names one carries it as well.  Any other call
    carries nothing, whatever its arguments.  An attribute assigned on any
    receiver, ``self`` included, is that attribute of every class.  A call
    through a receiver whose class is not resolved lands on every method of
    that name.  Each round adds a holder or a returner or stops, so the walk
    is bounded by the binding sites.
    """
    wanted = frozenset(keys)
    holders: set[Holder] = set()
    returners: set[Key] = set()
    everywhere: Key = ("", "")

    def fields_of(node: ast.ClassDef) -> list[str]:
        return [
            statement.target.id
            for statement in node.body
            if isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
        ]

    def held(module: str, node: ast.expr, scope: Key) -> bool:
        if isinstance(node, ast.Name):
            return (scope, node.id) in holders
        spelled = _spelling(node)
        if spelled is not None and spelled.startswith("self."):
            owner = _scope_of(scope)
            return (owner, spelled) in holders or (everywhere, spelled) in holders
        return False

    def names(module: str, node: ast.AST, scope: Key) -> bool:
        annotated = {
            id(inner)
            for outer in ast.walk(node)
            for annotation in (
                (outer.annotation,)
                if isinstance(outer, ast.arg | ast.AnnAssign)
                else (outer.returns,)
                if isinstance(outer, ast.FunctionDef | ast.AsyncFunctionDef)
                else ()
            )
            if annotation is not None
            for inner in ast.walk(annotation)
        }
        return any(
            isinstance(inner, ast.Name | ast.Attribute | ast.Call)
            and id(inner) not in annotated
            and (
                not denoted(index, module, inner)[0].isdisjoint(wanted | returners)
                or (not isinstance(inner, ast.Call) and held(module, inner, scope))
            )
            for inner in ast.walk(node)
        )

    def carries(module: str, node: ast.expr, scope: Key) -> bool:
        if isinstance(node, ast.Lambda):
            return names(module, node.body, scope)
        if isinstance(node, ast.IfExp):
            return carries(module, node.body, scope) or carries(
                module, node.orelse, scope
            )
        parts: list[ast.expr | None] = []
        if isinstance(node, ast.BoolOp):
            parts = list(node.values)
        elif isinstance(node, ast.Tuple | ast.List | ast.Set):
            parts = list(node.elts)
        elif isinstance(node, ast.Dict):
            parts = list(node.values)
        elif isinstance(node, ast.Starred | ast.NamedExpr | ast.Await | ast.Subscript):
            parts = [node.value]
        elif isinstance(node, ast.Call) and _reflected(node) is None:
            if not denoted(index, module, node.func)[0].isdisjoint(returners):
                return True
            spelled = _spelling(node.func) or ""
            position = _WRAPPED.get(spelled.rsplit(".", 1)[-1])
            if position is not None and len(node.args) > position:
                parts = [node.args[position]]
        elif isinstance(node, ast.Name | ast.Attribute | ast.Call):
            return not denoted(index, module, node)[0].isdisjoint(wanted) or (
                not isinstance(node, ast.Call) and held(module, node, scope)
            )
        return any(carries(module, part, scope) for part in parts if part is not None)

    def bind(targets: Collection[ast.expr], scope: Key) -> set[Holder]:
        bound: set[Holder] = set()
        for target in targets:
            bound |= {(scope, word) for word in _target_words(target)}
            if isinstance(target, ast.Attribute):
                bound.add((everywhere, f"self.{target.attr}"))
        return bound

    def callees(module: str, call: ast.Call) -> list[tuple[Key, ast.AST, bool]]:
        found: list[tuple[Key, ast.AST, bool]] = []
        keys_, _ = denoted(index, module, call.func)
        for key in keys_:
            node = index.units.get(key)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found.append((key, node, False))
            elif isinstance(node, ast.ClassDef):
                init = (key[0], f"{key[1]}.__init__")
                found.append(
                    (init, index.units[init], True)
                    if init in index.units
                    else (key, node, False)
                )
        if not keys_ and isinstance(call.func, ast.Attribute):
            found.extend(
                (key, index.units[key], True)
                for key in index.methods.get(call.func.attr, frozenset())
            )
        return found

    def handed(
        call: ast.Call, key: Key, node: ast.AST, receiver: bool
    ) -> dict[Holder, ast.expr]:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return {
                (key, parameter): value
                for parameter, value in _handed(
                    call, node, through_receiver=receiver
                ).items()
            }
        # A class without ``__init__``: its declared fields, in order.
        fields = fields_of(node) if isinstance(node, ast.ClassDef) else []
        by_field = dict(zip(fields, call.args, strict=False))
        by_field.update({word.arg: word.value for word in call.keywords if word.arg})
        return {(key, f"self.{field}"): value for field, value in by_field.items()}

    def module_level(tree: ast.Module) -> Iterator[ast.AST]:
        stack: list[ast.AST] = list(tree.body)
        while stack:
            node = stack.pop()
            yield node
            if not isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ):
                stack.extend(ast.iter_child_nodes(node))

    #: Each binding site once, with the module and scope it is read in: a
    #: parameter default, a nested definition, an assignment, a ``for``, a
    #: ``return`` and a call.  Collected in one walk, then re-read each round.
    work: list[tuple[str, Key, ast.AST]] = []
    for key, node in index.units.items():
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        arguments = node.args
        positional = [*arguments.posonlyargs, *arguments.args]
        for parameter, default in (
            *zip(
                positional[len(positional) - len(arguments.defaults) :],
                arguments.defaults,
                strict=True,
            ),
            *zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True),
        ):
            if default is not None:
                work.append(
                    (key[0], key, ast.keyword(arg=parameter.arg, value=default))
                )
        work.extend(
            (key[0], key, inner)
            for inner in ast.walk(node)
            if inner is not node and isinstance(inner, _BINDING_SITES)
        )
    for module, tree in index.trees.items():
        work.extend(
            (module, (module, _TOP), inner)
            for inner in module_level(tree)
            if isinstance(inner, _BINDING_SITES)
            and not isinstance(
                inner, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            )
        )
    # Each round adds a holder or a returner or stops, and every one of them
    # is named by one binding site, so the rounds cannot outnumber the sites.
    for _ in range(len(work) + 1):
        before = (len(holders), len(returners))
        for module, scope, node in work:
            value: ast.expr | None = None
            targets: list[ast.expr] = []
            if isinstance(node, ast.keyword) and node.arg is not None:
                if carries(module, node.value, scope):
                    holders.add((scope, node.arg))
            elif isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ):
                if names(module, node, scope):
                    holders.add((scope, node.name))
            elif isinstance(node, ast.Assign):
                value, targets = node.value, node.targets
            elif isinstance(node, ast.AnnAssign | ast.AugAssign | ast.NamedExpr):
                value, targets = node.value, [node.target]
            elif isinstance(node, ast.For | ast.AsyncFor):
                value, targets = node.iter, [node.target]
            elif isinstance(node, ast.Return) and node.value is not None:
                if carries(module, node.value, scope):
                    returners.add(scope)
            elif isinstance(node, ast.Call) and any(
                carries(module, argument, scope)
                for argument in (*node.args, *(word.value for word in node.keywords))
            ):
                for key, callee, receiver in callees(module, node):
                    for holder, argument in handed(node, key, callee, receiver).items():
                        if carries(module, argument, scope):
                            holders.add(holder)
            if value is not None and carries(module, value, scope):
                holders |= bind(targets, scope)
        if (len(holders), len(returners)) == before:
            break
    return Carried(holders=frozenset(holders), returners=frozenset(returners))


def holding(
    index: IdentityIndex, found: Carried, modules: Collection[str]
) -> frozenset[Holder]:
    """The holders of *found* that sit in one of *modules*.

    A holder whose definition is written there, and an attribute assigned
    on any receiver, wherever a class of one of *modules* reads that
    ``self.<word>``: a subclass there reads what a base elsewhere assigned.
    """
    inside = frozenset(modules)
    held = {holder for holder in found.holders if holder[0][0] in inside}
    unresolved = {word for (scope, word) in found.holders if scope == ("", "")}
    for key, node in index.units.items():
        if key[0] not in inside or not isinstance(node, ast.ClassDef):
            continue
        read = {
            spelled
            for inner in ast.walk(node)
            if isinstance(inner, ast.Attribute)
            and (spelled := _spelling(inner)) in unresolved
        }
        held |= {(key, word) for word in read}
    return frozenset(held)
