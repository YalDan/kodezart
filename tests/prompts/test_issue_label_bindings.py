"""The configured criterion label reaches judgment prompts without a literal."""

import pytest

from kodezart.core.errors import PromptRenderError
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.core.prompt_rendering import render_template
from kodezart.types.domain.operation import OperationConfig


def test_configured_issue_label_names_and_extra_keys_reach_the_render():
    config = OperationConfig(
        operation_name="fixture",
        workspace="fixture",
        issue_labels={"criterion": "Verification requirement", "phase": "Checked"},
    )
    assert (
        render_template(
            "{{issue_labels.criterion}} / {{issue_labels.phase}}",
            operation_bindings(config),
        )
        == "Verification requirement / Checked"
    )


def test_an_absent_criterion_label_is_guardable_but_not_substituted():
    config = OperationConfig(operation_name="fixture", workspace="fixture")
    bindings = operation_bindings(config)
    assert (
        render_template(
            "{{#if issue_labels.criterion}}{{issue_labels.criterion}}{{/if}}", bindings
        )
        == ""
    )
    with pytest.raises(PromptRenderError) as caught:
        render_template("{{issue_labels.criterion}}", bindings)
    assert caught.value.missing == ("issue_labels.criterion",)
