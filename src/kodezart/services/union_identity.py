"""Refuse local Git substitutions behind a union's immutable commit names."""

from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import GitService
from kodezart.domain.errors import UnionHeadReadError


async def require_union_object_identity(
    *, git: GitService, repository: str, scope_key: str
) -> None:
    """Settle the native read before propagating cancellation or refusal."""
    try:
        replacements = await settle(git.has_replace_refs(repository))
    except (OSError, RuntimeError, ValueError) as exc:
        raise UnionHeadReadError(
            scope_key=scope_key,
            branch=None,
            reason="the repository's Git replacement namespace is unreadable",
        ) from exc
    if replacements:
        raise UnionHeadReadError(
            scope_key=scope_key,
            branch=None,
            reason="the repository substitutes Git objects behind union commit names",
        )
