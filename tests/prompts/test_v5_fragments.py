"""KOD-88-AC-5 — the hoisted fragments, each with one source and one place.

Five repeated house-rule clusters, two full copies of the suppression check
plus a subset plus two rationale copies, twenty-two scattered depth tokens:
the corpus this set replaces stated each of them wherever it was needed.
Hoisting is only a saving if the source stays single and the consumers stay
countable, so both halves are asserted here rather than the first alone.
"""

import tomllib

import pytest

from kodezart.adapters.claude.agents_mapping import map_system_prompt
from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.sets import V5_SET, v5_registry
from tests.prompts.style_detectors import data_boundary_sentences
from tests.prompts.test_prompt_wiring import DEFAULT_SET, load_registry

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
        PromptKey.FIRE_RECORD.value,
        PromptKey.NATIVE_WRITER_CONTRACT.value,
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


def prose(text: str) -> str:
    """*text* with its line wrapping removed, so a sentence compares as a sentence."""
    return " ".join(text.split())


def member_files_carrying(text: str) -> list[str]:
    """Every `.md` under the sets root (each shipped set, lens bodies included)
    whose prose contains *text*, as posix paths relative to that root."""
    root = default_sets_root()
    files = sorted(root.rglob("*.md"))
    assert files
    needle = prose(text)
    return [
        path.relative_to(root).as_posix()
        for path in files
        if needle in prose(path.read_text("utf-8"))
    ]


# ---------------------------------------------------------------------------
# the member-file scan — the positive control every one-source test rests on
# ---------------------------------------------------------------------------


def test_the_member_file_scan_reaches_each_set_and_the_lens_bodies() -> None:
    """A scan that finds nothing anywhere would pass every one-source test.

    Three files whose own text must find them: a member of the new set, a
    member of the set it replaces, and a lens body under `definitions/`,
    the last derived from the tree rather than named.
    """
    root = default_sets_root()
    lens = sorted((root / V5_SET / "definitions").glob("*.md"))[0]
    for path in (
        root / V5_SET / "evaluation.md",
        root / DEFAULT_SET / "evaluation.md",
        lens,
    ):
        first_line = next(
            line for line in path.read_text("utf-8").splitlines() if line.strip()
        )
        assert path.relative_to(root).as_posix() in member_files_carrying(first_line)


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
    assert member_files_carrying(proxy_first_line) == []


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
# design_review — one source, two consumers (KOD-883)
# ---------------------------------------------------------------------------

#: The two roles that grade a changeset. Nothing else has changed files to
#: refute a design decision against.
DESIGN_REVIEW_CONSUMERS = frozenset(
    {PromptKey.EVALUATION.value, PromptKey.POST_MERGE_REVIEW.value},
)

#: Each clause the refutation would stop asking for if it were dropped.
DESIGN_REVIEW_CLAUSES: tuple[str, ...] = (
    "try to refute that the change meets the engineering standard in your house rules",
    "that simpler shape is the refutation",
    "fail every criterion whose evidence rests on that file",
    "passed=false, with the principle and the file:line in reasoning",
    "a concern raised in your own name rather than against one criterion's verdict",
)


def test_the_design_review_is_declared_exactly_once() -> None:
    """Same one-source rule as the proxy: the members ask, the set supplies."""
    assert member_files_carrying(fragment("design_review").splitlines()[0]) == []


def test_the_design_review_resolves_into_exactly_the_two_changeset_graders() -> None:
    """Countable consumers, and the instruction precedes the data boundary.

    Equality is both halves of the claim: composed into those two, absent
    from every other member. The order matters because an instruction placed
    after the boundary sentence reads as part of the data that follows it.
    """
    refutation = fragment("design_review")
    bodies = v5_bodies()
    consumers = {key for key, body in bodies.items() if refutation in body}
    assert consumers == DESIGN_REVIEW_CONSUMERS
    for key in sorted(consumers):
        body = bodies[key]
        assert body.index(refutation) < body.index(data_boundary_sentences(body)[0])


@pytest.mark.parametrize("clause", DESIGN_REVIEW_CLAUSES)
def test_the_design_review_keeps_each_load_bearing_clause(clause: str) -> None:
    """Named one by one, so removing any one of them reds its own case."""
    assert clause in prose(fragment("design_review"))


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
    """Absence from the bodies is only half the claim; this is the other half.

    Per key as well as per set: the append is read off the session policy the
    dispatch actually carries, and off the mapping that hands it to the SDK,
    so "every dispatched key receives it" is asserted where it is decided.
    """
    registry = v5_registry()
    rules = fragment("house_rules")
    assert registry.system_prompt_append() == rules
    for key in PromptKey:
        policy = registry.session_policy(key)
        assert policy.system_prompt_append == rules
        mapped = map_system_prompt(policy)
        assert mapped is not None
        assert mapped["append"] == rules


def test_the_legacy_set_declares_no_system_prompt_append() -> None:
    """The set that states its rules inline contributes no append, and is unchanged."""
    assert load_registry(default_set=DEFAULT_SET).system_prompt_append() is None


# ---------------------------------------------------------------------------
# house_rules — the engineering standard, one sentence per reading (KOD-882)
# ---------------------------------------------------------------------------

#: One sentence per reading of the standard, in the order the fragment states
#: them. Imported by the loop suite, which asserts the same six sentences
#: reach the writer and the grader, so the readings have one source too.
ENGINEERING_READINGS: tuple[str, ...] = (
    "SOLID, DRY, hexagonal, and KISS as the way to get there.",
    "Ports are narrow role protocols the application defines, and a consumer "
    "depends on the smallest role it needs.",
    "One adapter package per vendor, the vendor's wire shapes inside it, and no "
    "judgement in an adapter.",
    "Judgement is a prompt and arithmetic is a plain function.",
    "Typed errors before any backend call.",
    "The smallest change that satisfies the criterion: delete rather than carry, "
    "and no abstraction, parameter or file beyond what the task requires.",
)


@pytest.mark.parametrize("reading", ENGINEERING_READINGS)
def test_the_engineering_standard_states_each_reading(reading: str) -> None:
    """Named one by one, so dropping any one of them reds its own case."""
    assert reading in prose(fragment("house_rules"))


@pytest.mark.parametrize("reading", ENGINEERING_READINGS)
def test_each_reading_has_one_source_and_no_member_of_either_set_carries_it(
    reading: str,
) -> None:
    """Counted over the whole manifest, so no second fragment can restate it."""
    assert prose(SET_TOML.read_text(encoding="utf-8")).count(reading) == 1
    assert member_files_carrying(reading) == []


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
    assert member_files_carrying(fragment("ultrathink_instruction")) == []


def test_the_ultracode_token_is_declared_and_used_by_no_member() -> None:
    """Inert by design: origin-gated, so it is vocabulary rather than a trigger."""
    assert "Ultracode." in fragment("ultracode_instruction")
    assert [
        key for key, body in v5_bodies().items() if "ultracode" in body.lower()
    ] == []
