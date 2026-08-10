"""The feasibility sweep's arithmetic — verdicts computed from evidence.

Every classification case in this module is a row in a table.  That is a
requirement, not a style: a sweep that needs a new branch per case has
pattern-matched its examples instead of applying the fault-line test, and
the second-domain rows below are what catches it.
"""

import pytest
from pydantic import BaseModel, ValidationError

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
    BaseDemonstration,
    Contradiction,
    CostClaim,
    CostMeasurement,
    CriteriaArtifact,
    CriteriaValidationOutput,
    CriterionClass,
    CriterionFinding,
    CriterionFlag,
    CriterionVerdict,
    DraftedCriterion,
    ForbiddenCriterionClass,
    GeneratedCriterion,
    LimitArm,
    RepairKind,
    StruckGround,
    ValidatedCriterion,
)


def _criterion(id_: str, text: str) -> GeneratedCriterion:
    return GeneratedCriterion(
        id=id_,
        text=text,
        criterion_class=CriterionClass.hard_gate,
    )


# ---------------------------------------------------------------------------
# The repair set is closed — waiting is not a member
# (KOD-53/AC-12, KOD-66 item 1b)
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
    assert verdict.verdict is not CriterionVerdict.feasible
    assert verdict.verdict is CriterionVerdict.unverifiable
    assert verdict.missing_resource is not None
    assert verdict.limit_arm is LimitArm.resource_absent


# ---------------------------------------------------------------------------
# Fault-line classification table — the six evidence classes and the
# second-domain rows (KOD-53/AC-1 and KOD-53/AC-9, KOD-66 items 1a-1c)
# ---------------------------------------------------------------------------

_CLASSIFICATION_TABLE: list[
    tuple[str, CriterionFinding, CriterionVerdict, LimitArm, list[CriterionFlag]]
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
        CriterionVerdict.infeasible,
        LimitArm.not_a_limit,
        [],
    ),
    (
        "class-2 false premise: the named binding exists nowhere at base",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.criterion_text,
            refutation="no such binding is declared anywhere in the target repo",
        ),
        CriterionVerdict.infeasible,
        LimitArm.not_a_limit,
        [],
    ),
    (
        "class-4 already satisfied at base",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.none,
            base_demonstration=BaseDemonstration(
                command="uv run pytest tests/api/v1/test_health.py -q",
                satisfied_at_base=True,
            ),
        ),
        CriterionVerdict.feasible,
        LimitArm.not_a_limit,
        [CriterionFlag.vacuous_at_base],
    ),
    (
        "class-5 literal-count pinning",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.none,
            pinned_literals=["src/kodezart/core/config.py", "exactly 3 occurrences"],
        ),
        CriterionVerdict.feasible,
        LimitArm.not_a_limit,
        [CriterionFlag.literal_pinning],
    ),
    (
        "class-6 wrong-baseline scope criterion",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.criterion_text,
            refutation="measures scope against trunk rather than the recorded base",
        ),
        CriterionVerdict.infeasible,
        LimitArm.not_a_limit,
        [],
    ),
    (
        "no repair needed",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.none,
        ),
        CriterionVerdict.feasible,
        LimitArm.not_a_limit,
        [],
    ),
    (
        "premise holds, demonstration needs a database the runner lacks",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.environment_supply,
            missing_resource="a PostgreSQL server reachable from the runner",
        ),
        CriterionVerdict.unverifiable,
        LimitArm.resource_absent,
        [],
    ),
    # --- second domain: a browser-automation lane, no code changed ---------
    (
        "second domain, criterion side: the demanded selector API does not exist",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.criterion_text,
            refutation="the vendored driver exposes no such selector API at base",
        ),
        CriterionVerdict.infeasible,
        LimitArm.not_a_limit,
        [],
    ),
    (
        "second domain, environment side: no display server for the headed run",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.environment_supply,
            missing_resource="an X display for the headed browser session",
        ),
        CriterionVerdict.unverifiable,
        LimitArm.resource_absent,
        [],
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
        CriterionVerdict.feasible,
        LimitArm.not_a_limit,
        [],
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
        CriterionVerdict.feasible,
        LimitArm.not_a_limit,
        [],
    ),
    (
        "a quota prevented the demonstration: environment side, resource named",
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.environment_supply,
            missing_resource="the daily API quota the demonstration consumes",
        ),
        CriterionVerdict.unverifiable,
        LimitArm.resource_absent,
        [],
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
        CriterionVerdict.unverifiable,
        LimitArm.uneconomic,
        [],
    ),
]


@pytest.mark.parametrize(
    ("label", "finding", "expected_verdict", "expected_arm", "expected_flags"),
    _CLASSIFICATION_TABLE,
    ids=[row[0] for row in _CLASSIFICATION_TABLE],
)
def test_classification_table(
    label: str,
    finding: CriterionFinding,
    expected_verdict: CriterionVerdict,
    expected_arm: LimitArm,
    expected_flags: list[CriterionFlag],
) -> None:
    """Every row classifies from evidence alone — no per-case branch."""
    verdict = classify_finding(finding)
    assert verdict.verdict is expected_verdict, label
    assert verdict.limit_arm is expected_arm, label
    assert verdict.flags == expected_flags, label


def test_unverifiable_is_never_coerced_to_a_pass() -> None:
    """``unverifiable`` is its own outcome, not a degraded pass."""
    finding = CriterionFinding(
        criterion_id="AC-1",
        smallest_repair=RepairKind.environment_supply,
        missing_resource="a PostgreSQL server reachable from the runner",
    )
    verdict = classify_finding(finding)
    assert verdict.verdict is CriterionVerdict.unverifiable
    assert verdict.verdict is not CriterionVerdict.feasible


# ---------------------------------------------------------------------------
# A criterion the base already satisfies is FEASIBLE and flagged
# (KOD-53/AC-1 evidence class 4, R1 on KOD-66)
# ---------------------------------------------------------------------------


def test_the_two_observation_classes_are_told_apart_by_their_own_evidence() -> None:
    """The flag is carried by class-specific evidence, not by the repair field.

    Both findings report the SAME ``smallest_repair``.  If the sweep read
    the evidence class off that field the two would be indistinguishable;
    each is separated only by the evidence its own class supplies — a
    demonstration that ran at base, or the literals the criterion pins.
    """
    shared = {"criterion_id": "AC-1", "smallest_repair": RepairKind.none}
    satisfied_at_base = CriterionFinding(
        **shared,
        base_demonstration=BaseDemonstration(
            command="rg -n 'class AppConfig' src/",
            satisfied_at_base=True,
        ),
    )
    pinned = CriterionFinding(**shared, pinned_literals=["exactly 3 occurrences"])
    bare = CriterionFinding(**shared)

    assert classify_finding(satisfied_at_base).flags == [CriterionFlag.vacuous_at_base]
    assert classify_finding(pinned).flags == [CriterionFlag.literal_pinning]
    assert classify_finding(bare).flags == []


def test_a_demonstration_that_failed_at_base_flags_nothing() -> None:
    """Vacuity is the OBSERVED result, never the presence of a demonstration."""
    verdict = classify_finding(
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.none,
            base_demonstration=BaseDemonstration(
                command="uv run pytest tests/chains/test_ralph_loop.py -q",
                satisfied_at_base=False,
            ),
        )
    )
    assert verdict.verdict is CriterionVerdict.feasible
    assert verdict.flags == []


def test_satisfied_at_base_alongside_a_repair_demand_raises() -> None:
    """A check that ran and passed cannot also ground a demand to repair it."""
    with pytest.raises(UngroundedVerdictError) as excinfo:
        classify_finding(
            CriterionFinding(
                criterion_id="AC-4",
                smallest_repair=RepairKind.criterion_text,
                refutation="the clause already holds at base before any work",
                base_demonstration=BaseDemonstration(
                    command="uv run pytest -q",
                    satisfied_at_base=True,
                ),
            )
        )
    assert excinfo.value.criterion_id == "AC-4"


def test_a_flagged_criterion_is_forced_to_soft_signal_and_keeps_its_text() -> None:
    """The flag's whole consequence: it leaves the hard-gate partition.

    The text is byte-identical either side of the sweep and no
    regeneration round is consumed — the defect is discriminating power,
    which regeneration cannot repair.
    """
    criteria = mint_criteria(
        [
            DraftedCriterion(
                text="`AppConfig` exposes a `max_iterations` field.",
                criterion_class=CriterionClass.hard_gate,
            ),
            DraftedCriterion(
                text="The new module is importable from `kodezart.domain`.",
                criterion_class=CriterionClass.hard_gate,
            ),
        ]
    )
    output = CriteriaValidationOutput(
        findings=[
            CriterionFinding(
                criterion_id="AC-1",
                smallest_repair=RepairKind.none,
                base_demonstration=BaseDemonstration(
                    command="rg -n 'max_iterations' src/kodezart/core/config.py",
                    satisfied_at_base=True,
                ),
            ),
            CriterionFinding(criterion_id="AC-2", smallest_repair=RepairKind.none),
        ],
    )

    validation = sweep(criteria, output)
    artifact = build_artifact(criteria, validation)

    assert validation.verdicts[0].verdict is CriterionVerdict.feasible
    assert validation.verdicts[0].flags == [CriterionFlag.vacuous_at_base]
    assert regeneration_targets(validation) == ()
    assert demands_regeneration(validation) is False

    flagged, untouched = artifact.criteria
    assert flagged.criterion_class is CriterionClass.soft_signal
    assert flagged.text == criteria[0].text
    assert untouched.criterion_class is CriterionClass.hard_gate


def test_pinned_literals_downgrade_the_same_way() -> None:
    """Same mechanism, second observation class — one downgrade rule."""
    criteria = mint_criteria(
        [
            DraftedCriterion(
                text="`src/kodezart/domain/criteria.py` contains 4 public functions.",
                criterion_class=CriterionClass.hard_gate,
            )
        ]
    )
    validation = sweep(
        criteria,
        CriteriaValidationOutput(
            findings=[
                CriterionFinding(
                    criterion_id="AC-1",
                    smallest_repair=RepairKind.none,
                    pinned_literals=["4 public functions"],
                )
            ],
        ),
    )
    artifact = build_artifact(criteria, validation)

    assert validation.verdicts[0].verdict is CriterionVerdict.feasible
    assert regeneration_targets(validation) == ()
    assert artifact.criteria[0].criterion_class is CriterionClass.soft_signal


# ---------------------------------------------------------------------------
# Which arm a limit is — the discriminator is the measurement (KOD-53/AC-11)
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


# ---------------------------------------------------------------------------
# Cost is measured, not argued (KOD-53/AC-10, KOD-66 item 1c)
# ---------------------------------------------------------------------------


def test_unmeasured_cost_claim_is_struck_and_recorded() -> None:
    """The claim is not merely ignored — striking it is observable."""
    verdict = classify_finding(
        CriterionFinding(
            criterion_id="AC-1",
            smallest_repair=RepairKind.criterion_text,
            cost_claim=CostClaim(assertion="too expensive to demonstrate"),
        )
    )
    assert verdict.verdict is not CriterionVerdict.infeasible
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
    assert verdict.verdict is CriterionVerdict.feasible
    assert verdict.struck_grounds == [StruckGround.affordable_cost]


# ---------------------------------------------------------------------------
# Neither verdict is a resting place for an inconclusive refuter
# (KOD-53/AC-1, KOD-66 item 3)
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


@pytest.mark.parametrize(
    ("label", "finding"),
    [
        (
            "a forbidden class with nothing behind it",
            CriterionFinding(
                criterion_id="AC-5",
                smallest_repair=RepairKind.none,
                forbidden_class=ForbiddenCriterionClass.ci_status,
            ),
        ),
        (
            "an undeclared arm with nothing behind it",
            CriterionFinding(
                criterion_id="AC-5",
                smallest_repair=RepairKind.none,
                undeclared_switch_arms=["archived"],
            ),
        ),
    ],
    ids=["forbidden-class", "undeclared-arm"],
)
def test_an_ungradeable_report_without_a_refutation_raises(
    label: str,
    finding: CriterionFinding,
) -> None:
    """The third arm of the same rule, asserted like its two siblings.

    An ungradeable report is the strongest verdict this gate issues — it
    halts a run before the loop — and it is reached without any repair
    field being consulted, so a refuter that named a class and established
    nothing would otherwise buy the whole halt for a word.

    Both rows carry ``RepairKind.none`` deliberately.  A row demanding a
    criterion-text repair raises for a second, older reason — the repair
    has no refutation behind it — so it would keep passing with the
    ungradeable guard deleted and would demonstrate that guard's absence
    to nobody.  With no repair demanded, deleting the guard routes both
    rows to ``_classify_no_repair`` and both return ``feasible``, which is
    the removal this parametrisation is here to catch.
    """
    with pytest.raises(UngroundedVerdictError) as excinfo:
        classify_finding(finding)
    assert excinfo.value.criterion_id == "AC-5", label


# ---------------------------------------------------------------------------
# The fault line as one fixture, both arms asserted together
# (KOD-53/AC-7, and KOD-53/AC-8 for the bound it does not spend)
# ---------------------------------------------------------------------------


def _fault_line_pair() -> tuple[
    tuple[GeneratedCriterion, ...],
    CriteriaValidationOutput,
]:
    criteria = mint_criteria(
        [
            DraftedCriterion(
                text="The exported symbol `Foo` is importable from `app.api`.",
                criterion_class=CriterionClass.hard_gate,
            ),
            DraftedCriterion(
                text=(
                    "A round-trip through the  persistence layer preserves "
                    'the record\\\'s "id" field verbatim.'
                ),
                criterion_class=CriterionClass.hard_gate,
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

    assert by_id["AC-1"].verdict is CriterionVerdict.infeasible
    assert by_id["AC-1"].refutation is not None
    assert "lint boundary" in by_id["AC-1"].refutation

    assert by_id["AC-2"].verdict is CriterionVerdict.unverifiable
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
    """KOD-53/AC-8 — the bound is consumed by the infeasible arm alone."""
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
    assert by_id["AC-2"].verdict is CriterionVerdict.unverifiable
    assert by_id["AC-2"].missing_resource is not None


# ---------------------------------------------------------------------------
# Conjunction satisfiability (KOD-53/AC-2, KOD-66 item 2 / evidence class 3)
# ---------------------------------------------------------------------------


def test_jointly_unsatisfiable_set_names_the_minimal_conflicting_subset() -> None:
    criteria = mint_criteria(
        [
            DraftedCriterion(
                text="`handler.py` exports exactly one public function.",
                criterion_class=CriterionClass.hard_gate,
            ),
            DraftedCriterion(
                text="`handler.py` exports both `read` and `write` publicly.",
                criterion_class=CriterionClass.hard_gate,
            ),
            DraftedCriterion(
                text="`handler.py` carries a module docstring.",
                criterion_class=CriterionClass.soft_signal,
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

    assert all(v.verdict is CriterionVerdict.feasible for v in validation.verdicts), (
        "each criterion is individually feasible"
    )
    assert validation.conjunction.satisfiable is False
    assert validation.conjunction.conflicting_ids == ["AC-1", "AC-2"]
    assert validation.conjunction.explanation is not None
    assert set(regeneration_targets(validation)) == {"AC-1", "AC-2"}


def test_no_contradictions_yields_a_satisfiable_conjunction() -> None:
    assert minimal_conflicting_subset([]) is None


# ---------------------------------------------------------------------------
# Fan-in is fail-closed and observable (KOD-53/AC-6)
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
# Identity + persisted artifact — the shape round-trips, and the
# classification rides it (KOD-53/AC-5 and KOD-53/AC-14)
# ---------------------------------------------------------------------------


def test_ids_are_minted_in_emission_order() -> None:
    criteria = mint_criteria(
        [
            DraftedCriterion(
                text="a", criterion_class=CriterionClass.hard_gate
            ),
            DraftedCriterion(
                text="b",
                criterion_class=CriterionClass.soft_signal,
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


def test_criterion_class_round_trips_under_its_camel_case_alias() -> None:
    """KOD-53/AC-14 — the field crosses the wire under the ruled alias.

    The alias is the assertion: `criterion_class` is two words, so an
    artifact written by a model without the camelCase generator carries
    `"criterion_class"` and fails here.  It is what a reader of the
    persisted artifact addresses the field by.
    """
    criteria, output = _fault_line_pair()
    artifact = build_artifact(criteria, sweep(criteria, output))
    encoded = artifact.model_dump_json(by_alias=True)

    assert '"criterionClass":"hard_gate"' in encoded.replace(", ", ",")
    assert '"criterion_class"' not in encoded
    restored = CriteriaArtifact.model_validate_json(encoded)
    assert restored.criteria[0].criterion_class is CriterionClass.hard_gate


@pytest.mark.parametrize(
    ("record", "payload"),
    [
        (DraftedCriterion, {"text": "a criterion"}),
        (GeneratedCriterion, {"id": "AC-1", "text": "a criterion"}),
        (
            ValidatedCriterion,
            {
                "id": "AC-1",
                "text": "a criterion",
                "feasibility": {
                    "criterionId": "AC-1",
                    "verdict": CriterionVerdict.feasible.value,
                },
            },
        ),
    ],
    ids=["drafted", "generated", "validated"],
)
def test_a_payload_without_the_criterion_class_fails_validation(
    record: type[BaseModel],
    payload: dict[str, object],
) -> None:
    """KOD-53/AC-14, KOD-69 R3 — *populated* as a schema fact, not an aspiration.

    Every record carrying the field declares it with no default, so a
    payload omitting it raises at the model boundary rather than
    surfacing a silent default three surfaces downstream.  Asserted here
    because a later default would otherwise pass the whole suite.
    """
    with pytest.raises(ValidationError) as excinfo:
        record.model_validate(payload)
    missing = [
        error for error in excinfo.value.errors() if error["type"] == "missing"
    ]
    assert [error["loc"] for error in missing] == [("criterionClass",)]


def test_artifact_accepts_soft_signal_classification() -> None:
    criteria = mint_criteria(
        [
            DraftedCriterion(
                text="grep finds no new `# noqa` on changed lines",
                criterion_class=CriterionClass.soft_signal,
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
    assert restored.criteria[0].criterion_class is CriterionClass.soft_signal
