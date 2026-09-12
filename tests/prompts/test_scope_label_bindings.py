"""Scope admission names reach prompts through the operation configuration."""

import pytest

from kodezart.core.errors import PromptRenderError
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.core.prompt_rendering import render_template
from kodezart.types.domain.operation import OperationConfig


def test_configured_scope_names_and_extra_members_reach_the_render() -> None:
    config = OperationConfig(
        operation_name="fixture",
        workspace="fixture-workspace",
        scope_labels={
            "triage": "assess scope",
            "proposed": "await authorization",
            "approved": "authorized scope",
            "paused": "await maintenance",
        },
    )

    rendered = render_template(
        "{{scope_labels.approved}} / {{scope_labels.paused}}",
        operation_bindings(config),
    )

    assert rendered == "authorized scope / await maintenance"


def test_absent_scope_mapping_has_an_explicit_render_arm() -> None:
    config = OperationConfig(operation_name="fixture", workspace="fixture-workspace")

    rendered = render_template(
        "{{#if scope_labels}}{{scope_labels.approved}}{{/if}}"
        "{{#if scope_labels_absent}}scope admission is not configured{{/if}}",
        operation_bindings(config),
    )

    assert rendered == "scope admission is not configured"


def test_using_an_absent_scope_member_refuses_with_its_name() -> None:
    config = OperationConfig(operation_name="fixture", workspace="fixture-workspace")

    with pytest.raises(PromptRenderError) as caught:
        render_template("{{scope_labels.approved}}", operation_bindings(config))

    assert caught.value.missing == ("scope_labels.approved",)
