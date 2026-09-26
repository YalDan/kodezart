"""No module reads an arm's text beside the one total formatter (KOD-410).

Every name the scan tracks is read off the code rather than typed here: the
arm types off the partition's own union, the arm fields and the neighbouring
renderer off the total formatter's source, the digest function's name and its
home module off the function itself, the model's own text renders off their
return annotations, and the places a spec is held — a reader's return, a model
field, a state key, an annotated parameter — off every annotation in the
package.  The scan takes its sources as a map, so a module that never reaches
the tree can be injected as a control.

The walk follows the value, not the spelling.  A spec is held by an annotated
parameter (the arm under any alias an import or an assignment gives it,
inside a function or not, or through a module alias), by a parameter a call or
a ``functools.partial`` hands it to, by a lambda it is handed to, by an
attribute or a literal key it is stored under, by a type test, a class
pattern (captured or not) or a cast that restates it, and by every name bound
from any of those: an assignment, a walrus, a loop, a comprehension, a
``with``, an unpacking target (by its slot where a tuple is written out or a
reader's ``tuple[...]`` annotation states it, else by the whole value), and a
``match`` capture anywhere in its pattern.  A capture is read off where it
stands, not off which pattern spelled it: a class pattern that names an arm
makes its position an arm, under an arm the text field is text and the
payload field payload, a spec-typed field is a spec, and a sequence item, a
mapping value or a positional sub-pattern holds what its position held.  A
collection of specs is a spec to the walk, as the loop over it already was:
a display holding one (a tuple, list, set or dict, a ``**`` entry included),
a word a container method or a store put one into (``held.append(spec)``,
``held.update(k=spec)``, ``held[key] = spec``), a binary operation over one
(``[spec] + rest``), and an index, a slice, a key (bracketed or through
``.get``), a comprehension over it, a method of it (``.values()``,
``.model_dump()``, ``.model_copy()``), a method of an arm's class
(``TrackerSpec.model_validate(raw)``), a reader reached as an attribute (a
property annotated with an arm), and a builtin or a function imported from
outside the package handed it (``next(iter(...))``, ``enumerate``, ``zip``,
``copy.deepcopy``, ``RootModel[TrackerSpec](spec)``) are specs too.  A
function from outside the package handed an arm's class builds what
validates into an arm (``TypeAdapter(TrackerSpec)``, bound at module level or
not), so what its methods hand back is a spec.  The payload is followed the
same way through a comprehension, a display and a method of it.  The call edge
is re-walked over the grown scopes to a fixed point, so a spec handed on
through a chain of helpers is followed to the helper that finally reads it.

A read is the text field loaded off a spec by its own name — an attribute, a
literal key, ``getattr`` with a literal, an ``operator`` getter or
``methodcaller`` built over it, or any call handed a spec and a literal naming
the field anywhere in its arguments or its receiver chain
(``spec.__getattribute__("body")``, ``inspect.getattr_static(spec, "body")``,
``spec.model_dump(include={"body"})``, ``"{0.body}".format(spec)``,
``Template("$body").substitute(spec.model_dump())``) — or a word a pattern
captured the text into.  A literal is a string constant, an identifier inside
one, a word bound to one anywhere in the module (``_FIELD = "body"``), or a
display written out of those.  A render is a whole spec or its payload handed
to the neighbouring renderer, to ``str``, ``repr``, ``format`` or ``ascii``
(called, or handed as a value to the call that applies it, as
``map(str, specs)``), to an f-string, a ``%``, a ``str.format`` or a
``str.format_map``, a spec turned into text by the model's own render method,
or a whole spec handed to any other call whose stated return is text: a
package function annotated ``-> str`` other than the formatter
(``template.render({"task_md": spec})``), or a callable from outside the
package whose own annotation or typeshed stub says ``str``
(``json.dumps(spec.model_dump())``, ``"".join(...)``).  The planted controls
below pin each of these one row at a time, and the rows beside them pin that
a field that is not the text, an element of an unpacking that is not the
spec, and a value that never was a spec stay silent.

The one thing a module may do with an arm's text besides hand it to the
formatter is hand it to the one digest function, which hashes the bytes and
renders nothing.  Those positions are counted apart and pinned exactly.

One module reads the text itself, and it is named below rather than excused:
deciding whether an answer exceeds what the subject stated means reading what
the subject stated.  It renders nothing and reaches no prompt, so it is a read
of the text and not a second formatter, and it is listed so that a second one
cannot arrive unnoticed.

What is not a render, by decision.  A field of the payload
(``spec.ticket.title``): the payload renders only whole, and the package reads
the draft's title for a pull-request title in two places, which this walk
leaves to its own decision.  The payload's own serialisation
(``ticket.model_dump_json()``), handed on or not: the package persists the
authored ticket as JSON beside the formatter, which is a record of the payload
rather than a text of the fire, and a planted row pins that it stays silent.

Over-inclusion.  Scopes are module-wide, so a word bound to a spec anywhere in
a module is a spec wherever that module reads it; a call through a receiver
that spells no module is resolved to every method of that name; and a literal
naming the text field beside a spec makes the call a read whatever the literal
does there.  Each errs on the red side.

Stated limit.  Outside this guard's reach: a value handed across a function
boundary, where the other function is not resolved at this site (returned from
a helper, stored on an object and read elsewhere, or passed through a container
built elsewhere); a name built at run time; a binding made only when a
function runs (``setattr`` or ``globals()`` inside a function body).  The
shapes this walk meets of the first kind: a spec returned under a wider
annotation than an arm (``-> object``) and read with no type test, cast or
class pattern restating it; a starred argument, which lands on no parameter;
an unbound method called with an explicit instance, which lands its
arguments one place late; and a relative import, which is no route to a
definition.  A helper reached those ways must still annotate its parameter
under the strict type check, and an annotation naming an arm is a root of its
own.  Of the second kind: ``getattr``, ``vars`` or ``importlib`` with a
computed name, a field chosen by comparing names at run time, and
``eval``/``exec``.  ``STATED_LIMIT_SHAPES`` holds one row of each kind unseen.
There is no behavioural pin beside this guard: the formatter renders a tracker
arm as its body unchanged, so a module reading the body itself produces the
same text, and where the text is read is the whole of the Check.
"""

import ast
import builtins
import collections
import functools
import inspect
import operator
import re
import typing
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import get_args

import pytest
from pydantic import BaseModel

from kodezart.domain.fire_spec import body_digest
from kodezart.domain.ticket import format_fire_spec
from kodezart.types.domain import fire_spec as partition
from kodezart.types.domain.fire_spec import FireSpec
from tests.name_resolution import (
    SOURCE_ROOT,
    PatternStep,
    Resolution,
    annotated_parameters,
    bound_names,
    call_sites,
    definitions,
    parameters_of,
    parameters_receiving,
    parsed,
    pattern_captures,
    pattern_classes,
    resolve,
    returns_text,
    source_tree,
    through_partials,
    unpacking_bindings,
)

FORMATTER_SOURCE = Path(inspect.getsourcefile(format_fire_spec) or "")
FORMATTER = FORMATTER_SOURCE.relative_to(SOURCE_ROOT).as_posix()
ARMS = {arm.__name__: arm for arm in get_args(FireSpec)}
SPEC_TYPES = frozenset(
    set(ARMS) | {name for name, value in vars(partition).items() if value is FireSpec},
)
DIGEST = body_digest.__name__
FORMATTER_NAME = format_fire_spec.__name__
DIGEST_HOME = body_digest.__module__


def _arm_fields() -> dict[str, object]:
    """Each arm field the one formatter turns into the fire's text, annotated."""
    fields: dict[str, object] = {}
    for node in ast.walk(ast.parse(inspect.getsource(format_fire_spec))):
        if not isinstance(node, ast.MatchClass) or not isinstance(node.cls, ast.Name):
            continue
        arm = ARMS.get(node.cls.id)
        if arm is None:
            continue
        for attr in node.kwd_attrs:
            fields[attr] = arm.model_fields[attr].annotation
    return fields


ARM_FIELDS = _arm_fields()
TEXT_FIELDS = frozenset(
    name
    for name, annotation in ARM_FIELDS.items()
    if isinstance(annotation, type) and issubclass(annotation, str)
)
PAYLOAD_FIELDS = frozenset(ARM_FIELDS) - TEXT_FIELDS


def _neighbouring_renderers() -> frozenset[str]:
    """The formatter module's other rendering functions, by defined name."""
    tree = ast.parse(FORMATTER_SOURCE.read_text(encoding="utf-8"))
    return frozenset(
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name != format_fire_spec.__name__
        and isinstance(node.returns, ast.Name)
        and node.returns.id == "str"
    )


RENDERERS = _neighbouring_renderers()


def _mentions_spec(annotation: ast.expr | None, words: frozenset[str]) -> bool:
    """Whether an annotation names one of the partition's types anywhere."""
    if annotation is None:
        return False
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name) and node.id in words:
            return True
        if isinstance(node, ast.Attribute) and node.attr in words:
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                quoted = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                continue
            if _mentions_spec(quoted, words):
                return True
    return False


def _spells(node: ast.expr) -> str | None:
    """The bare or attribute-qualified word an expression spells."""
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else None


#: What the walk says a value is.  A spec stands for an arm and for any
#: collection of arms alike, the way a loop over the collection already binds
#: its target to the collection it walks.
SPEC, TEXT, PAYLOAD = "spec", "text", "payload"

#: Every field an arm declares, read off the arms.  An attribute of a spec
#: that is one of these is that field; any other attribute of a spec — a
#: method, ``.values``, ``__dict__`` — still reaches the arm.
ARM_MODEL_FIELDS = frozenset(name for arm in ARMS.values() for name in arm.model_fields)

#: The model's own methods that return text, read off their return
#: annotations: calling one on a spec renders the whole spec.
MODEL_RENDERS = frozenset(
    name
    for name, member in inspect.getmembers(BaseModel)
    if callable(member)
    and inspect.get_annotations(member).get("return") in (str, "str")
)

#: The builtins that turn any object into text.  Python's own closed set, so
#: it is named here rather than derived: a spec handed to one is rendered.
TEXT_BUILTINS = frozenset(
    {str.__name__, repr.__name__, format.__name__, ascii.__name__}
)

#: The builtin that reaches an attribute by a name held as a value:
#: ``getattr(spec, "body")`` is ``spec.body``.  A literal name is resolved; a
#: name built at run time is out of reach.  ``vars(spec)`` needs no entry of
#: its own: a builtin handed a spec is a spec, so a literal key off it is read.
REFLECTORS = frozenset({getattr.__name__})

#: The mapping lookups that take a key as their first argument: ``x.get("k")``
#: is ``x["k"]``, read off ``dict`` itself.
LOOKUPS = frozenset({dict.get.__name__, dict.pop.__name__, dict.setdefault.__name__})

#: The text methods that fill a template from a mapping or from arguments.
TEMPLATE_FILLS = frozenset({str.format.__name__, str.format_map.__name__})

#: ``typing.cast``: a cast to an arm states the value's type as an
#: annotation does.
CASTS = frozenset({typing.cast.__name__})

#: The ``operator`` getters: ``attrgetter("body")`` reads the text of the
#: spec it is later applied to, and ``methodcaller`` calls the lookup it names.
GETTERS = frozenset(
    {
        operator.attrgetter.__name__,
        operator.itemgetter.__name__,
        operator.methodcaller.__name__,
    }
)

#: The methods of the builtin containers, read off the containers: a spec
#: handed to one on a word (``held.append(spec)``, ``held.update(k=spec)``)
#: puts the spec in what that word holds, so the word holds a spec.
CONTAINER_METHODS = frozenset(
    name
    for kind in (list, set, dict, collections.deque)
    for name in vars(kind)
    if not name.startswith("_")
)

#: Every word a module's spellings are resolved for, in one pass.
RESOLVED = (
    SPEC_TYPES | RENDERERS | REFLECTORS | TEXT_BUILTINS | GETTERS | {FORMATTER_NAME}
)

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class _Module:
    """One module's declarations, read once for every pass over it."""

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.expressions = list(ast.walk(tree))
        self.functions = [
            node
            for node in self.expressions
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        self.returns = [
            (function.name, node.value)
            for function in self.functions
            for node in ast.walk(function)
            if isinstance(node, ast.Return) and node.value is not None
        ]
        # An arm, a renderer, a builtin or a getter is named by its own word,
        # by any alias an import or an assignment gives it, and through a
        # module that holds it.
        self.names = resolve(tree, names=RESOLVED)
        self.spec_words = SPEC_TYPES | frozenset(
            local for local, name in self.names.names.items() if name in SPEC_TYPES
        )
        self.spec_parameters = set(
            annotated_parameters(tree, mentioning=self.spec_words)
        )
        self.annotated_fields = {
            node.target.id
            for owner in self.expressions
            if isinstance(owner, ast.ClassDef)
            for node in owner.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and _mentions_spec(node.annotation, self.spec_words)
        }
        self.digest = resolve(tree, names={DIGEST}, from_module=DIGEST_HOME)
        self.casts = resolve(tree, names=CASTS, from_module=typing.__name__)
        self.bound: set[str] = set()
        #: Every word the module spells anywhere: a module none of whose
        #: words names a root can hold no spec, so its scope is empty.
        self.words: set[str] = set()
        for node in self.expressions:
            if isinstance(node, ast.Import | ast.ImportFrom):
                for alias in node.names:
                    self.bound.add(alias.asname or alias.name.partition(".")[0])
                    self.words.add(alias.name)
            elif isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ):
                self.bound.add(node.name)
            elif isinstance(node, ast.Name):
                self.words.add(node.id)
                if isinstance(node.ctx, ast.Store):
                    self.bound.add(node.id)
            elif isinstance(node, ast.Attribute):
                self.words.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                self.words.add(node.value)
            elif isinstance(node, ast.MatchClass):
                self.words.update(node.kwd_attrs)
        #: The local words an import from outside the package binds, with
        #: the dotted path each one names: a function reached through one is
        #: no definition the walk can read, but its stated return can be.
        self.origins: dict[str, str] = {}
        for node in self.expressions:
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                if (node.module or "").partition(".")[0] != SOURCE_ROOT.name:
                    self.origins.update(
                        (alias.asname or alias.name, f"{node.module}.{alias.name}")
                        for alias in node.names
                    )
            elif isinstance(node, ast.Import):
                self.origins.update(
                    (
                        alias.asname or alias.name.partition(".")[0],
                        alias.name if alias.asname else alias.name.partition(".")[0],
                    )
                    for alias in node.names
                    if alias.name.partition(".")[0] != SOURCE_ROOT.name
                )
        self.foreign = set(self.origins)
        #: The string constants each word is bound to, anywhere in the
        #: module: ``_FIELD = "body"`` names the field wherever ``_FIELD``
        #: is read.
        self.constants: dict[str, set[str]] = {}
        for node in self.expressions:
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                for target in targets:
                    if isinstance(target, ast.Name):
                        self.constants.setdefault(target.id, set()).add(value.value)
        self.unpacked = unpacking_bindings(tree)
        self.where = definitions(tree)
        self.calls = [node for node in self.expressions if isinstance(node, ast.Call)]
        self.lambdas: dict[str, list[ast.Lambda]] = {}
        for node in self.expressions:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Lambda):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.lambdas.setdefault(target.id, []).append(node.value)

    def denotes(
        self, resolution: Resolution, func: ast.expr, names: frozenset[str]
    ) -> bool:
        """Whether *func* is one of *names*, however this module spells it."""
        return resolution.denotes(func) in names or _spells(func) in names


class _Package:
    """The roots a spec is reachable from, derived over a whole source map."""

    def __init__(self, sources: Mapping[str, str]) -> None:
        self.modules = {
            path: _Module(tree) for path, tree in parsed(dict(sources)).items()
        }
        self.trees = {path: module.tree for path, module in self.modules.items()}
        self.reader_returns: dict[str, list[ast.expr]] = {}
        for module in self.modules.values():
            for function in module.functions:
                if function.returns is not None and _mentions_spec(
                    function.returns, module.spec_words
                ):
                    self.reader_returns.setdefault(function.name, []).append(
                        function.returns
                    )
        self.readers = set(self.reader_returns)
        #: Every package function whose own annotation says it returns
        #: text, the formatter aside: a whole spec handed to one is turned
        #: into text there, whatever that function does with it inside.
        self.text_returners = {
            function.name
            for module in self.modules.values()
            for function in module.functions
            if _spells(function.returns) == str.__name__
            if function.returns is not None
        } - {FORMATTER_NAME}
        self.fields = {
            field
            for module in self.modules.values()
            for field in module.annotated_fields
        }
        self.payload_readers: set[str] = set()
        self.partials = through_partials(self.trees)
        while True:
            self.scopes = {path: self.scope(path) for path in self.modules}
            grown = (
                self._grow_payload_readers(self.scopes)
                or self._grow_fields(self.scopes)
                or self._grow_handed(self.scopes)
            )
            if not grown:
                return

    def roots(self) -> set[str]:
        """Every word through which a module can come to hold a spec."""
        return self.readers | self.fields | self.payload_readers | set(SPEC_TYPES)

    def _grow_payload_readers(self, scopes: "dict[str, _Scope]") -> bool:
        found = {
            name
            for path, module in self.modules.items()
            for name, value in module.returns
            if scopes[path].is_payload(value)
        }
        if found <= self.payload_readers:
            return False
        self.payload_readers |= found
        return True

    def _grow_fields(self, scopes: "dict[str, _Scope]") -> bool:
        """An attribute or a literal key a spec is stored under holds a spec.

        ``self._held = spec`` and ``state["held"] = spec`` hand the value on
        as plainly as an annotated field does.
        """
        found: set[str] = set()
        for path, module in self.modules.items():
            for node in module.expressions:
                if isinstance(node, ast.Assign):
                    targets, value = node.targets, node.value
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    targets, value = [node.target], node.value
                else:
                    continue
                if not scopes[path].is_spec(value):
                    continue
                for target in targets:
                    if isinstance(target, ast.Attribute):
                        found.add(target.attr)
                    elif (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant)
                        and isinstance(target.slice.value, str)
                    ):
                        found.add(target.slice.value)
        if found <= self.fields:
            return False
        self.fields |= found
        return True

    def _grow_handed(self, scopes: "dict[str, _Scope]") -> bool:
        """Seed each definition with the parameters a call hands a spec to."""
        grown = False
        for (path, _function), parameters in self.handed(scopes).items():
            module = self.modules.get(path)
            if module is None or parameters <= module.spec_parameters:
                continue
            module.spec_parameters |= parameters
            grown = True
        return grown

    def handed(
        self, scopes: "dict[str, _Scope]"
    ) -> dict[tuple[str, str], frozenset[str]]:
        """The parameters a call, or a ``functools.partial``, hands a spec to."""
        return parameters_receiving(
            self.partials,
            yields=lambda path, argument: scopes[path].is_spec(argument),
        )

    def scope(self, path: str) -> "_Scope":
        return _Scope(self, self.modules[path])

    def handed_parameters(self) -> dict[tuple[str, str], frozenset[str]]:
        return self.handed(self.scopes)


class _Scope:
    """The names one module binds to a spec, to an arm's text or its payload."""

    def __init__(self, package: _Package, module: _Module) -> None:
        self.package = package
        self.module = module
        self.specs = set(module.spec_parameters)
        self.payloads: set[str] = set()
        self.texts: set[str] = set()
        if not self.specs and module.words.isdisjoint(package.roots()):
            return
        while True:
            before = set(self.specs), set(self.payloads), set(self.texts)
            captured = self._captures()
            self.specs |= (
                captured[SPEC]
                | self._lambda_parameters()
                | self._narrowed()
                | self._filled()
            )
            self.payloads |= captured[PAYLOAD]
            self.texts |= captured[TEXT]
            self.specs |= bound_names(
                module.tree,
                yields=lambda value, _names: self.is_spec(value),
                seeds=self.specs,
            )
            self.specs |= self._unpacked(self.is_spec, stated=True)
            self.payloads |= bound_names(
                module.tree,
                yields=lambda value, _names: self.is_payload(value),
                seeds=self.payloads,
            )
            self.payloads |= self._unpacked(self.is_payload, stated=False)
            if before == (self.specs, self.payloads, self.texts):
                break

    def kind(self, node: ast.expr) -> str | None:
        """What an expression yields, as far as the walk is concerned."""
        if self.is_spec(node):
            return SPEC
        if self.reads_text(node):
            return TEXT
        return PAYLOAD if self.is_payload(node) else None

    def _kind_along(
        self, start: str | None, steps: tuple[PatternStep, ...]
    ) -> str | None:
        """What a capture holds, read off the path from the match subject.

        A class pattern that names an arm makes its position an arm whatever
        the subject was; under an arm, the text field is text, the payload
        field is payload, a spec-typed field is a spec, and any other field
        of the arm is none of them.  Every other step — a sequence item, a
        mapping value, a positional sub-pattern — keeps what its position
        held, as a loop over a collection of specs does.
        """
        kind = start
        for step in steps:
            if step.kind == "class":
                if any(
                    self.module.denotes(self.module.names, cls, SPEC_TYPES)
                    for cls in step.classes
                ):
                    kind = SPEC
            elif step.kind == "field":
                kind = self._field_kind(kind, step.field or "")
        return kind

    def _field_kind(self, kind: str | None, field: str) -> str | None:
        if field in self.package.fields:
            return SPEC
        if kind != SPEC:
            return kind
        if field in TEXT_FIELDS:
            return TEXT
        if field in PAYLOAD_FIELDS:
            return PAYLOAD
        return None if field in ARM_MODEL_FIELDS else SPEC

    def _captures(self) -> dict[str, set[str]]:
        """Every word a case binds to a spec, an arm's text or its payload."""
        found: dict[str, set[str]] = {SPEC: set(), TEXT: set(), PAYLOAD: set()}
        for statement in self.module.expressions:
            if not isinstance(statement, ast.Match):
                continue
            start = self.kind(statement.subject)
            for case in statement.cases:
                for name, steps in pattern_captures(case.pattern):
                    kind = self._kind_along(start, steps)
                    if kind is not None:
                        found[kind].add(name)
        return found

    def _unpacked(
        self, yields: Callable[[ast.expr], bool], *, stated: bool
    ) -> set[str]:
        """The names an unpacking target binds to a value *yields* recognises.

        Where the element is fixed, the element decides: an item of a tuple
        written out, or the slot a reader's own ``tuple[...]`` return
        annotation states, so ``spec, criteria = self._capture(key)`` binds
        ``spec`` and not ``criteria``.  Anywhere else the whole value decides,
        so ``for key, one in specs.items()`` binds both names.
        """
        found: set[str] = set()
        for one in self.module.unpacked:
            value = one.value.value if isinstance(one.value, ast.Await) else one.value
            if one.index is not None and not one.walks:
                slot = self._slot(value, one.index, yields, stated=stated)
                if slot is not None:
                    if slot:
                        found.add(one.name)
                    continue
            if yields(value):
                found.add(one.name)
        return found

    def _slot(
        self,
        value: ast.expr,
        index: int,
        yields: Callable[[ast.expr], bool],
        *,
        stated: bool,
    ) -> bool | None:
        """Whether one fixed element of *value* yields, or ``None`` if unknown."""
        if isinstance(value, ast.Tuple | ast.List) and not any(
            isinstance(one, ast.Starred) for one in value.elts
        ):
            return index < len(value.elts) and yields(value.elts[index])
        if not stated or not isinstance(value, ast.Call):
            return None
        returns = self.package.reader_returns.get(_spells(value.func) or "")
        if not returns:
            return None
        slots: list[bool] = []
        for annotation in returns:
            if not (
                isinstance(annotation, ast.Subscript)
                and _spells(annotation.value) == tuple.__name__
                and isinstance(annotation.slice, ast.Tuple)
                and len(annotation.slice.elts) > index
                and not any(
                    isinstance(one, ast.Constant) and one.value is Ellipsis
                    for one in annotation.slice.elts
                )
            ):
                return None
            slots.append(
                _mentions_spec(annotation.slice.elts[index], self.module.spec_words)
            )
        return any(slots)

    def _narrowed(self) -> set[str]:
        """The words a type test states to be an arm.

        ``isinstance(value, TrackerSpec)`` and ``type(value) is TrackerSpec``
        say what ``value`` is as plainly as an annotation does, whatever the
        word held before the test.
        """
        found: set[str] = set()
        for node in self.module.expressions:
            if (
                isinstance(node, ast.Call)
                and _spells(node.func) == isinstance.__name__
                and len(node.args) == 2
                and isinstance(node.args[0], ast.Name)
                and _mentions_spec(node.args[1], self.module.spec_words)
            ):
                found.add(node.args[0].id)
            elif (
                isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Call)
                and _spells(node.left.func) == type.__name__
                and len(node.left.args) == 1
                and isinstance(node.left.args[0], ast.Name)
                and any(
                    _mentions_spec(one, self.module.spec_words)
                    for one in node.comparators
                )
            ):
                found.add(node.left.args[0].id)
            elif isinstance(node, ast.Match) and isinstance(node.subject, ast.Name):
                # ``case TrackerSpec():`` tests the subject as ``isinstance``
                # does, and binds no word: the subject itself is the arm.
                if any(
                    self.module.denotes(self.module.names, cls, SPEC_TYPES)
                    for case in node.cases
                    for cls in pattern_classes(case.pattern)
                ):
                    found.add(node.subject.id)
        return found

    def _filled(self) -> set[str]:
        """The words a spec is put into, by a container method or a store.

        ``held.append(spec)``, ``held.extend(specs)``, ``held.update(k=spec)``
        and ``held[key] = spec`` each leave a spec in what ``held`` holds, so
        ``held`` is a collection of specs from then on, as a display written
        out with the spec in it already is.
        """
        found: set[str] = set()
        for node in self.module.expressions:
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.attr in CONTAINER_METHODS
                and any(
                    self.is_spec(one)
                    for one in (*node.args, *(k.value for k in node.keywords))
                )
            ):
                found.add(node.func.value.id)
            elif isinstance(node, ast.Assign) and self.is_spec(node.value):
                found.update(
                    target.value.id
                    for target in node.targets
                    if isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                )
        return found

    def _literal_words(self, node: ast.AST) -> set[str]:
        """Every name a literal spells: its identifiers, however it is held.

        A string constant, an f-string's text, a word bound to a string
        constant anywhere in the module, and the items or keys of a display
        written out of those: ``"body"``, ``"{0.body}"``, ``"$body"``,
        ``_FIELD`` after ``_FIELD = "body"``, and ``{"body"}`` alike.
        """
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value, *_IDENTIFIER.findall(node.value)}
        if isinstance(node, ast.JoinedStr):
            return {word for one in node.values for word in self._literal_words(one)}
        if isinstance(node, ast.Name):
            return {
                word
                for value in self.module.constants.get(node.id, ())
                for word in (value, *_IDENTIFIER.findall(value))
            }
        if isinstance(node, ast.Tuple | ast.List | ast.Set):
            return {word for one in node.elts for word in self._literal_words(one)}
        if isinstance(node, ast.Dict):
            return {
                word
                for one in node.keys
                if one is not None
                for word in self._literal_words(one)
            }
        return set()

    def _literal(self, node: ast.expr) -> str | None:
        """The one name a literal key or attribute name spells, if one."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            values = self.module.constants.get(node.id, set())
            return next(iter(values)) if len(values) == 1 else None
        return None

    def _lambda_parameters(self) -> set[str]:
        """The parameters of a lambda a spec is handed to.

        Called inline, bound to a word that is called, or handed beside a
        spec to the call that applies it (a sort key, a ``map``): each of its
        parameters may receive the spec.
        """
        found: set[str] = set()
        for call in self.module.calls:
            given = [*call.args, *(keyword.value for keyword in call.keywords)]
            if not any(self.is_spec(one) for one in given):
                continue
            lambdas = [one for one in given if isinstance(one, ast.Lambda)]
            if isinstance(call.func, ast.Lambda):
                lambdas.append(call.func)
            elif isinstance(call.func, ast.Name):
                lambdas.extend(self.module.lambdas.get(call.func.id, ()))
            for function in lambdas:
                found.update(argument.arg for argument in parameters_of(function))
        return found

    def _field(self, node: ast.AST) -> tuple[ast.expr, str] | None:
        """The receiver and the field name a load reaches, however spelled.

        ``x.f``, ``x["f"]``, ``x.get("f")`` and ``getattr(x, "f")`` alike.
        """
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            return node.value, node.attr
        keyed = self._keyed(node)
        if keyed is not None and keyed[1] is not None:
            return keyed[0], keyed[1]
        if isinstance(node, ast.Call) and node.args:
            reflector = self.module.names.denotes(node.func) or _spells(node.func)
            if reflector == getattr.__name__ and len(node.args) >= 2:
                name = self._literal(node.args[1])
                if name is not None:
                    return node.args[0], name
        return None

    def _keyed(self, node: ast.AST) -> tuple[ast.expr, str | None] | None:
        """The receiver and the literal key of a lookup: ``x[k]`` or ``x.get(k)``.

        The key is ``None`` when it is no string literal or word bound to
        one: an index, a slice, or a key computed at run time.
        """
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            receiver, key = node.value, node.slice
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOOKUPS
            and node.args
        ):
            receiver, key = node.func.value, node.args[0]
        else:
            return None
        return receiver, self._literal(key)

    def is_spec(self, node: ast.expr) -> bool:
        """Whether the expression yields one of the partition's arms."""
        if isinstance(node, ast.Name):
            return node.id in self.specs
        if isinstance(node, ast.Await | ast.Starred | ast.NamedExpr):
            return self.is_spec(node.value)
        if isinstance(node, ast.IfExp):
            return self.is_spec(node.body) or self.is_spec(node.orelse)
        if isinstance(node, ast.BoolOp):
            return any(self.is_spec(one) for one in node.values)
        if isinstance(node, ast.Tuple | ast.List | ast.Set):
            return any(self.is_spec(one) for one in node.elts)
        if isinstance(node, ast.Dict):
            # A ``**`` entry's value stands under the key ``None``.
            return any(self.is_spec(one) for one in node.values)
        if isinstance(node, ast.BinOp) and not isinstance(node.op, ast.Mod):
            # ``[spec] + rest``, ``held | {"k": spec}``: the operands'
            # items end up in what the operation builds.
            return self.is_spec(node.left) or self.is_spec(node.right)
        if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp):
            return self.is_spec(node.elt)
        if isinstance(node, ast.DictComp):
            return self.is_spec(node.value)
        keyed = self._keyed(node)
        if keyed is not None:
            # A lookup in a collection of specs takes a spec out of it, by any
            # index, slice or key, bracketed or through ``.get``; a literal
            # key naming a spec field is that field, and one naming another
            # arm field is that field.
            receiver, key = keyed
            if key in self.package.fields:
                return True
            return key not in ARM_MODEL_FIELDS and self.is_spec(receiver)
        field = self._field(node)
        if field is not None:
            # A reader reached by attribute — a property annotated to return
            # a spec — hands back a spec as a field annotated with one does.
            receiver, name = field
            return (
                name in self.package.fields
                or (isinstance(node, ast.Attribute) and name in self.package.readers)
                or (name == "__dict__" and self.is_spec(receiver))
            )
        if isinstance(node, ast.Call):
            return self._call_yields_spec(node)
        return False

    def _call_yields_spec(self, node: ast.Call) -> bool:
        """A reader, an arm's class or a method of it, a cast to one, a spec's
        own method, or a function from outside the package handed a spec.

        A builtin — a word the module binds nothing to and ``builtins``
        holds — or a function imported from outside the package hands back
        what it was given as far as this walk can tell, so
        ``next(iter(specs))``, ``sorted(specs)``, ``enumerate(specs)`` and
        ``copy.deepcopy(spec)`` are specs.  A package function states what it
        returns in its own annotation, which the strict type check requires,
        so a reader is found by its annotation.  A method of an arm's class —
        ``TrackerSpec.model_validate(raw)`` — builds an arm as the class call
        does.  A function from outside the package handed an arm's class
        rather than an arm — ``TypeAdapter(TrackerSpec)`` — builds what
        validates into an arm, so it is a spec source, and its methods
        (``.validate_python(raw)``) hand back an arm as a spec's own do.
        """
        func = node.func
        if _spells(func) in self.package.readers or self.module.denotes(
            self.module.names, func, SPEC_TYPES
        ):
            return True
        if self.module.denotes(self.module.casts, func, CASTS):
            return bool(node.args) and _mentions_spec(
                node.args[0], self.module.spec_words
            )
        if isinstance(func, ast.Attribute) and (
            self.is_spec(func.value)
            or self.module.denotes(self.module.names, func.value, SPEC_TYPES)
        ):
            return True
        if not self._outside_the_package(func):
            return False
        given = [*node.args, *(keyword.value for keyword in node.keywords)]
        if any(self.is_spec(one) for one in given):
            return True
        root = self._root(func)
        return (
            isinstance(root, ast.Name)
            and root.id in self.module.foreign
            and any(_mentions_spec(one, self.module.spec_words) for one in given)
        )

    @staticmethod
    def _root(func: ast.expr) -> ast.expr:
        """The word a callee's receiver chain starts from.

        Down attributes, the callee of a call and the value of a subscript:
        ``TypeAdapter(TrackerSpec).dump_python``, ``RootModel[FireSpec]`` and
        ``Template("$body").substitute`` all start from the foreign word.
        """
        root = func
        while isinstance(root, ast.Attribute | ast.Call | ast.Subscript):
            root = root.func if isinstance(root, ast.Call) else root.value
        return root

    def _outside_the_package(self, func: ast.expr) -> bool:
        """Whether *func* is a builtin or reached through a foreign import."""
        root = self._root(func)
        if not isinstance(root, ast.Name):
            return False
        if root.id in self.module.foreign:
            return True
        return (
            root is func
            and root.id not in self.module.bound
            and hasattr(builtins, root.id)
        )

    def is_payload(self, node: ast.expr) -> bool:
        """Whether the expression yields an arm field the formatter renders."""
        if isinstance(node, ast.Name):
            return node.id in self.payloads
        if isinstance(node, ast.Await):
            return self.is_payload(node.value)
        if isinstance(node, ast.IfExp):
            return self.is_payload(node.body) or self.is_payload(node.orelse)
        if isinstance(node, ast.Tuple | ast.List | ast.Set):
            return any(self.is_payload(one) for one in node.elts)
        if isinstance(node, ast.Dict):
            return any(self.is_payload(one) for one in node.values)
        if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp):
            return self.is_payload(node.elt)
        field = self._field(node)
        if field is not None:
            receiver, name = field
            return name in PAYLOAD_FIELDS and self.is_spec(receiver)
        if isinstance(node, ast.Call):
            # A reader of the payload, or a method of it: a copy or a dump of
            # the payload is the payload to the walk, as a spec's is a spec.
            return _spells(node.func) in self.package.payload_readers or (
                isinstance(node.func, ast.Attribute)
                and self.is_payload(node.func.value)
            )
        return False

    def reads_text(self, node: ast.AST) -> bool:
        """Whether this site loads an arm's own text.

        Off a spec, by the field's own name — as an attribute, a literal key,
        ``getattr`` with a literal, or any call handed a spec and a literal
        naming the field anywhere in its arguments or its receiver chain
        (``spec.__getattribute__("body")``,
        ``inspect.getattr_static(spec, "body")``,
        ``spec.model_dump(include={"body"})``, ``"{0.body}".format(spec)``,
        ``Template("$body").substitute(spec.model_dump())``) — or off a word
        a pattern captured the text into, which holds the same text.  A
        literal is a string constant or a word bound to one.
        """
        if isinstance(node, ast.Name):
            return node.id in self.texts and isinstance(node.ctx, ast.Load)
        field = self._field(node)
        if field is not None:
            return field[1] in TEXT_FIELDS and self.is_spec(field[0])
        if not isinstance(node, ast.Call):
            return False
        given = [*node.args, *(keyword.value for keyword in node.keywords)]
        chain = node.func
        while isinstance(chain, ast.Attribute | ast.Call | ast.Subscript):
            if isinstance(chain, ast.Call):
                if self.reads_text(chain):
                    # The read is the call further in; this one takes what
                    # it read, and one read is one site.
                    return False
                given.extend(chain.args)
                given.extend(keyword.value for keyword in chain.keywords)
                chain = chain.func
            else:
                given.append(chain.value)
                chain = chain.value
        named = {word for one in given for word in self._literal_words(one)}
        return bool(named & TEXT_FIELDS) and any(self.is_spec(one) for one in given)

    def _renders_whole(self, node: ast.expr) -> bool:
        return self.is_payload(node) or self.is_spec(node)

    def renders(self, node: ast.AST) -> bool:
        """Whether this site turns an arm into text without the formatter."""
        if isinstance(node, ast.Name | ast.Attribute | ast.Subscript):
            return self.reads_text(node)
        if isinstance(node, ast.FormattedValue):
            return self._renders_whole(node.value)
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Mod)
            and isinstance(node.left, ast.Constant | ast.JoinedStr)
        ):
            return self._renders_whole(node.right)
        if not isinstance(node, ast.Call):
            return False
        if self.reads_text(node):
            return True
        func = node.func
        given = [*node.args, *(keyword.value for keyword in node.keywords)]
        if isinstance(func, ast.Attribute) and self.is_spec(func.value):
            return func.attr in MODEL_RENDERS
        if any(
            self.module.denotes(self.module.names, one, RENDERERS | TEXT_BUILTINS)
            for one in given
        ):
            # A renderer handed as a value beside what it renders —
            # ``map(str, specs)``, ``sorted(specs, key=repr)`` — is applied
            # to it by the call that holds both.
            return any(self._renders_whole(one) for one in given)
        if (
            isinstance(func, ast.Call)
            and self.module.denotes(self.module.names, func.func, GETTERS)
            and any(
                isinstance(one, ast.Constant) and one.value in TEXT_FIELDS
                for one in func.args
            )
        ):
            return any(self.is_spec(one) for one in given)
        reached = (
            self.module.denotes(self.module.names, func, RENDERERS)
            or self.module.denotes(self.module.names, func, TEXT_BUILTINS)
            or (
                isinstance(func, ast.Attribute)
                and func.attr in TEMPLATE_FILLS
                and isinstance(func.value, ast.Constant | ast.JoinedStr)
            )
        )
        if reached:
            return any(self._renders_whole(one) for one in given)
        if self.module.denotes(self.module.names, func, GETTERS):
            # A getter built over the text field and handed to what applies
            # it — ``map(attrgetter("body"), specs)`` — is counted at the
            # call that hands it a spec, below.
            return False
        return self._applies_a_text_getter(node, given) or self._turned_into_text(
            node, given
        )

    def _turned_into_text(self, node: ast.Call, given: list[ast.expr]) -> bool:
        """A whole spec handed to a call whose stated return is text.

        A package function annotated ``-> str`` (the formatter aside), or a
        callable from outside the package — a builtin, a foreign import, a
        method of a string literal or of what a foreign class builds — whose
        own annotation or typeshed stub says ``str``:
        ``json.dumps(spec.model_dump())``, ``"".join(...)``,
        ``template.render({"task_md": spec})``.  Only the innermost such call
        is the site, so a text read already inside what it is handed is not
        counted twice.  The payload's own serialisation handed on is the
        record of the payload the stated limits leave silent, so only a whole
        spec counts here.
        """
        if not any(self.is_spec(one) for one in given):
            return False
        if not (
            _spells(node.func) in self.package.text_returners
            and not self.module.denotes(
                self.module.names, node.func, frozenset({FORMATTER_NAME})
            )
        ):
            dotted = self._callee_path(node.func)
            if dotted is None or not returns_text(dotted):
                return False
        return not any(
            self.renders(inner)
            for one in given
            for inner in ast.walk(one)
            if isinstance(inner, ast.expr)
        )

    def _callee_path(self, func: ast.expr) -> str | None:
        """The dotted path of a callable from outside the package, if one."""
        if isinstance(func, ast.Name):
            if func.id in self.module.origins:
                return self.module.origins[func.id]
            if func.id not in self.module.bound and hasattr(builtins, func.id):
                return f"{builtins.__name__}.{func.id}"
            return None
        if not isinstance(func, ast.Attribute):
            return None
        receiver = func.value
        if isinstance(receiver, ast.JoinedStr) or (
            isinstance(receiver, ast.Constant) and isinstance(receiver.value, str)
        ):
            return f"{builtins.__name__}.{str.__name__}.{func.attr}"
        owner = self._callee_path(
            receiver.func if isinstance(receiver, ast.Call) else receiver
        )
        return None if owner is None else f"{owner}.{func.attr}"

    def _applies_a_text_getter(self, node: ast.Call, given: list[ast.expr]) -> bool:
        getters = [
            one
            for one in given
            if isinstance(one, ast.Call)
            and self.module.denotes(self.module.names, one.func, GETTERS)
            and any(
                isinstance(word, ast.Constant) and word.value in TEXT_FIELDS
                for word in one.args
            )
        ]
        return bool(getters) and any(
            self.is_spec(one) for one in given if all(one is not g for g in getters)
        )

    def is_digest(self, node: ast.AST) -> bool:
        """Whether this call hands an arm's text to the one digest function.

        Resolved through the digest's own home module, so a local definition
        of the same word is not it; and by shape, so a second argument, a
        keyword or anything but a bare text read is a reading of the bytes
        rather than a hashing of them.
        """
        return (
            isinstance(node, ast.Call)
            and self.module.digest.denotes(node.func) == DIGEST
            and len(node.args) == 1
            and not node.keywords
            and self.reads_text(node.args[0])
        )

    def digest_sites(self) -> list[str]:
        """The definitions in this module that hash an arm's text."""
        return [
            self.module.where[id(call)]
            for call in self.module.calls
            if self.is_digest(call)
        ]

    def render_sites(self) -> list[str]:
        """The definitions in this module that read an arm's text themselves."""
        hashed = {
            id(call.args[0]) for call in self.module.calls if self.is_digest(call)
        }
        return [
            self.module.where[id(node)]
            for node in self.module.expressions
            if self.renders(node) and id(node) not in hashed
        ]


def _report(sources: Mapping[str, str]) -> dict[str, dict[str, tuple[str, ...]]]:
    """Where an arm's text is read, and where it is only hashed.

    ``{"read": {module: definitions}, "digested": {module: definitions}}``,
    the formatter's own module excluded and a module with neither omitted.
    One entry per site, so a definition holding two sites is named twice.
    """
    package = _Package(sources)
    found: dict[str, dict[str, tuple[str, ...]]] = {"read": {}, "digested": {}}
    for path in sources:
        if path == FORMATTER:
            continue
        scope = package.scopes[path]
        for kind, sites in (
            ("read", scope.render_sites()),
            ("digested", scope.digest_sites()),
        ):
            if sites:
                found[kind][path] = tuple(sorted(sites))
    return found


PACKAGE = source_tree()
PARSED = parsed(PACKAGE)

#: The two positions where an arm's text leaves the formatter's reach without
#: being rendered: a record pins the subject it entered on, and the entry
#: compares that pin with the subject it just read.  A digest of the
#: bytes renders nothing, so it is counted apart from a read.  A third digest
#: position is a decision recorded here, not a convenience, and nothing is
#: re-routed through the formatter because that would change a record
#: digest's bytes.
DIGEST_POSITIONS = {
    "chains/ralph_loop.py": ("RalphLoop._lane_binding",),
    "domain/lane_entry.py": ("require_unamended_subject",),
}

#: The two positions that read an arm's text without handing it to the
#: formatter or the digest.  An answer is measured against the deliverables the
#: subject's own text states, so the section has to be read to be measured
#: against (KOD-629); it renders nothing and reaches no prompt.  And a stalled
#: pull request is titled by the spec's own title, a ticket's title or a tracker
#: subject's key, through the formatter module's ``fire_spec_title`` (KOD-1261).
#: A third entry here is a decision recorded in this comment, not a convenience.
TEXT_READS = {
    "chains/authored_publication.py": ("AuthoredPublication.open_stalled_pr",),
    "services/fire_time_rulings.py": ("FireTimeRulings.rule",),
}


def _control(source: str) -> tuple[int, int]:
    """How many reads and digests a control module adds to the scan."""
    report = _report({**PACKAGE, "control.py": source})
    return (
        len(report["read"].get("control.py", ())),
        len(report["digested"].get("control.py", ())),
    )


def test_no_module_beside_the_formatter_reads_an_arm_itself():
    """Every consumer reaches the arm through the formatter, or only hashes it.

    The roots are derived: a function whose return annotation names a spec
    type, a class field annotated with one, a state key of that name, a
    parameter annotated with one, and a parameter some call in the package
    hands such a value to.  From each, the names a module binds grow to a
    fixed point, and every read of the arm's own text off one of them is a
    site.
    """
    assert _report(PACKAGE) == {"read": TEXT_READS, "digested": DIGEST_POSITIONS}


def test_the_partition_is_reached_by_every_kind_of_root():
    """The scan is not vacuous: each kind of root is populated at head.

    The call edge is walked at head and finds parameters, but every one of
    them is already annotated with a spec type, so it seeds nothing the
    annotations did not already seed.  That is why the probe this root exists
    for had to be planted: an unannotated parameter handed a spec is a shape
    the package does not currently write, and the control below is the only
    place the root does work on its own.
    """
    package = _Package(PACKAGE)
    handed = package.handed_parameters()
    beyond_annotations = {
        key: parameters
        - set(annotated_parameters(package.modules[key[0]].tree, mentioning=SPEC_TYPES))
        for key, parameters in handed.items()
        if key[0] in package.modules
    }
    annotated = {path for path in PACKAGE if package.modules[path].spec_parameters}
    keyed = {
        path
        for path in PACKAGE
        if any(
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value in package.fields
            for node in package.modules[path].expressions
        )
    }

    assert package.readers
    assert package.fields
    assert package.payload_readers
    assert annotated
    assert keyed
    assert handed
    assert {key: extra for key, extra in beyond_annotations.items() if extra} == {}


def test_the_formatter_is_the_render_every_holder_reaches():
    """The formatter is called, so an empty read map is a fact about the tree."""
    reached = {
        site.module for site in call_sites(PARSED, names={format_fire_spec.__name__})
    }

    assert reached >= {"chains/fire_implementation.py", "services/fire_time_rulings.py"}
    assert FORMATTER not in reached


def test_the_scan_catches_a_spec_held_by_an_annotated_parameter():
    control = (
        "from kodezart.types.domain.fire_spec import TrackerSpec\n"
        "\n"
        "def subject_text(spec: TrackerSpec) -> str:\n"
        "    return spec.body\n"
    )
    assert _control(control) == (1, 0)


def test_the_scan_catches_a_spec_held_by_a_model_field():
    control = "def subject_text(request):\n    return request.original_spec.body\n"
    assert _control(control) == (1, 0)


def test_the_scan_catches_a_spec_held_by_a_state_key_or_a_port_read():
    # The port read this name refers to no longer answers a spec: KOD-710 moved
    # the composition to the stage that is its one caller, so no port member
    # returns one.  The second root is therefore the reader's return, which is
    # the shape that remains, and the two reads below are still two roots.
    control = (
        "from kodezart.domain.workflow_state import original_fire_spec\n"
        "\n"
        "async def node(state, tracker):\n"
        '    held = state["fire_spec"]\n'
        "    fetched = original_fire_spec(state)\n"
        "    return held.body, fetched.body\n"
    )
    assert _control(control) == (2, 0)


def test_the_boundary_catches_a_module_rendering_an_arm_itself():
    control = (
        "def node(state):\n"
        "    spec = current_fire_spec(state)\n"
        "    return format_ticket_as_task(spec.ticket), spec.body\n"
    )
    assert _control(control) == (2, 0)


def test_the_scan_catches_the_renderer_reached_under_another_name():
    control = (
        "from kodezart.domain import ticket\n"
        "from kodezart.domain.ticket import format_ticket_as_task as render\n"
        "\n"
        "def node(state):\n"
        "    return render(current_ticket(state)), ticket.format_ticket_as_task(\n"
        "        current_ticket(state),\n"
        "    )\n"
    )
    assert _control(control) == (2, 0)


def test_the_scan_catches_a_whole_spec_rendered_by_str_or_an_f_string():
    """The two forms that render a spec without naming a field of it.

    Neither reaches the arm's text through the formatter, and what each
    renders is the model's own repr rather than the text, which is why they
    are counted here and not left to the field read.
    """
    control = (
        "from kodezart.types.domain.fire_spec import AuthoredSpec\n"
        "\n"
        "def rendered(spec: AuthoredSpec) -> str:\n"
        '    return f"{spec}" + str(spec.ticket)\n'
    )
    assert _control(control) == (2, 0)


def test_the_scan_catches_an_arm_text_read_inside_a_comprehension():
    """A name taken one at a time out of a collection of specs is a spec.

    The comprehension's own target is the loop target written another way, so
    the read inside it is the same read the loop's body would have made.
    """
    control = (
        "from kodezart.types.domain.fire_spec import TrackerSpec\n"
        "\n"
        "def subject_texts(specs: tuple[TrackerSpec, ...]) -> list[str]:\n"
        "    return [one.body for one in specs]\n"
    )
    assert _control(control) == (1, 0)


def test_the_scan_catches_a_class_pattern_capturing_the_arm_text():
    """The formatter's own idiom beside the formatter is a read.

    A pattern that captures the text field binds the text to a word, and the
    word is read where the arm's own field would have been.
    """
    control = (
        "from kodezart.types.domain.fire_spec import TrackerSpec\n"
        "\n"
        "def subject_text(spec: TrackerSpec) -> str:\n"
        "    match spec:\n"
        "        case TrackerSpec(body=text):\n"
        "            return text\n"
        "        case _:\n"
        '            return ""\n'
    )
    assert _control(control) == (1, 0)


@pytest.mark.parametrize(
    ("cases", "expected"),
    [
        (
            "        case TrackerSpec() as arm:\n"
            "            return arm.body\n"
            "        case _:\n"
            '            return ""\n',
            (1, 0),
        ),
        (
            "        case (TrackerSpec() | AuthoredSpec()) as arm:\n"
            "            return arm.body\n"
            "        case _:\n"
            '            return ""\n',
            (1, 0),
        ),
        ("        case other:\n            return other.body\n", (1, 0)),
        (
            "        case TrackerSpec() as arm:\n"
            "            return arm.subject\n"
            "        case _:\n"
            '            return ""\n',
            (0, 0),
        ),
    ],
    ids=[
        "the_arm_captured_by_as",
        "the_arm_captured_over_an_alternation",
        "the_subject_captured_by_a_bare_word",
        "a_field_of_the_capture_that_is_not_the_text",
    ],
)
def test_a_class_pattern_capturing_the_whole_arm_captures_the_arm(cases, expected):
    """The capture beside the keyword one, one token away and the same read.

    A case that binds the subject rather than a field of it holds the arm, so
    ``.body`` off the captured word loads the arm's own text exactly where the
    field read would have; an alternation and a bare word bind the subject the
    same way.  A field that is not the text is no read off the capture either,
    so the capture reports the field it is asked about rather than itself.
    """
    control = (
        "from kodezart.types.domain.fire_spec import AuthoredSpec, TrackerSpec\n"
        "\n"
        "def subject_text(spec: TrackerSpec) -> str:\n"
        "    match spec:\n"
        f"{cases}"
    )
    assert _control(control) == expected


def test_a_captured_arm_text_handed_to_the_digest_is_a_digest():
    """The capture is text wherever it goes, so hashing it hashes the text."""
    control = (
        f"from {DIGEST_HOME} import {DIGEST}\n"
        "from kodezart.types.domain.fire_spec import TrackerSpec\n"
        "\n"
        "def pinned(spec: TrackerSpec) -> str:\n"
        "    match spec:\n"
        "        case TrackerSpec(body=text):\n"
        f"            return {DIGEST}(text)\n"
        "        case _:\n"
        '            return ""\n'
    )
    assert _control(control) == (0, 1)


PROBE = (
    "def _own_text(value):\n"
    "    return (\n"
    "        value.body\n"
    "        if isinstance(value, TrackerSpec)\n"
    "        else format_fire_spec(value)\n"
    "    )\n"
)
IMPLEMENTATION = "chains/fire_implementation.py"
IMPLEMENTATION_ANCHOR = "        task_md = format_fire_spec(spec)\n"


def test_the_scan_catches_a_spec_handed_to_an_unannotated_parameter_in_the_tree():
    """The probe that walked past the annotation-only roots, in a real module.

    The helper is written beside the consumer and takes its spec from the
    consumer's own call, so the consumer is no longer the site: the helper
    is, and the report names it.
    """
    sources = dict(PACKAGE)
    assert sources[IMPLEMENTATION].count(IMPLEMENTATION_ANCHOR) == 1
    sources[IMPLEMENTATION] = (
        sources[IMPLEMENTATION].replace(
            IMPLEMENTATION_ANCHOR, "        task_md = _own_text(spec)\n"
        )
        + "\n\n"
        + PROBE
    )

    report = _report(sources)

    assert report["read"] == {**TEXT_READS, IMPLEMENTATION: ("_own_text",)}
    assert report["digested"] == DIGEST_POSITIONS


PROMPT_ANCHOR = ').render({"task_md": task_md})'


def test_the_whole_arm_handed_to_the_implementation_prompt_is_a_render():
    """The package's own prompt idiom, handed the arm instead of its text.

    The arm stands under a key of a dict display, and the template's
    ``render`` states that it returns text, so the arm is turned into the
    prompt's text there without the formatter.  The walk follows the arm
    into ``render`` too, where the merged bindings are handed to
    ``render_template``, which states the same: two sites, one per call.
    """
    sources = dict(PACKAGE)
    assert sources[IMPLEMENTATION].count(PROMPT_ANCHOR) == 1
    sources[IMPLEMENTATION] = sources[IMPLEMENTATION].replace(
        PROMPT_ANCHOR, ').render({"task_md": spec})'
    )

    report = _report(sources)

    assert report["read"] == {
        **TEXT_READS,
        IMPLEMENTATION: ("FireImplementation.run_ralph_loop",),
        "core/prompt_rendering.py": ("PromptTemplate.render",),
    }


CONTROL_IMPORT = "from kodezart.control import _own_text"


@pytest.mark.parametrize(
    ("form", "imported", "call", "probe", "reported"),
    [
        ("positional", CONTROL_IMPORT, "_own_text(spec)", PROBE, "_own_text"),
        ("keyword", CONTROL_IMPORT, "_own_text(value=spec)", PROBE, "_own_text"),
        (
            "through_a_receiver",
            CONTROL_IMPORT,
            "self._own_text(spec)",
            "class Reader:\n"
            "    def _own_text(self, value):\n"
            "        return value.body\n",
            "Reader._own_text",
        ),
        (
            "aliased_import",
            "from kodezart.control import _own_text as h",
            "h(spec)",
            PROBE,
            "_own_text",
        ),
        (
            "module_alias",
            "import kodezart.control as helpers",
            "helpers._own_text(spec)",
            PROBE,
            "_own_text",
        ),
        (
            "submodule_import",
            "from kodezart import control",
            "control._own_text(spec)",
            PROBE,
            "_own_text",
        ),
    ],
)
def test_the_scan_catches_a_spec_handed_to_an_unannotated_parameter(
    form, imported, call, probe, reported
):
    """The helper is scanned wherever it lives and however it is reached."""
    caller = (
        f"{imported}\n"
        "\n"
        "def node(state):\n"
        "    spec = current_fire_spec(state)\n"
        f"    return {call}\n"
    )
    report = _report({**PACKAGE, "caller.py": caller, "control.py": probe})

    assert report["read"] == {**TEXT_READS, "control.py": (reported,)}


def test_a_spec_handed_on_through_two_unannotated_helpers_is_followed():
    """The chain is walked to the helper that reads, not stopped at the first.

    ``helper1`` takes the spec from the consumer's call and hands it on
    without reading it; ``helper2`` reads the arm's text.  The re-walk over
    the grown scopes is what seeds ``helper2``, so it is the only site.
    """
    caller = (
        "from kodezart.control import helper1\n"
        "\n"
        "def node(state):\n"
        "    spec = current_fire_spec(state)\n"
        "    return helper1(spec)\n"
    )
    control = (
        "def helper1(first):\n"
        "    return helper2(first)\n"
        "\n"
        "def helper2(second):\n"
        "    return second.body\n"
    )
    report = _report({**PACKAGE, "caller.py": caller, "control.py": control})

    assert report["read"] == {**TEXT_READS, "control.py": ("helper2",)}


@pytest.mark.parametrize(
    ("shape", "body", "expected"),
    [
        ("digest_alone", f"    return {DIGEST}(spec.body)\n", (0, 1)),
        (
            "digest_beside_a_read",
            f"    return {DIGEST}(spec.body) + spec.body\n",
            (1, 1),
        ),
        ("length", "    return len(spec.body)\n", (1, 0)),
        ("digest_with_a_salt", f"    return {DIGEST}(spec.body, salt)\n", (1, 0)),
    ],
)
def test_a_digest_of_the_arm_text_is_a_digest_and_anything_else_is_a_read(
    shape, body, expected
):
    """Only the digest's own one-argument shape hashes; the rest is a read."""
    control = (
        f"from {DIGEST_HOME} import {DIGEST}\n"
        "from kodezart.types.domain.fire_spec import TrackerSpec\n"
        "\n"
        "def pin(spec: TrackerSpec, salt):\n"
        f"{body}"
    )
    assert _control(control) == expected


@pytest.mark.parametrize(
    ("origin", "control", "expected"),
    [
        (
            "aliased_import",
            f"from {DIGEST_HOME} import {DIGEST} as pin\n"
            "from kodezart.types.domain.fire_spec import TrackerSpec\n"
            "\n"
            "def held(spec: TrackerSpec):\n"
            "    return pin(spec.body)\n",
            (0, 1),
        ),
        (
            "local_definition",
            "from kodezart.types.domain.fire_spec import TrackerSpec\n"
            "\n"
            f"def {DIGEST}(body):\n"
            "    return body\n"
            "\n"
            "def held(spec: TrackerSpec):\n"
            f"    return {DIGEST}(spec.body)\n",
            (1, 0),
        ),
    ],
)
def test_a_digest_under_another_name_is_still_a_digest_and_a_local_one_is_not(
    origin, control, expected
):
    """The exemption keys on the function, not on the word that spells it."""
    assert _control(control) == expected


def test_a_spec_field_read_that_is_not_the_arm_text_is_no_site():
    control = (
        "from kodezart.types.domain.fire_spec import TrackerSpec\n"
        "\n"
        "def addressed(spec: TrackerSpec):\n"
        "    return spec.subject, spec.criteria\n"
    )
    assert _control(control) == (0, 0)


ARM_IMPORT = "from kodezart.types.domain.fire_spec import AuthoredSpec, TrackerSpec\n\n"

#: Every other ordinary spelling of a read beside the formatter, one module
#: each, keyed by what it plants.  Each row stands for one widening of the
#: walk, so undoing that widening turns its row red.
PLANTED_READS = {
    # A capture anywhere in a pattern, read off where it stands.
    "text_captured_through_a_positional_sub_pattern": ARM_IMPORT
    + "def f(spec: TrackerSpec) -> str:\n"
    "    match spec:\n"
    "        case TrackerSpec(body=str(text)):\n"
    "            return text\n"
    "        case _:\n"
    "            return ''\n",
    "text_captured_through_an_or_pattern": ARM_IMPORT
    + "def f(spec: TrackerSpec) -> str:\n"
    "    match spec:\n"
    "        case TrackerSpec(body=('' as text) | text):\n"
    "            return text\n"
    "        case _:\n"
    "            return ''\n",
    "arm_captured_inside_a_sequence": ARM_IMPORT + "def f(spec):\n"
    "    match (spec,):\n"
    "        case (TrackerSpec() as arm,):\n"
    "            return arm.body\n",
    "arm_captured_under_a_spec_field": (
        "from kodezart.types.domain.fire_spec import TrackerSpec\n"
        "from kodezart.types.domain.workflow import RemediationRequest\n"
        "\n"
        "def f(request: RemediationRequest):\n"
        "    match request:\n"
        "        case RemediationRequest(original_spec=TrackerSpec() as arm):\n"
        "            return arm.body\n"
    ),
    "spec_field_captured_bare": (
        "from kodezart.types.domain.workflow import RemediationRequest\n"
        "\n"
        "def f(request: RemediationRequest):\n"
        "    match request:\n"
        "        case RemediationRequest(original_spec=held):\n"
        "            return held.body\n"
    ),
    "arm_captured_over_a_subject_that_is_no_spec": ARM_IMPORT
    + "def f(raw):\n    match raw:\n        case TrackerSpec() as arm:\n"
    "            return arm.body\n",
    "text_captured_over_a_subject_that_is_no_spec": ARM_IMPORT
    + "def f(raw):\n    match raw:\n        case TrackerSpec(body=text):\n"
    "            return text\n",
    "text_captured_by_a_class_that_names_no_arm_over_a_spec": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    match spec:\n        case object(body=text):\n"
    "            return text\n",
    "text_captured_by_a_mapping_key": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    match spec.model_dump():\n"
    "        case {'body': text}:\n            return text\n",
    "arm_captured_by_a_sequence_item_beside_a_star": ARM_IMPORT
    + "def f(specs: list[TrackerSpec]):\n    match specs:\n"
    "        case [first, *rest]:\n            return first.body\n",
    "arm_captured_by_a_starred_rest": ARM_IMPORT
    + "def f(specs: list[TrackerSpec]):\n    match specs:\n"
    "        case [*rest]:\n            return rest[0].body\n",
    "arm_captured_by_a_mapping_value_under_a_key": ARM_IMPORT
    + "def f(specs: dict[str, TrackerSpec]):\n    match specs:\n"
    "        case {'k': one}:\n            return one.body\n",
    "payload_captured_and_rendered": ARM_IMPORT
    + "from kodezart.domain.ticket import format_ticket_as_task\n\n"
    "def f(spec: AuthoredSpec):\n    match spec:\n"
    "        case AuthoredSpec(ticket=draft):\n"
    "            return format_ticket_as_task(draft)\n",
    # The arm class under another word.
    "arm_class_imported_under_an_alias_inside_the_function": (
        "def f(spec):\n"
        "    from kodezart.types.domain.fire_spec import TrackerSpec as Arm\n"
        "    match [spec][0]:\n"
        "        case Arm() as arm:\n"
        "            return arm.body\n"
    ),
    "arm_class_rebound_by_an_assignment": (
        "from kodezart.types.domain.fire_spec import TrackerSpec\n\n"
        "Arm = TrackerSpec\n\n"
        "def f(raw):\n    match raw:\n        case Arm() as arm:\n"
        "            return arm.body\n"
    ),
    "arm_class_reached_through_a_module_alias": (
        "import kodezart.types.domain.fire_spec as fs\n\n"
        "def f(raw):\n    match raw:\n        case fs.TrackerSpec() as arm:\n"
        "            return arm.body\n"
    ),
    "parameter_annotated_with_an_aliased_arm": (
        "from kodezart.types.domain.fire_spec import TrackerSpec as Arm\n\n"
        "def f(spec: Arm):\n    return spec.body\n"
    ),
    # A spec taken out of a collection of specs.
    "integer_index": ARM_IMPORT
    + "def f(specs: tuple[TrackerSpec, ...]) -> str:\n    return specs[0].body\n",
    "slice": ARM_IMPORT
    + "def f(specs: tuple[TrackerSpec, ...]):\n    for one in specs[1:]:\n"
    "        return one.body\n",
    "comprehension_of_specs_indexed": ARM_IMPORT
    + "def f(specs: tuple[TrackerSpec, ...]):\n"
    "    return [one for one in specs if one][0].body\n",
    "dict_comprehension_of_specs": ARM_IMPORT
    + "def f(specs: dict[str, TrackerSpec]):\n"
    "    return {key: one for key, one in specs.items()}['k'].body\n",
    "walrus_over_an_index": ARM_IMPORT
    + "def f(specs: tuple[TrackerSpec, ...]):\n    return (one := specs[0]).body\n",
    "mapping_values": ARM_IMPORT
    + "def f(specs: dict[str, TrackerSpec]) -> list[str]:\n"
    "    return [one.body for one in specs.values()]\n",
    "mapping_get": ARM_IMPORT
    + "def f(specs: dict[str, TrackerSpec]):\n    return specs.get('k').body\n",
    "mapping_items_unpacked": ARM_IMPORT
    + "def f(specs: dict[str, TrackerSpec]):\n    for key, one in specs.items():\n"
    "        return one.body\n",
    "enumerate_unpacked": ARM_IMPORT + "def f(specs: tuple[TrackerSpec, ...]):\n"
    "    for i, one in enumerate(specs):\n        return one.body\n",
    "zip_unpacked": ARM_IMPORT + "def f(specs: tuple[TrackerSpec, ...], keys):\n"
    "    for key, one in zip(keys, specs):\n        return one.body\n",
    "next_over_iter": ARM_IMPORT
    + "def f(specs: tuple[TrackerSpec, ...]):\n    return next(iter(specs)).body\n",
    "tuple_written_out_and_unpacked": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    first, second = spec, 1\n"
    "    return first.body\n",
    # The text field named by a literal.
    "getattr_with_a_literal": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return getattr(spec, 'body')\n",
    "literal_key_off_a_dump": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return spec.model_dump()['body']\n",
    "literal_key_off_vars": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return vars(spec)['body']\n",
    "literal_key_off_the_instance_dict": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return spec.__dict__['body']\n",
    "literal_key_through_get": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return spec.model_dump().get('body', '')\n",
    "text_field_named_to_the_instance_s_own_lookup": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return spec.__getattribute__('body')\n",
    "text_field_named_to_a_reflector_from_outside_the_package": ARM_IMPORT
    + "import inspect\n\n"
    "def f(spec: TrackerSpec):\n    return inspect.getattr_static(spec, 'body')\n",
    "attrgetter_applied": ARM_IMPORT + "from operator import attrgetter\n\n"
    "def f(spec: TrackerSpec):\n    return attrgetter('body')(spec)\n",
    "attrgetter_mapped": ARM_IMPORT + "import operator\n\n"
    "def f(specs: tuple[TrackerSpec, ...]):\n"
    "    return list(map(operator.attrgetter('body'), specs))\n",
    # A spec handed on.
    "lambda_mapped": ARM_IMPORT + "def f(specs: tuple[TrackerSpec, ...]):\n"
    "    return list(map(lambda s: s.body, specs))\n",
    "lambda_called_inline": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return (lambda s: s.body)(spec)\n",
    "lambda_bound_and_called": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    get = lambda s: s.body\n    return get(spec)\n",
    "partial_through_a_module_alias": ARM_IMPORT + "import functools\n\n"
    "def helper(value):\n    return value.body\n\n"
    "def f(spec: TrackerSpec):\n    return functools.partial(helper, spec)()\n",
    "partial_imported_under_an_alias": ARM_IMPORT
    + "from functools import partial as bind\n\n"
    "def helper(value):\n    return value.body\n\n"
    "def f(spec: TrackerSpec):\n    return bind(helper, value=spec)()\n",
    "state_key_read_through_get": "def f(state):\n"
    "    held = state.get('fire_spec')\n    return held.body\n",
    "arm_rebuilt_by_a_method_of_its_class": ARM_IMPORT
    + "def f(raw):\n    return TrackerSpec.model_validate(raw).body\n",
    "spec_handed_through_a_function_from_outside_the_package": ARM_IMPORT
    + "import copy\n\n"
    "def f(spec: TrackerSpec):\n    return copy.deepcopy(spec).body\n",
    "spec_handed_through_a_from_import_outside_the_package": ARM_IMPORT
    + "from copy import deepcopy as dup\n\n"
    "def f(spec: TrackerSpec):\n    return dup(spec).body\n",
    "spec_field_named_to_getattr": "def f(request):\n"
    "    return getattr(request, 'original_spec').body\n",
    "payload_field_named_to_getattr": ARM_IMPORT
    + "from kodezart.domain.ticket import format_ticket_as_task\n\n"
    "def f(spec: AuthoredSpec):\n"
    "    return format_ticket_as_task(getattr(spec, 'ticket'))\n",
    "attribute_the_spec_is_stored_under": ARM_IMPORT + "class Holder:\n"
    "    def __init__(self, spec: TrackerSpec) -> None:\n"
    "        self._held_arm = spec\n\n"
    "    def text(self):\n        return self._held_arm.body\n",
    # A type test or a cast restating what a value is.
    "isinstance_narrowing": ARM_IMPORT
    + "def f(raw: object):\n    if isinstance(raw, TrackerSpec):\n"
    "        return raw.body\n    return ''\n",
    "type_identity_narrowing": ARM_IMPORT
    + "def f(raw: object):\n    if type(raw) is TrackerSpec:\n"
    "        return raw.body\n",
    "cast": ARM_IMPORT + "from typing import cast\n\n"
    "def f(raw: object):\n    return cast(TrackerSpec, raw).body\n",
    # A whole spec turned into text.
    "str_of_the_spec": ARM_IMPORT + "def f(spec: TrackerSpec):\n    return str(spec)\n",
    "repr_of_the_spec": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return repr(spec)\n",
    "format_of_the_spec": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return format(spec)\n",
    "the_model_s_own_json_render": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return spec.model_dump_json()\n",
    "percent_formatting": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return '%s' % spec\n",
    "str_format": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return '{}'.format(spec)\n",
    "str_format_map": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return '{body}'.format_map(spec.model_dump())\n",
    "text_builtin_mapped_over_specs": ARM_IMPORT
    + "def f(specs: tuple[TrackerSpec, ...]):\n    return ''.join(map(str, specs))\n",
    "renderer_mapped_over_payloads": ARM_IMPORT
    + "from kodezart.domain.ticket import format_ticket_as_task\n\n"
    "def f(specs: tuple[AuthoredSpec, ...]):\n"
    "    return list(map(format_ticket_as_task, [one.ticket for one in specs]))\n",
    "payload_rendered_inside_a_tuple": ARM_IMPORT
    + "def f(spec: AuthoredSpec):\n    return str((spec.ticket, 1))\n",
    "payload_rendered_inside_a_dict_display": ARM_IMPORT
    + "def f(spec: AuthoredSpec):\n    return str({'k': spec.ticket})\n",
    "payload_copied_then_rendered": ARM_IMPORT
    + "def f(spec: AuthoredSpec):\n    return str(spec.ticket.model_copy())\n",
    # A collection filled by a container method, a store or an operator.
    "spec_appended_to_a_list": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    held_list = []\n    held_list.append(spec)\n"
    "    return held_list[0].body\n",
    "specs_extended_into_a_list": ARM_IMPORT
    + "def f(specs: tuple[TrackerSpec, ...]):\n    held_ext = []\n"
    "    held_ext.extend(specs)\n    return held_ext[0].body\n",
    "spec_updated_into_a_mapping": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    held_map = {}\n    held_map.update(k=spec)\n"
    "    return held_map['k'].body\n",
    "spec_stored_under_a_computed_key": ARM_IMPORT
    + "def f(spec: TrackerSpec, key):\n    held_store = {}\n"
    "    held_store[key] = spec\n    return held_store[key].body\n",
    "specs_concatenated": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return ([spec] + [])[0].body\n",
    "spec_added_to_a_tuple_by_concatenation": ARM_IMPORT
    + "def f(spec: TrackerSpec, rest):\n    return (rest + (spec,))[-1].body\n",
    "spec_under_a_key_of_a_dict_display": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return {'k': spec}['k'].body\n",
    "specs_merged_by_a_double_star_entry": ARM_IMPORT
    + "def f(specs: dict[str, TrackerSpec]):\n    return {**specs}['k'].body\n",
    # The text field named by a literal held elsewhere than at the read.
    "text_field_named_by_a_module_constant": ARM_IMPORT
    + "_TEXT_FIELD_NAME = 'body'\n\n"
    "def f(spec: TrackerSpec):\n    return getattr(spec, _TEXT_FIELD_NAME)\n",
    "text_key_named_by_a_module_constant": ARM_IMPORT + "_TEXT_KEY_NAME = 'body'\n\n"
    "def f(spec: TrackerSpec):\n    return spec.model_dump()[_TEXT_KEY_NAME]\n",
    "text_field_named_in_an_include_set": ARM_IMPORT + "def f(spec: TrackerSpec):\n"
    "    return ''.join(spec.model_dump(include={'body'}).values())\n",
    "text_field_named_in_a_format_field": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return '{0.body}'.format(spec)\n",
    "text_field_named_in_a_template_placeholder": ARM_IMPORT + "import string\n\n"
    "def f(spec: TrackerSpec):\n"
    "    return string.Template('$body').substitute(spec.model_dump())\n",
    "text_field_named_by_a_module_constant_to_a_reflector": ARM_IMPORT
    + "import inspect\n\n_REFLECTED_FIELD_NAME = 'body'\n\n"
    "def f(spec: TrackerSpec):\n"
    "    return inspect.getattr_static(spec, _REFLECTED_FIELD_NAME)\n",
    "text_field_named_in_an_include_set_and_listed": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n"
    "    return list(spec.model_dump(include={'body'}).values())\n",
    "text_field_named_in_a_template_handed_to_a_helper": ARM_IMPORT
    + "def fill_template(template: str, values: object) -> object:\n"
    "    return values\n\n"
    "def f(spec: TrackerSpec):\n"
    "    return fill_template('{body}', spec.model_dump())\n",
    "text_field_named_to_a_partial_over_getattr": ARM_IMPORT + "import functools\n\n"
    "def f(spec: TrackerSpec):\n"
    "    return functools.partial(getattr, spec)('body')\n",
    "methodcaller_mapped": ARM_IMPORT + "import operator\n\n"
    "def f(specs: tuple[TrackerSpec, ...]):\n"
    "    return list(map(operator.methodcaller('__getattribute__', 'body'), specs))\n",
    "methodcaller_applied": ARM_IMPORT + "import operator\n\n"
    "def f(spec: TrackerSpec):\n"
    "    return operator.methodcaller('__getattribute__', 'body')(spec)\n",
    # A receiver chain rooted at a call or a subscript of a foreign word.
    "spec_dumped_by_an_adapter_over_its_class": ARM_IMPORT
    + "from pydantic import TypeAdapter\n\n"
    "def f(spec: TrackerSpec):\n"
    "    return TypeAdapter(TrackerSpec).dump_python(spec)['body']\n",
    "spec_wrapped_by_a_subscripted_foreign_model": ARM_IMPORT
    + "from pydantic import RootModel\n\n"
    "def f(spec: TrackerSpec):\n"
    "    return RootModel[TrackerSpec](spec).model_dump()['body']\n",
    # An arm stated by a class pattern with no capture, or by an adapter.
    "arm_tested_by_a_class_pattern_with_no_capture": ARM_IMPORT
    + "def f(raw: object):\n    match raw:\n        case TrackerSpec():\n"
    "            return raw.body\n",
    "arm_validated_by_an_adapter_over_its_class": ARM_IMPORT
    + "from pydantic import TypeAdapter\n\n"
    "def f(raw):\n    return TypeAdapter(TrackerSpec).validate_python(raw).body\n",
    "arm_validated_by_a_module_level_adapter": ARM_IMPORT
    + "from pydantic import TypeAdapter\n\n"
    "_ARM_ADAPTER = TypeAdapter(TrackerSpec)\n\n"
    "def f(raw):\n    return _ARM_ADAPTER.validate_python(raw).body\n",
    "arm_returned_by_a_property": ARM_IMPORT + "class PropertyHolder:\n"
    "    def __init__(self, raw):\n        self.raw_held = raw\n\n"
    "    @property\n"
    "    def arm_by_property(self) -> TrackerSpec:\n        return self.raw_held\n\n"
    "    def text(self):\n        return self.arm_by_property.body\n",
    # A whole spec handed to a call whose stated return is text.
    "whole_spec_serialised_by_a_foreign_text_function": ARM_IMPORT + "import json\n\n"
    "def f(spec: TrackerSpec):\n    return json.dumps(spec.model_dump())\n",
    "whole_spec_handed_to_a_package_function_returning_text": ARM_IMPORT
    + "def as_prompt_text(values) -> str:\n    return ''\n\n"
    "def f(spec: TrackerSpec):\n    return as_prompt_text({'task_md': spec})\n",
}

#: The same spellings where the value is not the arm's text: each must stay
#: silent, so a widening cannot pass by reporting everything.
PLANTED_NON_READS = {
    "a_field_captured_that_is_not_the_text": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    match spec:\n"
    "        case TrackerSpec(subject=key):\n            return key\n",
    "a_literal_key_through_get_that_is_not_the_text": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return spec.model_dump().get('subject')\n",
    "a_field_named_to_a_reflector_that_is_not_the_text": ARM_IMPORT
    + "import inspect\n\n"
    "def f(spec: TrackerSpec):\n    return inspect.getattr_static(spec, 'subject')\n",
    "a_function_from_outside_the_package_handed_no_spec": "import copy\n\n"
    "def f(raw):\n    return copy.deepcopy(raw).body\n",
    "a_payload_serialised_by_its_own_model_method": ARM_IMPORT
    + "def f(spec: AuthoredSpec):\n    return spec.ticket.model_dump_json()\n",
    "a_renderer_mapped_over_no_spec": "def f(words):\n"
    "    return ''.join(map(str, words))\n",
    "the_other_element_of_a_tuple_written_out": ARM_IMPORT
    + "def f(spec: TrackerSpec, other):\n    first, second = spec, other\n"
    "    return second.body\n",
    "the_slot_a_reader_s_tuple_annotation_says_is_no_spec": ARM_IMPORT
    + "from kodezart.types.domain.tracker import TrackerIssue\n\n"
    "async def read_pair() -> tuple[TrackerSpec, dict[str, TrackerIssue]]:\n"
    "    raise NotImplementedError\n\n"
    "async def f():\n    spec, issues = await read_pair()\n"
    "    return issues['k'].body\n",
    "a_field_that_is_not_the_text_appended": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    held_other = []\n"
    "    held_other.append(spec.subject)\n    return held_other[0].body\n",
    "a_concatenation_holding_no_spec": ARM_IMPORT + "def f(spec: TrackerSpec, rest):\n"
    "    return (rest + [spec.subject])[0].body\n",
    "a_dict_display_holding_no_spec": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return {'k': spec.subject}['k'].body\n",
    "a_module_constant_naming_another_field": ARM_IMPORT
    + "_OTHER_FIELD_NAME = 'subject'\n\n"
    "def f(spec: TrackerSpec):\n    return getattr(spec, _OTHER_FIELD_NAME)\n",
    "an_adapter_over_no_arm": "from pydantic import TypeAdapter\n\n"
    "_OTHER_ADAPTER = TypeAdapter(dict)\n\n"
    "def f(raw):\n    return _OTHER_ADAPTER.validate_python(raw).body\n",
    "a_class_pattern_over_no_arm": "def f(raw: object):\n    match raw:\n"
    "        case dict():\n            return raw.body\n",
    "the_formatter_itself_returning_text": ARM_IMPORT
    + "from kodezart.domain.ticket import format_fire_spec\n\n"
    "def f(spec: TrackerSpec):\n    return format_fire_spec(spec)\n",
    "a_foreign_function_whose_return_is_no_text": ARM_IMPORT + "import copy\n\n"
    "def f(spec: TrackerSpec):\n    return copy.deepcopy(spec)\n",
}

#: One row per shape the stated limit puts out of reach, each held unseen:
#: a value handed across a function boundary where the other function is not
#: resolved at this site, a name built at run time, and a binding made only
#: when a function runs.  A row going red means the walk grew past its
#: stated limit, and the module docstring has to say so.
STATED_LIMIT_SHAPES = {
    "a_spec_returned_from_a_helper_under_a_wider_annotation": ARM_IMPORT
    + "def widened_by_helper(spec: TrackerSpec) -> object:\n    return spec\n\n"
    "def f(spec: TrackerSpec):\n    return widened_by_helper(spec).body\n",
    "a_field_name_built_at_run_time": ARM_IMPORT
    + "def f(spec: TrackerSpec):\n    return getattr(spec, 'bo' + 'dy')\n",
    "a_binding_made_only_when_a_function_runs": ARM_IMPORT
    + "def bind_late(spec: TrackerSpec):\n    globals()['late_bound_arm'] = spec\n\n"
    "def f():\n    return late_bound_arm.body\n",
}


def _planted_path(name: str) -> str:
    return f"planted_{name}.py"


def _planted_source(name: str, source: str) -> str:
    """The row's source with its function named after the row.

    A function a row defines can become a root (a payload reader, a reader),
    and a root is a word the whole walk shares; naming each row's function
    after its row keeps one row's root from reaching another row's calls.
    """
    return source.replace("def f(", f"def planted_{name}(")


@functools.cache
def _planted_report() -> dict[str, dict[str, tuple[str, ...]]]:
    """One walk over the package with every planted module beside it.

    Each planted module names its function after its row, and every other
    word a root grows from (a stored field, a reader, a module constant) is
    spelled in one row only, so each row reads as it would alone and one
    walk answers for every row.
    """
    planted = {**PLANTED_READS, **PLANTED_NON_READS, **STATED_LIMIT_SHAPES}
    return _report(
        {
            **PACKAGE,
            **{
                _planted_path(name): _planted_source(name, source)
                for name, source in planted.items()
            },
        }
    )


def _planted(name: str) -> tuple[int, int]:
    report = _planted_report()
    path = _planted_path(name)
    return (
        len(report["read"].get(path, ())),
        len(report["digested"].get(path, ())),
    )


@pytest.mark.parametrize("name", sorted(PLANTED_READS))
def test_every_ordinary_spelling_of_a_read_is_a_read(name):
    """Each planted spelling reads the arm's text once, and hashes nothing."""
    assert _planted(name) == (1, 0)


@pytest.mark.parametrize("name", sorted(PLANTED_NON_READS))
def test_a_value_that_is_not_the_arm_text_is_no_read(name):
    """The widenings report the arm's text, not every value near a spec."""
    assert _planted(name) == (0, 0)


@pytest.mark.parametrize("name", sorted(STATED_LIMIT_SHAPES))
def test_each_shape_the_stated_limit_names_is_unseen(name):
    """The limit is a fact the rows hold, not a sentence alone."""
    assert _planted(name) == (0, 0)


def test_the_planted_modules_leave_the_shipped_surface_as_it_is():
    """The planted walk is the shipped walk plus the planted modules only."""
    report = _planted_report()
    shipped = {
        kind: {path: sites for path, sites in found.items() if path in PACKAGE}
        for kind, found in report.items()
    }
    assert shipped == {"read": TEXT_READS, "digested": DIGEST_POSITIONS}


def test_every_derived_vocabulary_is_populated():
    """Not parametrised: an empty derivation fails here, not silently.

    The arms, their fields, the text and payload fields the formatter
    renders, its neighbouring renderers and the model's own text renders are
    each read off the code, and a walk over an empty one would report
    nothing while passing.
    """
    assert ARMS
    assert SPEC_TYPES >= set(ARMS)
    assert TEXT_FIELDS
    assert PAYLOAD_FIELDS
    assert TEXT_FIELDS | PAYLOAD_FIELDS <= ARM_MODEL_FIELDS
    assert RENDERERS
    assert MODEL_RENDERS
    assert PLANTED_READS
    assert PLANTED_NON_READS
    assert STATED_LIMIT_SHAPES
    assert CONTAINER_METHODS >= {"append", "extend", "update", "setdefault"}
    assert returns_text("json.dumps")
    assert returns_text("string.Template.substitute")
    assert not returns_text("copy.deepcopy")
