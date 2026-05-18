"""Domain value object for persistence results."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PersistResult:
    """Immutable commit-and-push result: SHA + branch.

    Only ``commit_sha`` and ``branch`` are consumed downstream
    (``agent_service.AgentService`` propagates them onto the buffered
    ``ResultEvent``).  Any additional human-readable message or
    source-distinguishing enum is YAGNI until a real consumer appears.
    """

    commit_sha: str
    branch: str
