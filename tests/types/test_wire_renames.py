"""KOD-91/AC-11 — the retired names are gone from the source, not just the wire.

Four renames landed with no compatibility shims, and two serializer hacks
were deleted with the tri-state bool that made them necessary.  A rename
that leaves the old name alive somewhere in ``src/`` is a rename that will
grow a shim, so the absence is asserted mechanically over the whole tree.

One exclusion, named rather than pattern-matched: ``WorkflowOutcome`` has
a member spelled ``ci_passed``.  It is a terminal-disposition VALUE — "the
run ended after CI passed" — owned by the outcome discriminator's issue,
whose module states that members are appended and never re-pointed.  It is
not the CI-status field this issue typed, and repointing another issue's
landed wire contract to satisfy a grep would be the tail wagging the dog.
The exclusion is pinned to those exact two lines so it cannot quietly
widen into "anything that mentions ci_passed".
"""

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "kodezart"

#: Every retired spelling, as the pattern that finds it.  ``fix_round`` is
#: matched on a word boundary: ``fix_rounds_used`` is the NEW name and a
#: substring search would report the fix as the defect.
RETIRED: dict[str, str] = {
    "_force_ci_field": r"_force_ci_field",
    "ci_passed": r"\bci_passed\b",
    "fix_round": r"\bfix_round\b",
    "WorkflowCompleteEvent.error": r"WorkflowCompleteEvent[^\n]*\.error\b",
}

#: The terminal-outcome member and its single read — the one exclusion,
#: quoted so a third occurrence has to be argued for rather than absorbed.
_OUTCOME_MEMBER: frozenset[str] = frozenset(
    {
        'ci_passed = "ci_passed"',
        "return WorkflowOutcome.ci_passed",
    },
)


def _source_lines() -> list[tuple[Path, str]]:
    return [
        (path, line)
        for path in sorted(SRC.rglob("*.py"))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


@pytest.mark.parametrize("name", sorted(RETIRED))
def test_no_retired_wire_name_survives_in_src(name: str) -> None:
    """A grep over the shipped source, one retired spelling at a time."""
    pattern = re.compile(RETIRED[name])
    hits = [
        f"{path.relative_to(SRC)}: {line.strip()}"
        for path, line in _source_lines()
        if pattern.search(line) and line.strip() not in _OUTCOME_MEMBER
    ]
    assert hits == []


def test_the_only_excluded_occurrences_are_the_outcome_member() -> None:
    """Non-vacuity: the exclusion covers exactly two lines, and both exist.

    Without this the exclusion set could silently stop matching anything —
    or grow — and the test above would keep passing either way.
    """
    excluded = [
        line.strip() for _, line in _source_lines() if line.strip() in _OUTCOME_MEMBER
    ]
    assert sorted(excluded) == sorted(_OUTCOME_MEMBER)


def test_the_sweep_would_catch_a_reintroduced_name() -> None:
    """The patterns are checked against the shapes they exist to reject."""
    reintroduced = {
        "_force_ci_field": "    def _force_ci_field(",
        "ci_passed": '        return {"ci_passed": passed}',
        "fix_round": "    fix_round: int",
        "WorkflowCompleteEvent.error": "    event: WorkflowCompleteEvent = x.error",
    }
    for name, line in reintroduced.items():
        assert re.compile(RETIRED[name]).search(line) is not None, name
    assert re.compile(RETIRED["fix_round"]).search("    fix_rounds_used: int") is None
