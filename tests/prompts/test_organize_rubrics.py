"""Each organize row's accept conditions are its rubric's, and nothing else's.

The wrappers state how to judge; what counts as accepted is the row's own
rubric, rendered into the per-call ``mandate_rubric`` binding. That is what
lets the pre-approval row be accepted on an organizational predicate while the
run-stage rows keep the implementation test, with one verify role for all of
them.

Read off the shipped operation file and rendered through the registry the way
the owner renders it, so a row repointed at another role reddens here.
"""

import re
from typing import get_args

import pytest
from pydantic import BaseModel

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.domain.organize import stage_rows
from kodezart.types.domain.agent import ORGANIZE_ADMISSION_SCHEMA, AdmissionJudgment
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
        # The presence side: the rubric is inside the wrapper, whole, and the
        # wrapper is more than the rubric: it carries its own bindings.
        assert rubric in rendered, wrapper.value
        assert rendered != rubric, wrapper.value
        assert "Golden source issue body" in rendered, wrapper.value
        for part in FOUR_PARTS:
            assert part in normalised(rendered), (wrapper.value, part)


#: How the admission wrapper states what it assesses: against the rubric it is
#: handed, and not against buildability.
ASSESSMENT = "Assess whether the issue satisfies the supplied mandate rubric"


@pytest.mark.parametrize("set_name", SETS)
def test_the_admission_wrapper_assesses_against_the_supplied_rubric(
    set_name: str,
) -> None:
    """The pre-approval row's admission wrapper defers to the rubric by name."""
    groom = row_of(MandateKind.GROOM)
    rubric = rendered_rubric(set_name, groom.spec.rubric_prompt_key)
    rendered = rendered_around(set_name, groom.spec.admission_prompt_key, rubric)
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


#: What an admission description may not define acceptance by: buildability
#: under any inflection, and the phrase the rubric's own exclusion uses.
BUILDABILITY = ("buildab", "can be built")


def schema_descriptions(node: object) -> list[str]:
    """Every ``description`` string anywhere in a JSON schema, not its values."""
    if isinstance(node, dict):
        return [
            text
            for key, value in node.items()
            for text in (
                [value]
                if key == "description" and isinstance(value, str)
                else schema_descriptions(value)
            )
        ]
    if isinstance(node, list):
        return [text for value in node for text in schema_descriptions(value)]
    return []


def declared_descriptions(root: type[BaseModel]) -> list[str]:
    """Every field description the admission models declare, shadowed or not.

    The schema shows a field's description only from the class that declares
    it last, so a base class's own description of a field every subclass
    redeclares is read here, off each model the root reaches and each of
    their bases.
    """
    found: list[type[BaseModel]] = []
    pending: list[type[BaseModel]] = [root]
    while pending:
        model = pending.pop(0)
        if model in found:
            continue
        found.append(model)
        pending.extend(
            base
            for base in model.__mro__[1:]
            if isinstance(base, type)
            and issubclass(base, BaseModel)
            and base.__module__.startswith("kodezart.")
        )
        for field in model.model_fields.values():
            pending.extend(_models_in(field.annotation))
    return [
        field.description
        for model in found
        for field in model.model_fields.values()
        if field.description is not None
    ]


def _models_in(annotation: object) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [model for arg in get_args(annotation) for model in _models_in(arg)]


def test_the_admission_schema_defines_acceptance_by_the_supplied_rubric() -> None:
    """The schema every row's session receives states no accept condition.

    Acceptance arrives in the rubric: the pre-approval row's rubric excludes
    buildability, so no description the schema carries may define it. Every
    description string is read, and only those: the schema's enum values
    legitimately spell ``buildable``. The descriptions the admission models
    declare are read too, including one a subclass shadows, so a base
    field's reworded description is seen although the schema omits it. The
    accepting verdict itself names the supplied rubric.
    """
    descriptions = [
        *schema_descriptions(ORGANIZE_ADMISSION_SCHEMA),
        *declared_descriptions(AdmissionJudgment),
    ]
    assert descriptions
    for description in descriptions:
        for claim in (*BUILDABILITY, *IMPLEMENTATION_TEST):
            assert claim.lower() not in description.lower(), (claim, description)
    definitions = ORGANIZE_ADMISSION_SCHEMA["$defs"]
    assert isinstance(definitions, dict)
    accepting = definitions["BuildableAdmission"]["properties"]["verdict"]
    assert "the supplied mandate rubric" in accepting["description"]


def test_the_description_scan_reads_descriptions_and_not_values() -> None:
    """The control for the scan: a nested description is read, a value is not."""
    schema = {
        "description": "top",
        "$defs": {
            "X": {
                "properties": {
                    "verdict": {"const": "buildable", "description": "inner"},
                    "description": {"type": "string", "description": "field"},
                }
            }
        },
        "enum": ["not_buildable"],
    }
    assert sorted(schema_descriptions(schema)) == ["field", "inner", "top"]
