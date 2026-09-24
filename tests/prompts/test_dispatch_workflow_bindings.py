"""The dispatch workflow reaches the intake prompts as one absentable pair.

Under the fire workflow the fire-prep pass stages issues to the queue's
proposed label, as v0.2 does.  Under the scope workflow nothing fires an
issue, so the same prompt stages no fire and sets no queue label: it grooms
triage into the hierarchy and proposes the node.  Measured 2026-09-24: a
scope deployment whose prompt still carried the staging routine produced a
frozen single-issue fire nothing could dispatch.
"""

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.types.domain.dispatch import DispatchWorkflow
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.sets import ALL_CASES, EXAMPLE_OPERATION, V5_SET
from tests.prompts.test_prompt_wiring import load_registry

STAGING_SENTENCE = "one coherent buildable scope is a single fire"
SCOPE_SENTENCE = "This deployment dispatches approved scopes, never issues"
KISS_SENTENCE = "The simplest solution, always."
QUEUE_STATES_SENTENCE = "Workflow states follow the queue, every pass."


def _rendered(key: PromptKey, workflow: DispatchWorkflow) -> str:
    bindings = operation_bindings(
        load_operation_config(EXAMPLE_OPERATION), dispatch_workflow=workflow
    )
    registry = load_registry(default_set=V5_SET, bindings=dict(bindings))
    case = next(name for name, (case_key, _) in ALL_CASES.items() if case_key is key)
    _, variables = ALL_CASES[case]
    return registry.template_for(key).render({**variables, "skills_reference": ""})


def test_the_pair_is_exclusive_and_defaults_to_the_fire_workflow() -> None:
    config = load_operation_config(EXAMPLE_OPERATION)
    default = operation_bindings(config)
    fire = operation_bindings(config, dispatch_workflow=DispatchWorkflow.FIRE)
    scope = operation_bindings(config, dispatch_workflow=DispatchWorkflow.SCOPE)

    assert (default["fire_dispatch"], default["fire_dispatch_absent"]) == (True, None)
    assert (fire["fire_dispatch"], fire["fire_dispatch_absent"]) == (True, None)
    assert (scope["fire_dispatch"], scope["fire_dispatch_absent"]) == (None, True)


def test_fire_prep_stages_fires_only_under_the_fire_workflow() -> None:
    fire = _rendered(PromptKey.FIRE_PREP_PASS, DispatchWorkflow.FIRE)
    scope = _rendered(PromptKey.FIRE_PREP_PASS, DispatchWorkflow.SCOPE)

    assert STAGING_SENTENCE in fire
    assert SCOPE_SENTENCE not in fire
    assert STAGING_SENTENCE not in scope
    assert SCOPE_SENTENCE in scope
    assert "the issues staged to" in fire
    assert "the nodes proposed for scope admission" in scope
    assert "the issues staged to" not in scope


def test_grooming_reads_the_queue_only_under_the_fire_workflow() -> None:
    fire = _rendered(PromptKey.GROOMING_PASS, DispatchWorkflow.FIRE)
    scope = _rendered(PromptKey.GROOMING_PASS, DispatchWorkflow.SCOPE)

    assert QUEUE_STATES_SENTENCE in fire
    assert QUEUE_STATES_SENTENCE not in scope
    assert "Never edit the fenced frozen body" in fire
    assert "Never edit the fenced frozen body" not in scope


def test_both_intake_prompts_carry_the_standing_rule_under_both_workflows() -> None:
    for key in (PromptKey.FIRE_PREP_PASS, PromptKey.GROOMING_PASS):
        for workflow in DispatchWorkflow:
            rendered = _rendered(key, workflow)
            assert KISS_SENTENCE in rendered
            assert "Push every scope to a decision" in rendered
            assert "one short comment with the question first" in rendered
