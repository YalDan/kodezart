"""What a deviating writer claims, and the verdict a reconciler reproduces.

A claim is an INPUT. Every field here is something the reconciler can go
and read for itself at the resolved base — a repository address, an exact
quote, the key of another criterion — and nothing here is the writer's
argument for why the departure was reasonable. There is deliberately no
reasoning, rationale or transcript field: a reconciler that received one
would be judging the argument instead of the repository.

The four grounds are separate models rather than one shape with a label,
so the evidence a ground needs cannot be carried over to a ground that
needs different evidence. Relabelling is not a rename here; it is a
different model that will not accept the other's fields.
"""

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel


def _canonical_address(value: str) -> str:
    """Reject anything the repository cannot address as one exact file."""
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or ".." in path.parts
        or "\x00" in value
    ):
        raise ValueError("a repository address is a canonical relative path")
    return value


#: One exact repository address, in the only spelling a tree read accepts.
RepositoryAddress = Annotated[
    str, Field(min_length=1), AfterValidator(_canonical_address)
]

#: Text quoted verbatim from a criterion or from the repository.
Quoted = Annotated[str, Field(min_length=1, pattern=r"\S")]

#: A tracker key — a criterion sub-issue's own identity.
CriterionKey = Annotated[str, Field(min_length=1, pattern=r"\S")]


class AmendmentGround(StrEnum):
    """The exhaustive grounds an amendment may rest on. There is no fifth."""

    UNSATISFIABLE_AT_BASE = "unsatisfiable_at_base"
    MUTUALLY_UNSATISFIABLE = "mutually_unsatisfiable"
    PREMISE_FALSE_AT_BASE = "premise_false_at_base"
    REQUIRES_BREAKING_HOUSE_RULE = "requires_breaking_house_rule"


class AmendmentDecision(StrEnum):
    """The whole verdict domain. UPHELD rests; AMENDED must be argued for.

    There is no third "unclear, proceed anyway" arm: a reconciliation that
    cannot prove its ground has already produced its answer.
    """

    UPHELD = "upheld"
    AMENDED = "amended"


class UpheldReason(StrEnum):
    """Why a claim did not amend — never "no reason", never free prose."""

    SUBJECT_NOT_A_CRITERION = "subject_not_a_criterion"
    GROUND_NOT_REPRODUCED = "ground_not_reproduced"
    EVIDENCE_UNREADABLE = "evidence_unreadable"


class AmendmentClaim(CamelCaseModel):
    """The parts every claim carries: whose criterion, and what instead.

    ``subject`` is the criterion sub-issue's own key, which is the
    criterion's identity: an amendment keeps it, so the evaluator and the
    audit still name the same criterion afterwards.
    """

    model_config = ConfigDict(frozen=True)

    subject: CriterionKey
    amendment: Quoted


class UnsatisfiableAtBase(AmendmentClaim):
    """The criterion names an address the base does not carry at all.

    Reproduced by reading the tree at the base: the criterion's own text
    names the address, and the base has no such file, so no implementation
    of the criterion AS WRITTEN can be performed from this base.
    """

    ground: Literal[AmendmentGround.UNSATISFIABLE_AT_BASE] = (
        AmendmentGround.UNSATISFIABLE_AT_BASE
    )
    target_path: RepositoryAddress


class PremiseFalseAtBase(AmendmentClaim):
    """The criterion asserts the base carries text the base does not carry.

    The complement of ``UnsatisfiableAtBase``: the file IS there and the
    quoted premise is not in it. A ground that needs the file present and
    one that needs it absent cannot both be reproduced by one fixture.
    """

    ground: Literal[AmendmentGround.PREMISE_FALSE_AT_BASE] = (
        AmendmentGround.PREMISE_FALSE_AT_BASE
    )
    path: RepositoryAddress
    premise: Quoted


class MutuallyUnsatisfiable(AmendmentClaim):
    """Subject and one named counter-criterion demand one region differently.

    The minimal conflicting subset is exactly the two criteria named here.
    Both name the anchor, the anchor is really at the base, and each
    criterion demands its own distinct replacement for it — so meeting one
    is refusing the other, and neither is already met at the base.
    """

    ground: Literal[AmendmentGround.MUTUALLY_UNSATISFIABLE] = (
        AmendmentGround.MUTUALLY_UNSATISFIABLE
    )
    counter_subject: CriterionKey
    path: RepositoryAddress
    anchor: Quoted
    subject_demand: Quoted
    counter_demand: Quoted

    @model_validator(mode="after")
    def a_conflict_needs_two_criteria_and_two_demands(self) -> Self:
        if self.counter_subject == self.subject:
            raise ValueError("a criterion is not mutually unsatisfiable with itself")
        if self.subject_demand == self.counter_demand:
            raise ValueError("two demands that agree are not a conflict")
        return self


class RequiresBreakingHouseRule(AmendmentClaim):
    """The criterion demands a construct the house rules at base forbid.

    The rule is read from the repository's own rules document at the base,
    so a rule the repository does not carry — or carried only after the
    base — is not a rule this run is under.
    """

    ground: Literal[AmendmentGround.REQUIRES_BREAKING_HOUSE_RULE] = (
        AmendmentGround.REQUIRES_BREAKING_HOUSE_RULE
    )
    rule: Quoted
    forbidden_construct: Quoted


#: Every claim shape, discriminated by the ground it asserts.
type AnyAmendmentClaim = Annotated[
    UnsatisfiableAtBase
    | PremiseFalseAtBase
    | MutuallyUnsatisfiable
    | RequiresBreakingHouseRule,
    Field(discriminator="ground"),
]


class AmendmentVerdict(CamelCaseModel):
    """One reconciliation, recorded against the criterion it judged.

    A verdict cannot be built with the wrong half: an amendment carries
    the ground it was proved on and no reason, a refusal carries the
    reason it was refused for and no ground.
    """

    model_config = ConfigDict(frozen=True)

    subject: CriterionKey
    decision: AmendmentDecision
    ground: AmendmentGround | None = None
    reason: UpheldReason | None = None

    @model_validator(mode="after")
    def a_decision_carries_exactly_its_own_half(self) -> Self:
        amended = self.decision is AmendmentDecision.AMENDED
        if amended and (self.ground is None or self.reason is not None):
            raise ValueError("an amendment carries the ground it was proved on")
        if not amended and (self.reason is None or self.ground is not None):
            raise ValueError("a refusal carries the reason it was refused for")
        return self
