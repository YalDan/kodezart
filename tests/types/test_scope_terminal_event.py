"""The terminal event's wire shape and the two things it refuses (KOD-471).

The scope outcome is asserted on the aliased wire snapshot and never on a
rendered string: what a consumer reads is the dump, and a body that happened
to agree with a wrong ``outcome`` would still be a wrong claim.
"""

import pytest
from pydantic import ValidationError

from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_state import LanePR
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_terminal import (
    STATUS_UPDATE_SCOPE_KINDS,
    ScopeLaneEntry,
    ScopeTerminalEvent,
    derive_scope_outcome,
)

SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="scoped-project")
PR = LanePR(url="https://forge.invalid/fixture/repo/pull/12", number=12, state="open")


def entry(issue: str, *, done: bool, branch: str | None = None, pr=None):
    return ScopeLaneEntry(issue=issue, done=done, branch=branch, pr=pr)


def event(*lanes: ScopeLaneEntry) -> ScopeTerminalEvent:
    return ScopeTerminalEvent(
        scope=SCOPE, lanes=lanes, outcome=derive_scope_outcome(lanes)
    )


def test_every_entry_carries_its_issue_reading_branch_and_pull_request() -> None:
    """The four columns KOD-471 names, on the snapshot a consumer reads."""
    payload = event(
        entry("A", done=True, branch="fixture/A-1a2b3c4d", pr=PR),
        entry("B", done=False),
    ).model_dump(mode="json", by_alias=True)

    assert payload["type"] == "scope_terminal"
    assert payload["scope"] == {"kind": "project", "key": "scoped-project"}
    assert payload["lanes"] == [
        {
            "issue": "A",
            "done": True,
            "branch": "fixture/A-1a2b3c4d",
            "pr": {"url": PR.url, "number": 12, "state": "open"},
        },
        {"issue": "B", "done": False, "branch": None, "pr": None},
    ]


def test_the_wire_outcome_equals_the_derivation_of_its_own_entries() -> None:
    """Asserted on the snapshot against the pure function, not on prose."""
    for lanes in (
        (entry("A", done=True),),
        (entry("A", done=True), entry("B", done=False)),
        (),
    ):
        payload = event(*lanes).model_dump(mode="json", by_alias=True)
        assert payload["outcome"] == derive_scope_outcome(lanes).value


def test_the_event_round_trips_through_its_own_wire_form() -> None:
    original = event(entry("A", done=True, branch="fixture/A", pr=PR))
    parsed = ScopeTerminalEvent.model_validate_json(original.model_dump_json())
    assert parsed == original


#: One vector per reading the derivation produces, so both directions of the
#: refusal are stated: a finished claim an open lane does not support, and an
#: unfinished claim a closed one does not.
FIXED_VECTORS = (
    (entry("A", done=False),),
    (entry("A", done=True),),
)

#: Every member the vector does not derive, over the whole enum rather than a
#: few picked ones: an outcome is arithmetic over the vector, so any other
#: value is authored and every one of them is refused.
UNDERIVED = [
    (lanes, member)
    for lanes in FIXED_VECTORS
    for member in WorkflowOutcome
    if member is not derive_scope_outcome(lanes)
]


@pytest.mark.parametrize(
    ("lanes", "outcome"),
    UNDERIVED,
    ids=[f"{lanes[0].done}-{member.name}" for lanes, member in UNDERIVED],
)
def test_an_outcome_the_vector_does_not_derive_refuses(lanes, outcome) -> None:
    """A claim its own entries do not support never reaches the wire."""
    with pytest.raises(ValidationError, match="derivation of its own lane vector"):
        ScopeTerminalEvent(scope=SCOPE, lanes=lanes, outcome=outcome)


def test_every_member_but_the_derived_one_is_covered() -> None:
    """Non-vacuity: the table is the whole enum bar one value per vector."""
    assert len(UNDERIVED) == len(FIXED_VECTORS) * (len(WorkflowOutcome) - 1)


#: Every shape a lane's recorded delivery can take, including two that differ
#: only in the value a merge would be read off.
RECORDED_DELIVERIES = (
    None,
    LanePR(url="https://forge.invalid/fixture/repo/pull/12", number=12, state="open"),
    LanePR(url="https://forge.invalid/fixture/repo/pull/12", number=12, state="closed"),
    LanePR(url="https://forge.invalid/fixture/repo/pull/12", number=12, state="merged"),
    LanePR(url="https://forge.invalid/fixture/repo/pull/13", number=13, state="open"),
)


@pytest.mark.parametrize(
    "pr", RECORDED_DELIVERIES, ids=["none", "open", "closed", "merged", "another open"]
)
@pytest.mark.parametrize("branch", [None, "fixture/A"], ids=["no branch", "branch"])
def test_the_outcome_is_independent_of_every_pull_request_and_branch_value(
    pr, branch
) -> None:
    """The derivation reads the done column and nothing else on the row.

    Both recorded columns are held at every value they can take while the done
    column is held fixed, in the pure function and on the validated event, so
    neither can move the outcome in either direction.
    """
    open_scope = (
        entry("A", done=True, branch=branch, pr=pr),
        entry("B", done=False, branch=branch, pr=pr),
    )
    assert derive_scope_outcome(open_scope) is WorkflowOutcome.scope_stopped_short
    assert event(*open_scope).outcome is WorkflowOutcome.scope_stopped_short

    finished = (
        entry("A", done=True, branch=branch, pr=pr),
        entry("B", done=True, branch=branch, pr=pr),
    )
    assert derive_scope_outcome(finished) is WorkflowOutcome.scope_converged
    assert event(*finished).outcome is WorkflowOutcome.scope_converged


def test_the_residual_member_is_parseable_and_unproducible_here() -> None:
    """KOD-767: the member stays a wire value and this event never carries it."""
    assert WorkflowOutcome.scope_converged_with_residual not in {
        derive_scope_outcome(lanes)
        for lanes in ((), (entry("A", done=True),), (entry("A", done=False),))
    }
    with pytest.raises(ValidationError, match="derivation of its own lane vector"):
        ScopeTerminalEvent(
            scope=SCOPE,
            lanes=(entry("A", done=True),),
            outcome=WorkflowOutcome.scope_converged_with_residual,
        )


def test_one_entry_per_lane_and_a_repeated_issue_refuses() -> None:
    with pytest.raises(ValidationError, match="cannot appear twice"):
        ScopeTerminalEvent(
            scope=SCOPE,
            lanes=(entry("A", done=True), entry("A", done=True)),
            outcome=WorkflowOutcome.scope_converged,
        )


def test_a_blank_issue_or_branch_refuses() -> None:
    with pytest.raises(ValidationError):
        ScopeLaneEntry(issue=" ", done=True, branch=None, pr=None)
    with pytest.raises(ValidationError):
        ScopeLaneEntry(issue="A", done=True, branch=" ", pr=None)


def test_the_status_update_kinds_are_the_container_kinds() -> None:
    """A milestone and an issue carry no status surface at the backend."""
    assert STATUS_UPDATE_SCOPE_KINDS == {ScopeKind.PROJECT, ScopeKind.INITIATIVE}
    assert ScopeKind.MILESTONE not in STATUS_UPDATE_SCOPE_KINDS
    assert ScopeKind.ISSUE not in STATUS_UPDATE_SCOPE_KINDS
