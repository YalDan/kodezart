"""The organize prompts' per-call binding names cannot collide with boot bindings."""

from pathlib import Path

import pytest
import structlog
from pydantic import create_model

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.prompts import boot_prompts
from kodezart.config.app import AppConfig
from kodezart.core.errors import PromptNamespaceCollisionError
from kodezart.core.prompt_namespaces import (
    PER_CALL_VARIABLE_NAMES,
    SET_FRAGMENT_NAMES,
    bindings_for,
)
from kodezart.types.domain.operation import OperationConfig
from tests.prompts.sets import EXAMPLE_OPERATION

ORGANIZE_BINDINGS = {
    "organize_context",
    "mandate_rubric",
    "issue_body",
    "linked_issue_bodies",
    "criterion_issue_bodies",
    "refusal_evidence",
    "defect_classes",
}


def test_named_per_call_roster_is_disjoint_from_both_other_namespaces() -> None:
    assert ORGANIZE_BINDINGS <= PER_CALL_VARIABLE_NAMES
    assert ORGANIZE_BINDINGS.isdisjoint(SET_FRAGMENT_NAMES)
    operation = load_operation_config(EXAMPLE_OPERATION)
    assert ORGANIZE_BINDINGS.isdisjoint(bindings_for(operation))
    assert ORGANIZE_BINDINGS.isdisjoint(OperationConfig.model_fields)


@pytest.mark.parametrize("name", sorted(ORGANIZE_BINDINGS))
async def test_a_configuration_field_collision_is_named_at_prompt_boot(
    name: str, tmp_path: Path
) -> None:
    """A fixture schema extension cannot capture a reserved per-call root.

    Production config forbids unknown fields. A derived fixture models an
    author adding such a field; boot must refuse it even before a template
    or operation projection has started consuming that field.
    """
    fixture_type = create_model(
        "CollidingOperation", __base__=OperationConfig, **{name: (str, ...)}
    )
    path = tmp_path / "operation.toml"
    path.write_text(
        'operation_name = "fixture"\nworkspace = "fixture"\n', encoding="utf-8"
    )
    operation = fixture_type.model_validate(
        {**load_operation_config(path).model_dump(), name: "configured collision"}
    )
    with pytest.raises(PromptNamespaceCollisionError) as caught:
        await boot_prompts(
            config=AppConfig(), operation=operation, log=structlog.get_logger(__name__)
        )
    assert caught.value.colliding == (name,)


@pytest.mark.parametrize("name", sorted(ORGANIZE_BINDINGS))
async def test_a_projected_configuration_binding_collision_is_named_at_boot(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation = load_operation_config(EXAMPLE_OPERATION)
    projected = {**bindings_for(operation), name: "fixture projected binding"}
    monkeypatch.setattr(
        "kodezart.core.prompt_namespaces.operation_bindings", lambda _: projected
    )
    with pytest.raises(PromptNamespaceCollisionError) as caught:
        await boot_prompts(
            config=AppConfig(), operation=operation, log=structlog.get_logger(__name__)
        )
    assert caught.value.colliding == (name,)
