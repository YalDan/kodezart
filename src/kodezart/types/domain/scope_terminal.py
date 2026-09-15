"""Lane report facts consumed by native terminal readback."""

from enum import StrEnum
from typing import Self

from pydantic import (
    ConfigDict,
    Field,
    model_validator,
)

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.surface import SurfaceKind


class LaneReportState(StrEnum):
    """A reported empty gap and a missing report are distinct facts."""

    CONVERGED = "converged"
    IN_GAP = "in_gap"
    HALTED = "halted"
    UNREPORTED = "unreported"


class LaneReport(CamelCaseModel):
    """One dispatched lane, including an explicit value for silence."""

    model_config = ConfigDict(frozen=True)

    lane_key: str = Field(min_length=1, pattern=r"\S")
    issue_id: str = Field(min_length=1, pattern=r"\S")
    state: LaneReportState
    detail: str | None = None


class ScopeResidualClass(StrEnum):
    """Why a scope's work is still owed, with no member for machine-complete.

    A scope that is machine-complete but not complete is not a seventh
    class: it is the answer to a query over these classes and the items'
    owners, so no member here — and none on ``WorkflowOutcome`` — states
    that distinction.

    ``UNRECORDED_AT_TERMINAL`` and ``LANE_UNREPORTED`` are the classes
    that block, because each says the terminal read found no record at
    all rather than a recorded piece of work someone owns.
    """

    UNCONVERGED_DEFECT_CLASS = "unconverged_defect_class"
    UNDEMONSTRABLE_HERE = "undemonstrable_here"
    OWNED_ELSEWHERE = "owned_elsewhere"
    LANE_WITHOUT_OPEN_PR = "lane_without_open_pr"
    LANE_UNREPORTED = "lane_unreported"
    UNRECORDED_AT_TERMINAL = "unrecorded_at_terminal"


#: Stated once, beside the vocabulary itself, so every consumer blocks on
#: the same classes rather than each restating the set.
BLOCKING_RESIDUAL_CLASSES: frozenset[ScopeResidualClass] = frozenset(
    {
        ScopeResidualClass.UNRECORDED_AT_TERMINAL,
        ScopeResidualClass.LANE_UNREPORTED,
    },
)

#: The record kinds a residual item may point at. A failing criterion is
#: already carried by its own sub-issue's state and Evidence; everything
#: else a run writes for itself stands in a marker-keyed comment.
RESIDUAL_RECORD_KINDS: frozenset[SurfaceKind] = frozenset(
    {SurfaceKind.CRITERION_SUB_ISSUE, SurfaceKind.MARKER_COMMENT},
)

#: The classes whose item is a criterion's own disposition, so its record
#: is that criterion's sub-issue and never a comment the run wrote.
_CRITERION_RECORDED_CLASSES: frozenset[ScopeResidualClass] = frozenset(
    {
        ScopeResidualClass.UNDEMONSTRABLE_HERE,
        ScopeResidualClass.OWNED_ELSEWHERE,
    },
)


class ScopeRecordRef(CamelCaseModel):
    """The tracker record already holding a residual item, not a new one.

    A criterion sub-issue is addressed by its own key alone; a marker
    comment by the marker that keys it, plus the tracker's comment id
    once the record has been read back.
    """

    model_config = ConfigDict(frozen=True)

    kind: SurfaceKind
    issue_key: str = Field(min_length=1, pattern=r"\S")
    marker: str | None = None
    comment_key: str | None = None

    @model_validator(mode="after")
    def _kind_carries_its_own_address(self) -> Self:
        if self.kind not in RESIDUAL_RECORD_KINDS:
            raise ValueError(
                f"a residual record is a criterion sub-issue or a marker "
                f"comment, not {self.kind.value}"
            )
        if self.kind is SurfaceKind.MARKER_COMMENT:
            if self.marker is None or not self.marker.strip():
                raise ValueError("a marker-keyed comment requires a nonblank marker")
        elif self.marker is not None or self.comment_key is not None:
            raise ValueError("a criterion sub-issue is addressed by its key alone")
        return self


class ScopeResidualOwnerKind(StrEnum):
    """Who owes the act: this lane, another lane, or a person."""

    THIS_LANE = "this_lane"
    ANOTHER_LANE = "another_lane"
    OPERATOR = "operator"


class ScopeResidualOwner(CamelCaseModel):
    """An owner is a kind together with the address that names it."""

    model_config = ConfigDict(frozen=True)

    kind: ScopeResidualOwnerKind
    key: str = Field(min_length=1, pattern=r"\S")


class ScopeResidualItem(CamelCaseModel):
    """One owed piece of work, its record, and the act that discharges it."""

    model_config = ConfigDict(frozen=True)

    issue_id: str = Field(min_length=1, pattern=r"\S")
    residual_class: ScopeResidualClass
    record: ScopeRecordRef
    detail: str = Field(min_length=1, pattern=r"\S")
    act: str = Field(min_length=1, pattern=r"\S")
    owner: ScopeResidualOwner

    @model_validator(mode="after")
    def _class_constrains_owner_and_record(self) -> Self:
        if (
            self.residual_class is ScopeResidualClass.UNRECORDED_AT_TERMINAL
            and self.owner.kind is ScopeResidualOwnerKind.OPERATOR
        ):
            raise ValueError(
                "a missing terminal record is owed by a lane, never by an operator"
            )
        if (
            self.residual_class in _CRITERION_RECORDED_CLASSES
            and self.record.kind is not SurfaceKind.CRITERION_SUB_ISSUE
        ):
            raise ValueError(
                f"a {self.residual_class.value} item is recorded on its "
                f"criterion sub-issue"
            )
        return self


class ScopeResidual(CamelCaseModel):
    """The whole residual of one scope, queryable by class and by owner."""

    model_config = ConfigDict(frozen=True)

    items: tuple[ScopeResidualItem, ...] = ()

    @model_validator(mode="after")
    def _each_record_carries_one_item_of_a_class(self) -> Self:
        addresses = [
            (item.issue_id, item.residual_class, item.record) for item in self.items
        ]
        if len(addresses) != len(set(addresses)):
            raise ValueError("duplicate residual item for one record and class")
        return self

    @property
    def blocking(self) -> tuple[ScopeResidualItem, ...]:
        return tuple(
            item
            for item in self.items
            if item.residual_class in BLOCKING_RESIDUAL_CLASSES
        )

    def by_class(
        self, residual_class: ScopeResidualClass
    ) -> tuple[ScopeResidualItem, ...]:
        """Every item of one class, in the order the residual was assembled."""
        return tuple(
            item for item in self.items if item.residual_class is residual_class
        )

    def by_owner(self, owner: ScopeResidualOwnerKind) -> tuple[ScopeResidualItem, ...]:
        """Every item owed by one kind of owner, assembly order preserved."""
        return tuple(item for item in self.items if item.owner.kind is owner)


class ScopeStoppingRule(CamelCaseModel):
    """The configured bound a declared stop was reached by, as data.

    A stop is declared only when a ``KODEZART_``-prefixed configuration
    field fixed before the run was exhausted, so the rule names that
    environment field, the value it carried and the rounds the run spent.
    The rounds must equal the value exactly: a run that stopped with
    rounds to spare was stopped by something other than this bound, and a
    run past it never ran.  Anything reached by no configured field is
    arithmetic, not a declared stop.
    """

    model_config = ConfigDict(frozen=True)

    #: The env spelling ``AppConfig`` loads: the ``KODEZART_`` prefix, a
    #: section whose own words are joined by single underscores, and the
    #: ``__`` nesting delimiter between section and field.
    config_field: str = Field(
        pattern=r"^KODEZART_[A-Z0-9]+(?:_[A-Z0-9]+)*(?:__[A-Z0-9]+(?:_[A-Z0-9]+)*)*$"
    )
    configured_value: int = Field(ge=1)
    rounds_used: int = Field(ge=1)

    @model_validator(mode="after")
    def _exhausted_exactly(self) -> Self:
        if self.rounds_used != self.configured_value:
            raise ValueError("a stopping rule must record the actual exhausted bound")
        return self
