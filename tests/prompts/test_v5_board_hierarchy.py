"""KOD-784 — the board's shape, stated once and carried by four members.

The standard is a text claim about placement: one tree, deliverables and
criteria as sub-issues, placement through tracker fields, and a missing
container flagged rather than invented.  It is declared once in the set and
composed into the groom judge, the repair author and the two scheduled
passes, so the four roles that read or write placement cannot disagree
about it.

The fragment also sizes work for quick wins (KOD-904): a parent issue is
finished and shipped on its own, soon; nothing is split finer than one
coherent change; a rare edge case becomes its own backlog issue.  Those five
sentences reach every carrier with the rest, and the two scheduled passes
are asserted to carry them.

Every sentence is written out here rather than derived from the fragment:
a test that reads its own expectation out of the text it guards stays
green when a sentence is dropped.  The whole fragment is pinned as well, so
a sentence added, reordered or reworded fails here first.
"""

import tomllib

import pytest

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.sets import OPUS_SET, V5_SET, render_v5_case
from tests.prompts.test_v5_fragments import fragment, v5_bodies

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

#: The quick-win sizing sentences (KOD-904), in declaration order.  Kept
#: apart from the hierarchy sentences because the legacy fire prep states
#: one of them word for word; the legacy set carries no hierarchy sentence,
#: and that claim stays about the hierarchy alone.
QUICK_WIN_SENTENCES = (
    "Size for quick wins: each parent issue can be finished and shipped on its"
    " own, soon, and shows progress when it lands.",
    "Split a parent issue that is larger; flag a project or milestone that is"
    " larger, with the split you propose.",
    "Don't split finer than one coherent change that is useful by itself.",
    "A rare or improbable edge case found along the way is its own backlog"
    " issue for cleanup, not added scope.",
    "An issue's finish line stays its stated criteria.",
)

#: The fragment's whole text: one sentence per line, the hierarchy first.
WHOLE_FRAGMENT = "\n".join(SENTENCES + QUICK_WIN_SENTENCES)

#: The two scheduled passes: the ones that hand work over to be built.
SIZING_PASSES = (PromptKey.GROOMING_PASS.value, PromptKey.FIRE_PREP_PASS.value)

#: The sentence the grooming pass no longer carries: grooming re-places what
#: the board misplaces, so declaring it no reorganisation contradicts the
#: standard it now states.
RETIRED_CLAIM = "not a reorganisation"


def member_files(set_name: str) -> list[str]:
    """Every member file of a shipped set, read as text."""
    members = sorted((default_sets_root() / set_name).glob("*.md"))
    assert members
    return [path.read_text(encoding="utf-8") for path in members]


def test_the_board_hierarchy_is_declared_exactly_once() -> None:
    """One source: no member FILE states the standard for itself.

    Counted over the files rather than the resolved bodies, because
    resolution is what puts the text into a body — a member carrying it
    verbatim would be the second copy the fragment exists to prevent.

    Every sentence is looked for, not the first line alone: a member that
    restated the last three sentences without the first would be a second,
    drifting copy that a first-line scan reports as nothing.
    """
    carriers = [
        body
        for body in member_files(V5_SET)
        if any(sentence in body for sentence in SENTENCES + QUICK_WIN_SENTENCES)
    ]
    assert carriers == []


def test_the_board_hierarchy_is_pinned_whole() -> None:
    """The declaration is exactly the hierarchy then the quick-win sizing."""
    assert fragment(FRAGMENT_NAME) == WHOLE_FRAGMENT


def test_the_board_hierarchy_resolves_into_exactly_its_four_carriers() -> None:
    """Countable carriers: the four roles that read or write placement."""
    standard = fragment(FRAGMENT_NAME)
    carriers = {key for key, body in v5_bodies().items() if standard in body}
    assert carriers == CARRIERS


@pytest.mark.parametrize("sentence", SENTENCES)
@pytest.mark.parametrize("carrier", sorted(CARRIERS))
def test_every_hierarchy_sentence_reaches_every_carrier(
    carrier: str,
    sentence: str,
) -> None:
    """Every sentence survives composition into every carrier's render."""
    assert sentence in render_v5_case(carrier)


@pytest.mark.parametrize("sentence", QUICK_WIN_SENTENCES)
@pytest.mark.parametrize("carrier", SIZING_PASSES)
def test_both_scheduled_passes_size_work_for_quick_wins(
    carrier: str,
    sentence: str,
) -> None:
    """Grooming and fire prep each render every quick-win sentence (KOD-904)."""
    assert sentence in render_v5_case(carrier)


def test_the_judge_names_misplacement_as_a_repairable_gap() -> None:
    """The judge's door: a buildable verdict with no findings skips the author.

    Without this sentence a misplaced but implementable issue is marked
    complete and no repair ever runs.
    """
    rendered = render_v5_case(PromptKey.ORGANIZE_ASSESS.value)
    assert "An issue outside that tree is not_buildable" in rendered
    assert "name the misplacement and the field that carries it" in rendered


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
