"""The feasibility sweep's arithmetic — verdicts computed from evidence.

Every classification case in this module is a row in a table.  That is a
requirement, not a style: a sweep that needs a new branch per case has
pattern-matched its examples instead of applying the fault-line test, and
the second-domain rows below are what catches it.
"""

import pytest

from kodezart.domain.criteria import build_artifact, mint_criteria
from kodezart.domain.criteria_feasibility import (
    classify_finding,
    demands_regeneration,
    minimal_conflicting_subset,
    reconcile,
    regeneration_targets,
    sweep,
)
from kodezart.domain.errors import CriteriaFanInError, UngroundedVerdictError
from kodezart.types.domain.criteria import (
    AcceptanceCriterion,
    Contradiction,
    CostClaim,
    CostMeasurement,
    CriteriaArtifact,
    CriteriaValidationOutput,
    CriterionClassification,
    CriterionFinding,
    DraftedCriterion,
    FeasibilityVerdict,
    LimitArm,
    RepairKind,
    StruckGround,
)


def _criterion(id_: str, text: str) -> AcceptanceCriterion:
    return AcceptanceCriterion(
        id=id_,
        text=text,
        classification=CriterionClassification.hard_gate,
    )


# ---------------------------------------------------------------------------
# The repair set is closed — waiting is not a member (KOD-66 item 1b)
# ---------------------------------------------------------------------------


def test_repair_set_has_exactly_three_members() -> None:
    """The gate ranges over one repair set and elapsed time is not in it.

    An implementation that admits waiting as a third repair lets a lack
    that clears with time read ``feasible`` here while the same case fails
    against a runner in the loop — the gate and the loop returning
    opposite readings on one criterion.
    """
    assert {member.value for member in RepairKind} == {
        "none",
        "criterion_text",
        "environment_supply",
    }


def test_a_lack_that_clears_with_time_does_not_reach_feasible() -> None:
    """Waiting is the absence of a repair, so the lack is still a lack."""
    finding = CriterionFinding(
        criterion_id="AC-1",
        smallest_repair=RepairKind.environment_supply,
        missing_resource="the provider rate-limit window, which resets in 4 hours",
    )
    verdict = classify_finding(finding)
    assert verdict.verdict is not FeasibilityVerdict.feasible
    assert verdict.verdict is FeasibilityVerdict.unverifiable
    assert verdict.missing_resource is not None
    assert verdict.limit_arm is LimitArm.resource_absent


# ---------------------------------------------------------------------------
# Fault-line classification table (KOD-66 items 1a-1c, AC-13 generality)
# ---------------------------------------------------------------------------

_CLASSIFICATION_TABLE: list[
    tuple[str, CriterionFinding, FeasibilityVerdict, LimitArm]
] = [
    (
        "class-1 structurally impossible: lint boundary forbids the export",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.criterion_text,
            refutation=(
                "pyproject.toml lint boundary rules forbid every consumer from "
                "importing the demanded export"
            ),
        ),
        FeasibilityVerdict.infeasible,
        LimitArm.not_a_limit,
    ),
    (
        "class-2 false premise: the named binding exists nowhere at base",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.criterion_text,
            refutation="no such binding is declared anywhere in the target repo",
        ),
        FeasibilityVerdict.infeasible,
        LimitArm.not_a_limit,
    ),
    (
        "class-4 already satisfied at base",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.criterion_text,
            refutation="the clause already holds at the base ref before any work",
        ),
        FeasibilityVerdict.infeasible,
        LimitArm.not_a_limit,
    ),
    (
        "class-5 literal-count pinning",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.criterion_text,
            refutation="pins an exact symbol count brittle to a legitimate refactor",
        ),
        FeasibilityVerdict.infeasible,
        LimitArm.not_a_limit,
    ),
    (
        "class-6 wrong-baseline scope criterion",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.criterion_text,
            refutation="measures scope against trunk rather than the recorded base",
        ),
        FeasibilityVerdict.infeasible,
        LimitArm.not_a_limit,
    ),
    (
        "no repair needed",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.none,
        ),
        FeasibilityVerdict.feasible,
        LimitArm.not_a_limit,
    ),
    (
        "premise holds, demonstration needs a database the runner lacks",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.environment_supply,
            missing_resource="a PostgreSQL server reachable from the runner",
        ),
        FeasibilityVerdict.unverifiable,
        LimitArm.resource_absent,
    ),
    # --- second domain: a browser-automation lane, no code changed ---------
    (
        "second domain, criterion side: the demanded selector API does not exist",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.criterion_text,
            refutation="the vendored driver exposes no such selector API at base",
        ),
        FeasibilityVerdict.infeasible,
        LimitArm.not_a_limit,
    ),
    (
        "second domain, environment side: no display server for the headed run",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.environment_supply,
            missing_resource="an X display for the headed browser session",
        ),
        FeasibilityVerdict.unverifiable,
        LimitArm.resource_absent,
    ),
    # --- cost claims (item 1c) --------------------------------------------
    (
        "unmeasured cost assertion cannot stand as infeasible",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.criterion_text,
            cost_claim=CostClaim(
                assertion="demonstrating this would take hours of compute",
            ),
        ),
        FeasibilityVerdict.feasible,
        LimitArm.not_a_limit,
    ),
    (
        "measured and affordable: the criterion text is untouched",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.criterion_text,
            cost_claim=CostClaim(
                assertion="demonstrating this would take hours of compute",
                measurement=CostMeasurement(observed="11s wall clock", affordable=True),
            ),
        ),
        FeasibilityVerdict.feasible,
        LimitArm.not_a_limit,
    ),
    (
        "a quota prevented the demonstration: environment side, resource named",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.environment_supply,
            missing_resource="the daily API quota the demonstration consumes",
        ),
        FeasibilityVerdict.unverifiable,
        LimitArm.resource_absent,
    ),
    (
        "the demonstration ran and is uneconomic: measured, so the other arm",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.environment_supply,
            missing_resource="a compute budget for the full sweep",
            cost_claim=CostClaim(
                assertion="the full sweep is uneconomic",
                measurement=CostMeasurement(
                    observed="9h of runner time",
                    affordable=False,
                ),
            ),
        ),
        FeasibilityVerdict.unverifiable,
        LimitArm.uneconomic,
    ),
]


@pytest.mark.parametrize(
    ("label", "finding", "expected_verdict", "expected_arm"),
    _CLASSIFICATION_TABLE,
    ids=[row[0] for row in _CLASSIFICATION_TABLE],
)
def test_classification_table(
    label: str,
    finding: CriterionFinding,
    expected_verdict: FeasibilityVerdict,
    expected_arm: LimitArm,
) -> None:
    """Every row classifies from evidence alone — no per-case branch."""
    verdict = classify_finding(finding)
    assert verdict.verdict is expected_verdict, label
    assert verdict.limit_arm is expected_arm, label


def test_unverifiable_is_never_coerced_to_a_pass() -> None:
    """``unverifiable`` is its own outcome, not a degraded pass."""
    finding = CriterionFinding(
        criterion_id="AC-1",
        smallest_repair=RepairKind.environment_supply,
        missing_resource="a PostgreSQL server reachable from the runner",
    )
    verdict = classify_finding(finding)
    assert verdict.verdict is FeasibilityVerdict.unverifiable
    assert verdict.verdict is not FeasibilityVerdict.feasible


# ---------------------------------------------------------------------------
# Which arm a limit is — the discriminator is the measurement (AC-15)
# ---------------------------------------------------------------------------


def test_a_limit_without_a_measurement_is_never_the_uneconomic_arm() -> None:
    """The two fixtures differ in nothing but the presence of a measurement."""
    shared = {
        "criterion_id": "AC-1",
        "smallest_repair": RepairKind.environment_supply,
        "missing_resource": "the sweep's compute allowance",
    }
    quota_blocked = CriterionFinding(
        **shared,
        cost_claim=CostClaim(assertion="the demonstration did not complete"),
    )
    ran_and_priced = CriterionFinding(
        **shared,
        cost_claim=CostClaim(
            assertion="the demonstration did not complete",
            measurement=CostMeasurement(observed="9h of runner time", affordable=False),
        ),
    )

    blocked = classify_finding(quota_blocked)
    priced = classify_finding(ran_and_priced)

    assert blocked.limit_arm is LimitArm.resource_absent
    assert blocked.cost_measurement is None
    assert blocked.missing_resource == "the sweep's compute allowance"
    assert StruckGround.unmeasured_cost in blocked.struck_grounds

    assert priced.limit_arm is LimitArm.uneconomic
    assert priced.cost_measurement is not None


def test_unmeasured_cost_claim_is_struck_and_recorded() -> None:
    """The claim is not merely ignored — striking it is observable."""
    verdict = classify_finding(
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.criterion_text,
            cost_claim=CostClaim(assertion="too expensive to demonstrate"),
        )
    )
    assert verdict.verdict is not FeasibilityVerdict.infeasible
    assert verdict.struck_grounds == [StruckGround.unmeasured_cost]


def test_measured_affordable_cost_leaves_the_criterion_feasible() -> None:
    verdict = classify_finding(
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.criterion_text,
            cost_claim=CostClaim(
                assertion="too expensive to demonstrate",
                measurement=CostMeasurement(observed="11s wall clock", affordable=True),
            ),
        )
    )
    assert verdict.verdict is FeasibilityVerdict.feasible
    assert verdict.struck_grounds == [StruckGround.affordable_cost]


# ---------------------------------------------------------------------------
# Neither verdict is a resting place for an inconclusive refuter (item 3)
# ---------------------------------------------------------------------------


def test_criterion_side_repair_without_a_refutation_raises() -> None:
    with pytest.raises(UngroundedVerdictError) as excinfo:
        classify_finding(
            CriterionFinding(
                criterion_id="AC-3",
                smallest_repair=RepairKind.criterion_text,
            )
        )
    assert excinfo.value.criterion_id == "AC-3"


def test_environment_side_repair_without_a_named_resource_raises() -> None:
    with pytest.raises(UngroundedVerdictError) as excinfo:
        classify_finding(
            CriterionFinding(
                criterion_id="AC-2",
                smallest_repair=RepairKind.environment_supply,
            )
        )
    assert excinfo.value.criterion_id == "AC-2"


# ---------------------------------------------------------------------------
# The fault line as one fixture, both arms asserted together (AC-11, AC-12)
# ---------------------------------------------------------------------------


def _fault_line_pair() -> tuple[
    tuple[AcceptanceCriterion, ...],
    CriteriaValidationOutput,
]:
    criteria = mint_criteria(
        [
            DraftedCriterion(
                text="The exported symbol `Foo` is importable from `app.api`.",
                classification=CriterionClassification.hard_gate,
            ),
            DraftedCriterion(
                text=(
                    "A round-trip through the  persistence layer preserves "
                    'the record\\\'s "id" field verbatim.'
                ),
                classification=CriterionClassification.hard_gate,
            ),
        ]
    )
    output = CriteriaValidationOutput(
        findings=[
            CriterionFinding(
                criterion_id="AC-1",
                smallest_repair=RepairKind.criterion_text,
                refutation=(
                    "the package's lint boundary forbids `app.api` from "
                    "exporting `Foo` to any consumer"
                ),
            ),
            CriterionFinding(
                criterion_id="AC-2",
                smallest_repair=RepairKind.environment_supply,
                missing_resource="a PostgreSQL server reachable from the runner",
            ),
        ],
    )
    return criteria, output


def test_fault_line_pair_routes_one_arm_and_leaves_the_other_untouched() -> None:
    """A is amended; B is not, and B's text survives the sweep byte-identical.

    One fixture, both arms: an implementation routing both to regeneration
    fails B's arm, and one halting on both fails A's.
    """
    criteria, output = _fault_line_pair()
    text_before = criteria[1].text

    validation = sweep(criteria, output)
    by_id = {v.criterion_id: v for v in validation.verdicts}

    assert by_id["AC-1"].verdict is FeasibilityVerdict.infeasible
    assert by_id["AC-1"].refutation is not None
    assert "lint boundary" in by_id["AC-1"].refutation

    assert by_id["AC-2"].verdict is FeasibilityVerdict.unverifiable
    assert (
        by_id["AC-2"].missing_resource
        == "a PostgreSQL server reachable from the runner"
    )

    assert regeneration_targets(validation) == ("AC-1",)

    artifact = build_artifact(criteria, validation)
    persisted = {c.id: c for c in artifact.criteria}
    assert persisted["AC-2"].text == text_before
    assert persisted["AC-2"].text.encode() == text_before.encode()


def test_unverifiable_only_set_consumes_no_regeneration_round() -> None:
    """The bound is consumed by the infeasible arm alone (AC-12)."""
    criteria, output = _fault_line_pair()
    feasible_a = CriteriaValidationOutput(
        findings=[
            CriterionFinding(
                criterion_id="AC-1",
                smallest_repair=RepairKind.none,
            ),
            output.findings[1],
        ],
    )
    validation = sweep(criteria, feasible_a)

    assert regeneration_targets(validation) == ()
    assert demands_regeneration(validation) is False
    by_id = {v.criterion_id: v for v in validation.verdicts}
    assert by_id["AC-2"].verdict is FeasibilityVerdict.unverifiable
    assert by_id["AC-2"].missing_resource is not None


# ---------------------------------------------------------------------------
# Conjunction satisfiability (KOD-66 item 2 / evidence class 3)
# ---------------------------------------------------------------------------


def test_jointly_unsatisfiable_set_names_the_minimal_conflicting_subset() -> None:
    criteria = mint_criteria(
        [
            DraftedCriterion(
                text="`handler.py` exports exactly one public function.",
                classification=CriterionClassification.hard_gate,
            ),
            DraftedCriterion(
                text="`handler.py` exports both `read` and `write` publicly.",
                classification=CriterionClassification.hard_gate,
            ),
            DraftedCriterion(
                text="`handler.py` carries a module docstring.",
                classification=CriterionClassification.soft_signal,
            ),
        ]
    )
    output = CriteriaValidationOutput(
        findings=[
            CriterionFinding(criterion_id="AC-1", smallest_repair=RepairKind.none),
            CriterionFinding(criterion_id="AC-2", smallest_repair=RepairKind.none),
            CriterionFinding(criterion_id="AC-3", smallest_repair=RepairKind.none),
        ],
        contradictions=[
            Contradiction(
                criterion_ids=["AC-1", "AC-2", "AC-3"],
                explanation="superset of the real conflict",
            ),
            Contradiction(
                criterion_ids=["AC-1", "AC-2"],
                explanation="one public export cannot also be two public exports",
            ),
        ],
    )

    validation = sweep(criteria, output)

    assert all(v.verdict is FeasibilityVerdict.feasible for v in validation.verdicts), (
        "each criterion is individually feasible"
    )
    assert validation.conjunction.satisfiable is False
    assert validation.conjunction.conflicting_ids == ["AC-1", "AC-2"]
    assert validation.conjunction.explanation is not None
    assert set(regeneration_targets(validation)) == {"AC-1", "AC-2"}


def test_no_contradictions_yields_a_satisfiable_conjunction() -> None:
    assert minimal_conflicting_subset([]) is None


# ---------------------------------------------------------------------------
# Fan-in is fail-closed and observable (KOD-66 output fan-in)
# ---------------------------------------------------------------------------


def test_missing_finding_is_fail_closed_and_names_the_id() -> None:
    criteria = (_criterion("AC-1", "a"), _criterion("AC-2", "b"))
    output = CriteriaValidationOutput(
        findings=[
            CriterionFinding(criterion_id="AC-1", smallest_repair=RepairKind.none)
        ],
    )
    with pytest.raises(CriteriaFanInError) as excinfo:
        reconcile(criteria, output)
    assert excinfo.value.missing_ids == ("AC-2",)
    assert "AC-2" in str(excinfo.value)


def test_duplicate_finding_is_fail_closed_and_names_the_id() -> None:
    criteria = (_criterion("AC-1", "a"),)
    output = CriteriaValidationOutput(
        findings=[
            CriterionFinding(criterion_id="AC-1", smallest_repair=RepairKind.none),
            CriterionFinding(criterion_id="AC-1", smallest_repair=RepairKind.none),
        ],
    )
    with pytest.raises(CriteriaFanInError) as excinfo:
        reconcile(criteria, output)
    assert excinfo.value.duplicate_ids == ("AC-1",)
    assert "AC-1" in str(excinfo.value)


def test_unknown_finding_id_is_fail_closed_and_named() -> None:
    criteria = (_criterion("AC-1", "a"),)
    output = CriteriaValidationOutput(
        findings=[
            CriterionFinding(criterion_id="AC-1", smallest_repair=RepairKind.none),
            CriterionFinding(criterion_id="AC-9", smallest_repair=RepairKind.none),
        ],
    )
    with pytest.raises(CriteriaFanInError) as excinfo:
        reconcile(criteria, output)
    assert excinfo.value.unknown_ids == ("AC-9",)


# ---------------------------------------------------------------------------
# Identity + persisted artifact (KOD-66 item 6, KOD-69 deliverable 2)
# ---------------------------------------------------------------------------


def test_ids_are_minted_in_emission_order() -> None:
    criteria = mint_criteria(
        [
            DraftedCriterion(
                text="a", classification=CriterionClassification.hard_gate
            ),
            DraftedCriterion(
                text="b",
                classification=CriterionClassification.soft_signal,
            ),
        ]
    )
    assert [c.id for c in criteria] == ["AC-1", "AC-2"]
    assert [c.text for c in criteria] == ["a", "b"]


def test_artifact_round_trips_ids_verdicts_and_evidence() -> None:
    criteria, output = _fault_line_pair()
    artifact = build_artifact(criteria, sweep(criteria, output))

    encoded = artifact.model_dump_json(indent=2, by_alias=True)
    restored = CriteriaArtifact.model_validate_json(encoded)

    assert restored == artifact
    assert [c.id for c in restored.criteria] == ["AC-1", "AC-2"]
    assert restored.criteria[1].feasibility.missing_resource is not None
    assert '"missingResource"' in encoded
    assert '"limitArm"' in encoded


def test_classification_round_trips_under_its_camel_case_alias() -> None:
    criteria, output = _fault_line_pair()
    artifact = build_artifact(criteria, sweep(criteria, output))
    encoded = artifact.model_dump_json(by_alias=True)

    assert '"classification":"hard_gate"' in encoded.replace(", ", ",")
    restored = CriteriaArtifact.model_validate_json(encoded)
    assert restored.criteria[0].classification is CriterionClassification.hard_gate


def test_artifact_accepts_soft_signal_classification() -> None:
    criteria = mint_criteria(
        [
            DraftedCriterion(
                text="grep finds no new `# noqa` on changed lines",
                classification=CriterionClassification.soft_signal,
            ),
        ]
    )
    output = CriteriaValidationOutput(
        findings=[
            CriterionFinding(criterion_id="AC-1", smallest_repair=RepairKind.none)
        ],
    )
    artifact = build_artifact(criteria, sweep(criteria, output))
    restored = CriteriaArtifact.model_validate_json(
        artifact.model_dump_json(by_alias=True),
    )
    assert restored.criteria[0].classification is CriterionClassification.soft_signal
