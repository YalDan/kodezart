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

    ``unresolved`` carries the keys of the criteria the whole scope still
    owes, as the same closure arithmetic the gaps come from reads them. It
    is on the read rather than recomputed by a reporter, because a second
    reading of what a criterion's workflow state means would be a second
    arithmetic free to disagree with the one the lanes were selected by.

    ``excluded`` carries the keys of the criteria that count for nothing on
    their state alone. They are returned beside the gap so nothing leaves the
    reading silently: a caller that saw neither the key in ``unresolved`` nor
    a statement about it could not tell a set-aside obligation from one that
    was never there (KOD-794).
    """

    scope: ResolvedScope
    ready: tuple[ScopeReadyLane, ...]
    blocked: tuple[BlockedIssue, ...]
    unapproved: tuple[str, ...] = ()
    criteria: tuple[TrackerIssue, ...] = ()
    closed: tuple[TrackerIssue, ...] = ()
    unresolved: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
