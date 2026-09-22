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
    """The satisfaction one cross-off records. Never collapsed to a boolean.

    ``undemonstrated`` is not a fail: a fail is a reading of the tree the
    sha names, and this is the state of having no such reading at all —
    no reading of the tree the sha names that says anything about this
    criterion.  The grading workspace holding changes the sha does not,
    or standing at another head, is one such reading; there are others,
    and which one failed is named by :class:`UndemonstratedReason` on the
    cross-off rather than by a member of its own here, so this enum keeps
    saying exactly what a criterion's satisfaction is.

    ``lapsed`` is not a fail either: the grading read the tree its sha
    names and passed, and what has changed since is the tree, not the
    reading. The criterion is owed again rather than broken, so a lapse
    takes the satisfaction back without reporting a regression.
    """

    passed = "passed"
    failed = "failed"
    lapsed = "lapsed"
    undemonstrated = "undemonstrated"


class UndemonstratedReason(StrEnum):
    """Which reading of the tree failed, when a grading proved nothing.

    One member per way a reading the harness takes comes back empty, never
    per cause a session might name: the member is chosen by code from a fact
    code read, and it is what the run's record carries in place of a verdict.
    """

    #: The grading workspace held changes the sha does not, or its head was
    #: not the sha the verdict would be stamped with.
    workspace_not_the_graded_sha = "workspace_not_the_graded_sha"
    #: The check this criterion names still passed after the behaviour it
    #: names had been removed from the tree, so passing it read nothing
    #: about that behaviour.
    check_survived_mutation = "check_survived_mutation"
    #: The check this criterion names already passed at the commit the
    #: lane's recorded base resolves to, where none of the work exists, so
    #: the head's pass is a reading of the base rather than of the branch.
    satisfied_at_base = "satisfied_at_base"
    #: The check this criterion names was to be run at the lane's base and
    #: no answer for it was settled there — the reading could not be taken,
    #: left this criterion out, or answered for it twice — so what the
    #: branch contributed to the head's pass was never read.
    base_reading_unsettled = "base_reading_unsettled"


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
    #: Which reading failed, on the one state that has no verdict to carry.
    #:
    #: Named ``undemonstrated_reason`` rather than ``reason``: the question
    #: it answers is which reading of the tree came back empty, and a bare
    #: ``reason`` on a frozen model that forbids extras invites a second
    #: meaning to be read into the same field later.
    undemonstrated_reason: UndemonstratedReason | None = None

    @model_validator(mode="after")
    def _the_reason_is_the_undemonstrated_state(self) -> Self:
        if (self.undemonstrated_reason is None) is (
            self.state is CrossOffState.undemonstrated
        ):
            raise ValueError(
                "an undemonstrated cross-off names the reading that failed, "
                "and no other state names one"
            )
        return self

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
