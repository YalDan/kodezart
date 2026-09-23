"""KOD-904 — the legacy set's grooming and fire prep size work for quick wins.

The claude-opus set has no shared fragment, so each pass states the rule in
its own principles: fire prep at the end of its first principle, grooming at
the end of principle 3.  Each paragraph is written out here whole, and each
pass's backlog sentence is asserted on its own, so a rare edge case keeps
its route to a backlog issue even if the sizing wording around it moves.
"""

import pytest

from kodezart.types.domain.prompts import PromptKey
from tests.prompts.sets import operation_registry, render_case

FIRE_PREP_PARAGRAPH = (
    "Size for quick wins as well as coherence: a fire, and each slice of an"
    " epic, is something kodezart can finish and ship soon, so every one shows"
    " progress when it lands; an epic too large to finish in such steps is"
    " split into milestones or smaller epics. Don't split finer than one"
    " coherent change that is useful by itself. A rare or improbable edge case"
    " found while preparing is its own backlog issue for cleanup, never added"
    " to the fire's scope: a fire's finish line is its stated criteria."
)

GROOMING_PARAGRAPH = (
    "Size what you hand over for quick wins: each parent issue can be finished"
    " and shipped soon and shows progress when it lands; split one that is"
    " larger, and never finer than one coherent change that is useful by"
    " itself. A rare or improbable edge case you find is its own backlog issue"
    " for cleanup, not added scope on the issue you are grooming."
)

#: pass -> (its appended paragraph, the backlog sentence inside it).
APPENDED: dict[str, tuple[str, str]] = {
    PromptKey.FIRE_PREP_PASS.value: (
        FIRE_PREP_PARAGRAPH,
        "A rare or improbable edge case found while preparing is its own"
        " backlog issue for cleanup, never added to the fire's scope: a fire's"
        " finish line is its stated criteria.",
    ),
    PromptKey.GROOMING_PASS.value: (
        GROOMING_PARAGRAPH,
        "A rare or improbable edge case you find is its own backlog issue for"
        " cleanup, not added scope on the issue you are grooming.",
    ),
}


@pytest.mark.parametrize("case", sorted(APPENDED))
def test_each_pass_renders_its_quick_win_paragraph_whole(case: str) -> None:
    """The appended paragraph reaches the render as one unbroken run of text."""
    paragraph, _ = APPENDED[case]
    assert paragraph in render_case(operation_registry(), case)


@pytest.mark.parametrize("case", sorted(APPENDED))
def test_each_pass_sends_a_rare_edge_case_to_the_backlog(case: str) -> None:
    """The backlog sentence is carried whatever the sizing wording around it."""
    _, backlog = APPENDED[case]
    assert backlog in render_case(operation_registry(), case)
