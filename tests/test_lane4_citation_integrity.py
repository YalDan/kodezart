"""Every criterion of fire KOD-53 is cited on a demonstration (KOD-53 R6).

R6 requires each of this lane's criteria to carry a ``KOD-53/AC-n``
citation on the test that demonstrates it, in the lane-wide numbering, and
forbids a marker that resolves to a live lane id it does not name.  The
map is held here as a query over the tree, because the alternative —
re-deriving it by mutation once per grading — is what two consecutive
gradings had to do and what R6 exists to stop.

This grades no criterion.  KOD-53 R1 and R2 forbid authoring a lane-level
acceptance criterion, and a pointer to a demonstration is not one: it
asserts nothing about behaviour and cannot substitute for a demonstration.
"""

import re
from pathlib import Path

#: KOD-66 contributes 12 Verification bullets, KOD-69 2, KOD-71 3, KOD-11 3
#: and KOD-36 7 — the lane-wide numbering runs AC-1..AC-27 in that order.
LANE_CRITERION_COUNT = 27

TESTS_ROOT = Path(__file__).resolve().parent
CITATION = re.compile(r"KOD-53/AC-(\d+)")


def cited_criteria() -> set[int]:
    """Every lane criterion number cited anywhere under ``tests/``."""
    return {
        int(match.group(1))
        for path in TESTS_ROOT.rglob("*.py")
        for match in CITATION.finditer(path.read_text(encoding="utf-8"))
    }


def test_every_lane_criterion_carries_a_citation() -> None:
    """A criterion with no cited demonstration is unfindable, not satisfied."""
    expected = set(range(1, LANE_CRITERION_COUNT + 1))
    assert expected - cited_criteria() == set()


def test_no_citation_names_a_criterion_the_lane_does_not_have() -> None:
    """The failure mode R6 calls worse than silence: a marker pointing nowhere."""
    assert {n for n in cited_criteria() if n > LANE_CRITERION_COUNT or n < 1} == set()
