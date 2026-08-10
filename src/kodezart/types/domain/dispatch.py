"""The dispatch pass's report — machine-readable, never a judgment.

A pass that fires nothing must still say exactly WHY, per issue, in terms
a machine can compare across passes.  "Nothing can fire" and "queue
blocked" are the failure this shape exists to prevent: prose that reads
as a verdict, cannot be falsified, and leaves a loaded queue idle with
nothing to debug.

Approved-but-blocked is a correct resting state — a fact to report, never
a contradiction to fix.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.tracker import IssuePriority, TrackerIssue


class DispatchOutcome(StrEnum):
    """Four-way partition of a dispatch pass's terminal dispositions.

    The FIELD is ``outcome``, matching the run-side discriminator's naming
    discipline, and this report is the single surface a pass reports on —
    there is no second channel and no ``reason``.  The TYPE is separate
    from ``WorkflowOutcome`` because that enum partitions a code-change
    run's terminal routes and is what a job record carries; a job record
    carrying ``empty_eligible_set`` would be incoherent.
    """

    fire_enqueued = "fire_enqueued"
    claim_lost = "claim_lost"
    empty_eligible_set = "empty_eligible_set"
    base_unresolved = "base_unresolved"
    """The claim was granted and the base could not be resolved, so nothing
    was enqueued and the claim was released.  A distinct member rather than
    a variant of ``empty_eligible_set``, because the issue WAS eligible: the
    obstacle is a missing premise on the graph, and the next pass re-selects
    it once the premise is recorded."""


class ExclusionClause(StrEnum):
    """The five eligibility clauses, as the reasons an issue was excluded.

    Members are ordered as the predicate evaluates them, and an issue is
    annotated with the FIRST clause that excluded it, so the annotation is
    a function of the data rather than of evaluation luck.
    """

    NOT_APPROVED = "not_approved"
    NOT_OPEN = "not_open"
    LIVE_BLOCKER = "live_blocker"
    CLAIMED_OR_IN_FLIGHT = "claimed_or_in_flight"
    OPEN_DELIVERY = "open_delivery"


class DispatchModel(CamelCaseModel):
    """Base for dispatch report models: frozen, closed."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class IssueSnapshot(DispatchModel):
    """One row of the raw query snapshot the pass computed over."""

    issue_key: str = Field(min_length=1)
    priority: IssuePriority
    state_name: str
    created_at: datetime


class IssueExclusion(DispatchModel):
    """One issue and the clause that excluded it.

    ``detail`` is a machine-readable qualifier for the clause — the blocking
    issue's key, the holder of the claim — never an explanation.
    """

    issue_key: str = Field(min_length=1)
    clause: ExclusionClause
    detail: str = ""


class DispatchReport(DispatchModel):
    """The outcome of exactly one dispatch pass.

    ``tied_candidates`` is non-empty only when the ranking reached the
    random tie-break, and then it carries the whole tied set, so the pass
    is reconstructable from the report as well as from the log.
    """

    outcome: DispatchOutcome
    snapshot: tuple[IssueSnapshot, ...]
    exclusions: tuple[IssueExclusion, ...]
    eligible: tuple[str, ...]
    tied_candidates: tuple[str, ...] = ()
    claimed_issue_key: str | None = None
    job_id: str | None = None
    base: BaseSpec | None = None
    """The base the fire was dispatched on, and everything it was computed
    from.  ``None`` on the two outcomes that enqueued nothing — a pass that
    claimed no issue resolved no base, which is a different fact from a
    base that resolved to trunk."""
    superseded_base: BaseSpec | None = None
    """The base a PREVIOUS dispatch of this issue recorded, when the graph
    has moved under it since.  ``None`` covers two states deliberately —
    a first dispatch, and a re-dispatch on an unchanged base — because
    neither carries a superseded value; what distinguishes them is the
    recorded spec, which the tracker holds and this report does not
    duplicate."""


class PassDelta(DispatchModel):
    """What the deterministic pre-query saw since the last tick.

    The gate a scheduled pass consults before anything expensive runs.  An
    empty set means nothing moved, and nothing that costs tokens wakes at
    all.  ``mark`` is the high-water stamp the next tick asks from —
    carried on the value rather than left implicit, so a tick is
    reconstructable from its own report.

    The issues themselves, not their keys, because for a pass that composes
    over a window the pre-query IS the window read: carrying keys alone
    would send the pass back to the tracker for rows the gate had already
    fetched, and the second read would return a different set than the one
    the mark was advanced over.
    """

    issues: tuple[TrackerIssue, ...] = ()
    mark: datetime | None = None

    @property
    def changed(self) -> tuple[str, ...]:
        """The keys that moved, for a consumer needing no more than them."""
        return tuple(issue.issue_key for issue in self.issues)

    def has_delta(self) -> bool:
        """True iff something moved and a full pass is therefore warranted."""
        return bool(self.issues)
