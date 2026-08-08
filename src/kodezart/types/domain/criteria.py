"""Typed shapes for the acceptance-criteria lifecycle.

A criterion stops being a bare string here.  It carries a stable identity
(``AC-n``, minted once at generation time), the hard-gate/soft-signal
classification the generator assigns, and — after the feasibility sweep —
a three-state verdict with the evidence that produced it.

The three-state vocabulary is load-bearing and is never collapsed to a
boolean.  ``infeasible`` and ``unverifiable`` differ in WHERE THE FAULT
LIES: an ``infeasible`` criterion is at fault in its own text and is
routed to an amendment; an ``unverifiable`` criterion is untouched and
names the resource whose absence blocks its demonstration.

``RepairKind`` is the whole of that distinction, expressed as a closed
set: the smallest repair that settles a criterion is either an edit to
its own text or a supply to the environment, and there is no third
member.  Elapsed time is not a repair — waiting is the absence of one —
so a lack that clears by waiting is an absent resource here and clears
empirically on a later grading rather than by a verdict issued now.
"""

from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel

#: Pattern every minted criterion identity matches.
CRITERION_ID_PATTERN = r"^AC-[1-9][0-9]*$"


class CriterionClassification(StrEnum):
    """Whether a criterion is a behavior contract or a shape signal."""

    hard_gate = "hard_gate"
    soft_signal = "soft_signal"


class FeasibilityVerdict(StrEnum):
    """Three-state feasibility outcome. Never collapsed to a boolean."""

    feasible = "feasible"
    infeasible = "infeasible"
    unverifiable = "unverifiable"


class RepairKind(StrEnum):
    """The COMPLETE repair set the feasibility gate ranges over.

    Exactly three members, and the count is a contract: an implementation
    that admits a third repair — elapsed time, most temptingly — lets the
    gate and the loop return opposite readings on one criterion.
    """

    none = "none"
    criterion_text = "criterion_text"
    environment_supply = "environment_supply"


class LimitArm(StrEnum):
    """Which arm a limit falls on, discriminated by a measurement.

    ``uneconomic`` requires a measurement of a demonstration that ACTUALLY
    RAN.  A demonstration a quota, a rate limit or a budget prevented from
    running produces no such measurement and is therefore always
    ``resource_absent`` — the discriminator is the presence of a
    measurement, never the wording of a failure.
    """

    not_a_limit = "not_a_limit"
    resource_absent = "resource_absent"
    uneconomic = "uneconomic"


class StruckGround(StrEnum):
    """Why a stated ground was struck rather than acted on."""

    unmeasured_cost = "unmeasured_cost"
    affordable_cost = "affordable_cost"


class CriterionFlag(StrEnum):
    """An observation about a criterion that is NOT a feasibility fault.

    A criterion the base already satisfies is satisfied by every
    implementation, including the empty one, so it is ``feasible`` by the
    vocabulary's own definition.  What it lacks is DISCRIMINATING POWER,
    and that is what a flag records.  A flagged criterion consumes no
    regeneration round, reaches no halt, and leaves the sweep with its
    text byte-identical — but it can no longer sit in the hard-gate
    partition the accept gate's arithmetic reads.
    """

    vacuous_at_base = "vacuous_at_base"
    literal_pinning = "literal_pinning"


class BaseDemonstration(CamelCaseModel):
    """A demonstration of a criterion performed against the repo AT BASE.

    Its existence is the claim that the refuter ran the criterion's own
    check before any work: ``satisfied_at_base`` is the observed result,
    never a prediction.  Vacuity is computed from this and from nothing
    else — a criterion is not called vacuous because it reads that way.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    command: str = Field(min_length=1)
    satisfied_at_base: bool


class CostMeasurement(CamelCaseModel):
    """A measurement of a demonstration that ACTUALLY RAN.

    Its existence is the claim that the demonstration completed; a
    demonstration a limit prevented from running has no instance of this
    type at all.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    observed: str = Field(min_length=1)
    affordable: bool


class CostClaim(CamelCaseModel):
    """An assertion about the price of demonstrating a criterion.

    ``measurement`` is ``None`` when the cost was argued rather than
    measured.  An argued cost may not produce ``infeasible``.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    assertion: str = Field(min_length=1)
    measurement: CostMeasurement | None = None


class AcceptanceCriterion(CamelCaseModel):
    """One criterion with a stable identity and a classification."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: str = Field(pattern=CRITERION_ID_PATTERN)
    text: str = Field(min_length=1)
    classification: CriterionClassification


class DraftedCriterion(CamelCaseModel):
    """One criterion as the generator emits it — identity is minted later."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    text: str = Field(min_length=1)
    classification: CriterionClassification


class CriterionFinding(CamelCaseModel):
    """One refuter finding about one criterion — the validator's raw output.

    The finding states the SMALLEST REPAIR that would settle the criterion
    and the evidence supporting it.  The verdict is computed from this,
    never asserted by the refuter.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    criterion_id: str = Field(pattern=CRITERION_ID_PATTERN)
    smallest_repair: RepairKind
    refutation: str | None = None
    missing_resource: str | None = None
    cost_claim: CostClaim | None = None
    base_demonstration: BaseDemonstration | None = None
    pinned_literals: list[str] = Field(default_factory=list)


class Contradiction(CamelCaseModel):
    """A subset of criterion ids whose conjunction admits no implementation."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    criterion_ids: list[str] = Field(min_length=2)
    explanation: str = Field(min_length=1)


class CriteriaValidationOutput(CamelCaseModel):
    """Structured output of the validator agent."""

    findings: list[CriterionFinding] = Field(min_length=1)
    contradictions: list[Contradiction] = Field(default_factory=list)


class CriterionFeasibility(CamelCaseModel):
    """The computed verdict for one criterion, with the evidence behind it."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    criterion_id: str = Field(pattern=CRITERION_ID_PATTERN)
    verdict: FeasibilityVerdict
    limit_arm: LimitArm = LimitArm.not_a_limit
    refutation: str | None = None
    missing_resource: str | None = None
    cost_measurement: CostMeasurement | None = None
    struck_grounds: list[StruckGround] = Field(default_factory=list)
    flags: list[CriterionFlag] = Field(default_factory=list)


class ConjunctionVerdict(CamelCaseModel):
    """Whether the whole set is jointly satisfiable, and by whom it is not."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    satisfiable: bool
    conflicting_ids: list[str] = Field(default_factory=list)
    explanation: str | None = None


class CriteriaValidation(CamelCaseModel):
    """The sweep's full report over one criteria set."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    verdicts: list[CriterionFeasibility] = Field(min_length=1)
    conjunction: ConjunctionVerdict


class ValidatedCriterion(CamelCaseModel):
    """One persisted criterion: identity, text, classification, verdict."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: str = Field(pattern=CRITERION_ID_PATTERN)
    text: str = Field(min_length=1)
    classification: CriterionClassification
    feasibility: CriterionFeasibility


class CriteriaArtifact(CamelCaseModel):
    """The ``.kodezart/criteria.json`` document.

    Replaces the bare ``TypeAdapter[list[str]]`` the persister used to
    dump: downstream consumers read identity, classification and verdict
    instead of guessing from position in a list.
    """

    model_config = ConfigDict(populate_by_name=True)

    criteria: list[ValidatedCriterion] = Field(min_length=1)
    conjunction: ConjunctionVerdict


class CriterionFailure(CamelCaseModel):
    """A failed criterion as the HARNESS records it.

    ``text`` is the harness's own stored criterion text looked up by id —
    never the evaluator's echo.  Re-injecting the echo is what let a
    whitespace-normalised or backslash-mangled criterion drift between
    iterations while the loop believed it was re-asking the same question.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    criterion_id: str = Field(pattern=CRITERION_ID_PATTERN)
    text: str = Field(min_length=1)
    reasoning: str = Field(min_length=1)
