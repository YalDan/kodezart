"""Issue label vocabulary is configured, optional, owned and unambiguous."""

import pytest

from kodezart.services.tracker_boot import configured_mappings, owned_mappings
from kodezart.types.domain.operation import (
    FIELD_OWNERSHIP,
    ConfigOwnership,
    OperationConfig,
    TeamEntry,
)
from kodezart.types.domain.tracker import MappingKind


def operation(**changes):
    return OperationConfig(operation_name="fixture", workspace="fixture", **changes)


def test_empty_issue_label_vocabulary_loads_and_adds_no_refs():
    config = operation()
    assert config.issue_labels == {}
    assert FIELD_OWNERSHIP["issue_labels"] is ConfigOwnership.OWNED
    assert owned_mappings(config) == ()


def test_issue_label_keys_are_open_and_names_are_preserved():
    labels = {"criterion": "Acceptance condition", "phase_ticket": "Ticket checked"}
    config = operation(issue_labels=labels)
    assert config.issue_labels == labels
    assert {
        (ref.kind, ref.name, ref.identifier) for ref in configured_mappings(config)
    } == {(MappingKind.ISSUE_LABEL, key, name) for key, name in labels.items()}


@pytest.mark.parametrize(
    "labels",
    [
        {"": "criterion"},
        {" ": "criterion"},
        {"criterion": ""},
        {"criterion": " \n"},
        {"criterion": "same", "phase_ticket": "same"},
    ],
)
def test_ambiguous_or_empty_issue_label_entries_refuse_at_construction(labels):
    with pytest.raises(ValueError, match="issue_labels"):
        operation(issue_labels=labels)


def test_issue_label_ownership_covers_every_declared_team():
    config = operation(
        issue_labels={"criterion": "Acceptance condition"},
        teams={
            "one": TeamEntry(name="Team one", key="ONE"),
            "two": TeamEntry(name="Team two", key="TWO"),
        },
    )
    assert {
        (ref.kind, ref.name, ref.identifier, ref.scope)
        for ref in owned_mappings(config)
    } == {
        (MappingKind.ISSUE_LABEL, "criterion", "Acceptance condition", "Team one"),
        (MappingKind.ISSUE_LABEL, "criterion", "Acceptance condition", "Team two"),
    }
