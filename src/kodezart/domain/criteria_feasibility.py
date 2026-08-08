"""The feasibility sweep's arithmetic — pure, total, and model-free.

The refuter reports EVIDENCE: the smallest repair that would settle each
criterion, plus what it established.  The verdict is computed here from
that evidence and from nothing else, so a new case is classified by
applying the fault-line test rather than by adding a branch for it.

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
  is struck and can support no repair, so it never produces
  ``infeasible``.  A measured demonstration that ran and proved
  affordable is likewise struck.  Only a measured, genuinely uneconomic
  demonstration survives, and it is environment-side.
"""

from collections.abc import Sequence

from kodezart.domain.errors import CriteriaFanInError, UngroundedVerdictError
from kodezart.types.domain.criteria import (
    AcceptanceCriterion,
    ConjunctionVerdict,
    Contradiction,
    CostClaim,
    CostMeasurement,
    CriteriaValidation,
    CriteriaValidationOutput,
    CriterionFeasibility,
    CriterionFinding,
    FeasibilityVerdict,
    LimitArm,
    RepairKind,
    StruckGround,
)


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _weigh_cost(
    claim: CostClaim | None,
) -> tuple[CostMeasurement | None, list[StruckGround]]:
    """Split a cost claim into the part that survives and the part struck."""
    if claim is None:
        return None, []
    if claim.measurement is None:
        return None, [StruckGround.unmeasured_cost]
    if claim.measurement.affordable:
        return None, [StruckGround.affordable_cost]
    return claim.measurement, []


def classify_finding(finding: CriterionFinding) -> CriterionFeasibility:
    """Compute one criterion's verdict from its finding. Raises when ungrounded."""
    surviving_cost, struck = _weigh_cost(finding.cost_claim)

    if finding.smallest_repair is RepairKind.criterion_text:
        return _classify_criterion_side(finding, surviving_cost, struck)
    if finding.smallest_repair is RepairKind.environment_supply:
        return _classify_environment_side(finding, surviving_cost, struck)
    return _classify_no_repair(finding, surviving_cost, struck)


def _classify_criterion_side(
    finding: CriterionFinding,
    surviving_cost: CostMeasurement | None,
    struck: list[StruckGround],
) -> CriterionFeasibility:
    if not _blank(finding.refutation):
        return CriterionFeasibility(
            criterion_id=finding.criterion_id,
            verdict=FeasibilityVerdict.infeasible,
            limit_arm=LimitArm.not_a_limit,
            refutation=finding.refutation,
            struck_grounds=struck,
        )
    if surviving_cost is not None:
        msg = (
            "A measured uneconomic demonstration is environment-side and must "
            "be filed as an environment supply naming the resource"
        )
        raise UngroundedVerdictError(msg, criterion_id=finding.criterion_id)
    if not struck:
        msg = "A criterion-text repair was demanded with no refutation behind it"
        raise UngroundedVerdictError(msg, criterion_id=finding.criterion_id)
    # The only support was a cost claim, and it did not survive weighing:
    # the criterion stands unrefuted and its text is untouched.
    return CriterionFeasibility(
        criterion_id=finding.criterion_id,
        verdict=FeasibilityVerdict.feasible,
        limit_arm=LimitArm.not_a_limit,
        struck_grounds=struck,
    )


def _classify_environment_side(
    finding: CriterionFinding,
    surviving_cost: CostMeasurement | None,
    struck: list[StruckGround],
) -> CriterionFeasibility:
    if _blank(finding.missing_resource):
        msg = "An environment-supply repair was demanded with no resource named"
        raise UngroundedVerdictError(msg, criterion_id=finding.criterion_id)
    arm = (
        LimitArm.uneconomic if surviving_cost is not None else LimitArm.resource_absent
    )
    return CriterionFeasibility(
        criterion_id=finding.criterion_id,
        verdict=FeasibilityVerdict.unverifiable,
        limit_arm=arm,
        missing_resource=finding.missing_resource,
        cost_measurement=surviving_cost,
        struck_grounds=struck,
    )


def _classify_no_repair(
    finding: CriterionFinding,
    surviving_cost: CostMeasurement | None,
    struck: list[StruckGround],
) -> CriterionFeasibility:
    if surviving_cost is not None:
        msg = (
            "A measured uneconomic demonstration is a repair and must be filed "
            "as an environment supply naming the resource"
        )
        raise UngroundedVerdictError(msg, criterion_id=finding.criterion_id)
    return CriterionFeasibility(
        criterion_id=finding.criterion_id,
        verdict=FeasibilityVerdict.feasible,
        limit_arm=LimitArm.not_a_limit,
        struck_grounds=struck,
    )


def minimal_conflicting_subset(
    contradictions: Sequence[Contradiction],
) -> Contradiction | None:
    """The smallest reported contradiction not containing another one.

    Minimal by inclusion first, then by size, then lexicographically — so
    the named subset is deterministic for a given report.
    """
    if not contradictions:
        return None
    sets = [(frozenset(c.criterion_ids), c) for c in contradictions]
    minimal = [
        contradiction
        for ids, contradiction in sets
        if not any(other < ids for other, _ in sets)
    ]
    return min(minimal, key=lambda c: (len(c.criterion_ids), sorted(c.criterion_ids)))


def reconcile(
    criteria: Sequence[AcceptanceCriterion],
    output: CriteriaValidationOutput,
) -> tuple[CriterionFinding, ...]:
    """Pair findings to dispatched ids 1:1. Raises on any correspondence hole."""
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
    missing = [id_ for id_ in dispatched if id_ not in seen]
    if missing or duplicates or unknown:
        msg = "Validator findings do not correspond 1:1 to the dispatched criteria"
        raise CriteriaFanInError(
            msg,
            missing_ids=missing,
            duplicate_ids=duplicates,
            unknown_ids=unknown,
        )
    return tuple(seen[id_] for id_ in dispatched)


def sweep(
    criteria: Sequence[AcceptanceCriterion],
    output: CriteriaValidationOutput,
) -> CriteriaValidation:
    """Reconcile, classify, and fold the conjunction check into one report."""
    findings = reconcile(criteria, output)
    verdicts = [classify_finding(finding) for finding in findings]
    conflict = minimal_conflicting_subset(output.contradictions)
    conjunction = (
        ConjunctionVerdict(satisfiable=True)
        if conflict is None
        else ConjunctionVerdict(
            satisfiable=False,
            conflicting_ids=list(conflict.criterion_ids),
            explanation=conflict.explanation,
        )
    )
    return CriteriaValidation(verdicts=verdicts, conjunction=conjunction)


def regeneration_targets(validation: CriteriaValidation) -> tuple[str, ...]:
    """Ids the regenerator is asked to amend — ``infeasible`` ones only.

    An ``unverifiable`` criterion is regenerated by nobody: its text
    leaves the sweep byte-identical.  The minimal conflicting subset joins
    the targets because an unsatisfiable conjunction is a defect of the
    criteria themselves, settled by editing their text.
    """
    targets = [
        verdict.criterion_id
        for verdict in validation.verdicts
        if verdict.verdict is FeasibilityVerdict.infeasible
    ]
    for id_ in validation.conjunction.conflicting_ids:
        if id_ not in targets:
            targets.append(id_)
    return tuple(targets)


def demands_regeneration(validation: CriteriaValidation) -> bool:
    """Whether this sweep consumes a regeneration round."""
    return bool(regeneration_targets(validation))
