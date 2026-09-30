"""Immutable results of ranking a snapshot of the tracker's dependency graph."""

from dataclasses import dataclass

from kodezart.types.domain.tracker import IssuePriority, TrackerIssue


@dataclass(frozen=True, slots=True)
class ReadyIssue:
    """An unblocked candidate and the priority inherited from its dependents."""

    issue: TrackerIssue
    effective_priority: IssuePriority


@dataclass(frozen=True, slots=True)
class BlockedIssue:
    """A candidate omitted from the ranking, with every live blocker named."""

    issue_key: str
    blocker_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TopologyPlan:
    """The ranked ready set and the candidates still blocked in this snapshot.

    This carries no dispatch order for later fires: the next pass reads the
    tracker and ranks again after the selected fire has run.
    """

    ready: tuple[ReadyIssue, ...]
    blocked: tuple[BlockedIssue, ...]
