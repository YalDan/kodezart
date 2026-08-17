"""KOD-89 — the orchestration slot, its two fragments, and what carries them.

The slot is filled by the REGISTRY from the set's declared primitive (the
fire-time ruling FR-2 on KOD-89), so both primitives are exercised here by
resolving the set with the metadata value swapped — the same authored
fragments, a different measured verdict.  Nothing in this module asks a
model to choose; the choice is data and the assertions are on the rendered
bytes.
"""

import shutil
from pathlib import Path

import pytest

from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
from kodezart.core.prompt_rendering import free_binding_names
from kodezart.types.domain.prompts import OrchestrationPrimitive, PromptKey
from tests.prompts.test_claude_opus_goldens import ALL_CASES, V5_SET
from tests.prompts.test_prompt_wiring import (
    CONFIGURED_INVESTIGATION_CAP,
    load_registry,
)

V5_SET_DIR = default_sets_root() / V5_SET

#: The two generative keys the slot is filled for.  ``criteria_generation``
#: in the issue text names the registered key ``acceptance_criteria`` (FR-1).
SLOTTED_KEYS = (PromptKey.ACCEPTANCE_CRITERIA, PromptKey.TICKET_CREATE)
EVALUATIVE_KEYS = (
    PromptKey.EVALUATION,
    PromptKey.CRITERIA_VALIDATION,
    PromptKey.TICKET_REVIEW,
)
UTILITY_KEYS = (
    PromptKey.BRANCH_NAME,
    PromptKey.COMMIT_MESSAGE,
    PromptKey.PR_DESCRIPTION,
)

WORKFLOW_INVOCATION = "run the /kodezart-investigate workflow"
AGENT_FRAGMENT = "execute this spec with parallel Agent\ndispatches in a single turn"
ULTRACODE_TOKEN = "Ultracode"
WORKFLOW_FILE = "kodezart-investigate.js"


def set_with_primitive(
    tmp_path: Path,
    primitive: OrchestrationPrimitive,
) -> InRepoPromptRegistry:
    """The shipped set, copied with one metadata value replaced."""
    root = tmp_path / "sets"
    shutil.copytree(V5_SET_DIR, root / V5_SET)
    metadata = root / V5_SET / "set.toml"
    metadata.write_text(
        metadata.read_text(encoding="utf-8").replace(
            f'orchestration_primitive = "{OrchestrationPrimitive.AGENT.value}"',
            f'orchestration_primitive = "{primitive.value}"',
        ),
        encoding="utf-8",
    )
    return load_registry(sets_root=root, default_set=V5_SET)


def render(registry: InRepoPromptRegistry, key: PromptKey) -> str:
    """Render *key* against the shared fixture case that covers it."""
    case = next(
        variables for case_key, variables in ALL_CASES.values() if case_key is key
    )
    return registry.template_for(key).render({**case, "skills_reference": ""})


def template_bodies() -> dict[str, str]:
    """Every authored member of the set, as it sits on disk."""
    return {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted(V5_SET_DIR.glob("*.md"))
    }


# ---------------------------------------------------------------------------
# AC-1 — the primitive selects the fragment, and the cap is configuration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", SLOTTED_KEYS)
def test_workflow_primitive_renders_the_named_invocation_and_the_opt_in(
    key: PromptKey,
    tmp_path: Path,
) -> None:
    """Primitive = workflow: the workflow is named, and the token rides with it."""
    rendered = render(
        set_with_primitive(tmp_path, OrchestrationPrimitive.WORKFLOW), key
    )

    assert WORKFLOW_INVOCATION in rendered
    assert ULTRACODE_TOKEN in rendered
    assert AGENT_FRAGMENT not in rendered


@pytest.mark.parametrize("key", SLOTTED_KEYS)
def test_agent_primitive_renders_the_parallel_dispatch_and_no_ultracode_token(
    key: PromptKey,
    tmp_path: Path,
) -> None:
    """Primitive = agent: the fallback fragment, and the token nowhere in it."""
    rendered = render(set_with_primitive(tmp_path, OrchestrationPrimitive.AGENT), key)

    assert AGENT_FRAGMENT in rendered
    assert ULTRACODE_TOKEN not in rendered
    assert WORKFLOW_INVOCATION not in rendered


@pytest.mark.parametrize("primitive", list(OrchestrationPrimitive))
@pytest.mark.parametrize("key", SLOTTED_KEYS)
def test_both_primitives_render_the_spec_with_the_configured_cap(
    primitive: OrchestrationPrimitive,
    key: PromptKey,
    tmp_path: Path,
) -> None:
    """One spec, shared: the two blocks differ in coordination, not in contract."""
    rendered = render(set_with_primitive(tmp_path, primitive), key)

    assert f"CAP:      at most {CONFIGURED_INVESTIGATION_CAP} agents" in rendered
    assert "one bounded" in rendered


def test_the_cap_reaches_the_render_from_configuration_not_from_the_set() -> None:
    """A different configured cap changes the rendered spec, so it is not a literal."""
    other = CONFIGURED_INVESTIGATION_CAP + 1
    registry = load_registry(default_set=V5_SET, investigation_cap=other)

    assert f"at most {other} agents" in render(registry, PromptKey.TICKET_CREATE)


# ---------------------------------------------------------------------------
# AC-2 and AC-3 — the roles that receive neither fragment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", EVALUATIVE_KEYS)
def test_evaluative_templates_have_no_orchestration_block(key: PromptKey) -> None:
    """The deliberate asymmetry: judgment sessions do not fan out."""
    body = template_bodies()[key.value]
    assert "orchestration_block" not in free_binding_names(body)

    rendered = render(load_registry(default_set=V5_SET), key)
    assert AGENT_FRAGMENT not in rendered
    assert WORKFLOW_INVOCATION not in rendered


@pytest.mark.parametrize("key", UTILITY_KEYS)
def test_utility_templates_have_no_orchestration_block(key: PromptKey) -> None:
    """A role that emits a name or a message has nothing to investigate."""
    body = template_bodies()[key.value]
    assert "orchestration_block" not in free_binding_names(body)

    rendered = render(load_registry(default_set=V5_SET), key)
    assert AGENT_FRAGMENT not in rendered
    assert WORKFLOW_INVOCATION not in rendered


def test_exactly_the_two_generative_keys_declare_the_slot() -> None:
    """Which members carry the slot is the templates' own census, not a roster."""
    declared = {
        name
        for name, body in template_bodies().items()
        if "orchestration_block" in free_binding_names(body)
    }
    assert declared == {key.value for key in SLOTTED_KEYS}


# ---------------------------------------------------------------------------
# AC-4 and AC-8 — statics over the whole set
# ---------------------------------------------------------------------------


def test_the_ultracode_token_appears_in_no_template_body() -> None:
    """The token lives in its fragment; a body carrying it is a rule break."""
    offenders = [
        name for name, body in template_bodies().items() if ULTRACODE_TOKEN in body
    ]
    assert offenders == []


def test_no_template_body_carries_the_spec_or_a_literal_cap() -> None:
    """The cap is configuration: the spec is a fragment, and it states a tag."""
    offenders = [name for name, body in template_bodies().items() if "CAP:" in body]
    assert offenders == []

    spec_line = next(
        line
        for line in (V5_SET_DIR / "set.toml").read_text(encoding="utf-8").splitlines()
        if line.startswith("CAP:")
    )
    assert "{{investigation_cap}}" in spec_line
    assert not any(char.isdigit() for char in spec_line)


def test_the_set_names_exactly_one_workflow_file() -> None:
    """One named workflow; growing the library is another lane's deliverable."""
    metadata = (V5_SET_DIR / "set.toml").read_text(encoding="utf-8")
    named = {
        word.strip(".,`'\"")
        for body in [*template_bodies().values(), metadata]
        for word in body.split()
        if word.endswith(".js") or "kodezart-investigate" in word
    }
    assert named == {"/kodezart-investigate"}
    assert (
        Path(__file__).resolve().parents[2] / ".claude" / "workflows" / WORKFLOW_FILE
    ).is_file()


def test_a_member_asking_for_the_slot_in_a_set_without_one_is_a_boot_error(
    tmp_path: Path,
) -> None:
    """Never a silently empty block: the key is named in the typed error."""
    from kodezart.core.errors import PromptResolutionError

    root = tmp_path / "sets"
    shutil.copytree(V5_SET_DIR, root / V5_SET)
    metadata = root / V5_SET / "set.toml"
    metadata.write_text(
        metadata.read_text(encoding="utf-8").replace(
            f'orchestration_primitive = "{OrchestrationPrimitive.AGENT.value}"',
            "",
        ),
        encoding="utf-8",
    )

    with pytest.raises(PromptResolutionError) as excinfo:
        load_registry(sets_root=root, default_set=V5_SET)
    assert set(excinfo.value.failing_keys) == {key.value for key in SLOTTED_KEYS}
