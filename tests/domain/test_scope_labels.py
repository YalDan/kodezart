"""Scope admission and issue queue state have independent vocabularies."""

import pytest

from kodezart.types.domain.operation import LifecycleStage, QueueState, ScopeLabel


@pytest.mark.parametrize("member", ["triage", "proposed", "approved"])
def test_scope_admission_has_its_own_type(member: str) -> None:
    scope_member = ScopeLabel(member)
    queue_member = QueueState(member)
    assert type(scope_member) is ScopeLabel
    assert type(queue_member) is QueueState
    assert not isinstance(scope_member, QueueState)
    assert not isinstance(queue_member, ScopeLabel)


@pytest.mark.parametrize("member", ["done", "decision", "scope:approved"])
def test_scope_labels_refuse_queue_only_and_vendor_members(member: str) -> None:
    with pytest.raises(ValueError):
        ScopeLabel(member)


def test_existing_issue_queue_vocabulary_is_preserved() -> None:
    assert {member.value for member in QueueState} == {
        "triage",
        "proposed",
        "approved",
        "done",
        "decision",
    }
    assert {member.value for member in ScopeLabel} == {
        "triage",
        "proposed",
        "approved",
    }


def test_the_lifecycle_stage_vocabulary_is_closed() -> None:
    """No size-, estimate- or forecast-based member joins unnoticed (KOD-726).

    The set is the literal one, not a count (KOD-781).
    """
    assert {member.value for member in LifecycleStage} == {
        "in_progress",
        "in_review",
        "done",
    }
