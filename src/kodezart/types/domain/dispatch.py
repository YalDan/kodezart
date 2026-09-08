"""The dispatch pass's report — machine-readable, never a judgment.

A pass that fires nothing must still say exactly WHY, per issue, in terms
a machine can compare across passes.  "Nothing can fire" and "queue
blocked" are the failure this shape exists to prevent: prose that reads
as a verdict, cannot be falsified, and leaves a loaded queue idle with
nothing to debug.

Approved-but-blocked is a correct resting state — a fact to report, never
a contradiction to fix.
"""

from collections import deque
from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.self_writes import OwnMutation
from kodezart.types.domain.tracker import IssuePriority


class DispatchOutcome(StrEnum):
    """The partition of a dispatch pass's terminal dispositions.

    Exhaustive and disjoint: a pass ends on exactly one member, and the
    set is named by nothing but this declaration.

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
    spent: the exclusion is decided before the first write.  The
    pass does not fall through to the next-ranked issue — one issue read
    per pass buys one decided winner — so the next pass recomputes over a
    set the blocked winner has been remembered out of, and the blocker's
    key rides in the exclusions under the live-blocker clause."""


class ExclusionClause(StrEnum):
    """The eligibility clauses, as the reasons an issue was excluded.

    Members are ordered as the predicate evaluates them, and an issue is
    annotated with the FIRST clause that excluded it, so the annotation is
    a function of the data rather than of evaluation luck.
    """

    OUTSIDE_TEAM = "outside_team"
    """The issue belongs to no team this operation declares.  Evaluated
    first because it is the only clause that is true of an issue this
    operation has no business reading at all: a workspace holds more than
    one operation's board, and every clause below it would otherwise be
    asked about another board's issue — the approved-state clause
    included, which reads the presence of a queue state and nothing more,
    so it passes on any board using the same queue vocabulary.
    Since that clause carries no attestation of WHO put the state there,
    this one is the whole of the containment."""

    BASE_UNRESOLVED = "base_unresolved"
    """A previous pass claimed the issue and could not resolve its base.
    Excluded until the issue CHANGES — its ``updated_at`` moving past the
    reading taken after that pass released — so a standing graph obstacle
    is one report line per pass instead of a claim/release cycle every
    tick feeding its own gate delta.  The detail carries the
    recorded resolution failure."""

    RUN_FAILED = "run_failed"
    """A fire this pass started ended without reaching a terminal outcome.
    Excluded until the issue CHANGES — its ``updated_at`` moving past the
    reading taken after the failure — by the same remembered-exclusion
    mechanism the clause above uses, so a standing failure is one report
    line per pass instead of the whole run fired again at the next tick,
    into the condition that killed the last one.  The detail
    carries the class the run died of, or how it ended when no error frame
    named one."""

    LANE_BACKOFF = "lane_backoff"
    """The lane is holding back after a failure the whole account shares.

    The one clause that is not about the issue it annotates: a provider
    rate limit killed the last fire, and the next-ranked candidate would
    meet it unchanged, so every remaining issue on the board carries this
    line until the cooldown lapses.  Evaluated after the clause
    above so the issue that died still reports what it died of, and lifted
    by the CLOCK rather than by a change on the board — nothing an issue
    does clears a rate limit.  The detail carries the failure class that
    set it."""

    OUT_OF_SCOPE = "out_of_scope"
    """The issue's team declares a scope and the issue's project and
    initiatives are not in it.  The detail carries the issue's
    project, or names that it belongs to none."""

    NO_RECORDED_REPOSITORY = "no_recorded_repository"
    """The issue's team binds no repository and no route was ever recorded
    on the issue: judgment has not routed it yet, so no deterministic pass
    may claim it — the typed refusal, visible in every report,
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

    Asked over each scan entry's edges, and again over the winner's at
    the pre-claim reading, which supplies the edges a listing does not
    carry — the measured backend answers a listing with each issue's own
    fields and no relations.  A winner that reading finds
    blocked is remembered under this clause, by the same mechanism
    ``BASE_UNRESOLVED`` and ``RUN_FAILED`` use, so the lane's next tick
    ranks the next unblocked candidate instead of re-deciding a blocker
    that has not moved."""

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
    criterion_keys: tuple[str, ...] = ()
    """The open criterion sub-issues the dispatched fire is FOR.

    Empty for a producer that selects whole issues: its unit of work is the
    issue, and naming children it never read would be a claim about a gap
    it did not compute.  A producer that selects over a lane's gap carries
    exactly the criteria still open, so what the fire was sent to close is
    readable from the report rather than reconstructed from the board
    afterwards."""

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


#: Bookkeeping can conservatively wake a lagging gate instead of retaining
#: arbitrary full comment bodies for the entire service lifetime.
_SELF_WRITE_RECEIPT_LIMIT: Final[int] = 256


class SelfWriteLedger:
    """Atomic issue stamps and ordered explicit mutation receipts.

    Only the native issue write response may supply an own issue stamp.
    Comment writes have no such response: they record their exact create,
    edit or delete instead. Each gate independently retains its observation
    and receipt position; reading never consumes another gate's receipts.
    The newest 256 receipts are retained across all issues. An older
    reader gets explicit unavailable history and conservatively wakes;
    it never treats a partial suffix as complete evidence. This avoids a
    reader-lifecycle registry and bounds retained full comment bodies.
    """

    def __init__(self) -> None:
        self._stamps: dict[str, datetime] = {}
        self._mutations: deque[tuple[str, int, OwnMutation]] = deque()
        self._versions: dict[str, int] = {}
        self._discarded: dict[str, int] = {}

    def record(self, *, issue_key: str, updated_at: datetime) -> None:
        """Remember the stamp our own write left on *issue_key*."""
        self._stamps[issue_key] = updated_at

    def wrote(self, *, issue_key: str, updated_at: datetime) -> bool:
        """Whether *updated_at* is exactly what our own last write left."""
        return self._stamps.get(issue_key) == updated_at

    def record_mutation(self, *, issue_key: str, mutation: OwnMutation) -> None:
        """Append only the effects described by a successful native write."""
        if len(self._mutations) == _SELF_WRITE_RECEIPT_LIMIT:
            discarded_issue, discarded_version, _ = self._mutations.popleft()
            self._discarded[discarded_issue] = discarded_version
        version = self._versions.get(issue_key, 0) + 1
        self._versions[issue_key] = version
        self._mutations.append((issue_key, version, mutation))

    def receipts(
        self, *, issue_key: str, after: int = 0
    ) -> tuple[int, tuple[OwnMutation, ...] | None]:
        """Return a cursor and complete receipts, or None for evicted history.

        Reads do not consume receipts, so gates and their rearm checkpoints
        remain independent. The caller must wake when history is missing.
        """
        version = self._versions.get(issue_key, 0)
        if after < self._discarded.get(issue_key, 0) or after > version:
            return version, None
        return version, tuple(
            mutation
            for key, held_version, mutation in self._mutations
            if key == issue_key and held_version > after
        )


class PassRun(StrEnum):
    """What one scheduled tick actually did: work, or nothing at all.

    A gate-skipped tick opened no session and produced no run, so it has
    nothing to record — the measured boot backfilled a "completed" row for
    a 3.5-second fire-prep tick that never started one.  Separate
    from ``RunOutcome``, which partitions how a run ENDED: a tick that ran
    nothing has no end to name, so the two never share a member.
    """

    RAN = "ran"
    SKIPPED = "skipped"
