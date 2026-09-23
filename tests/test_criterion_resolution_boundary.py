"""One site turns a criterion identity into its sub-issue; its holders take the role.

A consumer that needs ONE criterion may depend on the narrow resolving role and
nothing wider; a consumer that needs the whole family takes the family reader.
So two things are asserted over the shipped sources (KOD-651): the tree holds
exactly one function that resolves an identity to a row, and no module naming
the narrow role also names the family surface — which is what stops a consumer
being handed a roster plus permission to search it.  A third, separate clause is
kept: no module carries a checkbox shape it could locate a write target by.

The second of those is exactly "no HOLDER of the role also reads the family",
and that is narrower than "every other module depends on the resolver rather
than on the roster": a module that never names the role and resolves a key off
the roster itself holds nothing and so the dependency rule never looks at it.
Such a module is policed by the SITE COUNT instead — it is a second resolution
site, and there is only ever one.  The count reaches a site that reads the
family; one handed the rows — a parameter annotated with a collection of rows
however the row type is written, a ``*`` parameter of rows, or an unannotated
parameter beside the role's criterion parameter; and each method of a class
whose constructor is handed them.  Annotations are resolved by identity over
one index of the tree, so an import alias, a module-level or ``type`` alias, a
union with ``None`` and a forward reference inside a subscript are each the
row type they denote.  The shipped lookups handed the rows that resolve
nothing are a register of whole definitions — module, dotted name and exact
parameters — asserted both ways as a list.

Every name the walk keys on is read off the shipped objects — the role, its one
method, that method's identity parameters AND the types they carry, and the two
modules where the role and its implementation are DECLARED.
A declaration is not a dependency, so those two are the dependency rule's only
exemptions, and adding a second method to either role reddens the single-name
unpackings below rather than silently halving what is checked.  The set of names
a module can hold the role under is derived the same way, and is wider than the
role: it is the role plus every class in the tree declaring the role's one
method.  A consumer annotated with a concrete resolver reaches exactly as far as
one annotated with the role, so naming the implementation is no way around the
dependency rule — and since depending on an implementation where a narrow role
would do is itself a finding here, the wider set is the right answer twice over.

The resolution walk is a SIGNATURE shape, not an expression walk: a function
that reads the family and declares the identity the role addresses a criterion
by is a resolution however it performs the lookup — by dict index, by ``.get``,
by a keys list and ``.index()``, or behind an alias.  The refuted predecessor
walked comparison nodes and so missed all four lookup shapes, and it counted
MODULES rather than sites, which let two resolutions in one allowed module pass.
The count here is of sites.

What the site is NOT keyed on is the declared return.  A function given the
family and one identity has resolved that identity whatever it then hands back
— the row, a field off the row, a one-row tuple, nothing at all — so requiring
the return annotation to be the row would have admitted a second resolution
under any other annotation, which is ordinary code and not one of the misses
stated below.  The return is therefore read as no part of the shape.

Declaring that identity is read two ways, either sufficing, because a name and a
type each see what the other is blind to: a parameter set spelling the role's
own identity names, whatever it annotates them; or a parameter set that is in
type exactly the role's identity and nothing besides, whatever it spells them,
a criterion identity type read as the text it is a type of.  The second closes
renaming, and it has to be exact rather than at least, because
a function handed MORE than an identity has more to go on than an identity —
which is what keeps an honest creation, given a criterion's own content as well,
off a report it does not belong on.

What this cannot see, the one stated limit: a value handed across a function
boundary, where the other function is not resolved at this site (returned from
a helper, stored on an object and read elsewhere, or passed through a container
built elsewhere); a name built at run time; a binding made only when a function
runs (``setattr`` or ``globals()`` inside a function body).  A committed case
holds each of those shapes as unseen.  Two signature shapes are unseen as
well, and a case holds the second: one that pads its signature past the role's
identity while spelling none of its names, and one handed the rows with a
single key under a name other than the role's criterion parameter — that
signature is every single-key helper over rows the tree already writes, the
lane walk's put-back and the plateau's key reading, and no signature tells
them from a resolution.  The checkbox clause reads literals and never code, so
a scan composed by code from half-box pieces, such as a character-index test,
is outside it too.  It is a boundary check over declared surfaces, not a
decision procedure over behaviour; the behaviour that a native key cannot be
redirected by identical text or parent prose is pinned by the resolution
suite, not here.
"""

import ast
import functools
import inspect
import re
import re._compiler as sre_compile
import re._constants as sre_constants
import re._parser as sre_parse
import sys
import warnings
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import pytest

from kodezart.adapters.linear.tracker import LinearMcpTracker
from kodezart.core.protocols import CriterionResolver, TrackerCriteriaReader
from kodezart.domain.criterion_creation import existing_criterion
from kodezart.services.alarm_supervisor import AlarmSupervisor
from kodezart.services.criterion_sources import NativeCriterionResolver
from kodezart.types.domain.criteria import CriterionId
from kodezart.types.domain.criterion_ref import CriterionRef
from kodezart.types.domain.tracker import TrackerIssue
from tests.name_resolution import (
    IdentityIndex,
    Key,
    annotation_keys,
    definitions,
    denoted,
    identity_index,
    object_key,
    parsed,
    source_tree,
)

SOURCE = Path(__file__).resolve().parents[1] / "src" / "kodezart"
ROLE = CriterionResolver.__name__
FAMILY_ROLE = TrackerCriteriaReader.__name__
(FAMILY_READ,) = tuple(
    name for name in vars(TrackerCriteriaReader) if not name.startswith("_")
)
(RESOLVE,) = tuple(name for name in vars(CriterionResolver) if not name.startswith("_"))
IDENTITY_PARAMETERS = frozenset(
    set(inspect.signature(getattr(CriterionResolver, RESOLVE)).parameters) - {"self"}
)


def _annotation_name(annotation: object) -> str:
    """How a shipped annotation is spelled in source, so the two compare."""
    return getattr(annotation, "__name__", None) or str(annotation)


#: What the role's identities ARE rather than how they are spelled: the
#: annotations its identity parameters carry, as a multiset. Read this way, a
#: second resolution site cannot escape the walk by renaming its parameters.
IDENTITY_ANNOTATIONS = Counter(
    _annotation_name(parameter.annotation)
    for name, parameter in inspect.signature(
        getattr(CriterionResolver, RESOLVE)
    ).parameters.items()
    if name in IDENTITY_PARAMETERS
)
ROW = TrackerIssue.__name__


def _module_of(declared: type[object]) -> str:
    """Where the shipped class is declared, as a path inside the source tree."""
    module = sys.modules[declared.__module__]
    return Path(module.__file__ or "").resolve().relative_to(SOURCE).as_posix()


#: Where the role and its one implementation are declared. A declaration is
#: not a dependency, so these two are the rule's only exemptions, and each is
#: read off the shipped object rather than written here.
DECLARATIONS = frozenset(
    {
        _module_of(CriterionResolver),
        _module_of(NativeCriterionResolver),
    }
)


def _classes_declaring_the_role(root: Path) -> frozenset[str]:
    """Every class in the tree that declares the role's one method.

    Derived from the tree rather than listed, because the set has to grow by
    itself: a second implementation added tomorrow is a holder of the role the
    day it lands, without anyone remembering to write it down here.
    """
    return frozenset(
        node.name
        for path in sorted(root.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
            and member.name == RESOLVE
            for member in node.body
        )
    )


#: The names a module can hold the resolving role under: the role itself and
#: every class that implements it. A consumer annotated with a concrete
#: resolver holds the role just as surely as one annotated with the role, so
#: both trigger the dependency rule — and since depending on an implementation
#: where a narrow role would do is itself a finding here, the wider set is the
#: right answer twice over.
RESOLVER_NAMES = frozenset({ROLE}) | _classes_declaring_the_role(SOURCE)


def _names(tree: ast.AST, wanted: frozenset[str]) -> bool:
    """Whether the module names any of *wanted*: bare, attribute or imported."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in wanted:
            return True
        if isinstance(node, ast.Attribute) and node.attr in wanted:
            return True
        if isinstance(node, ast.alias) and (
            node.name in wanted or node.asname in wanted
        ):
            return True
        if isinstance(node, ast.ClassDef) and node.name in wanted:
            return True
    return False


def _reads_the_family(node: ast.AST) -> bool:
    """Whether this body reaches the family read at all, however it is held.

    By the read's word as a name, an attribute or an import, or as a string
    constant, which is how ``getattr`` or ``methodcaller`` spell it.
    """
    return _names(node, frozenset({FAMILY_READ})) or any(
        isinstance(inner, ast.Constant) and inner.value == FAMILY_READ
        for inner in ast.walk(node)
    )


#: The row type and the criterion identity types, each read off the shipped
#: object, so an annotation is read by what it denotes rather than its word.
ROW_KEY = object_key(TrackerIssue)
IDENTITY_KEYS = frozenset({object_key(CriterionRef), object_key(CriterionId)})


def _union_parts(annotation: ast.expr) -> list[ast.expr]:
    """The arms of an ``A | B`` union, or the annotation alone."""
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return [*_union_parts(annotation.left), *_union_parts(annotation.right)]
    return [annotation]


def _written(annotation: ast.expr) -> ast.expr:
    """A string annotation read as the expression it spells."""
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            return ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return annotation
    return annotation


@dataclass(frozen=True)
class Reading:
    """One index of the tree and the row types resolved over it."""

    index: IdentityIndex
    #: The row type and every module-level alias of it alone.
    rows: frozenset[Key]
    #: Every module-level alias whose value names a row: ``Rows =
    #: Sequence[TrackerIssue]``, ``type Rows = ...``.
    families: frozenset[Key]

    def _denotes(self, module: str, node: ast.expr, keys: frozenset[Key]) -> bool:
        return isinstance(node, ast.Name | ast.Attribute) and not denoted(
            self.index, module, node
        )[0].isdisjoint(keys)

    def is_row(self, module: str, annotation: ast.expr) -> bool:
        """Whether an annotation is one row: the type, an alias, or either or None."""
        parts = [
            part
            for part in _union_parts(_written(annotation))
            if not (isinstance(part, ast.Constant) and part.value is None)
        ]
        return bool(parts) and all(
            self._denotes(module, _written(part), self.rows) for part in parts
        )

    def names_a_row(self, module: str, annotation: ast.expr) -> bool:
        """Whether an annotation names a row or a family alias anywhere in it."""
        return not annotation_keys(self.index, module, annotation).isdisjoint(
            self.rows | self.families
        )

    def is_family(self, module: str, argument: ast.arg, *, spread: bool) -> bool:
        """Whether a parameter is handed a collection of rows.

        Annotated as one — a container of rows, an alias of one, a union with
        one, a string or forward reference inside one — or a ``*`` parameter
        annotated with the row itself.
        """
        annotation = argument.annotation
        if annotation is None or not self.names_a_row(module, annotation):
            return False
        return spread or not self.is_row(module, annotation)

    def kind(self, module: str, annotation: ast.expr) -> str:
        """An annotation as the role's identity types are spelled.

        A criterion identity type counts as the plain text it is a type of,
        so a site typing its key more precisely than the role does is the
        same site.
        """
        if self._denotes(module, _written(annotation), IDENTITY_KEYS):
            return _annotation_name(CriterionRef.__supertype__)
        return ast.unparse(annotation)


def reading(sources: Mapping[str, str]) -> Reading:
    """Index *sources* and resolve the row aliases, to a fixed point."""
    index = identity_index(parsed(sources))
    values = {
        key: node.value
        for key, node in index.units.items()
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.TypeAlias)
        and "." not in key[1]
        and node.value is not None
    }
    found = Reading(index=index, rows=frozenset({ROW_KEY}), families=frozenset())
    for _ in range(len(values) + 1):
        rows = found.rows | {
            key for key, value in values.items() if found.is_row(key[0], value)
        }
        families = found.families | {
            key
            for key, value in values.items()
            if key not in rows and found.names_a_row(key[0], value)
        }
        grown = Reading(index=index, rows=rows, families=families)
        if grown == found:
            break
        found = grown
    return found


def _identity_parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.arg]:
    """The parameters an identity can be declared by: named ones, less ``self``."""
    args = node.args
    return [
        argument
        for argument in (*args.posonlyargs, *args.args, *args.kwonlyargs)
        if argument.arg != "self"
    ]


def _declares_an_identity(
    found: Reading, module: str, parameters: list[ast.arg], *, names: frozenset[str]
) -> bool:
    """Whether the parameters carry the identities the role addresses by.

    Two readings, and either one declares it, because a name and a type each
    see what the other is blind to.

    By NAME: a parameter set spelling *names* — the role's own identity names,
    or for a site already handed the rows the criterion parameter alone —
    declares that identity however it is annotated, which is how a site that
    annotates nothing at all is still seen.

    By TYPE: a parameter set that is, in type, exactly what the role declares
    and nothing besides — the identity and no other information — declares it
    under any spelling whatever, a criterion identity type read as the text
    it is a type of.  This is the reading that closes renaming.  Exactly, not
    at least: a function handed MORE than an identity has more to go on than
    an identity, which is what makes an honest creation taking a criterion's
    own content a different act from resolving a key, and the only thing
    that tells the two apart once spelling is no longer the test.
    """
    if names <= {argument.arg for argument in parameters}:
        return True
    annotated = Counter(
        found.kind(module, argument.annotation)
        for argument in parameters
        if argument.annotation is not None
    )
    return annotated == IDENTITY_ANNOTATIONS


#: A resolution site: its module, its dotted name, and its parameters in
#: order, so a register entry names one definition rather than a word.
Site = tuple[str, str, tuple[str, ...]]


def _site(module: str, name: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> Site:
    """A function as a site, its parameters in the order a signature lists them."""
    args = node.args
    ordered = (
        *args.posonlyargs,
        *args.args,
        *((args.vararg,) if args.vararg else ()),
        *args.kwonlyargs,
        *((args.kwarg,) if args.kwarg else ()),
    )
    return module, name, tuple(argument.arg for argument in ordered)


def _resolution_sites(found: Reading, module: str) -> list[Site]:
    """Each function in *module* that reads the family and declares an identity.

    Two conjuncts, and the declared return is not one of them: see the module
    docstring — a site handed the family and one identity has resolved it
    whatever it hands back.
    """
    tree = found.index.trees[module]
    where = definitions(tree)
    return sorted(
        _site(module, where[id(node)], node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _reads_the_family(node)
        and _declares_an_identity(
            found, module, _identity_parameters(node), names=IDENTITY_PARAMETERS
        )
    )


#: The parameter the role names its criterion by, as distinct from the owner
#: the family read itself is addressed by: the role's identity parameters less
#: the family read's own.  Unpacked, so a second one reddens here.
(CRITERION_PARAMETER,) = tuple(
    IDENTITY_PARAMETERS
    - set(inspect.signature(getattr(TrackerCriteriaReader, FAMILY_READ)).parameters)
)


def _handed_resolution_sites(found: Reading, module: str) -> list[Site]:
    """Each function handed the family that resolves a criterion identity in it.

    Handed the rows rather than reading them — as a parameter annotated with a
    collection of rows (see ``Reading.is_family``), as a ``*`` parameter of
    rows, or as an unannotated parameter beside the role's criterion
    parameter, which the name alone declares — or held on ``self`` by a
    class whose constructor is handed them, where each other method of that
    class is a site of its own.  Each declares the identity by one of two
    readings over its parameters other than the rows: the role's criterion
    parameter, with or without the owner beside it, since rows already handed
    need no owner to be read by; or, in type, exactly the role's identity and
    nothing besides.
    """
    tree = found.index.trees[module]
    where = definitions(tree)
    sites: list[Site] = []

    def handed(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        spread = node.args.vararg
        return (
            spread is not None and found.is_family(module, spread, spread=True)
        ) or any(
            found.is_family(module, argument, spread=False)
            for argument in _identity_parameters(node)
        )

    def declares(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        rest = [
            argument
            for argument in _identity_parameters(node)
            if not found.is_family(module, argument, spread=False)
        ]
        return _declares_an_identity(
            found, module, rest, names=frozenset({CRITERION_PARAMETER})
        )

    held: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        methods = [
            member
            for member in node.body
            if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        if any(member.name == "__init__" and handed(member) for member in methods):
            held |= {id(member) for member in methods if member.name != "__init__"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        parameters = _identity_parameters(node)
        unannotated = any(argument.annotation is None for argument in parameters)
        if ((handed(node) or id(node) in held) and declares(node)) or (
            unannotated
            and CRITERION_PARAMETER in {argument.arg for argument in parameters}
        ):
            sites.append(_site(module, where[id(node)], node))
    return sorted(sites)


#: The shipped functions handed the family that the reading above reports and
#: that resolve no identity, each by its module, its dotted name and its exact
#: parameters as read off the object, with why.  The creation's own lookup is
#: handed a parent key and a Check text, two strings exactly as a renamed
#: resolution would be, and answers whether the criterion about to be
#: created is already there: it addresses no identity, because there is none
#: yet.  Asserted both ways below as a list, so the register cannot go stale
#: and a second definition under the name, or the lookup handed another
#: parameter, is a site.
HANDED_LOOKUPS: dict[Site, str] = {
    (
        _module_of(existing_criterion),
        existing_criterion.__qualname__,
        tuple(inspect.signature(existing_criterion).parameters),
    ): "the create-if-absent lookup by Check text under a parent",
}


@functools.cache
def _shipped() -> Mapping[str, str]:
    """Every shipped module's text by its tree-relative path, read once."""
    return MappingProxyType(source_tree())


@functools.cache
def _shipped_reading() -> Reading:
    """The shipped tree, indexed once; no walk here changes it."""
    return reading(_shipped())


#: The module a planted second site lands in: one the tree does not have.
SECOND = "services/second_pick.py"


def _planted(text: str, module: str = SECOND) -> dict[str, str]:
    """The shipped tree with *module* extended by *text*, or created as it."""
    return {**_shipped(), module: _shipped().get(module, "") + "\n" + text}


def _handed_in(found: Reading) -> list[Site]:
    """Every handed site in the tree that no read site already counts."""
    return sorted(
        site
        for module in sorted(found.index.trees)
        for site in _handed_resolution_sites(found, module)
        if site not in _resolution_sites(found, module)
    )


def _sites_in(sources: Mapping[str, str]) -> dict[str, list[Site]]:
    """Every resolution site in *sources*: read or handed the family.

    A handed site in the register is set aside by its whole definition; one
    that also reads the family is counted by the first reading whatever the
    register says.
    """
    found = reading(sources)
    sites: dict[str, list[Site]] = {}
    for module in sorted(found.index.trees):
        read = _resolution_sites(found, module)
        handed = [
            site
            for site in _handed_resolution_sites(found, module)
            if site not in read and site not in HANDED_LOOKUPS
        ]
        if read or handed:
            sites[module] = sorted(read + handed)
    return sites


def _family_dependents(root: Path) -> dict[str, list[str]]:
    """Each module that holds the resolving role and the family surface both.

    Holding the role means naming it OR naming a class that implements it: a
    consumer annotated with a concrete resolver has the same reach as one
    annotated with the role, and must not also carry a roster to search.
    """
    found = {}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative in DECLARATIONS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not _names(tree, RESOLVER_NAMES):
            continue
        also = sorted(
            name
            for name in (FAMILY_ROLE, FAMILY_READ)
            if _names(tree, frozenset({name}))
        )
        if also:
            found[relative] = also
    return found


#: The marks a checkbox is written with: the blank of an open box and either
#: case of the tick.
CHECKBOX_MARKS = " xX"
#: The two brackets a box is written between.
OPEN, CLOSE = "[", "]"


def _accepts(state: sre_parse.State, items: list, text: str) -> bool:
    """Whether the pattern items, standing alone, match exactly *text*.

    The items are compiled on their own under the pattern's own flags, so a
    class, an escape, a group of any kind, an alternation, a repeat or an
    inline flag answers by what it matches rather than by how it is spelled.
    """
    if len(items) == 1 and len(text) == 1 and not state.flags & re.IGNORECASE:
        op, value = items[0]
        if op is sre_constants.LITERAL:
            return value == ord(text)
    try:
        compiled = sre_compile.compile(sre_parse.SubPattern(state, items), state.flags)
    except (re.error, TypeError, ValueError, RecursionError):
        return False
    return compiled.fullmatch(text) is not None


def _sequences(pattern: sre_parse.SubPattern) -> Iterator[sre_parse.SubPattern]:
    """Every sequence of items in a parsed pattern: itself, and each nested one."""
    yield pattern
    for op, value in pattern.data:
        if op is sre_constants.SUBPATTERN:
            yield from _sequences(value[-1])
        elif op is sre_constants.BRANCH:
            for arm in value[1]:
                yield from _sequences(arm)
        elif op in (
            sre_constants.MAX_REPEAT,
            sre_constants.MIN_REPEAT,
            sre_constants.POSSESSIVE_REPEAT,
        ):
            yield from _sequences(value[2])
        elif op in (sre_constants.ASSERT, sre_constants.ASSERT_NOT):
            yield from _sequences(value[1])
        elif op is sre_constants.ATOMIC_GROUP:
            yield from _sequences(value)
        elif op is sre_constants.GROUPREF_EXISTS:
            yield from _sequences(value[1])
            if value[2] is not None:
                yield from _sequences(value[2])


#: How many readings of one sequence ``_spliced`` takes before it stops: a
#: sequence with more is reported as ``UNREAD`` rather than read in part.
SPLICE_LIMIT = 256
#: What a pattern too branchy to read in full is reported as.
UNREAD = f"{OPEN}?{CLOSE}"


class _TooBranchyError(Exception):
    """A sequence has more readings than ``SPLICE_LIMIT``."""


def _spliced(items: list) -> list[list]:
    """Each reading of a sequence with its groups opened and one arm per alternation.

    ``re`` factors the prefix every arm shares out of an alternation, so
    ``\\[x\\]|\\[ \\]`` parses as the opening bracket followed by an
    alternation of ``x\\]`` and `` \\]``; and a group can hold a bracket
    together with a mark, as ``(\\[x)\\]`` does.  Either way a box straddles
    the items of the parsed sequence, so each group's items are spliced into
    it and each alternation is replaced by one of its arms.  Bounded by
    ``SPLICE_LIMIT``: past it, ``_TooBranchyError``.
    """
    readings: list[list] = [[]]
    for op, value in items:
        if op is sre_constants.SUBPATTERN:
            options = _spliced(list(value[-1].data))
        elif op is sre_constants.BRANCH:
            options = [
                option for arm in value[1] for option in _spliced(list(arm.data))
            ]
        else:
            options = [[(op, value)]]
        readings = [reading + option for reading in readings for option in options]
        if len(readings) > SPLICE_LIMIT:
            raise _TooBranchyError
    return readings


def _pattern_boxes(text: str, flags: int) -> list[str]:
    """Each bracketed box the string accepts when it is read as a pattern.

    A box is an item that accepts the opening bracket and no mark, then items
    that together accept one mark, then an item that accepts the closing
    bracket and no mark, in one sequence of the parsed pattern or in one of
    its spliced readings.  Reported as the marks the position between the
    brackets accepts, and as ``UNREAD`` when a sequence has too many readings
    to take them all.
    """
    try:
        # Any string is read as a pattern here, and ``re`` warns of a class
        # whose meaning a later release may change, such as one opening with
        # ``[``.  The reading is the one ``re`` makes today, warning or not.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            parsed = sre_parse.parse(text, flags)
    except (re.error, OverflowError, RecursionError, ValueError):
        return []
    state = parsed.state

    def bracket(item: tuple, which: str) -> bool:
        return _accepts(state, [item], which) and not any(
            _accepts(state, [item], mark) for mark in CHECKBOX_MARKS
        )

    found: set[str] = set()
    for sequence in _sequences(parsed):
        try:
            readings = [list(sequence.data), *_spliced(list(sequence.data))]
        except _TooBranchyError:
            found.add(UNREAD)
            continue
        for items in readings:
            for first, item in enumerate(items):
                if not bracket(item, OPEN):
                    continue
                for last in range(first + 2, len(items)):
                    if not bracket(items[last], CLOSE):
                        continue
                    inside = items[first + 1 : last]
                    marks = [m for m in CHECKBOX_MARKS if _accepts(state, inside, m)]
                    if marks:
                        found.add(f"{OPEN}{''.join(marks)}{CLOSE}")
                    break
    return sorted(found)


def checkbox_shapes(text: str) -> list[str]:
    """Each complete checkbox shape in *text*, read by the marks it accepts.

    Two readings, either sufficing (KOD-651).  As TEXT: a box written
    verbatim — an opening bracket, one mark, a closing bracket — which is
    what a substring test, a prefix test or a template carries.  As a
    PATTERN: the string is parsed the way ``re`` parses it, plainly and as a
    verbose pattern, and a box is an opening bracket, a position accepting a
    mark, and a closing bracket, in sequence: in the parsed pattern at any
    depth — a group, a repeat, a lookaround, an atomic or a conditional
    group — and in each reading of a sequence with its groups opened and one
    arm taken per alternation, since ``re`` factors the prefix an
    alternation's arms share out of them.  A sequence with more than
    ``SPLICE_LIMIT`` such readings is reported as ``UNREAD`` rather than read
    in part.  The position is read by what
    it matches, so a class, an alternation of any arity, a capturing,
    non-capturing or named group whatever its name, an inline flag, an
    escape such as ``\\x20`` or ``\\s``, a repeat and ``.`` are one
    reading, and so is a bracket written as an escape or a class.  A pattern
    whose bracketed position accepts a mark among other things — ``\\w+``,
    ``.*`` — is reported as well: it scans boxes among what it scans.
    """
    literal = [
        f"{OPEN}{mark}{CLOSE}"
        for mark in CHECKBOX_MARKS
        if f"{OPEN}{mark}{CLOSE}" in text
    ]
    if OPEN not in text and "\\" not in text:
        return literal
    return literal + _pattern_boxes(text, 0) + _pattern_boxes(text, re.VERBOSE)


def _text_of(node: ast.AST) -> str | None:
    """The text a literal expression is, whole, or ``None``.

    A string constant; a bytes constant, read as Latin-1 so every byte is one
    character; a ``+`` of literal pieces; and an f-string whose every part
    is literal, a replacement field over a constant included.  Anything that
    takes a value at run time is no text here.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        if isinstance(node.value, bytes):
            return node.value.decode("latin-1")
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text_of(node.left), _text_of(node.right)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                if value.conversion != -1 or value.format_spec is not None:
                    return None
                value = value.value
            text = (
                str(value.value)
                if isinstance(value, ast.Constant) and value.value is not None
                else None
            )
            if text is None:
                return None
            parts.append(text)
        return "".join(parts)
    return None


def _checkbox_constants(tree: ast.AST) -> list[tuple[int, list[str]]]:
    """Each literal in this module that carries a complete checkbox shape.

    Every literal expression is read whole (see ``_text_of``), so a pattern
    joined from literal pieces is read as the pattern it joins to.
    """
    return [
        (getattr(node, "lineno", 0), shapes)
        for node in ast.walk(tree)
        for text in [_text_of(node)]
        if text is not None
        for shapes in [checkbox_shapes(text)]
        if shapes
    ]


def _literals_read(root: Path) -> int:
    """How many literal texts the scan reads under *root*."""
    return sum(
        _text_of(node) is not None
        for path in sorted(root.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
    )


def _checkbox_scans(root: Path) -> dict[str, list[tuple[int, list[str]]]]:
    found = {}
    for path in sorted(root.rglob("*.py")):
        hits = _checkbox_constants(ast.parse(path.read_text(encoding="utf-8")))
        if hits:
            found[path.relative_to(root).as_posix()] = hits
    return found


def test_one_site_turns_a_criterion_identity_into_its_sub_issue() -> None:
    """Counted as SITES, so two resolutions in one allowed module still fail.

    A site handed the rows counts as one read from the family (KOD-651). The
    register of handed lookups that resolve nothing is live and exact: the
    handed reading reports each entry's whole definition once, and nothing
    else, compared as a list.
    """
    sites = _sites_in(_shipped())
    assert sum(len(group) for group in sites.values()) == 1, sites
    assert list(sites) == [_module_of(NativeCriterionResolver)], sites
    assert _handed_in(_shipped_reading()) == sorted(HANDED_LOOKUPS)


def test_no_consumer_of_the_resolver_also_takes_the_criterion_family() -> None:
    """A holder of the narrow role may not also hold a roster to search."""
    assert _family_dependents(SOURCE) == {}


def test_holding_the_role_includes_holding_a_class_that_implements_it() -> None:
    """Annotating the concrete resolver is holding the role (KOD-651).

    The rule above triggers on the shipped implementation as well as on the
    role, so a consumer cannot keep both surfaces by naming the class instead
    of the narrow role it plays. The set is read off the tree, so it is the
    role, its shipped implementation, and whatever else declares that method.
    """
    assert {ROLE, NativeCriterionResolver.__name__} <= RESOLVER_NAMES


def test_no_module_scans_for_checkbox_syntax() -> None:
    """No shipped module carries a checkbox shape it could address a target by.

    Literals only — a string or bytes constant, literal pieces joined by
    ``+`` and an f-string of literals, each read whole — and nothing is
    executed: a pattern assembled at runtime or read from configuration is
    not seen, nor a scan code composes from half-box pieces. The
    walk does not tell a scan from a write either, which is what makes it cheap
    and total over literals — the sources carry no complete checkbox literal at
    all, because the one checkbox they write for a human composes its mark. So
    this is STRICTER than the clause it keeps, and it is a lint approximation
    rather than a decision procedure: `[X]` is also a one-letter subscript,
    which is why only literals are read and never code.

    A pattern is read by what it matches rather than by how it is spelled:
    see ``checkbox_shapes`` for both readings (KOD-651).
    """
    assert _checkbox_scans(SOURCE) == {}
    # Non-vacuous: the scan read the shipped tree's literals.
    assert _literals_read(SOURCE) > 0


@pytest.mark.parametrize(
    ("returns", "lookup"),
    [
        (
            f"{ROW} | None",
            "    by_key = {row.issue_key: row for row in rows}\n"
            "    return by_key[criterion_key]\n",
        ),
        (
            f"{ROW} | None",
            "    by_key = {row.issue_key: row for row in rows}\n"
            "    return by_key.get(criterion_key)\n",
        ),
        (
            f"{ROW} | None",
            "    keys = [row.issue_key for row in rows]\n"
            "    return rows[keys.index(criterion_key)]\n",
        ),
        (
            f"{ROW} | None",
            "    wanted = criterion_key\n"
            "    return [row for row in rows if row.issue_key == wanted][0]\n",
        ),
        # The fifth is not a lookup shape but a RETURN shape: the same walk,
        # the same family read, the same identity, handing back a field off
        # the resolved row instead of the row (KOD-651).
        (
            "str",
            "    by_key = {row.issue_key: row for row in rows}\n"
            "    return by_key[criterion_key].body\n",
        ),
    ],
)
def test_the_refused_lookup_shapes_are_reported(returns: str, lookup: str) -> None:
    """Every form the refuted comparison walk missed is one signature shape here.

    And one form no return annotation covers: the declared return is no part
    of the shape, so a site handing back a field off the row it resolved is
    reported exactly as the four lookups are.
    """
    source = (
        f"async def locate(*, tracker, issue_key: str, criterion_key: str"
        f") -> {returns}:\n"
        "    rows = await tracker.read_criteria(issue_key=issue_key)\n"
        f"{lookup}"
    )
    assert _resolution_sites(reading(_planted(source)), SECOND)


def test_a_second_resolution_in_one_module_counts_as_two_sites() -> None:
    """The predecessor counted modules; two sites in one module passed it."""
    source = (
        "async def first(*, tracker, issue_key: str, criterion_key: str"
        ") -> TrackerIssue:\n"
        "    rows = await tracker.read_criteria(issue_key=issue_key)\n"
        "    return {row.issue_key: row for row in rows}[criterion_key]\n"
        "async def second(*, tracker, issue_key: str, criterion_key: str"
        ") -> TrackerIssue:\n"
        "    rows = await tracker.read_criteria(issue_key=issue_key)\n"
        "    return [row for row in rows if row.issue_key == criterion_key][0]\n"
    )
    assert len(_resolution_sites(reading(_planted(source)), SECOND)) == 2


def test_a_resolution_that_renames_its_identities_is_reported() -> None:
    """The identity is the types the parameters carry, not the names (KOD-651).

    A site spelling the owning issue and the criterion identity anything it
    likes still declares them, so the house parameter names are not what the
    rule rests on and departing from them buys no cover.
    """
    source = (
        "async def target(self, *, parent: str, key: str) -> TrackerIssue:\n"
        "    rows = tuple(await self.tracker.read_criteria(issue_key=parent))\n"
        "    return {row.issue_key: row for row in rows}[key]\n"
    )
    assert _resolution_sites(reading(_planted(source)), SECOND)


def test_a_family_read_without_a_criterion_identity_is_not_a_resolution() -> None:
    """Creating a criterion that is absent addresses no key: there is none yet.

    Read off the SHIPPED creation rather than a source of this test's own
    making, because the thing to keep honest is the real one: the adapter reads
    the whole family, hands back one row, and is not resolving an identity — it
    is given a criterion's title, Check, Do and holder, which is strictly more
    than an identity and the reason the walk must not report it.  A stub of two
    strings would not carry that, and reading the identity by type rather than
    by name is exactly what makes the difference load-bearing.
    """
    assert _resolution_sites(_shipped_reading(), _module_of(LinearMcpTracker)) == []


def test_a_protocol_declaration_is_not_an_implementation() -> None:
    """A signature whose whole body is a docstring and an ellipsis resolves nothing."""
    source = (
        "class Role(Protocol):\n"
        "    async def resolve_criterion(self, *, issue_key: str,"
        " criterion_key: str) -> TrackerIssue:\n"
        '        """The one current sub-issue with that key."""\n'
        "        ...\n"
    )
    assert _resolution_sites(reading(_planted(source)), SECOND) == []


@pytest.mark.parametrize(
    "spelling",
    [
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s+\\[[ xX]\\]\\s+")\n',
        # The same pattern spelling its two marks as an alternation instead of
        # a class: the spelling the predecessor detector was caught on.
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*\\[( |x)\\]\\s*(?P<label>.+)$")\n',
        # The same alternation as a non-capturing group, as a named group, and
        # with a third arm: group syntax is no part of what is read.
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*\\[(?: |x)\\]\\s*(?P<label>.+)$")\n',
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*\\[(?P<mark> |x)\\]\\s*")\n',
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*\\[( |x|X)\\]\\s*(?P<label>.+)$")\n',
        # Read by what the position matches, not by its spelling (KOD-651): a
        # named group whatever its name's length, an inline flag, ticked boxes
        # only, any one character, an escaped class, an escaped mark, brackets
        # written as classes, repeats, and a verbose pattern's padding.
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*\\[(?P<checked> |x)\\]\\s*")\n',
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*\\[(?P<status> |x|X)\\]\\s*")\n',
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*\\[(?i:x| )\\]\\s*(?P<label>.+)")\n',
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*\\[(x|X)\\]\\s*(?P<label>.+)$")\n',
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*\\[.\\]\\s*(?P<label>.+)$")\n',
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*\\[[\\sxX]\\]\\s*(?P<label>.+)$")\n',
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*\\[(\\x20|x)\\]\\s*")\n',
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*[\\[][ x][\\]]\\s*")\n',
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*\\[\\s*x?\\s*\\]\\s*")\n',
        'CHECKBOX_LINE = re.compile(r"\\[ (x      |      X) \\]", re.VERBOSE)\n',
        # An open box whose blank a verbose reading would drop (KOD-651).
        'OPEN_BOX = re.compile(r"^\\s*[-*]\\s*\\[ \\]")\n',
        # A box ``re`` parses across its items: an alternation whose shared
        # opening bracket it factors out, and a group holding a bracket with
        # a mark.  Then a box nested in a capturing group, a repeat, a
        # repeat inside a group or inside one arm of an alternation, a
        # lookahead, an atomic group and either arm of a conditional group,
        # a class ``re`` warns may later mean a nested set, and a pattern too
        # branchy to read in full (KOD-651).
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*(?:\\[x\\]|\\[ \\])\\s*")\n',
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*(\\[x)\\]\\s*")\n',
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*(?P<box>\\[x\\])\\s*")\n',
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*](?:\\s*\\[x\\])+")\n',
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*](?P<boxes>(?:\\s*\\[x\\])+)")\n',
        'CHECKBOX_LINE = re.compile(r"^(?:\\*+|-(?:\\s*\\[x\\])+)")\n',
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*(?=\\[x\\])")\n',
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*(?>\\[x\\])")\n',
        'CHECKBOX_LINE = re.compile(r"^(-)?(?(1)\\s*\\[x\\]|\\*)")\n',
        'CHECKBOX_LINE = re.compile(r"^(-)?(?(1)\\*|\\s*\\[x\\])")\n',
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*\\[[[ x]\\]\\s*")\n',
        'CHECKBOX_LINE = re.compile(r"(ab|cd)(ab|cd)(ab|cd)(ab|cd)(ab|cd)'
        '(ab|cd)(ab|cd)(ab|cd)(ab|cd)\\[\\d\\]")\n',
        'def ticked(line):\n    return "- [x] " in line\n',
        'def ticked(line):\n    return line.startswith("- [ ]")\n',
        'ROW = "- [X] {key}: {check}"\n',
        # Literal pieces joined whole, and a bytes pattern (KOD-651).
        'LINE = re.compile(r"^\\s*[-*]\\s*\\[" "" + r"[ xX]" + r"\\]\\s*(?P<l>.+)$")\n',
        'LINE = re.compile(rb"^\\s*[-*]\\s*\\[[ xX]\\]\\s*")\n',
        "ROW = f\"- {'['}x] {'{key}'}\"\n",
    ],
)
def test_each_spelling_of_a_checkbox_scan_is_reported(spelling: str) -> None:
    """Compiled pattern, substring test, prefix test and format template alike.

    Compiled twice over, because how a pattern spells the marks it accepts is
    no part of what it scans for: a class and an alternation read the same
    lines and are the same clause breach.
    """
    assert _checkbox_constants(ast.parse(spelling))


@pytest.mark.parametrize(
    "spelling",
    [
        'KEY = re.compile(r"\\[(AC-\\d+)\\]")\n',
        'LINK = re.compile(r"\\[\\d\\]")\n',
        'LABEL = "[AC-1]"\n',
    ],
)
def test_a_bracketed_pattern_that_accepts_no_mark_is_not_a_box(spelling: str) -> None:
    """The pattern reading is keyed on the marks, not on the brackets alone."""
    assert _checkbox_constants(ast.parse(spelling)) == []


#: The imports a planted site's annotations resolve through, each written
#: from the shipped object.
_ROWS = (
    "from collections.abc import Sequence\n"
    f"from {TrackerIssue.__module__} import {ROW}\n"
    f"from {CriterionRef.__module__} import {CriterionRef.__name__}\n"
)

#: Each way a second site could be handed the rows instead of reading them:
#: the role's identity names, the criterion parameter alone, the role's
#: identity types under other names, and every way of annotating the rows
#: or holding them (KOD-651).
HANDED_SITES = {
    "the role's identity names": (
        f"{_ROWS}def pick_criterion(*, rows: Sequence[{ROW}], issue_key: str,"
        f" {CRITERION_PARAMETER}: str) -> {ROW}:\n"
        "    del issue_key\n"
        f"    return {{row.issue_key: row for row in rows}}[{CRITERION_PARAMETER}]\n"
    ),
    "the criterion parameter alone": (
        f"{_ROWS}def pick(rows: tuple[{ROW}, ...], {CRITERION_PARAMETER}: str)"
        f" -> {ROW}:\n"
        f"    return [r for r in rows if r.issue_key == {CRITERION_PARAMETER}][0]\n"
    ),
    "the identity types under other names": (
        f"{_ROWS}def pick(rows: 'list[{ROW}]', parent: str, key: str) -> {ROW}:\n"
        "    return {row.issue_key: row for row in rows}[key]\n"
    ),
    "the identity typed as the criterion identity under other names": (
        f"{_ROWS}def pick(rows: Sequence[{ROW}], parent: str,"
        f" key: {CriterionRef.__name__}) -> {ROW}:\n"
        "    return {row.issue_key: row for row in rows}[key]\n"
    ),
    "an optional collection of rows": (
        f"{_ROWS}def pick(rows: Sequence[{ROW}] | None, {CRITERION_PARAMETER}: str)"
        f" -> {ROW}:\n"
        f"    return {{r.issue_key: r for r in rows or ()}}[{CRITERION_PARAMETER}]\n"
    ),
    "a module alias of a collection of rows": (
        f"{_ROWS}Rows = Sequence[{ROW}]\n"
        f"def pick(rows: Rows, {CRITERION_PARAMETER}: str) -> {ROW}:\n"
        f"    return {{r.issue_key: r for r in rows}}[{CRITERION_PARAMETER}]\n"
    ),
    "a type statement over a collection of rows": (
        f"{_ROWS}type Rows = Sequence[{ROW}]\n"
        f"def pick(rows: Rows, {CRITERION_PARAMETER}: str) -> {ROW}:\n"
        f"    return {{r.issue_key: r for r in rows}}[{CRITERION_PARAMETER}]\n"
    ),
    "a forward reference inside the collection": (
        f"{_ROWS}def pick(rows: Sequence['{ROW}'], {CRITERION_PARAMETER}: str)"
        f" -> {ROW}:\n"
        f"    return {{r.issue_key: r for r in rows}}[{CRITERION_PARAMETER}]\n"
    ),
    "the row type imported under another word": (
        f"from collections.abc import Sequence\n"
        f"from {TrackerIssue.__module__} import {ROW} as Row\n"
        f"def pick(rows: Sequence[Row], {CRITERION_PARAMETER}: str) -> Row:\n"
        f"    return {{r.issue_key: r for r in rows}}[{CRITERION_PARAMETER}]\n"
    ),
    "an alias of the row type inside the collection": (
        f"{_ROWS}Row = {ROW}\n"
        f"def pick(rows: Sequence[Row], {CRITERION_PARAMETER}: str) -> Row:\n"
        f"    return {{r.issue_key: r for r in rows}}[{CRITERION_PARAMETER}]\n"
    ),
    "the rows handed as a star parameter": (
        f"{_ROWS}def pick({CRITERION_PARAMETER}: str, *rows: {ROW}) -> {ROW}:\n"
        f"    return {{r.issue_key: r for r in rows}}[{CRITERION_PARAMETER}]\n"
    ),
    "the rows handed unannotated beside the criterion parameter": (
        f"{_ROWS}def pick(rows, {CRITERION_PARAMETER}: str) -> {ROW}:\n"
        f"    return {{r.issue_key: r for r in rows}}[{CRITERION_PARAMETER}]\n"
    ),
    "the rows held on self by the constructor": (
        f"{_ROWS}class Picker:\n"
        f"    def __init__(self, rows: Sequence[{ROW}]) -> None:\n"
        "        self._rows = {row.issue_key: row for row in rows}\n"
        f"    def pick(self, {CRITERION_PARAMETER}: str) -> {ROW}:\n"
        f"        return self._rows[{CRITERION_PARAMETER}]\n"
    ),
    "an index class resolving by the role's identity": (
        f"{_ROWS}class Index:\n"
        f"    def __init__(self, rows: Sequence[{ROW}]) -> None:\n"
        "        self._by_key = {row.issue_key: row for row in rows}\n"
        f"    def resolve(self, *, issue_key: str, {CRITERION_PARAMETER}: str)"
        f" -> {ROW}:\n"
        f"        return self._by_key[{CRITERION_PARAMETER}]\n"
    ),
}

#: Each way a second site that reads the family could declare the identity
#: past the spellings the cases above use (KOD-651).
READ_SITES = {
    "the key typed as the criterion identity and renamed": (
        f"{_ROWS}class Picker:\n"
        "    def __init__(self, tracker) -> None:\n"
        "        self._tracker = tracker\n"
        "    async def locate(self, *, parent: str,"
        f" key: {CriterionRef.__name__}) -> {ROW}:\n"
        f"        rows = await self._tracker.{FAMILY_READ}(issue_key=parent)\n"
        "        return {row.issue_key: row for row in rows}[key]\n"
    ),
    "the family read fetched by getattr with its literal name": (
        "async def locate(*, tracker: object, issue_key: str, criterion_key: str):\n"
        f"    rows = await getattr(tracker, {FAMILY_READ!r})(issue_key=issue_key)\n"
        "    return {row.issue_key: row for row in rows}[criterion_key]\n"
    ),
}


@pytest.mark.parametrize("form", sorted(READ_SITES))
def test_a_second_site_reading_the_family_is_counted(form: str) -> None:
    """A read site is one however its key is typed or its read is fetched."""
    sources = _planted(READ_SITES[form])
    assert _resolution_sites(reading(sources), SECOND)
    assert SECOND in _sites_in(sources)


#: A definition the register must not cover: a function named after the
#: lane's alarm observation, in a module the reading does not register, that
#: looks a criterion up by text, and the registered lookup itself handed the
#: criterion parameter as well.
REGISTER_CONTROLS = {
    "a same-named function in a module the reading does not register": (
        _module_of(AlarmSupervisor),
        f"{_ROWS}def {AlarmSupervisor.observe_lane.__name__}(rows: Sequence[{ROW}],"
        f" {CRITERION_PARAMETER}: str) -> {ROW}:\n"
        f"    return {{r.issue_key: r for r in rows}}[{CRITERION_PARAMETER}]\n",
    ),
    "the registered lookup redefined with the criterion parameter": (
        _module_of(existing_criterion),
        f"{_ROWS}def {existing_criterion.__name__}(rows: Sequence[{ROW}],"
        f" {CRITERION_PARAMETER}: str) -> {ROW}:\n"
        f"    return {{r.issue_key: r for r in rows}}[{CRITERION_PARAMETER}]\n",
    ),
}


@pytest.mark.parametrize("form", sorted(REGISTER_CONTROLS))
def test_the_register_sets_aside_one_definition_not_a_word(form: str) -> None:
    """A register entry is a definition and its parameters, not its name (KOD-651)."""
    module, text = REGISTER_CONTROLS[form]
    sources = _planted(text, module)
    assert module in _sites_in(sources)
    assert _handed_in(reading(sources)) != sorted(HANDED_LOOKUPS)


#: The one stated limit, a case per shape: each is a second resolution the
#: count does not see, held here so the limit is a fact the tests hold.
UNSEEN_SITES = {
    "a value handed across a function boundary": (
        f"{_ROWS}class Picker:\n"
        f"    def pick(self, {CRITERION_PARAMETER}: str) -> {ROW}:\n"
        "        rows = self._load()\n"
        f"        return {{r.issue_key: r for r in rows}}[{CRITERION_PARAMETER}]\n"
    ),
    "a name built at run time": (
        "async def locate(*, tracker: object, issue_key: str, criterion_key: str):\n"
        "    read = getattr(tracker, '_'.join(('read', 'criteria')))\n"
        "    rows = await read(issue_key=issue_key)\n"
        "    return {row.issue_key: row for row in rows}[criterion_key]\n"
    ),
    "a binding made only when a function runs": (
        f"{_ROWS}class Picker:\n"
        f"    def pick(self, {CRITERION_PARAMETER}: str) -> {ROW}:\n"
        f"        return self._by_key[{CRITERION_PARAMETER}]\n"
        f"def load(picker: Picker, rows: Sequence[{ROW}]) -> None:\n"
        "    setattr(picker, '_by_key', {row.issue_key: row for row in rows})\n"
    ),
    "a single key handed with the rows under another name": (
        f"{_ROWS}def pick(rows: Sequence[{ROW}], key: str) -> {ROW}:\n"
        "    return {row.issue_key: row for row in rows}[key]\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(UNSEEN_SITES))
def test_each_shape_of_the_stated_limit_stays_unseen(shape: str) -> None:
    """The limit the module states is exactly what the count does not see."""
    assert SECOND not in _sites_in(_planted(UNSEEN_SITES[shape]))


@pytest.mark.parametrize("form", sorted(HANDED_SITES))
def test_a_site_handed_the_rows_is_a_resolution_site(form: str) -> None:
    """A second resolution site need not read the family itself (KOD-651).

    And the site count takes it as one, wherever it lands.
    """
    sources = _planted(HANDED_SITES[form])
    found = reading(sources)
    assert _resolution_sites(found, SECOND) == []
    assert _handed_resolution_sites(found, SECOND)
    assert SECOND in _sites_in(sources)


def test_a_criterion_text_comparison_on_a_key_addressed_target_is_not_reported() -> (
    None
):
    """The carve-out: confirming a target is not finding one (KOD-651).

    The shipped cross-off module compares a criterion's Check body against the
    criterion's own text before it writes — a stale-write precondition on a
    target the caller already addressed by key. All three walks must be silent
    on it, or an honest guard would forbid honest code.
    """
    module = SOURCE / "domain" / "criterion_cross_off.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))
    assert _resolution_sites(_shipped_reading(), "domain/criterion_cross_off.py") == []
    assert _checkbox_constants(tree) == []
    assert not _names(tree, frozenset({ROLE}))
