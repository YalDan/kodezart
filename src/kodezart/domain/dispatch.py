"""Selection is computed, never judged — the arithmetic, as pure functions.

There is no prompt anywhere in this module and none may be introduced.
The reason is operational, not aesthetic: a wrong judgment produces no
error, no log line to falsify and no recovery path, while a wrong
computation produces a reproducible bug fixable once.

Each of the six clauses is a separate total function of data the port
already returned, so a clause can be tested without standing up a pass.
Ranking is likewise a pure key function; only the tie-break draw is
injected, because a uniform draw is the one thing that cannot be a pure
function of the data.
"""

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from kodezart.types.domain.operation import QueueState
from kodezart.types.domain.tracker import (
    ClaimResult,
    IssuePriority,
    IssueRelationKind,
    StateTransition,
    TrackerIssue,
    is_open,
    priority_rank,
)


def clause_in_team(issue: TrackerIssue, *, team_keys: Collection[str]) -> bool:
    """Clause 1: the issue belongs to a team this operation declares.

    The container boundary, as a property of the issue rather than of the
    query that found it.  A scan is narrowed to the declared teams and this
    clause decides eligibility over what came back — the same division
    :func:`clause_approved` already keeps between the queue-state filter and
    the approving act.

    An issue whose ``team_key`` is ``None`` belongs to a team the
    configuration does not name, which is outside the boundary by
    definition.  Nothing here reads an issue key, a prefix or any other
    spelling: a key that happens to look like a team's is not membership in
    it, and on a backend that prefixes nothing there is no spelling to read.
    """
    return issue.team_key is not None and issue.team_key in team_keys


def clause_approved(
    issue: TrackerIssue,
    *,
    provenance: StateTransition | None,
    approver_key: str,
) -> bool:
    """Clause 2: the issue carries APPROVED, set by the configured approver.

    Authority binds to the approver's ACT, so carrying the state is not
    enough — the transition must have been performed by the approver.
    Approval is the only human act in the loop, and kodezart never
    performs it.
    """
    if QueueState.APPROVED not in issue.queue_states:
        return False
    return provenance is not None and provenance.actor_key == approver_key


def clause_open(issue: TrackerIssue) -> bool:
    """Clause 3: the workflow state is neither completed nor canceled."""
    return is_open(issue.state_kind)


def blocker_keys(issue: TrackerIssue) -> tuple[str, ...]:
    """Every issue this one is blocked by, in relation order."""
    return tuple(
        relation.issue_key
        for relation in issue.relations
        if relation.kind is IssueRelationKind.BLOCKED_BY
    )


def live_blocker(
    issue: TrackerIssue,
    *,
    blockers: Mapping[str, TrackerIssue],
) -> str | None:
    """Clause 4: the first live blocker's key, or ``None`` when unblocked.

    A blocker is live iff it is itself open.  An edge to a completed or
    canceled issue does not block — a closed blocker is a finished
    dependency, not a standing one.
    """
    for key in blocker_keys(issue):
        blocker = blockers.get(key)
        if blocker is not None and clause_open(blocker):
            return key
    return None


def clause_unclaimed(
    *,
    claim: ClaimResult | None,
    run_is_live: bool,
) -> bool:
    """Clause 5: no unexpired claim, and no active run or queue entry."""
    return claim is None and not run_is_live


def clause_undelivered(*, has_open_delivery: bool) -> bool:
    """Clause 6: no open pull request already delivers the issue.

    This is the clause that separates the two open-and-unclaimed cases the
    predicate would otherwise conflate.  DELIVERED-IN-REVIEW: an issue the
    lifecycle writes moved to a review state has an open pull request, so
    it is excluded until verified merge — re-selecting it would re-fire
    already-delivered work.  CRASHED: a started-state issue with no open
    pull request and no live run REMAINS eligible and is re-selected on a
    later pass — a crashed run is evidence about the run, never a verdict
    on its issue.  Workflow state alone cannot tell these apart; the open
    pull request is the mechanical discriminator.
    """
    return not has_open_delivery


@dataclass(frozen=True)
class RankKey:
    """The total order over the eligible set, as a comparable value.

    Priority is read through ``priority_rank`` — the domain order — so no
    raw backend encoding can reach a comparison.  ``created_at`` is
    full-precision: the random draw applies only to exact equality.
    """

    priority_rank: int
    created_at: datetime

    def __lt__(self, other: "RankKey") -> bool:
        return (self.priority_rank, self.created_at) < (
            other.priority_rank,
            other.created_at,
        )


def rank_key(issue: TrackerIssue) -> RankKey:
    """Primary rank (Urgent first, None last), secondary oldest-first."""
    return RankKey(
        priority_rank=priority_rank(issue.priority),
        created_at=issue.created_at,
    )


@dataclass(frozen=True)
class Selection:
    """The one issue a pass claims, and the tied set it was drawn from.

    ``tied`` holds more than one key only when the top of the order was an
    exact ``created_at`` tie, which is the only case a draw runs at all.
    """

    winner_key: str
    tied: tuple[str, ...]


def select_top_ranked(
    issues: Sequence[TrackerIssue],
    *,
    draw: Callable[[Sequence[str]], str],
) -> Selection | None:
    """The top-ranked issue, drawing uniformly only on an exact tie.

    Returns ``None`` for an empty eligible set.  The tied set is always
    reported, so a pass whose winner came from a draw is reconstructable
    rather than merely plausible.
    """
    if not issues:
        return None
    best = min(rank_key(issue) for issue in issues)
    tied = tuple(sorted(issue.issue_key for issue in issues if rank_key(issue) == best))
    if len(tied) == 1:
        return Selection(winner_key=tied[0], tied=tied)
    return Selection(winner_key=draw(tied), tied=tied)


def ranked_order(issues: Sequence[TrackerIssue]) -> tuple[str, ...]:
    """Every issue key in rank order, ties broken by key for determinism.

    Exists so a test can assert the order below the tie, and so a log can
    carry the whole computed order rather than only its head.
    """
    return tuple(
        issue.issue_key
        for issue in sorted(
            issues,
            key=lambda issue: (
                rank_key(issue).priority_rank,
                rank_key(issue).created_at,
                issue.issue_key,
            ),
        )
    )


DOMAIN_PRIORITY_ORDER: tuple[IssuePriority, ...] = tuple(
    sorted(IssuePriority, key=priority_rank),
)
