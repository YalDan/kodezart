"""The tracker port's member register, read off the port and the tree.

A non-test module the role guards share, so the derivations they assert
over are stated once. Every set a guard compares is computed here from a
live object or from the source text; the only literal sets are the two
exemptions, which are named exemptions rather than scanned surfaces.
"""

import re
from collections.abc import Mapping
from pathlib import Path

from typing_extensions import get_protocol_members

from kodezart.adapters.linear.tracker import LinearMcpTracker
from kodezart.core.protocols import TrackerPort
from tests.tracker.test_criterion_port_sites import module_of

TESTS_ROOT = Path(__file__).parents[1]

#: The port module and the vendor adapter package, by tree-relative path,
#: read off the classes that live in them.
PORT_MODULE = module_of(TrackerPort)
ADAPTERS = module_of(LinearMcpTracker).split("/", 1)[0]

#: The four run-record members KOD-836 holds until KOD-798 lands. Named, not
#: scanned: the exemption is the thing that criterion states, and it is
#: deleted with the members when KOD-798 deletes them.
RUN_RECORD_EXEMPTION = frozenset(
    {"record_run_alarm", "read_run_alarms", "post_run_event", "lane_run_events"}
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
