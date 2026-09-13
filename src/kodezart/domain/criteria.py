"""Authored criterion identity — the ``AC-n`` scheme minted harness-side.

Authored identity is assigned at generation time, never by a model.
Native execution instead carries each tracker key without minting.  The sweep, the
persisted artifact, evaluator dispatch and grading, and the re-injected
feedback text all key off these ids and never off criterion text: a
model-echoed string can drift.
"""

from collections.abc import Sequence

from kodezart.types.domain.criteria import (
    CRITERION_ID_PREFIX,
    CriteriaArtifact,
    CriteriaValidation,
    CriterionFeasibility,
    CriterionId,
    DraftedCriterion,
    GeneratedCriterion,
    ValidatedCriterion,
)


def mint_criterion_id(index: int) -> CriterionId:
    """The identity of the criterion at 1-based *index*.

    The single authored minting site, so no other surface knows the
    ``AC-n`` shape and none may invent one.  The prefix is the
    one the format pattern is built from, so a minted id always matches it.
    """
    if index < 1:
        msg = f"Criterion positions are 1-based; got {index}"
        raise ValueError(msg)
    return CriterionId(f"{CRITERION_ID_PREFIX}{index}")


def mint_criteria(
    drafted: Sequence[DraftedCriterion],
) -> tuple[GeneratedCriterion, ...]:
    """Assign ``AC-n`` identities to *drafted* in emission order."""
    return tuple(
        GeneratedCriterion(
            id=mint_criterion_id(index),
            text=criterion.text,
        )
        for index, criterion in enumerate(drafted, start=1)
    )


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
                feasibility=verdicts[criterion.id],
            )
            for criterion in criteria
        ],
        conjunction=validation.conjunction,
    )
