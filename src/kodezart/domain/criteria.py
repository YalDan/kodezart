"""Criterion identity — the ``AC-n`` scheme minted once, harness-side.

Identity is assigned at generation time, never by a model.  The sweep, the
persisted artifact, evaluator dispatch and grading, and the re-injected
feedback text all key off these ids and never off criterion text: an
echoed string drifts, and KOD-11 measured it drifting.
"""

from collections.abc import Sequence

from kodezart.types.domain.criteria import (
    CRITERION_ID_PREFIX,
    CriteriaArtifact,
    CriteriaValidation,
    CriterionClass,
    CriterionFeasibility,
    CriterionId,
    DraftedCriterion,
    GeneratedCriterion,
    ValidatedCriterion,
)


def mint_criterion_id(index: int) -> CriterionId:
    """The identity of the criterion at 1-based *index*.

    The single construction site for a ``CriterionId``, so no other surface
    knows the ``AC-n`` shape and none may invent one.  The prefix is the
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
    arithmetic reads.  An unflagged criterion keeps the class the
    generator assigned, byte for byte.

    LANE SYNTHESIS, not a mandated mechanism: no written specification
    asks for this downgrade, and it is defended by no measured failure.
    It has a run-level consequence worth naming — the flags it reads come
    from values the refuter supplies, and ``accept_verdict`` tests
    hard-gate membership first, so a set in which every criterion is
    flagged can never be rejected.  Recorded rather than changed: what
    moves a criterion between the partitions is a behaviour question for
    whoever owns the gate.
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
