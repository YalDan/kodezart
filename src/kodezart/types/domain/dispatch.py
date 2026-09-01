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
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.tracker import IssuePriority


class DispatchOutcome(StrEnum):
    """The partition of a dispatch pass's terminal dispositions.

    Exhaustive and disjoint: a pass ends on exactly one member, and the
    set is named by nothing but this declaration — a prose count of the
    members drifts the moment one is added, as it did here.

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

    winner_blocked = "winner_blocked"
    """The winner was read fresh before the claim and carries a live
    blocker, so nothing was claimed and nothing was enqueued.  Distinct
    from ``empty_eligible_set`` because the set this pass computed was NOT
    empty, and distinct from ``base_unresolved`` because no claim was
    spent: the exclusion is decided before the first write (KOD-173).  The
    pass does not fall through to the next-ranked issue — one issue read
    per pass buys one decided winner — so the next pass recomputes over a
    set the blocked winner has been remembered out of, and the blocker's
    key rides in the exclusions under the live-blocker clause."""


class ExclusionClause(StrEnum):
    """The eligibility clauses, as the reasons an issue was excluded.

    Members are ordered as the predicate evaluates them, and an issue is
    annotated with the FIRST clause that excluded it, so the annotation is
    a function of the data rather than of evaluation luck.  ``LIVE_BLOCKER``
    is the exception the order cannot express: it is decided on the winner
    after the predicate has run, and reaches a scanned issue only through
    the remembered-exclusion map.
    """

    OUTSIDE_TEAM = "outside_team"
    """The issue belongs to no team this operation declares.  Evaluated
    first because it is the only clause that is true of an issue this
    operation has no business reading at all: a workspace holds more than
    one operation's board, and every clause below it would otherwise be
    asked about another board's issue — the approved-state clause
    included, which reads the presence of a queue state and nothing more,
    so it passes on any board using the same queue vocabulary (KOD-144).
    Since that clause carries no attestation of WHO put the state there,
    this one is the whole of the containment."""

    BASE_UNRESOLVED = "base_unresolved"
    """A previous pass claimed the issue and could not resolve its base.
    Excluded until the issue CHANGES — its ``updated_at`` moving past the
    reading taken after that pass released — so a standing graph obstacle
    is one report line per pass instead of a claim/release cycle every
    tick feeding its own gate delta (KOD-169).  The detail carries the
    recorded resolution failure."""

    RUN_FAILED = "run_failed"
    """A fire this pass started ended without reaching a terminal outcome.
    Excluded until the issue CHANGES — its ``updated_at`` moving past the
    reading taken after the failure — by the same remembered-exclusion
    mechanism the clause above uses, so a standing failure is one report
    line per pass instead of the whole run fired again at the next tick,
    into the condition that killed the last one (KOD-174).  The detail
    carries the class the run died of, or how it ended when no error frame
    named one."""

    OUT_OF_SCOPE = "out_of_scope"
    """The issue's team declares a scope and the issue's project and
    initiatives are not in it (KOD-169).  The detail carries the issue's
    project, or names that it belongs to none."""

    NO_RECORDED_REPOSITORY = "no_recorded_repository"
    """The issue's team binds no repository and no route was ever recorded
    on the issue: judgment has not routed it yet, so no deterministic pass
    may claim it (KOD-169) — the typed refusal, visible in every report,
    never a claim by tick order."""

    RECORDED_ELSEWHERE = "recorded_elsewhere"
    """The recorded repository names a repository other than this pass's.
    The detail carries the recorded url: one naming another DECLARED
    repository is that repository's pass's to claim, and one outside the
    declared roster is visible against the config as routed nowhere."""

    NOT_APPROVED = "not_approved"
    NOT_OPEN = "not_open"
    LIVE_BLOCKER = "live_blocker"
    """An issue the graph blocks, by an edge to an issue still open.  The
    detail carries the blocker's key.

    Decided at ONE site, the pre-claim reading of the winner: a listing
    answers with each issue's own fields and no edges, so this clause
    asked over a scan entry could only pass (KOD-173).  The winner it
    excludes is then remembered under it, by the same mechanism
    ``BASE_UNRESOLVED`` and ``RUN_FAILED`` use, so the lane's next tick
    ranks the next unblocked candidate instead of spending every tick
    re-deciding a blocker that has not moved — which is also how the
    clause reaches a report line for an issue that is not this pass's
    winner."""

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
    claimed_state_name: str | None = None
    """The workflow state the claimed issue held when this pass READ it.

    Captured here because the pass is the only reader that sees it before
    the lifecycle moves it: a run that crashes has to be put back where it
    was found, and by then the tracker's copy holds the in-progress
    stage.  ``None`` on every outcome that claimed nothing."""
    claimed_visibility: RepoVisibility = RepoVisibility.PUBLIC
    """The visibility posture of the board the claimed issue sits on.

    Carried for the reason ``claimed_state_name`` is: the pass is where
    the winning issue's team is known, and the writer that scrubs the
    comments written back onto that issue is several hops away.

    ``PUBLIC`` rather than absent on every outcome that claimed nothing,
    because there is no fourth state to be in: the value is a posture to
    write under, and the unresolved one is the fail-closed one."""
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


class PassSignal(StrEnum):
    """The deterministic questions a pass may gate on — one port call each.

    A gate is the DISJUNCTION over the signals its pass configures: any
    signal reporting work runs the pass, and a pass configuring none runs
    unconditionally without issuing a query at all, so ungated stays the
    cheapest path rather than a special case.

    Members are named for the QUESTION rather than for the pass that asks
    it, because more than one pass asks the same question and a signal
    named after a caller would have to be duplicated for the second one.
    """

    approved_changed = "approved_changed"
    """Issues at the approved queue state that moved since this signal's
    mark.  The dispatch pass's entire gate, expressed as configuration."""

    issues_changed = "issues_changed"
    """Any issue that moved since this signal's mark, at any queue state."""

    triage_backlog = "triage_backlog"
    """The standing triage backlog is non-empty.  Holds NO mark by
    construction: the question is about the backlog's SIZE, not about
    movement, because a pass that re-sweeps its whole backlog has work to
    do on a board where nothing changed.  Consequence, stated rather than
    discovered: this signal is true while anything sits at triage — plan
    stubs deliberately parked there included — so a board that parks stubs
    keeps its pass running every tick.  An operator drops the signal rather
    than learning that from a bill."""

    reviews_changed = "reviews_changed"
    """Any review that moved since this signal's mark.  Reviews are a
    separate object class and no issue scan reaches them, so a pass whose
    prompt sweeps review threads is under-gated without this: a principal's
    mention on a review with no issue activity would be skipped."""


class PassDelta(DispatchModel):
    """What the deterministic pre-query saw since the last tick.

    The gate a scheduled pass consults before anything expensive runs.
    ``changed`` is the whole answer: an empty set means nothing moved, and
    nothing that costs tokens wakes at all.  ``mark`` is the high-water
    stamp the next tick asks from — carried on the value rather than left
    implicit, so a tick is reconstructable from its own report.
    """

    changed: tuple[str, ...] = ()
    mark: datetime | None = None

    def has_delta(self) -> bool:
        """True iff something moved and a full pass is therefore warranted."""
        return bool(self.changed)
