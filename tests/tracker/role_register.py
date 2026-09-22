"""The tracker port's member register, read off the port and the tree.

A non-test module the role guards share, so the derivations they assert
over are stated once. Every set a guard compares is computed here from a
live object or from the source text; the only literal sets are the two
exemptions, which are named exemptions rather than scanned surfaces.
"""

import ast
import re
from collections.abc import Mapping
from functools import cache
from pathlib import Path

from typing_extensions import get_protocol_members

from kodezart.adapters.linear.tracker import LinearMcpTracker
from kodezart.core.protocols import TrackerPort
from tests.domain.test_criterion_cross_off import SOURCE_ROOT
from tests.tracker.test_criterion_port_sites import module_of

TESTS_ROOT = Path(__file__).parents[1]

#: The name of the composed surface, off the object rather than spelled.
AGGREGATE = TrackerPort.__name__

#: The port module and the vendor adapter package, by tree-relative path,
#: read off the classes that live in them.
PORT_MODULE = module_of(TrackerPort)
ADAPTERS = module_of(LinearMcpTracker).split("/", 1)[0]

#: The four run-record members KOD-836 holds until KOD-798 lands. Named, not
#: scanned: the exemption is the thing that criterion states, and it is
#: deleted with the members when KOD-798 deletes them.
RUN_RECORD_EXEMPTION = frozenset(
    {"record_run_alarm", "read_run_alarm", "post_run_event", "lane_run_events"}
)

#: The authorship read KOD-390 names as the read its body refusal uses. It has
#: no production caller until that criterion adds one, and that build deletes
#: this exemption by adding the caller.
EXEMPT_UNTIL_KOD_390 = frozenset({"read_surface_authorship"})


def port_members() -> frozenset[str]:
    """Every member of the whole port, through its bases."""
    return frozenset(get_protocol_members(TrackerPort))


def call_pattern(name: str) -> re.Pattern[str]:
    """A call of *name* as a member, whatever the receiver is spelled."""
    return re.compile(rf"\.{re.escape(name)}\s*\(")


def production_text(sources: Mapping[str, str]) -> str:
    """The shipped tree a caller counts in: all of it but the port and adapters."""
    return "\n".join(
        text
        for path, text in sources.items()
        if path != PORT_MODULE and not path.startswith(f"{ADAPTERS}/")
    )


def zero_callers(sources: Mapping[str, str], members: frozenset[str]) -> frozenset[str]:
    """Every one of *members* that no production module calls."""
    text = production_text(sources)
    return frozenset(name for name in members if not call_pattern(name).search(text))


def tree_under_tests() -> dict[str, str]:
    """The test tree as text, keyed by its path under ``tests/``."""
    return {
        path.relative_to(TESTS_ROOT).as_posix(): path.read_text()
        for path in sorted(TESTS_ROOT.rglob("*.py"))
    }


def port_module_text() -> str:
    """The port module's own source, which the register is declared in."""
    return (SOURCE_ROOT / PORT_MODULE).read_text()


@cache
def protocol_defs(text: str) -> dict[str, ast.ClassDef]:
    """Every ``Protocol`` class the port module declares, by name."""
    return {
        node.name: node
        for node in ast.parse(text).body
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases
        )
    }


@cache
def own_declarations(text: str) -> dict[str, frozenset[str]]:
    """The public members each of those classes declares in its own body."""
    return {
        name: frozenset(
            item.name
            for item in node.body
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
            and not item.name.startswith("_")
        )
        for name, node in protocol_defs(text).items()
    }


@cache
def declared_bases(text: str) -> dict[str, tuple[str, ...]]:
    """Each class's own bases, by name, without ``Protocol`` itself."""
    return {
        name: tuple(
            base.id
            for base in node.bases
            if isinstance(base, ast.Name) and base.id != "Protocol"
        )
        for name, node in protocol_defs(text).items()
    }


@cache
def composed(text: str, name: str) -> frozenset[str]:
    """Every class *name* composes, however deep the composition goes."""
    bases = declared_bases(text)
    reached: set[str] = set()
    frontier = list(bases.get(name, ()))
    while frontier:
        base = frontier.pop()
        if base in reached or base not in bases:
            continue
        reached.add(base)
        frontier.extend(bases[base])
    return frozenset(reached)


@cache
def members_declared(text: str, name: str) -> frozenset[str]:
    """Everything *name* answers: its own declarations and the ones it composes."""
    own = own_declarations(text)
    return frozenset(own.get(name, frozenset())).union(
        *(own[base] for base in composed(text, name)), frozenset()
    )


@cache
def roles(text: str) -> frozenset[str]:
    """Every protocol in the port module that is a role of the tracker surface.

    A role is a protocol whose whole answer is a non-empty part of the
    aggregate's: the consumer roles are not bases of the aggregate, so its
    own composition cannot see them and the subset is what identifies them.
    """
    surface = port_members()
    return frozenset(
        name
        for name in own_declarations(text)
        if name != AGGREGATE
        and members_declared(text, name)
        and members_declared(text, name) <= surface
    )


@cache
def declaring_roles(text: str) -> frozenset[str]:
    """The roles that declare a member of their own."""
    own = own_declarations(text)
    return frozenset(name for name in roles(text) if own[name])


def twice_declared(text: str) -> dict[str, tuple[str, ...]]:
    """Every member declared on more than one role, with the roles that do."""
    own = own_declarations(text)
    places: dict[str, list[str]] = {}
    for name in sorted(roles(text)):
        for member in sorted(own[name]):
            places.setdefault(member, []).append(name)
    return {member: tuple(names) for member, names in places.items() if len(names) > 1}


def redeclared_from_a_base(text: str) -> dict[str, tuple[str, ...]]:
    """Every role that declares a member one of the roles it composes declares."""
    own = own_declarations(text)
    report: dict[str, tuple[str, ...]] = {}
    for name in sorted(roles(text)):
        shadowed = sorted(
            member
            for base in composed(text, name)
            for member in own.get(base, ())
            if member in own[name]
        )
        if shadowed:
            report[name] = tuple(shadowed)
    return report
