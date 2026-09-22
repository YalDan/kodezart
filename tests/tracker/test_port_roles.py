"""The tracker port's members, each called by production code (KOD-836).

A member no production module calls is a capability nothing uses, carried
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
    EXEMPT_UNTIL_KOD_390,
    PORT_MODULE,
    RUN_RECORD_EXEMPTION,
    call_pattern,
    port_members,
    tree_under_tests,
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
