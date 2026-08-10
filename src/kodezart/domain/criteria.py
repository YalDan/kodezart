"""Criterion identity — the ``AC-n`` scheme minted once, harness-side.

Identity is assigned by the harness at generation time, never by a model:
a stable id is exactly the thing an echoed string cannot be.  Everything
downstream — the feasibility sweep, the persisted artifact, evaluator
dispatch and grading, and the re-injected feedback text — keys off these
ids and never off criterion text.
"""

from collections.abc import Sequence

from kodezart.types.domain.criteria import (
    CriteriaArtifact,
    CriteriaValidation,
    CriterionClass,
    CriterionFeasibility,
    CriterionId,
    DraftedCriterion,
    GeneratedCriterion,
    ValidatedCriterion,
)

_ID_PREFIX = "AC-"


def mint_criterion_id(index: int) -> CriterionId:
    """The identity of the criterion at 1-based *index*.

    The single construction site for a ``CriterionId``: it formats the
    ``AC-n`` shape, so no other surface needs to know the shape and none
    may invent one.
    """
    if index < 1:
        msg = f"Criterion positions are 1-based; got {index}"
        raise ValueError(msg)
    return CriterionId(f"{_ID_PREFIX}{index}")


def mint_criteria(
    drafted: Sequence[DraftedCriterion],
) -> tuple[GeneratedCriterion, ...]:
    """Assign ``AC-n`` identities to *drafted* in emission order."""
    return tuple(
        GeneratedCriterion(
            id=mint_criterion_id(index),
            text=criterion.text,
            criterion_class=criterion.criterion_class,
        )
        for index, criterion in enumerate(drafted, start=1)
    )


def effective_criterion_class(
    criterion: GeneratedCriterion,
    feasibility: CriterionFeasibility,
) -> CriterionClass:
    """The ``criterion_class`` a criterion carries AFTER the sweep.

    A flagged criterion is forced to ``soft_signal``: a criterion the base
    already satisfies, or one pinned to literals, cannot gate anything, so
    it must not sit in the hard-gate partition the accept gate's
    arithmetic reads.  The downgrade is computed from the sweep's flags
    and is never a judgement; an unflagged criterion keeps the
    class the generator assigned, byte for byte.
    """
    if feasibility.flags:
        return CriterionClass.soft_signal
    return criterion.criterion_class


def build_artifact(
    criteria: Sequence[GeneratedCriterion],
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
                criterion_class=effective_criterion_class(
                    criterion,
                    verdicts[criterion.id],
                ),
                feasibility=verdicts[criterion.id],
            )
            for criterion in criteria
        ],
        conjunction=validation.conjunction,
    )
