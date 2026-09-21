"""Criterion sub-issues are listed through the port's criterion read alone.

The read has one declaration on the port and one implementation in the
adapter, and the adapter's descent into the backend is private to it: the
wire listing is reached only by the family read, which is reached only by
the two port members that answer with a criterion family.  A second
implementation, a call of the descent from outside the adapter, or a second
definition that lists children by parent on the wire each redden.

Every name the guard looks for is read off an object — the member off the
port's role protocol, the privates off the adapter class, the module paths
off the classes' own modules — so a rename moves the guard with the code.
The only spelled token is the backend's own field name for a parent, which
has no Python owner to take it from.

What it does not see: a member reached by reflection; a selection of
criterion rows out of issues some container read already returned, which is
not a listing and which an AST cannot tell from one — that rows come from
the port's read is what the conformance cases over the parametrized tracker
fixture establish; and the double, because the scanned tree is the shipped
package and the doubles live outside it.
"""

import ast
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from kodezart.adapters.linear.scope_reader import LinearScopeReader
from kodezart.adapters.linear.tracker import _TOOL_LIST_ISSUES, LinearMcpTracker
from kodezart.core.protocols import TrackerCriteriaReader
from tests.domain.test_criterion_cross_off import (
    callers_of,
    qualified_names,
    source_tree,
)

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
SCOPE_READER = module_of(LinearScopeReader)
READ = TrackerCriteriaReader.read_criteria.__name__
DESCENT = LinearMcpTracker._read_criteria.__name__
FAMILY = LinearMcpTracker._read_criterion_family.__name__
#: The tool the adapter sends to list an issue's children, read off the
#: adapter's own constant rather than spelled here.
LISTING_TOOL = _TOOL_LIST_ISSUES
#: The backend's field name for the parent a listing is addressed by. It is
#: the wire's word and has no owner in this package to take it from.
PARENT_FIELD = "parentId"


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
    field, and a call passing one of the listing tool's own names.  A
    definition holding a nested one holds what it holds, which is how the
    page loop a listing is written as is attributed to the method that
    addresses the parent.
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
                isinstance(argument, ast.Name) and argument.id in tools
                for argument in child.args
            )
            for child in inside
        )
        if addressed and listed:
            found.add(where[id(node)])
    return sorted(found)


def child_listings(tree: ast.Module) -> list[str]:
    """The wire listings of one module, its own tool names resolved first."""
    return child_listing_definitions(tree, tools=tool_names(tree, value=LISTING_TOOL))


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


def test_the_port_criterion_read_has_one_implementation_and_a_private_descent():
    sources = source_tree()

    assert by_module(sources, lambda tree: definitions_of(tree, name=READ)) == {
        ADAPTER: [LinearMcpTracker.read_criteria.__qualname__],
        PORT: [TrackerCriteriaReader.read_criteria.__qualname__],
    }
    assert by_module(sources, lambda tree: callers_of(tree, name=DESCENT)) == {
        ADAPTER: [LinearMcpTracker._read_criterion_family.__qualname__],
    }
    # The exact pair, rather than "some port member": a third member that
    # reached the family read would be a new listing surface and should
    # redden here until it is named.
    assert by_module(sources, lambda tree: callers_of(tree, name=FAMILY)) == {
        ADAPTER: sorted(
            [
                LinearMcpTracker.read_criteria.__qualname__,
                LinearMcpTracker.read_fire_spec.__qualname__,
            ]
        ),
    }
    # Two definitions in the package list children by parent on the wire,
    # and each is named: the adapter's criterion descent, and the scope
    # reader's own recursive walk, which is the container listing a scope
    # resolve is made of and reads no criterion label at all.  Naming the
    # second is deliberate — widening the key would stop the guard seeing a
    # third listing.
    assert by_module(sources, child_listings) == {
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
    "wire listings": lambda sources: by_module(sources, child_listings),
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
        "adapters/linear/second_reader.py",
        f'_TOOL = "{LISTING_TOOL}"\n'
        "class Second:\n"
        "    async def children(self, key):\n"
        f'        return await self._call(_TOOL, {{"{PARENT_FIELD}": key}})\n',
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
