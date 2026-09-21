"""The one composition of the reads an approval question needs."""

from kodezart.core.protocols import TrackerScopeApprovalReader
from kodezart.domain.scope_approval import resolve_container_approval
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.scope import ScopeKind, ScopeRef


async def scope_approved(*, ref: ScopeRef, tracker: TrackerScopeApprovalReader) -> bool:
    """Whether the addressed scope, or a container above it, carries approval.

    An issue scope is the per-issue reading. A container scope reads its own
    labels and its parent edge and walks upward; a milestone's own labels are
    never consulted, because a milestone has no label level (KOD-382).
    """
    if ref.kind is ScopeKind.ISSUE:
        return await tracker.execution_approved(issue_key=ref.key)

    async def read_container(node: ScopeRef) -> tuple[bool, ScopeRef | None]:
        approved = (
            False
            if node.kind is ScopeKind.MILESTONE
            else ScopeLabel.APPROVED in await tracker.read_scope_labels(ref=node)
        )
        return approved, (await tracker.container_metadata(ref=node)).parent

    return await resolve_container_approval(ref=ref, read_container=read_container)
