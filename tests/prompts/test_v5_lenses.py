"""KOD-87-AC-7 — the lens definitions, as PROPERTIES of the composed prompt.

Set content composed by the same path as every key: what is asserted is
the placement obligation and the reach, on the composed prompt, rather
than the bytes it happened to render to on the day.
"""

import pytest

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from tests.prompts.sets import V5_SET, v5_registry
from tests.prompts.test_prompt_wiring import load_registry

DEFINITION_NAMES = ("doc-verifier", "draft-critic", "explorer")


def test_the_set_declares_exactly_the_three_lenses() -> None:
    """Three definitions, named — a fourth is an authoring decision, not drift."""
    assert tuple(d.name for d in v5_registry().definitions()) == DEFINITION_NAMES


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
