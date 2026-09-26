"""One live scope selection, carrying each selected lane's exact gap."""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.scope import ResolvedScope
from kodezart.types.domain.topology import BlockedIssue
from kodezart.types.domain.tracker import IssuePriority, TrackerIssue


class UnreachableReason(StrEnum):
    """Why a scope's filter misses a criterion, in the filter's own terms.

    A project or initiative scope misses one that sits in another project, or
    in no project at all; a milestone scope misses one under another milestone,
    or under none.  The milestone readings are never folded into the project
    ones: two issues of one project sit on either side of a milestone filter,
    so a reading that answered with the project would answer the same for a
    criterion the filter reaches and one it does not.
    """

    OTHER_PROJECT = "other_project"
    NO_PROJECT = "no_project"
    OTHER_MILESTONE = "other_milestone"
    NO_MILESTONE = "no_milestone"


class UnreachableCriterion(CamelCaseModel):
    """One criterion a lane owes whose own issue the scope's filter misses.

    Unreachability is not ownership: the criterion sits inside the lane's
    subtree, so it is the lane's work.  It is named under any member, approved
    or not, blocked or not, and only a lane that is ready (approved and
    unblocked) is fired for it.  What the filter decides is only whether the
    scope can ADDRESS that issue in its own right, which is what ``reason``
    records.

    ``container`` is the project id or the milestone key the issue sits in
    instead, present exactly for the two ``OTHER_*`` reasons.  It defaults to
    absent because a ``NO_*`` entry has none to carry and reaches a consumer
    without the key at all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    issue_key: str = Field(min_length=1)
    reason: UnreachableReason
    container: str | None = Field(default=None, min_length=1)


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
class ScopeHeldMember:
    """One member whose walk is held on an open decision, and its criteria.

    ``criteria`` is every criterion record beneath the member, open or
    closed: the roster its lapse questions could have been raised for.
    """

    issue: TrackerIssue
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

    ``unreachable`` is the part of ``unresolved`` whose own issues the
    scope's members do not include, each with the reason the scope's filter
    missed it. It is on the read for the same reason ``unresolved`` is.

    ``excluded`` carries the keys of the criteria that count for nothing on
    their state alone. They are returned beside the gap so nothing leaves the
    reading silently: a caller that saw neither the key in ``unresolved`` nor
    a statement about it could not tell a set-aside obligation from one that
    was never there (KOD-794).

    ``held`` carries the members classified for decision that have
    criterion children, with their criteria, when the read was taken without
    the walker's stage barriers.
    The walker's own read refuses such a scope, so for it ``held`` is empty.
    """

    scope: ResolvedScope
    ready: tuple[ScopeReadyLane, ...]
    blocked: tuple[BlockedIssue, ...]
    unapproved: tuple[str, ...] = ()
    criteria: tuple[TrackerIssue, ...] = ()
    closed: tuple[TrackerIssue, ...] = ()
    unresolved: tuple[str, ...] = ()
    unreachable: tuple[UnreachableCriterion, ...] = ()
    excluded: tuple[str, ...] = ()
    held: tuple[ScopeHeldMember, ...] = ()
