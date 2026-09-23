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
        PromptKey.MUTATION_SURVIVAL.value,
        PromptKey.ORGANIZE_GROOM_RUBRIC.value,
        PromptKey.ORGANIZE_SPEC_RUBRIC.value,
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
#:
#: Whole clauses, not their tails: the scope the instruction applies over,
#: the premise the named shape has to satisfy and the condition that earns a
#: failed verdict each carry their own case, because a pin that begins after
#: one of them stays green while that part of the sentence is rewritten.
DESIGN_REVIEW_CLAUSES: tuple[str, ...] = (
    "For every changed file",
    "try to refute that the change meets the engineering standard in your house rules",
    "name the simpler shape that would have satisfied the criterion; "
    "that simpler shape is the refutation",
    "When you can name a violation with file:line and the principle breached, fail",
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
#: them. Imported by the loop suite, which asserts the same sentences reach
#: the writer and the grader, so the readings have one source too.
ENGINEERING_READINGS: tuple[str, ...] = (
    "SOLID, DRY, hexagonal, and KISS as the way to get there.",
    "One adapter package per vendor, the vendor's wire shapes inside it, and no "
    "judgement in an adapter.",
    "Judgement is a prompt and arithmetic is a plain function.",
    "A new vendor, a new pass or a new backend is a new module, not a new "
    "branch in an existing one.",
    "A test double answers its port's questions the way the real implementation "
    "does; a double that accepts what the real one refuses, or whose signature "
    "differs from the port it stands for, is a defect and not a convenience.",
    "Ports are narrow role protocols named for their consumer, and a consumer "
    "depends on the smallest role it needs; a port or an adapter that grows "
    "members for many consumers is a finding.",
    "The application defines its ports and the adapters implement them; nothing "
    "in the domain or the workflow imports an adapter or a vendor package.",
    "Every rule, name and derivation has one source that everything else reads; "
    "a second copy of a constant, a hand-kept list that mirrors a type, or a "
    "check that lists by hand what it could derive from the code is a finding.",
    "The domain and the workflow sit in the centre and reach the outside world "
    "only through ports; the tracker, the forge, git and model sessions are "
    "adapters at the edge, chosen once at the composition root and never by a "
    "branch at runtime.",
    "Typed errors before any backend call.",
    "The smallest change that satisfies the criterion: delete rather than carry, "
    "and no abstraction, parameter or file beyond what the task requires.",
)

#: The principles the standard names. The count lives here and nowhere else:
#: SOLID's five, plus DRY, hexagonal and KISS, is the eight the standard has
#: to name for the refutation to be able to report "the principle breached".
#: Matched case-insensitively, because a name that opens a sentence is
#: capitalised and the same name mid-sentence is not.
ENGINEERING_PRINCIPLES: tuple[str, ...] = (
    "single responsibility",
    "open/closed",
    "Liskov substitution",
    "interface segregation",
    "dependency inversion",
    "DRY",
    "hexagonal",
    "KISS",
)

#: The names the standard attaches to a reading, as it attaches them: every
#: one of the eight, each in front of its own reading, so the set is derived
#: from the tuple above rather than kept beside it. A name that opens a
#: sentence is capitalised; the tuple keeps the lowercase forms the
#: case-insensitive naming check reads.
ENGINEERING_PRINCIPLE_LABELS: frozenset[str] = frozenset(
    principle[0].upper() + principle[1:] for principle in ENGINEERING_PRINCIPLES
)

#: What the standard puts between a principle's name and its reading.
DASH = " — "

#: Exact. Every paragraph of the append OUTSIDE the standard's own, in the
#: order the fragment states them, each as prose — measured, not assumed:
#: the append's heading and the three paragraphs after the standard. Pinned
#: by their whole text, so a sentence added to any of them, or a paragraph
#: added between them, is a change here whatever it is spelled with.
OUTSIDE_THE_STANDARD: tuple[str, ...] = (
    "kodezart house rules:",
    "Hard prohibitions: no mocked or hardcoded values outside tests, no silent "
    "fallbacks, no backwards-compatibility shims, no fabricated success signals "
    "(returning OK without doing the work, swallowing errors, catching "
    "exceptions to pretend nothing happened). The linter and type checker are "
    "never disabled or suppressed.",
    "Cross-component conventions (naming patterns, branch formats, message "
    "schemas) belong in typed domain models, never in string literals or "
    "implicit contracts between files.",
    "Act as soon as you have enough information. Your turn is complete only "
    "when you have produced the required structured output — if you notice "
    "yourself ending with a plan or a promise about work not yet done, do that "
    "work now.",
)

#: The opening words of the standard's own paragraph.
STANDARD_OPENING = "Engineering standard:"

#: A ninth principle, planted into the shipped append in each ordinary
#: placement and spelling, as ``anchor -> planted``. The first group lands
#: outside the standard's paragraph — adjacent behind a lead-in line, one
#: paragraph further out, inside an existing paragraph, and as a second
#: paragraph opening like the standard — with an em dash (spaced and not),
#: an en dash, a hyphen or a colon between the name and its reading. The
#: second group lands inside the standard's own paragraph.
NINTH_OUTSIDE: dict[str, tuple[str, str]] = {
    "adjacent_on_a_later_line": (
        "task requires.\n\nHard prohibitions:",
        "task requires.\n\nAlso required of every change:\nYAGNI — Build nothing "
        "until it is asked for.\n\nHard prohibitions:",
    ),
    "adjacent_unspaced_em_dash": (
        "task requires.\n\nHard prohibitions:",
        "task requires.\n\nAlso required of every change:\nYAGNI—Build nothing "
        "until it is asked for.\n\nHard prohibitions:",
    ),
    "further_out_em_dash": (
        "\nCross-component conventions (naming patterns,",
        "\nYAGNI — Build nothing until it is asked for.\n\nCross-component "
        "conventions (naming patterns,",
    ),
    "further_out_colon": (
        "\nCross-component conventions (naming patterns,",
        "\nYAGNI: Build nothing until it is asked for.\n\nCross-component "
        "conventions (naming patterns,",
    ),
    "further_out_en_dash": (
        "\nCross-component conventions (naming patterns,",
        "\nYAGNI \N{EN DASH} Build nothing until it is asked for.\n\nCross-component "
        "conventions (naming patterns,",
    ),
    "further_out_hyphen": (
        "\nCross-component conventions (naming patterns,",
        "\nYAGNI - Build nothing until it is asked for.\n\nCross-component "
        "conventions (naming patterns,",
    ),
    "inside_an_outside_paragraph": (
        "implicit contracts between files.",
        "implicit contracts between files. YAGNI: build nothing until it is\n"
        "asked for.",
    ),
    "behind_the_closing_clause": (
        "do that work now.",
        "do that work now. YAGNI — build nothing until it is asked for.",
    ),
    "second_standard_paragraph": (
        "\nCross-component conventions (naming patterns,",
        "\nEngineering standard: YAGNI — Build nothing until it is asked for."
        "\n\nCross-component conventions (naming patterns,",
    ),
}
NINTH_INSIDE: dict[str, tuple[str, str]] = {
    "standard_colon": (
        "task requires.\n",
        "task requires. YAGNI: Build nothing until it is asked for.\n",
    ),
    "standard_en_dash": (
        "task requires.\n",
        "task requires. YAGNI \N{EN DASH} Build nothing until it is asked for.\n",
    ),
    "standard_unspaced_em_dash": (
        "task requires.\n",
        "task requires. YAGNI—Build nothing until it is asked for.\n",
    ),
}


def standard_paragraphs(rules: str) -> list[str]:
    """Every paragraph of *rules* that opens as the standard does, as prose."""
    return [
        prose(block)
        for block in rules.split("\n\n")
        if block.startswith(STANDARD_OPENING)
    ]


def engineering_standard() -> str:
    """The standard's own paragraph of the append, as one line of prose."""
    paragraphs = standard_paragraphs(fragment("house_rules"))
    assert len(paragraphs) == 1, "the append states its standard in one paragraph"
    return paragraphs[0]


def outside_the_standard(rules: str) -> tuple[str, ...]:
    """Every paragraph of *rules* but the standard's, in order, each as prose.

    Whole paragraphs, compared by their text: nothing here recognises a
    principle, so nothing here depends on how one is spelled. Blank
    paragraphs are dropped, so an extra blank line is not a change.
    """
    return tuple(
        prose(block)
        for block in rules.split("\n\n")
        if block.strip() and not block.startswith(STANDARD_OPENING)
    )


def sentences(text: str) -> tuple[str, ...]:
    """*text*'s sentences, each terminated, blank pieces dropped."""
    return tuple(
        piece if piece.endswith(".") else f"{piece}."
        for piece in (part.strip() for part in text.split(". "))
        if piece
    )


def unread_sentences(standard: str) -> tuple[str, ...]:
    """Every sentence of the standard's paragraph that is no registered reading.

    Its label, when it has one, is taken off first, so a sentence is refused
    unless what it says is one of the readings: a ninth principle is refused
    here whatever stands between its name and its reading.
    """
    body = standard.removeprefix(f"{STANDARD_OPENING} ")
    return tuple(
        sentence
        for sentence in sentences(body)
        if sentence.split(DASH, 1)[-1] not in ENGINEERING_READINGS
    )


def principle_labels(paragraph: str) -> frozenset[str]:
    """Every name *paragraph* puts in front of a reading, one per sentence."""
    return frozenset(
        sentence.split(DASH)[0]
        for sentence in paragraph.split(". ")
        if DASH in sentence
    )


@pytest.mark.parametrize("principle", ENGINEERING_PRINCIPLES)
def test_the_engineering_standard_names_each_of_its_eight_principles(
    principle: str,
) -> None:
    """Named one by one, so dropping any one name reds its own case.

    The refutation fragment has the session put "the principle breached" in
    its reasoning; a principle the standard never names cannot be reported
    under that instruction, and open/closed and Liskov substitution were the
    two it did not name.
    """
    assert principle.lower() in engineering_standard().lower()


def test_the_engineering_standard_names_eight_principles_and_no_ninth() -> None:
    """The count, asserted over the text rather than described beside it.

    Four halves, because each holds while the others are broken: every
    declared name is found, so an eighth cannot be dropped silently; the names
    put in front of a reading are exactly the declared labels, so a ninth
    cannot arrive as a label; the paragraph is CLOSED, so a ninth cannot
    arrive as a sentence of it; and every OTHER paragraph of the append is
    pinned by its whole text, so a ninth cannot arrive anywhere else in the
    text every session carries. A ninth smuggled into the opening sentence's
    list instead breaks that sentence's own reading pin.

    The closure halves are the ones that need saying, and neither recognises
    a principle by how it is written. Counting the declared names found in
    the text detects a name going missing and never a name arriving: the
    list it counts is this module's own. So the standard's paragraph is
    closed by requiring every sentence, its label taken off, to be one of
    the registered readings — whatever separates a name from its reading,
    a sentence that is no reading is refused. And every other paragraph is
    compared to the register of the shipped paragraphs by equality, so any
    sentence added to one of them, and any paragraph added between them,
    reds whatever it spells. A second paragraph opening as the standard does
    reds the one-paragraph assertion. Each closure carries its own control
    below, over the planted placements and spellings.

    The append is the reach claimed. The ``design_review`` fragment names
    the principles again in a parenthesis; it is composed into the two
    changeset graders rather than carried by every session, and its own
    clause tests pin it (KOD-883), not this one.
    """
    rules = fragment("house_rules")
    paragraph = engineering_standard()
    named = [
        principle
        for principle in ENGINEERING_PRINCIPLES
        if principle.lower() in paragraph.lower()
    ]
    assert len(named) == 8
    assert principle_labels(paragraph) == ENGINEERING_PRINCIPLE_LABELS
    assert sentences(paragraph.removeprefix(f"{STANDARD_OPENING} "))
    assert unread_sentences(paragraph) == ()
    assert outside_the_standard(rules) == OUTSIDE_THE_STANDARD


def test_a_ninth_principle_is_refused_in_every_placement_and_spelling() -> None:
    """The control for both closures, over the planted append.

    Each case is planted into the shipped fragment and read through the same
    functions the count test reads. Outside the standard, the register of
    whole paragraphs moves while the standard's paragraph stays closed, so
    the paragraph register is what catches it — and a second paragraph
    opening as the standard does is caught by counting those paragraphs.
    Inside the standard, the reading closure refuses the sentence while the
    register stays equal. The separators differ from case to case, so a
    check that recognised a principle by one of them would fail this test.
    """
    rules = fragment("house_rules")
    assert NINTH_OUTSIDE
    assert NINTH_INSIDE
    for case, (anchor, planted) in NINTH_OUTSIDE.items():
        assert rules.count(anchor) == 1, case
        mutated = rules.replace(anchor, planted)
        if case == "second_standard_paragraph":
            assert len(standard_paragraphs(mutated)) == 2, case
            assert outside_the_standard(mutated) == OUTSIDE_THE_STANDARD, case
            continue
        assert standard_paragraphs(mutated) == [engineering_standard()], case
        assert outside_the_standard(mutated) != OUTSIDE_THE_STANDARD, case
    for case, (anchor, planted) in NINTH_INSIDE.items():
        assert rules.count(anchor) == 1, case
        mutated = rules.replace(anchor, planted)
        (standard,) = standard_paragraphs(mutated)
        assert unread_sentences(standard) != (), case
        assert principle_labels(standard) == ENGINEERING_PRINCIPLE_LABELS, case
        assert outside_the_standard(mutated) == OUTSIDE_THE_STANDARD, case


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
