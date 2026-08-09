"""Criterion identity — the ``AC-n`` scheme minted once, harness-side.

Identity is assigned by the harness at generation time, never by a model:
a stable id is exactly the thing an echoed string cannot be.  Everything
downstream — the feasibility sweep, the persisted artifact, evaluator
dispatch and grading, and the re-injected feedback text — keys off these
ids and never off criterion text.
"""

import re
from collections.abc import Mapping, Sequence

from kodezart.types.domain.criteria import (
    CRITERION_ID_PATTERN,
    AcceptanceCriterion,
    CriteriaArtifact,
    CriteriaValidation,
    CriterionClassification,
    CriterionFeasibility,
    CriterionId,
    DraftedCriterion,
    ValidatedCriterion,
)

_ID_PREFIX = "AC-"


def mint_criterion_id(index: int) -> CriterionId:
    """The identity of the criterion at 1-based *index*.

    The single construction site for a ``CriterionId``: it formats the
    ``AC-n`` shape and validates what it formatted, so no other surface
    needs to know the shape and none may invent one.
    """
    if index < 1:
        msg = f"Criterion positions are 1-based; got {index}"
        raise ValueError(msg)
    minted = f"{_ID_PREFIX}{index}"
    if re.fullmatch(CRITERION_ID_PATTERN, minted) is None:
        msg = f"Minted criterion identity does not match the scheme: {minted!r}"
        raise ValueError(msg)
    return CriterionId(minted)


def mint_criteria(
    drafted: Sequence[DraftedCriterion],
) -> tuple[AcceptanceCriterion, ...]:
    """Assign ``AC-n`` identities to *drafted* in emission order."""
    return tuple(
        AcceptanceCriterion(
            id=mint_criterion_id(index),
            text=criterion.text,
            classification=criterion.classification,
        )
        for index, criterion in enumerate(drafted, start=1)
    )


def criteria_by_id(
    criteria: Sequence[AcceptanceCriterion],
) -> Mapping[CriterionId, AcceptanceCriterion]:
    """Index *criteria* by identity — the harness's own text, by id."""
    return {criterion.id: criterion for criterion in criteria}


def effective_classification(
    criterion: AcceptanceCriterion,
    feasibility: CriterionFeasibility,
) -> CriterionClassification:
    """The classification a criterion carries AFTER the sweep.

    A flagged criterion is forced to ``soft_signal``: a criterion the base
    already satisfies, or one pinned to literals, cannot gate anything, so
    it must not sit in the hard-gate partition the accept gate's
    arithmetic reads.  The downgrade is computed from the sweep's flags
    and is never a judgement; an unflagged criterion keeps the
    classification the generator assigned, byte for byte.
    """
    if feasibility.flags:
        return CriterionClassification.soft_signal
    return criterion.classification


def build_artifact(
    criteria: Sequence[AcceptanceCriterion],
    validation: CriteriaValidation,
) -> CriteriaArtifact:
    """Fold criteria and their sweep verdicts into the persisted document."""
    verdicts: dict[CriterionId, CriterionFeasibility] = {
        verdict.criterion_id: verdict for verdict in validation.verdicts
    }
    return CriteriaArtifact(
        criteria=[
            ValidatedCriterion(
                id=criterion.id,
                text=criterion.text,
                classification=effective_classification(
                    criterion,
                    verdicts[criterion.id],
                ),
                feasibility=verdicts[criterion.id],
            )
            for criterion in criteria
        ],
        conjunction=validation.conjunction,
    )
