"""Criterion identity — the ``AC-n`` scheme minted once, harness-side.

Identity is assigned by the harness at generation time, never by a model:
a stable id is exactly the thing an echoed string cannot be.  Everything
downstream — the feasibility sweep, the persisted artifact, evaluator
dispatch and grading, and the re-injected feedback text — keys off these
ids and never off criterion text.
"""

from collections.abc import Mapping, Sequence

from kodezart.types.domain.criteria import (
    AcceptanceCriterion,
    CriteriaArtifact,
    CriteriaValidation,
    CriterionFeasibility,
    DraftedCriterion,
    ValidatedCriterion,
)

_ID_PREFIX = "AC-"


def mint_criterion_id(position: int) -> str:
    """The identity of the criterion at 1-based *position*."""
    if position < 1:
        msg = f"Criterion positions are 1-based; got {position}"
        raise ValueError(msg)
    return f"{_ID_PREFIX}{position}"


def mint_criteria(
    drafted: Sequence[DraftedCriterion],
) -> tuple[AcceptanceCriterion, ...]:
    """Assign ``AC-n`` identities to *drafted* in emission order."""
    return tuple(
        AcceptanceCriterion(
            id=mint_criterion_id(position),
            text=criterion.text,
            classification=criterion.classification,
        )
        for position, criterion in enumerate(drafted, start=1)
    )


def criteria_by_id(
    criteria: Sequence[AcceptanceCriterion],
) -> Mapping[str, AcceptanceCriterion]:
    """Index *criteria* by identity — the harness's own text, by id."""
    return {criterion.id: criterion for criterion in criteria}


def build_artifact(
    criteria: Sequence[AcceptanceCriterion],
    validation: CriteriaValidation,
) -> CriteriaArtifact:
    """Fold criteria and their sweep verdicts into the persisted document."""
    verdicts: dict[str, CriterionFeasibility] = {
        verdict.criterion_id: verdict for verdict in validation.verdicts
    }
    return CriteriaArtifact(
        criteria=[
            ValidatedCriterion(
                id=criterion.id,
                text=criterion.text,
                classification=criterion.classification,
                feasibility=verdicts[criterion.id],
            )
            for criterion in criteria
        ],
        conjunction=validation.conjunction,
    )
