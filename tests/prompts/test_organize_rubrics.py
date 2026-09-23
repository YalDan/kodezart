"""Each organize row's accept conditions are its rubric's, and nothing else's.

The wrappers state how to judge; what counts as accepted is the row's own
rubric, rendered into the per-call ``mandate_rubric`` binding. That is what
lets the pre-approval row be accepted on an organizational predicate while the
run-stage rows keep the implementation test, with one verify role for all of
them.

Read off the shipped operation file and rendered through the registry the way
the owner renders it, so a row repointed at another role reddens here.
"""

import json
import re

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.domain.organize import stage_rows
from kodezart.types.domain.agent import ORGANIZE_ADMISSION_SCHEMA
from kodezart.types.domain.organize import MandateKind
from kodezart.types.domain.prompts import PromptKey
from tests.integration.test_scope_deployment import SCOPE_EXAMPLE
from tests.prompts.sets import OPUS_SET, ORGANIZE_CASE, V5_SET
from tests.prompts.test_prompt_wiring import load_registry
from tests.prompts.test_set_completeness import shipped_sets

SETS = shipped_sets()

#: The four parts of the organizational predicate, each numbered item of the
#: rubric by its whole text, whitespace-normalised. A part reworded away, a
#: defining clause reversed or dropped while its headline stays — an edge that
#: need not cross a container, a dependency in prose that is acceptable, a
#: choice that is settled rather than open, a date order turned around, a
#: criterion a member only may carry — is a different predicate.
FOUR_PARTS = (
    "1. Every dependency the body states exists as a blocking edge on the board,"
    " including edges that cross a container: a dependency named in prose and"
    " absent from the graph is a defect of this mandate.",
    "2. Every member that records an open human choice is assigned to the person"
    " accountable for that choice, so the choice has an owner rather than a"
    " reader.",
    "3. Target dates are ordered: nothing is dated earlier than something it"
    " depends on, and an undated member that something dated depends on is a"
    " defect.",
    "4. Every member that will be executed already carries at least one criterion"
    " item, so what would be graded is written down before execution is planned.",
)


def normalised(text: str) -> str:
    """*text* with every run of whitespace read as one space."""
    return " ".join(text.split())


def numbered_items(text: str) -> list[str]:
    """Every numbered item of *text*, each with its continuation lines, whole."""
    items: list[list[str]] = []
    for line in text.splitlines():
        if re.match(r"\d+\. ", line):
            items.append([line])
        elif items and line.startswith(" ") and line.strip():
            items[-1].append(line)
        elif items and not line.strip():
            items.append([])
    return [normalised(" ".join(item)) for item in items if item]


#: The four parts are a conjunction: every one must hold.
CONJUNCTION = "only when all four conditions below hold"

#: What the pre-approval rubric states it does not judge, and that it refuses
#: on the four parts alone.
EXCLUSION = (
    "is no part of this mandate",
    "Refuse here only on the four conditions above",
)

#: What a pre-approval accept condition may not say. Every one of these was in
#: the two shared wrappers before the rubric roles existed.
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


@pytest.mark.parametrize("set_name", SETS)
def test_the_shipped_pre_approval_rubric_states_the_four_parts(set_name: str) -> None:
    """The pre-approval row's accept condition is the organizational predicate."""
    rows = stage_rows(
        load_operation_config(SCOPE_EXAMPLE).resolve_organize_mandates(),
        under_approval=False,
    )
    assert len(rows) == 1
    rubric = rendered_rubric(set_name, rows[0].spec.rubric_prompt_key)
    assert numbered_items(rubric) == list(FOUR_PARTS)
    assert CONJUNCTION in rubric
    for exclusion in EXCLUSION:
        assert exclusion in rubric, exclusion
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
    rows = stage_rows(
        load_operation_config(SCOPE_EXAMPLE).resolve_organize_mandates(),
        under_approval=False,
    )
    assert len(rows) == 1
    groom = rows[0]
    rubric = rendered_rubric(set_name, groom.spec.rubric_prompt_key)
    for wrapper in (groom.spec.admission_prompt_key, PromptKey.ORGANIZE_VERIFY):
        rendered = rendered_around(set_name, wrapper, rubric)
        for claim in IMPLEMENTATION_TEST:
            assert claim not in rendered, (wrapper.value, claim)
        # The presence side: the rubric is inside the wrapper, whole.
        assert rubric in rendered, wrapper.value
        for part in FOUR_PARTS:
            assert part in normalised(rendered), (wrapper.value, part)


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


def test_the_admission_schema_defines_acceptance_by_the_supplied_rubric() -> None:
    """The schema every row's session receives states no accept condition.

    Acceptance arrives in the rubric: the pre-approval row's rubric excludes
    buildability, so the schema's accepting verdict may not define it. This
    is a scan of the schema alone, apart from the rendered wrappers, which
    legitimately say "can be built" inside the rubric's own exclusion.
    """
    schema = json.dumps(ORGANIZE_ADMISSION_SCHEMA)
    for claim in (*IMPLEMENTATION_TEST, "can be built"):
        assert claim not in schema, claim
    assert "the supplied mandate rubric" in schema
