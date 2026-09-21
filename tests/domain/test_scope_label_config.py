"""Scope-label configuration stays open and validates every required member."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.errors import OperationConfigError
from kodezart.types.domain.operation import OperationConfig

SCOPE_LABELS = {
    "triage": "needs a scope assessment",
    "proposed": "scope ready for a decision",
    "approved": "authorized implementation",
}


#: A whole issue-queue vocabulary, so a case can move one member onto the
#: admission label and leave the rest a legal mapping.
QUEUE_STATES = {
    "triage": "needs issue assessment",
    "proposed": "issue ready for a decision",
    "approved": "implementation authorized",
    "done": "issue finished",
    "decision": "issue awaiting a decision",
}


def _write_config(
    tmp_path: Path,
    labels: dict[str, str] | None,
    queue_states: dict[str, str] | None = None,
) -> Path:
    lines = [
        'operation_name = "fixture"',
        'workspace = "fixture-workspace"',
    ]
    for block, mapping in (("scope_labels", labels), ("queue_states", queue_states)):
        if mapping is None:
            continue
        lines.extend(("", f"[{block}]"))
        lines.extend(
            f"{json.dumps(key)} = {json.dumps(value)}"
            for key, value in sorted(mapping.items())
        )
    path = tmp_path / "operation.toml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@pytest.mark.parametrize("labels", [None, {}], ids=["absent", "empty"])
def test_absent_or_empty_scope_labels_load(
    tmp_path: Path,
    labels: dict[str, str] | None,
) -> None:
    config = load_operation_config(_write_config(tmp_path, labels))

    assert config.scope_labels == {}
    assert config.queue_states == {}


def test_configured_scope_label_names_are_preserved_on_load(tmp_path: Path) -> None:
    config = load_operation_config(_write_config(tmp_path, SCOPE_LABELS))

    assert config.scope_labels == SCOPE_LABELS
    assert config.queue_states == {}
    assert config.model_dump()["scope_labels"] == SCOPE_LABELS


def test_scope_labels_accept_an_additional_configuration_member(tmp_path: Path) -> None:
    labels = {**SCOPE_LABELS, "paused": "awaiting scope maintenance"}
    config = load_operation_config(_write_config(tmp_path, labels))

    assert config.scope_labels == labels
    assert config.model_dump()["scope_labels"] == labels


@pytest.mark.parametrize("missing", ["triage", "proposed", "approved"])
def test_each_missing_scope_label_is_a_typed_load_error(
    tmp_path: Path,
    missing: str,
) -> None:
    labels = {key: value for key, value in SCOPE_LABELS.items() if key != missing}

    with pytest.raises(OperationConfigError) as caught:
        load_operation_config(_write_config(tmp_path, labels))

    assert len(caught.value.failures) == 1
    assert (
        f"scope_labels is missing required key {missing!r}" in caught.value.failures[0]
    )


def test_populated_mapping_reports_every_missing_scope_label(tmp_path: Path) -> None:
    with pytest.raises(OperationConfigError) as caught:
        load_operation_config(
            _write_config(tmp_path, {"paused": "awaiting scope maintenance"}),
        )

    assert len(caught.value.failures) == len(SCOPE_LABELS)
    for member in SCOPE_LABELS:
        assert any(
            f"scope_labels is missing required key {member!r}" in failure
            for failure in caught.value.failures
        )


def test_scope_and_queue_structure_failures_are_reported_together() -> None:
    with pytest.raises(ValidationError) as caught:
        OperationConfig(
            operation_name="fixture",
            workspace="fixture-workspace",
            scope_labels={"triage": SCOPE_LABELS["triage"]},
            queue_states={"triage": "needs issue assessment"},
        )

    message = str(caught.value)
    assert "scope_labels is missing required key 'proposed'" in message
    assert "scope_labels is missing required key 'approved'" in message
    assert "queue_states is missing required key 'approved'" in message


def test_scope_label_values_remain_strings() -> None:
    with pytest.raises(ValidationError) as caught:
        OperationConfig.model_validate(
            {
                "operation_name": "fixture",
                "workspace": "fixture-workspace",
                "scope_labels": {**SCOPE_LABELS, "approved": 1},
            },
        )

    (failure,) = caught.value.errors()
    assert failure["loc"] == ("scope_labels", "approved")
    assert failure["type"] == "string_type"


@pytest.mark.parametrize("member", ["approved", "done"])
def test_a_queue_state_spelling_the_approval_label_is_refused_at_load_naming_both_keys(
    member: str,
) -> None:
    """Any queue member spelled as the approval label is refused, not just one.

    The queue member's own name carries no weight: what the rule is about is
    the label, so a "done" write spelled as the approval label is the same
    write as an "approved" one. Both keys are named, because a reader has two
    mappings to reconcile.
    """
    with pytest.raises(ValidationError) as caught:
        OperationConfig(
            operation_name="fixture",
            workspace="fixture-workspace",
            scope_labels=SCOPE_LABELS,
            queue_states={**QUEUE_STATES, member: SCOPE_LABELS["approved"]},
        )

    message = str(caught.value)
    assert f"queue_states[{member!r}]" in message
    assert "scope_labels['approved']" in message
    assert repr(SCOPE_LABELS["approved"]) in message


def test_the_collision_is_a_typed_load_error_beside_the_structural_ones(
    tmp_path: Path,
) -> None:
    """The collision is collected with the other structural failures, not first."""
    path = _write_config(
        tmp_path,
        {key: value for key, value in SCOPE_LABELS.items() if key != "triage"},
        {**QUEUE_STATES, "approved": SCOPE_LABELS["approved"]},
    )

    with pytest.raises(OperationConfigError) as caught:
        load_operation_config(path)

    assert any(
        "scope_labels is missing required key 'triage'" in failure
        for failure in caught.value.failures
    )
    assert any(
        "queue_states['approved']" in failure for failure in caught.value.failures
    )


def test_matching_non_approval_spellings_still_load(tmp_path: Path) -> None:
    """One label under both namespaces is legal unless it is the approval one."""
    config = load_operation_config(
        _write_config(
            tmp_path,
            SCOPE_LABELS,
            {**QUEUE_STATES, "triage": SCOPE_LABELS["triage"]},
        )
    )

    assert config.queue_states["triage"] == SCOPE_LABELS["triage"]
    assert config.scope_labels == SCOPE_LABELS
