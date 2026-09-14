"""The per-criterion cross-off: one satisfaction state beside its graded sha.

A cross-off is the whole per-criterion carrier.  Its verdict is the state
enum, so a criterion nothing has re-derived since its grading stays
distinguishable from one that failed; a boolean would collapse the two.

The re-derivation class says what re-deriving the criterion costs and
whether the loop may re-derive it at all.  It is sticky per criterion
identity: declared once and read back unchanged by every later iteration,
so no iteration turns an expensive or observed criterion into a cheap one
by declaring it so.
"""

from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated, Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_ref import CriterionRef


class RederivationClass(StrEnum):
    """What re-deriving a criterion costs, and whether the loop may."""

    cheap = "cheap"
    expensive = "expensive"
    observed = "observed"


class CrossOffState(StrEnum):
    """The satisfaction one cross-off records. Never collapsed to a boolean."""

    passed = "passed"
    failed = "failed"
    lapsed = "lapsed"


#: The classes whose criteria name the path prefixes their grading exercised.
#:
#: Both are exempt from re-derivation on every head move — the expensive one
#: because re-deriving it costs, the observed one because the loop cannot
#: re-derive it at all — and both therefore need the prefixes that say when
#: the exemption stops holding.
PATH_BOUND_CLASSES = frozenset(
    {RederivationClass.expensive, RederivationClass.observed}
)

#: One repository-relative path prefix a grading exercised.
ExercisedPath = Annotated[str, Field(min_length=1, pattern=r"\S")]


class StickyClassError(ValueError):
    """A criterion identity declared a second, different re-derivation class."""

    def __init__(
        self,
        *,
        criterion: CriterionRef,
        held: RederivationClass,
        declared: RederivationClass,
    ) -> None:
        super().__init__(
            f"{criterion} holds the {held.value} re-derivation class and cannot "
            f"be declared {declared.value}"
        )
        self.criterion = criterion
        self.held = held
        self.declared = declared


class CriterionCrossOff(CamelCaseModel):
    """One criterion's satisfaction at the commit it was graded at."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion: CriterionRef = Field(min_length=1, pattern=r"\S")
    state: CrossOffState
    evidence: CriterionEvidence
    rederivation_class: RederivationClass = RederivationClass.cheap
    exercised_paths: tuple[ExercisedPath, ...] = ()

    @model_validator(mode="after")
    def _path_bound_classes_name_their_paths(self) -> Self:
        if self.rederivation_class in PATH_BOUND_CLASSES and not self.exercised_paths:
            raise ValueError(
                f"an {self.rederivation_class.value} cross-off names the path "
                "prefixes its grading exercised"
            )
        return self


def held_rederivation_classes(
    cross_offs: Iterable[CriterionCrossOff],
) -> dict[CriterionRef, RederivationClass]:
    """The class each criterion identity holds across successive iterations."""
    held: dict[CriterionRef, RederivationClass] = {}
    for cross_off in cross_offs:
        declared = cross_off.rederivation_class
        current = held.setdefault(cross_off.criterion, declared)
        if current is not declared:
            raise StickyClassError(
                criterion=cross_off.criterion, held=current, declared=declared
            )
    return held
