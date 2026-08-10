"""Typed shapes for the acceptance-criteria lifecycle.

A criterion stops being a bare string here.  It carries a stable identity
(``AC-n``, minted once at generation time), the hard-gate/soft-signal
``criterion_class`` the generator assigns, and — after the sweep —
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
from typing import Annotated, NewType

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel

#: Pattern every minted criterion identity matches.
CRITERION_ID_PATTERN = r"^AC-[1-9][0-9]*$"

#: A criterion's identity, distinct from the strings it is spelled with.
#:
#: A ``NewType`` rather than a constrained ``str`` alias: an alias validates
#: the format and remains ``str`` to the type checker, so a union that
#: discriminates a criterion identity from another minted identity collapses
#: to ``str`` and admits any loose string.  Only the minting function
#: constructs one, so a value reaching a criterion-id annotation came from
#: the harness and not from a model's echo.
CriterionId = NewType("CriterionId", str)

#: A criterion identity carried INSIDE a list, format-checked per element.
#:
#: ``list[CriterionId]`` constrains nothing: ``CriterionId`` is a ``NewType``
#: over ``str``, so a list annotation validates as ``list[str]`` and a
#: ``min_length`` on the field constrains the LIST rather than its members.
#: Every scalar identity field carries the pattern; a list field carries it
#: here, on the element.
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


class ForbiddenCriterionClass(StrEnum):
    """The classes the drafter is instructed never to emit.

    The instruction is best-effort prose and the drafter is a model, so
    an instance reaching the sweep is expected rather than exceptional.
    What must NOT happen is one reaching the loop: five of the six
    describe criteria nothing in the run can grade, and a criterion the
    loop cannot grade fails every iteration and burns the budget proving
    a defect that existed before iteration one.

    ``literal_count`` is the exception and is not a feasibility fault:
    the count can be hit.  It is brittle, so it is FLAGGED and forced to
    ``soft_signal`` rather than regenerated.
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

    The finding states the SMALLEST REPAIR that would settle the criterion
    and the evidence supporting it.  The verdict is computed from this,
    never asserted by the refuter.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    criterion_id: CriterionId = Field(pattern=CRITERION_ID_PATTERN)
    smallest_repair: RepairKind
    refutation: str | None = None
    missing_resource: str | None = None
    cost_claim: CostClaim | None = None
    base_demonstration: BaseDemonstration | None = None
    pinned_literals: list[str] = Field(default_factory=list)
    forbidden_class: ForbiddenCriterionClass | None = None
    undeclared_switch_arms: list[str] = Field(default_factory=list)


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
    """The computed verdict for one criterion, with the evidence behind it."""

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
    dump: downstream consumers read identity, class and verdict
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

    criterion_id: CriterionId = Field(pattern=CRITERION_ID_PATTERN)
    text: str = Field(min_length=1)
    reasoning: str = Field(min_length=1)
