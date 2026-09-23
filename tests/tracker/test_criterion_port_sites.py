"""The port's criterion read and criterion mint: one site each, derived.

The read has one declaration on the port and one implementation in the
adapter, and the adapter's descent into the backend is private to it: the
wire listing is reached only by the family read, which is reached only by
the two port members that answer with a criterion family.  A second
implementation, a call of the descent from outside the adapter, or a second
definition that lists children by parent on the wire each redden.

The mint is declared on the aggregate port and on the segregated writer
role narrowed out of it, implemented once, and called once per holder: the
criteria stage's own write step, and the mark one lost designated assertion
leaves on its lane.  Its adapter method is identified by the surface kind
the port declaration itself names for the mint and by the creation payload
the adapter's write verifier recognises — not by a save method, of which the
adapter has none, and not by the criterion classification label, which the
classification writer also resolves.  Pinned elsewhere and not repeated
here: the identity value's one construction site, which the
criterion-lifecycle conformance module asserts over the whole package; that
the stage rules the criterion sub-issue set is the proposal's list, which
the run-stage owner case asserts; and the two exact call-site registers that
name the member.

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

A creation payload is read wherever it is written out: a mapping display,
a ``**`` of one, a ``dict(...)`` call with keywords or a written-out
mapping, and a mapping one definition builds up on one receiver by
assignment, subscript, ``update``, ``|=`` or ``setdefault``.

Outside the reach of every report here, the one stated limit: a value
handed across a function boundary, where the other function is not
resolved at this site (returned from a helper, stored on an object and read
elsewhere, or passed through a container built elsewhere); a name built at
run time; a binding made only when a function runs (``setattr`` or
``globals()`` inside a function body).  A committed case holds each of
those shapes as unseen.  Also not read: a selection of criterion rows out
of issues some container read already returned, which is not a listing and
which an AST cannot tell from one — that rows come from the port's read is
what the conformance cases over the parametrized tracker fixture
establish; and the doubles, because the scanned tree is the shipped
package.
"""

import ast
import functools
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from kodezart.adapters.linear.scope_reader import LinearScopeReader
from kodezart.adapters.linear.tracker import _TOOL_LIST_ISSUES, LinearMcpTracker
from kodezart.core.protocols import (
    CriterionMinter,
    TrackerCriteriaReader,
    TrackerPort,
)
from kodezart.services.organize_owner import OrganizeOwner
from kodezart.services.weakened_assertions import WeakenedAssertionMarks
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
MARKS = module_of(WeakenedAssertionMarks)
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


def _literal(node: ast.AST | None) -> str | None:
    """The text of a string constant, or ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_dict_call(node: ast.AST) -> bool:
    """Whether *node* calls the builtin mapping type, by its own name."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == dict.__name__
    )


def mapping_keys(node: ast.AST | None) -> set[str]:
    """The string keys a mapping written out in the code spells literally.

    A mapping display, its ``**`` of another written out included, and a
    call of ``dict`` with keywords, a ``**`` of a written-out mapping or a
    written-out mapping as its argument.  Anything else spells no key here.
    """
    if isinstance(node, ast.Dict):
        found: set[str] = set()
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                found |= mapping_keys(value)
            elif (text := _literal(key)) is not None:
                found.add(text)
        return found
    if isinstance(node, ast.Call) and _is_dict_call(node):
        found = set()
        for keyword in node.keywords:
            found |= (
                {keyword.arg}
                if keyword.arg is not None
                else mapping_keys(keyword.value)
            )
        for argument in node.args:
            found |= mapping_keys(argument)
        return found
    return set()


def _written(node: ast.AST) -> tuple[str, set[str]] | None:
    """A receiver and the keys one statement or call writes onto it.

    ``name = <mapping>`` (plain or annotated), ``name[<literal>] = ...``,
    ``name |= <mapping>``, ``name.update(<mapping> or keywords)`` and
    ``name.setdefault(<literal>, ...)``, the receiver by its dotted spelling.
    """
    if isinstance(node, ast.Assign | ast.AnnAssign):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Subscript)
                and (key := _literal(target.slice)) is not None
            ):
                spelled = ast.unparse(target.value)
                return spelled, {key}
            keys = mapping_keys(node.value)
            if keys and isinstance(target, ast.Name | ast.Attribute):
                return ast.unparse(target), keys
    if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.BitOr):
        keys = mapping_keys(node.value)
        if keys:
            return ast.unparse(node.target), keys
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        receiver = ast.unparse(node.func.value)
        if node.func.attr == dict.update.__name__:
            keys = {keyword.arg for keyword in node.keywords if keyword.arg}
            for argument in node.args:
                keys |= mapping_keys(argument)
            return (receiver, keys) if keys else None
        if node.func.attr == dict.setdefault.__name__ and node.args:
            key = _literal(node.args[0])
            return (receiver, {key}) if key is not None else None
    return None


def mappings_keyed(tree: ast.Module, *, keys: frozenset[str]) -> list[str]:
    """Every definition in *tree* that writes out a mapping keyed with *keys*.

    One mapping written out whole (see :func:`mapping_keys`), or one
    receiver the definition builds up key by key (see :func:`_written`):
    the keys written onto the same spelling inside the same definition are
    pooled.
    """
    where = qualified_names(tree)
    found: set[str] = set()
    pooled: dict[tuple[str, str], set[str]] = {}
    for node in ast.walk(tree):
        if keys <= mapping_keys(node):
            found.add(where[id(node)])
        written = _written(node)
        if written is not None:
            receiver, spelled = written
            pooled.setdefault((where[id(node)], receiver), set()).update(spelled)
    found |= {scope for (scope, _), spelled in pooled.items() if keys <= spelled}
    return sorted(found)


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

    Both halves are required: a mapping written out (see
    :func:`mapping_keys`) addressed by the parent field, and a call passing
    one of the listing tool's own names or the tool's literal, positionally
    or by keyword.  A definition holding a nested one holds what it holds,
    which is how the page loop a listing is written as is attributed to the
    method that addresses the parent.
    """
    where = qualified_names(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        inside = list(ast.walk(node))
        addressed = any(PARENT_FIELD in mapping_keys(child) for child in inside)
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
    """Every definition that writes out a mapping keyed with both wire fields."""
    return mappings_keyed(tree, keys=CREATION_KEYS)


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
    "a wire listing addressed by dict keywords": (
        f"{ADAPTERS}/other/reader.py",
        f'_TOOL = "{LISTING_TOOL}"\n'
        "class Second:\n"
        "    async def children(self, key):\n"
        f"        return await self._call(_TOOL, dict({PARENT_FIELD}=key))\n",
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


#: The mint's own definitions, each read off the object: the port's two
#: declarations, on the aggregate and on the narrow minting role, and the
#: adapter's implementation.
MINT_DEFINITIONS = frozenset(
    {
        object_key(CriterionMinter.create_criterion_if_absent),
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


def callers_outside_the_holders(callers: dict[str, list[str]]) -> list[str]:
    """The modules naming the mint other than its two holders, sorted.

    The one comparison the guard below and the planted control share, so a
    loosened comparison fails the control rather than passing silently.
    """
    return sorted(set(callers) - {OWNER, MARKS})


def test_the_criteria_stage_is_the_only_caller_of_the_criterion_mint():
    """The mint is declared on two roles, implemented once, named once per holder.

    The member, the classes and their steps are all read off the code, so
    renaming any of them moves the guard.  The port module declares the
    aggregate and the narrow minting role; the adapter implements it once.
    The count is of every definition that names the member, called or not,
    over the whole package at once (KOD-621): a definition that hands the
    member on names it, so a second minting caller is a second site wherever
    it later calls the value.  The stage's call sits in a closure of its write
    step, and the step is what the stage holds, so that assertion is on the
    prefix and the closure's own tail stays the code's word; a second closure
    in the same step would be a second naming definition and would redden the
    count.  The mark's call sits in the method's own body, and the equality
    over the callers is exact, so a third holder of the mint still reddens.
    """
    sources = source_tree()

    assert by_module(sources, lambda tree: definitions_of(tree, name=MINT)) == {
        ADAPTER: [LinearMcpTracker.create_criterion_if_absent.__qualname__],
        PORT: sorted(
            {
                CriterionMinter.create_criterion_if_absent.__qualname__,
                TrackerPort.create_criterion_if_absent.__qualname__,
            }
        ),
    }
    callers = mint_sites(sources)

    assert callers_outside_the_holders(callers) == []
    assert len(callers[OWNER]) == 1
    assert callers[OWNER][0].startswith(
        f"{OrganizeOwner.__name__}.{OrganizeOwner._author_write.__name__}."
    )
    assert callers[MARKS] == [
        f"{WeakenedAssertionMarks.__name__}."
        f"{WeakenedAssertionMarks.refuse_weakening.__name__}"
    ]


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


#: The five reports the two assertions above are made of, each by the name
#: the control below refers to it by.
MINT_REPORTS = {
    "implementations": lambda sources: by_module(
        sources, lambda tree: definitions_of(tree, name=MINT)
    ),
    "callers": mint_sites,
    "callers outside the holders": lambda sources: callers_outside_the_holders(
        mint_sites(sources)
    ),
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
        "callers outside the holders",
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
    "a second creation by dict keywords": (
        f"{ADAPTERS}/other/creation.py",
        "class Other:\n"
        "    async def mint(self, parent, label):\n"
        "        return await self._send(\n"
        '            "save",\n'
        f"            dict(title='t', {PARENT_FIELD}=parent, {LABEL_FIELD}=[label]),\n"
        "        )\n",
        "creations",
    ),
    "a second creation by dict of a spread display": (
        f"{ADAPTERS}/other/creation.py",
        "class Other:\n"
        "    async def mint(self, parent, label):\n"
        "        return await self._send(\n"
        '            "save",\n'
        f'            dict(**{{"{PARENT_FIELD}": parent}}, {LABEL_FIELD}=[label]),\n'
        "        )\n",
        "creations",
    ),
    "a second creation by dict of a display": (
        f"{ADAPTERS}/other/creation.py",
        "class Other:\n"
        "    async def mint(self, parent, label):\n"
        "        return await self._send(\n"
        '            "save",\n'
        f'            dict({{"{PARENT_FIELD}": parent}}, {LABEL_FIELD}=[label]),\n'
        "        )\n",
        "creations",
    ),
    "a second creation by a display spread into a display": (
        f"{ADAPTERS}/other/creation.py",
        "class Other:\n"
        "    async def mint(self, parent, label):\n"
        "        return await self._send(\n"
        '            "save",\n'
        f'            {{**{{"{PARENT_FIELD}": parent}}, "{LABEL_FIELD}": [label]}},\n'
        "        )\n",
        "creations",
    ),
    "a second creation built up by subscript": (
        f"{ADAPTERS}/other/creation.py",
        "class Other:\n"
        "    async def mint(self, parent, label):\n"
        "        payload = {'title': 't'}\n"
        f"        payload[{PARENT_FIELD!r}] = parent\n"
        f"        payload[{LABEL_FIELD!r}] = [label]\n"
        '        return await self._send("save", payload)\n',
        "creations",
    ),
    "a second creation built up from an annotated display": (
        f"{ADAPTERS}/other/creation.py",
        "class Other:\n"
        "    async def mint(self, parent, label):\n"
        f"        payload: dict[str, object] = {{{PARENT_FIELD!r}: parent}}\n"
        f"        payload[{LABEL_FIELD!r}] = [label]\n"
        '        return await self._send("save", payload)\n',
        "creations",
    ),
    "a second creation built up by update keywords": (
        f"{ADAPTERS}/other/creation.py",
        "class Other:\n"
        "    async def mint(self, parent, label):\n"
        "        payload = {}\n"
        f"        payload.update({PARENT_FIELD}=parent, {LABEL_FIELD}=[label])\n"
        '        return await self._send("save", payload)\n',
        "creations",
    ),
    "a second creation built up by update of a display": (
        f"{ADAPTERS}/other/creation.py",
        "class Other:\n"
        "    async def mint(self, parent, label):\n"
        "        payload = {}\n"
        f"        payload.update({{{PARENT_FIELD!r}: parent}})\n"
        f"        payload.update({{{LABEL_FIELD!r}: [label]}})\n"
        '        return await self._send("save", payload)\n',
        "creations",
    ),
    "a second creation built up by an in-place union": (
        f"{ADAPTERS}/other/creation.py",
        "class Other:\n"
        "    async def mint(self, parent, label):\n"
        f"        payload = {{{PARENT_FIELD!r}: parent}}\n"
        f"        payload |= {{{LABEL_FIELD!r}: [label]}}\n"
        '        return await self._send("save", payload)\n',
        "creations",
    ),
    "a second creation built up on an attribute": (
        f"{ADAPTERS}/other/creation.py",
        "class Other:\n"
        "    async def mint(self, parent, label):\n"
        f"        self._payload = {{{PARENT_FIELD!r}: parent}}\n"
        f"        self._payload[{LABEL_FIELD!r}] = [label]\n"
        '        return await self._send("save", self._payload)\n',
        "creations",
    ),
    "a second creation built up by setdefault": (
        f"{ADAPTERS}/other/creation.py",
        "class Other:\n"
        "    async def mint(self, parent, label):\n"
        "        payload = {}\n"
        f"        payload.setdefault({PARENT_FIELD!r}, parent)\n"
        f"        payload.setdefault({LABEL_FIELD!r}, [label])\n"
        '        return await self._send("save", payload)\n',
        "creations",
    ),
}


#: The one stated limit, a case per shape the reports can meet: each is a
#: second minting site the reports do not see, held here so the limit is a
#: fact the tests hold rather than a claim.
UNSEEN_MINTS = {
    "a value handed across a function boundary": (
        f"{ADAPTERS}/other/creation.py",
        "def _addressed(parent):\n"
        f"    return {{{PARENT_FIELD!r}: parent}}\n"
        "class Other:\n"
        "    async def mint(self, parent, label):\n"
        "        payload = _addressed(parent)\n"
        f"        payload[{LABEL_FIELD!r}] = [label]\n"
        '        return await self._send("save", payload)\n',
        "creations",
    ),
    "a name built at run time": (
        "services/second_stage.py",
        "async def stage(tracker):\n"
        f"    word = '_'.join({tuple(MINT.split('_'))!r})\n"
        "    return await getattr(tracker, word)(\n"
        "        parent_key='p', title='t', check='c', do='d', holder='h'\n"
        "    )\n",
        "callers",
    ),
    "a binding made only when a function runs": (
        f"{ADAPTERS}/other/creation.py",
        "class Other:\n"
        "    async def mint(self, request, parent, label):\n"
        f"        setattr(request, {PARENT_FIELD!r}, parent)\n"
        f"        setattr(request, {LABEL_FIELD!r}, [label])\n"
        '        return await self._send("save", vars(request))\n',
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


@pytest.mark.parametrize("shape", sorted(UNSEEN_MINTS))
def test_each_shape_of_the_stated_limit_stays_unseen(shape):
    """The limit the module states is exactly what the reports do not see."""
    sources = source_tree()
    module, text, report = UNSEEN_MINTS[shape]

    sources[module] = text

    assert module not in MINT_REPORTS[report](sources)
