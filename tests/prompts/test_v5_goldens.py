"""KOD-88-AC-3 and KOD-87-AC-7 — the new set's rendered corpus, frozen.

Rendered from the SAME fixtures the legacy goldens use, so the two sets sit
side by side and a diff between them is a diff of authoring rather than of
test setup.  The lens definitions are frozen here too: they are set content
composed by the same path, and their one placement obligation — the depth
block last — is asserted on the composed prompt rather than assumed from
the fragment being declared.
"""

import pytest

from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.test_claude_opus_goldens import (
    ALL_CASES,
    EXAMPLE_OPERATION,
    V5_SET,
)
from tests.prompts.test_prompt_wiring import GOLDENS, load_registry

V5_GOLDENS = GOLDENS.parent / V5_SET
#: Lens prompts are set content but not function keys, so they are frozen
#: OUTSIDE the key-golden tree: every file under that tree pins one key's
#: render, and a guard rests on the mapping being total.
DEFINITION_GOLDENS = GOLDENS.parent.parent / "definition_goldens" / V5_SET
DEFINITION_NAMES = ("doc-verifier", "draft-critic", "explorer")


def v5_registry() -> InRepoPromptRegistry:
    """The new set with the same operation namespace the legacy suite binds."""
    return load_registry(
        default_set=V5_SET,
        bindings=dict(operation_bindings(load_operation_config(EXAMPLE_OPERATION))),
    )


def render_case(golden_name: str) -> str:
    """Render one shared fixture case against the new set."""
    key, variables = ALL_CASES[golden_name]
    return v5_registry().template_for(key).render({**variables, "skills_reference": ""})


def test_the_new_set_covers_every_shared_fixture_case() -> None:
    """Non-vacuity: the case roster is the legacy one, key for key."""
    covered = {key for key, _ in ALL_CASES.values()}
    assert covered == set(PromptKey)


@pytest.mark.parametrize("golden_name", sorted(ALL_CASES))
def test_rendered_output_equals_the_checked_in_golden(golden_name: str) -> None:
    """One golden per case; authoring a template changes exactly one file."""
    expected = (V5_GOLDENS / f"{golden_name}.txt").read_text(encoding="utf-8")
    assert render_case(golden_name) == expected


def test_the_golden_corpus_is_a_census_of_the_cases() -> None:
    """A golden with no case, or a case with no golden, is a silent gap."""
    on_disk = {path.stem for path in V5_GOLDENS.glob("*.txt")}
    assert on_disk == set(ALL_CASES)


def test_the_two_sets_are_diffable_case_for_case() -> None:
    """Same names on both sides: the point of rendering from one fixture set."""
    legacy = {path.stem for path in GOLDENS.glob("*.txt")}
    assert {path.stem for path in V5_GOLDENS.glob("*.txt")} == legacy


# ---------------------------------------------------------------------------
# KOD-87-AC-7 — the lens definitions' composed prompts
# ---------------------------------------------------------------------------


def test_the_set_declares_exactly_the_three_lenses() -> None:
    """Three definitions, named — a fourth is an authoring decision, not drift."""
    assert tuple(d.name for d in v5_registry().definitions()) == DEFINITION_NAMES


@pytest.mark.parametrize("name", DEFINITION_NAMES)
def test_definition_prompt_matches_its_golden(name: str) -> None:
    """The composed lens prompt, frozen like every other piece of set content."""
    definition = next(d for d in v5_registry().definitions() if d.name == name)
    expected = (DEFINITION_GOLDENS / f"{name}.txt").read_text(encoding="utf-8")
    assert definition.prompt == expected


@pytest.mark.parametrize("name", DEFINITION_NAMES)
def test_definition_prompt_ends_with_the_depth_block(name: str) -> None:
    """A lens is a judgment role: it ends where every judgment role ends."""
    metadata_fragment = (default_sets_root() / V5_SET / "set.toml").read_text(
        encoding="utf-8"
    )
    assert "ultrathink_instruction" in metadata_fragment

    definition = next(d for d in v5_registry().definitions() if d.name == name)
    assert definition.prompt.rstrip("\n").endswith(
        "reason as thoroughly\nas the task warrants before you act."
    )
    assert definition.prompt.lower().count("ultrathink") == 1


@pytest.mark.parametrize("name", DEFINITION_NAMES)
def test_definition_tools_are_read_only(name: str) -> None:
    """The second of the two bounds on a lens's reach, asserted as content."""
    definition = next(d for d in v5_registry().definitions() if d.name == name)
    assert set(definition.tools) <= {"Read", "Glob", "Grep", "WebSearch", "WebFetch"}


def test_the_legacy_set_declares_no_definitions() -> None:
    """Authoring a second set gave the first one nothing it did not have."""
    assert load_registry().definitions() == ()
