"""One function weighs a graded sha against a head sha (KOD-413, KOD-696, KOD-596).

Two readers weighing the same two revisions is how a lapse comes to mean one
thing on a compliance mark and another on a lane check: one of them
eventually grows an ancestry test, a prefix match or a null case, and
nothing red says so.  The rule therefore lives in exactly one function, and
this guard keeps it there.

It does not try to recognise a head.  A head is just a string, and whatever
it is called -- ``head``, ``tip``, ``branch_head``, a value a port resolved
-- nothing static can tell it from any other.  The graded sha can be told,
because it is one field of one record.  So the guard keys on the graded side
alone and asks two questions of every scope in the package (a module body,
a class body, a function, a lambda, a comprehension):

* **Which scopes read the graded sha directly?**  Reading it directly means
  an attribute read of the field, a name spelled as the field, a subscript
  or ``.get`` (or any other read) by the field's name or its serialisation
  alias, a dotted name one of whose segments is either (the path
  ``attrgetter`` takes), a format field whose dotted or bracketed segments
  include either (``'{0.evidence.graded_sha}'.format(...)``,
  ``'{gradedSha}'.format(**row)``), a printf mapping key or a ``Template``
  placeholder equal to either (``'%(gradedSha)s' % row``,
  ``Template('$gradedSha').substitute(row)``), a ``match`` class pattern
  keyed on it, or a
  variable annotated as the Evidence record and used whole (iterated,
  dumped, handed on), which reads every field it has.  The annotation names
  the record by the record's own name or by object: a function's
  annotations are read off the imported function as the running program
  evaluated them, each name in an annotation is resolved in the enclosing
  class namespaces and then in the module's own namespace after import,
  and a name the running module does not bind is read through the
  module's imports (an import made only under ``TYPE_CHECKING``) and
  through the aliases written under ``TYPE_CHECKING``, in an enclosing
  class body, or as a bounded type parameter.  What it resolves to is the
  record when it is the record or a class derived from it, a ``NewType``
  over it, a ``TypeVar`` bound or constrained to it, a ``type`` alias, a
  plain or ``TypeAlias`` alias or a string spelling any of these, or any
  of these inside ``Optional``, ``Annotated`` or a union.  So an import
  under another name, a module alias, a subclass, a class-body alias used
  in a method's signature and a ``TYPE_CHECKING`` alias each name it, and a
  type derived from the record's base does not.  Inside that same scope the value is
  followed through every binding form to a fixed point: assignment,
  unpacking, a container stored into by subscript, a ``for`` target,
  ``with ... as``, the walrus, a comprehension target, a ``match`` capture,
  a default argument whose default carries it, and a closure over a name
  that carries it (from a function nested in the scope, or from a method or
  lambda of a class nested in it).  Every such scope is the rule or a
  registered row with its reason; a new one is red whatever it does with
  the value, and a row whose scope no longer reads the value is red too.
* **Where, inside each registered scope, is the value used?**  Every
  statement in which it appears is pinned verbatim in the scope's row, as a
  multiset (a compound statement by its header, its body elided).  A new
  use inside a registered scope is red, whatever the other operand is
  called: a comparison, a rebinding, a default-argument lambda, a container
  built from it.  The rule is the one scope whose uses are not pinned:
  weighing the pair is its whole job.  Each consultation of it is resolved
  by object, through the module's bindings and in the module's own
  namespace after import: its callee must be the rule in the rule's own
  module, so a definition, a star import or a ``globals()`` store
  shadowing the rule under the pinned call's text is red.

The field and its alias are read off ``CriterionEvidence.model_fields``; the
rule's module and name are read off the rule itself; the scanned tree is the
package the rule is packaged in.  The register lives beside this file in
``graded_sha_readers.json``.

The static assertion's reach, as the Check states it: it covers every scope
that reads the graded sha directly (the evidence field or its alias, and any
value bound from them inside that same scope) and pins every statement there
that touches the value, whatever the other operand is called.

The guard reads every module's syntax and resolves names in the module's own
namespace after import. Outside it: a value handed across a function
boundary (returned, passed, or stored on an object), a name built at run
time, and a binding made only when a function runs.

A record read whole through a value whose type the module does not declare
-- ``model_dump()``, ``dict(...)``, ``vars(...)`` or iteration on an
un-annotated ``evidence`` -- is the first of these: the value was passed in
across a function boundary, and nothing in the module says what it is.

Each is held as a fact by a test: ``test_a_value_returned_from_a_helper_is_not_seen``,
``test_a_name_built_at_run_time_is_not_seen``,
``test_a_binding_made_only_when_a_function_runs_is_not_seen`` and
``test_a_record_read_whole_without_a_declared_type_is_not_seen``.  In each,
the function that reads the field directly, or the same spelling made where
the guard reads, is seen, and what lies past the boundary is not.
``test_the_stated_limit_is_evasion_and_it_is_not_seen`` reads the rest of the
boundary as code: a global set in one function and compared in another, a
value stored on an object, and a value passed as an argument.
"""

import ast
import copy
import json
import re
import sys
import typing
from collections import ChainMap, Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from functools import cache
from importlib import import_module
from importlib.abc import SourceLoader
from importlib.util import resolve_name
from inspect import get_annotations, signature
from pathlib import Path
from string import Formatter, Template
from types import MappingProxyType, ModuleType
from typing import (
    Annotated,
    ForwardRef,
    NamedTuple,
    NewType,
    TypeAliasType,
    TypeVar,
    get_args,
    get_origin,
)

import pytest

from kodezart.domain.lapse import graded_state
from kodezart.types.domain.criterion_evidence import CriterionEvidence

#: The package the rule is packaged in, and so the tree it speaks for.
SOURCE = Path(sys.modules[graded_state.__module__].__file__ or "").resolve().parents[1]
#: Where the rule is written, and the name it is written under.
RULE_MODULE = (
    Path(sys.modules[graded_state.__module__].__file__ or "")
    .resolve()
    .relative_to(SOURCE)
    .as_posix()
)
RULE = graded_state.__name__
RULE_SITE = f"{RULE_MODULE}::{RULE}"

#: The identity a grading's revision is recorded under: the one name the
#: Evidence record and the rule both spell, so a rename of either is a
#: rename of both or this guard says so.
GRADED_IDENTITIES = frozenset(CriterionEvidence.model_fields) & frozenset(
    signature(graded_state).parameters
)
GRADED = min(GRADED_IDENTITIES, default="")
#: The graded sha's spellings: the field and the alias it is serialised under.
SPELLINGS = frozenset(
    spelling
    for info in (CriterionEvidence.model_fields.get(GRADED),)
    if info is not None
    for spelling in (GRADED, info.alias)
    if spelling
)
#: The record the field is declared on, and its other fields: a variable
#: annotated as this record reads the graded sha whenever it is used as
#: anything but a read of one of these.
RECORD = CriterionEvidence.__name__
OTHER_FIELDS = frozenset(CriterionEvidence.model_fields) - {GRADED}

DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
FUNCTIONS = (*DEFINITIONS, ast.Lambda)
NAMED = (*DEFINITIONS, ast.ClassDef)
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
SCOPES = (*FUNCTIONS, ast.ClassDef, *COMPREHENSIONS)
#: The scopes whose names a nested scope closes over.  A class body's names
#: are not visible to its methods, and a module's are globals.  A class
#: passes on what it closes over itself: its methods, its lambdas and the
#: classes inside it see the enclosing function's names, as Python resolves
#: them past the class body.
CLOSING = (*FUNCTIONS, *COMPREHENSIONS)
#: The nodes a use is pinned by: a statement, a ``match`` case or an
#: ``except`` clause, each rendered with its body elided.
CLAUSES = (ast.stmt, ast.match_case, ast.excepthandler)
ANONYMOUS = {
    ast.Lambda: "<lambda>",
    ast.ListComp: "<listcomp>",
    ast.SetComp: "<setcomp>",
    ast.GeneratorExp: "<genexpr>",
    ast.DictComp: "<dictcomp>",
}
MODULE = "<module>"

type ScopeNode = (
    ast.Module
    | ast.FunctionDef
    | ast.AsyncFunctionDef
    | ast.Lambda
    | ast.ClassDef
    | ast.ListComp
    | ast.SetComp
    | ast.GeneratorExp
    | ast.DictComp
)


def _split(node: ScopeNode) -> tuple[list[ast.AST], list[ast.AST]]:
    """What of a scope its enclosing scope evaluates, and what it evaluates.

    Decorators, defaults, annotations and a class's bases are evaluated where
    the scope is written; so is a comprehension's first iterable.  The rest
    is the scope's own.
    """
    if isinstance(node, COMPREHENSIONS):
        inner: list[ast.AST] = [
            value
            for name, value in ast.iter_fields(node)
            if name != "generators" and isinstance(value, ast.AST)
        ]
        for index, generator in enumerate(node.generators):
            inner.extend([generator.target, *generator.ifs])
            if index:
                inner.append(generator.iter)
        return [node.generators[0].iter], inner
    if isinstance(node, ast.Module):
        return [], list(node.body)
    outer: list[ast.AST] = [
        child
        for name, value in ast.iter_fields(node)
        if name != "body"
        for child in (value if isinstance(value, list) else [value])
        if isinstance(child, ast.AST)
    ]
    body = node.body
    return outer, list(body) if isinstance(body, list) else [body]


def _owned(parts: list[ast.AST]) -> tuple[list[ast.AST], list[ScopeNode]]:
    """Every node these parts evaluate in their own scope, and the scopes nested."""
    owned: list[ast.AST] = []
    nested: list[ScopeNode] = []
    stack = list(parts)
    while stack:
        node = stack.pop()
        owned.append(node)
        if isinstance(node, SCOPES):
            nested.append(node)
            stack.extend(_split(node)[0])
        else:
            stack.extend(ast.iter_child_nodes(node))
    return owned, nested


def _parameters(node: ScopeNode) -> list[ast.arg]:
    if not isinstance(node, FUNCTIONS):
        return []
    arguments = node.args
    return [
        *arguments.posonlyargs,
        *arguments.args,
        *([arguments.vararg] if arguments.vararg else []),
        *arguments.kwonlyargs,
        *([arguments.kwarg] if arguments.kwarg else []),
    ]


def _defaults(node: ScopeNode) -> list[tuple[ast.arg, ast.expr]]:
    """Each parameter with a default, and that default."""
    if not isinstance(node, FUNCTIONS):
        return []
    arguments = node.args
    positional = [*arguments.posonlyargs, *arguments.args]
    paired = list(
        zip(
            positional[len(positional) - len(arguments.defaults) :],
            arguments.defaults,
            strict=True,
        )
    )
    paired.extend(
        (parameter, default)
        for parameter, default in zip(
            arguments.kwonlyargs, arguments.kw_defaults, strict=True
        )
        if default is not None
    )
    return paired


def _format_fields(text: str) -> Iterator[str]:
    """Each replacement field's name in *text* read as a format string.

    A field nested in another's format spec is read too.  The walk ends:
    each spec is a part of the text it was read from, so every one is
    shorter than the last.
    """
    pending = [text]
    while pending:
        try:
            parsed = list(Formatter().parse(pending.pop()))
        except ValueError:
            parsed = []
        for _, field, spec, _ in parsed:
            if field is not None:
                yield field
            if spec:
                pending.append(spec)


def _spells_the_field(text: object) -> bool:
    """Whether a string constant names the graded sha, alone or on a dotted path.

    ``attrgetter("evidence.graded_sha")`` reads the field as surely as
    ``attrgetter("graded_sha")`` does, so a dotted name any of whose segments
    is a spelling is a read.  Prose is not a dotted name: a docstring or a log
    message that mentions the field has a segment that is not an identifier,
    and a longer identifier such as ``graded_sha_note`` is not the field.

    A format field is a path too: ``"{0.evidence.graded_sha}".format(x)``
    reads the field through ``getattr``, and ``"{gradedSha}".format(**row)``
    by its alias, so a field name whose dotted or bracketed segments include
    a spelling is a read.

    The other two formatting mini-languages take a mapping's value by the
    same key: ``"%(gradedSha)s" % row`` through its printf mapping key, and
    ``Template("$gradedSha").substitute(row)`` through its placeholder,
    braced or bare.  A key or a placeholder equal to a spelling is a read.
    """
    if not isinstance(text, str):
        return False
    segments = text.split(".")
    return (
        (
            all(segment.isidentifier() for segment in segments)
            and not SPELLINGS.isdisjoint(segments)
        )
        or any(
            not SPELLINGS.isdisjoint(re.split(r"[.\[\]]", field))
            for field in _format_fields(text)
        )
        or not SPELLINGS.isdisjoint(re.findall(r"%\((\w+)\)", text))
        or not SPELLINGS.isdisjoint(Template(text).get_identifiers())
    )


def _package(module: str) -> str:
    """The package a module of the scanned tree resolves a relative import in."""
    return ".".join([SOURCE.name, *Path(module).with_suffix("").parts][:-1])


def _imports(tree: ast.Module, package: str) -> list[tuple[str, str]]:
    """Each name the module binds by an import, with the dotted path it names."""
    bound: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                head = alias.name.partition(".")[0]
                bound.append(
                    (alias.asname, alias.name) if alias.asname else (head, head)
                )
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = resolve_name("." * node.level + base, package)
            bound.extend(
                (alias.asname or alias.name, f"{base}.{alias.name}")
                for alias in node.names
                if alias.name != "*"
            )
    return bound


@cache
def _resolved(path: str) -> object:
    """The object a dotted path names, or None when nothing is there.

    The longest prefix that imports is the module; the rest are attributes.
    """
    parts = path.split(".")
    for end in range(len(parts), 0, -1):
        try:
            found: object = import_module(".".join(parts[:end]))
        except ImportError:
            continue
        for part in parts[end:]:
            found = getattr(found, part, None)
        return found
    return None


def _dotted(node: ast.AST, imports: Mapping[str, str]) -> str | None:
    """The dotted path a name or an attribute chain names through the imports."""
    if isinstance(node, ast.Name):
        return imports.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, imports)
        return None if base is None else f"{base}.{node.attr}"
    return None


def _module_name(module: str) -> str:
    """The dotted name a module of the scanned tree is imported under."""
    parts = [SOURCE.name, *Path(module).with_suffix("").parts]
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


class _Lines(SourceLoader):
    """Module-level lines held in memory, run by the import system."""

    def __init__(self, module: str, text: str) -> None:
        self._module = module
        self._text = text

    def get_filename(self, fullname: str) -> str:
        return self._module

    def get_data(self, path: str) -> bytes:
        return self._text.encode()


def _namespace(module: str, text: str) -> Mapping[str, object]:
    """The module's own namespace after import, as the running program has it.

    The file on disk is imported and its namespace read.  Text that is not
    the file on disk -- a plant, or a new module -- is that namespace with
    the text's own module-level statements the file does not have run over
    a copy of it, in order, by the import system.  A definition or a class
    the file already has is the file's, whatever is planted in its body.  A
    statement that raises keeps what it bound before it raised, as an import
    that fails part-way does.  Nothing is called that the module's own
    import would not call.
    """
    name = _module_name(module)
    shipped = SHIPPED.get(module)
    imported: Mapping[str, object] = {}
    kept: Counter[str] = Counter()
    defined: frozenset[str] = frozenset()
    unrun: list[Exception] = []
    if shipped is not None:
        try:
            imported = vars(import_module(name))
        except Exception as error:
            unrun.append(error)
        if text == shipped:
            return imported
        body = ast.parse(shipped).body
        kept = Counter(ast.dump(statement) for statement in body)
        defined = frozenset(
            statement.name for statement in body if isinstance(statement, NAMED)
        )
    target = ModuleType(name)
    vars(target).update(imported)
    target.__package__ = _package(module)
    for statement in ast.parse(text).body:
        written = ast.dump(statement)
        if kept[written]:
            kept[written] -= 1
        elif not (isinstance(statement, NAMED) and statement.name in defined):
            try:
                _Lines(module, ast.unparse(statement)).exec_module(target)
            except Exception as error:
                unrun.append(error)
    return vars(target)


def _looked_up(node: ast.expr, namespace: Mapping[str, object]) -> object:
    """The object a name or an attribute chain names in *namespace*.

    A name the module does not bind is looked up among ``typing``'s names,
    which an annotation may spell without importing them.  Nothing found is
    None.
    """
    if isinstance(node, ast.Name):
        return namespace.get(node.id, vars(typing).get(node.id))
    if isinstance(node, ast.Attribute):
        return getattr(_looked_up(node.value, namespace), node.attr, None)
    return None


def _parsed(text: str) -> list[ast.AST]:
    """A string annotation, parsed as the expression it spells, if it is one."""
    try:
        return [ast.parse(text, mode="eval").body]
    except SyntaxError:
        return []


def _alias_statements(body: Sequence[ast.stmt]) -> dict[str, ast.AST]:
    """Each type a body writes under another name, by that name.

    A ``type`` statement, a plain assignment to a name and a ``TypeAlias``
    annotation: the three ways a module body, a ``TYPE_CHECKING`` block or
    a class body aliases a type.
    """
    found: dict[str, ast.AST] = {}
    for statement in body:
        if isinstance(statement, ast.TypeAlias) and isinstance(
            statement.name, ast.Name
        ):
            found[statement.name.id] = statement.value
        elif isinstance(statement, ast.Assign):
            found.update(
                (target.id, statement.value)
                for target in statement.targets
                if isinstance(target, ast.Name)
            )
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.value is not None
        ):
            found[statement.target.id] = statement.value
    return found


def _type_checking_aliases(
    tree: ast.Module, imports: Mapping[str, str]
) -> dict[str, ast.AST]:
    """The aliases written under ``if TYPE_CHECKING:``, which never run."""
    found: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and (
            (isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING")
            or _dotted(node.test, imports) == "typing.TYPE_CHECKING"
        ):
            found.update(_alias_statements(node.body))
    return found


def _bounds(node: ScopeNode) -> dict[str, ast.AST]:
    """Each bounded type parameter a ``def`` or ``class`` declares, to its bound."""
    return {
        parameter.name: parameter.bound
        for parameter in getattr(node, "type_params", [])
        if isinstance(parameter, ast.TypeVar) and parameter.bound is not None
    }


NO_ALIASES: Mapping[str, ast.AST] = MappingProxyType({})


def _names_the_record(
    annotation: ast.AST | None,
    imports: Mapping[str, str],
    namespace: Callable[[], Mapping[str, object]],
    aliases: Mapping[str, ast.AST] = NO_ALIASES,
    declared: object = None,
) -> bool:
    """Whether an annotation names the Evidence record, by its name or by object.

    The record's own name is enough.  So is any name or dotted path the
    module's imports resolve to the record itself.  Every name and dotted
    path is also resolved in the module's own namespace after import (see
    ``_namespace``; for a method, the enclosing class namespaces first),
    and what it resolves to is unwrapped: a ``type`` alias to its value, a
    ``NewType`` to its supertype, a ``TypeVar`` to its bound and its
    constraints, ``Annotated`` to what it annotates, ``Optional``, a union
    and any other generic to its arguments, and a string (a string
    annotation, a forward reference) to the expression it spells, resolved
    the same way.  The annotation names the record when anything it
    unwraps to is the record, or a class derived from it: every value of
    a subclass carries the field.

    *declared* is the object the running program holds for the annotation
    (``inspect.get_annotations`` of the real function), unwrapped the same
    way.  *aliases* are the names the running module does not bind, each
    to the expression it stands for: the aliases under ``TYPE_CHECKING``,
    the aliases of an enclosing class body, and the bounded type parameters
    of the enclosing definitions.  A name the namespace does not bind is
    read through them, whatever ``typing`` binds under it; an import made
    only under ``TYPE_CHECKING`` is resolved through the imports alone.

    The walk ends: each node, each string, each alias and each object is
    taken once, and the annotation, the strings it spells and the objects
    the namespace holds are finite.
    """
    if annotation is None:
        return False
    nodes: list[ast.AST] = [annotation]
    objects: list[object] = [] if declared is None else [declared]
    spelled: set[str] = set()
    expanded: set[str] = set()
    met: list[object] = []
    while nodes or objects:
        if nodes:
            node = nodes.pop()
            if (
                (isinstance(node, ast.Name) and node.id == RECORD)
                or (isinstance(node, ast.Attribute) and node.attr == RECORD)
                or (
                    (path := _dotted(node, imports)) is not None
                    and _resolved(path) is CriterionEvidence
                )
            ):
                return True
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                objects.append(node.value)
            elif isinstance(node, (ast.Name, ast.Attribute)):
                found = _looked_up(node, namespace())
                if (
                    isinstance(node, ast.Name)
                    and node.id in aliases
                    and node.id not in namespace()
                    and node.id not in expanded
                ):
                    expanded.add(node.id)
                    nodes.append(aliases[node.id])
                objects.append(found)
            nodes.extend(ast.iter_child_nodes(node))
            continue
        item = objects.pop()
        if any(item is seen for seen in met):
            continue
        met.append(item)
        if isinstance(item, type):
            try:
                if issubclass(item, CriterionEvidence):
                    return True
            except TypeError:
                pass
        elif isinstance(item, str):
            if item not in spelled:
                spelled.add(item)
                nodes.extend(_parsed(item))
        elif isinstance(item, ForwardRef):
            objects.append(item.__forward_arg__)
        elif isinstance(item, TypeAliasType):
            try:
                objects.append(item.__value__)
            except Exception:
                objects.append(None)
        elif isinstance(item, NewType):
            objects.append(item.__supertype__)
        elif isinstance(item, TypeVar):
            try:
                objects.extend([item.__bound__, *item.__constraints__])
            except Exception:
                objects.append(None)
        elif isinstance(item, (list, tuple)):
            objects.extend(item)
        elif get_origin(item) is Annotated:
            objects.append(get_args(item)[0])
        else:
            objects.extend([get_origin(item), *get_args(item)])
    return False


def _annotated(
    owned: list[ast.AST],
    imports: Mapping[str, str],
    namespace: Callable[[], Mapping[str, object]],
    aliases: Mapping[str, ast.AST],
) -> frozenset[str]:
    """The names this scope annotates as the Evidence record."""
    return frozenset(
        node.target.id
        for node in owned
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and _names_the_record(node.annotation, imports, namespace, aliases)
    )


def _bound(target: ast.AST) -> frozenset[str]:
    """The names a binding target binds.

    A name; each element of an unpacking; and the container a subscript
    stores into, which holds the value from then on.  An attribute store is
    a value parked on an object, which is outside the reach.
    """
    if isinstance(target, ast.Name):
        return frozenset({target.id})
    if isinstance(target, ast.Starred):
        return _bound(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        return frozenset().union(*(_bound(element) for element in target.elts))
    if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
        return frozenset({target.value.id})
    return frozenset()


def _captures(pattern: ast.AST) -> frozenset[str]:
    """The names a ``match`` pattern captures."""
    found: set[str] = set()
    for node in ast.walk(pattern):
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            found.add(node.name)
        if isinstance(node, ast.MatchMapping) and node.rest:
            found.add(node.rest)
    return frozenset(found)


def _bindings(
    scope: ScopeNode, owned: list[ast.AST]
) -> Iterator[tuple[list[ast.expr], ast.expr]]:
    """Every binding this scope makes, as its targets and the value bound.

    A subscript store binds the container it stores into, whether the value
    or the key carries the graded sha.
    """
    for node in owned:
        if isinstance(node, ast.Assign):
            yield list(node.targets), node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            if node.value is not None:
                yield [node.target], node.value
        elif isinstance(node, ast.NamedExpr):
            yield [node.target], node.value
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            yield [node.target], node.iter
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None:
                yield [node.optional_vars], node.context_expr
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            yield [node.value], node.slice
    if isinstance(scope, COMPREHENSIONS):
        for generator in scope.generators:
            yield [generator.target], generator.iter


def _render(clause: ast.AST) -> str:
    """A clause as written, with any body it has elided."""
    shallow = copy.copy(clause)
    for name in ("body", "orelse", "finalbody"):
        if isinstance(getattr(shallow, name, None), list) and getattr(shallow, name):
            setattr(shallow, name, [ast.Expr(ast.Constant(...))])
    for name in ("handlers", "cases"):
        if isinstance(getattr(shallow, name, None), list):
            setattr(shallow, name, [])
    return ast.unparse(shallow)


class _Module:
    """One module, read for the scopes that read the graded sha directly."""

    def __init__(self, text: str, module: str) -> None:
        tree = ast.parse(text)
        self.imports = dict(_imports(tree, _package(module)))
        self._text = text
        self._module = module
        self._namespace: Mapping[str, object] | None = None
        self.parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        self.readers: dict[str, Counter[str]] = {}
        self._scope(
            tree,
            MODULE,
            frozenset(),
            frozenset(),
            frozenset(),
            _type_checking_aliases(tree, self.imports),
        )

    def namespace(self) -> Mapping[str, object]:
        """The module's namespace after import, read once and only if asked."""
        if self._namespace is None:
            self._namespace = _namespace(self._module, self._text)
        return self._namespace

    def _bound_at(self, path: Sequence[str]) -> list[object]:
        """What the running module binds along a dotted label, outermost first.

        The first name is read in the module's namespace and each next one
        off the object before it; the walk stops at the first name that is
        bound to nothing, which is where a class or a function nested in a
        function begins.
        """
        found: list[object] = []
        owner: object = None
        for depth, name in enumerate(path):
            owner = (
                self.namespace().get(name) if depth == 0 else getattr(owner, name, None)
            )
            if owner is None:
                break
            found.append(owner)
        return found

    def _within(self, owners: Sequence[str]) -> Callable[[], Mapping[str, object]]:
        """The namespace an annotation is evaluated in, inside *owners*.

        A class body's names are visible to the annotations written in it
        and in its methods' signatures, so each enclosing class the running
        module binds is read, innermost first, before the module's names.
        """

        def within() -> Mapping[str, object]:
            classes = [
                vars(held) for held in self._bound_at(owners) if isinstance(held, type)
            ]
            return ChainMap(*reversed(classes), self.namespace())

        return within

    def _declared(self, path: Sequence[str]) -> Mapping[str, object]:
        """The annotations the running module holds for the function at *path*.

        Read off the function object as the running program has it, strings
        evaluated: a class-body alias is already the object it named when
        the method was defined, and a type parameter is the TypeVar it
        declares.  A function the module does not bind after import, or an
        annotation that will not evaluate, gives nothing here.
        """
        bound = self._bound_at(path)
        if len(bound) != len(path):
            return {}
        try:
            return dict(get_annotations(bound[-1], eval_str=True))
        except Exception:
            return {}

    def _reads(self, node: ast.AST, records: frozenset[str]) -> bool:
        """Whether *node* reads the graded sha directly."""
        if isinstance(node, ast.Attribute):
            return isinstance(node.ctx, ast.Load) and node.attr in SPELLINGS
        if isinstance(node, ast.Constant):
            return _spells_the_field(node.value)
        if isinstance(node, ast.MatchClass):
            return any(attribute in SPELLINGS for attribute in node.kwd_attrs)
        if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
            return False
        if node.id in SPELLINGS:
            return True
        above = self.parents.get(node)
        return node.id in records and not (
            isinstance(above, ast.Attribute) and above.attr in OTHER_FIELDS
        )

    def _uses(
        self, node: ast.AST, carriers: frozenset[str], records: frozenset[str]
    ) -> bool:
        """Whether *node* is the graded sha, read here or through a local."""
        return self._reads(node, records) or (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in carriers
        )

    def _carries(
        self, value: ast.AST, carriers: frozenset[str], records: frozenset[str]
    ) -> bool:
        return any(self._uses(node, carriers, records) for node in ast.walk(value))

    def _captured(
        self, case: ast.match_case, carriers: frozenset[str], records: frozenset[str]
    ) -> frozenset[str]:
        """What a ``match`` case captures from the graded sha."""
        match = self.parents[case]
        assert isinstance(match, ast.Match)
        if self._carries(match.subject, carriers, records):
            return _captures(case.pattern)
        found: set[str] = set()
        for node in ast.walk(case.pattern):
            if isinstance(node, ast.MatchClass):
                for attribute, pattern in zip(
                    node.kwd_attrs, node.kwd_patterns, strict=True
                ):
                    if attribute in SPELLINGS:
                        found |= _captures(pattern)
            if isinstance(node, ast.MatchMapping):
                for key, pattern in zip(node.keys, node.patterns, strict=True):
                    if self._carries(key, carriers, records):
                        found |= _captures(pattern)
        return frozenset(found)

    def _settle(
        self,
        scope: ScopeNode,
        owned: list[ast.AST],
        carriers: frozenset[str],
        records: frozenset[str],
    ) -> frozenset[str]:
        """The names carrying the graded sha in *scope*, to a fixed point.

        One pass would miss a name bound before the name it is bound from.
        Each pass adds a name or stops, so it ends within the scope's names.
        """
        cases = [node for node in owned if isinstance(node, ast.match_case)]
        while True:
            grown = set(carriers)
            for targets, value in _bindings(scope, owned):
                if self._carries(value, carriers, records):
                    for target in targets:
                        grown |= _bound(target)
            for case in cases:
                grown |= self._captured(case, carriers, records)
            if grown == carriers:
                return carriers
            carriers = frozenset(grown)

    def _clause(self, node: ast.AST) -> ast.AST:
        while not isinstance(node, CLAUSES):
            node = self.parents[node]
        return node

    def _scope(
        self,
        node: ScopeNode,
        label: str,
        closure: frozenset[str],
        typed: frozenset[str],
        defaulted: frozenset[str],
        aliases: Mapping[str, ast.AST],
    ) -> None:
        owned, nested = _owned(_split(node)[1])
        parameters = _parameters(node)
        shadowed = frozenset(parameter.arg for parameter in parameters)
        path = () if label == MODULE else tuple(label.split("."))
        aliases = {**aliases, **_bounds(node)}
        if isinstance(node, ast.ClassDef):
            aliases = {**aliases, **_alias_statements(node.body)}
        namespace = self._within(path if isinstance(node, ast.ClassDef) else path[:-1])
        declared: Mapping[str, object] = {}
        if isinstance(node, DEFINITIONS) and any(
            parameter.annotation is not None for parameter in parameters
        ):
            declared = self._declared(path)
        records = (
            (typed - shadowed)
            | _annotated(owned, self.imports, namespace, aliases)
            | frozenset(
                parameter.arg
                for parameter in parameters
                if _names_the_record(
                    parameter.annotation,
                    self.imports,
                    namespace,
                    aliases,
                    declared.get(parameter.arg),
                )
            )
        )
        carriers = self._settle(node, owned, (closure - shadowed) | defaulted, records)
        clauses = {
            id(clause): clause
            for clause in (
                self._clause(item)
                for item in owned
                if self._uses(item, carriers, records)
            )
        }
        if clauses:
            uses = self.readers.setdefault(label, Counter())
            uses.update(_render(clause) for clause in clauses.values())
        for child in nested:
            name = child.name if isinstance(child, NAMED) else ANONYMOUS[type(child)]
            inherited, inherited_records = frozenset[str](), frozenset[str]()
            if isinstance(node, CLOSING) or isinstance(child, COMPREHENSIONS):
                inherited, inherited_records = carriers, records
            elif isinstance(node, ast.ClassDef):
                inherited, inherited_records = closure, typed
            self._scope(
                child,
                name if label == MODULE else f"{label}.{name}",
                inherited,
                inherited_records,
                frozenset(
                    parameter.arg
                    for parameter, default in _defaults(child)
                    if self._carries(default, carriers, records)
                ),
                aliases,
            )


def readers(sources: dict[str, str]) -> dict[str, Counter[str]]:
    """Every scope of *sources* reading the graded sha directly, with its uses.

    Keyed ``module::qualified.name``; the module body is ``<module>`` and an
    anonymous scope carries Python's own name for it (``<lambda>``,
    ``<genexpr>`` and so on), so two of them in one scope share a row.
    """
    found: dict[str, Counter[str]] = {}
    for module, text in sources.items():
        for label, uses in _Module(text, module).readers.items():
            found[f"{module}::{label}"] = uses
    return found


def _sources() -> dict[str, str]:
    return {
        path.relative_to(SOURCE).as_posix(): path.read_text()
        for path in sorted(SOURCE.rglob("*.py"))
    }


SHIPPED = _sources()
SHIPPED_READERS = readers(SHIPPED)


class Row(NamedTuple):
    """A registered reader: why it reads the graded sha, and where."""

    reason: str
    uses: tuple[str, ...]


#: Every scope that reads the graded sha directly and is not the rule, each
#: with the reason it is not the rule's business and every statement in it
#: that touches the value.
#:
#: A recorded revision read against ITSELF, or against a second recorded
#: revision, is provenance: it answers whether a reading is about the commit
#: it says it is, which is a different question from whether that reading
#: still stands.  A revision handed to a port as a ref, quoted into a
#: session's prompt, recorded onto a row or carried into the rule's own
#: arguments weighs nothing at all.  A row is not a licence for the body:
#: one more use, or a changed one, is red.
REGISTER_FILE = Path(__file__).with_name("graded_sha_readers.json")
REGISTERED: dict[str, Row] = {
    site: Row(reason=row["reason"], uses=tuple(row["uses"]))
    for site, row in json.loads(REGISTER_FILE.read_text()).items()
}


def findings(found: dict[str, Counter[str]], register: dict[str, Row]) -> list[str]:
    """Every way *found* differs from the register, in words.

    A reader nobody registered, a registered one using the value anywhere
    its row does not pin, and a row for a scope of these modules that no
    longer reads it.
    """
    messages: list[str] = []
    for site, uses in sorted(found.items()):
        if site == RULE_SITE:
            continue
        row = register.get(site)
        if row is None:
            messages.append(f"{site} reads the graded sha and is not registered")
            continue
        pinned = Counter(row.uses)
        if uses != pinned:
            messages.append(
                f"{site} uses it at {sorted((uses - pinned).elements())} "
                f"beyond its row, and not at {sorted((pinned - uses).elements())}"
            )
    modules = {site.partition("::")[0] for site in found}
    messages.extend(
        f"{site} is registered and no longer reads the graded sha"
        for site in sorted(register)
        if site.partition("::")[0] in modules and site not in found
    )
    return messages


def _scope_node(tree: ast.Module, label: str) -> ast.AST | None:
    """The named scope a dotted path names, or None for an anonymous one."""
    node: ast.AST = tree
    for name in label.split(".") if label != MODULE else []:
        stack: list[ast.AST] = list(ast.iter_child_nodes(node))
        while stack:
            current = stack.pop(0)
            if isinstance(current, NAMED) and current.name == name:
                node = current
                break
            if not isinstance(current, SCOPES):
                stack.extend(ast.iter_child_nodes(current))
        else:
            return None
    return node


def planted(site: str, block: str, module_lines: str = "") -> dict[str, Counter[str]]:
    """The site's module read again with *block* written into the site.

    The block goes in as the first statement of the scope's body, indented
    to it; at module scope it goes at the end.  Where it goes inside the
    scope does not matter: bindings are settled over the whole scope.
    *module_lines* go at the end of the module, after the block.
    """
    module, _, label = site.partition("::")
    source = SHIPPED[module]
    node = _scope_node(ast.parse(source), label)
    assert isinstance(node, (ast.Module, *NAMED)), site
    lines = source.splitlines(keepends=True)
    if isinstance(node, ast.Module):
        at, indent = len(lines), ""
    else:
        first = node.body[0]
        assert first.lineno > node.lineno, site
        at, indent = first.lineno - 1, " " * first.col_offset
    written = [f"{indent}{line}" for line in block.splitlines(keepends=True)]
    text = "".join([*lines[:at], *written, *lines[at:]])
    return readers({module: text + module_lines})


def alone(source: str) -> dict[str, Counter[str]]:
    """One new module, read on its own."""
    return readers({"reader.py": source})


def _indented(block: str) -> str:
    return "".join(f"    {line}" for line in block.splitlines(keepends=True))


def _named_scope(site: str) -> str:
    """The named scope a site is written in: itself, or the nearest one around it.

    A lambda or a comprehension holds no statement, and no named scope can be
    written inside one, so its anonymous segments are the trailing ones.
    """
    module, _, label = site.partition("::")
    names = label.split(".")
    while names and names[-1] in ANONYMOUS.values():
        names.pop()
    return f"{module}::{'.'.join(names) or MODULE}"


#: Where the plants below are written: every registered reader, and for an
#: anonymous one the named scope it is written in.
PLANT_SITES = tuple(sorted({_named_scope(site) for site in REGISTERED}))

#: Each spelling that walked past earlier rounds of this guard, and a few
#: more a reader would call ordinary Python, written as statements so each
#: can be planted as a module of its own and inside any scope.  The names
#: are the ones a reader would use; none of them is bound anywhere, and the
#: heads are called whatever a reader would call them.
PLANTS = {
    "operator-module": (
        "import operator\n_planted = operator.ne(evidence.graded_sha, head_sha)\n"
    ),
    "comparison-dunder": "_planted = evidence.graded_sha.__eq__(head_sha)\n",
    "local-then-compared": (
        "_recorded = str(evidence.graded_sha)\n_planted = _recorded != head_sha\n"
    ),
    "comprehension-bound-operand": (
        "_planted = any(\n"
        "    sha != head_sha for sha in {row.evidence.graded_sha for row in prior}\n"
        ")\n"
    ),
    "serialised-record-get": (
        "_planted = evidence.model_dump().get('graded_sha') != head_sha\n"
    ),
    "match-class-pattern": (
        "match evidence:\n"
        "    case CriterionEvidence(graded_sha=_recorded):\n"
        "        _planted = _recorded != head_sha\n"
    ),
    "match-mapping-pattern": (
        "match row:\n"
        "    case {'graded_sha': _recorded}:\n"
        "        _planted = _recorded != head_sha\n"
    ),
    "match-value-pattern": (
        "match head_sha:\n    case evidence.graded_sha:\n        _planted = True\n"
    ),
    "match-class-keyword-against-a-head": (
        "match evidence:\n"
        "    case CriterionEvidence(graded_sha=lane.head_sha):\n"
        "        _planted = True\n"
    ),
    "attrgetter": (
        "from operator import attrgetter\n"
        "_planted = attrgetter('graded_sha')(evidence) != head_sha\n"
    ),
    "a-dotted-attrgetter-path": (
        "from operator import attrgetter\n"
        "_planted = attrgetter('evidence.graded_sha')(cross_off) != head_sha\n"
    ),
    "iterating-the-models-fields": (
        "for _key, _value in evidence:\n"
        "    if _key == 'graded_sha':\n"
        "        _planted = _value != head_sha\n"
    ),
    "iterating-a-record-imported-under-another-name": (
        f"from {CriterionEvidence.__module__} import {RECORD} as Evidence\n"
        "_record: Evidence = evidence\n"
        "_planted = any(_value == head_sha for _, _value in _record)\n"
    ),
    "iterating-a-typed-record": (
        "_record: CriterionEvidence = evidence\n"
        "_planted = any(_value == head_sha for _, _value in _record)\n"
    ),
    "lambda": "_lapsed = lambda head_sha: evidence.graded_sha != head_sha\n",
    "class-level-staticmethod-lambda": (
        "class _Rule:\n"
        "    lapsed = staticmethod(lambda head_sha: evidence.graded_sha != head_sha)\n"
    ),
    "comprehension-scope": (
        "_planted = [evidence.graded_sha != head for head in (head_sha,)]\n"
    ),
    "bare-name-ancestry": "_planted = is_ancestor(evidence.graded_sha, head_sha)\n",
    "prefix-match": "_planted = head_sha.startswith(evidence.graded_sha)\n",
    "helper-under-other-names": (
        "def _differs(recorded, current):\n"
        "    return recorded != current\n"
        "_planted = _differs(evidence.graded_sha, head_sha)\n"
    ),
    "value-put-in-a-container": (
        "_seen = set()\n_seen.add(evidence.graded_sha)\n_planted = head_sha in _seen\n"
    ),
    "walrus": "_planted = (_recorded := evidence.graded_sha) != head_sha\n",
    "serialised-alias-key": "_planted = row['gradedSha'] != head_sha\n",
    "a-lane-records-pushed-head": (
        "_planted = lane.pushed_head_sha == evidence.graded_sha\n"
    ),
    "a-head-read-from-the-port": (
        "_head = git.current_sha(cwd)\n_planted = _head != evidence.graded_sha\n"
    ),
    "a-head-called-head": "_planted = evidence.graded_sha != head\n",
    "a-head-called-tip": "_planted = evidence.graded_sha != tip\n",
    "a-head-called-current-sha": "_planted = evidence.graded_sha != current_sha\n",
    "a-head-called-head-ref": "_planted = evidence.graded_sha != head_ref\n",
    "a-head-in-a-branch-head-field": (
        "_planted = evidence.graded_sha != lane.branch_head\n"
    ),
    "a-head-from-resolve-commit": (
        "_tip = git.resolve_commit(cwd=cwd, ref=branch)\n"
        "_planted = evidence.graded_sha != _tip\n"
    ),
    "a-digest-from-the-graded-sha": (
        "_planted = git.diff_summary(\n"
        "    cwd=cwd, base_ref=evidence.graded_sha, head_ref=branch\n"
        ")\n"
    ),
    "a-default-argument-lambda": (
        "_lapsed = lambda recorded=evidence.graded_sha: recorded != head_sha\n"
    ),
    "one-operand-holding-both": (
        "from operator import itemgetter\n"
        "_blobs = ((evidence.graded_sha, 1), (head_sha, 2))\n"
        "_planted = len(set(map(itemgetter(0), _blobs))) == 1\n"
    ),
    "a-rebinding-before-a-compare": (
        "latest_head = evidence.graded_sha\n_planted = latest_head != head_sha\n"
    ),
    "a-format-field-path": (
        "_planted = '{0.evidence.graded_sha}'.format(cross_off) != head_sha\n"
    ),
    "a-format-key-by-alias": "_planted = '{gradedSha}'.format(**row) != head_sha\n",
    "a-percent-key-by-alias": "_planted = '%(gradedSha)s' % row != head_sha\n",
    "a-template-field-by-alias": (
        "from string import Template\n"
        "_planted = Template('$gradedSha').substitute(row) != head_sha\n"
    ),
    "an-f-string-of-the-field": "_planted = f'{evidence.graded_sha}' != head_sha\n",
}


def test_the_graded_identity_the_guard_scans_for_is_the_evidence_records_own_field():
    """The walk keys on the record's field and its alias, not on a spelling here."""
    assert len(GRADED_IDENTITIES) == 1
    assert GRADED in CriterionEvidence.model_fields
    assert GRADED in signature(graded_state).parameters
    alias = CriterionEvidence.model_fields[GRADED].alias
    assert alias
    assert alias != GRADED
    assert frozenset({GRADED, alias}) == SPELLINGS
    assert RECORD
    assert OTHER_FIELDS
    assert GRADED not in OTHER_FIELDS


def test_the_rule_the_guard_permits_is_the_one_the_sources_import():
    """The permitted site is derived, so a rename carries the guard with it."""
    assert (SOURCE / RULE_MODULE).is_file()
    assert RULE_MODULE.startswith("domain/")
    assert RULE in (SOURCE / RULE_MODULE).read_text()


def test_the_rule_is_reported_as_a_direct_reader():
    """The walk sees the rule itself, so it is not blind where it matters most."""
    assert SHIPPED_READERS[RULE_SITE]


def test_the_sources_compare_a_graded_sha_with_a_head_sha_in_one_body_only():
    """Every direct reader is the rule or registered, using it only as pinned."""
    assert findings(SHIPPED_READERS, REGISTERED) == []


def test_every_exemption_names_a_site_the_walk_actually_reports():
    """A stale row reds: the register cannot outlive the reading it explains."""
    assert REGISTERED
    assert frozenset(REGISTERED) <= frozenset(SHIPPED_READERS), sorted(
        frozenset(REGISTERED) - frozenset(SHIPPED_READERS)
    )


def test_a_row_whose_scope_stopped_reading_is_reported():
    """The same staleness, read through the findings a plant is judged by."""
    stale = "domain/lapse.py::no_longer_reads"
    register = {**REGISTERED, stale: Row(reason="gone", uses=("x",))}
    assert findings(readers({RULE_MODULE: SHIPPED[RULE_MODULE]}), register) == [
        f"{stale} is registered and no longer reads the graded sha"
    ]


def test_every_exemption_carries_the_reason_it_is_one():
    assert all(row.reason.strip() for row in REGISTERED.values())
    assert all(row.uses for row in REGISTERED.values())
    assert RULE_SITE not in REGISTERED


def test_removing_any_row_or_any_pinned_use_is_reported():
    """Each row, and each statement each row pins, is load-bearing."""
    assert REGISTERED
    for site, row in REGISTERED.items():
        without_row = {key: value for key, value in REGISTERED.items() if key != site}
        assert findings(SHIPPED_READERS, without_row), site
        for index in range(len(row.uses)):
            fewer = row.uses[:index] + row.uses[index + 1 :]
            shrunk = {**REGISTERED, site: row._replace(uses=fewer)}
            assert findings(SHIPPED_READERS, shrunk), (site, row.uses[index])


def test_every_registered_reader_is_planted_into_or_through_its_named_scope():
    """Every plant site is a scope a statement can be written into."""
    assert PLANT_SITES
    assert frozenset(REGISTERED) - frozenset(PLANT_SITES) <= {
        site for site in REGISTERED if _named_scope(site) != site
    }
    for site in PLANT_SITES:
        module, _, label = site.partition("::")
        assert _scope_node(ast.parse(SHIPPED[module]), label) is not None, site


@pytest.mark.parametrize("spelling", list(PLANTS))
def test_every_spelling_that_compares_the_graded_sha_is_reported(spelling):
    """Each spelling, planted as a module of its own, reds -- and in a body."""
    block = PLANTS[spelling]
    assert findings(alone(block), REGISTERED)
    body = "def lapsed(evidence, head_sha, prior, row, lane, git, cwd):\n"
    assert findings(alone(body + _indented(block)), REGISTERED)


@pytest.mark.parametrize("spelling", list(PLANTS))
def test_a_second_module_performing_the_comparison_is_reported(spelling):
    """Beside the rule's own module, a second module using the value reds.

    The rule's module is read as shipped and is clean; the reader's own
    scopes are the only findings.
    """
    sources = {RULE_MODULE: SHIPPED[RULE_MODULE]}
    assert findings(readers(sources), REGISTERED) == []
    sources["reader.py"] = "def lapsed(evidence, head_sha, prior, row, lane, git):\n"
    sources["reader.py"] += _indented(PLANTS[spelling])
    found = findings(readers(sources), REGISTERED)
    assert found
    assert all(
        finding.startswith("reader.py::lapsed")
        and finding.endswith("reads the graded sha and is not registered")
        for finding in found
    ), found


@pytest.mark.parametrize("spelling", list(PLANTS))
def test_every_spelling_planted_inside_a_registered_scope_is_reported(spelling):
    """A row pins uses, not a body: a use added inside any registered reader reds."""
    missed = [
        site
        for site in PLANT_SITES
        if not findings(planted(site, PLANTS[spelling]), REGISTERED)
    ]
    assert missed == []


def test_a_default_argument_lambda_in_the_lane_reader_is_reported():
    site = "domain/lapse.py::held_standing"
    found = findings(
        planted(
            site,
            "_lapsed = lambda recorded=cross_off.evidence.graded_sha: "
            "recorded != head_sha\n",
        ),
        REGISTERED,
    )
    assert any(finding.startswith(f"{site} uses it") for finding in found), found
    assert f"{site}.<lambda> reads the graded sha and is not registered" in found


#: A read of the field through the dotted path ``attrgetter`` takes, written
#: inside the lane reader's loop.
DOTTED_READ = (
    "if attrgetter('evidence.graded_sha')(cross_off) != head_sha:\n"
    "    rederive.append(cross_off)\n"
    "    continue\n"
)


def test_a_dotted_path_to_the_field_is_a_read_alone_and_in_the_lane_reader():
    """``attrgetter("evidence.graded_sha")`` reads the field, wherever it is written."""
    source = (
        "from operator import attrgetter\n"
        "def lapsed(cross_off, head_sha):\n"
        "    return attrgetter('evidence.graded_sha')(cross_off) != head_sha\n"
    )
    assert findings(alone(source), REGISTERED) == [
        "reader.py::lapsed reads the graded sha and is not registered"
    ]
    site = "domain/lapse.py::held_standing"
    module = site.partition("::")[0]
    anchor = "        state = graded_state(\n"
    assert SHIPPED[module].count(anchor) == 1
    written = SHIPPED[module].replace(
        anchor, _indented(_indented(DOTTED_READ)) + anchor
    )
    found = findings(readers({module: written}), REGISTERED)
    use = "if attrgetter('evidence.graded_sha')(cross_off) != head_sha:\n    ..."
    assert found == [f"{site} uses it at {[use]} beyond its row, and not at []"]


def test_a_string_that_only_mentions_the_field_is_not_a_read():
    """Prose and a longer name stay out; only a dotted name reaching the field is in.

    A docstring, a log message, a comment, a keyword naming the field to
    write it, a name that merely starts with it, and a dotted path to another
    field are not reads of the graded sha.
    """
    sources = (
        "def noted(evidence, head_sha):\n"
        '    """Reads evidence.graded_sha against the head."""\n'
        "    return evidence.recorded_sha != head_sha\n",
        "def logged(log, head_sha):\n"
        "    log.info('evidence.graded_sha moved', head=head_sha)\n",
        "def commented(head_sha):\n"
        "    # attrgetter('evidence.graded_sha')\n"
        "    return head_sha\n",
        "def stamp(head_sha):\n    return Report(graded_sha=head_sha)\n",
        "def note(row, head_sha):\n    return row['graded_sha_note'] != head_sha\n",
        "def other(row, head_sha):\n"
        "    return attrgetter('evidence.recorded_sha')(row) != head_sha\n",
    )
    assert [source for source in sources if alone(source)] == []


def test_a_format_field_naming_the_field_is_a_read_alone_and_in_the_audit_observation():
    """A format string reads the field through the path its field names."""
    for block in (
        "_planted = '{0.evidence.graded_sha}'.format(cross_off) != head_sha\n",
        "_planted = '{gradedSha}'.format(**row) != head_sha\n",
    ):
        source = "def lapsed(cross_off, row, head_sha):\n" + _indented(block)
        assert findings(alone(source), REGISTERED) == [
            "reader.py::lapsed reads the graded sha and is not registered"
        ], block
        site = "chains/audit_evidence.py::AuditEvidenceVerifier._observe"
        assert findings(planted(site, block), REGISTERED) == [
            f"{site} uses it at {[block.strip()]} beyond its row, and not at []"
        ], block


#: The same read of a passed-in row by its alias, written in each of the
#: three formatting mini-languages, and the printf form of the field itself.
MAPPING_READS = (
    "def lapsed(row, head_sha):\n    return '%(gradedSha)s' % row != head_sha\n",
    "def lapsed(evidence, head_sha):\n"
    "    return '%(graded_sha)s' % vars(evidence) != head_sha\n",
    "from string import Template\n"
    "def lapsed(row, head_sha):\n"
    "    return Template('$gradedSha').substitute(row) != head_sha\n",
    "from string import Template\n"
    "def lapsed(row, head_sha):\n"
    "    return Template('${graded_sha}').substitute(row) != head_sha\n",
)


def test_a_percent_key_or_a_template_field_naming_the_alias_is_a_read():
    """A printf key and a Template placeholder each take ``row['gradedSha']``.

    Each is a read alone, and the printf key written into the lane reader
    and the placeholder written into the audit observation are each a use
    beyond the row that pins that scope.
    """
    for source in MAPPING_READS:
        assert findings(alone(source), REGISTERED) == [
            "reader.py::lapsed reads the graded sha and is not registered"
        ], source
    site = "domain/lapse.py::held_standing"
    module = site.partition("::")[0]
    anchor = "        state = graded_state(\n"
    assert SHIPPED[module].count(anchor) == 1
    percent = (
        'if "%(gradedSha)s" % cross_off.evidence.model_dump(by_alias=True) '
        "!= head_sha:\n"
        "    rederive.append(cross_off)\n"
        "    continue\n"
    )
    written = SHIPPED[module].replace(anchor, _indented(_indented(percent)) + anchor)
    use = (
        "if '%(gradedSha)s' % cross_off.evidence.model_dump(by_alias=True) "
        "!= head_sha:\n    ..."
    )
    assert findings(readers({module: written}), REGISTERED) == [
        f"{site} uses it at {[use]} beyond its row, and not at []"
    ]
    site = "chains/audit_evidence.py::AuditEvidenceVerifier._observe"
    block = "_planted = Template('$gradedSha').substitute(row) != head_sha\n"
    assert findings(
        planted(site, block, module_lines="from string import Template\n"), REGISTERED
    ) == [f"{site} uses it at {[block.strip()]} beyond its row, and not at []"]


def test_a_format_string_with_no_field_naming_it_is_not_a_read():
    """A log format with no such field, and an f-string of other values, stay out.

    An f-string that prints the graded sha is a read already, by the
    attribute it prints: none of its constant parts spells the field.  A
    printf key or a Template placeholder naming the head, a longer name, or
    another field, stays out with them.
    """
    sources = (
        "def logged(log, evidence, head_sha):\n"
        "    log.info('{0.recorded_sha} moved to {1}'.format(evidence, head_sha))\n",
        "def noted(row, head_sha):\n"
        "    return '{graded_sha_note}'.format(**row) != head_sha\n",
        "def said(head_sha):\n    return '{} is the head {head!r:>{width}}'\n",
        "def printed(evidence, head_sha):\n"
        "    return f'moved from {evidence.recorded_sha} to {head_sha}'\n",
        "def keyed(row, head_sha):\n    return '%(head)s' % row != head_sha\n",
        "def longer(row, head_sha):\n"
        "    return '%(graded_sha_note)s' % row != head_sha\n",
        "from string import Template\n"
        "def placed(row, head_sha):\n"
        "    return Template('$head').substitute(row) != head_sha\n",
        "from string import Template\n"
        "def other(row, head_sha):\n"
        "    return Template('$recorded_sha').substitute(row) != head_sha\n",
        "def priced(head_sha):\n    return f'$ {head_sha} % 3' + '$'\n",
    )
    assert [source for source in sources if alone(source)] == []
    assert [
        text
        for text in ("%(gradedSha)s", "$graded_sha", "${gradedSha}")
        if not _spells_the_field(text)
    ] == []
    assert [
        text
        for text in ("%(head)s", "$head", "%(graded_sha_note)s", "$", "%")
        if _spells_the_field(text)
    ] == []
    shown = "def shown(evidence):\n    return f'graded at {evidence.graded_sha}'\n"
    assert alone(shown) == {
        "reader.py::shown": Counter({"return f'graded at {evidence.graded_sha}'": 1})
    }
    assert not any(
        _spells_the_field(node.value)
        for node in ast.walk(ast.parse(shown))
        if isinstance(node, ast.Constant)
    )


def test_one_operand_holding_both_revisions_in_the_drift_detector_is_reported():
    """``blobs`` is keyed by the graded commit in the same scope, so it carries it."""
    site = "services/assertion_drift.py::AssertionDriftDetector.compare"
    block = "_planted = len(set(map(itemgetter(0), blobs))) == 1\n"
    assert findings(planted(site, block), REGISTERED) == [
        f"{site} uses it at {[block.strip()]} beyond its row, and not at []"
    ]


def test_a_rebinding_before_a_compare_in_the_audit_observation_is_reported():
    """The shipped head-against-head compare there becomes a use of the value.

    Bindings are settled over the whole scope, so rebinding the name the
    compare reads to the graded sha turns that compare into a use nobody
    pinned, as well as the rebinding itself.
    """
    site = "chains/audit_evidence.py::AuditEvidenceVerifier._observe"
    block = "latest_head = evidence.graded_sha\n"
    added = sorted([block.strip(), "if latest_head != head:\n    ..."])
    assert findings(planted(site, block), REGISTERED) == [
        f"{site} uses it at {added} beyond its row, and not at []"
    ]


#: One case per binding form the walk follows inside a scope.  In each, the
#: named statement touches the graded sha only through that binding, so
#: undoing the form in the walk drops the statement from the scope's uses.
BINDINGS = {
    "assignment": (
        "def lapsed(evidence, head):\n"
        "    recorded = evidence.graded_sha\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "annotated-assignment": (
        "def lapsed(evidence, head):\n"
        "    recorded: str = evidence.graded_sha\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "augmented-assignment": (
        "def lapsed(evidence, head):\n"
        "    recorded = ''\n"
        "    recorded += evidence.graded_sha\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "unpacking": (
        "def lapsed(evidence, head):\n"
        "    recorded, current = evidence.graded_sha, head\n"
        "    return recorded != current\n",
        "reader.py::lapsed",
        "return recorded != current",
    ),
    "a-container-stored-into": (
        "def lapsed(evidence, head):\n"
        "    seen = {}\n"
        "    seen[evidence.graded_sha] = 1\n"
        "    return head in seen\n",
        "reader.py::lapsed",
        "return head in seen",
    ),
    "for-target": (
        "def lapsed(evidence, head):\n"
        "    for recorded in (evidence.graded_sha,):\n"
        "        pass\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "with-as": (
        "def lapsed(evidence, head):\n"
        "    with held(evidence.graded_sha) as recorded:\n"
        "        pass\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "walrus": (
        "def lapsed(evidence, head):\n"
        "    (recorded := evidence.graded_sha)\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "comprehension-target": (
        "def lapsed(evidence, head):\n"
        "    return [recorded != head for recorded in (evidence.graded_sha,)]\n",
        "reader.py::lapsed.<listcomp>",
        "return [recorded != head for recorded in (evidence.graded_sha,)]",
    ),
    "match-capture": (
        "def lapsed(evidence, head):\n"
        "    match evidence.graded_sha:\n"
        "        case recorded:\n"
        "            pass\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "match-class-keyword": (
        "def lapsed(evidence, head):\n"
        "    match evidence:\n"
        "        case Evidence(graded_sha=recorded):\n"
        "            pass\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "match-mapping-key": (
        "def lapsed(row, head):\n"
        "    match row:\n"
        "        case {'graded_sha': recorded}:\n"
        "            pass\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "default-argument": (
        "def lapsed(evidence, head):\n"
        "    return lambda recorded=evidence.graded_sha: recorded != head\n",
        "reader.py::lapsed.<lambda>",
        "return lambda recorded=evidence.graded_sha: recorded != head",
    ),
    "closure": (
        "def lapsed(evidence):\n"
        "    recorded = evidence.graded_sha\n"
        "    def against(head):\n"
        "        return recorded != head\n"
        "    return against\n",
        "reader.py::lapsed.against",
        "return recorded != head",
    ),
    "closure-through-a-nested-class": (
        "def lapsed(evidence):\n"
        "    recorded = evidence.graded_sha\n"
        "    class A:\n"
        "        def weigh(self, head):\n"
        "            return recorded != head\n"
        "    return A\n",
        "reader.py::lapsed.A.weigh",
        "return recorded != head",
    ),
    "closure-through-a-class-level-lambda": (
        "def lapsed(evidence):\n"
        "    recorded = evidence.graded_sha\n"
        "    class A:\n"
        "        weigh = staticmethod(lambda head: recorded != head)\n"
        "    return A\n",
        "reader.py::lapsed.A.<lambda>",
        "weigh = staticmethod(lambda head: recorded != head)",
    ),
    "closure-through-two-nested-classes": (
        "def lapsed(evidence):\n"
        "    recorded = evidence.graded_sha\n"
        "    class A:\n"
        "        class B:\n"
        "            def weigh(self, head):\n"
        "                return recorded != head\n"
        "    return A\n",
        "reader.py::lapsed.A.B.weigh",
        "return recorded != head",
    ),
    "a-record-annotated-as-the-evidence": (
        "def lapsed(evidence: CriterionEvidence, head):\n"
        "    return head in dict(evidence).values()\n",
        "reader.py::lapsed",
        "return head in dict(evidence).values()",
    ),
    "a-record-imported-under-another-name": (
        f"from {CriterionEvidence.__module__} import {RECORD} as Evidence\n"
        "def lapsed(evidence: Evidence, head_sha):\n"
        "    return any(value == head_sha for _, value in evidence)\n",
        "reader.py::lapsed",
        "return any((value == head_sha for _, value in evidence))",
    ),
    "a-record-through-a-module-alias": (
        f"import {CriterionEvidence.__module__} as records\n"
        "def lapsed(evidence, head):\n"
        "    kept: records.CriterionEvidence = evidence\n"
        "    return head in dict(kept).values()\n",
        "reader.py::lapsed",
        "return head in dict(kept).values()",
    ),
    "a-record-inside-annotated-and-optional": (
        "from typing import Annotated, Optional\n"
        f"from {CriterionEvidence.__module__} import {RECORD} as Evidence\n"
        "def lapsed(evidence: Annotated[Optional[Evidence], 'kept'], head):\n"
        "    return head in vars(evidence).values()\n",
        "reader.py::lapsed",
        "return head in vars(evidence).values()",
    ),
    "a-record-through-a-type-statement": (
        f"from {CriterionEvidence.__module__} import {RECORD}\n"
        f"type Evidence = {RECORD}\n"
        "def lapsed(evidence: Evidence, head_sha):\n"
        "    return any(value == head_sha for _, value in evidence)\n",
        "reader.py::lapsed",
        "return any((value == head_sha for _, value in evidence))",
    ),
    "a-record-through-an-assigned-alias": (
        f"from {CriterionEvidence.__module__} import {RECORD}\n"
        f"Evidence = {RECORD}\n"
        "def lapsed(evidence: Evidence, head_sha):\n"
        "    return head_sha in dict(evidence).values()\n",
        "reader.py::lapsed",
        "return head_sha in dict(evidence).values()",
    ),
    "a-record-through-a-typealias-annotation": (
        "from typing import TypeAlias\n"
        f"from {CriterionEvidence.__module__} import {RECORD}\n"
        f"Ev: TypeAlias = {RECORD}\n"
        "def lapsed(evidence, head_sha):\n"
        "    kept: Ev = evidence\n"
        "    return head_sha in dict(kept).values()\n",
        "reader.py::lapsed",
        "return head_sha in dict(kept).values()",
    ),
    "a-record-in-a-string-annotation": (
        f"from {CriterionEvidence.__module__} import {RECORD}\n"
        f"def lapsed(evidence: '{RECORD}', head_sha):\n"
        "    return head_sha in dict(evidence).values()\n",
        "reader.py::lapsed",
        "return head_sha in dict(evidence).values()",
    ),
    "a-record-in-a-string-imported-only-for-type-checking": (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        f"    from {CriterionEvidence.__module__} import {RECORD} as Ev\n"
        "def lapsed(evidence: 'Ev | None', head_sha):\n"
        "    return head_sha in dict(evidence).values()\n",
        "reader.py::lapsed",
        "return head_sha in dict(evidence).values()",
    ),
    # The record declared by the module as a type of its own: each is the
    # record by object, because every value of the type carries the field.
    "a-record-through-a-subclass": (
        f"from {CriterionEvidence.__module__} import {RECORD}\n"
        f"class Recorded({RECORD}):\n    pass\n"
        "def lapsed(evidence: Recorded, head_sha):\n"
        "    return head_sha in dict(evidence).values()\n",
        "reader.py::lapsed",
        "return head_sha in dict(evidence).values()",
    ),
    "a-record-through-a-newtype": (
        "from typing import NewType\n"
        f"from {CriterionEvidence.__module__} import {RECORD}\n"
        f"Recorded = NewType('Recorded', {RECORD})\n"
        "def lapsed(evidence: Recorded, head_sha):\n"
        "    return head_sha in dict(evidence).values()\n",
        "reader.py::lapsed",
        "return head_sha in dict(evidence).values()",
    ),
    "a-record-through-a-typevar-bound": (
        "from typing import TypeVar\n"
        f"from {CriterionEvidence.__module__} import {RECORD}\n"
        f"T = TypeVar('T', bound={RECORD})\n"
        "def lapsed(evidence: T, head_sha):\n"
        "    return head_sha in dict(evidence).values()\n",
        "reader.py::lapsed",
        "return head_sha in dict(evidence).values()",
    ),
    "a-record-through-a-typevar-constraint": (
        "from typing import TypeVar\n"
        f"from {CriterionEvidence.__module__} import {RECORD}\n"
        f"T = TypeVar('T', {RECORD}, int)\n"
        "def lapsed(evidence: T, head_sha):\n"
        "    return head_sha in dict(evidence).values()\n",
        "reader.py::lapsed",
        "return head_sha in dict(evidence).values()",
    ),
    "a-record-through-a-type-parameter": (
        f"from {CriterionEvidence.__module__} import {RECORD}\n"
        f"def lapsed[T: {RECORD}](evidence: T, head_sha):\n"
        "    return head_sha in dict(evidence).values()\n",
        "reader.py::lapsed",
        "return head_sha in dict(evidence).values()",
    ),
    "a-record-through-a-type-parameter-of-a-nested-function": (
        f"from {CriterionEvidence.__module__} import {RECORD}\n"
        "def outer():\n"
        f"    def lapsed[T: {RECORD}](evidence: T, head_sha):\n"
        "        return head_sha in dict(evidence).values()\n"
        "    return lapsed\n",
        "reader.py::outer.lapsed",
        "return head_sha in dict(evidence).values()",
    ),
    "a-record-through-a-class-type-parameter": (
        f"from {CriterionEvidence.__module__} import {RECORD}\n"
        f"class Probe[T: {RECORD}]:\n"
        "    def lapsed(self, evidence: T, head_sha):\n"
        "        return head_sha in dict(evidence).values()\n",
        "reader.py::Probe.lapsed",
        "return head_sha in dict(evidence).values()",
    ),
    # A class body's names are the namespace a method's signature is
    # evaluated in: an alias written there is the record for the method.
    "a-record-through-a-class-body-type-statement": (
        f"from {CriterionEvidence.__module__} import {RECORD}\n"
        "class Probe:\n"
        f"    type Recorded = {RECORD}\n"
        "    def lapsed(self, evidence: Recorded, head_sha):\n"
        "        return head_sha in dict(evidence).values()\n",
        "reader.py::Probe.lapsed",
        "return head_sha in dict(evidence).values()",
    ),
    "a-record-through-a-class-attribute": (
        f"from {CriterionEvidence.__module__} import {RECORD}\n"
        "class Probe:\n"
        f"    Recorded = {RECORD}\n"
        "    def lapsed(self, evidence: Recorded, head_sha):\n"
        "        return head_sha in dict(evidence).values()\n",
        "reader.py::Probe.lapsed",
        "return head_sha in dict(evidence).values()",
    ),
    # Bound by unpacking and spelled as a string: neither the function's own
    # evaluated annotations nor the alias statements say what it is, and only
    # the imported class's namespace does.
    "a-record-through-a-class-body-name-in-a-string": (
        f"from {CriterionEvidence.__module__} import {RECORD}\n"
        "class Probe:\n"
        f"    Recorded, Other = {RECORD}, int\n"
        "    def lapsed(self, evidence: 'Recorded', head_sha):\n"
        "        return head_sha in dict(evidence).values()\n",
        "reader.py::Probe.lapsed",
        "return head_sha in dict(evidence).values()",
    ),
    # Neither the class nor its method is bound after import; the alias is
    # read off the class body as written.
    "a-record-through-a-class-body-alias-inside-a-function": (
        f"from {CriterionEvidence.__module__} import {RECORD}\n"
        "def outer():\n"
        "    class Probe:\n"
        f"        type Recorded = {RECORD}\n"
        "        def lapsed(self, evidence: Recorded, head_sha):\n"
        "            return head_sha in dict(evidence).values()\n"
        "    return Probe\n",
        "reader.py::outer.Probe.lapsed",
        "return head_sha in dict(evidence).values()",
    ),
    # Only the running program knows what this annotation evaluates to.
    "a-record-looked-up-when-the-def-runs": (
        f"from {CriterionEvidence.__module__} import {RECORD}\n"
        f"RECORDS = {{'evidence': {RECORD}}}\n"
        "def lapsed(evidence: RECORDS['evidence'], head_sha):\n"
        "    return head_sha in dict(evidence).values()\n",
        "reader.py::lapsed",
        "return head_sha in dict(evidence).values()",
    ),
    # An alias one step past a TYPE_CHECKING import: the running module binds
    # neither name, and the alias is read as written.
    "a-record-through-a-type-checking-type-statement": (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        f"    from {CriterionEvidence.__module__} import {RECORD}\n"
        f"    type Recorded = {RECORD}\n"
        "def lapsed(evidence: 'Recorded', head_sha):\n"
        "    return head_sha in dict(evidence).values()\n",
        "reader.py::lapsed",
        "return head_sha in dict(evidence).values()",
    ),
    "a-record-through-a-type-checking-assignment": (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        f"    from {CriterionEvidence.__module__} import {RECORD}\n"
        f"    Recorded = {RECORD}\n"
        "def lapsed(evidence: 'Recorded', head_sha):\n"
        "    return head_sha in dict(evidence).values()\n",
        "reader.py::lapsed",
        "return head_sha in dict(evidence).values()",
    ),
    "a-record-through-a-type-checking-typealias": (
        "from typing import TYPE_CHECKING, TypeAlias\n"
        "if TYPE_CHECKING:\n"
        f"    from {CriterionEvidence.__module__} import {RECORD}\n"
        f"    Recorded: TypeAlias = {RECORD}\n"
        "def lapsed(evidence: 'Recorded', head_sha):\n"
        "    return head_sha in dict(evidence).values()\n",
        "reader.py::lapsed",
        "return head_sha in dict(evidence).values()",
    ),
}


@pytest.mark.parametrize("form", list(BINDINGS))
def test_every_binding_form_carries_the_graded_sha_to_its_use(form):
    """Each binding form is followed; undoing any one of them reds its case."""
    source, site, use = BINDINGS[form]
    found = alone(source)
    assert site in found, sorted(found)
    assert use in found[site], found[site]


#: A method, a class-level lambda and a nested ``def``, each closing over a
#: carrier the registered scope already binds, planted into that scope.
NESTED_CLOSURES = {
    "domain/criterion_cross_off.py::cross_offs_for": "recorded",
    "services/assertion_drift.py::AssertionDriftDetector.compare": "resolved_graded",
}


@pytest.mark.parametrize("site", list(NESTED_CLOSURES))
def test_a_closure_through_a_nested_class_in_a_registered_reader_is_reported(site):
    """A registered row is not a licence for a class written inside it.

    A method or a lambda of a class nested in the registered function closes
    over the function's carrier as surely as a nested ``def`` does, and each
    is a reader of its own.
    """
    carrier = NESTED_CLOSURES[site]
    assert carrier in SHIPPED[site.partition("::")[0]]
    blocks = {
        f"{site}._Probe.lapsed": (
            "class _Probe:\n"
            "    def lapsed(self, head):\n"
            f"        return {carrier} != head\n"
        ),
        f"{site}._Rule.<lambda>": (
            f"class _Rule:\n    lapsed = staticmethod(lambda head: {carrier} != head)\n"
        ),
        f"{site}._probe": f"def _probe(head):\n    return {carrier} != head\n",
    }
    for reader, block in blocks.items():
        assert findings(planted(site, block), REGISTERED) == [
            f"{reader} reads the graded sha and is not registered"
        ], reader


def test_a_class_passes_on_only_the_names_its_enclosing_function_carries():
    """A method's own parameter, and a class at module scope, carry nothing.

    The names a class closes over reach its methods; a parameter spelled
    like a carrier shadows it, and a module-level class has nothing to pass.
    """
    sources = (
        "def lapsed(evidence):\n"
        "    recorded = evidence.graded_sha\n"
        "    class A:\n"
        "        def weigh(self, recorded, head):\n"
        "            return recorded != head\n"
        "    return recorded\n",
        "class A:\n"
        "    recorded = 'x'\n"
        "    def weigh(self, head):\n"
        "        return recorded != head\n",
    )
    assert [frozenset(alone(source)) for source in sources] == [
        {"reader.py::lapsed"},
        frozenset(),
    ]


def test_a_name_imported_as_another_class_is_not_the_record():
    """The widening is by object: the record's own base, aliased, stays out."""
    base = CriterionEvidence.__mro__[1]
    assert base is not CriterionEvidence
    source = (
        f"from {base.__module__} import {base.__name__} as Evidence\n"
        "def lapsed(evidence: Evidence, head_sha):\n"
        "    return head_sha in dict(evidence).values()\n"
    )
    assert alone(source) == {}


#: The record named through a binding the module makes itself, as module-level
#: lines and the annotation that spells the binding.  None is an import of
#: the name the annotation uses, so each is found only in the module's own
#: namespace, or, for the string, by reading what it spells.
RECORD_ALIASES = {
    "a-type-statement": (f"type _Recorded = {RECORD}\n", "_Recorded"),
    "an-assigned-alias": (f"_Recorded = {RECORD}\n", "_Recorded"),
    "a-typealias-annotation": (
        f"from typing import TypeAlias\n_Recorded: TypeAlias = {RECORD}\n",
        "_Recorded",
    ),
    "a-string-annotation": ("", f"'{RECORD}'"),
    "a-subclass": (f"class _Recorded({RECORD}):\n    pass\n", "_Recorded"),
    "a-newtype": (
        f"from typing import NewType\n_Recorded = NewType('_Recorded', {RECORD})\n",
        "_Recorded",
    ),
    "a-typevar-bound": (
        "from typing import TypeVar\n"
        f"_Recorded = TypeVar('_Recorded', bound={RECORD})\n",
        "_Recorded",
    ),
    "a-typevar-constraint": (
        "from typing import TypeVar\n"
        f"_Recorded = TypeVar('_Recorded', {RECORD}, int)\n",
        "_Recorded",
    ),
    "a-class-body-alias": (
        f"class _Probe:\n    type Recorded = {RECORD}\n",
        "_Probe.Recorded",
    ),
    "a-type-checking-type-statement": (
        "from typing import TYPE_CHECKING\n"
        f"if TYPE_CHECKING:\n    type _Recorded = {RECORD}\n",
        "'_Recorded'",
    ),
    "a-type-checking-assignment": (
        "from typing import TYPE_CHECKING\n"
        f"if TYPE_CHECKING:\n    _Recorded = {RECORD}\n",
        "'_Recorded'",
    ),
    "a-type-checking-typealias": (
        "from typing import TYPE_CHECKING, TypeAlias\n"
        f"if TYPE_CHECKING:\n    _Recorded: TypeAlias = {RECORD}\n",
        "'_Recorded'",
    ),
}


def _aliased(form: str) -> tuple[str, str]:
    """The module-level lines a record alias needs, and a whole read through it."""
    lines, annotation = RECORD_ALIASES[form]
    header = f"from {CriterionEvidence.__module__} import {RECORD}\n{lines}"
    use = (
        f"recorded: {annotation} = cross_off.evidence\n"
        "if head_sha not in dict(recorded).values():\n"
        "    rederive.append(cross_off)\n"
        "    continue\n"
    )
    return header, use


@pytest.mark.parametrize("form", list(RECORD_ALIASES))
def test_a_record_named_through_the_modules_own_binding_is_reported(form):
    """A record type the module aliases itself is the record, by object.

    Planted as a new module and inside the lane reader, where the rule is
    consulted: in each the whole read is a use of the graded sha.
    """
    header, use = _aliased(form)
    source = header + "def lapsed(cross_off, head_sha, rederive):\n"
    source += _indented("for _ in (cross_off,):\n" + _indented(use))
    assert findings(alone(source), REGISTERED) == [
        "reader.py::lapsed reads the graded sha and is not registered"
    ]
    site = "domain/lapse.py::held_standing"
    module = site.partition("::")[0]
    anchor = "        state = graded_state(\n"
    assert SHIPPED[module].count(anchor) == 1
    written = SHIPPED[module].replace(anchor, _indented(_indented(use)) + anchor)
    found = findings(readers({module: written + header}), REGISTERED)
    read = "if head_sha not in dict(recorded).values():\n    ..."
    assert found == [f"{site} uses it at {[read]} beyond its row, and not at []"]


@pytest.mark.parametrize("form", list(RECORD_ALIASES))
def test_a_record_named_through_an_alias_in_any_registered_reader_is_reported(form):
    """The same aliased record, read whole inside every registered reader."""
    lines, annotation = RECORD_ALIASES[form]
    header = f"from {CriterionEvidence.__module__} import {RECORD}\n{lines}"
    block = f"_record: {annotation} = evidence\n_planted = dict(_record)\n"
    missed = [
        site
        for site in PLANT_SITES
        if not findings(planted(site, block, module_lines=header), REGISTERED)
    ]
    assert missed == []


#: A method typed by its class body's alias and a function typed by its own
#: type parameter, each written inside the lane reader, where the running
#: module binds neither the class nor the function.
NESTED_DECLARATIONS = {
    "domain/lapse.py::held_standing._Probe.lapsed": (
        "class _Probe:\n"
        f"    type Recorded = {RECORD}\n"
        "    def lapsed(self, evidence: Recorded, head):\n"
        "        return head in dict(evidence).values()\n"
    ),
    "domain/lapse.py::held_standing._lapsed": (
        f"def _lapsed[T: {RECORD}](evidence: T, head):\n"
        "    return head in dict(evidence).values()\n"
    ),
}


@pytest.mark.parametrize("reader", list(NESTED_DECLARATIONS))
def test_a_record_declared_inside_the_lane_reader_is_reported(reader):
    """A type declared where nothing runs is still read as the record."""
    site = "domain/lapse.py::held_standing"
    assert reader.startswith(f"{site}.")
    header = f"from {CriterionEvidence.__module__} import {RECORD}\n"
    found = findings(
        planted(site, NESTED_DECLARATIONS[reader], module_lines=header), REGISTERED
    )
    assert found == [f"{reader} reads the graded sha and is not registered"]


def test_a_declared_type_that_is_not_the_record_stays_out():
    """By object: the record's base, declared in every widened shape, stays out.

    The base lacks the field, so a value of a type derived from it, a
    ``NewType`` over it, a ``TypeVar`` bound or constrained to it, a type
    parameter bound to it, a class-body or ``TYPE_CHECKING`` alias of it and
    a lookup evaluating to it are not reads of the graded sha; nor is an
    unbounded ``TypeVar`` or type parameter.
    """
    base = CriterionEvidence.__mro__[1]
    assert not issubclass(base, CriterionEvidence)
    other = base.__name__
    importing = f"from {base.__module__} import {other}\n"
    checking = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n" + _indented(
        importing
    )
    whole = "return head_sha in dict(evidence).values()\n"

    def read(annotation: str) -> str:
        return f"def lapsed(evidence: {annotation}, head_sha):\n    {whole}"

    def method(annotation: str) -> str:
        return _indented(
            f"def lapsed(self, evidence: {annotation}, head_sha):\n    {whole}"
        )

    sources = (
        importing + f"class Other({other}):\n    pass\n" + read("Other"),
        importing
        + f"from typing import NewType\nOther = NewType('Other', {other})\n"
        + read("Other"),
        importing
        + f"from typing import TypeVar\nT = TypeVar('T', bound={other})\n"
        + read("T"),
        importing
        + f"from typing import TypeVar\nT = TypeVar('T', {other}, int)\n"
        + read("T"),
        "from typing import TypeVar\nT = TypeVar('T')\n" + read("T"),
        importing + f"def lapsed[T: {other}](evidence: T, head_sha):\n    {whole}",
        f"def lapsed[T](evidence: T, head_sha):\n    {whole}",
        importing + f"class Probe[T: {other}]:\n" + method("T"),
        importing + f"class Probe:\n    type Other = {other}\n" + method("Other"),
        importing
        + f"class Probe:\n    Other, Another = {other}, int\n"
        + method("'Other'"),
        importing
        + "def outer():\n"
        + _indented(f"class Probe:\n    type Other = {other}\n" + method("Other"))
        + "    return Probe\n",
        importing + f"KINDS = {{'evidence': {other}}}\n" + read("KINDS['evidence']"),
        checking + f"    type Other = {other}\n" + read("'Other'"),
        checking + f"    Other = {other}\n" + read("'Other'"),
    )
    assert [source for source in sources if alone(source)] == []


def test_an_alias_or_a_string_naming_another_class_is_not_the_record():
    """Resolution is by object: an alias of another class, a string that names
    another class, and a string that is no expression at all, stay out."""
    base = CriterionEvidence.__mro__[1]
    importing = f"from {base.__module__} import {base.__name__}\n"
    sources = (
        importing + f"type Evidence = {base.__name__}\n"
        "def lapsed(evidence: Evidence, head_sha):\n"
        "    return head_sha in dict(evidence).values()\n",
        importing + f"Evidence = {base.__name__}\n"
        "def lapsed(evidence: Evidence, head_sha):\n"
        "    return head_sha in dict(evidence).values()\n",
        f"def lapsed(evidence: '{base.__name__}', head_sha):\n"
        "    return head_sha in dict(evidence).values()\n",
        "def lapsed(evidence: 'the recorded grading', head_sha):\n"
        "    return head_sha in dict(evidence).values()\n",
    )
    assert [source for source in sources if alone(source)] == []


def test_naming_the_graded_sha_without_comparing_it_is_not_a_site():
    """Naming the field to write it is not reading it.

    A record built with the field as a keyword, a field declared under the
    name, an attribute stored under it, another field of the record, and
    every other revision, are not reads of the graded sha.
    """
    sources = (
        "def stamp(head_sha):\n    return Report(graded_sha=head_sha)\n",
        "class Report:\n    graded_sha: str\n",
        "def park(self, head_sha):\n    self.graded_sha = head_sha\n",
        "def other(evidence):\n"
        "    return evidence.recorded_sha != evidence.checked_sha\n",
        "def moved(head_sha, other_head_sha):\n    return head_sha != other_head_sha\n",
        "def pointed(evidence: CriterionEvidence, pointer):\n"
        "    return evidence.test.endswith(pointer)\n",
    )
    assert [source for source in sources if alone(source)] == []


def test_a_reader_consulting_the_rule_is_not_a_site_and_its_own_arithmetic_is():
    """The consultation is a pinned use; arithmetic beside it is not.

    Read on the shipped readers of the rule: each is green as it stands, and
    the same reader with the pair weighed beside the consultation is red.
    """
    assert CALLERS
    for site in CALLERS:
        module = site.partition("::")[0]
        assert findings(readers({module: SHIPPED[module]}), REGISTERED) == []
        weighed = planted(site, "_planted = evidence.graded_sha != head_sha\n")
        assert [
            finding.partition(" ")[0] for finding in findings(weighed, REGISTERED)
        ] == [site]


def test_one_body_comparing_twice_is_one_site_and_two_bodies_are_two():
    source = (
        "def lapsed(evidence, head_sha, other_head_sha):\n"
        "    if evidence.graded_sha == head_sha:\n"
        "        return False\n"
        "    return evidence.graded_sha != other_head_sha\n"
        "def stale(evidence, head_sha):\n"
        "    return evidence.graded_sha != head_sha\n"
    )
    assert alone(source) == {
        "reader.py::lapsed": Counter(
            {
                "if evidence.graded_sha == head_sha:\n    ...": 1,
                "return evidence.graded_sha != other_head_sha": 1,
            }
        ),
        "reader.py::stale": Counter({"return evidence.graded_sha != head_sha": 1}),
    }


def test_the_stated_limit_is_evasion_and_it_is_not_seen():
    """What the docstring says this check does not claim, read as code.

    In each boundary case the function that reads the field directly is
    reported, and the function comparing what it was handed is not.  What
    ``eval`` is handed is out of reach only when the name is built at run
    time: a literal dotted path to the field is a read wherever it is
    written.
    """
    evasions = (
        "def lapsed(evidence, head_sha):\n"
        "    return getattr(evidence, 'graded' + '_sha') != head_sha\n"
        "def evaluated(evidence, head_sha):\n"
        "    return eval('evidence.graded' + '_sha') != head_sha\n"
    )
    assert alone(evasions) == {}
    evaluated_literal = (
        "def evaluated(evidence, head_sha):\n"
        "    return eval('evidence.graded_sha') != head_sha\n"
    )
    assert frozenset(alone(evaluated_literal)) == {"reader.py::evaluated"}
    through_a_global = (
        "def keep(evidence):\n"
        "    global _RECORDED\n"
        "    _RECORDED = evidence.graded_sha\n"
        "def lapsed(head):\n"
        "    return _RECORDED != head\n"
    )
    assert frozenset(alone(through_a_global)) == {"reader.py::keep"}
    through_a_return = (
        "class Ledger:\n"
        "    def recorded(self):\n"
        "        return self._evidence.graded_sha\n"
        "def lapsed(ledger, head):\n"
        "    return ledger.recorded() != head\n"
    )
    assert frozenset(alone(through_a_return)) == {"reader.py::Ledger.recorded"}
    through_an_object = (
        "class Reader:\n"
        "    def keep(self, row):\n"
        "        self._taken = row.graded_sha\n"
        "    def lapsed(self, head):\n"
        "        return self._taken != head\n"
    )
    assert frozenset(alone(through_an_object)) == {"reader.py::Reader.keep"}
    through_an_argument = (
        "def differs(recorded, head):\n"
        "    return recorded != head\n"
        "def asks(evidence, head):\n"
        "    return differs(evidence.graded_sha, head)\n"
    )
    assert frozenset(alone(through_an_argument)) == {"reader.py::asks"}


def test_a_record_read_whole_without_a_declared_type_is_not_seen():
    """A value passed in whose type the module does not declare, held as a fact.

    Read whole through a value the module never declares as the record, the
    graded sha is unseen: it crossed a function boundary as a parameter, and
    nothing names its type.  The same read with the record's annotation is
    seen.
    """
    uses = (
        "head_sha in dict(evidence).values()",
        "head_sha in evidence.model_dump().values()",
        "head_sha in vars(evidence).values()",
        "any(value == head_sha for _, value in evidence)",
    )
    for use in uses:
        unseen = f"def lapsed(evidence, head_sha):\n    return {use}\n"
        assert alone(unseen) == {}, use
    seen = (
        f"def lapsed(evidence: {RECORD}, head_sha):\n"
        "    return head_sha in dict(evidence).values()\n"
    )
    assert alone(seen) == {
        "reader.py::lapsed": Counter({"return head_sha in dict(evidence).values()": 1})
    }


def test_a_value_returned_from_a_helper_is_not_seen():
    """A value handed back across a function boundary is out of reach.

    The helper that reads the field is seen; the caller comparing what the
    helper returned is not.
    """
    source = (
        "def recorded(evidence):\n"
        "    return evidence.graded_sha\n"
        "def lapsed(evidence, head_sha):\n"
        "    return recorded(evidence) != head_sha\n"
    )
    assert alone(source) == {
        "reader.py::recorded": Counter({"return evidence.graded_sha": 1})
    }


def test_a_name_built_at_run_time_is_not_seen():
    """A name built at run time and handed to ``eval`` is out of reach.

    The same path written as a literal is a read.
    """
    built = (
        "def evaluated(evidence, head_sha):\n"
        "    field = 'graded' + '_sha'\n"
        "    return eval(f'evidence.{field}') != head_sha\n"
    )
    assert alone(built) == {}
    literal = (
        "def evaluated(evidence, head_sha):\n"
        "    return eval('evidence.graded_sha') != head_sha\n"
    )
    assert frozenset(alone(literal)) == {"reader.py::evaluated"}


def test_a_binding_made_only_when_a_function_runs_is_not_seen():
    """A ``setattr`` on the module made inside a function is out of reach.

    Nothing is called, so a binding the module makes only when one of its
    functions runs is not in its namespace after import: neither a second
    rule bound under the rule's name nor a record alias bound for an
    annotation is seen.  The same bindings made at module level are.
    """
    module = "chains/audit_evidence.py"
    call = f"{RULE}({GRADED}=evidence.{GRADED}, head_sha=head)"
    install = (
        f"def _install():\n    setattr(sys.modules[__name__], '{RULE}', _shadow)\n"
    )
    assert _misdirected(module, SHIPPED[module] + f"\n\n{SHADOW}{install}") == []
    at_import = f"\n\n{SHADOW}globals()['{RULE}'] = _shadow\n"
    assert _misdirected(module, SHIPPED[module] + at_import) == [call]
    alias = (
        f"from {CriterionEvidence.__module__} import {RECORD}\n"
        "def _install():\n"
        f"    setattr(sys.modules[__name__], '_Recorded', {RECORD})\n"
        "def lapsed(evidence: '_Recorded', head_sha):\n"
        "    return head_sha in dict(evidence).values()\n"
    )
    assert alone(alias) == {}
    bound = alias + f"_Recorded = {RECORD}\n"
    assert frozenset(alone(bound)) == {"reader.py::lapsed"}


def _is_rule_call(node: ast.AST) -> bool:
    """Whether this expression is the rule being consulted."""
    if not isinstance(node, ast.Call):
        return False
    called = node.func
    if isinstance(called, ast.Attribute):
        return called.attr == RULE
    return isinstance(called, ast.Name) and called.id == RULE


#: Every body that consults the rule, each with the reading it takes from it.
#: One body per package: a package asking twice is two readers of one answer,
#: which is how the second of them starts qualifying it.
CALLERS = {
    "domain/lapse.py::held_standing": (
        "the lane arm's one reader: the partition the loop's next iteration "
        "is dispatched from"
    ),
    "types/domain/audit_evidence.py::AuditEvidenceObservation.is_lapse": (
        "the audit lane's observation of a finished claim's recorded grading"
    ),
    "chains/audit_evidence.py::AuditEvidenceVerifier._observe": (
        "whether a finished claim is still worth verifying afresh at the head"
    ),
}


def _calls(tree: ast.AST) -> frozenset[str]:
    """Each scope that consults the rule, once per scope."""
    found: set[str] = set()

    def walk(node: ast.AST, label: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            here = label
            if isinstance(child, NAMED):
                here = child.name if label is None else f"{label}.{child.name}"
            elif label is None:
                here = f"line {child.lineno}"
            if _is_rule_call(child):
                found.add(here if here is not None else f"line {child.lineno}")
            walk(child, here)

    walk(tree, None)
    return frozenset(found)


def callers(root: Path) -> frozenset[str]:
    """Every body in *root* that consults the rule, as module::qualname."""
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        found.update(
            f"{module}::{site}" for site in _calls(ast.parse(path.read_text()))
        )
    return frozenset(found)


def test_every_reader_of_the_rule_is_named_with_the_reading_it_takes():
    """A new consumer is a decision, so it arrives here or it reds."""
    assert callers(SOURCE) == frozenset(CALLERS), sorted(
        callers(SOURCE) ^ frozenset(CALLERS)
    )
    assert all(reason.strip() for reason in CALLERS.values())


def test_every_reader_of_the_rule_is_a_registered_direct_reader():
    """Consulting the rule means handing it the graded sha, so both tables see it."""
    assert frozenset(CALLERS) <= frozenset(REGISTERED)


def test_the_rule_is_consulted_from_one_body_in_each_package_that_reads_it():
    """One reader per package, so the answer is not qualified twice over."""
    packages: dict[str, list[str]] = {}
    for site in sorted(callers(SOURCE)):
        module, _, _ = site.partition("::")
        packages.setdefault(module.rpartition("/")[0], []).append(site)
    assert packages
    assert all(len(sites) == 1 for sites in packages.values()), packages


def _bindings_of(tree: ast.Module, module: str, name: str) -> list[object]:
    """What each binding of *name* anywhere in the module binds it to.

    An import binds the object its path resolves to.  The rule's own
    definition, at the top of the rule's own module, binds the rule.  Any
    other binding -- a definition, a class, an assignment, a parameter, a
    capture -- binds something that is not the rule, written as None.
    """
    found: list[object] = [
        _resolved(path)
        for bound, path in _imports(tree, _package(module))
        if bound == name
    ]
    for node in ast.walk(tree):
        if isinstance(node, (*NAMED, ast.ExceptHandler)) and node.name == name:
            written_here = module == RULE_MODULE and node in tree.body
            found.append(graded_state if written_here and name == RULE else None)
        elif (
            (
                isinstance(node, ast.Name)
                and not isinstance(node.ctx, ast.Load)
                and node.id == name
            )
            or (isinstance(node, ast.arg) and node.arg == name)
            or (isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name)
        ):
            found.append(None)
    return found


def _misdirected(module: str, text: str) -> list[str]:
    """Each consultation of the rule in *module* whose callee is not the rule.

    The callee is resolved by object, twice.  Statically, its name (or the
    name its dotted path starts from) must be bound, everywhere in the
    module, only to the rule or to the module the rule lives in.  In the
    module's own namespace after import (see ``_namespace``), the name, or
    its dotted route, must be the rule itself: a star import, a ``globals()``
    store and any other module-level rebinding have run by then.  A pinned
    consultation pins the call's text, so a rule shadowed under the pinned
    call's text is caught here or nowhere.
    """
    tree = ast.parse(text)
    namespace = _namespace(module, text)
    misdirected: list[str] = []
    for node in ast.walk(tree):
        if not _is_rule_call(node):
            continue
        assert isinstance(node, ast.Call)
        attributes: list[str] = []
        root = node.func
        while isinstance(root, ast.Attribute):
            attributes.insert(0, root.attr)
            root = root.value
        bound = (
            _bindings_of(tree, module, root.id) if isinstance(root, ast.Name) else []
        )
        callees = [
            getattr(value, attributes[0], None) if attributes else value
            for value in bound
        ]
        for attribute in attributes[1:]:
            callees = [getattr(value, attribute, None) for value in callees]
        if (
            not callees
            or not all(callee is graded_state for callee in callees)
            or _looked_up(node.func, namespace) is not graded_state
        ):
            misdirected.append(ast.unparse(node))
    return misdirected


def test_every_consultation_calls_the_rule_itself_by_object():
    """Each pinned ``graded_state(...)`` resolves to the rule in its home module.

    Read in each consulting module as imported, too: the name each
    consultation calls is the rule itself.
    """
    consulting = sorted({site.partition("::")[0] for site in CALLERS})
    assert consulting
    for module in consulting:
        imported = vars(import_module(_module_name(module)))
        calls = [
            node
            for node in ast.walk(ast.parse(SHIPPED[module]))
            if isinstance(node, ast.Call) and _is_rule_call(node)
        ]
        assert calls, module
        assert all(_looked_up(call.func, imported) is graded_state for call in calls), (
            module
        )
    assert {module: _misdirected(module, SHIPPED[module]) for module in consulting} == {
        module: [] for module in consulting
    }
    assert [
        module
        for module, text in SHIPPED.items()
        if module not in consulting
        and any(map(_is_rule_call, ast.walk(ast.parse(text))))
    ] == []


#: A definition that shadows the imported rule, appended to a caller.
SHADOWING_RULE = (
    f"def {RULE}(**reading: str) -> GradedState:\n"
    "    recorded, head = reading.values()\n"
    "    return GradedState.counted if recorded == head else GradedState.lapsed\n"
)


def test_a_definition_shadowing_the_rule_in_a_caller_is_reported():
    """The pinned text still matches, and the call no longer reaches the rule."""
    module = "chains/audit_evidence.py"
    shadowed = SHIPPED[module] + "\n\n" + SHADOWING_RULE
    assert findings(readers({module: shadowed}), REGISTERED) == []
    assert _misdirected(module, SHIPPED[module]) == []
    assert _misdirected(module, shadowed) == [
        f"{RULE}({GRADED}=evidence.{GRADED}, head_sha=head)"
    ]


#: A second rule under another name, written into a caller to be bound as
#: the rule's name at module level.
SHADOW = (
    "def _shadow(**reading: str) -> GradedState:\n"
    "    recorded, head = reading.values()\n"
    "    return GradedState.counted if recorded == head else GradedState.lapsed\n"
)


def test_a_rule_rebound_through_globals_in_a_caller_is_reported():
    """A ``globals()`` store rebinds the name the pinned call reads.

    Nothing the census pins changes, and the call no longer reaches the
    rule; storing the rule itself there changes nothing.
    """
    module = "chains/audit_evidence.py"
    call = f"{RULE}({GRADED}=evidence.{GRADED}, head_sha=head)"
    rebound = SHIPPED[module] + f"\n\n{SHADOW}globals()['{RULE}'] = _shadow\n"
    assert findings(readers({module: rebound}), REGISTERED) == []
    assert _misdirected(module, rebound) == [call]
    itself = SHIPPED[module] + f"\n\nglobals()['{RULE}'] = {RULE}\n"
    assert _misdirected(module, itself) == []


def test_a_rule_shadowed_by_a_star_import_in_a_caller_is_reported(monkeypatch):
    """A star import of a module defining its own rule rebinds the name.

    A star import of the rule's own module keeps it the rule.
    """
    module = "chains/audit_evidence.py"
    call = f"{RULE}({GRADED}=evidence.{GRADED}, head_sha=head)"
    other = ModuleType(f"{SOURCE.name}.chains._zz_rule")

    def shadow(**reading: str) -> bool:
        recorded, head = reading.values()
        return recorded == head

    vars(other).update({"__all__": [RULE], RULE: shadow})
    monkeypatch.setitem(sys.modules, other.__name__, other)
    starred = SHIPPED[module] + f"\n\nfrom {other.__name__} import *\n"
    assert findings(readers({module: starred}), REGISTERED) == []
    assert _misdirected(module, starred) == [call]
    home = SHIPPED[module] + f"\n\nfrom {graded_state.__module__} import *\n"
    assert _misdirected(module, home) == []


def test_a_consultation_is_resolved_through_whatever_import_binds_it():
    """The rule's module under an alias resolves; another object so named does not."""
    call = f"{RULE}({GRADED}=evidence.{GRADED}, head_sha=head)"
    home = graded_state.__module__
    package, _, leaf = home.rpartition(".")
    through_the_module = (
        f"from {package} import {leaf} as rules\n"
        f"def one(evidence, head):\n    return rules.{call}\n"
    )
    assert _misdirected("reader.py", through_the_module) == []
    another = (
        f"from {home} import GradedState as {RULE}\n"
        f"def one(evidence, head):\n    return {call}\n"
    )
    assert _misdirected("reader.py", another) == [call]
    written_here = f"def {RULE}(**reading):\n    return None\n" + (
        f"def one(evidence, head):\n    return {call}\n"
    )
    assert _misdirected("reader.py", written_here) == [call]


def test_a_second_reader_in_one_package_is_reported(tmp_path):
    source = (
        "def one(evidence, head_sha):\n"
        f"    return {RULE}({GRADED}=evidence.{GRADED}, head_sha=head_sha)\n"
        "def two(evidence, head_sha):\n"
        f"    return {RULE}({GRADED}=evidence.{GRADED}, head_sha=head_sha)\n"
    )
    assert _calls(ast.parse(source)) == frozenset({"one", "two"})
