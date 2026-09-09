"""The vocabulary a criterion amendment is claimed, ruled and recorded in.

A writer that deviates from a criterion does not get to rule on its own
claim.  What it says is a CLAIM: the subject it departed from, what the
departure was, the ground it says excuses it, and the repository
addresses it offers as evidence.  What comes back is a VERDICT, and the
verdict rests only on what the reconciler read back for itself at the
resolved base.

The decision domain has exactly two arms.  ``UPHELD`` is the resting
state — the criterion stands — and it needs no proof; ``AMENDED`` is
reachable only through a ground whose evidence the reconciler
reproduced.  There is deliberately no third "unclear, proceed anyway"
arm: a claim nobody could substantiate is a claim that did not
substantiate, and that is already one of the two.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.criterion_ref import CriterionRef


class AmendmentGround(StrEnum):
    """Every ground an amendment may rest on, and there is no other.

    An enumeration rather than free text because the set is the whole
    point: a ground outside these four is not a weaker ground, it is not
    a ground, and a string field would let one in by being spelled.
    """

    #: No change to the repository at the resolved base can satisfy the
    #: criterion as written.
    UNSATISFIABLE_AT_BASE = "unsatisfiable_at_base"
    #: The criterion and another criterion of the same issue cannot both
    #: be satisfied.
    MUTUALLY_UNSATISFIABLE = "mutually_unsatisfiable"
    #: The criterion states something about the repository at the
    #: resolved base that is not so.
    PREMISE_FALSE_AT_BASE = "premise_false_at_base"
    #: The criterion can be satisfied only by breaking a house rule the
    #: repository states.
    REQUIRES_BREAKING_HOUSE_RULE = "requires_breaking_house_rule"


#: How each ground is reproduced at the resolved base: whether the
#: repository has to CARRY the quoted text there, or whether the ground is
#: exactly the measured absence of it at a readable address.
#:
#: Three grounds are claims ABOUT something the repository states, so the
#: statement has to be found.  ``PREMISE_FALSE_AT_BASE`` is the one claim
#: about what the repository does NOT state, and reproducing it is reading
#: the blob the premise points at and not finding what the premise says is
#: there.  A read that fails reproduces nothing either way — absence is a
#: measurement, and a measurement needs a blob that was read.
#:
#: Total over the enum by construction, and asserted so: a fifth ground
#: cannot be added without deciding how it is reproduced.
QUOTE_CARRIED_AT_BASE: Mapping[AmendmentGround, bool] = MappingProxyType(
    {
        AmendmentGround.UNSATISFIABLE_AT_BASE: True,
        AmendmentGround.MUTUALLY_UNSATISFIABLE: True,
        AmendmentGround.PREMISE_FALSE_AT_BASE: False,
        AmendmentGround.REQUIRES_BREAKING_HOUSE_RULE: True,
    }
)


class AmendmentDecision(StrEnum):
    """The two arms a reconciled claim can end in."""

    #: The criterion stands exactly as written.
    UPHELD = "upheld"
    #: The criterion is to be amended on a reproduced ground.
    AMENDED = "amended"


class GroundEvidence(CamelCaseModel):
    """One address in the repository, and the text expected to be at it.

    An ADDRESS, never a conclusion: a claimant offering this says "read
    here", and the reconciler is the one that reads.  The pair is what
    makes reproduction mechanical — the path selects the blob at the
    resolved base and the quote is looked for in that blob's own bytes.
    """

    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class AmendmentClaim(CamelCaseModel):
    """A writer's assertion that a criterion cannot stand as written.

    Everything here is an INPUT.  ``asserted_ground`` says which of the
    four the claimant believes it is and ``asserted_evidence`` says where
    to look; neither is a finding, and no field carries the claimant's
    reasoning or the transcript of the session that produced it.  A claim
    offering no address at all is a legal claim — it is simply one
    nothing can reproduce.

    ``counter_subject`` belongs to exactly one ground.  A mutual
    unsatisfiability is a claim about a PAIR, so the claim has to say
    which pair; every other ground is about the subject alone, and naming
    a second criterion there would be a field with no meaning rather than
    an ignored one.
    """

    model_config = ConfigDict(frozen=True)

    subject_id: CriterionRef
    deviation: str = Field(min_length=1)
    asserted_ground: AmendmentGround
    asserted_evidence: tuple[GroundEvidence, ...] = ()
    counter_subject: CriterionRef | None = None

    @model_validator(mode="after")
    def _only_a_mutual_claim_names_a_pair(self) -> Self:
        mutual = self.asserted_ground is AmendmentGround.MUTUALLY_UNSATISFIABLE
        if mutual and self.counter_subject is None:
            msg = "a mutually unsatisfiable claim names the other criterion"
            raise ValueError(msg)
        if not mutual and self.counter_subject is not None:
            msg = "only a mutually unsatisfiable claim names a counter subject"
            raise ValueError(msg)
        return self


class AmendmentVerdict(CamelCaseModel):
    """What the reconciler ruled, recorded against the subject's identity.

    ``subject_id`` is required in both arms: a refusal that cannot say
    what it refused is not a record of anything, and the repeat count a
    loop reports is a count PER SUBJECT.

    The two arms carry different fields and the model refuses the
    mixtures: ``AMENDED`` names its ground and the evidence reproduced
    for it, ``UPHELD`` names neither.  An amendment whose ground went
    missing, or a refusal that somehow carries reproduced evidence,
    cannot be constructed at all.
    """

    model_config = ConfigDict(frozen=True)

    subject_id: CriterionRef
    decision: AmendmentDecision
    ground: AmendmentGround | None = None
    reproduced: tuple[GroundEvidence, ...] = ()

    @model_validator(mode="after")
    def _each_arm_carries_exactly_its_own_grounds(self) -> Self:
        amended = self.decision is AmendmentDecision.AMENDED
        if amended and (self.ground is None or not self.reproduced):
            msg = "an amended verdict names its ground and the evidence for it"
            raise ValueError(msg)
        if not amended and (self.ground is not None or self.reproduced):
            msg = "an upheld verdict rests on no ground and no evidence"
            raise ValueError(msg)
        return self
