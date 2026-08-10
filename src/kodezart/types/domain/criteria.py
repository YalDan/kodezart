"""Typed shapes for the acceptance-criteria lifecycle.

A criterion carries a stable identity (``AC-n``, minted at generation
time), the hard-gate/soft-signal ``criterion_class`` the generator
assigns, and — after the sweep — a three-state verdict with its evidence.

``infeasible`` and ``unverifiable`` differ in WHERE THE FAULT LIES: an
``infeasible`` criterion is at fault in its own text and is routed to an
amendment; an ``unverifiable`` one is untouched and names the resource
whose absence blocks its demonstration.  The vocabulary is never collapsed
to a boolean.
"""

from enum import StrEnum
from typing import Annotated, NewType, Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel

#: Pattern every minted criterion identity matches.
CRITERION_ID_PATTERN = r"^AC-[1-9][0-9]*$"

#: A criterion's identity, distinct from the strings it is spelled with.
#:
#: A ``NewType``: a constrained alias stays ``str`` to the type checker, so
#: a union discriminating one minted identity from another collapses and
#: admits any loose string.  Only the minting function constructs one.
CriterionId = NewType("CriterionId", str)

#: A criterion identity carried INSIDE a list, format-checked per element.
#:
#: ``list[CriterionId]`` constrained nothing — the ``NewType`` validates as
#: ``str`` and a field ``min_length`` constrains the LIST, not its members —
#: so ``criterionIds: ["banana"]`` round-tripped intact.
CriterionIdItem = Annotated[CriterionId, Field(pattern=CRITERION_ID_PATTERN)]


class CriterionClass(StrEnum):
    """Whether a criterion is a behavior contract or a shape signal."""

    hard_gate = "hard_gate"
    soft_signal = "soft_signal"


class CriterionVerdict(StrEnum):
    """Three-state feasibility outcome. Never collapsed to a boolean."""

    feasible = "feasible"
    infeasible = "infeasible"
    unverifiable = "unverifiable"


class RepairKind(StrEnum):
    """The COMPLETE repair set a finding may name.

    Exactly three members, and the count is the point: a fourth — elapsed
    time, most temptingly — would let a lack that clears by waiting be
    filed as no lack at all, and the loop would then fail what the sweep
    called settled.
    """

    none = "none"
    criterion_text = "criterion_text"
    environment_supply = "environment_supply"


#: The one repair each verdict names, and the only one it may name.
#:
#: The refuter's instructions state the mapping in terms — "``criterion_text``
#: is ``infeasible``, ``environment_supply`` is ``unverifiable``, ``none`` is
#: ``feasible``" — as prose addressed to a model.  Held here so the pair is
#: checked rather than requested.
_REPAIR_FOR_VERDICT: dict[CriterionVerdict, RepairKind] = {
    CriterionVerdict.feasible: RepairKind.none,
    CriterionVerdict.infeasible: RepairKind.criterion_text,
    CriterionVerdict.unverifiable: RepairKind.environment_supply,
}


def _blank(value: str | None) -> bool:
    """Whether an evidence field carries nothing a reader could act on."""
    return value is None or not value.strip()


class LimitArm(StrEnum):
    """Which arm a limit falls on, discriminated by a measurement.

    ``uneconomic`` requires a measurement of a demonstration that ACTUALLY
    RAN and cost too much.  A quota, a rate limit or a budget that stopped
    it running produces no measurement, so that case is always
    ``resource_absent``: the discriminator is the measurement, never the
    wording of the failure.
    """

    not_a_limit = "not_a_limit"
    resource_absent = "resource_absent"
    uneconomic = "uneconomic"


class CriterionFlag(StrEnum):
    """An observation about a criterion that is NOT a feasibility fault.

    A criterion the base already satisfies is satisfied by every
    implementation, including the empty one; what it lacks is
    DISCRIMINATING POWER, and that is what a flag records.

    The one consequence the harness still draws from a flag is the forced
    ``soft_signal`` downgrade: a flagged criterion leaves the hard-gate
    partition.  It no longer follows that the criterion is ``feasible``,
    that it consumes no regeneration round, or that its text survives —
    the refuter states the verdict now, and a flagged criterion it calls
    ``infeasible`` is regenerated and can reach the pre-loop halt like any
    other.  The template does instruct it to report ``feasible`` there
    ("a demonstration that ran and passed cannot also ground a repair
    demand"); nothing checks that.
    """

    vacuous_at_base = "vacuous_at_base"
    literal_pinning = "literal_pinning"


class ForbiddenCriterionClass(StrEnum):
    """The classes the drafter is instructed never to emit.

    The instruction is prose addressed to a model, so instances reach the
    sweep.  Five of the six describe criteria nothing in the run can
    grade, and one of those reaching the loop fails every iteration and
    burns the budget proving a defect that predates iteration one — the
    refuter returns ``infeasible`` for them and the class is recorded here.

    ``literal_count`` is the exception: the count can be hit, so it is not
    a feasibility fault.  It is FLAGGED and forced to ``soft_signal``.
    """

    pull_request_body = "pull_request_body"
    ci_status = "ci_status"
    merge_state = "merge_state"
    execution_graded = "execution_graded"
    literal_count = "literal_count"
    transient_pipeline_state = "transient_pipeline_state"


class BaseDemonstration(CamelCaseModel):
    """A demonstration of a criterion performed against the repo AT BASE.

    Its existence is the claim that the refuter ran the criterion's own
    check before any work: ``satisfied_at_base`` is an observed result,
    never a prediction, and vacuity is read from it and nothing else.
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
    measured.  Only a measurement of a demonstration that ACTUALLY RAN
    and proved unaffordable reaches the ``uneconomic`` limit arm.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    assertion: str = Field(min_length=1)
    measurement: CostMeasurement | None = None


class GeneratedCriterion(CamelCaseModel):
    """One criterion with a stable identity and a class."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: CriterionId = Field(pattern=CRITERION_ID_PATTERN)
    text: str = Field(min_length=1)
    criterion_class: CriterionClass


class DraftedCriterion(CamelCaseModel):
    """One criterion as the generator emits it — identity is minted later."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    text: str = Field(min_length=1)
    criterion_class: CriterionClass


class CriterionFinding(CamelCaseModel):
    """One refuter finding about one criterion — the validator's raw output.

    The refuter states the ``verdict`` and the evidence behind it: the
    smallest repair that would settle the criterion, and what it
    established.  Evidence is carried so a human can audit "supply a
    Postgres instance"; nothing downstream re-derives the verdict from it.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    criterion_id: CriterionId = Field(pattern=CRITERION_ID_PATTERN)
    verdict: CriterionVerdict
    smallest_repair: RepairKind
    refutation: str | None = None
    missing_resource: str | None = None
    cost_claim: CostClaim | None = None
    base_demonstration: BaseDemonstration | None = None
    pinned_literals: list[str] = Field(default_factory=list)
    forbidden_class: ForbiddenCriterionClass | None = None
    undeclared_switch_arms: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _verdict_carries_its_grounds(self) -> Self:
        """A stated verdict must arrive complete: its repair and its evidence.

        Nothing here derives a verdict from evidence — the refuter states
        it.  What is checked is that the statement is finished, and it is
        checked at construction because this validate call is the only
        thing between the model's JSON and the run: server-side strict
        enforcement does not engage for any schema kodezart ships, and the
        template's own "report the pair consistently" is prose.

        Both failures are collected, so a finding that is inconsistent AND
        ungrounded reports both rather than whichever was tested first.
        """
        failures: list[str] = []
        required = _REPAIR_FOR_VERDICT[self.verdict]
        if self.smallest_repair is not required:
            failures.append(
                f"verdict {self.verdict.value} names {required.value} as its "
                f"smallest repair, not {self.smallest_repair.value}"
            )
        if self.verdict is CriterionVerdict.infeasible and _blank(self.refutation):
            failures.append("verdict infeasible requires a refutation")
        if self.verdict is CriterionVerdict.unverifiable and _blank(
            self.missing_resource
        ):
            failures.append("verdict unverifiable requires a missingResource")
        if failures:
            msg = "; ".join(failures)
            raise ValueError(msg)
        return self


class Contradiction(CamelCaseModel):
    """A subset of criterion ids whose conjunction admits no implementation."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    criterion_ids: list[CriterionIdItem] = Field(min_length=2)
    explanation: str = Field(min_length=1)


class CriteriaValidationOutput(CamelCaseModel):
    """Structured output of the validator agent."""

    findings: list[CriterionFinding] = Field(min_length=1)
    contradictions: list[Contradiction] = Field(default_factory=list)


class CriterionFeasibility(CamelCaseModel):
    """One criterion's verdict as the run records it, with its evidence."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    criterion_id: CriterionId = Field(pattern=CRITERION_ID_PATTERN)
    verdict: CriterionVerdict
    limit_arm: LimitArm = LimitArm.not_a_limit
    refutation: str | None = None
    missing_resource: str | None = None
    cost_measurement: CostMeasurement | None = None
    flags: list[CriterionFlag] = Field(default_factory=list)
    forbidden_class: ForbiddenCriterionClass | None = None
    undeclared_switch_arms: list[str] = Field(default_factory=list)


class ConjunctionVerdict(CamelCaseModel):
    """Whether the whole set is jointly satisfiable, and by whom it is not.

    Every retained conflict is carried, each keeping its own ids and its
    own explanation: a set can be unsatisfiable in two unrelated ways at
    once, and folding them into one id list and one joined sentence loses
    which ids belong to which conflict.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    satisfiable: bool
    contradictions: list[Contradiction] = Field(default_factory=list)


class CriteriaValidation(CamelCaseModel):
    """The sweep's full report over one criteria set."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    verdicts: list[CriterionFeasibility] = Field(min_length=1)
    conjunction: ConjunctionVerdict


class ValidatedCriterion(CamelCaseModel):
    """One persisted criterion: identity, text, class, verdict."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: CriterionId = Field(pattern=CRITERION_ID_PATTERN)
    text: str = Field(min_length=1)
    criterion_class: CriterionClass
    feasibility: CriterionFeasibility


class CriteriaArtifact(CamelCaseModel):
    """The ``.kodezart/criteria.json`` document.

    Replaces the bare ``TypeAdapter[list[str]]`` the persister used to
    dump, so a consumer reads identity, class and verdict rather than
    guessing from position in a list.
    """

    model_config = ConfigDict(populate_by_name=True)

    criteria: list[ValidatedCriterion] = Field(min_length=1)
    conjunction: ConjunctionVerdict


class CriterionFailure(CamelCaseModel):
    """A failed criterion as the HARNESS records it.

    ``text`` is the harness's own stored text looked up by id, never the
    evaluator's echo.  Re-injecting the echo let a whitespace-normalised or
    backslash-mangled criterion drift between iterations while the loop
    believed it was re-asking the same question.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    criterion_id: CriterionId = Field(pattern=CRITERION_ID_PATTERN)
    text: str = Field(min_length=1)
    reasoning: str = Field(min_length=1)
