"""One site turns a criterion identity into its sub-issue; its holders take the role.

A consumer that needs ONE criterion may depend on the narrow resolving role and
nothing wider; a consumer that needs the whole family takes the family reader.
So two things are asserted over the shipped sources (KOD-651): the tree holds
exactly one function that resolves an identity to a row, and no module naming
the narrow role also names the family surface — which is what stops a consumer
being handed a roster plus permission to search it.  A third, separate clause is
kept: no module carries a checkbox shape it could locate a write target by.

Every name the walk keys on is read off the shipped objects — the role, its one
method, that method's own identity parameters, the row it returns, and the two
modules where the role and its implementation are DECLARED.  A declaration is
not a dependency, so those two are the dependency rule's only exemptions, and
adding a second method to either role reddens the single-name unpackings below
rather than silently halving what is checked.

The resolution walk is a SIGNATURE shape, not an expression walk: a function
that reads the family, declares every identity parameter the role declares, and
returns exactly one row is a resolution however it performs the lookup — by
dict index, by ``.get``, by a keys list and ``.index()``, or behind an alias.
The refuted predecessor walked comparison nodes and so missed all four, and it
counted MODULES rather than sites, which let two resolutions in one allowed
module pass.  The count here is of sites.

What this cannot see: the walk is textual and executes nothing, so a resolution
assembled at runtime or reached through a wrapper whose own signature declares
no identity is outside it.  It is a boundary check over declared surfaces, not
a decision procedure over behaviour; the behaviour that a native key cannot be
redirected by identical text or parent prose is pinned by the resolution suite,
not here.
"""

import ast
import inspect
import re
import sys
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
ROW = TrackerIssue.__name__
#: The return annotations that say "exactly one criterion row", optional
#: included: returning the row or nothing is still resolving one identity.
ONE_ROW = frozenset({ROW, f"{ROW} | None"})


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
    """Whether every identity the role addresses a criterion by is a parameter."""
    args = node.args
    declared = {
        argument.arg for argument in (*args.posonlyargs, *args.args, *args.kwonlyargs)
    }
    return IDENTITY_PARAMETERS <= declared


def _returns_one_row(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether the declared return is exactly one criterion row."""
    return node.returns is not None and ast.unparse(node.returns) in ONE_ROW


def _resolution_sites(tree: ast.AST) -> list[str]:
    """Each function in this module that resolves a criterion identity to a row."""
    return sorted(
        f"{node.name}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _reads_the_family(node)
        and _declares_an_identity(node)
        and _returns_one_row(node)
    )


def _sites_in(root: Path) -> dict[str, list[str]]:
    found = {}
    for path in sorted(root.rglob("*.py")):
        sites = _resolution_sites(ast.parse(path.read_text(encoding="utf-8")))
        if sites:
            found[path.relative_to(root).as_posix()] = sites
    return found


def _family_dependents(root: Path) -> dict[str, list[str]]:
    """Each module that names the narrow role and the family surface both."""
    found = {}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative in DECLARATIONS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not _names(tree, frozenset({ROLE})):
            continue
        also = sorted(
            name
            for name in (FAMILY_ROLE, FAMILY_READ)
            if _names(tree, frozenset({name}))
        )
        if also:
            found[relative] = also
    return found


#: A checkbox once written, and the character class a pattern spells one with.
CHECKBOX = re.compile(r"\[[ xX]{1,4}\]")


def checkbox_shapes(text: str) -> list[str]:
    """Each complete checkbox shape in *text*: `[ ]`, `[x]`, or a class naming both."""
    return [
        found.group(0)
        for found in CHECKBOX.finditer(text)
        if len(found.group(0)) == 3 or " " in found.group(0)
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
    """
    assert _checkbox_scans(SOURCE) == {}


@pytest.mark.parametrize(
    "lookup",
    [
        "    by_key = {row.issue_key: row for row in rows}\n"
        "    return by_key[criterion_key]\n",
        "    by_key = {row.issue_key: row for row in rows}\n"
        "    return by_key.get(criterion_key)\n",
        "    keys = [row.issue_key for row in rows]\n"
        "    return rows[keys.index(criterion_key)]\n",
        "    wanted = criterion_key\n"
        "    return [row for row in rows if row.issue_key == wanted][0]\n",
    ],
)
def test_the_refused_lookup_shapes_are_reported(lookup: str) -> None:
    """Every form the refuted comparison walk missed is one signature shape here."""
    source = (
        "async def locate(*, tracker, issue_key: str, criterion_key: str"
        ") -> TrackerIssue | None:\n"
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


def test_a_family_read_without_a_criterion_identity_is_not_a_resolution() -> None:
    """Creating a criterion that is absent addresses no key: there is none yet."""
    source = (
        "async def create_if_absent(*, parent_key: str, check: str"
        ") -> TrackerIssue:\n"
        "    children = await self.read_criteria(issue_key=parent_key)\n"
        "    existing = existing_criterion(parent_key=parent_key, check=check,"
        " children=children)\n"
        "    return existing if existing is not None else await create(check)\n"
    )
    assert _resolution_sites(ast.parse(source)) == []


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
        'def ticked(line):\n    return "- [x] " in line\n',
        'def ticked(line):\n    return line.startswith("- [ ]")\n',
        'ROW = "- [X] {key}: {check}"\n',
    ],
)
def test_each_spelling_of_a_checkbox_scan_is_reported(spelling: str) -> None:
    """Compiled pattern, substring test, prefix test and format template alike."""
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
