"""KOD-83-AC-4 and AC-5 — the style-regression suite and its self-test.

Eleven rules from KOD-68 deliverable 5, each carrying three obligations:

* it holds over every ``anthropic_v5`` template,
* it FAILS a deliberately violating fixture, so the rule is not vacuous,
* where the violation exists in the legacy corpus, the same detector reports
  it, with the count re-derived against the corpus that exists.

The count table is re-derived per the fire-time ruling on KOD-83 (2026-08-11):
the pinned census was measured over ``src/kodezart/prompts/*.py`` at 92597c0,
a tree the composed base does not have. Six of the seven pinned rows reproduce
exactly at 92597c0 and one (``Sherlock``) was off by one at its own source,
which is why the counts below are asserted per key as well as in total — a
corpus change then names the key that moved instead of failing on an integer.
"""

import tomllib
from dataclasses import dataclass

import pytest

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.sets import V5_SET
from tests.prompts.style_detectors import (
    COUNTED_DETECTORS,
    artifact_tag_names,
    data_boundary_sentences,
    defer_shaming,
    dispatch_parallel_sentence,
    dispatch_subagent_type,
    emphasis_inflation,
    house_rules_prose,
    no_defer_rule_header,
    occurrences_outside,
    persona_sherlock,
    persona_watson,
    reasoning_reproduction,
    skill_enumeration_detector,
    ultracode_tokens,
    ultrathink_placement_violations,
    unbalanced_artifact_tags,
)
from tests.prompts.test_prompt_wiring import DEFAULT_SET, load_registry

# ---------------------------------------------------------------------------
# AC-5 — the legacy corpus counts, re-derived at the composed base
# ---------------------------------------------------------------------------

#: detector row -> {function key: occurrences}. Keys absent from a row have
#: zero occurrences of it; the totals are the sum of each row.
LEGACY_COUNTS: dict[str, dict[str, int]] = {
    "sherlock": {
        "acceptance_criteria": 3,
        "evaluation": 4,
        "fire_prep_pass": 6,
        "grooming_pass": 1,
        "iteration_feedback": 1,
        "post_merge_review": 4,
        "remediation_ticket": 1,
        "ticket_create": 2,
        "ticket_review": 4,
        "ticket_revision": 2,
    },
    "watson": {
        "acceptance_criteria": 27,
        "evaluation": 30,
        "fire_prep_pass": 14,
        "grooming_pass": 1,
        "iteration_feedback": 7,
        "post_merge_review": 30,
        "ticket_create": 2,
        "ticket_review": 30,
        "ticket_revision": 2,
    },
    "subagent_type": {
        "acceptance_criteria": 6,
        "evaluation": 6,
        "iteration_feedback": 5,
        "post_merge_review": 6,
        "remediation_ticket": 1,
        "ticket_create": 2,
        "ticket_review": 7,
        "ticket_revision": 2,
    },
    "parallel_dispatch_sentence": {
        "acceptance_criteria": 1,
        "evaluation": 1,
        "post_merge_review": 1,
        "ticket_review": 1,
    },
    "no_defer_rule": {
        "acceptance_criteria": 1,
        "evaluation": 1,
        "iteration_feedback": 1,
        "post_merge_review": 1,
        "ticket_review": 1,
    },
    "be_extremely_thorough": {
        "acceptance_criteria": 5,
        "evaluation": 5,
        "post_merge_review": 5,
        "ticket_review": 4,
    },
    "ultrathink": {
        "acceptance_criteria": 7,
        "criteria_validation": 1,
        "evaluation": 7,
        "post_merge_review": 7,
        "remediation_ticket": 1,
        "ticket_create": 1,
        "ticket_review": 7,
        "ticket_revision": 1,
    },
    "defer_shaming": {
        "acceptance_criteria": 2,
        "evaluation": 4,
        "fire_prep_pass": 2,
        "iteration_feedback": 2,
        "post_merge_review": 4,
        "ticket_review": 4,
    },
    "house_rules": {
        "acceptance_criteria": 1,
        "evaluation": 1,
        "fire_prep_pass": 1,
        "iteration_feedback": 1,
        "post_merge_review": 1,
        "remediation_ticket": 3,
        "ticket_create": 3,
        "ticket_review": 1,
        "ticket_revision": 3,
    },
}

LEGACY_TOTALS: dict[str, int] = {
    row: sum(counts.values()) for row, counts in LEGACY_COUNTS.items()
}

#: The skills rule's legacy footprint: the declared roster's names plus the
#: invocation-condition phrasings, both measured over the same bodies.
LEGACY_SKILL_ENUMERATIONS: dict[str, int] = {"fire_prep_pass": 6, "grooming_pass": 2}


def legacy_bodies() -> dict[str, str]:
    """Every legacy template body, addressed by set name, keyed by function key."""
    registry = load_registry(default_set=DEFAULT_SET)
    return {key.value: registry.template_for(key).body for key in PromptKey}


def declared_skill_names(set_name: str) -> frozenset[str]:
    """Every skill name the named set declares across all of its keys."""
    registry = load_registry(default_set=set_name)
    return frozenset(
        name for key in PromptKey for name in registry.declared_skills(key)
    )


@pytest.mark.parametrize("row", sorted(LEGACY_COUNTS))
def test_detectors_fire_on_legacy_corpus(row: str) -> None:
    """Every counted detector reports the re-derived legacy counts, per key.

    This is the anti-vacuity proof: the object the v5 suite requires to
    report nothing is the same object that reports these.
    """
    detector = COUNTED_DETECTORS[row]
    measured = {
        key: len(detector(body))
        for key, body in legacy_bodies().items()
        if detector(body)
    }
    assert measured == LEGACY_COUNTS[row]
    assert sum(measured.values()) == LEGACY_TOTALS[row]


def test_skill_enumeration_detector_fires_on_legacy_corpus() -> None:
    """The skills rule is grounded too: the legacy corpus enumerates them."""
    detect = skill_enumeration_detector(declared_skill_names(DEFAULT_SET))
    measured = {
        key: len(detect(body)) for key, body in legacy_bodies().items() if detect(body)
    }
    assert measured == LEGACY_SKILL_ENUMERATIONS


def test_every_counted_detector_reports_something_on_the_legacy_corpus() -> None:
    """No row of the table may be zero — a zero row is a vacuous rule."""
    silent = sorted(row for row, total in LEGACY_TOTALS.items() if total == 0)
    assert silent == []


# ---------------------------------------------------------------------------
# AC-4 — the rules, over the new set and over violating fixtures
# ---------------------------------------------------------------------------

ULTRATHINK_FRAGMENT_NAME = "ultrathink_instruction"
ULTRACODE_FRAGMENT_NAME = "ultracode_instruction"
SUPPRESSION_FRAGMENT_NAME = "suppression_proxy"
HOUSE_RULES_FRAGMENT_NAME = "house_rules"
UTILITY_ROSTER_KEY = "utility_keys"

#: The three keys deliverable 5 names as utility. A set's declared roster may
#: be wider — the six keys added after this issue was written are classified
#: at authoring time — but never narrower.
NAMED_UTILITY_KEYS = frozenset(
    {
        PromptKey.BRANCH_NAME.value,
        PromptKey.COMMIT_MESSAGE.value,
        PromptKey.PR_DESCRIPTION.value,
    }
)

V5_SET_DIR = default_sets_root() / V5_SET
V5_ABSENT = not V5_SET_DIR.is_dir()
V5_REASON = f"the {V5_SET} set is authored by KOD-88; nothing to check until it ships"


@dataclass(frozen=True)
class SetContext:
    """Everything a rule needs about the set the template belongs to."""

    fragments: dict[str, str]
    utility_keys: frozenset[str]
    skill_names: frozenset[str]

    def fragment(self, name: str) -> str:
        """A named fragment; absence is a failure, never an empty string."""
        assert name in self.fragments, f"the set declares no {name} fragment"
        return self.fragments[name]


@dataclass(frozen=True)
class Rule:
    """One style rule: how it is checked, and one text that must fail it."""

    describe: str
    violating_fixture: str


def check_rule(name: str, key: str, body: str, context: SetContext) -> tuple[str, ...]:
    """Apply the rule named *name* to one template body."""
    if name == "no_persona_tokens":
        return persona_sherlock(body) + persona_watson(body)
    if name == "no_dispatch_protocol":
        return dispatch_subagent_type(body) + dispatch_parallel_sentence(body)
    if name == "no_defer_shaming":
        return no_defer_rule_header(body) + defer_shaming(body)
    if name == "no_emphasis_inflation":
        return emphasis_inflation(body)
    if name == "ultrathink_is_the_final_block":
        return ultrathink_placement_violations(
            body,
            fragment=context.fragment(ULTRATHINK_FRAGMENT_NAME),
            utility=key in context.utility_keys,
        )
    if name == "ultracode_only_inside_its_fragment":
        return occurrences_outside(
            context.fragment(ULTRACODE_FRAGMENT_NAME),
            ultracode_tokens,
            body,
        )
    if name == "no_skill_enumeration":
        return skill_enumeration_detector(context.skill_names)(body)
    if name == "no_reasoning_reproduction":
        return reasoning_reproduction(body)
    if name == "artifact_tags_are_balanced":
        return unbalanced_artifact_tags(body)
    if name == "one_data_boundary_sentence":
        sentences = data_boundary_sentences(body)
        if artifact_tag_names(body) and len(sentences) != 1:
            return (f"{len(sentences)} boundary sentences, expected exactly 1",)
        if not artifact_tag_names(body) and sentences:
            return (f"{len(sentences)} boundary sentences with no artifact tag",)
        return ()
    if name == "house_rules_live_in_the_append_fragment":
        return house_rules_prose(body)
    raise AssertionError(f"unknown rule {name}")


RULES: dict[str, Rule] = {
    "no_persona_tokens": Rule(
        describe="zero Sherlock/Watson persona tokens",
        violating_fixture="You are Sherlock Holmes; dispatch five Watsons.",
    ),
    "no_dispatch_protocol": Rule(
        describe="zero in-prompt dispatch protocol",
        violating_fixture=(
            "Send a SINGLE message containing five parallel Agent tool calls "
            "with subagent_type=Explore."
        ),
    ),
    "no_defer_shaming": Rule(
        describe="zero defer-shaming blocks",
        violating_fixture="── NO-DEFER RULE ── if you defer this you are being lazy.",
    ),
    "no_emphasis_inflation": Rule(
        describe="zero effort exhortation",
        violating_fixture="Be extremely thorough about every one of these.",
    ),
    "ultrathink_is_the_final_block": Rule(
        describe="ultrathink appears once, as the final block",
        violating_fixture="Ultrathink. Do the work. Ultrathink again at the end.",
    ),
    "ultracode_only_inside_its_fragment": Rule(
        describe="the ultracode token only inside its own fragment",
        violating_fixture="When the work is wide, say ultracode and fan out.",
    ),
    "no_skill_enumeration": Rule(
        describe="no template names a skill or when to invoke it",
        violating_fixture="Load and apply the code-review skill before judging.",
    ),
    "no_reasoning_reproduction": Rule(
        describe="no instruction to emit internal reasoning",
        violating_fixture="Show your work and include your chain of thought.",
    ),
    "artifact_tags_are_balanced": Rule(
        describe="every artifact tag opened is closed",
        violating_fixture="<ticket>\nthe ticket text\n",
    ),
    "one_data_boundary_sentence": Rule(
        describe="exactly one data-not-instructions sentence per artifact template",
        violating_fixture="<ticket>\nticket\n</ticket>\n",
    ),
    "house_rules_live_in_the_append_fragment": Rule(
        describe="the house rules appear in no template body",
        violating_fixture="Comply strictly with SOLID DRY KISS at all times.",
    ),
}

FIXTURE_CONTEXT = SetContext(
    fragments={
        ULTRATHINK_FRAGMENT_NAME: (
            "Ultrathink. Reason as thoroughly as the task warrants."
        ),
        ULTRACODE_FRAGMENT_NAME: "Ultracode. This work opts into orchestration.",
        SUPPRESSION_FRAGMENT_NAME: (
            "The linter-never-disabled policy is checked by grep."
        ),
        HOUSE_RULES_FRAGMENT_NAME: "kodezart house rules: SOLID, DRY, KISS.",
    },
    utility_keys=NAMED_UTILITY_KEYS,
    skill_names=frozenset({"code-review", "security-review"}),
)


@pytest.mark.parametrize("rule_name", sorted(RULES))
def test_each_rule_fails_a_deliberately_violating_fixture(rule_name: str) -> None:
    """A rule that passes its own violation is a rule that checks nothing."""
    fixture = RULES[rule_name].violating_fixture
    found = check_rule(rule_name, PromptKey.EVALUATION.value, fixture, FIXTURE_CONTEXT)
    assert found, f"rule {rule_name} did not fire on its violating fixture"


def test_every_deliverable_five_rule_has_a_negative_case() -> None:
    """The rule roster and the fixture roster are the same roster."""
    assert len(RULES) == 11


# ---------------------------------------------------------------------------
# The new set's arm — live from the commit that authors the set
# ---------------------------------------------------------------------------


def v5_metadata() -> dict[str, object]:
    """The new set's raw metadata, read as data."""
    raw = (V5_SET_DIR / "set.toml").read_text(encoding="utf-8")
    return tomllib.loads(raw)


def v5_context() -> SetContext:
    """The new set's fragments, utility roster and declared skill roster."""
    metadata = v5_metadata()
    fragments = metadata.get("fragments", {})
    assert isinstance(fragments, dict)
    roster = metadata.get(UTILITY_ROSTER_KEY)
    assert isinstance(roster, list), (
        f"the {V5_SET} set must declare {UTILITY_ROSTER_KEY}: which roles carry "
        "no reasoning-depth instruction is a property of the set, not a guess"
    )
    utility = frozenset(str(name) for name in roster)
    assert NAMED_UTILITY_KEYS <= utility
    return SetContext(
        fragments={str(k): str(v) for k, v in fragments.items()},
        utility_keys=utility,
        skill_names=declared_skill_names(V5_SET),
    )


def v5_bodies() -> dict[str, str]:
    """Every template body of the new set, keyed by function key."""
    if V5_ABSENT:
        return {}
    registry = load_registry(default_set=V5_SET)
    return {key.value: registry.template_for(key).body for key in PromptKey}


V5_CASES = [
    (rule_name, key) for rule_name in sorted(RULES) for key in sorted(v5_bodies())
] or [pytest.param("", "", marks=pytest.mark.skip(reason=V5_REASON))]


@pytest.mark.parametrize(("rule_name", "key"), V5_CASES)
def test_rule_holds_over_every_v5_template(rule_name: str, key: str) -> None:
    """Each rule, over each template of the new set."""
    body = v5_bodies()[key]
    found = check_rule(rule_name, key, body, v5_context())
    assert found == (), f"{key} violates {RULES[rule_name].describe}: {found}"


@pytest.mark.skipif(V5_ABSENT, reason=V5_REASON)
def test_suppression_proxy_is_defined_once_and_renders_into_three_templates() -> None:
    """One fragment source, exactly three consumers."""
    context = v5_context()
    proxy = context.fragment(SUPPRESSION_FRAGMENT_NAME)
    consumers = sorted(key for key, body in v5_bodies().items() if proxy in body)
    assert len(consumers) == 3, f"suppression_proxy renders into {consumers}"


@pytest.mark.skipif(V5_ABSENT, reason=V5_REASON)
def test_the_v5_arm_covers_every_registered_key() -> None:
    """Non-vacuity: once the set exists, no key escapes the style rules."""
    assert set(v5_bodies()) == {key.value for key in PromptKey}
