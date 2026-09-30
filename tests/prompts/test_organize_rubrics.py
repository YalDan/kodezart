"""Each organize row's accept conditions are its rubric's, and nothing else's.

The wrappers state how to judge; what counts as accepted is the row's own
rubric, rendered into the per-call ``mandate_rubric`` binding, with one verify
role for every row.

Read off the shipped operation file and rendered through the registry the way
the owner renders it, so a row repointed at another role reddens here.
"""

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.types.domain.organize import MandateKind
from kodezart.types.domain.prompts import PromptKey
from tests.integration.test_scope_deployment import SCOPE_EXAMPLE
from tests.prompts.sets import OPUS_SET, ORGANIZE_CASE, V5_SET
from tests.prompts.test_prompt_wiring import load_registry
from tests.prompts.test_set_completeness import shipped_sets

SETS = shipped_sets()


def normalised(text: str) -> str:
    """*text* with every run of whitespace read as one space."""
    return " ".join(text.split())


#: The implementation test the run-stage rubric states. Every one of these was
#: in the two shared wrappers before the rubric roles existed.
IMPLEMENTATION_TEST = (
    "dry implementation",
    "grading demonstration",
    "demonstrated in the declared grading environment",
    "implemented from its own specification",
)


def test_the_shipped_sets_are_read_off_the_tree() -> None:
    """The derived set list is not empty, and holds both sets shipped today."""
    assert {OPUS_SET, V5_SET} <= set(SETS), SETS


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


#: How the admission wrapper states what it assesses: against the rubric it is
#: handed, and not against buildability.
ASSESSMENT = "Assess whether the issue satisfies the supplied mandate rubric"


@pytest.mark.parametrize("set_name", SETS)
def test_the_admission_wrapper_assesses_against_the_supplied_rubric(
    set_name: str,
) -> None:
    """The ticket row's admission wrapper defers to the rubric by name."""
    ticket = row_of(MandateKind.TICKET)
    rubric = rendered_rubric(set_name, ticket.spec.rubric_prompt_key)
    rendered = rendered_around(set_name, ticket.spec.admission_prompt_key, rubric)
    assert ASSESSMENT in normalised(rendered)
    assert rendered != rubric


@pytest.mark.parametrize("set_name", SETS)
def test_the_run_stage_rubric_states_the_implementation_test(set_name: str) -> None:
    """The control for the pin above: the same wrappers, the other rubric.

    The sentences did not disappear from the corpus; they moved to the rows
    whose mandate is buildability, and the same rendering finds them there.
    """
    ticket = row_of(MandateKind.TICKET)
    rubric = rendered_rubric(set_name, ticket.spec.rubric_prompt_key)
    for wrapper in (ticket.spec.admission_prompt_key, PromptKey.ORGANIZE_VERIFY):
        rendered = rendered_around(set_name, wrapper, rubric)
        for claim in IMPLEMENTATION_TEST:
            assert claim in rendered, (wrapper.value, claim)
