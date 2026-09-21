"""Fresh approval arithmetic over explicit issue ancestry and membership."""

from collections.abc import Awaitable, Callable

from kodezart.domain.errors import ScopeReadError
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import TrackerIssue

type ReadIssueApproval = Callable[[str], Awaitable[tuple[TrackerIssue, bool]]]
type ReadContainerApproval = Callable[
    [ScopeRef], Awaitable[tuple[bool, ScopeRef | None]]
]


async def resolve_execution_approval(
    *,
    issue_key: str,
    read_issue: ReadIssueApproval,
    read_container: ReadContainerApproval,
) -> bool:
    """Read issue ancestors, then the addressed issue's container ancestors.

    Container membership remains independent of issue parent edges. A
    parent issue's explicit approval covers its descendants across projects;
    its project label covers that project's actual members. Milestones add
    no label level: their project supplies container approval.
    """
    requested = ScopeRef(kind=ScopeKind.ISSUE, key=issue_key)
    seen: set[ScopeRef] = set()
    key: str | None = issue_key
    project: str | None = None
    while key is not None:
        ref = ScopeRef(kind=ScopeKind.ISSUE, key=key)
        if ref in seen:
            raise ScopeReadError("issue parent cycle", ref=requested)
        seen.add(ref)
        issue, approved = await read_issue(key)
        if issue.issue_key != key:
            raise ScopeReadError("issue approval identity changed", ref=requested)
        if key == issue_key:
            project = issue.project_id
            if issue.milestone_key is not None and project is None:
                raise ScopeReadError(
                    "milestone member has no owning project", ref=requested
                )
            if issue.project is not None and project is None:
                raise ScopeReadError(
                    "project membership has no canonical key", ref=requested
                )
        if approved:
            return True
        key = issue.parent_key

    if project is None:
        return False
    return await resolve_container_approval(
        ref=ScopeRef(kind=ScopeKind.PROJECT, key=project),
        read_container=read_container,
        seen=seen,
    )


async def resolve_container_approval(
    *,
    ref: ScopeRef,
    read_container: ReadContainerApproval,
    seen: set[ScopeRef] | None = None,
) -> bool:
    """Walk a container chain upward until a node carries approval.

    The caller's ``seen`` set is shared when there is one, so cycle
    detection spans the issue chain and the container chain as one walk.
    A milestone's owning project is the first container node: a milestone
    adds no label level of its own.
    """
    walked = seen if seen is not None else set()
    if ref.kind is ScopeKind.ISSUE:
        raise ScopeReadError("not an approval container", ref=ref)
    ancestor: ScopeRef | None = ref
    if ref.kind is ScopeKind.MILESTONE:
        _, owner = await read_container(ref)
        if owner is None or owner.kind is not ScopeKind.PROJECT:
            raise ScopeReadError("milestone has no owning project", ref=ref)
        walked.add(ref)
        ancestor = owner
    while ancestor is not None:
        if ancestor in walked:
            raise ScopeReadError("container parent cycle", ref=ref)
        walked.add(ancestor)
        approved, parent = await read_container(ancestor)
        if parent is not None and parent.kind is not ScopeKind.INITIATIVE:
            raise ScopeReadError("invalid approval container parent", ref=ancestor)
        if approved:
            return True
        ancestor = parent
    return False
