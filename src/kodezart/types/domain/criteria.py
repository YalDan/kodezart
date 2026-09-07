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

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, NewType, Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel

#: The prefix every minted criterion identity carries — one owner.
#:
#: The pattern below derives from it, and so does the minting function in
#: :mod:`kodezart.domain.criteria`, so no second surface spells the scheme
#: and the two cannot drift apart into a pattern nothing minted matches.
CRITERION_ID_PREFIX = "AC-"

#: Pattern every minted criterion identity matches.
CRITERION_ID_PATTERN = rf"^{CRITERION_ID_PREFIX}[1-9][0-9]*$"

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


class CriterionFlag(StrEnum):
    """An observation about a criterion that is NOT a feasibility fault.

    A criterion the base already satisfies is satisfied by every
    implementation, including the empty one; what it lacks is
    DISCRIMINATING POWER, and that is what a flag records.

    The consequence the harness draws from a flag is the forced
    ``soft_signal`` downgrade: a flagged criterion leaves the hard-gate
    partition.  The template's instruction that a demonstration which ran
    and passed cannot also ground a repair demand is checked, not merely
    requested: the sweep's derivation raises on a ``vacuous_at_base``
    observation paired with any repair, so a criterion the base satisfies
    consumes no regeneration round and reaches no halt.
    """

    vacuous_at_base = "vacuous_at_base"
    literal_pinning = "literal_pinning"


class LimitArm(StrEnum):
    """Which kind of limit blocks a demonstration — or that none does.

    ``resource_absent`` and ``uneconomic`` partition the environment-side
    arm by the presence of a measurement: a demonstration a limit stopped
    from running has no measurement of its cost, a demonstration that ran
    and priced itself uneconomic has one.  A derivation component, never a
    persisted field — the wire carries the same distinction as
    ``costMeasurement``'s presence.
    """

    not_a_limit = "not_a_limit"
    resource_absent = "resource_absent"
    uneconomic = "uneconomic"


@dataclass(frozen=True, slots=True)
class DerivedFeasibility:
    """What one finding's evidence alone derives.

    The harness's own classification, computed beside the stated verdict
    and never from it.  The sweep refuses any finding whose stated verdict
    its evidence does not derive, so the record a run proceeds on always
    carries the pair in agreement.
    """

    verdict: CriterionVerdict
    limit_arm: LimitArm
    flags: tuple[CriterionFlag, ...]


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

    command: str = Field(
        min_length=1,
        description="The command you ran at the base ref to establish this.",
    )
    satisfied_at_base: bool = Field(
        description="Whether the criterion already holds before any work is done.",
    )


class CostMeasurement(CamelCaseModel):
    """A measurement of a demonstration that ACTUALLY RAN.

    Its existence is the claim that the demonstration completed; a
    demonstration a limit prevented from running has no instance of this
    type at all.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    observed: str = Field(
        min_length=1,
        description="What the measurement returned, quoted from the output you ran.",
    )
    affordable: bool = Field(
        description="Whether the measured cost is one an implementer can pay.",
    )


class CostClaim(CamelCaseModel):
    """An assertion about the price of demonstrating a criterion.

    ``measurement`` is ``None`` when the cost was argued rather than
    measured, and the refuter is told an argued cost may not carry a
    verdict.  The sweep's derivation weighs the claim rather than reading
    it past: an unmeasured claim and an affordable measurement are both
    struck and support no repair; only a measured, uneconomic one
    survives, and it is environment-side.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    assertion: str = Field(
        min_length=1,
        description="The cost the criterion is claimed to impose.",
    )
    measurement: CostMeasurement | None = Field(
        default=None,
        description=(
            "The measurement backing the assertion; absent when none was taken."
        ),
    )


class GeneratedCriterion(CamelCaseModel):
    """One criterion with a stable identity and a class."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: CriterionId = Field(pattern=CRITERION_ID_PATTERN)
    text: str = Field(min_length=1)
    criterion_class: CriterionClass


class DraftedCriterion(CamelCaseModel):
    """One criterion as the generator emits it — identity is minted later."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    text: str = Field(
        min_length=1,
        description=(
            "The criterion, stated so a later reviewer who sees only this "
            "text, the repository and a changeset can decide it."
        ),
    )
    criterion_class: CriterionClass = Field(
        description="Whether failing this criterion blocks the run or only flags it.",
    )


class CriterionFinding(CamelCaseModel):
    """One refuter finding about one criterion — the validator's raw output.

    The refuter states the ``verdict`` and the evidence behind it: the
    smallest repair that would settle the criterion, and what it
    established.  Evidence is carried so a human can audit "supply a
    Postgres instance", and so the sweep can ground the statement:
    ``classify_finding`` derives its own verdict from the evidence alone,
    and a stated verdict the evidence does not derive is refused.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    criterion_id: CriterionId = Field(
        pattern=CRITERION_ID_PATTERN,
        description=(
            "The dispatched criterion's id, echoed exactly. Return one finding "
            "per dispatched id and invent none."
        ),
    )
    verdict: CriterionVerdict = Field(
        description=(
            "Feasible, infeasible, or unverifiable — never folded into each other."
        ),
    )
    smallest_repair: RepairKind = Field(
        description=(
            "The smallest repair that would settle the criterion, bound to "
            "the verdict: feasible names none, infeasible names "
            "criterion_text (the criterion's own text must change), "
            "unverifiable names environment_supply (something absent must "
            "be supplied to the environment — not a text change). Any "
            "other pairing is refused."
        ),
    )
    refutation: str | None = Field(
        default=None,
        description=(
            "Evidence an implementer could not overturn, for an infeasible "
            "verdict. An unproven suspicion is not a refutation."
        ),
    )
    missing_resource: str | None = Field(
        default=None,
        description=(
            "The file, symbol, binding or rule that must be SUPPLIED before "
            "the criterion can be decided. Its absence is a lack in the "
            "environment, not a fault in the criterion's text: it pairs "
            "with unverifiable and environment_supply, never with "
            "infeasible."
        ),
    )
    cost_claim: CostClaim | None = Field(
        default=None,
        description="A cost the criterion imposes, with the measurement behind it.",
    )
    base_demonstration: BaseDemonstration | None = Field(
        default=None,
        description="Evidence that the criterion already holds at the base ref.",
    )
    pinned_literals: list[str] = Field(
        default_factory=list,
        description=(
            "Exact counts, paths or formatting the criterion pins that a correct "
            "refactor may change."
        ),
    )
    forbidden_class: ForbiddenCriterionClass | None = Field(
        default=None,
        description="The forbidden criterion class this one falls into, when it does.",
    )
    undeclared_switch_arms: list[str] = Field(
        default_factory=list,
        description=(
            "Cases the criterion names that the declared type does not "
            "have. Naming ANY arm here makes the criterion infeasible — "
            "the fault is in the criterion's own text, and nothing "
            "supplied to a runner makes an arm exist. A type that could "
            "not be found at all is a missingResource, not an undeclared "
            "arm."
        ),
    )

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

    criterion_ids: list[CriterionIdItem] = Field(
        min_length=2,
        description="The minimal subset of criterion ids that cannot all hold at once.",
    )
    explanation: str = Field(
        min_length=1,
        description="Why no single implementation satisfies that subset.",
    )


class CriteriaValidationOutput(CamelCaseModel):
    """Structured output of the validator agent."""

    findings: list[CriterionFinding] = Field(
        min_length=1,
        description=(
            "Exactly one finding per dispatched criterion id, covering every id."
        ),
    )
    contradictions: list[Contradiction] = Field(
        default_factory=list,
        description=(
            "Subsets of individually feasible criteria that cannot hold together."
        ),
    )


class CriterionFeasibility(CamelCaseModel):
    """One criterion's verdict as the run records it, with its evidence.

    ``undeclared_switch_arms``, ``forbidden_class`` and ``cost_measurement``
    have no reader in ``src`` and are not meant to have one: they are
    carried FOR A HUMAN, and both surfaces that carry them were measured
    rather than assumed.

    This model is serialized whole into the ``workflow_criteria_validation``
    SSE frame — the handler's own ``model_dump(by_alias=True,
    exclude_none=True)``, a documented event — and written verbatim into
    ``.kodezart/criteria.json``, which is committed and pushed to the ralph
    branch and deleted from nowhere.  (``open_pr`` strips ``.kodezart/``
    from the feature branch tip, so the pull request's file tree does not
    carry it; the ralph branch and the merge history do.)  ``exclude_none``
    is what makes an absence meaningful: present means observed.

    A field here that reached neither surface would be an orphan write and
    would go, as ``cost_claim_struck`` did.  ``limit_arm`` left THIS model
    on the same ground and did not leave the codebase: it survives on
    :class:`DerivedFeasibility` as a derivation component, because the wire
    carries the distinction it drew as ``cost_measurement``'s presence.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    criterion_id: CriterionId = Field(pattern=CRITERION_ID_PATTERN)
    verdict: CriterionVerdict
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


class FanInReport(CamelCaseModel):
    """The non-permutation a bounded re-dispatch could not clear.

    Carried on an emitted event ONLY when the guard's attempts were spent
    and the run graded a set that does not correspond 1:1 to the
    dispatched ids.  Absent means the returned set was a permutation.

    Not a flag: "the guard cleared" and "the guard could not clear it" are
    different facts, and the second one has to name WHICH ids were wrong
    and what it cost to find out — a reader who only learns that grading
    was fail-closed cannot tell a model that answered a different question
    from criteria the work genuinely failed.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    missing_ids: list[CriterionIdItem] = Field(default_factory=list)
    unknown_ids: list[CriterionIdItem] = Field(default_factory=list)
    duplicate_ids: list[CriterionIdItem] = Field(default_factory=list)
    dispatched_count: int = Field(ge=1)
    attempts: int = Field(ge=1)


class ContractBreach(CamelCaseModel):
    """One refused response, named by the class that refused it."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    breach_class: str = Field(min_length=1)
    detail: str = Field(min_length=1)


class ContractCorrection(CamelCaseModel):
    """A contract violation the node corrected in flight.

    Carried on the criteria-validation event ONLY when a response was
    refused and a later one conformed: absent means the first answer
    conformed, and a refusal that never corrects raises instead of
    reaching this event at all.  A run that had to argue with its
    validator costs whole judgment sessions, and a reader who cannot see
    WHICH rule was broken cannot tell a model that misread the contract
    from criteria the sweep genuinely refuses.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    breaches: list[ContractBreach] = Field(min_length=1)
    attempts: int = Field(ge=1)
