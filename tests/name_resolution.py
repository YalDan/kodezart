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

``named_object``, ``module_namespace`` and ``referencing_definitions`` resolve
by the object instead of by the word, a string constant naming one included,
and each states its own reach.
"""

import ast
import functools
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import inspect
import pkgutil
import re
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


def defining_module(value: object, root: Path = SOURCE_ROOT) -> str:
    """The path of the module that defines *value*, keyed as ``source_tree`` keys it.

    Read off the object's own ``__module__``, so a definition that moves
    carries every guard keyed on its home with it.
    """
    module = importlib.import_module(getattr(value, "__module__", ""))
    return Path(module.__file__ or "").resolve().relative_to(root.resolve()).as_posix()


def _dotted_modules(trees: Mapping[str, ast.Module]) -> dict[str, str]:
    """Dotted module name -> the path *trees* keys it under, packages included."""
    named: dict[str, str] = {}
    for relative in trees:
        parts = relative.removesuffix(".py").split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        named[".".join((SOURCE_ROOT.name, *parts))] = relative
    return named


def imported_modules(
    module: str, tree: ast.Module, trees: Mapping[str, ast.Module]
) -> frozenset[str]:
    """Every module of *trees* that *module*'s source names, by any static route.

    - an ``import`` or a ``from`` import anywhere in the module: at the top,
      inside a function or a class, under ``if TYPE_CHECKING:`` alike;
    - a relative ``from`` import, resolved against *module*'s own package;
    - ``from package import name`` where ``package.name`` is a module;
    - a dotted spelling that runs through a name an import binds —
      ``import kodezart`` and then ``kodezart.domain.gap.compute_gap``, or
      ``from kodezart import domain`` and then ``domain.gap``;
    - a string constant that spells a module's dotted name, the way
      ``importlib.import_module`` or ``__import__`` is handed one.

    Not seen: a module name assembled at run time, a relative name handed to
    ``importlib.import_module`` with its package, and ``eval`` or ``exec``.
    """
    dotted = _dotted_modules(trees)
    own = next(
        (name for name, path in dotted.items() if path == module),
        SOURCE_ROOT.name,
    )
    package = own if module.endswith("__init__.py") else own.rpartition(".")[0]
    named: set[str] = set()
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                named.add(alias.name)
                if alias.asname:
                    bound[alias.asname] = alias.name
                else:
                    root = alias.name.partition(".")[0]
                    bound[root] = root
        elif isinstance(node, ast.ImportFrom):
            base = (
                importlib.util.resolve_name(
                    "." * node.level + (node.module or ""), package
                )
                if node.level
                else node.module or ""
            )
            named.add(base)
            for alias in node.names:
                if f"{base}.{alias.name}" in dotted:
                    named.add(f"{base}.{alias.name}")
                    bound[alias.asname or alias.name] = f"{base}.{alias.name}"
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            named.add(node.value)
    for node in ast.walk(tree):
        spelled = (
            _spelling(node) if isinstance(node, ast.Name | ast.Attribute) else None
        )
        if spelled is None:
            continue
        root, _, rest = spelled.partition(".")
        if root not in bound:
            continue
        parts = [bound[root], *([rest] if rest else [])]
        full = ".".join(parts).split(".")
        named.update(".".join(full[:end]) for end in range(1, len(full) + 1))
    return frozenset(dotted[name] for name in named if name in dotted)


def modules_reaching(
    trees: Mapping[str, ast.Module], *, homes: Collection[str]
) -> frozenset[str]:
    """Every module of *trees* whose imports lead to one of *homes*, at any depth.

    The homes themselves included.  Grown to a fixed point over
    ``imported_modules``; each round adds a module or ends the walk, so it
    takes at most one round per module.
    """
    edges = {
        module: imported_modules(module, tree, trees) for module, tree in trees.items()
    }
    reached = set(homes) & set(trees)
    for _round in range(len(trees) + 1):
        grown = {module for module, named in edges.items() if named & reached}
        if grown <= reached:
            break
        reached |= grown
    return frozenset(reached)


#: A text that spells a dotted path, optionally split once by a colon: the
#: shape ``pkgutil.resolve_name`` reads as ``module:attr`` or ``module.attr``.
_NAMED_PATH = re.compile(
    r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?::[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)?"
)


def named_object(text: str) -> object | None:
    """The object *text* names as ``module:attr`` or ``module.attr``, or ``None``.

    What ``pkgutil.resolve_name`` answers for it, which is how a string
    constant naming a function is turned into that function at run time.
    Only a text that spells a dotted path inside the package is read; a text
    that names nothing there answers ``None``.
    """
    if not _NAMED_PATH.fullmatch(text) or not text.startswith(f"{SOURCE_ROOT.name}."):
        return None
    try:
        return pkgutil.resolve_name(text)
    except (ImportError, AttributeError, ValueError):
        return None


def _dotted_name(relative: str) -> str:
    """The dotted module name a posix path under the package is imported as."""
    parts = relative.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join((SOURCE_ROOT.name, *parts))


class _SourceText(importlib.abc.SourceLoader):
    """A loader that runs a module from text held in memory."""

    def __init__(self, path: str, source: str) -> None:
        self.path = path
        self.source = source

    def get_filename(self, fullname: str) -> str:
        return self.path

    def get_data(self, path: str) -> bytes:
        return self.source.encode("utf-8")


def module_namespace(
    relative: str, source: str, root: Path = SOURCE_ROOT
) -> Mapping[str, object]:
    """The globals *source* binds when it runs as the package module *relative*.

    The imported module's own globals when *source* is that module's text on
    disk, so the objects read here are the objects the package runs with.
    Any other text is run as a fresh module under the same name, never
    registered in ``sys.modules``, so a planted or changed module is read by
    what it binds rather than by how it spells it.
    """
    dotted = _dotted_name(relative)
    path = root / relative
    if path.is_file() and path.read_text(encoding="utf-8") == source:
        return vars(importlib.import_module(dotted))
    loader = _SourceText(str(path), source)
    module = importlib.util.module_from_spec(
        importlib.machinery.ModuleSpec(
            dotted,
            loader,
            origin=str(path),
            is_package=relative.endswith("__init__.py"),
        )
    )
    loader.exec_module(module)
    return vars(module)


def _unwrapped(value: object) -> tuple[object, ...]:
    """*value* and every object it stands in for, one hop at a time.

    A ``functools.partial`` stands for its function and a static method for
    its own.  Bounded by a seen set, so a cycle ends the walk.
    """
    found: list[object] = []
    seen: set[int] = set()
    current: object | None = value
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        found.append(current)
        if isinstance(current, functools.partial):
            current = current.func
        elif isinstance(current, staticmethod):
            current = current.__func__
        else:
            current = None
    return tuple(found)


def _import_bindings(relative: str, tree: ast.Module) -> dict[str, list[object]]:
    """Local name -> the objects an import anywhere in *tree* binds it to.

    An import inside a function binds its name as surely as one at the top,
    so both are read; a relative import is resolved against the module's own
    package, and ``from package import name`` binds the submodule when the
    package holds no such attribute yet, as the import itself would.  An
    import that does not resolve binds nothing.
    """
    dotted = _dotted_name(relative)
    package = dotted if relative.endswith("__init__.py") else dotted.rpartition(".")[0]
    bound: dict[str, list[object]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                named = alias.name if alias.asname else alias.name.partition(".")[0]
                try:
                    module = importlib.import_module(named)
                except ImportError:
                    continue
                bound.setdefault(alias.asname or named, []).append(module)
        elif isinstance(node, ast.ImportFrom):
            try:
                base = importlib.util.resolve_name(
                    "." * node.level + (node.module or ""), package
                )
                home = importlib.import_module(base)
            except (ImportError, ValueError):
                continue
            for alias in node.names:
                value = inspect.getattr_static(home, alias.name, _ABSENT)
                if value is _ABSENT:
                    try:
                        value = importlib.import_module(f"{base}.{alias.name}")
                    except ImportError:
                        continue
                bound.setdefault(alias.asname or alias.name, []).append(value)
    return bound


#: What a lookup answers when the name is not there.
_ABSENT = object()
#: A definition a reference can sit inside.
_Scope = ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef


def _denoted(
    node: ast.expr,
    namespace: Mapping[str, object],
    bound: Mapping[str, list[object]],
) -> tuple[object, ...]:
    """Every object an expression can denote here, each with what it stands in for.

    A loaded name through the module's globals and through every import that
    binds it, an attribute through each object its receiver denotes, and a
    string constant through ``named_object``.
    """
    candidates: list[object] = []
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        if node.id in namespace:
            candidates.append(namespace[node.id])
        candidates.extend(bound.get(node.id, ()))
    elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
        for receiver in _denoted(node.value, namespace, bound):
            value = inspect.getattr_static(receiver, node.attr, _ABSENT)
            if value is not _ABSENT:
                candidates.append(value)
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        named = named_object(node.value)
        if named is not None:
            candidates.append(named)
    return tuple(found for value in candidates for found in _unwrapped(value))


def referencing_definitions(
    relative: str,
    tree: ast.Module,
    namespace: Mapping[str, object],
    *,
    wanted: Collection[object],
) -> tuple[tuple[str, ast.AST], ...]:
    """Every definition of *tree* whose text refers to one of *wanted*, by identity.

    ``(dotted definition, its node)``.  A reference is any expression that
    denotes the object itself, called or not: a name the module's globals or
    an import anywhere in it binds to the object (an aliased import, an
    import inside a function, a module-level rebinding, a re-export), an
    attribute of a module or class that is the object, a string constant
    naming it as ``module:attr`` or ``module.attr``, and a
    ``functools.partial`` or static method of it.

    Each function holding a reference is a definition here, and so is every
    function enclosing that one, so a closure's caller is read with it.  A
    reference outside every function is recorded against the class body it
    sits in, or ``<module>`` with the whole module as its node.

    Not seen: an object reached through a value handed in at run time — an
    argument, an attribute of an instance, a mapping — and a name built at
    run time.
    """
    targets = {id(value) for value in wanted}
    bound = _import_bindings(relative, tree)
    found: dict[int, tuple[str, ast.AST]] = {}

    def record(scopes: tuple[_Scope, ...]) -> None:
        names: list[str] = []
        functions: list[tuple[str, ast.AST]] = []
        for scope in scopes:
            names.append(scope.name)
            if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef):
                functions.append((".".join(names), scope))
        if functions:
            for name, scope in functions:
                found[id(scope)] = (name, scope)
        elif scopes:
            found[id(scopes[-1])] = (".".join(names), scopes[-1])
        else:
            found[id(tree)] = ("<module>", tree)

    def walk(node: ast.AST, scopes: tuple[_Scope, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr) and any(
                id(value) in targets for value in _denoted(child, namespace, bound)
            ):
                record(scopes)
            inner = scopes
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                inner = (*scopes, child)
            walk(child, inner)

    walk(tree, ())
    return tuple(sorted(found.values(), key=lambda pair: pair[0]))
