"""Domain value objects for persistence results."""

from dataclasses import dataclass
from enum import StrEnum


class ArtifactPersistStatus(StrEnum):
    """Outcome of an ``ArtifactPersister.persist`` call.

    ``UNCHANGED`` and ``IGNORED_BY_TARGET`` both leave the branch
    untouched but for opposite reasons: the artifacts already match what
    is committed, versus the target repository's ignore rules matching
    the artifact directory so nothing was ever staged.  The second means
    the operator gets zero artifacts on every run against this target
    until its ignore rules change, and needs to know.
    """

    PERSISTED = "persisted"
    UNCHANGED = "unchanged"
    IGNORED_BY_TARGET = "ignored_by_target"


class PersistSource(StrEnum):
    """How the canonical ref was advanced to the workspace HEAD.

    Distinguishes the persister paths externally via a typed enum
    instead of a string sentinel embedded in ``PersistResult.message``.
    """

    WORKING_TREE_COMMIT = "working_tree_commit"
    AGENT_DIRECT_COMMIT = "agent_direct_commit"
    DIVERGENCE_REPLAY = "divergence_replay"


@dataclass(frozen=True, slots=True)
class PersistResult:
    """Immutable commit-and-push result.

    ``source`` records which persister path produced the result.
    ``message`` carries the real commit message (NOT a sentinel
    string).  For ``AGENT_DIRECT_COMMIT`` it is the output of
    ``git log -1 --format=%B HEAD``.  For ``WORKING_TREE_COMMIT`` it is
    the message that was generated and used for the new commit.

    For ``DIVERGENCE_REPLAY`` tree-equal subcase, the message is the
    remote-tip commit's message (``commit_sha = remote_tip``).  For
    ``DIVERGENCE_REPLAY`` tree-differ subcase, the message is the
    divergent HEAD's message, which IS the replay commit's own message
    (``commit_sha = replay_sha``).
    """

    commit_sha: str
    branch: str
    message: str
    source: PersistSource
