"""Every ordering that consults priority reads the rank inputs alone (KOD-731).

The guard is the shape of the rank, not a word list. Over the whole package it
finds every ordering expression — a ``sorted``, ``.sort``, ``min``, ``max``,
``heapq.nsmallest`` or ``heapq.nlargest`` call, and every ``__lt__`` — whose
key or arguments reach a rank name, and compares what it found to two exact
registers: the sites, how many such expressions each definition holds and
every name read inside them *as an attribute*; and, per site, the unparsed
text of each ordering's key and arguments. A size fed into a rank has to be
read off something, and the text register moves for any read at all — an
attribute, a bare name, a subscript of one — so an ordering that reads a name
outside the rank inputs reds even when it spells no suspicious word: that is
the mutation this guard exists for, ``estimate=len(issue.body)`` on the rank
key and in both sort keys, which used no forbidden token at all.
``ast.unparse`` normalises formatting, so reflowing a key leaves the text
register green while any change to what that key reads moves it. The one
expression that makes a rank is pinned by its text for the same reason: what
it reads is counted as attributes off ``issue`` and what it calls by the word
spelled, and a subscript of the bare ``issue`` name is neither. That function
is pinned by its WHOLE body, not by that expression alone: the unparsed
statements after its docstring are compared to the one written return, so the
definition has room for no statement at all — nothing may substitute the issue
before the read. A call is pinned there by the word it spells, so a second
register reads the module's binding syntax: every name a statement of the
module binds is disjoint from every name it imports from the domain types.
That register reads syntax. What a name resolves to at run time is pinned by
object on the dispatch path below.

The dispatch path is derived from the live function that makes a rank: every
definition that hands it rows or that it hands on to, found by what their
reads resolve to, not by name. Each definition on it is pinned by the ``def``
its live code object was compiled from, with its decorators, its whole body
and the home of every object it reads. So a size table cannot sit in front
of a rank in the orderings, in the producer's pass, in its scan or in the
domain order one call deeper, and a decorator, a rebinding, a ``globals()``
or ``setattr`` write, or a module-level ``def`` of an imported word moves
what a name resolves to. The path test states its own limits.

Two weaker nets sit outside the shape pins. One collects every ``len(...)``
whose argument is an attribute and asserts none of those attributes is a text
column of the two row types the rank computes over, counting a column written
``str | None`` or wrapped in ``Annotated`` as the text column it is. The other
reads every identifier in the package — every name that is a ``Name``, an
attribute, a ``def``, a class, an argument or a keyword name — and asserts
none carries an estimate word.

The wired rank path is the unscoped producer's selection over
``domain/dispatch.py``; the scope dispatcher has no production caller, so it
is not where a rank input would arrive. The register is over the whole
package, so the question of which dispatch does not arise: an ordering that
consults priority anywhere is registered.

Blind spots, stated: a name assembled from parts rather than joined by
underscores is not matched; a size used as a filter rather than as an
ordering, or an ordering that reads no priority name in a definition the
register does not count, is outside every pin here. An ordering is registered
only when its key or arguments spell or denote a rank name themselves: a key
that reaches the rank through a helper — a named function, or a lambda that
calls one — is not registered, and the helper's body is not read. What the
registers read is what a key reads, not what those names were computed from,
so a size folded into a rank input *before* the ordering reaches the
ordering under the rank input's own name and is outside these registers; on
the dispatch path it is caught by the whole-body pin of the definition that
folds it, and off that path it is outside every pin here. The ordering
surface is matched by the word a callee spells, so a ``sorted`` reached under
another name — ``from builtins import sorted as s``, or ``s = sorted`` — is
no ordering expression here, even though
the resolver beside this guard denotes both spellings as ``sorted``. An
ordering expression here is one of the six calls named above or a ``__lt__``:
``bisect.insort``, ``heapq.heapify`` and ``heapq.heappush`` order by a key and
are not ordering expressions to this guard, and a hand-rolled comparison loop
is not detectable by call shape and is not claimed. The ``sorted`` around a
``functools.cmp_to_key`` comparator *is* found; it is not registered because
the comparator's name denotes no rank. A length
taken of a local name rather than of a field is not a text-length read, and a
length taken of a field that is not a text column — a count of relations, the
size of a label set — is outside the text-length net; strings and comments are
not scanned. The
words ``effort``, ``remaining`` and ``size`` are deliberately absent from the
vocabulary because the package uses them for a session effort setting, for
iteration and round counters, and for page sizes. The register of bound names
reads bindings — assignments with unpacking undone, loop and comprehension
targets, ``with`` and ``except`` aliases, parameters, and imports of anything
but the domain types — in this one module only, and it is disjoint from the
imported names rather than from every name those objects could be reached
under: an imported module's attribute rewritten in place, or the same domain
name re-imported under an alias from the domain types themselves, is outside
it. Names a class body declares are in that class's namespace, not the
module's, so they are not bindings here; the methods of that class are read
like any other definition.

``fire_plateaued`` compares ``len(ticks)`` with the plateau bound. The bound
counts ticks, which is the per-tick budget the lane deliverable allows, and it
is not a size, an estimate or a forecast of remaining work. The pins are
written narrowly enough never to reach it and ``len()`` is forbidden nowhere;
a control asserts the plateau module is reported by neither the register nor
the text-length net.
"""

import ast
import dataclasses
import functools
import inspect
import sys
import types
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Union, get_args, get_origin

import pytest

from kodezart.domain import dispatch, fire_plateau
from kodezart.domain.dispatch import RankKey, rank_key
from kodezart.types.domain.dispatch import IssueSnapshot
from kodezart.types.domain.scope_ready import ScopeReadyLane
from kodezart.types.domain.topology import ReadyIssue
from kodezart.types.domain.tracker import TrackerIssue, priority_rank
from tests.name_resolution import (
    SOURCE_ROOT,
    compiled_def,
    declares,
    definitions,
    home,
    in_package,
    package_functions,
    parsed,
    planted_module,
    references,
    resolve,
    source_tree,
    unwrapped,
    written_methods,
)

#: Every word a rank consults, read off the code: the rank value, the two
#: functions that make one, and every field of a row or a ready entry whose
#: own name says it carries a priority.
RANK_NAMES = (
    frozenset({RankKey.__name__, rank_key.__name__, priority_rank.__name__})
    | {
        name
        for model in (IssueSnapshot, TrackerIssue)
        for name in model.model_fields
        if "priority" in name
    }
    | {
        field.name
        for owner in (ReadyIssue, ScopeReadyLane)
        for field in dataclasses.fields(owner)
        if "priority" in field.name
    }
)
#: What an ordering may read. Anything else inside a key is a derived input.
RANK_INPUTS = frozenset(
    {
        "priority_rank",
        "priority",
        "effective_priority",
        "created_at",
        "issue_key",
        "issue",
    }
)
#: The only functions an ordering key may call.
RANK_CALLS = frozenset({priority_rank.__name__, rank_key.__name__})
#: The calls that order something by a key here. The two ``heapq`` selections
#: order by a key the way ``sorted`` does, and the callee's own attribute is no
#: region, so each is found by the word it spells.
ORDERING_CALLS = frozenset({"sorted", "sort", "min", "max", "nsmallest", "nlargest"})
#: Words a size, an estimate or a remaining-work forecast would be spelled
#: with, matched against an identifier's underscore-separated parts and any
#: run of them.
ESTIMATE_VOCABULARY = frozenset(
    {
        "estimate",
        "estimated",
        "estimates",
        "forecast",
        "forecasts",
        "velocity",
        "story_points",
    }
)
COMPARISON = "__lt__"


def _spells(node: ast.expr) -> str | None:
    """The bare or attribute-qualified word an expression spells."""
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else None


def _ordering_regions(node: ast.AST) -> tuple[ast.AST, ...] | None:
    """The key and arguments of an ordering, or ``None`` if this is not one.

    The callee's own attribute is not a region: ``ready.sort`` names the list
    it orders, not something the order reads.
    """
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return tuple(node.body) if node.name == COMPARISON else None
    if isinstance(node, ast.Call) and _spells(node.func) in ORDERING_CALLS:
        return (*node.args, *(keyword.value for keyword in node.keywords))
    return None


def _inside(regions: tuple[ast.AST, ...]) -> list[ast.AST]:
    return [node for region in regions for node in ast.walk(region)]


def _consults_rank(regions: tuple[ast.AST, ...], resolution) -> bool:
    """Whether an ordering's key or arguments reach a rank name at all."""
    for node in _inside(regions):
        if not isinstance(node, ast.Name | ast.Attribute):
            continue
        if resolution.denotes(node) in RANK_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in RANK_NAMES:
            return True
    return False


def attribute_reads(regions: tuple[ast.AST, ...]) -> frozenset[str]:
    """Every attribute name an ordering's key and arguments read."""
    return frozenset(
        node.attr for node in _inside(regions) if isinstance(node, ast.Attribute)
    )


def called_names(regions: tuple[ast.AST, ...]) -> frozenset[str]:
    """Every name an ordering's key and arguments call."""
    return frozenset(
        spelled
        for node in _inside(regions)
        if isinstance(node, ast.Call) and (spelled := _spells(node.func)) is not None
    )


def _priority_orderings(trees) -> Iterator[tuple[str, tuple[ast.AST, ...]]]:
    """Every priority ordering in *trees*, keyed the way a register keys it.

    ``module::dotted definition`` paired with the ordering's key and
    arguments. An ordering consults priority when its key or arguments reach a
    rank name under any spelling. One walk, because what each ordering reads
    and what it calls are two readings of the same expressions.
    """
    for module, tree in sorted(trees.items()):
        resolution = resolve(tree, names=RANK_NAMES)
        where = definitions(tree)
        for node in ast.walk(tree):
            regions = _ordering_regions(node)
            if regions is None or not _consults_rank(regions, resolution):
                continue
            yield f"{module}::{where.get(id(node), '<module>')}", regions


def ordering_sites(trees) -> dict[str, tuple[int, frozenset[str]]]:
    """Every ordering in the package that consults priority, by definition.

    Valued by how many such expressions the definition holds and every
    attribute name read inside their keys and arguments.
    """
    found: dict[str, list[frozenset[str]]] = {}
    for key, regions in _priority_orderings(trees):
        found.setdefault(key, []).append(attribute_reads(regions))
    return {
        key: (len(reads), frozenset().union(*reads)) for key, reads in found.items()
    }


def ordering_calls(trees) -> dict[str, frozenset[str]]:
    """Every name the registered orderings call, by the same key."""
    found: dict[str, set[str]] = {}
    for key, regions in _priority_orderings(trees):
        found.setdefault(key, set()).update(called_names(regions))
    return {key: frozenset(names) for key, names in found.items()}


def ordering_key_texts(trees) -> dict[str, tuple[str, ...]]:
    """The written key and arguments of every priority ordering, by the same key.

    One unparsed text per ordering, sorted within a definition so the reading
    does not depend on walk order. ``ordering_sites`` above reports the names a
    key reads *as attributes*, so a read spelled as a bare name or as a
    subscript of one leaves its row unchanged; the text moves for any of them.
    ``ast.unparse`` normalises formatting, so reflowing a key is not a change
    to what it reads and is not a change here.
    """
    found: dict[str, list[str]] = {}
    for key, regions in _priority_orderings(trees):
        found.setdefault(key, []).append(
            ", ".join(ast.unparse(region) for region in regions)
        )
    return {key: tuple(sorted(texts)) for key, texts in found.items()}


#: Exact. The five definitions in the package that order anything by priority,
#: with the attribute names each order reads. A sixth entry, or a read outside
#: RANK_INPUTS, is a new input to the rank and is read before it is accepted.
DISPATCH_ORDERINGS = {
    "domain/dispatch.py::<module>": (1, frozenset()),
    "domain/dispatch.py::RankKey.__lt__": (
        1,
        frozenset({"priority_rank", "created_at"}),
    ),
    "domain/dispatch.py::select_top_ranked": (2, frozenset({"issue_key"})),
    "domain/dispatch.py::ranked_order": (
        1,
        frozenset({"priority_rank", "created_at", "issue_key"}),
    ),
    "domain/topology.py::plan_topology": (
        2,
        frozenset({"effective_priority", "issue", "created_at"}),
    ),
}

#: Exact. The one expression that makes a rank, written out, so the same read
#: the attribute and callee pins cannot see — a subscript of the bare ``issue``
#: name into a size table — moves a register at the site that makes the rank
#: and not only at the sites that order by it.
RANK_KEY_TEXT = (
    "RankKey(priority_rank=priority_rank(issue.priority), created_at=issue.created_at)"
)

#: Exact. The whole body of that function after its docstring: the one return
#: and room for nothing else, so a statement placed before it — one that
#: substitutes the issue, or rebinds the name the return calls — moves a
#: register here even while the returned expression stays byte-identical.
RANK_KEY_BODY = (
    "return RankKey(priority_rank=priority_rank(issue.priority), "
    "created_at=issue.created_at)"
)

#: The package the domain row types are declared in, read off one of them, so
#: the rebinding register names no module by hand.
DOMAIN_TYPES = priority_rank.__module__.rsplit(".", 1)[0]

#: Exact, over the same five definitions. The text of each registered
#: ordering's key and arguments, so a read the attribute register cannot see —
#: a bare name, a subscript of one — moves a register too.
DISPATCH_ORDERING_KEYS = {
    "domain/dispatch.py::<module>": ("IssuePriority, priority_rank",),
    "domain/dispatch.py::RankKey.__lt__": (
        "return (self.priority_rank, self.created_at) < "
        "(other.priority_rank, other.created_at)",
    ),
    "domain/dispatch.py::select_top_ranked": (
        "(issue.issue_key for issue in issues if rank_key(issue) == best)",
        "(rank_key(issue) for issue in issues)",
    ),
    "domain/dispatch.py::ranked_order": (
        "issues, lambda issue: (rank_key(issue).priority_rank, "
        "rank_key(issue).created_at, issue.issue_key)",
    ),
    "domain/topology.py::plan_topology": (
        "effective[blocker], effective[key], priority_rank",
        "lambda entry: (priority_rank(entry.effective_priority), "
        "entry.issue.created_at)",
    ),
}


def _declared_text(annotation: object) -> frozenset[object]:
    """The types an annotation declares, with ``Annotated`` and a union undone.

    A container is left whole, unlike ``_named_types`` below: the element type
    of ``frozenset[str]`` is not what a length of that field measures.
    """
    if getattr(annotation, "__metadata__", None) is not None:
        return _declared_text(get_args(annotation)[0])
    if get_origin(annotation) in (Union, types.UnionType):
        return frozenset(
            one
            for argument in get_args(annotation)
            for one in _declared_text(argument)
            if one is not type(None)
        )
    return frozenset({annotation})


def text_fields(annotated: Mapping[str, object]) -> frozenset[str]:
    """Every field whose annotation declares text and nothing else.

    A column written ``str | None`` or wrapped in ``Annotated`` is the same
    text column as a bare ``str``, the way the numeric pin reads the leaves of
    what a field declares rather than its written form. A container of ``str``
    is not a text column: the length of a label set counts labels.
    """
    return frozenset(
        name
        for name, annotation in annotated.items()
        if _declared_text(annotation) == frozenset({str})
    )


def text_columns() -> frozenset[str]:
    """The text fields of the two row types a rank computes over."""
    return frozenset().union(
        *(
            text_fields(
                {name: field.annotation for name, field in model.model_fields.items()}
            )
            for model in (IssueSnapshot, TrackerIssue)
        )
    )


def text_length_reads(tree: ast.Module) -> frozenset[str]:
    """Every attribute whose length a ``len(...)`` in this module takes."""
    return frozenset(
        node.args[0].attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _spells(node.func) == len.__name__
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Attribute)
    )


def _parts(identifier: str) -> tuple[str, ...]:
    return tuple(part for part in identifier.lower().split("_") if part)


def estimate_words(identifier: str) -> frozenset[str]:
    """Every estimate word an identifier's underscore-separated parts spell."""
    parts = _parts(identifier)
    return frozenset(
        word
        for start in range(len(parts))
        for end in range(start + 1, len(parts) + 1)
        if (word := "_".join(parts[start:end])) in ESTIMATE_VOCABULARY
    )


def estimate_identifiers(tree: ast.Module) -> frozenset[str]:
    """Every identifier in this module that carries an estimate word."""
    spelled: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            spelled.add(node.id)
        elif isinstance(node, ast.Attribute):
            spelled.add(node.attr)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            spelled.add(node.name)
        elif isinstance(node, ast.arg):
            spelled.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            spelled.add(node.arg)
    return frozenset(name for name in spelled if estimate_words(name))


def declared_fields(tree: ast.Module, *, owner: str) -> tuple[str, ...]:
    """The annotated names a class body declares, in written order."""
    declared = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == owner
    )
    return tuple(
        statement.target.id
        for statement in declared.body
        if isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
    )


def _defined(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == name
    )


def receiver_reads(
    function: ast.FunctionDef | ast.AsyncFunctionDef, *, receiver: str
) -> frozenset[str]:
    """Every attribute a definition reads off one named receiver."""
    return frozenset(
        node.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == receiver
    )


def returned(function: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.expr:
    """The one expression a definition returns.

    Found rather than indexed: the definition that makes a rank opens with a
    docstring, so its first statement is not its return, and a second return
    would mean the rank is made in more than one place.
    """
    returns = [node for node in ast.walk(function) if isinstance(node, ast.Return)]
    assert len(returns) == 1
    value = returns[0].value
    assert value is not None
    return value


def callees(function: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    """Every name a definition calls, plain or as an attribute."""
    return frozenset(
        spelled
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and (spelled := _spells(node.func)) is not None
    )


def body_after_docstring(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.stmt, ...]:
    """Every statement a definition holds after its docstring.

    A docstring is a string constant in the first statement position, so a
    definition written without one keeps its whole body here.
    """
    body = tuple(function.body)
    opens_with_prose = (
        bool(body)
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    )
    return body[1:] if opens_with_prose else body


def body_text(function: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """The unparsed statements of a definition after its docstring.

    One text for the whole body, so a pin over it says what the definition
    does and not only what its return expression reads. ``ast.unparse``
    normalises formatting here as everywhere else in this module.
    """
    return "\n".join(
        ast.unparse(statement) for statement in body_after_docstring(function)
    )


def _bound_by(target: ast.expr) -> Iterator[str]:
    """Every name one assignment target binds, unpacking undone."""
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, ast.Starred):
        yield from _bound_by(target.value)
    elif isinstance(target, ast.Tuple | ast.List):
        for element in target.elts:
            yield from _bound_by(element)


def _class_scoped(tree: ast.Module) -> frozenset[int]:
    """The assignments a class body holds, which bind in its own namespace."""
    return frozenset(
        id(statement)
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        for statement in node.body
    )


def _parameters(function: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[str]:
    """Every parameter name a definition binds, in every position."""
    arguments = function.args
    for one in (
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
        arguments.vararg,
        arguments.kwarg,
    ):
        if one is not None:
            yield one.arg


def _from_domain_types(node: ast.ImportFrom) -> bool:
    """Whether an import reads from the package the domain types live in."""
    module = node.module or ""
    return module == DOMAIN_TYPES or module.startswith(f"{DOMAIN_TYPES}.")


def imported_domain_names(tree: ast.Module) -> frozenset[str]:
    """Every name a module imports from the domain types, as it binds it."""
    return frozenset(
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and _from_domain_types(node)
        for alias in node.names
    )


def bound_names(tree: ast.Module) -> frozenset[str]:
    """Every name a module binds other than by importing it from the domain.

    A binding is an assignment — plain, annotated, augmented or walrus, with
    unpacking undone — a loop or comprehension target, a ``with`` or
    ``except`` alias, a parameter, or an import of anything but the domain
    types, whose own import is what :func:`imported_domain_names` reads and
    would otherwise report every module as rebinding what it imports. An
    annotation carrying no value binds nothing. A name a class body declares
    binds in that class's namespace rather than the module's, so a field
    called after an imported function is no rebinding of it, while the
    methods of that class are read like any other definition.
    """
    class_scoped = _class_scoped(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign)
            and id(node) in class_scoped
        ):
            continue
        if isinstance(node, ast.Assign):
            for target in node.targets:
                found.update(_bound_by(target))
        elif isinstance(node, ast.AnnAssign):
            if node.value is not None:
                found.update(_bound_by(node.target))
        elif isinstance(node, ast.AugAssign | ast.NamedExpr):
            found.update(_bound_by(node.target))
        elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
            found.update(_bound_by(node.target))
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None:
                found.update(_bound_by(node.optional_vars))
        elif isinstance(node, ast.ExceptHandler):
            if node.name is not None:
                found.add(node.name)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            found.update(_parameters(node))
        elif isinstance(node, ast.Import):
            found.update(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and not _from_domain_types(node):
            found.update(alias.asname or alias.name for alias in node.names)
    return frozenset(found)


def _named_types(annotation: object) -> tuple[object, ...]:
    """Every type an annotation names, with wrappers and unions unwrapped.

    A quantity declared as ``int | None`` or inside an annotated field is the
    same declared quantity as a bare ``int``, so the numeric pin below reads
    the leaves rather than the written form.
    """
    if getattr(annotation, "__metadata__", None) is not None:
        return _named_types(get_args(annotation)[0])
    arguments = get_args(annotation)
    if not arguments:
        return (annotation,)
    return tuple(named for one in arguments for named in _named_types(one))


def numeric_fields(annotated: Mapping[str, object]) -> list[str]:
    """Every field whose annotation names a number anywhere inside it."""
    return sorted(
        name
        for name, annotation in annotated.items()
        if any(named in NUMERIC for named in _named_types(annotation))
    )


PACKAGE = source_tree()
PARSED = parsed(PACKAGE)
DISPATCH = "domain/dispatch.py"
PLATEAU = "domain/fire_plateau.py"
NUMERIC = (int, float, Decimal)


# ---------------------------------------------------------------------------
# The dispatch path, derived from the objects and pinned by what executes
# ---------------------------------------------------------------------------


def row_type(start: types.FunctionType) -> type:
    """The row a rank is made from, read off the one parameter of *start*."""
    (parameter,) = inspect.signature(start, eval_str=True).parameters.values()
    return parameter.annotation


def _takes(function: types.FunctionType, row: type) -> bool:
    return any(
        declares(parameter.annotation, row)
        for parameter in inspect.signature(function, eval_str=True).parameters.values()
    )


def _returns(function: types.FunctionType, row: type) -> bool:
    signature = inspect.signature(function, eval_str=True)
    return declares(signature.return_annotation, row)


def _owner(function: types.FunctionType) -> object:
    """The class a method is written in, or ``None`` for a module-level one."""
    if "<locals>" in function.__qualname__ or "." not in function.__qualname__:
        return None
    outer, *inner = function.__qualname__.split(".")[:-1]
    return functools.reduce(getattr, inner, function.__globals__[outer])


def _receiver_calls(function: types.FunctionType) -> frozenset[str]:
    """Every method *function* calls on its own receiver."""
    made = compiled_def(function)
    positional = [*made.args.posonlyargs, *made.args.args]
    if not positional:
        return frozenset()
    receiver = positional[0].arg
    return frozenset(
        node.func.attr
        for node in ast.walk(made)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == receiver
    )


def dispatch_path(
    start: types.FunctionType, *, functions: Sequence[types.FunctionType]
) -> tuple[types.FunctionType, ...]:
    """Every definition a rank passes through, found from the objects.

    Starts at *start*, the live function that makes a rank, and grows to a
    fixed point under three rules, each over what the definitions resolve to
    rather than what they spell:

    - **climb**: a definition taking the row type as a parameter is joined
      by every one of *functions* that references it — through a global, a
      module attribute, an import inside it, a ``functools.partial`` or a
      bound method;
    - **suppliers**: a method is joined by every method of its own class it
      calls on its receiver whose return declares the row type, which is how
      rows arrive at the producer's pass from its scan;
    - **forward**: every package function a definition on the path
      references joins, and so does every method written in a package class
      it references.
    """
    row = row_type(start)
    reached = {
        id(function): {id(unwrapped(value)) for value in references(function).values()}
        for function in functions
    }
    path: dict[int, types.FunctionType] = {id(start): start}
    while True:
        before = len(path)
        for function in tuple(path.values()):
            for value in map(unwrapped, references(function).values()):
                if isinstance(value, types.FunctionType) and in_package(value):
                    path.setdefault(id(value), value)
                elif isinstance(value, type) and in_package(value):
                    for method in written_methods(value):
                        path.setdefault(id(method), method)
            if _takes(function, row):
                for other in functions:
                    if id(function) in reached[id(other)]:
                        path.setdefault(id(other), other)
            owner = _owner(function)
            if isinstance(owner, type):
                for name in _receiver_calls(function):
                    method = unwrapped(inspect.getattr_static(owner, name, None))
                    if isinstance(method, types.FunctionType) and _returns(method, row):
                        path.setdefault(id(method), method)
        if len(path) == before:
            return tuple(path.values())


@dataclasses.dataclass(frozen=True)
class Executed:
    """What one definition on the path runs, read off its live object."""

    #: The decorators written on the ``def`` its code was compiled from.
    decorators: tuple[str, ...]
    #: That ``def``'s statements after its docstring, unparsed.
    body: str
    #: Every read it resolves, spelling -> the home of the object reached.
    reads: Mapping[str, str]


def executed(function: types.FunctionType) -> Executed:
    """What *function* runs: its compiled ``def`` and where each read lands."""
    made = compiled_def(function)
    return Executed(
        decorators=tuple(ast.unparse(decorator) for decorator in made.decorator_list),
        body=body_text(made),
        reads={
            spelling: home(value) for spelling, value in references(function).items()
        },
    )


#: The whole body of the producer's pass, from its scan to its hand-off.
RUN_PASS_BODY = "\n".join(
    (
        "team_keys = self._operation.team_keys_for_repo(self._repo_url)",
        "snapshot = await self._scan(team_keys)",
        "eligible: list[TrackerIssue] = []",
        "exclusions: list[IssueExclusion] = []",
        "for issue in snapshot:",
        "    exclusion = await self._exclude(issue, team_keys=team_keys)",
        "    if exclusion is None:",
        "        eligible.append(issue)",
        "    else:",
        "        exclusions.append(exclusion)",
        "rows = tuple((IssueSnapshot(issue_key=issue.issue_key, "
        "priority=issue.priority, state_name=issue.state_name, "
        "created_at=issue.created_at) for issue in snapshot))",
        "selection = select_top_ranked(eligible, draw=self._draw)",
        "if selection is None:",
        "    await self._log.ainfo('dispatch_empty_eligible_set', "
        "outcome=DispatchOutcome.empty_eligible_set.value, "
        "snapshot=[row.issue_key for row in rows], "
        "exclusions=[{'issueKey': item.issue_key, 'clause': item.clause.value} "
        "for item in exclusions])",
        "    return DispatchReport(outcome=DispatchOutcome.empty_eligible_set, "
        "snapshot=rows, exclusions=tuple(exclusions), eligible=())",
        "await self._log.ainfo('dispatch_ranked', order=list(ranked_order(eligible)), "
        "tied=list(selection.tied), winner=selection.winner_key)",
        "return await self._claim_and_enqueue(selection=selection, "
        "eligible=eligible, rows=rows, exclusions=tuple(exclusions))",
    )
)

#: Exact. Every definition on the dispatch path, by the home of its live
#: object, with what it runs: no decorators, its whole body, and the home of
#: everything it reads. Measured, not assumed: the walk reaches these seven
#: and no other.
DISPATCH_PATH: dict[str, Executed] = {
    "kodezart.domain.dispatch.rank_key": Executed(
        decorators=(),
        body=RANK_KEY_BODY,
        reads={
            "RankKey": "kodezart.domain.dispatch.RankKey",
            "priority_rank": "kodezart.types.domain.tracker.priority_rank",
        },
    ),
    "kodezart.domain.dispatch.RankKey.__lt__": Executed(
        decorators=(),
        body="return (self.priority_rank, self.created_at) < "
        "(other.priority_rank, other.created_at)",
        reads={},
    ),
    "kodezart.types.domain.tracker.priority_rank": Executed(
        decorators=(),
        body="return PRIORITY_RANK_ORDER.index(priority)",
        reads={
            "PRIORITY_RANK_ORDER": "(<IssuePriority.URGENT: 'urgent'>, "
            "<IssuePriority.HIGH: 'high'>, <IssuePriority.MEDIUM: 'medium'>, "
            "<IssuePriority.LOW: 'low'>, <IssuePriority.NONE: 'none'>)",
        },
    ),
    "kodezart.domain.dispatch.select_top_ranked": Executed(
        decorators=(),
        body="\n".join(
            (
                "if not issues:",
                "    return None",
                "best = min((rank_key(issue) for issue in issues))",
                "tied = tuple(sorted((issue.issue_key for issue in issues "
                "if rank_key(issue) == best)))",
                "if len(tied) == 1:",
                "    return Selection(winner_key=tied[0], tied=tied)",
                "return Selection(winner_key=draw(tied), tied=tied)",
            )
        ),
        reads={
            "Selection": "kodezart.domain.dispatch.Selection",
            "min": "builtins.min",
            "tuple": "builtins.tuple",
            "sorted": "builtins.sorted",
            "len": "builtins.len",
            "rank_key": "kodezart.domain.dispatch.rank_key",
        },
    ),
    "kodezart.domain.dispatch.ranked_order": Executed(
        decorators=(),
        body="return tuple((issue.issue_key for issue in sorted(issues, "
        "key=lambda issue: (rank_key(issue).priority_rank, "
        "rank_key(issue).created_at, issue.issue_key))))",
        reads={
            "tuple": "builtins.tuple",
            "sorted": "builtins.sorted",
            "rank_key": "kodezart.domain.dispatch.rank_key",
        },
    ),
    "kodezart.services.fire_dispatcher.FireDispatcher.run_pass": Executed(
        decorators=(),
        body=RUN_PASS_BODY,
        reads={
            "DispatchReport": "kodezart.types.domain.dispatch.DispatchReport",
            "list": "builtins.list",
            "tuple": "builtins.tuple",
            "select_top_ranked": "kodezart.domain.dispatch.select_top_ranked",
            "IssueSnapshot": "kodezart.types.domain.dispatch.IssueSnapshot",
            "DispatchOutcome": "kodezart.types.domain.dispatch.DispatchOutcome",
            "ranked_order": "kodezart.domain.dispatch.ranked_order",
        },
    ),
    "kodezart.services.fire_dispatcher.FireDispatcher._scan": Executed(
        decorators=(),
        body="\n".join(
            (
                "found: list[TrackerIssue] = []",
                "for team_key in team_keys:",
                "    found.extend(await self._tracker.scan_issues("
                "query=IssueQuery(queue_state=QueueState.APPROVED, "
                "team_key=team_key, page_size=self._query_page_size)))",
                "return tuple(found)",
            )
        ),
        reads={
            "tuple": "builtins.tuple",
            "IssueQuery": "kodezart.types.domain.tracker.IssueQuery",
            "QueueState": "kodezart.types.domain.operation.QueueState",
        },
    ),
}

#: A size table planted on the dispatch path, as ``module -> (anchor ->
#: planted, ...)`` over the shipped source, with the qualified name of the
#: definition it reaches. The first five change what a name resolves to and
#: leave every body written in the module byte-identical: a decorator, a
#: rebinding after the ``def``, a ``globals()`` write, a ``setattr`` on the
#: module and a module-level ``def`` of the imported name. The rest put the
#: table inside a body: the function that makes the rank, both orderings,
#: the producer's pass and its scan, and the domain order one call deeper —
#: its body, and the table it reads.
_SIZES = "_SIZES: dict = {}\n\n\n"
_WRAPPER = (
    "def _through(make):\n"
    "    @functools.wraps(make)\n"
    "    def wrapped(issue):\n"
    "        return make(_SIZES.get(issue, issue))\n"
    "\n"
    "    return wrapped\n"
    "\n"
    "\n"
)
PRODUCER = "services/fire_dispatcher.py"
DOMAIN_ORDER = "types/domain/tracker.py"
RANK_KEY_DEF = "def rank_key(issue: TrackerIssue) -> RankKey:\n"
SELECTION_CLASS = "@dataclass(frozen=True)\nclass Selection:\n"
IMPORTS = "from collections.abc import Callable, Collection, Mapping, Sequence\n"
SIZE_TABLES: dict[str, tuple[str, str, tuple[tuple[str, str], ...]]] = {
    "decorated_rank_key": (
        DISPATCH,
        "rank_key",
        (
            (IMPORTS, f"import functools\n{IMPORTS}"),
            (RANK_KEY_DEF, f"{_SIZES}{_WRAPPER}@_through\n{RANK_KEY_DEF}"),
        ),
    ),
    "rebound_rank_key": (
        DISPATCH,
        "rank_key",
        (
            (IMPORTS, f"import functools\n{IMPORTS}"),
            (
                SELECTION_CLASS,
                f"{_SIZES}{_WRAPPER}rank_key = _through(rank_key)\n\n\n"
                f"{SELECTION_CLASS}",
            ),
        ),
    ),
    "globals_write": (
        DISPATCH,
        "rank_key",
        (
            (
                RANK_KEY_DEF,
                f"{_SIZES}globals()['priority_rank'] = "
                f"lambda priority: _SIZES.get(priority, 0)\n\n\n{RANK_KEY_DEF}",
            ),
        ),
    ),
    "setattr_on_the_module": (
        DISPATCH,
        "rank_key",
        (
            (IMPORTS, f"import sys\n{IMPORTS}"),
            (
                RANK_KEY_DEF,
                f"{_SIZES}def _sized(priority):\n    return _SIZES.get(priority, 0)"
                "\n\n\nsetattr(sys.modules[__name__], 'priority_rank', _sized)\n\n\n"
                f"{RANK_KEY_DEF}",
            ),
        ),
    ),
    "module_def_of_the_imported_name": (
        DISPATCH,
        "rank_key",
        (
            (
                RANK_KEY_DEF,
                f"{_SIZES}def priority_rank(priority):\n"
                "    return _SIZES.get(priority, 0)\n\n\n"
                f"{RANK_KEY_DEF}",
            ),
        ),
    ),
    "rank_key_body": (
        DISPATCH,
        "rank_key",
        (
            (
                f'{RANK_KEY_DEF}    """Primary rank (Urgent first, None last), '
                'secondary oldest-first."""\n',
                f'{_SIZES}{RANK_KEY_DEF}    """Primary rank (Urgent first, None '
                'last), secondary oldest-first."""\n'
                "    issue = _SIZES.get(issue, issue)\n",
            ),
        ),
    ),
    "select_top_ranked_body": (
        DISPATCH,
        "select_top_ranked",
        (
            (SELECTION_CLASS, f"{_SIZES}{SELECTION_CLASS}"),
            (
                "    if not issues:\n",
                "    issues = [_SIZES.get(issue, issue) for issue in issues]\n"
                "    if not issues:\n",
            ),
        ),
    ),
    "ranked_order_body": (
        DISPATCH,
        "ranked_order",
        (
            (SELECTION_CLASS, f"{_SIZES}{SELECTION_CLASS}"),
            (
                "        for issue in sorted(\n            issues,\n",
                "        for issue in sorted(\n"
                "            [_SIZES.get(issue, issue) for issue in issues],\n",
            ),
        ),
    ),
    "producer_pass_body": (
        PRODUCER,
        "FireDispatcher.run_pass",
        (
            ("class FireDispatcher:\n", f"{_SIZES}class FireDispatcher:\n"),
            (
                "        selection = select_top_ranked(eligible, draw=self._draw)\n",
                "        eligible = [_SIZES.get(issue, issue) for issue in eligible]\n"
                "        selection = select_top_ranked(eligible, draw=self._draw)\n",
            ),
        ),
    ),
    "producer_scan_body": (
        PRODUCER,
        "FireDispatcher._scan",
        (
            ("class FireDispatcher:\n", f"{_SIZES}class FireDispatcher:\n"),
            (
                "        return tuple(found)\n",
                "        return tuple(_SIZES.get(issue, issue) for issue in found)\n",
            ),
        ),
    ),
    "domain_order_body": (
        DOMAIN_ORDER,
        "priority_rank",
        (
            (
                "def priority_rank(priority: IssuePriority) -> int:\n",
                f"{_SIZES}def priority_rank(priority: IssuePriority) -> int:\n",
            ),
            (
                "    return PRIORITY_RANK_ORDER.index(priority)\n",
                "    return PRIORITY_RANK_ORDER.index(priority) + "
                "_SIZES.get(priority, 0)\n",
            ),
        ),
    ),
    "domain_order_table": (
        DOMAIN_ORDER,
        "priority_rank",
        (
            (
                "    IssuePriority.URGENT,\n    IssuePriority.HIGH,\n",
                "    IssuePriority.HIGH,\n    IssuePriority.URGENT,\n",
            ),
        ),
    ),
}

#: The five that leave every body in the module as written.
RESOLVED_ELSEWHERE = frozenset(
    {
        "decorated_rank_key",
        "rebound_rank_key",
        "globals_write",
        "setattr_on_the_module",
        "module_def_of_the_imported_name",
    }
)

#: A module that reaches the function making a rank under each ordinary
#: spelling, each in its own definition taking rows, and one definition that
#: reaches it under none.
CALLERS = """\
import functools
import importlib

import kodezart.domain.dispatch as ranking
from kodezart.domain.dispatch import rank_key as key_of
from kodezart.types.domain.tracker import TrackerIssue

_ALIAS = key_of
_PARTIAL = functools.partial(key_of)


class Ranker:
    def key(self, issue: TrackerIssue):
        return key_of(issue)


_BOUND = Ranker().key


def by_import_alias(rows):
    return sorted(rows, key=key_of)


def by_module_attribute(rows):
    return sorted(rows, key=ranking.rank_key)


def by_import_inside(rows):
    from kodezart.domain.dispatch import rank_key

    return sorted(rows, key=rank_key)


def by_module_alias(rows):
    return sorted(rows, key=_ALIAS)


def by_partial(rows):
    return sorted(rows, key=_PARTIAL)


def by_conditional(rows):
    return sorted(rows, key=key_of if rows else len)


def by_getattr_literal(rows):
    return sorted(rows, key=getattr(ranking, "rank_key"))


def by_import_module_literal(rows):
    return sorted(
        rows, key=importlib.import_module("kodezart.domain.dispatch").rank_key
    )


def by_bound_method(rows):
    return sorted(rows, key=_BOUND)


def by_no_route(rows):
    return sorted(rows)
"""


def test_the_rank_key_is_priority_then_age_and_nothing_else():
    """The rank is a priority and an age, made from two reads and two calls.

    Pinned by text as well, the way the comparison is: the reads are counted
    as attributes off ``issue`` and the calls by the word they spell, so a
    read that is neither — a subscript of the bare ``issue`` name into a
    module-level table, filled from a count by whoever selects — adds a
    derived quantity to the rank inside the one function that makes one and
    moves no other register here. "Nothing else" is a claim about the whole
    expression, so the whole expression is written out.

    It is a claim about the whole function too, because a statement before the
    return leaves that expression byte-identical while substituting the issue
    it reads or the function it calls. So the body after the docstring is
    written out as well, leaving room for no statement at all, and no binding
    statement of the module rebinds a name it imports from the domain types.
    Both read the written module. What ``priority_rank`` resolves to when the
    function runs is pinned by object in the dispatch path test.
    """
    tree = PARSED[DISPATCH]
    comparison = _defined(tree, COMPARISON)
    made = _defined(tree, rank_key.__name__)

    assert [field.name for field in dataclasses.fields(RankKey)] == [
        "priority_rank",
        "created_at",
    ]
    assert declared_fields(tree, owner=RankKey.__name__) == (
        "priority_rank",
        "created_at",
    )
    assert ast.unparse(next(iter(comparison.body)).value) == (
        "(self.priority_rank, self.created_at) < "
        "(other.priority_rank, other.created_at)"
    )
    assert receiver_reads(made, receiver="issue") == frozenset(
        {"priority", "created_at"}
    )
    assert callees(made) == frozenset({RankKey.__name__, priority_rank.__name__})
    assert ast.unparse(returned(made)) == RANK_KEY_TEXT
    assert body_text(made) == RANK_KEY_BODY
    assert priority_rank.__name__ in imported_domain_names(tree)
    assert bound_names(tree) & imported_domain_names(tree) == frozenset()


def test_every_ordering_that_consults_priority_reads_only_the_rank_inputs():
    """The whole package's priority orderings, compared to the register.

    The planted module is the library route the four builtins leave open: a
    ``heapq`` selection whose key orders by the rank and by a relation count.
    Planted into the scanned tree it becomes a sixth entry reading a name
    outside the rank inputs, so the exact register reds.
    """
    sites = ordering_sites(PARSED)
    calls = ordering_calls(PARSED)
    planted = ast.parse(
        "import heapq\n"
        "\n"
        f"from kodezart.domain.dispatch import {rank_key.__name__}\n"
        "\n"
        "def pick(rows):\n"
        "    return heapq.nsmallest(\n"
        f"        rows, 1, key=lambda r: ({rank_key.__name__}(r), len(r.relations))\n"
        "    )\n"
    )
    with_planted = ordering_sites({**PARSED, "planted.py": planted})

    assert sites == DISPATCH_ORDERINGS
    for _key, (_count, reads) in sites.items():
        assert reads <= RANK_INPUTS
    for _key, named in calls.items():
        assert named <= RANK_CALLS
    assert "relations" not in RANK_INPUTS
    assert with_planted["planted.py::pick"] == (1, frozenset({"relations"}))
    assert with_planted != DISPATCH_ORDERINGS


def test_every_registered_orderings_key_text_is_pinned_exactly():
    """Both registers cover the same sites, and the text one has its control.

    The attribute register cannot see a read that is no attribute, so a size
    table keyed by issue key and subscripted inside ``ranked_order``'s sort key
    leaves every row of it unchanged — same count, same attribute names, same
    called names — while the unparsed text of that key moves. That read is
    what this pin exists for.
    """
    assert ordering_key_texts(PARSED) == DISPATCH_ORDERING_KEYS
    assert set(DISPATCH_ORDERING_KEYS) == set(DISPATCH_ORDERINGS)

    key_anchor = "                rank_key(issue).created_at,\n"
    key_sized = key_anchor + "                _SIZES[issue.issue_key],\n"
    table_anchor = f"def {dispatch.ranked_order.__name__}("
    table_sized = (
        f"_SIZES: dict[str, int] = {{}}\n\n\ndef {dispatch.ranked_order.__name__}("
    )
    source = PACKAGE[DISPATCH]
    assert source.count(key_anchor) == 1
    assert source.count(table_anchor) == 1
    sized = {
        **PARSED,
        DISPATCH: ast.parse(
            source.replace(key_anchor, key_sized).replace(table_anchor, table_sized)
        ),
    }

    assert ordering_sites(sized) == DISPATCH_ORDERINGS
    assert ordering_calls(sized) == ordering_calls(PARSED)
    assert ordering_key_texts(sized) != DISPATCH_ORDERING_KEYS


def test_an_ordering_that_consults_the_rank_under_another_spelling_is_registered():
    """Discovery keys on what a spelling denotes, not on the word written.

    The planted module's only rank spelling is the alias, and the attributes
    its key reads are outside ``RANK_NAMES``, so nothing but the resolved
    import can put the ordering on the register. With the alias left unused the
    same ordering is no site at all.
    """
    consulting = ast.parse(
        f"from kodezart.domain.dispatch import {RankKey.__name__} as Key\n"
        "\n"
        "def pick(rows):\n"
        "    return sorted(rows, key=lambda row: Key(row.level, row.at))\n"
    )
    unused = ast.parse(
        f"from kodezart.domain.dispatch import {RankKey.__name__} as Key\n"
        "\n"
        "def pick(rows):\n"
        "    return sorted(rows, key=lambda row: (row.level, row.at))\n"
    )

    assert not {"level", "at"} & RANK_NAMES
    assert ordering_sites({"planted.py": consulting}) == {
        "planted.py::pick": (1, frozenset({"level", "at"}))
    }
    assert ordering_sites({"planted.py": unused}) == {}


def test_no_row_the_rank_reads_carries_a_numeric_or_estimate_like_field():
    """A size has to be declared somewhere; none of the rows declares one."""
    assert set(IssueSnapshot.model_fields) == {
        "issue_key",
        "priority",
        "state_name",
        "created_at",
    }
    for model in (IssueSnapshot, TrackerIssue):
        assert (
            numeric_fields(
                {name: field.annotation for name, field in model.model_fields.items()}
            )
            == []
        )
        assert [name for name in model.model_fields if estimate_words(name)] == []
    for owner in (ReadyIssue, ScopeReadyLane):
        assert (
            numeric_fields(
                {field.name: field.type for field in dataclasses.fields(owner)}
            )
            == []
        )
        assert [
            field.name
            for field in dataclasses.fields(owner)
            if estimate_words(field.name)
        ] == []
    assert numeric_fields({"weight": int | None, "at": datetime}) == ["weight"]
    assert text_fields(
        {
            "body": str,
            "project": str | None,
            "title": Annotated[str, "titled"],
            "issue_labels": frozenset[str],
            "either": str | int,
            "at": datetime,
        }
    ) == frozenset({"body", "project", "title"})


def test_no_length_of_an_issue_text_column_is_taken_anywhere_in_the_package():
    """No length of an issue's text is taken, so none can reach a rank."""
    columns = text_columns()
    taken = {
        module: reads & columns
        for module, tree in PARSED.items()
        if (reads := text_length_reads(tree)) & columns
    }

    assert columns
    assert taken == {}
    assert (
        text_length_reads(ast.parse(inspect.getsource(fire_plateau))) & columns
        == frozenset()
    )
    assert (
        text_length_reads(ast.parse(inspect.getsource(dispatch))) & columns
        == frozenset()
    )
    assert (
        text_length_reads(ast.parse("def f(ticks):\n    return len(ticks)\n"))
        == frozenset()
    )
    assert text_length_reads(
        ast.parse("def f(issue):\n    return len(issue.body)\n")
    ) == frozenset({"body"})


def test_no_estimate_vocabulary_is_spelled_anywhere_in_the_package():
    """The outer net: no identifier in the package spells an estimate word."""
    spelled = {
        module: named
        for module, tree in PARSED.items()
        if (named := estimate_identifiers(tree))
    }

    assert spelled == {}


@pytest.mark.parametrize("word", sorted(ESTIMATE_VOCABULARY))
def test_every_estimate_word_is_controlled(word):
    """Each word in the vocabulary reddens the net on its own."""
    control = ast.parse(f"def rank(issue):\n    return issue.{word}_days\n")
    clean = ast.parse("def rank(issue):\n    return issue.priority\n")

    assert estimate_identifiers(control) == frozenset({f"{word}_days"})
    assert estimate_identifiers(clean) == frozenset()


MUTANTS = {
    "rank_key_field": (
        "    priority_rank: int\n    created_at: datetime\n",
        "    priority_rank: int\n    created_at: datetime\n    estimate: int\n",
    ),
    "rank_key_call": (
        "        created_at=issue.created_at,\n",
        "        created_at=issue.created_at,\n        estimate=len(issue.body),\n",
    ),
    "ranked_order_key": (
        "                rank_key(issue).created_at,\n",
        "                rank_key(issue).created_at,\n"
        "                rank_key(issue).estimate,\n",
    ),
    "rank_key_size_table": (
        "def rank_key(issue: TrackerIssue) -> RankKey:\n"
        '    """Primary rank (Urgent first, None last), secondary oldest-first."""\n'
        "    return RankKey(\n"
        "        priority_rank=priority_rank(issue.priority),\n",
        "_SIZES: dict = {}\n"
        "\n"
        "\n"
        "def rank_key(issue: TrackerIssue) -> RankKey:\n"
        '    """Primary rank (Urgent first, None last), secondary oldest-first."""\n'
        "    return RankKey(\n"
        "        priority_rank=priority_rank(issue.priority)\n"
        "        + (_SIZES[issue] if issue in _SIZES else 0),\n",
    ),
    "rank_key_substituted_issue": (
        "def rank_key(issue: TrackerIssue) -> RankKey:\n"
        '    """Primary rank (Urgent first, None last), secondary oldest-first."""\n'
        "    return RankKey(\n",
        "_TABLE: dict = {}\n"
        "\n"
        "\n"
        "def rank_key(issue: TrackerIssue) -> RankKey:\n"
        '    """Primary rank (Urgent first, None last), secondary oldest-first."""\n'
        "    issue = _TABLE[issue] if issue in _TABLE else issue\n"
        "    return RankKey(\n",
    ),
    "rank_key_shadowed_order": (
        "def rank_key(issue: TrackerIssue) -> RankKey:\n"
        '    """Primary rank (Urgent first, None last), secondary oldest-first."""\n'
        "    return RankKey(\n",
        "_TABLE: dict = {}\n"
        "_ORDER = priority_rank\n"
        "\n"
        "\n"
        "def rank_key(issue: TrackerIssue) -> RankKey:\n"
        '    """Primary rank (Urgent first, None last), secondary oldest-first."""\n'
        "    priority_rank = _TABLE[issue] if issue in _TABLE else _ORDER\n"
        "    return RankKey(\n",
    ),
}


@pytest.mark.parametrize("hunk", sorted(MUTANTS))
def test_the_guard_reddens_on_a_size_derived_rank_input(hunk):
    """The mutations that survived, planted one hunk at a time.

    Each hunk is caught by the pin that covers the place it touched: the
    declared rank fields, the reads and calls of the one function that makes a
    rank, the register of what every ordering reads, and the text of the
    expression that makes the rank. The first three spell a word of the
    vocabulary and take the length of a text column, so the outer nets red on
    them too — though the shape pins would have caught all three without
    either.

    The size table is the hunk that proves the nets are not what does the
    work. It spells no vocabulary word, takes no length, reads no new
    attribute off ``issue``, calls nothing new and leaves every ordering's
    register — count, attribute names, called names and written text — exactly
    as it was, because it touches neither an ordering nor a field but the
    arithmetic between them. Each of those is asserted here as an equality,
    not skipped: what reds it is the text of the rank-making expression and
    nothing else in this module.

    The last two move the same table one statement earlier, where the returned
    expression stays byte-identical and the text of it is satisfied too. One
    substitutes the issue whose fields the return then reads; the other rebinds
    the name the return calls, so the call still spells ``priority_rank`` while
    meaning a per-issue function a producer injects. Every register the size
    table leaves alone these leave alone as well, the text of the return among
    them, and each is asserted here as an equality: what reds the first is the
    body of the rank-making function, and the second reds that and the
    disjointness of what this module binds from what it imports.
    """
    anchor, planted = MUTANTS[hunk]
    source = PACKAGE[DISPATCH]
    assert source.count(anchor) == 1
    mutated = ast.parse(source.replace(anchor, planted))

    if hunk == "rank_key_field":
        assert declared_fields(mutated, owner=RankKey.__name__) != (
            "priority_rank",
            "created_at",
        )
        assert estimate_identifiers(mutated)
    elif hunk == "rank_key_call":
        made = _defined(mutated, rank_key.__name__)
        assert receiver_reads(made, receiver="issue") == frozenset(
            {"priority", "created_at", "body"}
        )
        assert callees(made) == frozenset(
            {RankKey.__name__, priority_rank.__name__, len.__name__}
        )
        assert text_length_reads(mutated) == frozenset({"body"})
        assert estimate_identifiers(mutated)
    elif hunk == "ranked_order_key":
        sites = ordering_sites({**PARSED, DISPATCH: mutated})
        assert sites != DISPATCH_ORDERINGS
        assert "estimate" in sites[f"{DISPATCH}::ranked_order"][1]
        assert estimate_identifiers(mutated)
    elif hunk == "rank_key_size_table":
        made = _defined(mutated, rank_key.__name__)
        with_table = {**PARSED, DISPATCH: mutated}
        assert receiver_reads(made, receiver="issue") == frozenset(
            {"priority", "created_at"}
        )
        assert callees(made) == frozenset({RankKey.__name__, priority_rank.__name__})
        assert ordering_sites(with_table) == DISPATCH_ORDERINGS
        assert ordering_calls(with_table) == ordering_calls(PARSED)
        assert ordering_key_texts(with_table) == DISPATCH_ORDERING_KEYS
        assert text_length_reads(mutated) == text_length_reads(PARSED[DISPATCH])
        assert estimate_identifiers(mutated) == frozenset()
        assert ast.unparse(returned(made)) != RANK_KEY_TEXT
    elif hunk == "rank_key_substituted_issue":
        made = _defined(mutated, rank_key.__name__)
        with_table = {**PARSED, DISPATCH: mutated}
        assert receiver_reads(made, receiver="issue") == frozenset(
            {"priority", "created_at"}
        )
        assert callees(made) == frozenset({RankKey.__name__, priority_rank.__name__})
        assert ast.unparse(returned(made)) == RANK_KEY_TEXT
        assert ordering_sites(with_table) == DISPATCH_ORDERINGS
        assert ordering_calls(with_table) == ordering_calls(PARSED)
        assert ordering_key_texts(with_table) == DISPATCH_ORDERING_KEYS
        assert text_length_reads(mutated) == text_length_reads(PARSED[DISPATCH])
        assert estimate_identifiers(mutated) == frozenset()
        assert bound_names(mutated) & imported_domain_names(mutated) == frozenset()
        assert body_text(made) != RANK_KEY_BODY
    elif hunk == "rank_key_shadowed_order":
        made = _defined(mutated, rank_key.__name__)
        with_table = {**PARSED, DISPATCH: mutated}
        assert receiver_reads(made, receiver="issue") == frozenset(
            {"priority", "created_at"}
        )
        assert callees(made) == frozenset({RankKey.__name__, priority_rank.__name__})
        assert ast.unparse(returned(made)) == RANK_KEY_TEXT
        assert ordering_sites(with_table) == DISPATCH_ORDERINGS
        assert ordering_calls(with_table) == ordering_calls(PARSED)
        assert ordering_key_texts(with_table) == DISPATCH_ORDERING_KEYS
        assert text_length_reads(mutated) == text_length_reads(PARSED[DISPATCH])
        assert estimate_identifiers(mutated) == frozenset()
        assert body_text(made) != RANK_KEY_BODY
        assert priority_rank.__name__ in bound_names(mutated)
        assert priority_rank.__name__ in imported_domain_names(mutated)
    else:
        raise AssertionError(f"{hunk} is planted with no control asserted")


def test_the_plateau_bound_is_a_tick_count_and_not_a_rank_input():
    """The shipped barren bound is a tick count, not a size input.

    ``fire_plateaued`` compares how many ticks a lane has taken with the
    per-tick budget the lane deliverable allows. It orders nothing by
    priority, it takes no length of a field, and the pins above are written
    narrowly enough that they never reach it.
    """
    modules = {key.split("::")[0] for key in ordering_sites(PARSED)}

    assert PLATEAU not in modules
    assert text_length_reads(PARSED[PLATEAU]) & text_columns() == frozenset()
    assert estimate_identifiers(PARSED[PLATEAU]) == frozenset()


def _module_name(relative: str) -> str:
    """The dotted module a path of the source tree is imported as."""
    return ".".join((SOURCE_ROOT.name, *relative.removesuffix(".py").split("/")))


def _reach(owner: object, qualname: str) -> types.FunctionType:
    """The function *qualname* names inside *owner*, attribute by attribute."""
    return functools.reduce(getattr, qualname.split("."), owner)


def test_every_definition_on_the_dispatch_path_runs_exactly_what_is_registered():
    """What reaches a rank, pinned by what executes rather than what is spelled.

    The path is derived from the live function that makes a rank, so no list
    of names is kept by hand: whatever reaches it — the orderings that call
    it, the producer's pass that hands them its rows, the scan those rows
    come from, and everything each of those calls in the package — is on it.
    Each definition is pinned by the ``def`` its live code was compiled
    from: its decorators, its whole body after the docstring, and the home
    of every object its reads resolve to. So a size table cannot be put
    before any of these ranks under any spelling that runs: in a body it
    moves the body, and anywhere else — a decorator, a rebinding, a write to
    the module's globals, a ``def`` of the same word — it moves what a name
    resolves to. The derived path is compared with the register by
    equality, so a walk rule undone shrinks it and reds here.

    Limits, stated. Rows are made by the tracker adapter behind the port the
    scan calls, and that port is not walked. A supplier reached through a
    collaborator attribute (``self._tracker``), a method replaced on the
    instance, and a function reached through a container or an instance are
    not followed. A filter reads rows without changing them, since the rows
    are frozen models, and the eligibility clauses are outside the walk. The
    injected tie-break draw is not walked. A method a class decorator or a
    metaclass generates is compiled from no file of the tree and is not on
    the path; the rank's own fields are pinned above. A name built at run
    time for ``getattr``, ``importlib`` or ``__dict__``, and ``eval`` or
    ``exec``, are deliberate evasion and out of scope.
    """
    functions = package_functions()
    assert functions
    path = dispatch_path(rank_key, functions=functions)
    reached = {home(function): executed(function) for function in path}

    assert row_type(rank_key) is TrackerIssue
    assert len(reached) == len(path)
    assert reached == DISPATCH_PATH


def test_a_size_table_anywhere_on_the_path_moves_what_it_runs(tmp_path):
    """The control for the path pin, over planted live modules.

    Each case is planted into the shipped source and the module is run, so
    the definition read is the live object the planted code makes, through
    the same function the pin reads. The first five leave the whole written
    body of the function that makes a rank as the earlier pins require it,
    and leave the module's bindings disjoint from its domain imports —
    asserted here, so this shows it is the resolution that catches them.
    """
    assert SIZE_TABLES
    assert RESOLVED_ELSEWHERE <= set(SIZE_TABLES)
    for case, (relative, qualname, hunks) in SIZE_TABLES.items():
        source = PACKAGE[relative]
        for anchor, planted in hunks:
            assert source.count(anchor) == 1, case
            source = source.replace(anchor, planted)
        directory = tmp_path / case
        directory.mkdir()
        name = _module_name(relative)
        shipped = _reach(sys.modules[name], qualname)
        live = _reach(planted_module(source, name=name, directory=directory), qualname)

        assert executed(shipped) == DISPATCH_PATH[home(shipped)], case
        assert executed(live) != DISPATCH_PATH[home(shipped)], case
        if case in RESOLVED_ELSEWHERE:
            tree = ast.parse(source)
            assert body_text(_defined(tree, rank_key.__name__)) == RANK_KEY_BODY
            assert bound_names(tree) & imported_domain_names(tree) == frozenset()


def test_the_walk_finds_a_caller_of_the_rank_under_every_spelling(tmp_path):
    """The control for the walk's climb and forward rules.

    The planted module reaches the function that makes a rank under each
    ordinary spelling — an aliased import, a module attribute, an import
    inside the function, a module-level alias, a ``functools.partial``, a
    conditional expression, ``getattr`` and ``importlib`` with a literal,
    and a bound method one definition further out — and each reaching
    definition is on the path. The one that reaches it under none is not,
    so the walk is not everything. Over no package function at all, the
    forward rule alone still reaches the domain order and the comparison.
    """
    module = planted_module(CALLERS, name="planted_callers", directory=tmp_path)
    callers = tuple(
        value
        for value in vars(module).values()
        if isinstance(value, types.FunctionType) and value.__module__ == module.__name__
    )
    spellings = {caller.__name__ for caller in callers} - {"by_no_route"}
    path = {
        home(function)
        for function in dispatch_path(rank_key, functions=(*callers, module.Ranker.key))
    }
    forward = {home(function) for function in dispatch_path(rank_key, functions=())}

    assert len(spellings) == 9
    for spelling in spellings:
        assert f"planted_callers.{spelling}" in path, spelling
    assert "planted_callers.Ranker.key" in path
    assert "planted_callers.by_no_route" not in path
    assert forward == {
        "kodezart.domain.dispatch.rank_key",
        "kodezart.domain.dispatch.RankKey.__lt__",
        "kodezart.types.domain.tracker.priority_rank",
    }
