"""Set-world WIRING tests for criteria generation and refutation.

These tests assert what the harness controls: that the ticket, the ids
and the resolved base reach the template, and that the guard a defect
pattern needs is present and addressed to the case.

**They do not assert compliance, and must never be read as doing so.**
That an instruction is present is not that the output obeys it, and a
green gate over the substituted claim is precisely the failure this lane
exists to remove.  The compliance claim — that no forbidden-class
instance and no non-domain switch arm reaches the loop — is behaviour,
enforced by the sweep and asserted over the DISPATCHED criteria in
``tests/chains/test_criteria_validation.py``.
"""

import pytest

from kodezart.domain.criteria import mint_criteria
from kodezart.domain.criteria_prompt import render_validation_findings
from kodezart.types.domain.criteria import (
    ConjunctionVerdict,
    CriteriaValidation,
    CriterionClass,
    CriterionFeasibility,
    CriterionVerdict,
    DraftedCriterion,
)
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.test_prompt_wiring import load_registry

# Fixture tickets abstracted from the runs that induced the patterns.
PATTERN_1_TICKET = (
    "Add a `status` arm to the run-state renderer so every state the domain "
    "declares is rendered, including any the ticket author expects to exist."
)
PATTERN_3_TICKET = (
    "Tighten the lint surface: the change must not weaken any existing check."
)
PATTERN_5_TICKET = (
    "Complete the switch over `WorkflowOutcome` so no terminal route is "
    "left undiscriminated."
)


def _render(
    ticket: str,
    *,
    findings: str | None = None,
    base_ref: str = "main",
) -> str:
    return (
        load_registry()
        .template_for(PromptKey.ACCEPTANCE_CRITERIA)
        .render(
            {
                "task_description": ticket,
                "validation_findings": findings,
                "base_ref": base_ref,
                "skills_reference": "",
            },
        )
    )


@pytest.mark.parametrize(
    "ticket",
    [PATTERN_1_TICKET, PATTERN_3_TICKET, PATTERN_5_TICKET],
    ids=["pattern-1", "pattern-3", "pattern-5"],
)
def test_the_forbidden_classes_reach_every_pattern_fixture(ticket: str) -> None:
    """The ban is addressed to the case — a wiring claim, not a compliance one.

    What happens when the drafter emits one anyway is asserted where it
    can be: over the criteria the loop receives.
    """
    rendered = _render(ticket)
    assert "FORBIDDEN CRITERIA CLASSES" in rendered
    assert "pull-request body" in rendered
    assert "CI / check-run status" in rendered
    assert "merge / branch state" in rendered
    assert "require command execution to grade" in rendered.replace(
        "requiring command execution to grade",
        "require command execution to grade",
    )
    assert "LITERAL COUNTS" in rendered
    assert ticket in rendered


@pytest.mark.parametrize(
    "ticket",
    [PATTERN_1_TICKET, PATTERN_5_TICKET],
    ids=["pattern-1", "pattern-5"],
)
def test_exhaustive_switch_criteria_are_cross_checked_against_the_type(
    ticket: str,
) -> None:
    """Pattern 5's instruction is present; its enforcement is in the sweep."""
    rendered = _render(ticket)
    assert "BEHAVIORAL OVER LITERAL — EXHAUSTIVE SWITCHES" in rendered
    assert "read that type's ACTUAL definition first" in rendered
    assert "an arm that does not exist cannot be handled" in rendered
    assert "the criterion is that the TYPE gains the case" in rendered


def test_every_criterion_is_classified_hard_gate_or_soft_signal() -> None:
    """KOD-69 deliverable 2: the class is produced, not inferred."""
    rendered = _render(PATTERN_3_TICKET)
    assert "HARD GATE OR SOFT SIGNAL" in rendered
    assert "`hard_gate`" in rendered
    assert "`soft_signal`" in rendered
    assert "`criterionClass`" in rendered


def test_the_self_check_no_longer_claims_to_be_the_only_defence() -> None:
    """The disclaimer KOD-66 quotes is gone — there IS a hard guard now."""
    rendered = _render(PATTERN_3_TICKET)
    assert "in-prompt best-effort, not a hard guard" not in rendered
    assert "it does not introduce a new graph node" not in rendered
    assert "adversarial refuter" in rendered
    assert "minimal conflicting subset" in rendered


def test_the_first_round_carries_no_validation_findings_block() -> None:
    rendered = _render(PATTERN_1_TICKET)
    assert "<validation_findings>" not in rendered


def test_a_regeneration_round_inlines_only_the_amended_criteria() -> None:
    """`unverifiable` criteria are named to nobody — they are not amended."""
    criteria = list(
        mint_criteria(
            [
                DraftedCriterion(
                    text="`Foo` is importable from `app.api`.",
                    criterion_class=CriterionClass.hard_gate,
                ),
                DraftedCriterion(
                    text="A record round-trips through the store.",
                    criterion_class=CriterionClass.hard_gate,
                ),
            ]
        )
    )
    validation = CriteriaValidation(
        verdicts=[
            CriterionFeasibility(
                criterion_id="AC-1",
                verdict=CriterionVerdict.infeasible,
                refutation="the lint boundary forbids the export",
            ),
            CriterionFeasibility(
                criterion_id="AC-2",
                verdict=CriterionVerdict.unverifiable,
                missing_resource="a PostgreSQL server",
            ),
        ],
        conjunction=ConjunctionVerdict(satisfiable=True),
    )
    findings = render_validation_findings(criteria, validation)
    assert findings is not None

    rendered = _render(PATTERN_1_TICKET, findings=findings)
    assert "<validation_findings>" in rendered
    assert "AC-1 infeasible" in rendered
    assert "the lint boundary forbids the export" in rendered
    assert "AC-2" not in rendered
    assert "PostgreSQL" not in rendered
    assert "do not amend criteria that are not named here" in rendered


def test_a_clean_sweep_renders_no_findings_block() -> None:
    criteria = list(
        mint_criteria(
            [
                DraftedCriterion(
                    text="`Foo` is importable from `app.api`.",
                    criterion_class=CriterionClass.hard_gate,
                ),
            ]
        )
    )
    validation = CriteriaValidation(
        verdicts=[
            CriterionFeasibility(
                criterion_id="AC-1",
                verdict=CriterionVerdict.feasible,
            ),
        ],
        conjunction=ConjunctionVerdict(satisfiable=True),
    )
    assert render_validation_findings(criteria, validation) is None


# ---------------------------------------------------------------------------
# The refuter prompt
# ---------------------------------------------------------------------------


def _render_validator(base_ref: str = "main") -> str:
    criteria = list(
        mint_criteria(
            [
                DraftedCriterion(
                    text="`Foo` is importable from `app.api`.",
                    criterion_class=CriterionClass.hard_gate,
                ),
                DraftedCriterion(
                    text="No new `# noqa` appears on changed lines.",
                    criterion_class=CriterionClass.soft_signal,
                ),
            ]
        )
    )
    return (
        load_registry()
        .template_for(PromptKey.CRITERIA_VALIDATION)
        .render(
            {
                "task_description": PATTERN_1_TICKET,
                "acceptance_criteria": criteria,
                "base_ref": base_ref,
                "skills_reference": "",
            },
        )
    )


def test_the_refuter_dispatches_id_tagged_criteria_against_a_named_base() -> None:
    rendered = _render_validator(base_ref="kodezart/pr3-lane")
    assert "kodezart/pr3-lane" in rendered
    assert "AC-1 [hard_gate] `Foo` is importable from `app.api`." in rendered
    assert "AC-2 [soft_signal] No new `# noqa` appears on changed lines." in rendered
    assert "Exactly one finding per criterion id" in rendered


def test_the_refuter_is_told_the_repair_set_is_closed() -> None:
    """Waiting is not a repair, and the prompt says so at the point of use."""
    rendered = _render_validator()
    assert "exactly two members, and there is no third" in rendered
    assert "Elapsed time is not a repair" in rendered
    assert "Never report `none` on the grounds that the obstacle is temporary" in (
        rendered
    )


def test_the_refuter_must_measure_a_cost_claim_rather_than_argue_it() -> None:
    rendered = _render_validator()
    assert "RUN THE CHEAPEST EXPERIMENT THAT WOULD SETTLE IT" in rendered
    assert "A measurement is of a demonstration that ACTUALLY RAN" in rendered
    assert "never dress it up as an uneconomic demonstration" in rendered


def test_the_refuter_never_receives_the_generator_reasoning() -> None:
    """The generator cannot be its own refuter."""
    rendered = _render_validator()
    assert "You do not have the drafter's reasoning" in rendered
    assert "dispatch no subagents" in rendered


def test_neither_outcome_is_a_resting_place_for_an_inconclusive_refuter() -> None:
    rendered = _render_validator()
    assert "NEITHER OUTCOME IS A RESTING PLACE" in rendered
    assert "is not a finding" in rendered
