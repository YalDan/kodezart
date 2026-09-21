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
    """
    first_line = fragment(FRAGMENT_NAME).splitlines()[0]
    carriers = [body for body in member_files(V5_SET) if first_line in body]
    assert carriers == []


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
    """The set no deployment dispatches stays exactly as it was (KOD-306)."""
    metadata = tomllib.loads(
        (default_sets_root() / OPUS_SET / "set.toml").read_text(encoding="utf-8"),
    )
    fragments = metadata["fragments"]
    assert isinstance(fragments, dict)
    assert FRAGMENT_NAME not in fragments
    assert [body for body in member_files(OPUS_SET) if SENTENCES[0] in body] == []
