"""The shared entry read for every producer of a scoped workflow."""

from kodezart.core.protocols import TrackerPort
from kodezart.types.domain.scope import ResolvedScope, ScopeRef


async def resolve_scope(*, ref: ScopeRef, tracker: TrackerPort) -> ResolvedScope:
    """Resolve current membership through the tracker port.

    The adapter resolves container membership and issue descendants. The
    graph receives the resulting issues unchanged: relations and parent
    fields carry topology; issue prose and branch names do not.
    """
    issues = await tracker.scope_issues(ref=ref)
    return ResolvedScope(ref=ref, issues=tuple(issues))
