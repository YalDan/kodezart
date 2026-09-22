"""Each organize row's accept conditions are its rubric's, and nothing else's.

The wrappers state how to judge; what counts as accepted is the row's own
rubric, rendered into the per-call ``mandate_rubric`` binding. That is what
lets the pre-approval row be accepted on an organizational predicate while the
run-stage rows keep the implementation test, with one verify role for all of
them.

Read off the shipped operation file and rendered through the registry the way
the owner renders it, so a row repointed at another role reddens here.
"""

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.domain.organize import stage_rows
from kodezart.types.domain.organize import MandateKind
from kodezart.types.domain.prompts import PromptKey
from tests.integration.test_scope_deployment import SCOPE_EXAMPLE
from tests.prompts.sets import OPUS_SET, ORGANIZE_CASE, V5_SET
from tests.prompts.test_prompt_wiring import load_registry

SETS = [OPUS_SET, V5_SET]

#: The four parts of the organizational predicate, each by a phrase the rubric
#: states it in and no line wraps. A part reworded away is a part nothing asks
#: for.
FOUR_PARTS = (
    "blocking edge",
    "accountable",
    "Target dates are ordered",
    "at least one criterion",
)

#: What a pre-approval accept condition may not say. Every one of these was in
#: the two shared wrappers before the rubric roles existed.
IMPLEMENTATION_TEST = (
    "dry implementation",
    "grading demonstration",
    "demonstrated in the declared grading environment",
    "implemented from its own specification",
)


def row_of(kind: MandateKind):
    """The shipped file's row for *kind*, whichever side of approval it runs."""
    rows = load_operation_config(SCOPE_EXAMPLE).resolve_organize_mandates()
    return next(row for row in rows if row.spec.kind is kind)


def rendered_rubric(set_name: str, key: PromptKey) -> str:
    """One rubric, rendered as the owner renders it into a request."""
    return load_registry(default_set=set_name).template_for(key).render(ORGANIZE_CASE)


def rendered_around(set_name: str, wrapper: PromptKey, rubric: str) -> str:
    """One judging wrapper, rendered with *rubric* in its rubric binding."""
    registry = load_registry(default_set=set_name)
    return registry.template_for(wrapper).render(
        {**ORGANIZE_CASE, "mandate_rubric": rubric}
    )


@pytest.mark.parametrize("set_name", SETS)
def test_the_shipped_pre_approval_rubric_states_the_four_parts(set_name: str) -> None:
    """The pre-approval row's accept condition is the organizational predicate."""
    rows = stage_rows(
        load_operation_config(SCOPE_EXAMPLE).resolve_organize_mandates(),
        under_approval=False,
    )
    assert len(rows) == 1
    rubric = rendered_rubric(set_name, rows[0].spec.rubric_prompt_key)
    for part in FOUR_PARTS:
        assert part in rubric, part
    assert "{{" not in rubric


@pytest.mark.parametrize("set_name", SETS)
def test_no_pre_approval_accept_condition_names_the_dry_implementation(
    set_name: str,
) -> None:
    """Buildability is the later rows' test, and it is stated nowhere here.

    Both wrappers are checked, because the verify role is fixed for every row:
    a sentence left in either of them would be an accept condition this
    mandate never agreed to.
    """
    groom = row_of(MandateKind.GROOM)
    rubric = rendered_rubric(set_name, groom.spec.rubric_prompt_key)
    for wrapper in (groom.spec.admission_prompt_key, PromptKey.ORGANIZE_VERIFY):
        rendered = rendered_around(set_name, wrapper, rubric)
        for claim in IMPLEMENTATION_TEST:
            assert claim not in rendered, (wrapper.value, claim)


@pytest.mark.parametrize("set_name", SETS)
def test_the_run_stage_rubric_states_the_implementation_test(set_name: str) -> None:
    """The control for the pin above: the same wrappers, the other rubric.

    The sentences did not disappear from the corpus; they moved to the rows
    whose mandate is buildability, and the same rendering finds them there.
    """
    ticket = row_of(MandateKind.TICKET)
    rubric = rendered_rubric(set_name, ticket.spec.rubric_prompt_key)
    rendered = rendered_around(set_name, ticket.spec.admission_prompt_key, rubric)
    for claim in IMPLEMENTATION_TEST:
        assert claim in rendered, claim
