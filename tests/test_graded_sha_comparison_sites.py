"""One function weighs a graded sha against a head sha (KOD-413, KOD-696, KOD-596).

Two readers weighing the same two revisions is how a lapse comes to mean one
thing on a compliance mark and another on a lane check: one of them
eventually grows an ancestry test, a prefix match or a null case, and
nothing red says so.  The rule therefore lives in exactly one function, and
this guard keeps it there.

It asks two questions of every scope in the package, and neither of them
licenses a body:

* **Which scopes hold both revisions?**  Exactly the rule and the scopes the
  register names, each with the reason it holds both.  A new co-holder is
  red, and so is a row for a scope that no longer holds both.
* **Where, inside any scope, do the two revisions meet?**  A meeting is one
  expression -- or one ``match`` case -- in which one operand carries the
  graded revision and a DIFFERENT operand carries the head.  Each registered
  scope's meetings are pinned verbatim in its row, as a multiset, so a
  comparison added inside a registered scope is a meeting nobody pinned and
  is red there too.  The rule is the one scope whose meetings are not
  pinned: weighing the pair is its whole job.

A meeting is found without knowing what a comparison looks like.  An
operator, a comparison dunder, the ``operator`` module, a prefix match, an
ancestry helper, a port handed both ends of an interval, a container, an
f-string and a pattern match are one test: two distinct operands, one from
each side.  So there is no list of spellings here for the next one to walk
past.

What each side IS is read off the shipped code.  The graded revision is the
one field the Evidence record and the rule both spell; the head is the
rule's other revision parameter and every record field ending in that
spelling; both carry their serialisation aliases.  Which records carry a
side is read off every model's ``model_fields``, to a fixed point, so a
record holding a record that carries the graded revision carries it too.

A revision is followed wherever ordinary Python takes it inside the
package: an attribute, a name, a key spelled at the read (a subscript, a
call argument, a mapping key, a mapping pattern), every kind of binding to a
fixed point (assignment, unpack, loop and comprehension targets, ``with``,
walrus, ``match`` captures), a keyword argument spelled as a revision (which
also marks the name handed under it), a mutating call on a local, a closure,
a value a body of the same module returns or parks on an attribute, a
module-level function imported by name from another module, a port method
named for a revision, a record used whole (iterated, dumped, compared, or
read through anything that is not one of its declared fields), and the body
of a condition whose test spells a side.

The check's stated limits -- what it does not claim to see:

* **deliberate evasion**: ``getattr`` with a name composed at run time,
  ``eval`` or ``exec``, ``__dict__`` read by a computed key, a spelling kept
  in a variable rather than written where it is read;
* **a head nobody names**: a head is a value the shipped code names as one --
  the rule's parameter, a record's head field, a value handed on under
  either, a port method named for a revision.  A revision read under a
  neutral name and never recorded as a head is, to a static reading, any
  other string;
* **flow through a method of another module**: a method is reached through
  an object whose type a static reading does not know, so a value handed
  back by one is followed only by the record fields it arrives under.

Provenance readings that weigh one RECORDED revision against another -- the
restamp history, the restamped verdict's history check, the forge
observation's exact commit -- hold no head, so they are outside the census by
construction rather than by a row.
"""

import ast
import json
import sys
import typing
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from functools import cache
from importlib import import_module
from inspect import signature
from pathlib import Path
from pkgutil import walk_packages
from typing import NamedTuple

import pytest
from pydantic import BaseModel

from kodezart.domain.lapse import graded_state
from kodezart.types.domain.criterion_evidence import CriterionEvidence

#: The package the rule is packaged in, and so the tree it speaks for.
SOURCE = Path(sys.modules[graded_state.__module__].__file__ or "").resolve().parents[1]
PACKAGE = graded_state.__module__.partition(".")[0]
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
#: Evidence record and the rule both spell.
GRADED_IDENTITIES = frozenset(CriterionEvidence.model_fields) & frozenset(
    signature(graded_state).parameters
)
GRADED = min(GRADED_IDENTITIES, default="")
#: The segment a revision-shaped name ends with, read off that identity.
REVISION = GRADED.rpartition("_")[2]
#: The rule's other revision: its parameter besides the graded identity whose
#: name ends in the same segment.
HEADS = frozenset(
    parameter
    for parameter in signature(graded_state).parameters
    if parameter not in GRADED_IDENTITIES and parameter.rpartition("_")[2] == REVISION
)
HEAD = min(HEADS, default="")
#: The package the records live in, read off the record the rule is about.
RECORDS = CriterionEvidence.__module__.partition(".domain.")[0]

GRADED_TAG = "graded"
HEAD_TAG = "head"
BOTH = frozenset({GRADED_TAG, HEAD_TAG})

Sides = frozenset[str]
NONE: Sides = frozenset()


def _models() -> tuple[type[BaseModel], ...]:
    """Every record the types package declares, once each, in a stable order."""
    found: dict[str, type[BaseModel]] = {}
    package = import_module(RECORDS)
    for module in walk_packages(package.__path__, f"{package.__name__}."):
        for member in vars(import_module(module.name)).values():
            if isinstance(member, type) and issubclass(member, BaseModel):
                found[f"{member.__module__}.{member.__qualname__}"] = member
    return tuple(found[key] for key in sorted(found))


MODELS = _models()


def _referenced(annotation: object) -> frozenset[type]:
    """Every class an annotation names, through unions and containers.

    Each argument is visited once, so the walk is bounded by the size of the
    annotation.
    """
    seen: set[int] = set()
    found: set[type] = set()
    stack: list[object] = [annotation]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, type):
            found.add(current)
        stack.extend(typing.get_args(current))
    return frozenset(found)


def _spellings(names: Callable[[str], bool]) -> frozenset[str]:
    """Every field name *names* accepts, with the alias it serialises as."""
    return frozenset(
        spelling
        for model in MODELS
        for name, info in model.model_fields.items()
        if names(name)
        for spelling in (name, info.alias)
        if spelling
    )


GRADED_SPELLINGS = GRADED_IDENTITIES | _spellings(GRADED_IDENTITIES.__contains__)
#: The head: the rule's own spelling of it, or a record field ending in that
#: spelling -- a lane's pushed head, a loop context's resumed head.
HEAD_SPELLINGS = (
    HEADS
    | _spellings(
        lambda name: (
            name not in GRADED_IDENTITIES
            and (name == HEAD or name.endswith(f"_{HEAD}"))
        )
    )
) - GRADED_SPELLINGS
SPELLINGS = {GRADED_TAG: GRADED_SPELLINGS, HEAD_TAG: HEAD_SPELLINGS}


def _spelled(name: object) -> Sides:
    """The side a name, attribute or key is spelled as, if any.

    A name the records do not declare is still a head when it is qualified
    the way the records qualify one -- a remote head, a latest head -- so a
    local reader's own head is read as one.
    """
    if not isinstance(name, str):
        return NONE
    qualified = name.endswith(f"_{HEAD}") and name not in GRADED_SPELLINGS
    return frozenset(
        tag
        for tag, spelled in SPELLINGS.items()
        if name in spelled or (qualified and tag == HEAD_TAG)
    )


def _carried() -> dict[str, Sides]:
    """The sides each record carries, grown to a fixed point over its fields.

    A record carries a side when it declares a field spelled as that side, or
    a field whose annotation names a record that carries it.  Each pass can
    only add sides, so the loop is bounded by the number of records.
    """
    carried = {
        model.__name__: NONE.union(*map(_spelled, model.model_fields))
        for model in MODELS
    }
    for _ in range(len(MODELS) + 1):
        grown = {
            model.__name__: carried[model.__name__].union(
                *(
                    carried.get(referenced.__name__, NONE)
                    for info in model.model_fields.values()
                    for referenced in _referenced(info.annotation)
                )
            )
            for model in MODELS
        }
        if grown == carried:
            break
        carried = grown
    return {name: sides for name, sides in carried.items() if sides}


CARRIERS = _carried()


def _typed(annotation: object) -> frozenset[str]:
    """The carrying records an annotation names."""
    return frozenset(
        referenced.__name__
        for referenced in _referenced(annotation)
        if referenced.__name__ in CARRIERS
    )


def _fields() -> dict[str, dict[str, frozenset[str]]]:
    """For each carrying record, each field and the carrying records it holds."""
    found: dict[str, dict[str, frozenset[str]]] = {}
    for model in MODELS:
        if model.__name__ not in CARRIERS:
            continue
        declared = found.setdefault(model.__name__, {})
        for name, info in model.model_fields.items():
            for spelling in (name, info.alias):
                if spelling:
                    declared[spelling] = declared.get(spelling, frozenset()) | _typed(
                        info.annotation
                    )
    return found


FIELDS = _fields()


def _unambiguous() -> dict[str, frozenset[str]]:
    """Field names every record that declares them types with a carrier.

    Such a name tells the walk what a record holds even when the record it
    is read off is not known; a name some record declares as a plain value
    tells it nothing.
    """
    typed: dict[str, frozenset[str]] = {}
    plain: set[str] = set()
    for model in MODELS:
        for name, info in model.model_fields.items():
            carriers = _typed(info.annotation)
            for spelling in (name, info.alias):
                if not spelling:
                    continue
                if carriers:
                    typed[spelling] = typed.get(spelling, frozenset()) | carriers
                else:
                    plain.add(spelling)
    return {name: held for name, held in typed.items() if name not in plain}


UNAMBIGUOUS = _unambiguous()


class Carry(NamedTuple):
    """What an expression evaluates from: revisions, and records whole."""

    values: Sides = NONE
    records: frozenset[str] = frozenset()

    def join(self, *others: "Carry") -> "Carry":
        return Carry(
            self.values.union(*(other.values for other in others)),
            self.records.union(*(other.records for other in others)),
        )

    @property
    def sides(self) -> Sides:
        """Every side this operand reaches, a record's included."""
        return self.values.union(*(CARRIERS[name] for name in self.records))

    def whole(self) -> "Carry":
        """The same operand used whole, so a record yields what it carries."""
        return Carry(self.sides)


NOTHING = Carry()
#: The scopes the census names.  A lambda or a comprehension is walked with
#: the scope it is written in, so no scope kind is a place to hide.
SCOPES = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
BODIES = (ast.FunctionDef, ast.AsyncFunctionDef)
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
ScopeNode = ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef


def _named(node: ast.AST) -> str | None:
    """The name a callee or a class is reached under, if it has one."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _keyed(node: ast.AST) -> Sides:
    """The side a string constant spells, read where it is used as a key."""
    return (
        _spelled(node.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else NONE
    )


def _tested(node: ast.AST) -> Sides:
    """The sides a condition selects on by spelling one."""
    return NONE.union(*map(_keyed, ast.walk(node)))


def _records(names: Iterable[str | None]) -> frozenset[str]:
    """Those of *names* that are carrying records."""
    return frozenset(name for name in names if name in CARRIERS)


def _label(node: ast.AST) -> str:
    """How a meeting is pinned: verbatim, a pattern marked as the case it is."""
    if isinstance(node, ast.pattern):
        return f"case {ast.unparse(node)}"
    return ast.unparse(node)


@dataclass
class Scope:
    """What one scope holds, where the two sides meet in it, what it returns."""

    label: str
    holds: set[str] = field(default_factory=set)
    meetings: Counter[str] = field(default_factory=Counter)
    returned: Carry = NOTHING


@dataclass
class Flow:
    """What a module knows across its scopes: returns, parked attributes."""

    returns: dict[str, Carry] = field(default_factory=dict)
    parked: dict[str, Carry] = field(default_factory=dict)


def _add(table: dict[str, Carry], name: str, carry: Carry) -> None:
    if carry.sides:
        table[name] = table.get(name, NOTHING).join(carry)


class Walker:
    """One pass over one scope's own nodes, with the names bound in it.

    Names are bound flow-insensitively: a name stands for everything any of
    its bindings in the scope gave it, which reports a meeting too many and
    never one too few.
    """

    def __init__(
        self,
        scope: Scope,
        env: dict[str, Carry],
        flows: tuple[Flow, Flow],
        selves: frozenset[str],
    ) -> None:
        self.scope = scope
        self.env = env
        self.known, self.grown = flows
        self.selves = selves
        self.changed = False
        self.nested: list[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef] = []

    # --- binding ---------------------------------------------------------
    def bind(self, target: ast.AST, carry: Carry) -> None:
        if isinstance(target, ast.Name):
            self.set(target.id, carry.join(Carry(_spelled(target.id))))
        elif isinstance(target, ast.Tuple | ast.List):
            for element in target.elts:
                self.bind(element, carry)
        elif isinstance(target, ast.Starred):
            self.bind(target.value, carry)
        elif isinstance(target, ast.Attribute):
            self.expr(target.value, NONE)
            _add(self.grown.parked, target.attr, carry)
            self.set(ast.unparse(target), carry)
        elif isinstance(target, ast.Subscript):
            self.mutate(target.value, carry.join(self.expr(target.slice, NONE)))

    def set(self, name: str, carry: Carry) -> None:
        before = self.env.get(name, NOTHING)
        after = before.join(carry)
        if after != before:
            self.env[name] = after
            self.changed = True

    def mutate(self, receiver: ast.AST, carry: Carry) -> None:
        """A value put INTO a container is held by that container."""
        while isinstance(receiver, ast.Subscript):
            receiver = receiver.value
        if not carry.sides:
            return
        if isinstance(receiver, ast.Attribute) or (
            isinstance(receiver, ast.Name) and receiver.id not in self.selves
        ):
            self.set(ast.unparse(receiver), carry)

    # --- meeting ---------------------------------------------------------
    def meet(self, node: ast.AST, parts: Iterable[Carry], region: Sides) -> None:
        """Record *node* when two different operands bring one side each."""
        sides = [part.sides for part in parts]
        if region:
            sides.append(region)
        graded = [i for i, held in enumerate(sides) if GRADED_TAG in held]
        head = [i for i, held in enumerate(sides) if HEAD_TAG in held]
        if any(i != j for i in graded for j in head):
            self.scope.meetings[_label(node)] += 1

    # --- expressions -----------------------------------------------------
    def expr(self, node: ast.AST | None, region: Sides) -> Carry:
        if node is None:
            return NOTHING
        carry = self._expr(node, region)
        self.scope.holds.update(carry.values)
        return carry

    def _expr(self, node: ast.AST, region: Sides) -> Carry:
        if isinstance(node, ast.Name):
            return self.env.get(node.id, NOTHING).join(Carry(_spelled(node.id)))
        if isinstance(node, ast.Attribute):
            return self._attribute(node, region)
        if isinstance(node, ast.Await | ast.Starred):
            return self.expr(node.value, region)
        if isinstance(node, ast.NamedExpr):
            carry = self.expr(node.value, region)
            self.bind(node.target, carry)
            return carry
        if isinstance(node, ast.Subscript):
            parts = [self.expr(node.value, region), self._key(node.slice, region)]
            self.meet(node, parts, region)
            return Carry(parts[0].values | parts[1].sides, parts[0].records)
        if isinstance(node, ast.Call):
            return self._call(node, region)
        if isinstance(node, ast.IfExp):
            inner = region | _tested(node.test)
            parts = [
                self.expr(node.test, region),
                self.expr(node.body, inner),
                self.expr(node.orelse, inner),
            ]
            self.meet(node, parts, region)
            return parts[1].join(parts[2], Carry(inner - region))
        if isinstance(node, COMPREHENSIONS):
            return self._comprehension(node, region)
        if isinstance(node, ast.Lambda):
            self._parameters(node.args, region)
            return self.expr(node.body, region)
        return self._operands(node, region)

    def _operands(self, node: ast.AST, region: Sides) -> Carry:
        """Any other expression: its operands, each used whole."""
        keys = node.keys if isinstance(node, ast.Dict) else []
        parts = [self._key(key, region) for key in keys if key is not None]
        parts.extend(
            self.expr(child, region)
            for child in ast.iter_child_nodes(node)
            if isinstance(child, ast.expr) and not any(child is key for key in keys)
        )
        self.meet(node, parts, region)
        return Carry(NONE.union(*(part.sides for part in parts)))

    def _key(self, node: ast.AST, region: Sides) -> Carry:
        return self.expr(node, region).join(Carry(_keyed(node)))

    def _attribute(self, node: ast.Attribute, region: Sides) -> Carry:
        """A field of a known record is that field; anything else is the whole."""
        receiver = self.expr(node.value, region)
        declared = [
            FIELDS[name][node.attr]
            for name in receiver.records
            if node.attr in FIELDS.get(name, {})
        ]
        records = frozenset[str]().union(*declared)
        if not receiver.records:
            records |= UNAMBIGUOUS.get(node.attr, frozenset())
        values = receiver.values | _spelled(node.attr)
        if receiver.records and not declared:
            values |= receiver.sides
        return Carry(values, records).join(
            self.known.parked.get(node.attr, NOTHING),
            self.env.get(ast.unparse(node), NOTHING),
        )

    def _call(self, node: ast.Call, region: Sides) -> Carry:
        parts = [self.expr(node.func, region)]
        parts.extend(self._key(argument, region) for argument in node.args)
        for word in node.keywords:
            spelled = _spelled(word.arg)
            parts.append(self._key(word.value, region).join(Carry(spelled)))
            if spelled and isinstance(word.value, ast.Name | ast.Attribute):
                self.set(ast.unparse(word.value), Carry(spelled))
        self.meet(node, parts, region)
        if isinstance(node.func, ast.Attribute):
            self.mutate(node.func.value, NOTHING.join(*parts[1:]))
        name = _named(node.func)
        read = (
            Carry(frozenset({HEAD_TAG}))
            if name is not None
            and name.endswith(f"_{REVISION}")
            and name not in GRADED_SPELLINGS
            else NOTHING
        )
        return NOTHING.join(
            *parts,
            Carry(NONE, _records([name])),
            read,
            self.known.returns.get(name or "", NOTHING),
        )

    def _comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp,
        region: Sides,
    ) -> Carry:
        parts: list[Carry] = []
        inner = region
        for clause in node.generators:
            source = self.expr(clause.iter, inner)
            parts.append(source)
            self.bind(clause.target, source)
            for condition in clause.ifs:
                parts.append(self.expr(condition, inner))
                inner = inner | _tested(condition)
        yielded = (
            [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        )
        parts.extend(self.expr(part, inner) for part in yielded)
        self.meet(node, parts, region)
        return NOTHING.join(*parts, Carry(inner - region))

    def _parameters(self, args: ast.arguments, region: Sides) -> None:
        for default in (*args.defaults, *args.kw_defaults):
            self.expr(default, region)
        for name, annotation in _declared_parameters(args):
            self.set(name, _parameter(name, annotation))

    # --- patterns --------------------------------------------------------
    def pattern(self, node: ast.pattern, incoming: Carry, region: Sides) -> None:
        """Bind a ``match`` case's captures, and meet its value patterns."""
        if isinstance(node, ast.MatchValue):
            self.meet(node, [incoming, self.expr(node.value, region)], region)
        elif isinstance(node, ast.MatchSequence | ast.MatchOr):
            for inner in node.patterns:
                self.pattern(inner, incoming, region)
        elif isinstance(node, ast.MatchMapping):
            for key, inner in zip(node.keys, node.patterns, strict=True):
                keyed = incoming.whole().join(self._key(key, region))
                self.pattern(inner, keyed, region)
            if node.rest is not None:
                self.set(node.rest, incoming.whole())
        elif isinstance(node, ast.MatchClass):
            self._class_pattern(node, incoming, region)
        elif isinstance(node, ast.MatchAs):
            own = incoming
            if node.pattern is not None:
                self.pattern(node.pattern, incoming, region)
                if isinstance(node.pattern, ast.MatchClass):
                    own = own.join(Carry(NONE, _records([_named(node.pattern.cls)])))
            if node.name is not None:
                self.set(node.name, own)
        elif isinstance(node, ast.MatchStar) and node.name is not None:
            self.set(node.name, incoming.whole())

    def _class_pattern(
        self, node: ast.MatchClass, incoming: Carry, region: Sides
    ) -> None:
        records = _records([_named(node.cls)]) | incoming.records
        for inner in node.patterns:
            self.pattern(inner, incoming.whole().join(Carry(NONE, records)), region)
        for attr, inner in zip(node.kwd_attrs, node.kwd_patterns, strict=True):
            held = frozenset[str]().union(
                *(
                    FIELDS[name][attr]
                    for name in records
                    if attr in FIELDS.get(name, {})
                )
            )
            if not records:
                held |= UNAMBIGUOUS.get(attr, frozenset())
            self.pattern(inner, Carry(_spelled(attr), held), region)

    # --- statements ------------------------------------------------------
    def body(self, statements: Iterable[ast.stmt], region: Sides) -> None:
        for statement in statements:
            self.stmt(statement, region)

    def stmt(self, node: ast.stmt, region: Sides) -> None:
        if isinstance(node, SCOPES):
            self._nested(node, region)
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            self._assign(node, region)
        elif isinstance(node, ast.For | ast.AsyncFor):
            self.bind(node.target, self.expr(node.iter, region).join(Carry(region)))
            self.body(node.body, region)
            self.body(node.orelse, region)
        elif isinstance(node, ast.If | ast.While):
            self.expr(node.test, region)
            inner = region | _tested(node.test)
            self.body(node.body, inner)
            self.body(node.orelse, inner)
        elif isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                carry = self.expr(item.context_expr, region)
                if item.optional_vars is not None:
                    self.bind(item.optional_vars, carry)
            self.body(node.body, region)
        elif isinstance(node, ast.Match):
            self._match(node, region)
        elif isinstance(node, ast.Try | ast.TryStar):
            self.body(node.body, region)
            for handler in node.handlers:
                self.expr(handler.type, region)
                self.body(handler.body, region)
            self.body(node.orelse, region)
            self.body(node.finalbody, region)
        elif isinstance(node, ast.Return):
            if node.value is not None:
                self._hand_back(self.expr(node.value, region).join(Carry(region)))
        else:
            self._statement(node, region)

    def _statement(self, node: ast.stmt, region: Sides) -> None:
        """Any other statement: its expressions and the statements in it."""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                carry = self.expr(child, region)
                if isinstance(child, ast.Yield | ast.YieldFrom):
                    self._hand_back(carry)
            elif isinstance(child, ast.stmt):
                self.stmt(child, region)

    def _hand_back(self, carry: Carry) -> None:
        self.scope.returned = self.scope.returned.join(carry)

    def _nested(
        self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, region: Sides
    ) -> None:
        """What a nested scope evaluates where it is written; its body waits."""
        for decorator in node.decorator_list:
            self.expr(decorator, region)
        if isinstance(node, ast.ClassDef):
            for base in (*node.bases, *(word.value for word in node.keywords)):
                self.expr(base, region)
        else:
            for default in (*node.args.defaults, *node.args.kw_defaults):
                self.expr(default, region)
        self.nested.append(node)

    def _assign(
        self, node: ast.Assign | ast.AnnAssign | ast.AugAssign, region: Sides
    ) -> None:
        carry = self.expr(node.value, region).join(Carry(region))
        if isinstance(node, ast.Assign):
            for target in node.targets:
                self.bind(target, carry)
            return
        if isinstance(node, ast.AugAssign):
            current = self.expr(_read(node.target), region)
            self.meet(node, [current, carry], region)
        else:
            carry = carry.join(Carry(NONE, _annotated(node.annotation)))
            if node.value is None:
                return
        self.bind(node.target, carry)

    def _match(self, node: ast.Match, region: Sides) -> None:
        subject = self.expr(node.subject, region)
        for case in node.cases:
            self.pattern(case.pattern, subject, region)
            inner = region | _tested(case.pattern)
            if case.guard is not None:
                self.expr(case.guard, inner)
                inner |= _tested(case.guard)
            self.body(case.body, inner)


def _read(target: ast.expr) -> ast.expr:
    """The same target, as the expression that reads it."""
    return ast.parse(ast.unparse(target), mode="eval").body


def _annotated(annotation: ast.AST) -> frozenset[str]:
    """The carrying records an annotation names, a forward reference included."""
    names: set[str | None] = set()
    stack = [annotation]
    while stack:
        current = stack.pop()
        names.add(_named(current))
        if isinstance(current, ast.Constant) and isinstance(current.value, str):
            try:
                stack.append(ast.parse(current.value, mode="eval").body)
            except SyntaxError:
                continue
        stack.extend(ast.iter_child_nodes(current))
    return _records(names)


def _declared_parameters(
    args: ast.arguments,
) -> Iterator[tuple[str, ast.expr | None]]:
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    every.extend(arg for arg in (args.vararg, args.kwarg) if arg is not None)
    for arg in every:
        yield arg.arg, arg.annotation


def _parameter(name: str, annotation: ast.expr | None) -> Carry:
    """What a parameter holds by its name, or by the records it is typed as."""
    records = UNAMBIGUOUS.get(name, frozenset())
    if annotation is not None:
        records |= _annotated(annotation)
    return Carry(_spelled(name), records)


def _walk_scope(
    node: ScopeNode,
    label: str,
    inherited: dict[str, Carry],
    owner: str | None,
    flows: tuple[Flow, Flow],
) -> tuple[Scope, Walker]:
    """One scope, walked until the names bound in it stop growing.

    Each pass can only add to what a name carries, so the loop is bounded by
    the scope's size and leaves as soon as a pass adds nothing.
    """
    env = dict(inherited)
    selves: frozenset[str] = frozenset()
    if isinstance(node, BODIES):
        for name, annotation in _declared_parameters(node.args):
            env[name] = env.get(name, NOTHING).join(_parameter(name, annotation))
        first = [*node.args.posonlyargs, *node.args.args][:1]
        if owner is not None and first:
            selves = frozenset({first[0].arg})
            env[first[0].arg] = Carry(NONE, _records([owner]))
    for _ in range(sum(1 for _ in ast.walk(node)) + 1):
        scope = Scope(label)
        walker = Walker(scope, env, flows, selves)
        walker.body(node.body, NONE)
        if not walker.changed:
            break
    else:
        raise AssertionError(f"{label}: the names bound in it never settled")
    if isinstance(node, BODIES):
        for name, _ in _declared_parameters(node.args):
            scope.holds.update(env.get(name, NOTHING).values)
    return scope, walker


def _walk_module(tree: ast.Module, imported: dict[str, Carry]) -> list[Scope]:
    """Every scope of one module, grown to a fixed point over the module.

    What a body returns and what it parks on an attribute are what the rest
    of the module reads them as, so the module is walked again with each
    pass's answer folded in.  A pass can only add, there are no more returns
    and attributes than the module has bodies and attributes, and the loop
    leaves as soon as a pass adds nothing.
    """
    known = Flow(dict(imported), {})
    bound = sum(
        1 for node in ast.walk(tree) if isinstance(node, (*BODIES, ast.Attribute))
    )
    for _ in range(bound + 1):
        grown = Flow(dict(known.returns), dict(known.parked))
        scopes: list[Scope] = []
        pending: list[tuple[ScopeNode, str, dict[str, Carry], str | None]] = [
            (tree, "<module>", {}, None)
        ]
        while pending:
            node, label, inherited, owner = pending.pop()
            scope, walker = _walk_scope(node, label, inherited, owner, (known, grown))
            scopes.append(scope)
            if isinstance(node, BODIES):
                _add(grown.returns, node.name, scope.returned)
            for child in walker.nested:
                pending.append(
                    (
                        child,
                        child.name if label == "<module>" else f"{label}.{child.name}",
                        walker.env,
                        child.name
                        if isinstance(child, ast.ClassDef)
                        else owner
                        if isinstance(node, ast.ClassDef)
                        else None,
                    )
                )
        if grown == known:
            return scopes
        known = grown
    raise AssertionError("the module's returns and attributes never settled")


def _imports(
    tree: ast.Module, exported: dict[str, dict[str, Carry]]
) -> dict[str, Carry]:
    """What each function this module imports by name hands back."""
    found: dict[str, Carry] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                carry = exported.get(node.module, {}).get(alias.name)
                if carry is not None:
                    found[alias.asname or alias.name] = carry
    return found


def _dotted(module: str) -> str:
    """The import path of a module named by its path under the package."""
    dotted = module.removesuffix(".py").replace("/", ".")
    return f"{PACKAGE}.{dotted}".removesuffix(".__init__")


def _by_site(module: str, scopes: Iterable[Scope]) -> dict[str, Scope]:
    """A module's scopes by site; two defined under one name are one site."""
    found: dict[str, Scope] = {}
    for scope in scopes:
        site = f"{module}::{scope.label}"
        held = found.setdefault(site, Scope(scope.label))
        held.holds |= scope.holds
        held.meetings += scope.meetings
        held.returned = held.returned.join(scope.returned)
    return found


class Tree(NamedTuple):
    """The walked tree: each scope by site, and what each module imports."""

    scopes: dict[str, Scope]
    imported: dict[str, dict[str, Carry]]


def walk(sources: dict[str, str]) -> Tree:
    """Every scope of every module, with imported returns to a fixed point.

    A module-level function's return is read by the modules that import it
    by name, so the tree is walked again with each pass's exports folded in.
    A pass can only add, and there are no more exports than module-level
    functions, so the loop is bounded by that count.
    """
    trees = {module: ast.parse(text) for module, text in sources.items()}
    exported: dict[str, dict[str, Carry]] = {}
    bound = sum(
        1 for tree in trees.values() for node in tree.body if isinstance(node, BODIES)
    )
    for _ in range(bound + 1):
        found: dict[str, Scope] = {}
        imported: dict[str, dict[str, Carry]] = {}
        grown: dict[str, dict[str, Carry]] = {}
        for module, tree in trees.items():
            imported[module] = _imports(tree, exported)
            scopes = _walk_module(tree, imported[module])
            sites = _by_site(module, scopes)
            found.update(sites)
            tops = {node.name for node in tree.body if isinstance(node, BODIES)}
            exports = {
                scope.label: scope.returned
                for scope in sites.values()
                if scope.label in tops and scope.returned.sides
            }
            if exports:
                grown[_dotted(module)] = exports
        if grown == exported:
            return Tree(found, imported)
        exported = grown
    raise AssertionError("the tree's imported returns never settled")


def co_holders(scopes: dict[str, Scope]) -> dict[str, tuple[str, ...]]:
    """Every scope that holds both revisions or meets them, with its meetings."""
    return {
        site: tuple(sorted(scope.meetings.elements()))
        for site, scope in scopes.items()
        if BOTH <= scope.holds or scope.meetings
    }


@cache
def _sources() -> dict[str, str]:
    return {
        path.relative_to(SOURCE).as_posix(): path.read_text()
        for path in sorted(SOURCE.rglob("*.py"))
    }


@cache
def shipped() -> Tree:
    """The shipped tree, walked once for every test that asks."""
    return walk(_sources())


class Row(NamedTuple):
    """Why a scope holds both revisions, and every place it meets them."""

    reason: str
    meetings: tuple[str, ...]


#: Every scope in the sources that holds BOTH revisions, why that is
#: legitimate there, and each expression in it where the two meet, verbatim.
#: The rule is not here: weighing the pair is its whole job.
#:
#: A recorded revision read against ITSELF, or against a second recorded
#: revision, is provenance: it answers whether a reading is about the commit
#: it says it is, which is a different question from whether that reading
#: still stands.  A revision handed to a port as a ref, quoted into a
#: session's prompt, recorded onto a row or carried into the rule's own
#: arguments weighs nothing at all.  A meeting a row pins is not a licence
#: for the body: one more meeting, or a changed one, is red.
#:
#: The rows live in a data file beside this one, so each meeting is pinned
#: verbatim however long its line is.
REGISTER_FILE = Path(__file__).with_name("graded_sha_co_holders.json")
REGISTERED: dict[str, Row] = {
    site: Row(reason=row["reason"], meetings=tuple(row["meetings"]))
    for site, row in json.loads(REGISTER_FILE.read_text()).items()
}


def findings(scopes: dict[str, Scope], register: dict[str, Row]) -> list[str]:
    """Every way *scopes* differ from the register, in words.

    A co-holder nobody registered, a registered one meeting the two revisions
    anywhere its row does not pin, and a row for a scope of these modules that
    no longer holds both.
    """
    held = co_holders(scopes)
    found: list[str] = []
    for site, meetings in sorted(held.items()):
        row = register.get(site)
        if site == RULE_SITE:
            continue
        if row is None:
            found.append(f"{site} holds both revisions and is not registered")
        elif meetings != tuple(sorted(row.meetings)):
            found.append(f"{site} meets them at {meetings}, not {row.meetings}")
    modules = {site.partition("::")[0] for site in scopes}
    found.extend(
        f"{site} is registered and no longer holds both"
        for site in sorted(register)
        if site.partition("::")[0] in modules and site not in held
    )
    return found


def _scope_node(tree: ast.Module, label: str) -> ScopeNode:
    """The scope a site's dotted path names, searched scope by scope."""
    node: ScopeNode = tree
    for name in label.split(".") if label != "<module>" else []:
        stack: list[ast.AST] = list(ast.iter_child_nodes(node))
        while stack:
            current = stack.pop(0)
            if isinstance(current, SCOPES) and current.name == name:
                node = current
                break
            if not isinstance(current, SCOPES):
                stack.extend(ast.iter_child_nodes(current))
        else:
            raise AssertionError(f"no scope {name!r} in {label}")
    return node


def planted(site: str, block: str) -> dict[str, Scope]:
    """The site's module walked again with *block* written into the site.

    The block goes in as the first statement of the scope's body, indented
    to it; at module scope it goes at the end.
    """
    module, _, label = site.partition("::")
    source = _sources()[module]
    node = _scope_node(ast.parse(source), label)
    lines = source.splitlines(keepends=True)
    if isinstance(node, ast.Module):
        at, indent = len(lines), ""
    else:
        first = node.body[0]
        assert first.lineno > node.lineno, site
        at, indent = first.lineno - 1, " " * first.col_offset
    written = [f"{indent}{line}" for line in block.splitlines(keepends=True)]
    tree = ast.parse("".join([*lines[:at], *written, *lines[at:]]))
    return _by_site(module, _walk_module(tree, shipped().imported[module]))


def alone(source: str) -> dict[str, Scope]:
    """One new module, walked on its own."""
    return walk({"reader.py": source}).scopes


#: Each spelling that walked past earlier rounds of this guard, and a few
#: more a reader would call ordinary Python, written as statements so
#: each can be planted as a module of its own and inside any scope.  The
#: names are the ones a reader would use; none of them is bound anywhere.
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
    "attrgetter": (
        "from operator import attrgetter\n"
        "_planted = attrgetter('graded_sha')(evidence) != head_sha\n"
    ),
    "iterating-the-models-fields": (
        "for _key, _value in evidence:\n"
        "    if _key == 'graded_sha':\n"
        "        _planted = _value != head_sha\n"
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
}


def _indented(block: str) -> str:
    return "".join(f"    {line}" for line in block.splitlines(keepends=True))


def test_the_graded_identity_the_guard_scans_for_is_the_evidence_records_own_field():
    """The walk keys on the record's field, not on a spelling repeated here."""
    assert len(GRADED_IDENTITIES) == 1
    assert GRADED in CriterionEvidence.model_fields
    assert GRADED in signature(graded_state).parameters
    assert CARRIERS[CriterionEvidence.__name__] == frozenset({GRADED_TAG})


def test_the_head_is_the_rules_other_revision_and_the_fields_named_for_it():
    """The head side is derived from the rule and the records, not listed."""
    assert len(HEADS) == 1
    assert HEAD in signature(graded_state).parameters
    assert HEADS <= HEAD_SPELLINGS
    assert HEAD_SPELLINGS & GRADED_SPELLINGS == frozenset()
    fields = _spellings(lambda name: name.endswith(f"_{HEAD}"))
    assert fields
    assert fields <= HEAD_SPELLINGS


def test_a_record_holding_the_evidence_carries_the_graded_revision():
    """Which records carry a side is grown over their fields, not listed."""
    holders = [
        model.__name__
        for model in MODELS
        if any(
            CriterionEvidence in _referenced(info.annotation)
            for info in model.model_fields.values()
        )
    ]
    assert holders
    assert all(GRADED_TAG in CARRIERS[name] for name in holders)


def test_the_rule_the_guard_permits_is_the_one_the_sources_import():
    """The permitted site is derived, so a rename carries the guard with it."""
    assert (SOURCE / RULE_MODULE).is_file()
    assert RULE_MODULE.startswith("domain/")
    assert RULE in (SOURCE / RULE_MODULE).read_text()


def test_the_sources_compare_a_graded_sha_with_a_head_sha_in_one_body_only():
    """Every co-holder is the rule or registered, meeting them only as pinned."""
    assert findings(shipped().scopes, REGISTERED) == []


def test_every_exemption_names_a_site_the_walk_actually_reports():
    """A stale row reds: the register cannot outlive the holding it explains."""
    held = co_holders(shipped().scopes)
    assert frozenset(REGISTERED) <= frozenset(held), sorted(
        frozenset(REGISTERED) - frozenset(held)
    )


def test_every_exemption_carries_the_reason_it_is_one():
    assert all(row.reason.strip() for row in REGISTERED.values())
    assert RULE_SITE not in REGISTERED


def test_the_walk_reaches_every_scope_the_package_has():
    """Each module, class and function body of the package is a scope walked."""
    tree = shipped()
    expected = {f"{module}::<module>" for module in _sources()} | {
        f"{module}::{label}"
        for module, text in _sources().items()
        for label in _labels(ast.parse(text))
    }
    assert frozenset(tree.scopes) == frozenset(expected)


def _labels(tree: ast.Module) -> Iterator[str]:
    """Every named scope's dotted path, read independently of the walk."""
    stack: list[tuple[ast.AST, str]] = [(tree, "")]
    while stack:
        node, prefix = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, SCOPES):
                label = f"{prefix}{child.name}"
                yield label
                stack.append((child, f"{label}."))
            else:
                stack.append((child, prefix))


@pytest.mark.parametrize("spelling", list(PLANTS))
def test_every_spelling_that_compares_the_graded_sha_is_reported(spelling):
    """Each spelling, planted as a module of its own, reds -- and in a body."""
    block = PLANTS[spelling]
    assert findings(alone(block), REGISTERED)
    body = "def lapsed(evidence, head_sha, prior, row, lane, git, cwd):\n"
    assert findings(alone(body + _indented(block)), REGISTERED)


@pytest.mark.parametrize("spelling", list(PLANTS))
def test_a_second_module_performing_the_comparison_is_reported(spelling):
    """Beside the rule's own module, a second module weighing the pair reds.

    The rule's module is walked as shipped and is clean; the reader's own
    scopes are the only findings.
    """
    sources = {RULE_MODULE: _sources()[RULE_MODULE]}
    assert findings(walk(sources).scopes, REGISTERED) == []
    sources["reader.py"] = (
        "def lapsed(evidence, head_sha, prior, row, lane, git, cwd):\n"
    )
    sources["reader.py"] += _indented(PLANTS[spelling])
    found = findings(walk(sources).scopes, REGISTERED)
    assert found
    assert all(
        finding.startswith("reader.py::lapsed")
        and finding.endswith("holds both revisions and is not registered")
        for finding in found
    ), found


@pytest.mark.parametrize("spelling", list(PLANTS))
def test_every_spelling_planted_inside_a_registered_scope_is_reported(spelling):
    """A row pins meetings, not a body: a comparison added inside one reds.

    Every registered scope is planted in turn -- the rule's neighbour in its
    own module among them -- and each planted module has to differ from the
    register.
    """
    missed = [
        site
        for site in sorted(REGISTERED)
        if not findings(planted(site, PLANTS[spelling]), REGISTERED)
    ]
    assert missed == []


def test_a_new_scope_holding_both_revisions_is_reported_without_a_meeting():
    """Holding both is the census, whether or not they meet yet."""
    source = (
        "def hold(evidence, lane):\n"
        "    recorded = evidence.graded_sha\n"
        "    pushed = lane.pushed_head_sha\n"
        "    return None\n"
    )
    scopes = alone(source)
    assert scopes["reader.py::hold"].meetings == Counter()
    assert findings(scopes, REGISTERED) == [
        "reader.py::hold holds both revisions and is not registered"
    ]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "def other(evidence):\n"
            "    return evidence.recorded_sha != evidence.checked_sha\n",
            id="two-other-revisions",
        ),
        pytest.param(
            "def moved(head_sha, other_head_sha):\n"
            "    return head_sha != other_head_sha\n",
            id="two-heads",
        ),
        pytest.param(
            "def pointed(evidence, pointer):\n"
            "    return evidence.test.endswith(pointer)\n",
            id="another-field-of-the-record",
        ),
        pytest.param(
            "def dumped(record, head_sha):\n"
            "    return record['head_sha'] != head_sha\n",
            id="another-serialised-key",
        ),
        pytest.param(
            "def recorded(git, repo, evidence):\n"
            "    return git.reset_hard(cwd=repo, ref=evidence.graded_sha)\n",
            id="one-revision-handed-to-a-port",
        ),
        pytest.param(
            "NAMES = frozenset({'graded_sha', 'head_sha', 'check'})\n",
            id="a-table-of-names",
        ),
    ],
)
def test_naming_the_graded_sha_without_comparing_it_is_not_a_site(source):
    assert findings(alone(source), REGISTERED) == []


def test_a_reader_consulting_the_rule_is_not_a_site_and_its_own_arithmetic_is():
    """The consultation is the one meeting its row pins; arithmetic beside it is not.

    Read on the shipped readers of the rule: each is green as it stands, and
    the same reader with the pair weighed beside the consultation is red.
    """
    assert CALLERS
    for site in CALLERS:
        module = site.partition("::")[0]
        scopes = {
            key: scope
            for key, scope in shipped().scopes.items()
            if key.startswith(f"{module}::")
        }
        assert findings(scopes, REGISTERED) == []
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
    held = co_holders(alone(source))
    assert held == {
        "reader.py::lapsed": (
            "evidence.graded_sha != other_head_sha",
            "evidence.graded_sha == head_sha",
        ),
        "reader.py::stale": ("evidence.graded_sha != head_sha",),
    }


def test_a_helper_handed_the_pair_under_other_names_is_reported_at_its_caller():
    """The boundary, read from both sides.

    A body handed both revisions under names no record uses holds nothing
    this walk can see.  The caller that hands them over meets them in that
    very call, so a second weighing is reachable only through a reported
    meeting.
    """
    taken = "def lapsed(recorded, current):\n    return recorded != current\n"
    assert co_holders(alone(taken)) == {}
    handing = taken + (
        f"def asks(evidence, head_sha):\n    return lapsed(evidence.{GRADED}, {HEAD})\n"
    )
    assert co_holders(alone(handing)) == {
        "reader.py::asks": (f"lapsed(evidence.{GRADED}, {HEAD})",)
    }


def test_a_value_returned_or_parked_in_one_body_is_followed_into_another():
    """A body that hands the revision back, or parks it, is read as holding it."""
    source = (
        "def recorded(row):\n"
        "    return row.graded_sha\n"
        "class Reader:\n"
        "    def keep(self, row):\n"
        "        self._taken = row.graded_sha\n"
        "    def lapsed(self, head_sha, row):\n"
        "        return self._taken != head_sha or recorded(row) != head_sha\n"
    )
    assert "reader.py::Reader.lapsed" in co_holders(alone(source))


def test_a_function_imported_by_name_hands_back_what_it_returns():
    source = {
        "graded.py": "def recorded(row):\n    return row.graded_sha\n",
        "reader.py": (
            f"from {PACKAGE}.graded import recorded\n"
            "def lapsed(row, head_sha):\n"
            "    return recorded(row) != head_sha\n"
        ),
    }
    assert co_holders(walk(source).scopes) == {
        "reader.py::lapsed": ("recorded(row) != head_sha",)
    }


def test_the_stated_limit_is_evasion_and_it_is_not_seen():
    """What the docstring says this check does not claim, read as code."""
    evasions = (
        "def lapsed(evidence, head_sha):\n"
        "    return getattr(evidence, 'graded' + '_sha') != head_sha\n"
        "def evaluated(evidence, head_sha):\n"
        "    return eval('evidence.graded_sha') != head_sha\n"
    )
    assert co_holders(alone(evasions)) == {}


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
            if isinstance(child, SCOPES):
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


def test_every_reader_of_the_rule_is_registered_as_a_body_that_holds_both():
    """Consulting the rule is holding both, so both tables see it."""
    assert frozenset(CALLERS) <= frozenset(co_holders(shipped().scopes))
    assert frozenset(CALLERS) <= frozenset(REGISTERED)


def test_the_rule_is_consulted_from_one_body_in_each_package_that_reads_it():
    """One reader per package, so the answer is not qualified twice over."""
    packages: dict[str, list[str]] = {}
    for site in sorted(callers(SOURCE)):
        module, _, _ = site.partition("::")
        packages.setdefault(module.rpartition("/")[0], []).append(site)
    assert packages
    assert all(len(sites) == 1 for sites in packages.values()), packages


def test_a_second_reader_in_one_package_is_reported(tmp_path):
    source = (
        "def one(evidence, head_sha):\n"
        f"    return {RULE}({GRADED}=evidence.{GRADED}, head_sha=head_sha)\n"
        "def two(evidence, head_sha):\n"
        f"    return {RULE}({GRADED}=evidence.{GRADED}, head_sha=head_sha)\n"
    )
    assert _calls(ast.parse(source)) == frozenset({"one", "two"})
