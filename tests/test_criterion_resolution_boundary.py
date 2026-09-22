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
site, and there is only ever one — so the two assertions together are the
Check's sentence, and neither is it alone.

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
type exactly the role's identity and nothing besides, whatever it spells them.
The second closes renaming, and it has to be exact rather than at least, because
a function handed MORE than an identity has more to go on than an identity —
which is what keeps an honest creation, given a criterion's own content as well,
off a report it does not belong on.

What this cannot see: the walk is textual and executes nothing, so a resolution
assembled at runtime or reached through a wrapper whose own signature declares
no identity is outside it.  So is one that pads its signature past the role's
identity while spelling none of its names.  It is a boundary check over declared
surfaces, not a decision procedure over behaviour; the behaviour that a native
key cannot be redirected by identical text or parent prose is pinned by the
resolution suite, not here.
"""

import ast
import inspect
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

from kodezart.core.protocols import CriterionResolver, TrackerCriteriaReader
from kodezart.services.criterion_sources import NativeCriterionResolver
from kodezart.types.domain.tracker import TrackerIssue

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
    """Whether this body reaches the family read at all, however it is held."""
    return _names(node, frozenset({FAMILY_READ}))


def _declares_an_identity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether the parameters carry the identities the role addresses by.

    Two readings, and either one declares it, because a name and a type each
    see what the other is blind to.

    By NAME: a parameter set spelling the role's own identity names declares
    that identity however it is annotated, which is how a site that annotates
    nothing at all is still seen.

    By TYPE: a parameter set that is, in type, exactly what the role declares
    and nothing besides — the identity and no other information — declares it
    under any spelling whatever.  This is the reading that closes renaming.
    Exactly, not at least: a function handed MORE than an identity has more to
    go on than an identity, which is what makes an honest creation taking a
    criterion's own content a different act from resolving a key, and the only
    thing that tells the two apart once spelling is no longer the test.
    """
    args = node.args
    parameters = [
        argument
        for argument in (*args.posonlyargs, *args.args, *args.kwonlyargs)
        if argument.arg != "self"
    ]
    if IDENTITY_PARAMETERS <= {argument.arg for argument in parameters}:
        return True
    annotated = Counter(
        ast.unparse(argument.annotation)
        for argument in parameters
        if argument.annotation is not None
    )
    return annotated == IDENTITY_ANNOTATIONS


def _resolution_sites(tree: ast.AST) -> list[str]:
    """Each function in this module that resolves a criterion identity to a row.

    Two conjuncts, and the declared return is not one of them: see the module
    docstring — a site handed the family and one identity has resolved it
    whatever it hands back.
    """
    return sorted(
        f"{node.name}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _reads_the_family(node)
        and _declares_an_identity(node)
    )


def _sites_in(root: Path) -> dict[str, list[str]]:
    found = {}
    for path in sorted(root.rglob("*.py")):
        sites = _resolution_sites(ast.parse(path.read_text(encoding="utf-8")))
        if sites:
            found[path.relative_to(root).as_posix()] = sites
    return found


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


#: A checkbox once written, and the two ways a pattern spells the marks it
#: accepts: a character class, and an alternation group. Both, because the
#: predecessor detector this clause replaced was caught on the alternation
#: spelling — a pattern reading `\[( |x)\]` scans for a checkbox exactly as
#: one reading `\[[ x]\]` does, and a walk that knew only the class would
#: have reported the second and stayed silent on the first (KOD-651).
CHECKBOX = re.compile(r"\[(?:[ xX]{1,4}\]|\(\s*[ xX]\s*\|\s*[ xX]\s*\)\\?\])")


def checkbox_shapes(text: str) -> list[str]:
    """Each complete checkbox shape in *text*: `[ ]`, `[x]`, or a pair of marks.

    A pair of marks rather than one: `[X]` on its own is also a one-letter
    subscript, so a three-character hit counts and a longer one counts when it
    names both marks — spelled as a class containing the blank, or as an
    alternation, which is what the pipe reads.
    """
    return [
        found.group(0)
        for found in CHECKBOX.finditer(text)
        if len(found.group(0)) == 3 or " " in found.group(0) or "|" in found.group(0)
    ]


def _checkbox_constants(tree: ast.AST) -> list[tuple[int, list[str]]]:
    """Each string constant in this module that carries a complete checkbox shape."""
    return [
        (node.lineno, shapes)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        for shapes in [checkbox_shapes(node.value)]
        if shapes
    ]


def _checkbox_scans(root: Path) -> dict[str, list[tuple[int, list[str]]]]:
    found = {}
    for path in sorted(root.rglob("*.py")):
        hits = _checkbox_constants(ast.parse(path.read_text(encoding="utf-8")))
        if hits:
            found[path.relative_to(root).as_posix()] = hits
    return found


def test_one_site_turns_a_criterion_identity_into_its_sub_issue() -> None:
    """Counted as SITES, so two resolutions in one allowed module still fail."""
    sites = _sites_in(SOURCE)
    assert sum(len(group) for group in sites.values()) == 1, sites
    assert list(sites) == [_module_of(NativeCriterionResolver)], sites


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

    String constants only, and nothing is executed: a pattern assembled at
    runtime, read from configuration, or spelled with escapes is not seen. The
    walk does not tell a scan from a write either, which is what makes it cheap
    and total over literals — the sources carry no complete checkbox literal at
    all, because the one checkbox they write for a human composes its mark. So
    this is STRICTER than the clause it keeps, and it is a lint approximation
    rather than a decision procedure: `[X]` is also a one-letter subscript,
    which is why only literals are read and never code.

    Both spellings of the marks a pattern accepts are read, the class and the
    alternation, so the shape of the pattern is no cover.
    """
    assert _checkbox_scans(SOURCE) == {}


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
    assert _resolution_sites(ast.parse(source))


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
    assert len(_resolution_sites(ast.parse(source))) == 2


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
    assert _resolution_sites(ast.parse(source))


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
    module = SOURCE / "adapters" / "linear" / "tracker.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))
    assert _resolution_sites(tree) == []


def test_a_protocol_declaration_is_not_an_implementation() -> None:
    """A signature whose whole body is a docstring and an ellipsis resolves nothing."""
    source = (
        "class Role(Protocol):\n"
        "    async def resolve_criterion(self, *, issue_key: str,"
        " criterion_key: str) -> TrackerIssue:\n"
        '        """The one current sub-issue with that key."""\n'
        "        ...\n"
    )
    assert _resolution_sites(ast.parse(source)) == []


@pytest.mark.parametrize(
    "spelling",
    [
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s+\\[[ xX]\\]\\s+")\n',
        # The same pattern spelling its two marks as an alternation instead of
        # a class: the spelling the predecessor detector was caught on.
        'CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s*\\[( |x)\\]\\s*(?P<label>.+)$")\n',
        'def ticked(line):\n    return "- [x] " in line\n',
        'def ticked(line):\n    return line.startswith("- [ ]")\n',
        'ROW = "- [X] {key}: {check}"\n',
    ],
)
def test_each_spelling_of_a_checkbox_scan_is_reported(spelling: str) -> None:
    """Compiled pattern, substring test, prefix test and format template alike.

    Compiled twice over, because how a pattern spells the marks it accepts is
    no part of what it scans for: a class and an alternation read the same
    lines and are the same clause breach.
    """
    assert _checkbox_constants(ast.parse(spelling))


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
    assert _resolution_sites(tree) == []
    assert _checkbox_constants(tree) == []
    assert not _names(tree, frozenset({ROLE}))
