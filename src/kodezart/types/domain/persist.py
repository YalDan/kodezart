"""Domain value object for persistence results."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PersistResult:
    """Immutable commit-and-push result: SHA, branch, message."""

    commit_sha: str
    branch: str
    message: str
