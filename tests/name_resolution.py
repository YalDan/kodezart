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

The last group of functions resolves by object rather than by spelling: they
read live functions — the ``def`` a code object was compiled from, and what
each of its reads resolves to in the namespace it runs in — so they import
what they read. Their own limits are stated on :func:`live_references`.

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

``named_object``, ``module_namespace``, ``denoted_objects``, ``loaded_values`` and
``referencing_definitions`` resolve by the object instead of by the word: a
string constant naming one, a literal name read as an attribute
(``getattr``, ``operator.attrgetter``, ``vars`` and ``__dict__``) and a local
assigned inside the definition included, and each states its own reach.
"""

import ast
import builtins
import dataclasses
import dis
import functools
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import inspect
import linecache
import operator
import pkgutil
import re
import string
import sys
import types
import typing
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, get_args

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
# Resolution by object: what a live definition reads, not what it spells
# ---------------------------------------------------------------------------

#: A value no route reaches, distinct from every value a route can reach.
_UNREACHED = object()


@functools.cache
def _parsed_file(filename: str) -> ast.Module:
    """The module a code object was compiled from, read through ``linecache``."""
    return ast.parse("".join(linecache.getlines(filename)))


def compiled_def(
    function: types.FunctionType,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """The ``def`` the live *function*'s code object was compiled from.

    Found by the code object, not by the name the function is reached under:
    a function replaced by a wrapper — a decorator, or a rebinding such as
    ``f = wrap(f)`` — is the wrapper's own ``def`` here, whatever
    ``functools.wraps`` copied onto it. A decorated ``def`` starts at its
    first decorator, which is the line the code object records.
    """
    code = function.__code__
    for node in ast.walk(_parsed_file(code.co_filename)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = min([node.lineno, *(line.lineno for line in node.decorator_list)])
        if node.name == code.co_name and first == code.co_firstlineno:
            return node
    raise AssertionError(f"no def in {code.co_filename} compiles to {function!r}")


def _global_reads(code: types.CodeType) -> frozenset[str]:
    """Every name *code*, or code nested in it, loads from the module globals."""
    names = {
        instruction.argval
        for instruction in dis.get_instructions(code)
        if instruction.opname == "LOAD_GLOBAL"
    }
    for constant in code.co_consts:
        if isinstance(constant, types.CodeType):
            names |= _global_reads(constant)
    return frozenset(names)


def unwrapped(value: object) -> object:
    """*value* with ``functools.partial``, bound methods and method wrappers undone."""
    while True:
        if isinstance(value, functools.partial):
            value = value.func
        elif isinstance(value, types.MethodType | staticmethod | classmethod):
            value = value.__func__
        else:
            return value


def _local_imports(node: ast.AST) -> dict[str, object]:
    """Local spelling -> the object an import inside a definition binds."""
    bound: dict[str, object] = {}
    for inner in ast.walk(node):
        if isinstance(inner, ast.Import):
            for alias in inner.names:
                if alias.asname is not None:
                    bound[alias.asname] = importlib.import_module(alias.name)
                else:
                    top = alias.name.split(".")[0]
                    bound[top] = importlib.import_module(top)
        elif (
            isinstance(inner, ast.ImportFrom)
            and inner.level == 0
            and inner.module is not None
        ):
            module = importlib.import_module(inner.module)
            for alias in inner.names:
                bound[alias.asname or alias.name] = getattr(module, alias.name)
    return bound


def live_references(function: types.FunctionType) -> dict[str, object]:
    """Spelling -> the object each read of *function* resolves to, live.

    Read off the ``def`` its code was compiled from and resolved in the
    namespace it runs in: a global read (the names its bytecode loads as
    globals, so a parameter or local of the same word is not one), a
    builtin, an import inside the definition, an attribute of a module
    reached any of those ways, and ``getattr`` of a module or
    ``importlib.import_module`` with a string literal. So an alias, a
    rebinding and a module-level ``def`` of the same word are each read as
    the object the function will call, and so is a ``globals()`` or
    ``setattr`` write that has already run — one made at import. The
    namespace is read as it stands when this is called: a write made later,
    inside a function that runs at boot, is not seen. A read through a
    receiver that is no module — an attribute of ``self``, of a parameter
    or of a class — is not resolved.
    """
    node = compiled_def(function)
    namespace = function.__globals__
    reads = _global_reads(function.__code__)
    local = _local_imports(node)

    def value_of(expr: ast.AST) -> object:
        if isinstance(expr, ast.Name):
            if expr.id in local:
                return local[expr.id]
            if expr.id not in reads:
                return _UNREACHED
            if expr.id in namespace:
                return namespace[expr.id]
            return getattr(builtins, expr.id, _UNREACHED)
        if isinstance(expr, ast.Attribute):
            base = value_of(expr.value)
            if isinstance(base, types.ModuleType):
                return getattr(base, expr.attr, _UNREACHED)
            return _UNREACHED
        if isinstance(expr, ast.Call) and expr.args:
            literal = expr.args[-1]
            if not (
                isinstance(literal, ast.Constant) and isinstance(literal.value, str)
            ):
                return _UNREACHED
            callee = value_of(expr.func)
            if callee is getattr and len(expr.args) == 2:
                base = value_of(expr.args[0])
                if isinstance(base, types.ModuleType):
                    return getattr(base, literal.value, _UNREACHED)
            if callee is importlib.import_module and len(expr.args) == 1:
                return importlib.import_module(literal.value)
        return _UNREACHED

    found: dict[str, object] = {}
    for inner in ast.walk(node):
        if isinstance(inner, ast.Name | ast.Attribute | ast.Call):
            value = value_of(inner)
            if value is not _UNREACHED:
                found[ast.unparse(inner)] = value
    return found


def home(value: object) -> str:
    """Where *value* was made: ``module.qualname``, a module's name, or its repr.

    Read off the object, so two spellings of one object share a home and a
    stand-in under the same word does not.
    """
    if isinstance(value, types.ModuleType):
        return value.__name__
    qualname = getattr(value, "__qualname__", None)
    module = getattr(value, "__module__", None)
    if isinstance(qualname, str) and isinstance(module, str):
        return f"{module}.{qualname}"
    return repr(value)


def written(function: types.FunctionType) -> bool:
    """Whether *function* was compiled from a file of the source tree."""
    return Path(function.__code__.co_filename).is_relative_to(SOURCE_ROOT)


def in_package(value: object) -> bool:
    """Whether *value* was made by a module of the production package."""
    module = getattr(value, "__module__", None)
    return isinstance(module, str) and (
        module == SOURCE_ROOT.name or module.startswith(f"{SOURCE_ROOT.name}.")
    )


def written_methods(owner: type) -> tuple[types.FunctionType, ...]:
    """Every method *owner*'s own body writes, nested classes' included.

    A ``staticmethod``, a ``classmethod`` and each function of a
    ``property`` are the function written. A method a class decorator or a
    metaclass generates — a dataclass's ``__eq__``, a model's validator
    wrapper — is compiled from no file of the tree and is not one. Nor is a
    function written outside the class body and assigned into it: it keeps
    its own qualified name, so the body does not write it.
    """
    found: list[types.FunctionType] = []
    for value in vars(owner).values():
        if isinstance(value, property):
            candidates: tuple[object, ...] = (value.fget, value.fset, value.fdel)
        else:
            candidates = (unwrapped(value),)
        for candidate in candidates:
            nested = getattr(candidate, "__qualname__", "")
            if not nested.startswith(f"{owner.__qualname__}."):
                continue
            if isinstance(candidate, types.FunctionType) and written(candidate):
                found.append(candidate)
            elif isinstance(candidate, type):
                found.extend(written_methods(candidate))
    return tuple(found)


def package_functions(root: Path = SOURCE_ROOT) -> tuple[types.FunctionType, ...]:
    """Every function the package writes: module level and every class's methods.

    Each module under *root* is imported and read as it runs, so a function
    is found by the object a module holds, whatever it is bound as there.
    """
    found: dict[int, types.FunctionType] = {}
    for path in sorted(root.rglob("*.py")):
        parts = [
            part
            for part in path.relative_to(root).with_suffix("").parts
            if part != "__init__"
        ]
        module = importlib.import_module(".".join([root.name, *parts]))
        for value in vars(module).values():
            value = unwrapped(value)
            if getattr(value, "__module__", None) != module.__name__:
                continue
            if isinstance(value, types.FunctionType) and written(value):
                found[id(value)] = value
            elif isinstance(value, type):
                found.update((id(method), method) for method in written_methods(value))
    return tuple(found.values())


def declares(annotation: object, wanted: type) -> bool:
    """Whether an evaluated annotation declares *wanted* anywhere inside it."""
    return annotation is wanted or any(
        declares(one, wanted) for one in get_args(annotation)
    )


def planted_module(source: str, *, name: str, directory: Path) -> types.ModuleType:
    """*source* run as the module *name*, from a file in *directory*.

    So a guard's control reads a live object built from planted source
    through the same functions that read the shipped one. The module is in
    ``sys.modules`` under *name* while it runs, so a write through
    ``sys.modules[__name__]`` lands on it, and the shipped module is put
    back afterwards.
    """
    path = directory / f"{name.replace('.', '_')}.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{path} cannot be loaded as {name}")
    module = importlib.util.module_from_spec(spec)
    shipped = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if shipped is None:
            del sys.modules[name]
        else:
            sys.modules[name] = shipped
    return module


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
      ``importlib.import_module`` or ``__import__`` is handed one;
    - a string constant that names an object as ``module:attr`` or
      ``module.attr``, the way ``pkgutil.resolve_name`` is handed one:
      resolved through ``named_object``, it names every module along its
      dotted path and the module the object itself is defined in.

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
            named.update(_modules_named_by(node.value))
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


def _modules_named_by(text: str) -> frozenset[str]:
    """The dotted modules a string naming an object names, or none.

    Every prefix of its dotted path, the way an import of the path names
    each package along it, and the module the named object is defined in,
    or the object itself when it is a module.  A text ``named_object``
    resolves to nothing names no module here.
    """
    named = named_object(text)
    if named is None:
        return frozenset()
    parts = text.replace(":", ".").split(".")
    home = (
        named.__name__
        if inspect.ismodule(named)
        else getattr(named, "__module__", None)
    )
    return frozenset(
        {
            *(".".join(parts[:end]) for end in range(1, len(parts) + 1)),
            *([home] if isinstance(home, str) else []),
        }
    )


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
#: A definition whose assignments bind locals.
_Function = ast.FunctionDef | ast.AsyncFunctionDef


def _assigned(node: ast.AST) -> dict[str, list[ast.expr]]:
    """Local name -> every expression an assignment under *node* binds it to.

    An assignment, an annotated assignment and a walrus, anywhere under
    *node*, a nested definition included, so a closure's locals are read
    with its enclosing function's.  A tuple-unpacking target binds nothing
    here.
    """
    assigned: dict[str, list[ast.expr]] = {}
    for inner in ast.walk(node):
        if isinstance(inner, ast.Assign):
            targets = [
                target for target in inner.targets if isinstance(target, ast.Name)
            ]
            value = inner.value
        elif isinstance(inner, ast.AnnAssign | ast.NamedExpr):
            if inner.value is None or not isinstance(inner.target, ast.Name):
                continue
            targets = [inner.target]
            value = inner.value
        else:
            continue
        for target in targets:
            assigned.setdefault(target.id, []).append(value)
    return assigned


@dataclass(frozen=True)
class Bindings:
    """What one module's names are bound to, for reading an expression by object.

    ``namespace`` is the module's globals after import; ``imported`` maps a
    local name to the objects an import anywhere in the module binds it to;
    ``assigned`` maps a local name to the expressions the assignments inside
    the definition being read bind it to.
    """

    namespace: Mapping[str, object]
    imported: Mapping[str, list[object]]
    assigned: Mapping[str, list[ast.expr]] = dataclasses.field(default_factory=dict)


def bindings(
    relative: str,
    tree: ast.Module,
    namespace: Mapping[str, object],
    *,
    within: ast.AST | None = None,
) -> Bindings:
    """The bindings of module *relative*, with the locals assigned under *within*."""
    return Bindings(
        namespace=namespace,
        imported=_import_bindings(relative, tree),
        assigned=_assigned(within) if within is not None else {},
    )


def _attribute_path(receiver: object, dotted: str) -> tuple[object, ...]:
    """The object a dotted attribute path reaches from *receiver*, statically."""
    current = receiver
    for part in dotted.split("."):
        current = inspect.getattr_static(current, part, _ABSENT)
        if current is _ABSENT:
            return ()
    return (current,)


def _literal_names(call: ast.Call) -> tuple[str, ...]:
    """The string constants *call* is handed by position, in order."""
    return tuple(
        argument.value
        for argument in call.args
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    )


def _one_of(values: Collection[object], target: object) -> bool:
    """Whether *target* itself is among *values*."""
    return any(value is target for value in values)


def _named(name: str, bound: Bindings, following: frozenset[str]) -> list[object]:
    """What a loaded name denotes: its global, its imports, its locals, its builtin."""
    found: list[object] = []
    if name in bound.namespace:
        found.append(bound.namespace[name])
    found.extend(bound.imported.get(name, ()))
    if name not in following:
        for value in bound.assigned.get(name, ()):
            found.extend(denoted_objects(value, bound, following | {name}))
    if not (
        name in bound.namespace or name in bound.imported or name in bound.assigned
    ):
        builtin = getattr(builtins, name, _ABSENT)
        if builtin is not _ABSENT:
            found.append(builtin)
    return found


def _looked_up(
    node: ast.Subscript, bound: Bindings, following: frozenset[str]
) -> list[object]:
    """What ``vars(x)["name"]`` or ``x.__dict__["name"]`` denotes: x's attribute."""
    if not (isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)):
        return []
    holder = node.value
    if isinstance(holder, ast.Attribute) and holder.attr == "__dict__":
        receivers = denoted_objects(holder.value, bound, following)
    elif (
        isinstance(holder, ast.Call)
        and len(holder.args) == 1
        and _one_of(denoted_objects(holder.func, bound, following), builtins.vars)
    ):
        receivers = denoted_objects(holder.args[0], bound, following)
    else:
        return []
    return [
        found
        for receiver in receivers
        for found in _attribute_path(receiver, node.slice.value)
    ]


def _called_objects(
    call: ast.Call, bound: Bindings, following: frozenset[str]
) -> list[object]:
    """What a call denotes: a named object it is handed, or a literal name it reads."""
    found: list[object] = []
    for argument in (*call.args, *(keyword.value for keyword in call.keywords)):
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            named = named_object(argument.value)
            if named is not None:
                found.append(named)
    if (
        _one_of(denoted_objects(call.func, bound, following), builtins.getattr)
        and len(call.args) >= 2
        and isinstance(call.args[1], ast.Constant)
        and isinstance(call.args[1].value, str)
    ):
        for receiver in denoted_objects(call.args[0], bound, following):
            found.extend(_attribute_path(receiver, call.args[1].value))
    if isinstance(call.func, ast.Call) and _one_of(
        denoted_objects(call.func.func, bound, following), operator.attrgetter
    ):
        for argument in call.args:
            for receiver in denoted_objects(argument, bound, following):
                for path in _literal_names(call.func):
                    found.extend(_attribute_path(receiver, path))
    return found


def denoted_objects(
    node: ast.expr, bound: Bindings, following: frozenset[str] = frozenset()
) -> tuple[object, ...]:
    """Every object an expression can denote under *bound*, and their stand-ins.

    Syntax, resolved by object after import.  A loaded name through the
    module's globals, through every import that binds it, and through every
    expression an assignment inside the definition read binds it to, so a
    local bound from any expression read here is followed; an attribute
    through each object its receiver denotes; a string constant through
    ``named_object``; a call handed a string constant naming an object as
    that object, the way ``pkgutil.resolve_name("kodezart.domain:gap")`` or
    ``importlib.import_module("kodezart.domain.gap")`` returns it, so an
    attribute taken off such a call, or off a local bound to it, is read off
    the named object.  Literal names count wherever they appear:
    ``getattr(x, "name")``, ``operator.attrgetter("a.name")(x)``,
    ``vars(x)["name"]`` and ``x.__dict__["name"]`` are that attribute of
    each object ``x`` denotes, through ``inspect.getattr_static``.  A name
    bound nowhere in the module is the builtin of that word, which is how
    ``getattr`` and ``vars`` are read as themselves.  A ``functools.partial``
    and a static method stand in for their function.

    Bounded: *following* holds the locals on the chain being followed, so a
    local is followed once along any one chain and a name assigned from
    itself ends the walk.
    """
    candidates: list[object] = []
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        candidates.extend(_named(node.id, bound, following))
    elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
        for receiver in denoted_objects(node.value, bound, following):
            candidates.extend(_attribute_path(receiver, node.attr))
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        named = named_object(node.value)
        if named is not None:
            candidates.append(named)
    elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        candidates.extend(_looked_up(node, bound, following))
    elif isinstance(node, ast.Call):
        candidates.extend(_called_objects(node, bound, following))
    return tuple(found for value in candidates for found in _unwrapped(value))


def loaded_values(
    relative: str,
    tree: ast.Module,
    namespace: Mapping[str, object],
    within: ast.AST,
) -> tuple[object, ...]:
    """Every object a loaded name or attribute inside *within* can denote.

    Read the way ``referencing_definitions`` reads a reference
    (``denoted_objects``): a name through the module's globals, through every import
    anywhere in *tree* that binds it and through every assignment inside
    *within* that binds it, an attribute through each object its receiver
    denotes.  So a constant a name is bound to — in this module, in the
    module an import names, or by a local assigned from either — is read by
    its value, not by its word.  Outside the reach: a value handed across a
    function boundary, where the other function is not resolved at this site
    (returned from a helper, stored on an object and read elsewhere, or
    passed through a container built elsewhere); a name built at run time;
    and a binding made only when a function runs (``setattr`` or
    ``globals()`` inside a function body).
    """
    bound = bindings(relative, tree, namespace, within=within)
    return tuple(
        value
        for node in ast.walk(within)
        if isinstance(node, ast.Name | ast.Attribute) and isinstance(node.ctx, ast.Load)
        for value in denoted_objects(node, bound)
    )


def referencing_definitions(
    relative: str,
    tree: ast.Module,
    namespace: Mapping[str, object],
    *,
    wanted: Collection[object],
) -> tuple[tuple[str, ast.AST], ...]:
    """Every definition of *tree* whose text refers to one of *wanted*, by identity.

    ``(dotted definition, its node)``.  A reference is any expression that
    denotes the object itself (``denoted_objects``), called or not: a name the
    module's globals or an import anywhere in it binds to the object (an
    aliased import, an import inside a function, a module-level rebinding, a
    re-export), a local assigned inside the definition from any such
    expression, an attribute of a module or class that is the object, a
    string constant naming it as ``module:attr`` or ``module.attr``, an
    attribute taken off a call handed such a string or off a local bound to
    one, a literal name read as an attribute of an object that is resolved
    here (``getattr``, ``operator.attrgetter``, ``vars`` and ``__dict__``),
    and a ``functools.partial`` or static method of it.

    Each function holding a reference is a definition here, and so is every
    function enclosing that one, so a closure's caller is read with it, and
    the locals a closure can read are the outermost enclosing function's
    together with its own.  A reference outside every function is recorded
    against the class body it sits in, or ``<module>`` with the whole module
    as its node.

    Outside the reach: a value handed across a function boundary, where the
    other function is not resolved at this site (returned from a helper,
    stored on an object and read elsewhere, or passed through a container
    built elsewhere); a name built at run time; and a binding made only when
    a function runs (``setattr`` or ``globals()`` inside a function body).
    """
    targets = {id(value) for value in wanted}
    bound = bindings(relative, tree, namespace)
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

    def walk(node: ast.AST, scopes: tuple[_Scope, ...], bound: Bindings) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr) and any(
                id(value) in targets for value in denoted_objects(child, bound)
            ):
                record(scopes)
            inner, within = scopes, bound
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                inner = (*scopes, child)
                if isinstance(child, _Function) and not any(
                    isinstance(scope, _Function) for scope in scopes
                ):
                    within = replace(bound, assigned=_assigned(child))
            walk(child, inner, within)

    walk(tree, (), bound)
    return tuple(sorted(found.values(), key=lambda pair: pair[0]))


# ---------------------------------------------------------------------------
# Resolution by identity: which definition a spelling IS, across the modules.
#
# Separate from the functions above and with limits of its own, stated in
# each function's docstring: unlike them, it resolves a relative import, a
# star import and an import written inside a function, and it reads a
# tuple-unpacking target as binding each name.
#
# Outside its reach, the one stated limit of every guard built on it: a
# value handed across a function boundary, where the other function is not
# resolved at this site (returned from a helper, stored on an object and read
# elsewhere, or passed through a container built elsewhere); a name built at
# run time; a binding made only when a function runs (``setattr`` or
# ``globals()`` inside a function body).
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
    and each name bound in its body, plain or annotated, that a top-level
    class states: ``Holder.mint`` for ``class Holder: mint = ...``, never a
    method by that name.  An import binds its spelling wherever in the
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
                    elif isinstance(member, ast.Assign | ast.AnnAssign):
                        attributes = tuple(
                            (module, f"{statement.name}.{word}")
                            for target in (
                                member.targets
                                if isinstance(member, ast.Assign)
                                else [member.target]
                            )
                            for word in _target_words(target)
                        )
                        units.update(dict.fromkeys(attributes, member))
                        if attributes:
                            starts[id(member)] = attributes
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


def _literal(node: ast.AST | None) -> str | None:
    """The text of a string constant, or ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _called(node: ast.Call) -> str:
    """The last word of a call's callee: ``getattr``, ``import_module``."""
    return (_spelling(node.func) or "").rsplit(".", 1)[-1]


def _argument(node: ast.Call, position: int, keyword: str) -> ast.expr | None:
    """The argument a call hands to one parameter, by position or keyword."""
    if len(node.args) > position and not isinstance(node.args[position], ast.Starred):
        return node.args[position]
    return next((word.value for word in node.keywords if word.arg == keyword), None)


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


def _chain(receiver: ast.expr, dotted: str) -> ast.expr:
    """``receiver.a.b`` for ``"a.b"``, as the attribute expression it spells."""
    for word in dotted.split("."):
        receiver = ast.Attribute(value=receiver, attr=word, ctx=ast.Load())
    return receiver


#: The spellings of the table of imported modules, read off the object.
_MODULE_TABLES = frozenset({f"{sys.__name__}.modules", "modules"})


def denoted(
    index: IdentityIndex, module: str, node: ast.expr
) -> tuple[frozenset[Key], str | None]:
    """The definitions an expression in *module* IS, or the module it is.

    A word through its module's own definitions and imports; an attribute of
    a module through that module's, a submodule included; an attribute of a
    class through the class's own method or class-body name.  A literal name
    counts wherever it is written: ``getattr(x, "word")``,
    ``x.__dict__["word"]``, ``vars(x)["word"]`` and
    ``operator.attrgetter("a.word")(x)`` as that attribute;
    ``importlib.import_module`` with a literal name (a relative one resolved
    against its literal package), ``__import__`` with a literal name (the
    named module with a ``fromlist``, its top package without) and
    ``sys.modules["name"]`` as that module.  A value of any other origin —
    a parameter, an instance, a call's result — denotes nothing here.
    """
    if isinstance(node, ast.Name):
        return _lookup(index, module, node.id)
    if isinstance(node, ast.Attribute):
        return _attribute_of(index, module, node.value, node.attr)
    if isinstance(node, ast.Subscript) and (word := _literal(node.slice)) is not None:
        table = node.value
        if isinstance(table, ast.Attribute) and table.attr == "__dict__":
            return _attribute_of(index, module, table.value, word)
        if (
            isinstance(table, ast.Call)
            and _called(table) == vars.__name__
            and len(table.args) == 1
        ):
            return _attribute_of(index, module, table.args[0], word)
        if _spelling(table) in _MODULE_TABLES:
            return frozenset(), _module_home(word, index.trees)
        return frozenset(), None
    if not isinstance(node, ast.Call):
        return frozenset(), None
    reflected = _reflected(node)
    if reflected is not None:
        return _attribute_of(index, module, *reflected)
    called = _called(node)
    name = _literal(_argument(node, 0, "name"))
    if called == importlib.import_module.__name__ and name is not None:
        package = _literal(_argument(node, 1, "package"))
        if name.startswith("."):
            if package is None:
                return frozenset(), None
            name = importlib.util.resolve_name(name, package)
        return frozenset(), _module_home(name, index.trees)
    if called == builtins.__import__.__name__ and name is not None:
        listed = _argument(node, 3, "fromlist")
        whole = isinstance(listed, ast.Tuple | ast.List | ast.Set) and bool(listed.elts)
        return frozenset(), _module_home(
            name if whole else name.split(".")[0], index.trees
        )
    getter = node.func
    if (
        isinstance(getter, ast.Call)
        and _called(getter) == operator.attrgetter.__name__
        and len(getter.args) == 1
        and (dotted := _literal(getter.args[0])) is not None
        and len(node.args) == 1
    ):
        return denoted(index, module, _chain(node.args[0], dotted))
    return frozenset(), None


def _attribute_of(
    index: IdentityIndex, module: str, receiver: ast.expr, word: str
) -> tuple[frozenset[Key], str | None]:
    """What ``receiver.word`` denotes in *module*."""
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


def _named_by_text(index: IdentityIndex, module: str, node: ast.Call) -> frozenset[Key]:
    """What a literal names inside a call's text: ``"{0.word}".format(x)``.

    Each replacement field of a literal format string, read as the attribute
    chain it formats off the argument it names.
    """
    text = _literal(node.func.value) if isinstance(node.func, ast.Attribute) else None
    if text is None or not isinstance(node.func, ast.Attribute):
        return frozenset()
    if node.func.attr != str.format.__name__:
        return frozenset()
    try:
        fields = [field for _, field, _, _ in string.Formatter().parse(text) if field]
    except ValueError:
        return frozenset()
    found: set[Key] = set()
    automatic = 0
    for field in fields:
        head, _, tail = field.partition(".")
        head = head.split("[", 1)[0]
        argument: ast.expr | None
        if not head:
            argument = node.args[automatic] if automatic < len(node.args) else None
            automatic += 1
        elif head.isdigit():
            argument = node.args[int(head)] if int(head) < len(node.args) else None
        else:
            argument = next(
                (word.value for word in node.keywords if word.arg == head), None
            )
        dotted = tail.split("[", 1)[0]
        if argument is not None and dotted:
            found |= denoted(index, module, _chain(argument, dotted))[0]
    return frozenset(found)


def _named_by_path(index: IdentityIndex, text: str) -> frozenset[Key]:
    """The definition a ``"package.module:word"`` string names, if any.

    Its first word after the colon: a member of a class after a dot is read
    as the class, which holds whatever its members name.
    """
    dotted, colon, attribute = text.partition(":")
    home = _module_home(dotted, index.trees) if colon else None
    if home is None or not attribute:
        return frozenset()
    return _lookup(index, home, attribute.split(".")[0])[0]


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

    Each name, attribute, literal subscript, reflective call and from-import
    is resolved by :func:`denoted` — so an alias, a re-export, a module
    alias or a local import names the definition it binds, and a word that
    merely spells the same name does not.  A literal names what it spells
    wherever it is written: a replacement field of a literal format string,
    ``"{0.word}".format(x)``, names that attribute of its argument, and a
    ``"package.module:attr"`` string names that definition.  An attribute
    on a value whose origin is not resolved names nothing, except an
    attribute spelled like one of *members*, which names every method of
    that name, as does a string constant spelling one of them: a member is
    reached through the instance that holds it, so its spelling is all
    there is to key on.
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
            if isinstance(node, ast.Name | ast.Attribute | ast.Subscript | ast.Call):
                keys, _ = denoted(index, module, node)
                word = node.attr if isinstance(node, ast.Attribute) else None
                if not keys and word in wanted:
                    keys = index.methods.get(word or "", frozenset())
                if isinstance(node, ast.Call):
                    keys = frozenset(keys) | _named_by_text(index, module, node)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                keys = (
                    index.methods.get(node.value, frozenset())
                    if node.value in wanted
                    else _named_by_path(index, node.value)
                )
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


def annotation_keys(
    index: IdentityIndex, module: str, annotation: ast.expr
) -> set[Key]:
    """Every definition an annotation in *module* names, anywhere inside it.

    Each name and attribute resolved by :func:`denoted`, and a string
    constant — a forward reference, whole or inside a subscript — read as
    the expression it spells.
    """
    found: set[Key] = set()
    for inner in ast.walk(annotation):
        if isinstance(inner, ast.Name | ast.Attribute):
            found |= denoted(index, module, inner)[0]
        elif (text := _literal(inner)) is not None:
            try:
                written = ast.parse(text, mode="eval").body
            except SyntaxError:
                continue
            found |= annotation_keys(index, module, written)
    return found


def declaring(index: IdentityIndex, types: Collection[Key]) -> frozenset[Key]:
    """Every class a value of *types* is validated into, to a fixed point.

    A top-level class one of whose declared fields — ``word: T`` in its
    body, a string annotation read as the expression it spells — names one
    of *types* anywhere in its annotation, or names a class or alias already
    found; a class whose base is one found; and a module-level alias whose
    value names one found (``Rows = list[Model]``, ``type Rows = ...``).
    Constructing or validating any of them turns raw values into a value of
    *types*.  Bounded by the definition count: every round adds one or stops.
    """
    named: dict[Key, set[Key]] = {}
    for key, node in index.units.items():
        module = key[0]
        if isinstance(node, ast.ClassDef) and key[1] == node.name:
            found: set[Key] = set()
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign):
                    found |= annotation_keys(index, module, statement.annotation)
            for base in node.bases:
                found |= annotation_keys(index, module, base)
            named[key] = found
        elif (
            isinstance(node, ast.Assign | ast.AnnAssign | ast.TypeAlias)
            and "." not in key[1]
            and node.value is not None
        ):
            named[key] = annotation_keys(index, module, node.value)
    wanted = frozenset(types)
    found_: frozenset[Key] = frozenset()
    for _ in range(len(named) + 1):
        grown = found_ | {
            key for key, names in named.items() if not names.isdisjoint(wanted | found_)
        }
        if grown == found_:
            break
        found_ = grown
    return found_ - wanted


#: The builtin type tests, whose class argument checks a value it never builds.
_TYPE_TESTS = frozenset({isinstance.__name__, issubclass.__name__})


def _declared(
    index: IdentityIndex, module: str, node: ast.AST, *, receiver: str
) -> dict[str, frozenset[Key]]:
    """Word -> the definitions its declaration names, inside *node*.

    With ``receiver="self"``, each ``self.<word>`` a class declares: a
    field in its body, or an annotated assignment in any of its methods.
    Otherwise each parameter and annotated local of a function.
    """
    declared: dict[str, set[Key]] = {}
    if receiver == "self" and isinstance(node, ast.ClassDef):
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign) and isinstance(
                statement.target, ast.Name
            ):
                declared.setdefault(statement.target.id, set()).update(
                    annotation_keys(index, module, statement.annotation)
                )
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.AnnAssign)
                and isinstance(inner.target, ast.Attribute)
                and _spelling(inner.target.value) == receiver
            ):
                declared.setdefault(inner.target.attr, set()).update(
                    annotation_keys(index, module, inner.annotation)
                )
    elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        for argument in parameters_of(node):
            if argument.annotation is not None:
                declared.setdefault(argument.arg, set()).update(
                    annotation_keys(index, module, argument.annotation)
                )
        for inner in ast.walk(node):
            if isinstance(inner, ast.AnnAssign) and isinstance(inner.target, ast.Name):
                declared.setdefault(inner.target.id, set()).update(
                    annotation_keys(index, module, inner.annotation)
                )
    return {word: frozenset(keys) for word, keys in declared.items()}


def validations(
    index: IdentityIndex,
    classes: Collection[Key],
    *,
    modules: Collection[str],
    methods: Collection[str] = (),
) -> tuple[Reference, ...]:
    """Every place one of *modules* builds or validates one of *classes*.

    A value that denotes one of them — called, read for a classmethod such
    as ``model_validate``, handed to ``TypeAdapter`` or any other call, or
    bound to a word — anywhere but an annotation or the class argument of a
    builtin ``isinstance``/``issubclass`` test, which check a value and
    build none; an import binds a word and denotes nothing.  And a call of
    one of *methods* on a receiver declared as one of them: a parameter or
    annotated local of an enclosing function, or ``self.<word>`` declared in
    the enclosing class, a string annotation read too.  A receiver of any
    other origin is not resolved.
    """
    wanted = frozenset(classes)
    builders = frozenset(methods)
    found: list[Reference] = []

    def visit(
        module: str,
        node: ast.AST,
        scope: tuple[str, ...],
        names: Mapping[str, frozenset[Key]],
        attributes: Mapping[str, frozenset[Key]],
    ) -> None:
        if isinstance(node, ast.ClassDef):
            attributes = _declared(index, module, node, receiver="self")
            scope = (*scope, node.name)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names = {**names, **_declared(index, module, node, receiver="")}
            scope = (*scope, node.name)
        keys: set[Key] = set()
        if isinstance(node, ast.Name | ast.Attribute | ast.Subscript | ast.Call):
            keys |= denoted(index, module, node)[0] & wanted
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in builders:
                held = node.func.value
                if isinstance(held, ast.Name):
                    keys |= names.get(held.id, frozenset()) & wanted
                elif (
                    isinstance(held, ast.Attribute) and _spelling(held.value) == "self"
                ):
                    keys |= attributes.get(held.attr, frozenset()) & wanted
        found.extend(
            Reference(
                module=module,
                line=getattr(node, "lineno", 0),
                key=key,
                definition=".".join(scope) if scope else "<module>",
                within=(),
                called=False,
                position="value",
            )
            for key in sorted(keys)
        )
        tested = (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _TYPE_TESTS
            and _lookup(index, module, node.func.id) == (frozenset(), None)
        )
        for field, value in ast.iter_fields(node):
            if field in {"annotation", "returns"}:
                continue
            for position, child in enumerate(
                value if isinstance(value, list) else [value]
            ):
                if tested and field == "args" and position == 1:
                    continue
                if isinstance(child, ast.AST):
                    visit(module, child, scope, names, attributes)

    for module in sorted(modules):
        tree = index.trees.get(module)
        if tree is not None:
            visit(module, tree, (), {}, {})
    return tuple(found)


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
#: value it casts.  Each word read off the object.
_WRAPPED = {
    functools.partial.__name__: 0,
    functools.partialmethod.__name__: 0,
    typing.cast.__name__: 1,
}

#: The calls that bind arguments to the function they are handed first.
_PARTIALS = frozenset({functools.partial.__name__, functools.partialmethod.__name__})


def _spread(call: ast.Call) -> ast.Call:
    """*call* with each literal ``*`` and ``**`` written out.

    A ``*`` of a tuple or list display becomes the positional arguments it
    spreads, and a ``**`` of a dict display the keywords its literal keys
    spell.  A ``*`` of anything else spreads values not written here, so
    the positional arguments from it on land nowhere.
    """
    arguments: list[ast.expr] = []
    for argument in call.args:
        if not isinstance(argument, ast.Starred):
            arguments.append(argument)
        elif isinstance(argument.value, ast.Tuple | ast.List):
            arguments.extend(argument.value.elts)
        else:
            break
    keywords: list[ast.keyword] = []
    for word in call.keywords:
        if word.arg is None and isinstance(word.value, ast.Dict):
            keywords.extend(
                ast.keyword(arg=name, value=value)
                for key, value in zip(word.value.keys, word.value.values, strict=True)
                if (name := _literal(key)) is not None
            )
        else:
            keywords.append(word)
    return ast.Call(func=call.func, args=arguments, keywords=keywords)


def _applied(call: ast.Call) -> Iterator[ast.Call]:
    """The calls *call* hands its arguments on at, each spread by :func:`_spread`.

    The call itself, and for ``partial(f, *args, **kwargs)`` or
    ``partialmethod`` the call of ``f`` with those arguments.
    """
    yield _spread(call)
    if (
        _called(call) in _PARTIALS
        and call.args
        and not isinstance(call.args[0], ast.Starred)
    ):
        yield _spread(
            ast.Call(func=call.args[0], args=call.args[1:], keywords=call.keywords)
        )


def _scope_of(key: Key) -> Key:
    """The class a method's ``self`` belongs to, or the key itself."""
    module, name = key
    return (module, name.split(".")[0]) if "." in name else key


def carried(index: IdentityIndex, keys: Collection[Key]) -> Carried:
    """Every holder a value of *keys* is handed to, to a fixed point.

    The value itself, not what calling it returns: ``mint = criterion_ref``
    hands the mint on, ``key = criterion_ref(row)`` hands on an identity.  A
    value is handed on by an assignment, annotated assignment, ``for``
    target or walrus to a name or an attribute, in a function, a module's
    top level or a class body, where a name bound is ``self.<word>`` of
    every class; by a positional or keyword argument to the parameter it
    lands on — ``def``, a class's ``__init__``, or a class's declared fields
    when it has none — with a ``*`` of a tuple or list display and a ``**``
    of a dict display written out, and through ``functools.partial`` or
    ``partialmethod`` to the function they bind; by a parameter default;
    and by ``return``, which makes every call of that definition a value of
    it.  An expression carries a value when it is one — a literal name
    included, as :func:`denoted` reads it — or is a conditional, boolean
    operation, tuple, list, set or dict display, subscript, starred value,
    walrus or ``await`` over one that does, or ``partial``,
    ``partialmethod`` or ``cast`` of one, or a call of a definition that
    returns one; a lambda or nested ``def`` whose body names one, or a
    lambda one of whose defaults carries one, carries it as well.  Any other
    call carries nothing, whatever its arguments.  An attribute assigned on
    any receiver, ``self`` included, is that attribute of every class.  A
    call through a receiver whose class is not resolved lands on every
    method of that name, and returns what any of them returns.  Each round
    adds a holder or a returner or stops, so the walk is bounded by the
    binding sites.
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
            isinstance(inner, ast.Name | ast.Attribute | ast.Subscript | ast.Call)
            and id(inner) not in annotated
            and (
                not denoted(index, module, inner)[0].isdisjoint(wanted | returners)
                or (
                    isinstance(inner, ast.Name | ast.Attribute)
                    and held(module, inner, scope)
                )
            )
            for inner in ast.walk(node)
        )

    def carries(module: str, node: ast.expr, scope: Key) -> bool:
        if isinstance(node, ast.Lambda):
            defaults = [*node.args.defaults, *node.args.kw_defaults]
            return names(module, node.body, scope) or any(
                carries(module, default, scope)
                for default in defaults
                if default is not None
            )
        if isinstance(node, ast.IfExp):
            return carries(module, node.body, scope) or carries(
                module, node.orelse, scope
            )
        if isinstance(node, ast.Name | ast.Attribute | ast.Subscript | ast.Call):
            if not denoted(index, module, node)[0].isdisjoint(wanted):
                return True
            if isinstance(node, ast.Name | ast.Attribute):
                return held(module, node, scope)
        parts: list[ast.expr | None] = []
        if isinstance(node, ast.BoolOp):
            parts = list(node.values)
        elif isinstance(node, ast.Tuple | ast.List | ast.Set):
            parts = list(node.elts)
        elif isinstance(node, ast.Dict):
            parts = list(node.values)
        elif isinstance(node, ast.Starred | ast.NamedExpr | ast.Await | ast.Subscript):
            parts = [node.value]
        elif isinstance(node, ast.Call):
            callee = denoted(index, module, node.func)[0]
            if not callee.isdisjoint(returners):
                return True
            if (
                not callee
                and isinstance(node.func, ast.Attribute)
                and not index.methods.get(node.func.attr, frozenset()).isdisjoint(
                    returners
                )
            ):
                return True
            position = _WRAPPED.get(_called(node))
            if position is not None and len(node.args) > position:
                parts = [node.args[position]]
        return any(carries(module, part, scope) for part in parts if part is not None)

    def bind(targets: Collection[ast.expr], scope: Key) -> set[Holder]:
        in_class = isinstance(index.units.get(scope), ast.ClassDef)
        bound: set[Holder] = set()
        for target in targets:
            words = set(_target_words(target))
            bound |= {(scope, word) for word in words}
            if in_class:
                bound |= {(everywhere, f"self.{word}") for word in words}
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

    def module_level(body: list[ast.stmt]) -> Iterator[ast.AST]:
        stack: list[ast.AST] = list(body)
        while stack:
            node = stack.pop()
            yield node
            if not isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ):
                stack.extend(ast.iter_child_nodes(node))

    #: Each binding site once, with the module and scope it is read in: a
    #: parameter default, a nested definition, an assignment, a ``for``, a
    #: ``return`` and a call, in a function, a class body or a module's own
    #: top level.  Collected in one walk, then re-read each round.
    work: list[tuple[str, Key, ast.AST]] = []
    for key, node in index.units.items():
        if isinstance(node, ast.ClassDef) and key[1] == node.name:
            work.extend(
                (key[0], key, inner)
                for inner in module_level(node.body)
                if isinstance(inner, _BINDING_SITES)
                and not isinstance(
                    inner, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
                )
            )
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
            for inner in module_level(tree.body)
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
            elif isinstance(node, ast.Call):
                for call in _applied(node):
                    if not any(
                        carries(module, argument, scope)
                        for argument in (
                            *call.args,
                            *(word.value for word in call.keywords),
                        )
                    ):
                        continue
                    for key, callee, receiver in callees(module, call):
                        for holder, argument in handed(
                            call, key, callee, receiver
                        ).items():
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
