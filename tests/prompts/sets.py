"""The shipped prompt sets, and the fixture cases every suite renders them with.

Each set is a complete corpus authored for one model and held to the same
rules as every other; none is a frozen copy of another, and nothing here
pins bytes.  What
is shared is the CASE roster — one fixed set of variables per function
key — so a suite that renders either set renders it the way every other
suite does, and a difference between two renders is a difference of
authoring rather than of test setup.
"""

from kodezart.adapters.in_repo_prompt_registry import InRepoPromptRegistry
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.types.domain.prompts import PromptKey
from tests.fakes import pass_render_variables
from tests.prompts.test_prompt_wiring import (
    DEFAULT_SET,
    RENDER_CASES,
    REPO_ROOT,
    TASK_MD,
    load_registry,
)

#: A set is a corpus authored for a MODEL, and which one runs is chosen for
#: the model in use: ``anthropic_v5`` is the configured default today,
#: ``claude-opus`` is the set for that engine, and a third engine gets a
#: third directory (KOD-306).  ``DEFAULT_SET`` in the wiring suite names
#: ``claude-opus`` — the name is the migration's, from when it was.
V5_SET = "anthropic_v5"
OPUS_SET = DEFAULT_SET

EXAMPLE_OPERATION = REPO_ROOT / "docs" / "operation.example.toml"

#: What the RUNNER binds when it fires a pass and nothing else does: the
#: per-run record title every pass template carries (KOD-290, KOD-306).  A
#: suite that renders a pass template directly binds this, the way the
#: service does through ``pass_render_bindings``.
PER_RUN: dict[str, object] = {"record_title": "pass — fixture @ the pass start"}

AUDITED_PAYLOAD = "Golden payload under audit.\nSecond line."
AUDIT_DESTINATION = "a public code-hosting surface"

#: The keys the wiring suite's roster leaves to the operation namespace:
#: rendered from the same kind of fixed fixtures, plus the operation the
#: two pass keys and the knowledge map address.
EXTENDED_CASES: dict[str, tuple[PromptKey, dict[str, object]]] = {
    "content_audit": (
        PromptKey.CONTENT_AUDIT,
        {"content": AUDITED_PAYLOAD, "destination": AUDIT_DESTINATION},
    ),
    "knowledge_map": (PromptKey.KNOWLEDGE_MAP, {}),
    "fire_prep_pass": (
        PromptKey.FIRE_PREP_PASS,
        pass_render_variables(PromptKey.FIRE_PREP_PASS),
    ),
    "grooming_pass": (
        PromptKey.GROOMING_PASS,
        pass_render_variables(PromptKey.GROOMING_PASS),
    ),
    "remediation_ticket": (
        PromptKey.REMEDIATION_TICKET,
        {
            "original_ticket": TASK_MD,
            "done_work": "golden done work",
            "failure_evidence": "golden failure evidence",
        },
    ),
}

ALL_CASES: dict[str, tuple[PromptKey, dict[str, object]]] = {
    **RENDER_CASES,
    **EXTENDED_CASES,
}


def operation_registry(*, default_set: str = OPUS_SET) -> InRepoPromptRegistry:
    """A set with the example operation namespace bound.

    The extra names are additive: a template that references none of them
    renders exactly as it does under the wiring suite's empty bindings,
    which is what lets one registry serve every case.
    """
    return load_registry(
        default_set=default_set,
        bindings=dict(operation_bindings(load_operation_config(EXAMPLE_OPERATION))),
    )


def v5_registry() -> InRepoPromptRegistry:
    """The configured default set, with the same operation namespace bound."""
    return operation_registry(default_set=V5_SET)


def render_case(registry: InRepoPromptRegistry, case: str) -> str:
    """Render one case with the skills fragment bound EMPTY."""
    key, variables = ALL_CASES[case]
    return registry.template_for(key).render({**variables, "skills_reference": ""})


def render_v5_case(case: str) -> str:
    """Render one case against the configured default set, skills bound EMPTY."""
    return render_case(v5_registry(), case)


def render_case_with_declared_skills(registry: InRepoPromptRegistry, case: str) -> str:
    """Render one case with the key's DECLARED skills loadout bound."""
    key, variables = ALL_CASES[case]
    return registry.template_for(key).render(variables)


def test_every_registered_function_key_has_a_render_case() -> None:
    """The roster is a census of the keys, not a sample of them."""
    assert {key for key, _ in ALL_CASES.values()} == set(PromptKey)
