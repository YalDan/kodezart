"""Domain value object for persistence results."""

from dataclasses import dataclass
from enum import StrEnum


class PersistSource(StrEnum):
    """Which path produced the `PersistResult`.

    - ``WORKING_TREE_COMMIT``: persister staged + committed dirty working tree.
    - ``AGENT_DIRECT_COMMIT``: agent already committed; persister only pushed.
    """

    WORKING_TREE_COMMIT = "working_tree_commit"
    AGENT_DIRECT_COMMIT = "agent_direct_commit"


@dataclass(frozen=True, slots=True)
class PersistResult:
    """Immutable commit-and-push result: SHA, branch, message, source."""

    commit_sha: str
    branch: str
    message: str
    source: PersistSource
