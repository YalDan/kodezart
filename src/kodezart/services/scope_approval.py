"""The one composition of the reads a scope-member question needs."""

from kodezart.core.protocols import TrackerScopeApprovalReader
from kodezart.domain.scope_approval import resolve_container_approval
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.scope import ScopeKind, ScopeRef


async def scope_carries(
    *, ref: ScopeRef, member: ScopeLabel, tracker: TrackerScopeApprovalReader
) -> bool:
    """Whether the addressed scope, or a container above it, carries *member*.

    An issue scope reads the approval member as the per-issue cascade and any
    other member as the addressed issue's own configured members. A container
    scope reads its own members and its parent edge and walks upward; a
    milestone's own members are never consulted, because a milestone has no
    label level (KOD-382). One walk for every configured member: a milestone's
    triage is its project's, as its approval is.
    """
    if ref.kind is ScopeKind.ISSUE:
        if member is ScopeLabel.APPROVED:
            return await tracker.execution_approved(issue_key=ref.key)
        return member in await tracker.read_scope_labels(ref=ref)

    async def read_container(node: ScopeRef) -> tuple[bool, ScopeRef | None]:
        carried = (
            False
            if node.kind is ScopeKind.MILESTONE
            else member in await tracker.read_scope_labels(ref=node)
        )
        return carried, (await tracker.container_metadata(ref=node)).parent

    return await resolve_container_approval(ref=ref, read_container=read_container)


async def scope_approved(*, ref: ScopeRef, tracker: TrackerScopeApprovalReader) -> bool:
    """The approval member of the addressed scope, through the one resolver."""
    return await scope_carries(ref=ref, member=ScopeLabel.APPROVED, tracker=tracker)
