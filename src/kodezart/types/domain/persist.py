"""Domain value objects for persistence results."""

from dataclasses import dataclass
from enum import StrEnum


class PersistSource(StrEnum):
    """How the canonical ref was advanced to the workspace HEAD.

    Distinguishes the two persister paths externally via a typed enum
    instead of a string sentinel embedded in ``PersistResult.message``.
    """

    WORKING_TREE_COMMIT = "working_tree_commit"
    AGENT_DIRECT_COMMIT = "agent_direct_commit"


@dataclass(frozen=True, slots=True)
class PersistResult:
    """Immutable commit-and-push result.

    ``source`` records which persister path produced the result.
    ``message`` carries the real commit message (NOT a sentinel
    string).  For ``AGENT_DIRECT_COMMIT`` it is the output of
    ``git log -1 --format=%B HEAD``.  For ``WORKING_TREE_COMMIT`` it is
    the message that was generated and used for the new commit.
    """

    commit_sha: str
    branch: str
    message: str
    source: PersistSource
