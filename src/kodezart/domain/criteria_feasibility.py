"""The feasibility sweep — the stated verdict, grounded against its evidence.

The refuter states each criterion's verdict and the evidence behind it.
The harness derives its own verdict from that evidence — never from the
statement — and refuses, fail-closed, any statement its evidence does not
derive, so a new case is classified by applying the fault-line test
rather than by trusting the word that arrived with it.

The fault line, in one sentence: if the only thing that settles a
criterion is an edit to its own text the criterion is at fault and the
verdict is ``infeasible``; if supplying something to the environment
settles it the criterion is untouched and the verdict is
``unverifiable``; if neither is needed it is ``feasible``.

Two rules keep that line from drifting:

* **Waiting is not a repair.**  :class:`RepairKind` has exactly three
  members and elapsed time is not among them, so a lack that clears with
  time is an absent resource — ``unverifiable`` here, cleared empirically
  on a later grading, never ``feasible`` at this gate.
* **A cost claim is measured, not argued.**  An unmeasured cost assertion
  is struck and can support no repair, so it never stands as
  ``infeasible``.  A measured demonstration that ran and proved
  affordable is likewise struck.  Only a measured, genuinely uneconomic
  demonstration survives, and it is environment-side.

A criterion the base ALREADY SATISFIES is satisfied by every
implementation, so it is ``feasible`` under the same definition, and a
repair demanded alongside the demonstration that ran and passed is
refused.  Its defect is discriminating power, not feasibility, and that
is what :class:`CriterionFlag` records — from evidence only its own class
supplies, never from ``smallest_repair``, so an observation cannot be
produced by re-labelling a repair.  Flagged criteria consume no
regeneration round and reach no halt; their consequence is the forced
``soft_signal`` downgrade in :mod:`kodezart.domain.criteria`.
"""

from collections.abc import Callable, Sequence

from kodezart.domain.errors import UngroundedVerdictError
from kodezart.domain.fan_in import fan_in_breach
from kodezart.types.domain.criteria import (
    ConjunctionVerdict,
    Contradiction,
    CostClaim,
    CostMeasurement,
    CriteriaValidation,
    CriteriaValidationOutput,
    CriterionFeasibility,
    CriterionFinding,
    CriterionFlag,
    CriterionVerdict,
    DerivedFeasibility,
    ForbiddenCriterionClass,
    GeneratedCriterion,
    LimitArm,
    RepairKind,
)


def _observed_flags(finding: CriterionFinding) -> list[CriterionFlag]:
    """The two observations that are not feasibility faults.

    Restated from the evidence its own class supplies — a demonstration
    run against the repo at base, or the literals the criterion pins —
    never from ``smallest_repair``, so an observation cannot be produced
    by re-labelling a repair.
    """
    flags: list[CriterionFlag] = []
    demonstration = finding.base_demonstration
    if demonstration is not None and demonstration.satisfied_at_base:
        flags.append(CriterionFlag.vacuous_at_base)
    if finding.pinned_literals or (
        finding.forbidden_class is ForbiddenCriterionClass.literal_count
    ):
        flags.append(CriterionFlag.literal_pinning)
    return flags


def _weigh_cost(claim: CostClaim | None) -> CostMeasurement | None:
    """The surviving measurement: a measured, genuinely uneconomic one.

    An unmeasured claim and an affordable measurement are both struck —
    neither supports a repair — so neither survives weighing.
    """
    if claim is None or claim.measurement is None:
        return None
    return None if claim.measurement.affordable else claim.measurement


def _ungradeable(finding: CriterionFinding) -> bool:
    """Whether the finding reports something the loop could never grade.

    A forbidden class other than ``literal_count`` names a criterion about
    something outside the tree the loop can read, and an undeclared switch
    arm names a case the type does not have.  Both are faults in the
    criterion's own text, so both take the criterion-text arm — never the
    environment arm, because nothing supplied to a runner makes an arm
    exist.
    """
    named_class = finding.forbidden_class
    ungradeable_class = (
        named_class is not None
        and named_class is not ForbiddenCriterionClass.literal_count
    )
    return ungradeable_class or bool(finding.undeclared_switch_arms)


def classify_finding(finding: CriterionFinding) -> DerivedFeasibility:
    """Derive one criterion's verdict from its evidence alone.

    Never reads the stated ``verdict``: the repair the finding names, the
    cost claim, the base demonstration, the pinned literals, the forbidden
    class and the undeclared arms are the whole input.  Raises when the
    evidence contradicts itself — a repair demanded on a criterion
    demonstrated satisfied at base, or a measured uneconomic cost filed
    anywhere but the environment arm.
    """
    surviving_cost = _weigh_cost(finding.cost_claim)
    flags = tuple(_observed_flags(finding))

    if _ungradeable(finding):
        return DerivedFeasibility(
            verdict=CriterionVerdict.infeasible,
            limit_arm=LimitArm.not_a_limit,
            flags=flags,
        )

    if (
        CriterionFlag.vacuous_at_base in flags
        and finding.smallest_repair is not RepairKind.none
    ):
        msg = (
            "A criterion demonstrated satisfied at base needs no repair: a "
            "demonstration that ran and passed cannot also ground a repair demand"
        )
        raise UngroundedVerdictError(msg, criterion_id=finding.criterion_id)

    if finding.smallest_repair is RepairKind.criterion_text:
        return _classify_criterion_side(finding, surviving_cost, flags)
    if finding.smallest_repair is RepairKind.environment_supply:
        return _classify_environment_side(surviving_cost, flags)
    return _classify_no_repair(finding, surviving_cost, flags)


def _classify_criterion_side(
    finding: CriterionFinding,
    surviving_cost: CostMeasurement | None,
    flags: tuple[CriterionFlag, ...],
) -> DerivedFeasibility:
    """A criterion-text repair stands on its refutation, never on a cost.

    A struck cost claim — unmeasured, or measured and affordable — was
    filed as the finding's support, and supports nothing: the criterion
    stands unrefuted and derives ``feasible``.
    """
    if surviving_cost is not None:
        msg = (
            "A measured uneconomic demonstration is environment-side and must "
            "be filed as an environment supply naming the resource"
        )
        raise UngroundedVerdictError(msg, criterion_id=finding.criterion_id)
    if finding.cost_claim is not None:
        return DerivedFeasibility(
            verdict=CriterionVerdict.feasible,
            limit_arm=LimitArm.not_a_limit,
            flags=flags,
        )
    return DerivedFeasibility(
        verdict=CriterionVerdict.infeasible,
        limit_arm=LimitArm.not_a_limit,
        flags=flags,
    )


def _classify_environment_side(
    surviving_cost: CostMeasurement | None,
    flags: tuple[CriterionFlag, ...],
) -> DerivedFeasibility:
    """The arm a limit is, discriminated by the presence of a measurement."""
    arm = (
        LimitArm.uneconomic if surviving_cost is not None else LimitArm.resource_absent
    )
    return DerivedFeasibility(
        verdict=CriterionVerdict.unverifiable,
        limit_arm=arm,
        flags=flags,
    )


def _classify_no_repair(
    finding: CriterionFinding,
    surviving_cost: CostMeasurement | None,
    flags: tuple[CriterionFlag, ...],
) -> DerivedFeasibility:
    if surviving_cost is not None:
        msg = (
            "A measured uneconomic demonstration is a repair and must be filed "
            "as an environment supply naming the resource"
        )
        raise UngroundedVerdictError(msg, criterion_id=finding.criterion_id)
    return DerivedFeasibility(
        verdict=CriterionVerdict.feasible,
        limit_arm=LimitArm.not_a_limit,
        flags=flags,
    )


def _grounded(finding: CriterionFinding) -> DerivedFeasibility:
    """The derivation, checked against the statement it sits beside."""
    derived = classify_finding(finding)
    if derived.verdict is not finding.verdict:
        msg = (
            f"A stated {finding.verdict.value} verdict is not derivable from "
            f"its own evidence, which derives {derived.verdict.value}"
        )
        raise UngroundedVerdictError(msg, criterion_id=finding.criterion_id)
    return derived


def _feasibility(
    finding: CriterionFinding,
    derived: DerivedFeasibility,
) -> CriterionFeasibility:
    """One finding as the run records it: the derived verdict, the evidence."""
    measurement = (
        finding.cost_claim.measurement if finding.cost_claim is not None else None
    )
    return CriterionFeasibility(
        criterion_id=finding.criterion_id,
        verdict=derived.verdict,
        refutation=finding.refutation,
        missing_resource=finding.missing_resource,
        cost_measurement=measurement,
        flags=list(derived.flags),
        forbidden_class=finding.forbidden_class,
        undeclared_switch_arms=list(finding.undeclared_switch_arms),
    )


def minimal_conflicting_subsets(
    contradictions: Sequence[Contradiction],
) -> tuple[Contradiction, ...]:
    """Every reported contradiction that contains no other reported one.

    Minimality is per conflict, not across the report: two disjoint
    conflicts are both minimal and both retained, and a superset of
    either is dropped in favour of the subset it contains.
    """
    sets = [(frozenset(c.criterion_ids), c) for c in contradictions]
    return tuple(
        contradiction
        for ids, contradiction in sets
        if not any(other < ids for other, _ in sets)
    )


def reconcile(
    criteria: Sequence[GeneratedCriterion],
    output: CriteriaValidationOutput,
) -> tuple[CriterionFinding, ...]:
    """Pair findings to dispatched ids 1:1. Raises on any correspondence hole.

    Both of the validator's channels are guarded: one finding per
    dispatched id, and no contradiction naming an id nobody dispatched.
    An unreconciled contradiction id reaches the conjunction verdict, the
    regeneration targets, the drafter's prompt and the pre-loop halt, so
    two rounds of a hallucinated one end a run over criteria that were
    never asked about.

    This is one of the two fan-in channels KOD-91 deliverable 4 covers,
    and it is the CHECK the node's bounded re-dispatch runs between
    sessions: the error it raises is the shared one, built at the single
    site in :mod:`kodezart.domain.fan_in`.  A pure fold cannot re-run a
    session, so the bound lives in the node and the refusal lives here.
    """
    dispatched = [c.id for c in criteria]
    dispatched_set = set(dispatched)
    seen: dict[str, CriterionFinding] = {}
    duplicates: list[str] = []
    unknown: list[str] = []
    for finding in output.findings:
        if finding.criterion_id not in dispatched_set:
            unknown.append(finding.criterion_id)
            continue
        if finding.criterion_id in seen:
            duplicates.append(finding.criterion_id)
            continue
        seen[finding.criterion_id] = finding
    for contradiction in output.contradictions:
        for id_ in contradiction.criterion_ids:
            if id_ not in dispatched_set and id_ not in unknown:
                unknown.append(id_)
    missing: list[str] = [id_ for id_ in dispatched if id_ not in seen]
    breach = fan_in_breach(
        missing_ids=missing,
        duplicate_ids=duplicates,
        unknown_ids=unknown,
    )
    if breach is not None:
        raise breach
    return tuple(seen[id_] for id_ in dispatched)


def sweep(
    criteria: Sequence[GeneratedCriterion],
    output: CriteriaValidationOutput,
) -> CriteriaValidation:
    """Reconcile the report, ground every verdict, fold the conjunction check."""
    return _fold(criteria, output, _grounded)


def sweep_derived(
    criteria: Sequence[GeneratedCriterion],
    output: CriteriaValidationOutput,
) -> CriteriaValidation:
    """The same sweep with the STATED verdict struck from the input.

    What the run records is the derivation either way — ``_feasibility``
    reads ``derived.verdict`` and never the statement — so the only thing
    dropped here is the refusal to proceed when the two disagree.  The
    statement becomes one more reported item and the derivation is what
    stands.

    It still raises where NO derivation exists: a correspondence hole
    leaves a criterion with no finding to derive from, and evidence that
    contradicts itself derives nothing.  Striking a statement cannot
    manufacture a verdict, so those halt here exactly as before.
    """
    return _fold(criteria, output, classify_finding)


def _fold(
    criteria: Sequence[GeneratedCriterion],
    output: CriteriaValidationOutput,
    derive: Callable[[CriterionFinding], DerivedFeasibility],
) -> CriteriaValidation:
    findings = reconcile(criteria, output)
    conflicts = minimal_conflicting_subsets(output.contradictions)
    return CriteriaValidation(
        verdicts=[_feasibility(finding, derive(finding)) for finding in findings],
        conjunction=ConjunctionVerdict(
            satisfiable=not conflicts,
            contradictions=list(conflicts),
        ),
    )


def regeneration_targets(validation: CriteriaValidation) -> tuple[str, ...]:
    """Ids the regenerator is asked to amend — ``infeasible`` ones only.

    An ``unverifiable`` criterion is regenerated by nobody: its text
    leaves the sweep byte-identical.  Every retained conflicting subset
    joins the targets because an unsatisfiable conjunction is a defect of
    the criteria themselves, settled by editing their text.
    """
    targets = [
        verdict.criterion_id
        for verdict in validation.verdicts
        if verdict.verdict is CriterionVerdict.infeasible
    ]
    for contradiction in validation.conjunction.contradictions:
        for id_ in contradiction.criterion_ids:
            if id_ not in targets:
                targets.append(id_)
    return tuple(targets)


def demands_regeneration(validation: CriteriaValidation) -> bool:
    """Whether this sweep consumes a regeneration round."""
    return bool(regeneration_targets(validation))
