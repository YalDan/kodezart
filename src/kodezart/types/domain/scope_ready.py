"""One live scope selection, carrying each selected lane's exact gap."""

from dataclasses import dataclass

from kodezart.types.domain.scope import ResolvedScope
from kodezart.types.domain.topology import BlockedIssue
from kodezart.types.domain.tracker import IssuePriority, TrackerIssue


@dataclass(frozen=True, slots=True)
class ScopeReadyLane:
    """One selected lane, its priority, and the two readings of its subtree.

    ``criteria`` is every criterion record beneath the lane, open or closed,
    and ``gap`` is the open part of it. A consumer asking whether the lane
    moved between two ticks needs the whole roster: the identities that were
    open earlier and are absent from ``gap`` now are what closed, and the
    open reading alone cannot name them.
    """

    issue: TrackerIssue
    effective_priority: IssuePriority
    gap: tuple[TrackerIssue, ...]
    criteria: tuple[TrackerIssue, ...]


@dataclass(frozen=True, slots=True)
class ScopeReadySet:
    """One reading of a scope: what owes work, what is blocked, what is finished.

    ``closed`` carries the approved members owing nothing. They are not
    ready lanes and carry no gap and no priority — there is no iteration for
    a topology to order them into — but they are the members a delivery is
    still owed for, so a read that dropped them left the only carrier of
    that fact out of its answer.
    """

    scope: ResolvedScope
    ready: tuple[ScopeReadyLane, ...]
    blocked: tuple[BlockedIssue, ...]
    unapproved: tuple[str, ...] = ()
    criteria: tuple[TrackerIssue, ...] = ()
    closed: tuple[TrackerIssue, ...] = ()
