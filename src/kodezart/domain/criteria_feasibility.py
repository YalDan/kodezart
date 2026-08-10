"""The feasibility sweep — reconcile the refuter's report into one record.

The refuter states each criterion's verdict and the evidence behind it.
Nothing here derives a verdict from that evidence: this module pairs
findings to the dispatched ids fail-closed, keeps the smallest reported
contradictions, and projects the result onto the shapes the run persists
and grades on.
"""

from collections.abc import Sequence

from kodezart.domain.errors import CriteriaFanInError
from kodezart.types.domain.criteria import (
    ConjunctionVerdict,
    Contradiction,
    CostMeasurement,
    CriteriaValidation,
    CriteriaValidationOutput,
    CriterionFeasibility,
    CriterionFinding,
    CriterionFlag,
    CriterionVerdict,
    ForbiddenCriterionClass,
    GeneratedCriterion,
    LimitArm,
)


def _limit_arm(
    verdict: CriterionVerdict,
    measurement: CostMeasurement | None,
) -> LimitArm:
    """Which arm an ``unverifiable`` verdict falls on.

    The discriminator is a measurement of a demonstration that ACTUALLY
    RAN and proved unaffordable.  A demonstration a quota, a rate limit or
    a budget prevented from running produces no measurement at all, so it
    reaches the environment-side arm naming the resource.
    """
    if verdict is not CriterionVerdict.unverifiable:
        return LimitArm.not_a_limit
    if measurement is not None and not measurement.affordable:
        return LimitArm.uneconomic
    return LimitArm.resource_absent


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


def _feasibility(finding: CriterionFinding) -> CriterionFeasibility:
    """One finding as the run records it — a copy, not a derivation."""
    measurement = (
        finding.cost_claim.measurement if finding.cost_claim is not None else None
    )
    return CriterionFeasibility(
        criterion_id=finding.criterion_id,
        verdict=finding.verdict,
        limit_arm=_limit_arm(finding.verdict, measurement),
        refutation=finding.refutation,
        missing_resource=finding.missing_resource,
        cost_measurement=measurement,
        flags=_observed_flags(finding),
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

    A KOD-91 workaround rather than architecture.  Server-side strict
    enforcement does not engage for any schema kodezart ships — every one
    uses keywords outside the strict allowlist — so nothing upstream
    rejects a validator return that answers some ids twice and others not
    at all.

    KOD-91 is the expiry, and its deliverable 4 names this channel in
    terms — "The identical guard applies to the KOD-66 validator's verdict
    set: one verdict per dispatched id".  What KOD-91 retires is the
    missing / unknown / duplicate detection here.  What survives it is the
    fail-closed arm, because deliverable 4 "falls through to KOD-11's
    fail-closed grading" once the retries are exhausted.  Detection is
    retained TODAY on a scope boundary — KOD-11 assigns the retrying guard
    to KOD-91 — and not on a mechanism gap.
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
    missing = [id_ for id_ in dispatched if id_ not in seen]
    if missing or duplicates or unknown:
        msg = "Validator output does not correspond 1:1 to the dispatched criteria"
        raise CriteriaFanInError(
            msg,
            missing_ids=missing,
            duplicate_ids=duplicates,
            unknown_ids=unknown,
        )
    return tuple(seen[id_] for id_ in dispatched)


def sweep(
    criteria: Sequence[GeneratedCriterion],
    output: CriteriaValidationOutput,
) -> CriteriaValidation:
    """Reconcile the report and fold the conjunction check into one record."""
    findings = reconcile(criteria, output)
    conflicts = minimal_conflicting_subsets(output.contradictions)
    return CriteriaValidation(
        verdicts=[_feasibility(finding) for finding in findings],
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
