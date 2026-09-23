"""The port's criterion read and criterion mint: one site each, derived.

The read has one declaration on the port and one implementation in the
adapter, and the adapter's descent into the backend is private to it: the
wire listing is reached only by the family read, which is reached only by
the two port members that answer with a criterion family.  A second
implementation, a call of the descent from outside the adapter, or a second
definition that lists children by parent on the wire each redden.

The mint has one declaration, one implementation and one caller: the
criteria stage's own write step.  Its adapter method is identified by the
surface kind the port declaration itself names for the mint and by the
creation payload the adapter's write verifier recognises — not by a save
method, of which the adapter has none, and not by the criterion
classification label, which the classification writer also resolves.
Pinned elsewhere and not repeated here: the identity value's one
construction site, which the criterion-lifecycle conformance module asserts
over the whole package; that the stage rules the criterion sub-issue set is
the proposal's list, which the run-stage owner case asserts; and the two
exact call-site registers that name the member, which are unchanged.  A
segregated writer role for the mint, which the house port shape would
prefer over the aggregate declaration, is for the piece that next touches
the port.

Every name the guard looks for is read off an object — the members off the
port protocols, the privates off the adapter class, the surface off the
enumeration, the module paths off the classes' own modules — so a rename
moves the guard with the code.  The only spelled tokens are the backend's
own field names for a parent and for labels, which have no Python owner to
take them from.  The listing tool is recognised by every name the package
binds to its value, pooled over the whole tree and resolved to each
module's own words, and by the tool's literal, whether the tool is passed
positionally or by keyword — so importing the constant from the module that
binds it, importing it under another name, or spelling the tool inline are
each still a listing.

The mint's callers are counted by what cannot be respelled: every place in
the whole package that names the member at all, called or not.  A member is
reached through the instance that holds it, so its name as an attribute is
the one thing every route to it spells, and handing it on is itself a
naming — binding it to a word, to ``self`` or to a conditional, passing it
as an argument or through a constructor, returning it, wrapping it in a
``partial`` or a lambda, fetching it by ``getattr`` or ``methodcaller`` with
its literal name.  Each of those is a site in the definition that writes
it, whichever module later calls the value, so a base class binding the
member in one module and a subclass calling it in another is a site in the
first.  The listing descent and the family read are still counted as calls,
by the per-module alias walk in the cross-off module.

What it does not see: a member reached by ``getattr`` or ``__dict__`` under a
name built at run time, or by ``eval``; a selection of criterion rows out of
issues some container read already returned, which is not a listing and
which an AST cannot tell from one — that rows come from the port's read is
what the conformance cases over the parametrized tracker fixture establish;
a mint that neither takes the surface nor spells the creation payload,
which would be building its request by subscript and would also be outside
the lease discipline the creation conformance case pins; and the doubles,
because the scanned tree is the shipped package.
"""

import ast
import functools
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from kodezart.adapters.linear.scope_reader import LinearScopeReader
from kodezart.adapters.linear.tracker import _TOOL_LIST_ISSUES, LinearMcpTracker
from kodezart.core.protocols import TrackerCriteriaReader, TrackerPort
from kodezart.services.organize_owner import OrganizeOwner
from kodezart.types.domain.surface import SurfaceKind
from tests.domain.test_criterion_cross_off import (
    callers_of,
    qualified_names,
    source_tree,
)
from tests.identity_guards import _constructor_names
from tests.name_resolution import identity_index, object_key, parsed, references

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "kodezart"


def module_of(owner: type) -> str:
    """The tree-relative path of the module *owner* is defined in."""
    return (
        Path(sys.modules[owner.__module__].__file__ or "")
        .resolve()
        .relative_to(SOURCE_ROOT.resolve())
        .as_posix()
    )


PORT = module_of(TrackerCriteriaReader)
ADAPTER = module_of(LinearMcpTracker)
#: The package every vendor adapter lives under, taken off one of them.
ADAPTERS = ADAPTER.split("/", 1)[0]
SCOPE_READER = module_of(LinearScopeReader)
OWNER = module_of(OrganizeOwner)
READ = TrackerCriteriaReader.read_criteria.__name__
DESCENT = LinearMcpTracker._read_criteria.__name__
FAMILY = LinearMcpTracker._read_criterion_family.__name__
MINT = TrackerPort.create_criterion_if_absent.__name__
#: The surface the port declaration names for the mint, by the enumeration
#: member's own word.
MINT_SURFACE = SurfaceKind.CRITERION_CHILD_SET.name
#: The tool the adapter sends to list an issue's children, read off the
#: adapter's own constant rather than spelled here.
LISTING_TOOL = _TOOL_LIST_ISSUES
#: The backend's field name for the parent a listing is addressed by. It is
#: the wire's word and has no owner in this package to take it from.
PARENT_FIELD = "parentId"
#: The backend's field name for an issue's labels, the wire's word as well.
LABEL_FIELD = "labels"
#: The pair of wire fields that make a save a labelled child creation, and
#: so the payload shape a criterion mint is.
CREATION_KEYS = frozenset({PARENT_FIELD, LABEL_FIELD})


def definitions_of(tree: ast.Module, *, name: str) -> list[str]:
    """Every function definition in *tree* named *name*, by dotted name."""
    where = qualified_names(tree)
    return sorted(
        {
            where[id(node)]
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == name
        }
    )


def _constant_keys(node: ast.Dict) -> set[str]:
    """The string keys this mapping display spells literally."""
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def tool_names(tree: ast.Module, *, value: str) -> set[str]:
    """Every module-level name *tree* binds to the string constant *value*."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and node.value.value == value
        ):
            names |= {
                target.id for target in node.targets if isinstance(target, ast.Name)
            }
    return names


def child_listing_definitions(tree: ast.Module, *, tools: set[str]) -> list[str]:
    """Every definition in *tree* that lists children by parent on the wire.

    Both halves are required: a mapping display addressed by the parent
    field, and a call passing one of the listing tool's own names or the
    tool's literal, positionally or by keyword.  A definition holding a
    nested one holds what it holds, which is how the page loop a listing is
    written as is attributed to the method that addresses the parent.
    """
    where = qualified_names(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        inside = list(ast.walk(node))
        addressed = any(
            isinstance(child, ast.Dict) and PARENT_FIELD in _constant_keys(child)
            for child in inside
        )
        listed = any(
            isinstance(child, ast.Call)
            and any(
                (isinstance(argument, ast.Name) and argument.id in tools)
                or (
                    isinstance(argument, ast.Constant)
                    and argument.value == LISTING_TOOL
                )
                for argument in (
                    *child.args,
                    *(keyword.value for keyword in child.keywords),
                )
            )
            for child in inside
        )
        if addressed and listed:
            found.add(where[id(node)])
    return sorted(found)


def listing_tool_names(sources: dict[str, str]) -> frozenset[str]:
    """Every name *sources* binds to the listing tool, pooled over the map.

    Pooled before any module is scanned, so a listing that imports the
    tool's constant from the module that binds it is a listing where it is
    sent rather than nowhere at all.
    """
    return frozenset(
        name
        for source in sources.values()
        for name in tool_names(ast.parse(source), value=LISTING_TOOL)
    )


def child_listings(tree: ast.Module, pool: frozenset[str]) -> list[str]:
    """The wire listings of one module, the pool resolved to its own words."""
    local: set[str] = set()
    for name in pool:
        local |= _constructor_names(tree, name)
    return child_listing_definitions(tree, tools=local)


def scopes_naming(tree: ast.Module, *, attribute: str) -> list[str]:
    """Every definition in *tree* that reaches *attribute* as an attribute."""
    where = qualified_names(tree)
    return sorted(
        {
            where[id(node)]
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == attribute
        }
    )


def labelled_child_creations(tree: ast.Module) -> list[str]:
    """Every definition holding a mapping display keyed with both wire fields."""
    where = qualified_names(tree)
    return sorted(
        {
            where[id(node)]
            for node in ast.walk(tree)
            if isinstance(node, ast.Dict) and CREATION_KEYS <= _constant_keys(node)
        }
    )


def by_module(
    sources: dict[str, str], finder: Callable[[ast.Module], list[str]]
) -> dict[str, list[str]]:
    """What *finder* reports for each module of *sources* that reports any."""
    found: dict[str, list[str]] = {}
    for module, source in sources.items():
        reported = finder(ast.parse(source))
        if reported:
            found[module] = reported
    return found


def wire_listings(sources: dict[str, str]) -> dict[str, list[str]]:
    """Every module of *sources* that lists children by parent, by module.

    The tool's names are pooled over the whole map once, before any module
    is scanned, and then resolved to each module's own words.
    """
    pool = listing_tool_names(sources)
    return by_module(sources, lambda tree: child_listings(tree, pool))


def test_the_port_criterion_read_has_one_implementation_and_a_private_descent():
    sources = source_tree()

    assert by_module(sources, lambda tree: definitions_of(tree, name=READ)) == {
        ADAPTER: [LinearMcpTracker.read_criteria.__qualname__],
        PORT: [TrackerCriteriaReader.read_criteria.__qualname__],
    }
    assert by_module(sources, lambda tree: callers_of(tree, name=DESCENT)) == {
        ADAPTER: [LinearMcpTracker._read_criterion_family.__qualname__],
    }
    # The exact caller, rather than "some port member": a second member that
    # reached the family read would be a new listing surface and should
    # redden here until it is named. The fire entry is not one of them — it
    # lists nothing and measures membership over the subtree instead.
    assert by_module(sources, lambda tree: callers_of(tree, name=FAMILY)) == {
        ADAPTER: [LinearMcpTracker.read_criteria.__qualname__],
    }
    # Two definitions in the package list children by parent on the wire,
    # and each is named: the adapter's criterion descent, and the scope
    # reader's own recursive walk, which is the container listing a scope
    # resolve is made of and reads no criterion label at all.  Naming the
    # second is deliberate — widening the key would stop the guard seeing a
    # third listing.
    pool = listing_tool_names(sources)
    assert by_module(sources, lambda tree: child_listings(tree, pool)) == {
        ADAPTER: [LinearMcpTracker._read_criteria.__qualname__],
        SCOPE_READER: [LinearScopeReader._subtree.__qualname__],
    }


def test_production_lists_criteria_by_calling_the_port_member():
    """Every listing consumer calls the member, and defines no listing itself.

    The consumer set is derived and is not written down: the restated rule
    is that criterion sub-issues are listed THROUGH the port's read, so
    however many callers there are, each of them is permitted.  What is
    asserted is that there are callers at all and that none of them holds a
    listing of its own.
    """
    sources = source_tree()
    consumers = {
        module: scopes
        for module, scopes in by_module(
            sources, lambda tree: callers_of(tree, name=READ)
        ).items()
        if module not in {PORT, ADAPTER}
    }

    assert consumers
    for module in consumers:
        tree = ast.parse(sources[module])
        for name in (READ, DESCENT, FAMILY):
            assert definitions_of(tree, name=name) == []


#: The three reports the assertion above is made of, each by the name the
#: control below refers to it by.
LISTING_REPORTS = {
    "implementations": lambda sources: by_module(
        sources, lambda tree: definitions_of(tree, name=READ)
    ),
    "descent callers": lambda sources: by_module(
        sources, lambda tree: callers_of(tree, name=DESCENT)
    ),
    "wire listings": wire_listings,
}

#: Each way a second listing surface could arrive: the module it arrives
#: as, the text it arrives as, and the report that must name it.
PLANTED_LISTINGS = {
    "a second implementation": (
        "services/lister.py",
        f"class Lister:\n"
        f"    async def {READ}(self, *, issue_key):\n"
        f"        return ()\n",
        "implementations",
    ),
    "a call of the descent": (
        "services/lister.py",
        f"async def lister(tracker, issue):\n"
        f"    return await tracker.{DESCENT}(parent=issue)\n",
        "descent callers",
    ),
    "a second wire listing": (
        f"{ADAPTERS}/other/reader.py",
        f'_TOOL = "{LISTING_TOOL}"\n'
        "class Second:\n"
        "    async def children(self, key):\n"
        f'        return await self._call(_TOOL, {{"{PARENT_FIELD}": key}})\n',
        "wire listings",
    ),
    "a wire listing by imported constant": (
        f"{ADAPTERS}/other/reader.py",
        f"from {LinearMcpTracker.__module__} import _TOOL_LIST_ISSUES\n"
        "class Second:\n"
        "    async def children(self, key):\n"
        "        return await self._call("
        f'_TOOL_LIST_ISSUES, {{"{PARENT_FIELD}": key}})\n',
        "wire listings",
    ),
    "a wire listing by aliased import": (
        f"{ADAPTERS}/other/reader.py",
        f"from {LinearMcpTracker.__module__} import _TOOL_LIST_ISSUES as _TOOL\n"
        "class Second:\n"
        "    async def children(self, key):\n"
        f'        return await self._call(_TOOL, {{"{PARENT_FIELD}": key}})\n',
        "wire listings",
    ),
    "a wire listing by literal tool name": (
        f"{ADAPTERS}/other/reader.py",
        "class Second:\n"
        "    async def children(self, key):\n"
        f'        return await self._call("{LISTING_TOOL}",'
        f' {{"{PARENT_FIELD}": key}})\n',
        "wire listings",
    ),
    "a wire listing by keyword tool name": (
        f"{ADAPTERS}/other/reader.py",
        "class Second:\n"
        "    async def children(self, key):\n"
        f'        return await self._call(tool="{LISTING_TOOL}",'
        f' arguments={{"{PARENT_FIELD}": key}})\n',
        "wire listings",
    ),
}


@pytest.mark.parametrize("form", sorted(PLANTED_LISTINGS))
def test_a_second_listing_site_is_reported(form):
    sources = source_tree()
    module, text, report = PLANTED_LISTINGS[form]
    assert module not in LISTING_REPORTS[report](sources)

    sources[module] = text

    assert module in LISTING_REPORTS[report](sources)


#: The mint's own definitions, each read off the object: the port's
#: declaration and the adapter's implementation.
MINT_DEFINITIONS = frozenset(
    {
        object_key(TrackerPort.create_criterion_if_absent),
        object_key(LinearMcpTracker.create_criterion_if_absent),
    }
)


def mint_sites(sources: dict[str, str]) -> dict[str, list[str]]:
    """Every definition in *sources* that names the mint, called or not, by module.

    Resolved over the whole map at once, by identity where a receiver is a
    class or module the code names, and by the member's own name where it is
    an instance — the way every caller reaches a port member.
    """
    found: dict[str, set[str]] = {}
    for reference in references(identity_index(parsed(sources)), members={MINT}):
        if reference.key in MINT_DEFINITIONS:
            found.setdefault(reference.module, set()).add(reference.definition)
    return {module: sorted(definitions) for module, definitions in found.items()}


def mint_surfaces(sources: dict[str, str]) -> dict[str, list[str]]:
    """Every scope inside a vendor adapter that names the mint's surface.

    Restricted to the adapter packages on purpose: the stage's own lease and
    the artifact reader name the same surface from the other side of the
    port, and neither of them mints anything.
    """
    inside = {
        module: source
        for module, source in sources.items()
        if module.startswith(f"{ADAPTERS}/")
    }
    return by_module(inside, lambda tree: scopes_naming(tree, attribute=MINT_SURFACE))


def test_the_criteria_stage_is_the_only_caller_of_the_criterion_mint():
    """The mint is declared once, implemented once and named from one step.

    The member, the stage class and its write step are all read off the code,
    so renaming any of them moves the guard.  The count is of every
    definition that names the member, called or not, over the whole package
    at once (KOD-621): a definition that hands the member on names it, so a
    second minting caller is a second site wherever it later calls the value.
    The call sits in a closure of the write step; the step is what the stage
    holds, so the assertion is on that prefix and the closure's own tail
    stays the code's word.  A second closure in the same step would be a
    second naming definition and would redden the count.
    """
    sources = source_tree()

    assert by_module(sources, lambda tree: definitions_of(tree, name=MINT)) == {
        ADAPTER: [LinearMcpTracker.create_criterion_if_absent.__qualname__],
        PORT: [TrackerPort.create_criterion_if_absent.__qualname__],
    }
    callers = mint_sites(sources)

    assert set(callers) == {OWNER}
    assert len(callers[OWNER]) == 1
    assert callers[OWNER][0].startswith(
        f"{OrganizeOwner.__name__}.{OrganizeOwner._author_write.__name__}."
    )


def test_the_adapter_holds_one_minting_method():
    """One scope takes the mint's surface, and it is the one that creates.

    Two independent keys have to agree: the surface kind the port
    declaration names, sought inside the adapter packages, and the labelled
    child creation payload, sought over the whole package.  Each reports one
    scope, they report the same scope, and it is inside the one adapter
    method that implements the member.
    """
    sources = source_tree()
    mint = f"{LinearMcpTracker.create_criterion_if_absent.__qualname__}."

    surfaces = mint_surfaces(sources)
    creations = by_module(sources, labelled_child_creations)

    assert set(surfaces) == {ADAPTER}
    assert len(surfaces[ADAPTER]) == 1
    assert set(creations) == {ADAPTER}
    assert creations[ADAPTER] == surfaces[ADAPTER]
    assert surfaces[ADAPTER][0].startswith(mint)


#: The four reports the two assertions above are made of, each by the name
#: the control below refers to it by.
MINT_REPORTS = {
    "implementations": lambda sources: by_module(
        sources, lambda tree: definitions_of(tree, name=MINT)
    ),
    "callers": mint_sites,
    "surfaces": mint_surfaces,
    "creations": lambda sources: by_module(sources, labelled_child_creations),
}

#: A module the tree does not have, which a planted row may import from.
SECOND_STAGE_MODULE = f"{SOURCE_ROOT.name}.services.second_stage"

#: Each way a second minting site could arrive: the module it arrives as,
#: the text it arrives as, and the report that must name it.
PLANTED_MINTS = {
    "a second caller": (
        "services/second_stage.py",
        f"async def stage(tracker):\n"
        f"    return await tracker.{MINT}(\n"
        f"        parent_key='p', title='t', check='c', do='d', holder='h'\n"
        f"    )\n",
        "callers",
    ),
    # The same call with the member bound to a local word first.  An ordinary
    # second minting site, not reflection: the caller walk resolves the alias
    # rather than comparing the called word (KOD-621).
    "a second caller by bound alias": (
        "services/second_stage.py",
        f"async def stage(tracker):\n"
        f"    mint = tracker.{MINT}\n"
        f"    return await mint(\n"
        f"        parent_key='p', title='t', check='c', do='d', holder='h'\n"
        f"    )\n",
        "callers",
    ),
    # The same member held on an instance in ``__init__`` and called through
    # ``self`` — the way this tree's own services hold a collaborator.  The
    # alias is recorded under its dotted spelling and the call is matched by
    # the whole spelling of its target (KOD-621).
    "a second caller by a member held on self": (
        "services/second_stage.py",
        f"class SecondStage:\n"
        f"    def __init__(self, tracker):\n"
        f"        self._mint = tracker.{MINT}\n"
        f"\n"
        f"    async def stage(self):\n"
        f"        return await self._mint(\n"
        f"            parent_key='p', title='t', check='c', do='d', holder='h'\n"
        f"        )\n",
        "callers",
    ),
    # The shapes that hand the member on before anything calls it: each names
    # the member in the definition that writes it, which is the site
    # (KOD-621).
    "a second caller through a method that returns the member": (
        "services/second_stage.py",
        f"class SecondStage:\n"
        f"    def __init__(self, tracker):\n"
        f"        self._tracker = tracker\n"
        f"\n"
        f"    def _minter(self):\n"
        f"        return self._tracker.{MINT}\n"
        f"\n"
        f"    async def stage(self):\n"
        f"        return await self._minter()(\n"
        f"            parent_key='p', title='t', check='c', do='d', holder='h'\n"
        f"        )\n",
        "callers",
    ),
    "a second caller by the member handed as an argument": (
        "services/second_stage.py",
        f"async def _run(mint):\n"
        f"    return await mint(\n"
        f"        parent_key='p', title='t', check='c', do='d', holder='h'\n"
        f"    )\n"
        f"\n"
        f"async def stage(tracker):\n"
        f"    return await _run(tracker.{MINT})\n",
        "callers",
    ),
    "a second caller by a conditional binding on self": (
        "services/second_stage.py",
        f"class SecondStage:\n"
        f"    def __init__(self, tracker):\n"
        f"        self._mint = tracker.{MINT} if tracker else None\n",
        "callers",
    ),
    "a second caller by the member injected through a constructor": (
        "services/second_wiring.py",
        f"from {SECOND_STAGE_MODULE} import SecondStage\n"
        f"\n"
        f"def wire(tracker):\n"
        f"    return SecondStage(tracker.{MINT})\n",
        "callers",
    ),
    "a second caller by a base class binding the member on self": (
        "services/second_base.py",
        f"class SecondBase:\n"
        f"    def __init__(self, tracker):\n"
        f"        self._mint = tracker.{MINT}\n",
        "callers",
    ),
    "a second caller by a walrus": (
        "services/second_stage.py",
        f"async def stage(tracker):\n"
        f"    if mint := tracker.{MINT}:\n"
        f"        return await mint(\n"
        f"            parent_key='p', title='t', check='c', do='d', holder='h'\n"
        f"        )\n",
        "callers",
    ),
    "a second caller by a tuple unpacking": (
        "services/second_stage.py",
        f"async def stage(tracker):\n"
        f"    (mint,) = (tracker.{MINT},)\n"
        f"    return await mint(\n"
        f"        parent_key='p', title='t', check='c', do='d', holder='h'\n"
        f"    )\n",
        "callers",
    ),
    "a second caller by a partial": (
        "services/second_stage.py",
        f"from functools import partial\n"
        f"\n"
        f"async def stage(tracker):\n"
        f"    mint = partial(tracker.{MINT}, holder='h')\n"
        f"    return await mint(parent_key='p', title='t', check='c', do='d')\n",
        "callers",
    ),
    "a second caller by getattr with the member's literal name": (
        "services/second_stage.py",
        f"async def stage(tracker):\n"
        f"    return await getattr(tracker, {MINT!r})(\n"
        f"        parent_key='p', title='t', check='c', do='d', holder='h'\n"
        f"    )\n",
        "callers",
    ),
    "a second caller by methodcaller with the member's literal name": (
        "services/second_stage.py",
        f"from operator import methodcaller\n"
        f"\n"
        f"async def stage(tracker):\n"
        f"    return await methodcaller(\n"
        f"        {MINT!r}, parent_key='p', title='t', check='c', do='d', holder='h'\n"
        f"    )(tracker)\n",
        "callers",
    ),
    "a second implementation": (
        "services/second_port.py",
        f"class Second:\n"
        f"    async def {MINT}(self, *, parent_key, title, check, do, holder):\n"
        f"        return None\n",
        "implementations",
    ),
    "a second surface holder": (
        f"{ADAPTERS}/other/minter.py",
        f"from {SurfaceKind.__module__} import {SurfaceKind.__name__}\n"
        f"class Other:\n"
        f"    async def mint(self):\n"
        f"        return {SurfaceKind.__name__}.{MINT_SURFACE}\n",
        "surfaces",
    ),
    "a second creation": (
        f"{ADAPTERS}/other/creation.py",
        f"class Other:\n"
        f"    async def mint(self, parent, label):\n"
        f"        return await self._send(\n"
        f'            "save",\n'
        f'            {{"{PARENT_FIELD}": parent, "{LABEL_FIELD}": [label]}},\n'
        f"        )\n",
        "creations",
    ),
}


#: The modules a row needs beside the one it plants: the subclass that calls
#: what a base class in another module bound on ``self``.
ALSO_PLANTED = {
    "a second caller by a base class binding the member on self": {
        "services/second_stage.py": (
            f"from {SOURCE_ROOT.name}.services.second_base import SecondBase\n"
            f"\n"
            f"class SecondStage(SecondBase):\n"
            f"    async def stage(self):\n"
            f"        return await self._mint(\n"
            f"            parent_key='p', title='t', check='c', do='d', holder='h'\n"
            f"        )\n"
        ),
    },
}


@functools.cache
def shipped_mint_report(report: str) -> dict[str, list[str]]:
    """What *report* names over the shipped package, read once."""
    return MINT_REPORTS[report](source_tree())


@pytest.mark.parametrize("form", sorted(PLANTED_MINTS))
def test_a_second_mint_caller_and_a_second_minting_method_are_each_reported(form):
    sources = source_tree()
    module, text, report = PLANTED_MINTS[form]
    assert module not in shipped_mint_report(report)

    sources[module] = text
    sources.update(ALSO_PLANTED.get(form, {}))

    assert module in MINT_REPORTS[report](sources)
