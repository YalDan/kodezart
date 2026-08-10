"""The sweep: reconciliation, the conjunction fold, and the persisted shape.

The refuter states each criterion's verdict, so nothing here asserts a
verdict the harness computed.  What is asserted is what the harness still
decides: which findings are accepted at all, which conflicts survive, and
what the run persists and grades on.
"""

import pytest
from pydantic import BaseModel, ValidationError

from kodezart.domain.criteria import build_artifact, mint_criteria
from kodezart.domain.criteria_feasibility import (
    demands_regeneration,
    minimal_conflicting_subsets,
    reconcile,
    regeneration_targets,
    sweep,
)
from kodezart.domain.errors import CriteriaFanInError
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
    GeneratedCriterion,
    RepairKind,
    ValidatedCriterion,
)


def _criterion(id_: str, text: str) -> GeneratedCriterion:
    return GeneratedCriterion(
        id=id_,
        text=text,
        criterion_class=CriterionClass.hard_gate,
    )


def _feasible(id_: str, **evidence: object) -> CriterionFinding:
    return CriterionFinding(
        criterion_id=id_,
        verdict=CriterionVerdict.feasible,
        smallest_repair=RepairKind.none,
        **evidence,
    )


def _swept(*findings: CriterionFinding) -> list:
    """The verdicts the sweep records for *findings*, in dispatch order."""
    criteria = tuple(
        _criterion(finding.criterion_id, f"criterion {finding.criterion_id}")
        for finding in findings
    )
    return list(
        sweep(criteria, CriteriaValidationOutput(findings=list(findings))).verdicts
    )


# ---------------------------------------------------------------------------
# The repair set is closed — waiting is not a member
# (KOD-53/AC-12, KOD-66 item 1b)
# ---------------------------------------------------------------------------


def test_repair_set_has_exactly_three_members() -> None:
    """The vocabulary the refuter is taught is closed; waiting is not in it."""
    assert {member.value for member in RepairKind} == {
        "none",
        "criterion_text",
        "environment_supply",
    }


# ---------------------------------------------------------------------------
# A STATED verdict arrives complete — its repair, and the evidence it rests on
# ---------------------------------------------------------------------------

#: The pair the refuter's instructions declare, restated INDEPENDENTLY of the
#: mapping under test so a wrong mapping cannot satisfy these cases.
_DECLARED_PAIRS = [
    (CriterionVerdict.feasible, RepairKind.none),
    (CriterionVerdict.infeasible, RepairKind.criterion_text),
    (CriterionVerdict.unverifiable, RepairKind.environment_supply),
]

_MISMATCHED_PAIRS = [
    (verdict, repair)
    for verdict, own in _DECLARED_PAIRS
    for _, repair in _DECLARED_PAIRS
    if repair is not own
]


def _finding_payload(
    verdict: CriterionVerdict,
    repair: RepairKind,
    **evidence: object,
) -> dict[str, object]:
    """One finding as the validator agent puts it on the wire, aliases and all."""
    return {
        "criterionId": "AC-1",
        "verdict": verdict.value,
        "smallestRepair": repair.value,
        **evidence,
    }


def _grounded(verdict: CriterionVerdict, repair: RepairKind) -> dict[str, object]:
    """A finding carrying BOTH evidence fields, so only the pair can fail."""
    return _finding_payload(
        verdict,
        repair,
        refutation="src/kodezart/core/config.py declares no such setting",
        missingResource="a PostgreSQL server reachable from the runner",
    )


@pytest.mark.parametrize(("verdict", "repair"), _MISMATCHED_PAIRS)
def test_a_verdict_wearing_another_verdicts_repair_is_refused(
    verdict: CriterionVerdict,
    repair: RepairKind,
) -> None:
    """The declared pair is CHECKED, not merely requested of the model.

    The refuter's instructions state the mapping in terms — "criterion_text
    is infeasible, environment_supply is unverifiable, none is feasible" —
    and then ask for it in a sentence.  Prose addressed to a model is not
    enforcement: a `feasible` verdict carrying a `criterion_text` repair
    swept clean and produced no regeneration target, so the criterion the
    refuter said was at fault in its own text was never sent back.
    """
    with pytest.raises(ValidationError) as excinfo:
        CriterionFinding.model_validate(_grounded(verdict, repair))
    assert verdict.value in str(excinfo.value)
    assert repair.value in str(excinfo.value)


@pytest.mark.parametrize(("verdict", "repair"), _DECLARED_PAIRS)
def test_each_verdict_admits_its_own_repair(
    verdict: CriterionVerdict,
    repair: RepairKind,
) -> None:
    """The paired positive: the consistency check admits every declared pair."""
    finding = CriterionFinding.model_validate(_grounded(verdict, repair))
    assert finding.verdict is verdict
    assert finding.smallest_repair is repair


@pytest.mark.parametrize(
    "evidence",
    [{}, {"refutation": None}, {"refutation": ""}, {"refutation": "   "}],
    ids=["omitted", "null", "empty", "whitespace"],
)
def test_an_infeasible_verdict_with_nothing_established_is_refused(
    evidence: dict[str, object],
) -> None:
    """An impossibility claim that shows nothing is an opinion, not a verdict.

    Blank counts as absent: a refutation of spaces reaches the drafter
    looking like evidence and carries none.
    """
    with pytest.raises(ValidationError) as excinfo:
        CriterionFinding.model_validate(
            _finding_payload(
                CriterionVerdict.infeasible,
                RepairKind.criterion_text,
                **evidence,
            ),
        )
    assert "refutation" in str(excinfo.value)


@pytest.mark.parametrize(
    "evidence",
    [{}, {"missingResource": None}, {"missingResource": ""}, {"missingResource": "  "}],
    ids=["omitted", "null", "empty", "whitespace"],
)
def test_an_unverifiable_verdict_naming_no_resource_is_refused(
    evidence: dict[str, object],
) -> None:
    """``unverifiable`` is a claim about a NAMED absent resource.

    Unnamed, it is where an inconclusive pass came to rest: it clamped
    the run's ceiling, took no seat in the arithmetic, was persisted with
    ``missingResource: null``, and only then failed — at the accept gate,
    a whole loop later, with the run's work already done.
    """
    with pytest.raises(ValidationError) as excinfo:
        CriterionFinding.model_validate(
            _finding_payload(
                CriterionVerdict.unverifiable,
                RepairKind.environment_supply,
                **evidence,
            ),
        )
    assert "missingResource" in str(excinfo.value)


def test_both_failures_are_reported_together_not_whichever_came_first() -> None:
    """A finding that is inconsistent AND ungrounded names both faults."""
    with pytest.raises(ValidationError) as excinfo:
        CriterionFinding.model_validate(
            _finding_payload(CriterionVerdict.infeasible, RepairKind.none),
        )
    message = str(excinfo.value)
    assert "criterion_text" in message
    assert "refutation" in message


def test_the_refusal_lands_on_the_call_the_validate_node_makes() -> None:
    """Enforcement sits where the model's JSON becomes a Python object.

    ``_validate_criteria_node`` turns the agent's output into a report
    with ``CriteriaValidationOutput.model_validate`` and nothing else
    inspects the payload, so that call is the whole of it — server-side
    strict enforcement does not engage for any schema kodezart ships.
    """
    with pytest.raises(ValidationError) as excinfo:
        CriteriaValidationOutput.model_validate(
            {
                "findings": [
                    _finding_payload(
                        CriterionVerdict.infeasible,
                        RepairKind.criterion_text,
                    ),
                ],
            },
        )
    assert [error["loc"] for error in excinfo.value.errors()] == [("findings", 0)]


def test_an_infeasible_verdict_the_sweep_records_carries_its_refutation() -> None:
    """The consequence: no reader of the sweep meets a blank refutation.

    ``render_validation_findings`` interpolates the refutation into the
    regeneration prompt with no guard of its own, so an ungrounded
    ``infeasible`` finding put the literal text ``refutation: None`` in
    front of the drafter.  What keeps it out is that the finding it would
    have been projected from cannot be constructed.
    """
    refutation = "src/kodezart/core/config.py declares no such setting"
    finding = CriterionFinding.model_validate(
        _finding_payload(
            CriterionVerdict.infeasible,
            RepairKind.criterion_text,
            refutation=refutation,
        ),
    )
    (recorded,) = _swept(finding)
    assert recorded.refutation == refutation


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
    satisfied_at_base = _feasible(
        "AC-1",
        base_demonstration=BaseDemonstration(
            command="rg -n 'class AppConfig' src/",
            satisfied_at_base=True,
        ),
    )
    pinned = _feasible("AC-2", pinned_literals=["exactly 3 occurrences"])
    bare = _feasible("AC-3")

    assert [verdict.flags for verdict in _swept(satisfied_at_base, pinned, bare)] == [
        [CriterionFlag.vacuous_at_base],
        [CriterionFlag.literal_pinning],
        [],
    ]


def test_a_demonstration_that_failed_at_base_flags_nothing() -> None:
    """Vacuity is the OBSERVED result, never the presence of a demonstration."""
    (verdict,) = _swept(
        _feasible(
            "AC-1",
            base_demonstration=BaseDemonstration(
                command="uv run pytest tests/chains/test_ralph_loop.py -q",
                satisfied_at_base=False,
            ),
        )
    )
    assert verdict.flags == []


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
            _feasible(
                "AC-1",
                base_demonstration=BaseDemonstration(
                    command="rg -n 'max_iterations' src/kodezart/core/config.py",
                    satisfied_at_base=True,
                ),
            ),
            _feasible("AC-2"),
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
            findings=[_feasible("AC-1", pinned_literals=["4 public functions"])],
        ),
    )
    artifact = build_artifact(criteria, validation)

    assert validation.verdicts[0].verdict is CriterionVerdict.feasible
    assert regeneration_targets(validation) == ()
    assert artifact.criteria[0].criterion_class is CriterionClass.soft_signal


# ---------------------------------------------------------------------------
# A cost claim crosses the sweep verbatim, measured or argued
# ---------------------------------------------------------------------------


def _blocked(id_: str, cost_claim: CostClaim) -> CriterionFinding:
    return CriterionFinding(
        criterion_id=id_,
        verdict=CriterionVerdict.unverifiable,
        smallest_repair=RepairKind.environment_supply,
        missing_resource="the sweep's compute allowance",
        cost_claim=cost_claim,
    )


def test_an_argued_cost_reaches_the_record_carrying_no_measurement() -> None:
    """The two fixtures differ in nothing but the presence of a measurement.

    A demonstration a quota prevented from running produces no
    measurement, and the record says so rather than inventing one: what
    a reader can tell apart is a priced demonstration from an argued one.
    """
    quota_blocked, ran_and_priced = _swept(
        _blocked("AC-1", CostClaim(assertion="the demonstration did not complete")),
        _blocked(
            "AC-2",
            CostClaim(
                assertion="the demonstration did not complete",
                measurement=CostMeasurement(
                    observed="9h of runner time",
                    affordable=False,
                ),
            ),
        ),
    )

    assert quota_blocked.cost_measurement is None
    assert quota_blocked.missing_resource == "the sweep's compute allowance"

    assert ran_and_priced.cost_measurement is not None
    assert ran_and_priced.cost_measurement.observed == "9h of runner time"
    assert ran_and_priced.cost_measurement.affordable is False


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
                verdict=CriterionVerdict.infeasible,
                smallest_repair=RepairKind.criterion_text,
                refutation=(
                    "the package's lint boundary forbids `app.api` from "
                    "exporting `Foo` to any consumer"
                ),
            ),
            CriterionFinding(
                criterion_id="AC-2",
                verdict=CriterionVerdict.unverifiable,
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
        findings=[_feasible("AC-1"), output.findings[1]],
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
            _feasible("AC-1"),
            _feasible("AC-2"),
            _feasible("AC-3"),
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
    assert [c.criterion_ids for c in validation.conjunction.contradictions] == [
        ["AC-1", "AC-2"]
    ]
    assert set(regeneration_targets(validation)) == {"AC-1", "AC-2"}


def test_no_contradictions_yields_a_satisfiable_conjunction() -> None:
    assert minimal_conflicting_subsets([]) == ()


def test_two_disjoint_conflicts_are_both_carried_and_both_regenerated() -> None:
    """A report naming two unrelated conflicts loses neither.

    Collapsing to one named subset discarded the second conflict entirely:
    its ids never reached the regenerator, so a set unsatisfiable in two
    ways came back amended in one.
    """
    criteria = mint_criteria(
        [
            DraftedCriterion(text=f"c{n}", criterion_class=CriterionClass.hard_gate)
            for n in range(1, 7)
        ]
    )
    output = CriteriaValidationOutput(
        findings=[_feasible(f"AC-{n}") for n in range(1, 7)],
        contradictions=[
            Contradiction(
                criterion_ids=["AC-1", "AC-2"],
                explanation="one public export cannot also be two",
            ),
            Contradiction(
                criterion_ids=["AC-5", "AC-6"],
                explanation="the module cannot be both frozen and mutable",
            ),
        ],
    )

    validation = sweep(criteria, output)

    assert validation.conjunction.satisfiable is False
    assert [
        (c.criterion_ids, c.explanation) for c in validation.conjunction.contradictions
    ] == [
        (["AC-1", "AC-2"], "one public export cannot also be two"),
        (["AC-5", "AC-6"], "the module cannot be both frozen and mutable"),
    ]
    assert set(regeneration_targets(validation)) == {"AC-1", "AC-2", "AC-5", "AC-6"}


def test_a_superset_of_a_reported_conflict_is_dropped() -> None:
    """Minimality is per conflict: the pair survives, the triple does not."""
    retained = minimal_conflicting_subsets(
        [
            Contradiction(
                criterion_ids=["AC-1", "AC-2", "AC-5"],
                explanation="a strict superset of the real conflict",
            ),
            Contradiction(
                criterion_ids=["AC-1", "AC-2"],
                explanation="one exported symbol cannot also be two",
            ),
        ]
    )

    assert [c.criterion_ids for c in retained] == [["AC-1", "AC-2"]]


# ---------------------------------------------------------------------------
# Fan-in is fail-closed and observable (KOD-53/AC-6)
# ---------------------------------------------------------------------------


def test_missing_finding_is_fail_closed_and_names_the_id() -> None:
    criteria = (_criterion("AC-1", "a"), _criterion("AC-2", "b"))
    output = CriteriaValidationOutput(
        findings=[_feasible("AC-1")],
    )
    with pytest.raises(CriteriaFanInError) as excinfo:
        reconcile(criteria, output)
    assert excinfo.value.missing_ids == ("AC-2",)
    assert "AC-2" in str(excinfo.value)


def test_duplicate_finding_is_fail_closed_and_names_the_id() -> None:
    criteria = (_criterion("AC-1", "a"),)
    output = CriteriaValidationOutput(
        findings=[_feasible("AC-1"), _feasible("AC-1")],
    )
    with pytest.raises(CriteriaFanInError) as excinfo:
        reconcile(criteria, output)
    assert excinfo.value.duplicate_ids == ("AC-1",)
    assert "AC-1" in str(excinfo.value)


def test_an_undispatched_contradiction_id_is_fail_closed_and_named() -> None:
    """The second channel is reconciled too, and sweep never returns.

    An id nobody dispatched, arriving through ``contradictions`` rather
    than through ``findings``, reached the conjunction verdict, the
    regeneration targets, the drafter's prompt and the pre-loop halt.
    """
    criteria = (_criterion("AC-1", "a"), _criterion("AC-2", "b"))
    output = CriteriaValidationOutput(
        findings=[_feasible("AC-1"), _feasible("AC-2")],
        contradictions=[
            Contradiction(
                criterion_ids=["AC-1", "AC-99"],
                explanation="a conflict with a criterion nobody dispatched",
            ),
        ],
    )
    with pytest.raises(CriteriaFanInError) as excinfo:
        sweep(criteria, output)
    assert excinfo.value.unknown_ids == ("AC-99",)


def test_a_contradiction_over_dispatched_ids_reconciles() -> None:
    """The paired negative: a well-formed report is untouched by the guard."""
    criteria = (_criterion("AC-1", "a"), _criterion("AC-2", "b"))
    output = CriteriaValidationOutput(
        findings=[_feasible("AC-1"), _feasible("AC-2")],
        contradictions=[
            Contradiction(
                criterion_ids=["AC-1", "AC-2"],
                explanation="one export cannot also be two",
            ),
        ],
    )
    assert sweep(criteria, output).conjunction.satisfiable is False


def test_unknown_finding_id_is_fail_closed_and_named() -> None:
    criteria = (_criterion("AC-1", "a"),)
    output = CriteriaValidationOutput(
        findings=[_feasible("AC-1"), _feasible("AC-9")],
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
            DraftedCriterion(text="a", criterion_class=CriterionClass.hard_gate),
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
    assert '"limitArm"' not in encoded


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
    missing = [error for error in excinfo.value.errors() if error["type"] == "missing"]
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
        findings=[_feasible("AC-1")],
    )
    artifact = build_artifact(criteria, sweep(criteria, output))
    restored = CriteriaArtifact.model_validate_json(
        artifact.model_dump_json(by_alias=True),
    )
    assert restored.criteria[0].criterion_class is CriterionClass.soft_signal
