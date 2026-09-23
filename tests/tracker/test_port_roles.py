"""The tracker port as role interfaces named by consumer (KOD-833, KOD-834, KOD-836).

Every member of the tracker surface is declared on exactly one role, the
aggregate declares none of its own, and no role declares a member one of the
roles it composes already declares. The register is derived from the port
module's own text and from the aggregate's live member set, so a member moved
between roles moves the guard with it and a member declared twice is named.

The second half is the caller question. A member no production module calls
is a capability nothing uses, carried on every implementation for no
consumer. The list of such members is derived: every member of the whole
port, found as a member call on a role binding in the parsed modules of the
shipped tree outside the port module, the vendor adapters and the test tree,
so a member spelled only in a comment, a docstring or a string has no caller,
nor has a same-named method called on an object that is no tracker role, and
a call moved into one of those three places is no caller either. A caller in a
module the run does not reach still counts, as the criterion words it, and
the reachability of its role is pinned separately. It holds nothing but two
named exemptions — the four run-record members KOD-798 decides, and the
authorship read KOD-390 names as landed — and the three issue writes that
had no caller are gone from every tree, tests included, because the type
gate reads ``src/`` only and a deleted member surviving in test scaffolding
would otherwise pass it.

The third half is the dependency question. Every service and chain names
the roles it takes in its annotations and nothing wider: the whole port is
named only by the entry point and the composition root, which hold one
adapter and hand it to role-typed parameters; every role that declares a
member, among those a module takes, is one it calls a member of on the
binding it holds, or hands that binding to an in-tree callee whose own
parameter takes it; no role-typed parameter
has a default or a union beside it; no module outside the adapters and the
root imports a vendor adapter; and every role is taken, itself or composed
into another, by a module the entry point reaches, but for the authorship
read KOD-390 wires and the roles only an unwired consumer takes.

What it does not see: a member reached by reflection or by a name built at
runtime, which reads here as uncalled and is a finding in its own right; and
a call that type-checks against a role it does not carry, which the type
gate over ``src/`` refuses already.
"""

import importlib
import inspect
import pkgutil
import re
from collections.abc import Mapping
from types import ModuleType

import pytest
from typing_extensions import get_protocol_members

from kodezart.adapters.linear.tracker import LinearMcpTracker
from kodezart.core.protocols import ScopeWalkTracker, TrackerPort
from tests.chains.test_write_back_adoption import write_methods
from tests.domain.test_criterion_cross_off import source_tree
from tests.fakes import FakeTrackerPort
from tests.tracker.role_register import (
    ADAPTERS,
    AGGREGATE,
    EXEMPT_UNTIL_KOD_390,
    PORT_MODULE,
    RUN_RECORD_EXEMPTION,
    TESTS,
    UNWIRED_CONSUMER_ROLES,
    adapter_importers,
    aggregate_annotations,
    annotation_names,
    call_pattern,
    called_members,
    composed,
    declared_bases,
    declaring_roles,
    defaulted_role_parameters,
    first_party_closure,
    members_declared,
    monoliths,
    own_declarations,
    port_members,
    port_module_text,
    production_modules,
    redeclared_from_a_base,
    roles,
    roles_off_the_aggregate,
    runtime_checkable_classes,
    stray_classes,
    tree_under_tests,
    twice_declared,
    uncredited_roles,
    unreached_roles,
    zero_callers,
)

#: The issue writes nothing called, deleted from port, adapter and double.
#: Spelled here because they are gone: no object is left to read them off.
DELETED_ISSUE_WRITES = frozenset({"create_issue", "update_issue", "upsert_issue"})


def deleted_member_sites(tree: Mapping[str, str]) -> list[str]:
    """Every module in *tree* that defines, binds or calls a deleted issue write."""
    return sorted(
        path
        for path, text in tree.items()
        if any(
            call_pattern(name).search(text)
            or re.search(rf"\bdef\s+{re.escape(name)}\b", text)
            or re.search(rf"^\s+{re.escape(name)}\s*[:=]", text, re.MULTILINE)
            for name in DELETED_ISSUE_WRITES
        )
    )


def modules_holding_the_surface() -> tuple[ModuleType, ...]:
    """The port module, every module of the vendor adapter's package, the double.

    Read off the live objects: the port and the double by the module their
    aggregate lives in, the adapter package by walking the package the
    adapter's own module belongs to.
    """
    package = importlib.import_module(LinearMcpTracker.__module__.rsplit(".", 1)[0])
    return (
        importlib.import_module(TrackerPort.__module__),
        *(
            importlib.import_module(found.name)
            for found in pkgutil.walk_packages(
                package.__path__, prefix=f"{package.__name__}."
            )
        ),
        importlib.import_module(FakeTrackerPort.__module__),
    )


def classes_defined_in(modules: tuple[ModuleType, ...]) -> frozenset[type]:
    """Every class each of *modules* defines itself, not one it imports."""
    return frozenset(
        cls
        for module in modules
        for _, cls in inspect.getmembers(module, inspect.isclass)
        if cls.__module__ == module.__name__
    )


def classes_holding_a_deleted_write(
    classes: frozenset[type] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Every live class of the port, adapter or double that binds a deleted write.

    Read off the objects rather than the text, so a write bound by
    assignment, or under a name built at runtime, is seen as well as a
    ``def``; and every class those modules define is read, not only the ones
    one composed class reaches, so a double composed into nothing is seen.
    """
    found = (
        classes_defined_in(modules_holding_the_surface())
        if classes is None
        else classes
    )
    return {
        cls.__qualname__: held
        for cls in found
        if (held := tuple(sorted(DELETED_ISSUE_WRITES & set(vars(cls)))))
    }


def test_no_port_member_lacks_a_production_caller_beyond_the_exemptions():
    assert (
        zero_callers(source_tree(), port_members()) - RUN_RECORD_EXEMPTION
        == EXEMPT_UNTIL_KOD_390
    )


def test_every_exempted_member_is_a_member_of_the_port_today():
    """An exemption naming no member would hide nothing and say something false.

    The day KOD-798 deletes the run-record members this reddens, and the
    exemption goes with them. The run-record exemption is compared with the
    criterion's own list of four, written out here as KOD-836 words it: a
    name dropped from it, or one added to it, changes what the zero-caller
    guard lets through.
    """
    assert RUN_RECORD_EXEMPTION == frozenset(
        {"record_run_alarm", "read_run_alarm", "post_run_event", "lane_run_events"}
    )
    assert RUN_RECORD_EXEMPTION <= port_members()
    assert EXEMPT_UNTIL_KOD_390 <= port_members()


def single_caller(sources: Mapping[str, str]) -> tuple[str, str]:
    """A member one production module calls, and that module."""
    callers = {
        name: [
            path
            for path, text in production_modules(sources).items()
            if name in called_members(text)
        ]
        for name in sorted(port_members())
    }
    return next(
        (name, modules[0]) for name, modules in callers.items() if len(modules) == 1
    )


def test_a_member_whose_only_caller_goes_is_reported():
    sources = source_tree()
    members = port_members()
    name, module = single_caller(sources)
    assert name not in zero_callers(sources, members)

    del sources[module]

    assert name in zero_callers(sources, members)


#: The places a call does not count as a caller, as KOD-836 words it: the
#: vendor adapters, the port module and the test tree, each as the path a
#: module moved there would arrive under.
PLANTED_ELSEWHERE = {
    "the vendor adapters": f"{ADAPTERS}/planted.py",
    "the port module": PORT_MODULE,
    "the test tree": f"{TESTS}/planted.py",
}


@pytest.mark.parametrize("place", sorted(PLANTED_ELSEWHERE))
def test_a_caller_outside_the_production_modules_does_not_count(place):
    """A member whose one caller moves out of the counted modules has none."""
    sources = source_tree()
    name, module = single_caller(sources)
    text = sources.pop(module)
    sources[PLANTED_ELSEWHERE[place]] = text

    assert name in called_members(text)
    assert name in zero_callers(sources, port_members())


#: The ways a module can spell a member without calling it on a tracker role.
PLANTED_MENTIONS = {
    "a comment": "# was: await tracker.{name}(issue_key=key)\nVALUE = 1\n",
    "a docstring": '"""Calls ``tracker.{name}(issue_key=key)`` once."""\n',
    "a string": 'NOTE = "await tracker.{name}(issue_key=key)"\n',
    "a same-named method on another object": (
        "class Notes:\n    def {name}(self, *, issue_key):\n"
        "        return issue_key\n\n\n"
        "def note(tracker, key):\n    return Notes().{name}(issue_key=key)\n"
    ),
}


@pytest.mark.parametrize("form", sorted(PLANTED_MENTIONS))
def test_a_member_spelled_but_not_called_has_no_caller(form):
    sources = source_tree()
    name, module = single_caller(sources)
    sources[module] = PLANTED_MENTIONS[form].format(name=name)

    assert call_pattern(name).search(sources[module])
    assert name in zero_callers(sources, port_members())


def test_a_caller_of_the_exempted_read_empties_its_exemption():
    """Adding the caller KOD-390 owes takes the read off the list, reddening above."""
    sources = source_tree()
    (name,) = EXEMPT_UNTIL_KOD_390
    sources["services/authorship_reader.py"] = (
        f"async def authorship(*, tracker: {kod_390_role()}, surface):\n"
        f"    return await tracker.{name}(surface=surface)\n"
    )

    assert name not in zero_callers(sources, port_members())


def test_the_deleted_issue_writes_are_neither_defined_nor_called_anywhere():
    assert deleted_member_sites(source_tree()) == []
    assert deleted_member_sites(tree_under_tests()) == []


def test_no_class_of_the_port_adapter_or_double_holds_a_deleted_write():
    assert classes_holding_a_deleted_write() == {}
    for whole in (TrackerPort, LinearMcpTracker, FakeTrackerPort):
        assert not any(hasattr(whole, name) for name in DELETED_ISSUE_WRITES)


def test_the_classes_read_are_every_class_those_modules_define():
    """The double's consumer doubles sit outside every composed class's MRO."""
    classes = classes_defined_in(modules_holding_the_surface())
    composed_somewhere = {
        *TrackerPort.__mro__,
        *LinearMcpTracker.__mro__,
        *FakeTrackerPort.__mro__,
    }

    assert {TrackerPort, LinearMcpTracker, FakeTrackerPort} <= classes
    assert classes - composed_somewhere


@pytest.mark.parametrize("name", sorted(DELETED_ISSUE_WRITES))
def test_a_deleted_write_bound_on_a_class_is_named(name):
    """A write bound after the class body, where no text clause reads, is named."""
    revived = type("Revived", (FakeTrackerPort,), {})
    setattr(revived, name, FakeTrackerPort._create_issue)

    assert classes_holding_a_deleted_write(frozenset({revived})) == {
        revived.__qualname__: (name,)
    }


@pytest.mark.parametrize("name", sorted(DELETED_ISSUE_WRITES))
@pytest.mark.parametrize("form", ["call", "definition", "assignment", "annotation"])
def test_a_revived_issue_write_is_reported(name, form):
    text = {
        "call": f"async def seed(tracker):\n    await tracker.{name}(issue_key='k')\n",
        "definition": f"class Revived:\n    async def {name}(self):\n        ...\n",
        "assignment": f"class Revived(Base):\n    {name} = Base._write\n",
        "annotation": f"class Revived:\n    {name}: Callable[..., None]\n",
    }[form]
    tree = {"tracker/revived.py": text}

    assert deleted_member_sites(tree) == ["tracker/revived.py"]


def test_the_aggregate_declares_no_member_of_its_own():
    text = port_module_text()

    assert own_declarations(text)[AGGREGATE] == frozenset()


def test_every_member_of_the_surface_is_declared_on_exactly_one_role():
    text = port_module_text()
    own = own_declarations(text)

    assert twice_declared(text) == {}
    assert redeclared_from_a_base(text) == {}
    assert frozenset().union(*(own[name] for name in roles(text))) == port_members()


def test_every_declaring_role_is_composed_into_the_aggregate():
    """A role the aggregate does not name would answer for no adapter at all.

    The roles are found by what the adapter answers, not by what the
    aggregate composes, so a role dropped from it is still found and named.
    """
    text = port_module_text()

    assert roles_off_the_aggregate(text) == frozenset()
    assert declaring_roles(text) <= set(declared_bases(text)[AGGREGATE])


def test_no_role_but_the_aggregate_answers_the_whole_surface():
    assert monoliths(port_module_text()) == frozenset()


def test_a_second_whole_surface_composite_is_reported():
    text = port_module_text()
    grown = text + (
        f"\n\n@runtime_checkable\nclass TrackerSurface({AGGREGATE}, Protocol):\n"
        '    """Every member again, under another name."""\n'
    )

    assert monoliths(grown) == frozenset({"TrackerSurface"})


def test_a_composite_short_of_the_whole_is_reported_where_it_is_taken():
    """Short of two roles it is no monolith by the surface, so its taker answers."""
    sources = source_tree()
    text = port_module_text()
    bases = declared_bases(text)[AGGREGATE]
    uppermost = [
        base
        for base in bases
        if not any(base in composed(text, other) for other in bases)
    ]
    kept = [base for base in bases if base not in uppermost[:2]]
    grown = text + (
        "\n\n@runtime_checkable\nclass TrackerSurface(\n"
        + "".join(f"    {base},\n" for base in kept)
        + '    Protocol,\n):\n    """Most of the surface, under another name."""\n'
    )
    member = min(own_declarations(text)[kept[0]])
    planted_path = "chains/wide_holder.py"
    sources[planted_path] = (
        f"async def hold(*, tracker: TrackerSurface) -> None:\n"
        f"    await tracker.{member}()\n"
    )

    assert "TrackerSurface" in roles(grown)
    assert monoliths(grown) == frozenset()
    assert planted_path not in aggregate_annotations(sources)
    assert planted_path in uncredited_roles(sources, register=grown)


def test_a_role_dropped_from_the_aggregate_is_reported():
    text = port_module_text()
    role = min(declared_bases(text)[AGGREGATE])
    line = f"\n    {role},\n"
    start = text.index(f"class {AGGREGATE}(")
    assert line in text[start:]
    grown = text[:start] + text[start:].replace(line, "\n", 1)

    assert roles_off_the_aggregate(grown) == frozenset({role})


def test_every_class_that_touches_the_surface_is_a_role_declared_as_one():
    text = port_module_text()

    assert stray_classes(text) == {}
    assert roles(text) <= runtime_checkable_classes(text)


def test_no_role_answers_nothing_of_the_surface():
    text = port_module_text()
    disjoint = {
        name
        for name in own_declarations(text)
        if not members_declared(text, name) & port_members()
    }

    assert disjoint & roles(text) == set()


#: Each way a class can touch the tracker surface without being a role
#: declared as one: the text that arrives, and the class the report names.
PLANTED_STRAYS = {
    "a mixed protocol": (
        "\n\n@runtime_checkable\nclass MixedIssueNotes(Protocol):\n"
        "    async def {member}(self) -> None: ...\n"
        "    def scratch_note(self) -> str: ...\n",
        "MixedIssueNotes",
    ),
    "a role grown off the surface": (
        "\n\n@runtime_checkable\nclass OrphanReader({role}, Protocol):\n"
        "    async def orphan_read(self) -> None: ...\n",
        "OrphanReader",
    ),
    "a class that names no Protocol": (
        "\n\nclass OrganizeScratch({role}):\n    ...\n",
        "OrganizeScratch",
    ),
    "a role without the runtime check": (
        "\n\nclass Unchecked({role}, Protocol):\n    ...\n",
        "Unchecked",
    ),
}


@pytest.mark.parametrize("form", sorted(PLANTED_STRAYS))
def test_a_class_touching_the_surface_that_is_not_a_role_is_reported(form):
    text = port_module_text()
    member = sorted(port_members())[0]
    role = min(declaring_roles(text))
    planted, name = PLANTED_STRAYS[form]
    grown = text + planted.format(member=member, role=role)

    reports = (
        stray_classes(grown),
        twice_declared(grown),
        redeclared_from_a_base(grown),
        {AGGREGATE: ()} if own_declarations(grown)[AGGREGATE] else {},
    )

    assert name in stray_classes(grown)
    assert [bool(report) for report in reports].count(True) == 1


#: Each way a member can sit off its one role: the text that arrives, and
#: the one report that must name it.
PLANTED_PLACEMENTS = {
    "a member back on the aggregate": (
        "\n\n@runtime_checkable\nclass {aggregate}(Protocol):\n"
        "    async def {member}(self) -> None: ...\n",
        "aggregate",
    ),
    "a member on a sibling role": (
        "\n\n@runtime_checkable\nclass SecondPlace(Protocol):\n"
        "    async def {member}(self) -> None: ...\n",
        "twice",
    ),
    "a member shadowing its base": (
        "\n\n@runtime_checkable\nclass Shadowing({owner}, Protocol):\n"
        "    async def {member}(self) -> None: ...\n",
        "redeclared",
    ),
}


@pytest.mark.parametrize("form", sorted(PLANTED_PLACEMENTS))
def test_a_member_off_its_one_role_is_reported(form):
    text = port_module_text()
    member = sorted(port_members())[0]
    owner = next(
        name for name in sorted(roles(text)) if member in own_declarations(text)[name]
    )
    planted, expected = PLANTED_PLACEMENTS[form]
    grown = text + planted.format(aggregate=AGGREGATE, member=member, owner=owner)

    reports = {
        "aggregate": member in own_declarations(grown)[AGGREGATE],
        "twice": member in twice_declared(grown),
        "redeclared": any(
            member in shadowed for shadowed in redeclared_from_a_base(grown).values()
        ),
    }

    assert [name for name, reported in reports.items() if reported] == [expected]


def kod_390_role() -> str:
    """The role that declares the exempted authorship read."""
    text = port_module_text()
    (owner,) = (
        name
        for name in roles(text)
        if own_declarations(text)[name] & EXEMPT_UNTIL_KOD_390
    )
    return owner


def test_no_module_outside_the_allowlist_annotates_the_whole_port():
    assert aggregate_annotations(source_tree()) == ()


def test_every_role_a_module_takes_is_called_or_handed_on():
    assert uncredited_roles(source_tree()) == {}


def test_the_walks_role_carries_one_write_the_put_back():
    """The scope walk claims nothing and leases nothing (KOD-788).

    The one write its role carries is the put-back of a state it read, and
    the write surface is the one the write-back adoption guard derives.
    """
    carried = frozenset(get_protocol_members(ScopeWalkTracker))

    assert carried & write_methods() == {"restore_workflow_state"}


def test_no_role_dependency_outside_the_allowlist_is_defaulted():
    assert defaulted_role_parameters(source_tree()) == {}


def test_no_module_outside_the_adapters_and_the_root_imports_a_vendor_adapter():
    assert adapter_importers(source_tree()) == ()


def test_every_role_is_taken_by_a_module_the_run_reaches():
    sources = source_tree()

    assert unreached_roles(sources, port_module_text()) == (
        UNWIRED_CONSUMER_ROLES | {kod_390_role()}
    )


def test_every_unwired_role_is_a_role_a_module_outside_the_run_takes():
    """An entry naming no role, or one the run already reaches, hides nothing."""
    sources = source_tree()
    text = port_module_text()
    outside = set(sources) - first_party_closure(sources)
    taken = {
        name for path in outside for name in annotation_names(sources[path])
    } & roles(text)

    assert UNWIRED_CONSUMER_ROLES <= taken
    assert "services/scope_runtime.py" in first_party_closure(sources)


#: One planted consumer per clause, each naming a role the way a module
#: would and using it, and the one report that must name it.
PLANTED_CONSUMERS = {
    "the whole port": (
        "def hold(port: {aggregate}) -> None:\n    port.{member}()\n",
        "aggregate",
    ),
    "a role it never uses": (
        "def hold(reader: {role}) -> None:\n    return None\n",
        "credit",
    ),
    "a positional default": (
        "def hold(reader: {role} = cast({role}, None)) -> None:\n"
        "    reader.{member}()\n",
        "default",
    ),
    "a keyword-only default": (
        "def hold(*, reader: {role} = cast({role}, None)) -> None:\n"
        "    reader.{member}()\n",
        "default",
    ),
    "a union with no default": (
        "def hold(*, reader: {role} | None) -> None:\n    reader.{member}()\n",
        "default",
    ),
    "a defaulted field": (
        "@dataclass\nclass Holder:\n    reader: {role} = cast({role}, None)\n\n"
        "    def use(self) -> None:\n        self.reader.{member}()\n",
        "default",
    ),
    "a field that admits None": (
        "@dataclass\nclass Holder:\n    reader: {role} | None\n\n"
        "    def use(self) -> None:\n        self.reader.{member}()\n",
        "default",
    ),
    "a vendor adapter import": (
        "from {adapter} import {adapter_class}\n",
        "adapter",
    ),
}


@pytest.mark.parametrize("form", sorted(PLANTED_CONSUMERS))
def test_a_consumer_that_takes_more_than_it_calls_is_reported(form):
    sources = source_tree()
    text = port_module_text()
    role = min(declaring_roles(text))
    planted, expected = PLANTED_CONSUMERS[form]
    planted_path = "services/overreaching.py"
    sources[planted_path] = planted.format(
        aggregate=AGGREGATE,
        role=role,
        member=min(own_declarations(text)[role]),
        adapter=LinearMcpTracker.__module__,
        adapter_class=LinearMcpTracker.__name__,
    )

    reports = {
        "aggregate": aggregate_annotations(sources),
        "credit": tuple(uncredited_roles(sources)),
        "default": tuple(defaulted_role_parameters(sources)),
        "adapter": adapter_importers(sources),
    }

    assert [name for name, report in reports.items() if planted_path in report] == [
        expected
    ]


#: Each other way to spell what a clause reads, and the one report that must
#: name the module spelling it.
PLANTED_SPELLINGS = {
    "a quoted whole port": (
        'def hold(port: "{aggregate}") -> None:\n    port.{member}()\n',
        "aggregate",
    ),
    "a qualified whole port": (
        "from {port_package} import {port_module}\n\n\n"
        "def hold(port: {port_module}.{aggregate}) -> None:\n    port.{member}()\n",
        "aggregate",
    ),
    "an alias of the whole port": (
        "TrackerWhole = {aggregate}\n\n\n"
        "def hold(port: TrackerWhole) -> None:\n    port.{member}()\n",
        "aggregate",
    ),
    "a type alias of the whole port": (
        "type TrackerWhole = {aggregate}\n\n\n"
        "def hold(port: TrackerWhole) -> None:\n    port.{member}()\n",
        "aggregate",
    ),
    "a quoted role it never uses": (
        'def hold(reader: "{role}") -> None:\n    return None\n',
        "credit",
    ),
    "an optional role": (
        "def hold(*, reader: Optional[{role}]) -> None:\n    reader.{member}()\n",
        "default",
    ),
    "a union of a role and None": (
        "def hold(*, reader: Union[{role}, None]) -> None:\n    reader.{member}()\n",
        "default",
    ),
    "an annotated optional field": (
        "@dataclass\nclass Holder:\n"
        '    reader: Annotated[{role} | None, "held"]\n\n'
        "    def use(self) -> None:\n        self.reader.{member}()\n",
        "default",
    ),
    "a relative vendor adapter import": (
        "from ..{adapter_relative} import {adapter_class}\n",
        "adapter",
    ),
}


@pytest.mark.parametrize("form", sorted(PLANTED_SPELLINGS))
def test_every_spelling_names_what_it_spells(form):
    sources = source_tree()
    text = port_module_text()
    role = min(declaring_roles(text))
    port_package, port_module = TrackerPort.__module__.rsplit(".", 1)
    planted, expected = PLANTED_SPELLINGS[form]
    planted_path = "services/overreaching.py"
    sources[planted_path] = planted.format(
        aggregate=AGGREGATE,
        role=role,
        member=min(own_declarations(text)[role]),
        port_package=port_package,
        port_module=port_module,
        adapter_relative=LinearMcpTracker.__module__.split(".", 1)[1],
        adapter_class=LinearMcpTracker.__name__,
    )

    reports = {
        "aggregate": aggregate_annotations(sources),
        "credit": tuple(uncredited_roles(sources)),
        "default": tuple(defaulted_role_parameters(sources)),
        "adapter": adapter_importers(sources),
    }

    assert [name for name, report in reports.items() if planted_path in report] == [
        expected
    ]


def wider_role(text: str) -> tuple[str, str, str]:
    """A role, a member of one role it composes, and a declaring role left idle.

    Calling the member credits its declaring role and whatever that role
    composes; the idle one is composed by the wider role and by neither.
    """
    own = own_declarations(text)
    declaring = declaring_roles(text)
    for wider in sorted(roles(text)):
        parts = sorted(({wider} | composed(text, wider)) & declaring)
        for part in parts:
            carried = {part} | composed(text, part)
            idle = sorted(set(parts) - carried)
            if idle:
                return wider, min(own[part]), idle[0]
    raise AssertionError("no role composes two unrelated declaring roles")


#: Each way a module can hold a role it does not use, and still look busy.
PLANTED_CREDITS = {
    "a wider role with one member called": (
        "def hold(*, reader: {wider}) -> None:\n    reader.{member}()\n",
        "hold(reader): {idle}",
    ),
    "an idle role beside a call to another's member": (
        "def hold(*, reader: {idle}, other: {part_role}) -> None:\n"
        "    other.{member}()\n",
        "hold(reader): {idle}",
    ),
    "an idle role whose member is only spelled": (
        "def hold(*, reader: {idle}) -> None:\n"
        '    """Calls ``reader.{idle_member}()`` once."""\n'
        "    # reader.{idle_member}()\n"
        '    note = "reader.{idle_member}()"\n'
        "    del note\n",
        "hold(reader): {idle}",
    ),
    "an idle role under a name another function hands on": (
        "def helper(*, tracker: {part_role}) -> None:\n    tracker.{member}()\n\n\n"
        "def first(*, tracker: {part_role}) -> None:\n    helper(tracker=tracker)\n\n\n"
        "def held(*, tracker: {idle}) -> {idle}:\n    return tracker\n",
        "held(tracker): {idle}",
    ),
}


@pytest.mark.parametrize("form", sorted(PLANTED_CREDITS))
def test_a_role_the_module_does_not_use_is_reported_by_the_credit_clause(form):
    sources = source_tree()
    text = port_module_text()
    wider, member, idle = wider_role(text)
    (part_role,) = (
        role for role, members in own_declarations(text).items() if member in members
    )
    planted, expected = PLANTED_CREDITS[form]
    fields = {
        "wider": wider,
        "member": member,
        "idle": idle,
        "idle_member": min(own_declarations(text)[idle]),
        "part_role": part_role,
    }
    planted_path = "services/idle_holder.py"
    sources[planted_path] = planted.format(**fields)

    reports = (
        aggregate_annotations(sources),
        tuple(uncredited_roles(sources)),
        tuple(defaulted_role_parameters(sources)),
        adapter_importers(sources),
    )

    assert [planted_path in report for report in reports].count(True) == 1
    assert expected.format(**fields) in uncredited_roles(sources)[planted_path]
    assert not any(
        entry.startswith(("helper(", "first(", "hold(other)"))
        for entry in uncredited_roles(sources)[planted_path]
    )


def test_a_role_nothing_in_the_run_takes_is_reported():
    sources = source_tree()
    text = port_module_text()
    base = min(declaring_roles(text))
    grown = text + (
        f"\n\n@runtime_checkable\nclass Untaken({base}, Protocol):\n    ...\n"
    )

    assert "Untaken" in roles(grown)
    assert "Untaken" in unreached_roles(sources, grown)


def test_wiring_an_unwired_consumer_takes_its_role_off_the_list():
    """The exemption reddens the day one of those consumers is wired."""
    sources = source_tree()
    text = port_module_text()
    role = min(UNWIRED_CONSUMER_ROLES)
    (module,) = (
        path
        for path, source in sorted(sources.items())
        if role in annotation_names(source)
        and path.startswith("services/")
        and not any(
            other in annotation_names(source)
            for other in UNWIRED_CONSUMER_ROLES - {role}
        )
    )
    dotted = module.removesuffix(".py").replace("/", ".")
    sources["main.py"] += (
        f"\nimport {LinearMcpTracker.__module__.split('.')[0]}.{dotted}\n"
    )

    assert role not in unreached_roles(sources, text)
