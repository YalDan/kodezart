"""The tracker port as role interfaces named by consumer (KOD-833, KOD-836).

Every member of the tracker surface is declared on exactly one role, the
aggregate declares none of its own, and no role declares a member one of the
roles it composes already declares. The register is derived from the port
module's own text and from the aggregate's live member set, so a member moved
between roles moves the guard with it and a member declared twice is named.

The second half is the caller question. A member no production module calls
is a capability nothing uses, carried
on every implementation for no consumer. The list of such members is
derived: every member of the whole port, matched as a member call across
the shipped tree outside the port module and the vendor adapters. It holds
nothing but two named exemptions — the four run-record members KOD-798
decides, and the authorship read KOD-390 names as landed — and the three
issue writes that had no caller are gone from every tree, tests included,
because the type gate reads ``src/`` only and a deleted member surviving in
test scaffolding would otherwise pass it.

What it does not see: a member reached by reflection or by a name built at
runtime, which reads here as uncalled and is a finding in its own right.
"""

import re
from collections.abc import Mapping

import pytest

from tests.domain.test_criterion_cross_off import source_tree
from tests.tracker.role_register import (
    ADAPTERS,
    AGGREGATE,
    EXEMPT_UNTIL_KOD_390,
    PORT_MODULE,
    RUN_RECORD_EXEMPTION,
    call_pattern,
    composed,
    declaring_roles,
    own_declarations,
    port_members,
    port_module_text,
    redeclared_from_a_base,
    roles,
    tree_under_tests,
    twice_declared,
    zero_callers,
)

#: The issue writes nothing called, deleted from port, adapter and double.
#: Spelled here because they are gone: no object is left to read them off.
DELETED_ISSUE_WRITES = frozenset({"create_issue", "update_issue", "upsert_issue"})


def deleted_member_sites(tree: Mapping[str, str]) -> list[str]:
    """Every module in *tree* that defines or calls a deleted issue write."""
    return sorted(
        path
        for path, text in tree.items()
        if any(
            call_pattern(name).search(text)
            or re.search(rf"\bdef\s+{re.escape(name)}\b", text)
            for name in DELETED_ISSUE_WRITES
        )
    )


def test_no_port_member_lacks_a_production_caller_beyond_the_exemptions():
    assert (
        zero_callers(source_tree(), port_members()) - RUN_RECORD_EXEMPTION
        == EXEMPT_UNTIL_KOD_390
    )


def test_every_exempted_member_is_a_member_of_the_port_today():
    """An exemption naming no member would hide nothing and say something false.

    The day KOD-798 deletes the run-record members this reddens, and the
    exemption goes with them.
    """
    assert RUN_RECORD_EXEMPTION <= port_members()
    assert EXEMPT_UNTIL_KOD_390 <= port_members()


def test_a_member_whose_only_caller_goes_is_reported():
    sources = source_tree()
    members = port_members()
    callers = {
        name: [
            path
            for path, text in sources.items()
            if path != PORT_MODULE
            and not path.startswith(f"{ADAPTERS}/")
            and call_pattern(name).search(text)
        ]
        for name in sorted(members)
    }
    name, modules = next(
        (name, modules) for name, modules in callers.items() if len(modules) == 1
    )
    assert name not in zero_callers(sources, members)

    del sources[modules[0]]

    assert name in zero_callers(sources, members)


def test_a_caller_of_the_exempted_read_empties_its_exemption():
    """Adding the caller KOD-390 owes takes the read off the list, reddening above."""
    sources = source_tree()
    (name,) = EXEMPT_UNTIL_KOD_390
    sources["services/authorship_reader.py"] = (
        f"async def authorship(tracker, surface):\n"
        f"    return await tracker.{name}(surface=surface)\n"
    )

    assert name not in zero_callers(sources, port_members())


def test_the_deleted_issue_writes_are_neither_defined_nor_called_anywhere():
    assert deleted_member_sites(source_tree()) == []
    assert deleted_member_sites(tree_under_tests()) == []


@pytest.mark.parametrize("name", sorted(DELETED_ISSUE_WRITES))
@pytest.mark.parametrize("form", ["call", "definition"])
def test_a_revived_issue_write_is_reported(name, form):
    text = {
        "call": f"async def seed(tracker):\n    await tracker.{name}(issue_key='k')\n",
        "definition": f"class Revived:\n    async def {name}(self):\n        ...\n",
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
    """A role the aggregate does not reach would answer for no adapter at all."""
    text = port_module_text()

    assert declaring_roles(text) <= composed(text, AGGREGATE)


@pytest.mark.parametrize(
    "form",
    ["a member back on the aggregate", "a member on two roles", "a base redeclared"],
)
def test_a_member_off_its_one_role_is_reported(form):
    text = port_module_text()
    member = sorted(port_members())[0]
    owner = next(
        name for name in sorted(roles(text)) if member in own_declarations(text)[name]
    )
    planted = {
        "a member back on the aggregate": (
            f"\n\n@runtime_checkable\nclass {AGGREGATE}(Protocol):\n"
            f"    async def {member}(self) -> None: ...\n"
        ),
        "a member on two roles": (
            f"\n\n@runtime_checkable\nclass SecondPlace({owner}, Protocol):\n"
            f"    async def {member}(self) -> None: ...\n"
        ),
        "a base redeclared": (
            f"\n\n@runtime_checkable\nclass Shadowing({owner}, Protocol):\n"
            f"    async def {member}(self) -> None: ...\n"
        ),
    }[form]
    grown = text + planted

    if form == "a member back on the aggregate":
        assert own_declarations(grown)[AGGREGATE] == frozenset({member})
    else:
        assert "SecondPlace" in str(twice_declared(grown)) or "Shadowing" in str(
            redeclared_from_a_base(grown)
        )
