"""KOD-784 — the board's shape, stated once and carried by four members.

The standard is a text claim about placement: one tree, deliverables and
criteria as sub-issues, placement through tracker fields, and a missing
container flagged rather than invented.  It is declared once in the set and
composed into the groom judge, the repair author and the two scheduled
passes, so the four roles that read or write placement cannot disagree
about it.

The four sentences are written out here rather than derived from the
fragment: a test that reads its own expectation out of the text it guards
stays green when a sentence is dropped.
"""

import tomllib

import pytest

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from kodezart.types.domain.organize import RefusalKind
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.sets import OPUS_SET, V5_SET, render_v5_case, v5_registry
from tests.prompts.test_v5_fragments import (
    fragment,
    member_files_carrying,
    prose,
    v5_bodies,
)

FRAGMENT_NAME = "board_hierarchy"

#: The roles that read or write placement: the judge whose verdict is the
#: only door to a placement repair, the author that proposes one, and the
#: two scheduled passes that groom a board no organize tick walks.
CARRIERS = frozenset(
    {
        PromptKey.GROOMING_PASS.value,
        PromptKey.FIRE_PREP_PASS.value,
        PromptKey.ORGANIZE_ASSESS.value,
        PromptKey.ORGANIZE_AUTHOR.value,
    },
)

#: The standard, sentence by sentence, each newline-free so no assertion
#: here depends on how the declaration is wrapped.
SENTENCES = (
    "Keep one tree: initiative → project → milestone → parent issue → sub-issue.",
    "Deliverables and criteria are sub-issues.",
    "Place with tracker fields, not prose.",
    "Missing project or milestone: flag it, don't invent it.",
)

#: The sentence the grooming pass no longer carries: grooming re-places what
#: the board misplaces, so declaring it no reorganisation contradicts the
#: standard it now states.
RETIRED_CLAIM = "not a reorganisation"

#: The two refusal kinds, spelled by the roster the routing switch reads
#: rather than typed a second time here: the words below are load-bearing
#: because ``domain/organize.py`` routes on them, so they are taken from
#: the declaration and not from a copy of it.
SPEC_GAP = RefusalKind.SPEC_GAP.value
HUMAN_DECISION = RefusalKind.HUMAN_DECISION.value

#: The sentence that tells the judge how to CLASSIFY a refusal at all.
#: Pinned whole, because reporting every repairable gap AS a human decision
#: is a rewrite of this one sentence that routes every refusal to ESCALATE —
#: strictly more than a misplacement misrouted, and nothing else states it.
CLASSIFYING_SENTENCE = (
    "A not_buildable result names the invented decision and distinguishes a "
    f"repairable {SPEC_GAP} from a {HUMAN_DECISION}."
)

#: The sentence that APPLIES that classification to a misplacement, up to
#: its terminator. The colon is part of the pin: without it the sentence is
#: only required to START this way, and an appended alternative — "or a
#: human_decision, whichever you judge it to be" — leaves a containment
#: check satisfied while licensing the halt this criterion exists to
#: prevent.
MISPLACEMENT_SENTENCE = (
    f"An issue outside that tree is not_buildable with a repairable {SPEC_GAP}:"
)


def member_files(set_name: str) -> list[str]:
    """Every member file of a shipped set, read as text."""
    members = sorted((default_sets_root() / set_name).glob("*.md"))
    assert members
    return [path.read_text(encoding="utf-8") for path in members]


def lens_prompts() -> dict[str, str]:
    """Every declared lens prompt of the new set, keyed by lens name.

    A lens body resolves the set's fragments the way a member body does
    and ships as an agent definition, so it is a composed prompt the
    standard can reach and no function key names it.
    """
    declared = v5_registry().definitions()
    assert declared
    return {definition.name: definition.prompt for definition in declared}


def test_the_board_hierarchy_is_declared_exactly_once() -> None:
    """One source: no member FILE states the standard for itself.

    Counted over the files rather than the resolved bodies, because
    resolution is what puts the text into a body — a member carrying it
    verbatim would be the second copy the fragment exists to prevent.

    Every sentence is looked for, not the first line alone: a member that
    restated the last three sentences without the first would be a second,
    drifting copy that a first-line scan reports as nothing.

    Scanned with the set's own file scan, which walks the whole sets root:
    a lens body under `definitions/` is composed through the same fragment
    seam as a member and shipped as an agent definition, so it is a file
    the standard can be pasted into, and a directory-level glob never
    looks there. The scan has its own reach control in the fragment
    suite. Each carrier is reported with the sentence that found it.
    """
    carriers = {
        sentence: found
        for sentence in SENTENCES
        if (found := member_files_carrying(sentence))
    }
    assert carriers == {}


def test_the_board_hierarchy_resolves_into_exactly_its_four_carriers() -> None:
    """Countable carriers: the four roles that read or write placement.

    Counted over the composed lens bodies too, not the function keys
    alone. A lens declared by the set resolves its fragments the same way
    and is dispatched as an agent definition, so a `{{board_hierarchy}}`
    placed in a lens body renders the standard into a fifth composed
    prompt that a PromptKey census cannot see. No lens carries it.
    """
    standard = fragment(FRAGMENT_NAME)
    carriers = {key for key, body in v5_bodies().items() if standard in body}
    lenses = {name for name, body in lens_prompts().items() if standard in body}
    assert carriers == CARRIERS
    assert lenses == set()


@pytest.mark.parametrize("sentence", SENTENCES)
@pytest.mark.parametrize("carrier", sorted(CARRIERS))
def test_every_hierarchy_sentence_reaches_every_carrier(
    carrier: str,
    sentence: str,
) -> None:
    """Every sentence survives composition into every carrier's render."""
    assert sentence in render_v5_case(carrier)


def test_the_judge_names_misplacement_as_a_repairable_gap() -> None:
    """The judge's door: a buildable verdict with no findings skips the author.

    Without this sentence a misplaced but implementable issue is marked
    complete and no repair ever runs.

    The refusal kind is asserted with the verdict, because the two words
    after it are the whole difference between a repair and a halt: a
    human_decision refusal routes to ESCALATE and every other
    ``not_buildable`` result to REAUTHOR (``domain/organize.py``), so a
    misplacement classed as a human decision stops the stage instead of
    reaching the author. Read off the prose, so rewrapping the member is
    not a change to what it says.

    Both sentences that decide that route are pinned, and the applying one
    is pinned CLOSED. A sentence required only to be CONTAINED can be
    extended: offering the judge the other refusal kind as an alternative
    leaves the words up to the gap in place and hands back the halt. So the
    pin runs to the terminator, and the other kind is required to be
    mentioned exactly once in the whole rendered member — in the sentence
    that keeps the two kinds apart, which is asserted here beside it,
    because collapsing THAT sentence routes every refusal to ESCALATE and
    no other member states it.
    """
    rendered = prose(render_v5_case(PromptKey.ORGANIZE_ASSESS.value))
    assert MISPLACEMENT_SENTENCE in rendered
    assert "name the misplacement and the field that carries it" in rendered
    assert CLASSIFYING_SENTENCE in rendered
    assert rendered.count(HUMAN_DECISION) == 1


def test_grooming_is_no_longer_declared_a_non_reorganisation() -> None:
    """The retired claim is gone from the files and from every render."""
    assert [body for body in member_files(V5_SET) if RETIRED_CLAIM in body] == []
    assert [key for key, body in v5_bodies().items() if RETIRED_CLAIM in body] == []


def test_the_board_hierarchy_binds_no_operation_namespace() -> None:
    """A binding inside the standard would render only where it is declared.

    The prompt suite binds the example operation, which declares every
    namespace; a deployment that declares fewer would then fail at its
    first session render rather than here.  So the standard names none.
    """
    assert "{{" not in fragment(FRAGMENT_NAME)


def test_the_legacy_set_declares_no_board_hierarchy() -> None:
    """The set no deployment dispatches stays exactly as it was (KOD-306).

    Both halves are about the legacy corpus: the key is absent from its
    metadata, and no sentence of the standard appears anywhere under its
    directory — members and metadata alike. The second half is stated over
    the whole directory on purpose. Over the member files alone it would
    hold of the new set as well, where the text lives in set.toml and in no
    member file either, and an assertion true of both sets tells them
    apart not at all; over the directory it is true here and false there.
    """
    legacy_root = default_sets_root() / OPUS_SET
    metadata = tomllib.loads(
        (legacy_root / "set.toml").read_text(encoding="utf-8"),
    )
    fragments = metadata["fragments"]
    assert isinstance(fragments, dict)
    assert FRAGMENT_NAME not in fragments

    legacy_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(legacy_root.iterdir())
        if path.is_file()
    )
    assert [sentence for sentence in SENTENCES if sentence in legacy_text] == []
