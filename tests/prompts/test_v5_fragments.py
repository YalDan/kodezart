"""KOD-88-AC-5 — the hoisted fragments, each with one source and one place.

Five repeated house-rule clusters, two full copies of the suppression check
plus a subset plus two rationale copies, twenty-two scattered depth tokens:
the corpus this set replaces stated each of them wherever it was needed.
Hoisting is only a saving if the source stays single and the consumers stay
countable, so both halves are asserted here rather than the first alone.
"""

import tomllib

import pytest

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.test_claude_opus_goldens import V5_SET
from tests.prompts.test_prompt_wiring import DEFAULT_SET, load_registry
from tests.prompts.test_v5_goldens import v5_registry

SET_TOML = default_sets_root() / V5_SET / "set.toml"

#: The three roles that carry the shared lint vocabulary. The validator is
#: NOT among them, per the fire-time ruling of 2026-08-11: the proxy greps
#: the changed lines of a diff, and the validator runs at the base ref
#: where there is no diff to grep.
PROXY_CONSUMERS = frozenset(
    {
        PromptKey.EVALUATION.value,
        PromptKey.POST_MERGE_REVIEW.value,
        PromptKey.ACCEPTANCE_CRITERIA.value,
    },
)

UTILITY_KEYS = frozenset(
    {
        PromptKey.BRANCH_NAME.value,
        PromptKey.COMMIT_MESSAGE.value,
        PromptKey.PR_DESCRIPTION.value,
        PromptKey.KNOWLEDGE_MAP.value,
    },
)


def metadata() -> dict[str, object]:
    """The set's declared metadata, read as data."""
    return tomllib.loads(SET_TOML.read_text(encoding="utf-8"))


def fragment(name: str) -> str:
    """One declared fragment's text; absence is a failure, not an empty string."""
    fragments = metadata()["fragments"]
    assert isinstance(fragments, dict)
    assert name in fragments, f"the set declares no {name} fragment"
    return str(fragments[name])


def v5_bodies() -> dict[str, str]:
    """Every resolved member body of the new set, keyed by function key."""
    registry = v5_registry()
    return {key.value: registry.template_for(key).body for key in PromptKey}


# ---------------------------------------------------------------------------
# suppression_proxy — one source, three consumers
# ---------------------------------------------------------------------------


def test_the_suppression_proxy_is_declared_exactly_once() -> None:
    """One source: the text appears in the set's metadata and nowhere else.

    Counted over the member FILES rather than the resolved bodies, because
    resolution is what puts it into a body — a member that carried the text
    verbatim would be the second copy this fragment exists to prevent.
    """
    proxy_first_line = fragment("suppression_proxy").splitlines()[0]
    members = sorted((default_sets_root() / V5_SET).glob("*.md"))
    assert members
    carriers = [
        path.name for path in members if proxy_first_line in path.read_text("utf-8")
    ]
    assert carriers == []


def test_the_suppression_proxy_resolves_into_exactly_its_three_consumers() -> None:
    """Countable consumers: the three roles that judge or author against a diff."""
    proxy = fragment("suppression_proxy")
    consumers = {key for key, body in v5_bodies().items() if proxy in body}
    assert consumers == PROXY_CONSUMERS


def test_the_validator_does_not_carry_the_diff_grep() -> None:
    """It runs at the base ref, where the changed lines it names do not exist."""
    bodies = v5_bodies()
    assert fragment("suppression_proxy") not in bodies[PromptKey.CRITERIA_VALIDATION]


# ---------------------------------------------------------------------------
# house_rules — in no body, delivered as the system-prompt append
# ---------------------------------------------------------------------------


def test_the_house_rules_appear_in_no_template_body() -> None:
    """A rule stated in every prompt is the duplication being retired."""
    rules = fragment("house_rules")
    first_paragraph = rules.split("\n\n")[1]
    carriers = sorted(
        key for key, body in v5_bodies().items() if first_paragraph in body
    )
    assert carriers == []


def test_the_house_rules_are_delivered_as_the_system_prompt_append() -> None:
    """Absence from the bodies is only half the claim; this is the other half."""
    assert v5_registry().system_prompt_append() == fragment("house_rules")


def test_the_legacy_set_declares_no_system_prompt_append() -> None:
    """The set that states its rules inline contributes no append, and is unchanged."""
    assert load_registry(default_set=DEFAULT_SET).system_prompt_append() is None


def test_the_no_early_stopping_paragraph_survives_the_hoist() -> None:
    """The load-bearing paragraph: ending without the output IS early stopping."""
    rules = fragment("house_rules")
    assert "Act as soon as you have enough information." in rules
    assert "produced the required structured output" in rules


# ---------------------------------------------------------------------------
# ultrathink_instruction — final block of every non-utility role, absent elsewhere
# ---------------------------------------------------------------------------


def test_the_declared_utility_roster_is_the_one_the_rules_read() -> None:
    """The roster is set data; this pins which roles it names."""
    assert set(metadata()["utility_keys"]) == UTILITY_KEYS  # type: ignore[arg-type]


@pytest.mark.parametrize("key", sorted(UTILITY_KEYS))
def test_utility_templates_carry_no_depth_instruction(key: str) -> None:
    """A name, a message, a description, a prelude: none of them reason."""
    assert "ultrathink" not in v5_bodies()[key].lower()


@pytest.mark.parametrize(
    "key",
    sorted({k.value for k in PromptKey} - UTILITY_KEYS),
)
def test_every_judgment_template_ends_with_the_depth_block(key: str) -> None:
    """Exactly one occurrence, and it is the last thing the session reads."""
    body = v5_bodies()[key]
    instruction = fragment("ultrathink_instruction")
    assert body.lower().count("ultrathink") == 1
    assert body.rstrip("\n").endswith(instruction.rstrip("\n"))


def test_the_depth_block_is_declared_once_and_carried_by_no_member_file() -> None:
    """Same one-source rule as the proxy: the members ask, the set supplies."""
    instruction = fragment("ultrathink_instruction")
    members = sorted((default_sets_root() / V5_SET).glob("*.md"))
    carriers = [path.name for path in members if instruction in path.read_text("utf-8")]
    assert carriers == []


def test_the_ultracode_token_is_declared_and_used_by_no_member() -> None:
    """Inert by design: origin-gated, so it is vocabulary rather than a trigger."""
    assert "Ultracode." in fragment("ultracode_instruction")
    assert [
        key for key, body in v5_bodies().items() if "ultracode" in body.lower()
    ] == []
