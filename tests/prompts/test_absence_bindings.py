"""Three-state renderer bindings: absence is named, never blank (KOD-112 R5).

Every binding that can be absent carries the mutually exclusive
``x`` / ``x_absent`` pair.  A guarded template names the absence; an
unguarded reference over the absent state fails loudly as an unbound
placeholder.  The one outcome no state produces is a blank render.
"""

import pytest

from kodezart.core.errors import PromptRenderError
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.core.prompt_rendering import render_template
from kodezart.types.domain.operation import (
    CHECKPOINT_DOCUMENT_KEY,
    DocumentEntry,
    DocumentSystem,
    OperationConfig,
)
from tests.prompts.test_operation_config import example_config

COLLECTION_FIELDS = (
    "principals",
    "agent_identities",
    "teams",
    "queue_states",
    "workflow_states",
    "repos",
    "documents",
    "records",
    "knowledge",
    "endpoints",
    "initiatives",
)


def minimal_config() -> OperationConfig:
    return OperationConfig(
        operation_name="fixture",
        workspace="fixture-workspace",
    )


@pytest.mark.parametrize("field", [*COLLECTION_FIELDS, "private_surface"])
def test_an_absent_field_binds_the_marker_and_no_value(field: str) -> None:
    bindings = operation_bindings(minimal_config())
    assert bindings[field] is None
    assert bindings[f"{field}_absent"] is True


@pytest.mark.parametrize("field", [*COLLECTION_FIELDS, "private_surface"])
def test_a_present_field_binds_the_value_and_no_marker(field: str) -> None:
    bindings = operation_bindings(example_config())
    assert bindings[field] is not None
    assert bindings[f"{field}_absent"] is None


def test_every_pair_is_mutually_exclusive_in_both_states() -> None:
    """Exactly one of each pair is non-``None``, whatever the config."""
    for config in (minimal_config(), example_config()):
        bindings = operation_bindings(config)
        markers = [name for name in bindings if name.endswith("_absent")]
        assert markers
        for marker in markers:
            value = bindings[marker.removesuffix("_absent")]
            assert (value is None) != (bindings[marker] is None), marker


def test_an_unguarded_reference_over_an_absent_collection_fails_loudly() -> None:
    """The renderer refuses; it does not emit a sentence with a hole."""
    with pytest.raises(PromptRenderError) as excinfo:
        render_template(
            "Principals: {{#each principals}}{{this.handle}}{{/each}}",
            operation_bindings(minimal_config()),
        )
    assert "principals" in excinfo.value.missing


def test_a_guarded_template_names_the_absence() -> None:
    body = (
        "{{#if principals}}declared{{/if}}"
        "{{#if principals_absent}}no principals declared{{/if}}"
    )
    assert render_template(body, operation_bindings(minimal_config())) == (
        "no principals declared"
    )
    assert render_template(body, operation_bindings(example_config())) == "declared"


def test_a_team_carries_the_repository_pair_per_item() -> None:
    """The roster is a list, and each entry names one of the two states."""
    teams = operation_bindings(example_config())["teams"]
    assert isinstance(teams, list)
    assert teams
    for entry in teams:
        assert isinstance(entry, dict)
        assert (entry["repository"] is None) != (entry["repository_absent"] is None)


def test_a_principal_carries_the_forge_handle_pair_per_item() -> None:
    """The example declares both states, and each item names exactly one.

    The namespace is keyed by position and by role (KOD-60 R16); the
    positional entries are the roster, and the two roles the routines
    address singly alias into it.
    """
    bindings = operation_bindings(example_config())
    principals = bindings["principals"]
    assert isinstance(principals, dict)
    items = [view for key, view in principals.items() if key.isdigit()]
    assert items
    assert principals["approver"] in items
    assert principals["assignee"] in items
    states = set()
    for item in items:
        assert isinstance(item, dict)
        assert (item["forge_handle"] is None) != (item["forge_handle_absent"] is None)
        states.add(item["forge_handle"] is None)
    assert states == {True, False}


def test_an_unadopted_document_id_is_named_not_blank() -> None:
    config = OperationConfig(
        operation_name="fixture",
        workspace="fixture-workspace",
        documents={
            CHECKPOINT_DOCUMENT_KEY: DocumentEntry(
                system=DocumentSystem.TRACKER,
                name="checkpoint",
            ),
        },
    )
    bindings = operation_bindings(config)
    documents = bindings["documents"]
    assert isinstance(documents, dict)
    entry = documents[CHECKPOINT_DOCUMENT_KEY]
    assert entry["id"] is None
    assert entry["id_absent"] is True

    adopted = operation_bindings(example_config())["documents"]
    assert isinstance(adopted, dict)
    assert adopted[CHECKPOINT_DOCUMENT_KEY]["id"] is not None
    assert adopted[CHECKPOINT_DOCUMENT_KEY]["id_absent"] is None


def test_a_gate_step_names_its_gatehood_instead_of_a_missing_ancestor() -> None:
    bindings = operation_bindings(example_config())
    repos = bindings["repos"]
    assert isinstance(repos, list)
    steps = repos[0]["checks"]
    by_name = {step["name"]: step for step in steps}
    assert by_name["install"]["depends_on"] is None
    assert by_name["install"]["depends_on_absent"] is True
    assert by_name["typecheck"]["depends_on"] == "install"
    assert by_name["typecheck"]["depends_on_absent"] is None
