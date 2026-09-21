"""Every ordering that consults priority reads the rank inputs alone (KOD-731).

The guard is the shape of the rank, not a word list. Over the whole package it
finds every ordering expression — a ``sorted``, ``.sort``, ``min`` or ``max``
call, and every ``__lt__`` — whose key or arguments reach a rank name, and
compares the sites it found, how many each definition holds and every
attribute name read inside them to an exact register. A size fed into a rank
has to be read off something, so an ordering that reads a name outside the
rank inputs reds even when it spells no suspicious word: that is the mutation
this guard exists for, ``estimate=len(issue.body)`` on the rank key and in
both sort keys, which used no forbidden token at all.

Two weaker nets sit outside the shape pins. One collects every ``len(...)``
whose argument is an attribute and asserts none of those attributes is a text
column of the two row types the rank computes over. The other reads every
identifier in the package and asserts none carries an estimate word.

The wired rank path is the unscoped producer's selection over
``domain/dispatch.py``; the scope dispatcher has no production caller, so it
is not where a rank input would arrive. The register is over the whole
package, so the question of which dispatch does not arise: an ordering that
consults priority anywhere is registered.

Blind spots, stated: a name assembled from parts rather than joined by
underscores is not matched; a size used as a filter rather than as an
ordering, or an ordering that reads no priority name in a definition the
register does not count, is outside every pin here — the register catches a
size only once it enters an ordering that also consults priority, which is the
mutation this guard exists for; a length taken of a local name rather than of
a field is not a text-length read, and a length taken of a field that is not a
text column — a count of relations, the size of a label set — is outside the
text-length net; strings and comments are not scanned. The
words ``effort``, ``remaining`` and ``size`` are deliberately absent from the
vocabulary because the package uses them for a session effort setting, for
iteration and round counters, and for page sizes.

``fire_plateaued`` compares ``len(ticks)`` with the plateau bound. The bound
counts ticks, which is the per-tick budget the lane deliverable allows, and it
is not a size, an estimate or a forecast of remaining work. The pins are
written narrowly enough never to reach it and ``len()`` is forbidden nowhere;
a control asserts the plateau module is reported by neither the register nor
the text-length net.
"""

import ast
import dataclasses
import inspect
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import get_args

import pytest

from kodezart.domain import dispatch, fire_plateau
from kodezart.domain.dispatch import RankKey, rank_key
from kodezart.types.domain.dispatch import IssueSnapshot
from kodezart.types.domain.scope_ready import ScopeReadyLane
from kodezart.types.domain.topology import ReadyIssue
from kodezart.types.domain.tracker import TrackerIssue, priority_rank
from tests.name_resolution import definitions, parsed, resolve, source_tree

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
ORDERING_CALLS = frozenset({"sorted", "sort", "min", "max"})
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


def ordering_sites(trees) -> dict[str, tuple[int, frozenset[str]]]:
    """Every ordering in the package that consults priority, by definition.

    Keyed ``module::dotted definition``; valued by how many such expressions
    the definition holds and every attribute name read inside their keys and
    arguments. An ordering consults priority when its key or arguments reach
    a rank name under any spelling.
    """
    found: dict[str, list[frozenset[str]]] = {}
    for module, tree in sorted(trees.items()):
        resolution = resolve(tree, names=RANK_NAMES)
        where = definitions(tree)
        for node in ast.walk(tree):
            regions = _ordering_regions(node)
            if regions is None or not _consults_rank(regions, resolution):
                continue
            key = f"{module}::{where.get(id(node), '<module>')}"
            found.setdefault(key, []).append(attribute_reads(regions))
    return {
        key: (len(reads), frozenset().union(*reads)) for key, reads in found.items()
    }


def ordering_calls(trees) -> dict[str, frozenset[str]]:
    """Every name the registered orderings call, by the same key."""
    found: dict[str, set[str]] = {}
    for module, tree in sorted(trees.items()):
        resolution = resolve(tree, names=RANK_NAMES)
        where = definitions(tree)
        for node in ast.walk(tree):
            regions = _ordering_regions(node)
            if regions is None or not _consults_rank(regions, resolution):
                continue
            key = f"{module}::{where.get(id(node), '<module>')}"
            found.setdefault(key, set()).update(called_names(regions))
    return {key: frozenset(names) for key, names in found.items()}


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


def text_columns() -> frozenset[str]:
    """The text fields of the two row types a rank computes over."""
    return frozenset(
        name
        for model in (IssueSnapshot, TrackerIssue)
        for name, field in model.model_fields.items()
        if field.annotation is str
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


def callees(function: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    """Every name a definition calls, plain or as an attribute."""
    return frozenset(
        spelled
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and (spelled := _spells(node.func)) is not None
    )


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


def test_the_rank_key_is_priority_then_age_and_nothing_else():
    """The rank is a priority and an age, made from two reads and two calls."""
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


def test_every_ordering_that_consults_priority_reads_only_the_rank_inputs():
    """The whole package's priority orderings, compared to the register."""
    sites = ordering_sites(PARSED)
    calls = ordering_calls(PARSED)

    assert sites == DISPATCH_ORDERINGS
    for _key, (_count, reads) in sites.items():
        assert reads <= RANK_INPUTS
    for _key, named in calls.items():
        assert named <= RANK_CALLS


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
}


@pytest.mark.parametrize("hunk", sorted(MUTANTS))
def test_the_guard_reddens_on_a_size_derived_rank_input(hunk):
    """The mutation that survived, planted one hunk at a time.

    Each hunk is caught by the pin that covers the place it touched: the
    declared rank fields, the reads and calls of the one function that makes a
    rank, and the register of what every ordering reads. The length of a text
    column reds the outer net as well, and the word reds the vocabulary net —
    but the shape pins would have caught all three without either.
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
    elif hunk == "rank_key_call":
        made = _defined(mutated, rank_key.__name__)
        assert receiver_reads(made, receiver="issue") == frozenset(
            {"priority", "created_at", "body"}
        )
        assert callees(made) == frozenset(
            {RankKey.__name__, priority_rank.__name__, len.__name__}
        )
        assert text_length_reads(mutated) == frozenset({"body"})
    else:
        sites = ordering_sites({**PARSED, DISPATCH: mutated})
        assert sites != DISPATCH_ORDERINGS
        assert "estimate" in sites[f"{DISPATCH}::ranked_order"][1]

    assert estimate_identifiers(mutated)


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
