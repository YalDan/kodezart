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


def test_an_outcome_the_vector_does_not_derive_refuses() -> None:
    """A finished claim its own entries do not support never reaches the wire."""
    with pytest.raises(ValidationError, match="derivation of its own lane vector"):
        ScopeTerminalEvent(
            scope=SCOPE,
            lanes=(entry("A", done=False),),
            outcome=WorkflowOutcome.scope_converged,
        )
    with pytest.raises(ValidationError, match="derivation of its own lane vector"):
        ScopeTerminalEvent(
            scope=SCOPE,
            lanes=(entry("A", done=True),),
            outcome=WorkflowOutcome.scope_stopped_short,
        )


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
