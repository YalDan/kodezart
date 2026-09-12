"""Exhaustive scope reads over named Linear tools and the issue read seam."""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Protocol, assert_never

from pydantic import ValidationError

from kodezart.adapters.linear_mcp_types import LinearWireModel
from kodezart.adapters.linear_scope_types import (
    LinearApprovalInitiativeWire,
    LinearApprovalProjectWire,
    LinearScopeIdentityWire,
    LinearScopeInitiativeWire,
    LinearScopeIssuesWire,
    LinearScopeIssueWire,
    LinearScopeMetadataWire,
    LinearScopeMilestonesWire,
    LinearScopePageWire,
    LinearScopeProjectsWire,
    LinearScopeProjectWire,
)
from kodezart.adapters.pagination import cursor_pages
from kodezart.core.errors import TrackerProtocolError
from kodezart.core.protocols import McpToolResult
from kodezart.domain.errors import ScopeReadError
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.tracker import TrackerIssue

# Vendor maximum page sizes, not limits on the scope's membership.
_ISSUE_PAGE_SIZE = 250
_PROJECT_PAGE_SIZE = 50

_TOOL_GET_PROJECT = "get_project"
_TOOL_GET_INITIATIVE = "get_initiative"
_TOOL_LIST_ISSUES = "list_issues"
_TOOL_LIST_PROJECTS = "list_projects"
_TOOL_LIST_MILESTONES = "list_milestones"
_TOOL_GET_MILESTONE = "get_milestone"

SCOPE_READ_TOOLS = frozenset(
    {
        _TOOL_GET_PROJECT,
        _TOOL_GET_INITIATIVE,
        _TOOL_LIST_ISSUES,
        _TOOL_LIST_PROJECTS,
        _TOOL_LIST_MILESTONES,
        _TOOL_GET_MILESTONE,
    },
)

type CallTool = Callable[[str, Mapping[str, object]], Awaitable[McpToolResult]]


class ReadIssue(Protocol):
    async def __call__(self, *, issue_key: str) -> TrackerIssue: ...


class LinearScopeReader:
    """A read operation's local traversal state; no cached tracker answers."""

    def __init__(self, *, call: CallTool, read_issue: ReadIssue) -> None:
        self._call = call
        self._read_issue = read_issue

    async def scope_issues(self, *, ref: ScopeRef) -> Sequence[TrackerIssue]:
        match ref.kind:
            case ScopeKind.ISSUE:
                issues = await self._subtree(ref)
            case ScopeKind.PROJECT:
                project = await self._project(ref.key)
                issues = await self._project_issues(project.id, ref=ref)
            case ScopeKind.MILESTONE:
                project_key, milestone = await self._milestone(ref)
                issues = await self._project_issues(
                    project_key,
                    ref=ref,
                    milestone=milestone.id,
                )
            case ScopeKind.INITIATIVE:
                issues = await self._initiative_issues(ref)
            case _:
                assert_never(ref.kind)
        self._check_issue_parents(issues, ref)
        return tuple(issues.values())

    async def approval_parent(
        self, *, ref: ScopeRef, approved_label: str
    ) -> tuple[bool, ScopeRef | None]:
        labels, parent = await self.labels_parent(ref=ref)
        return approved_label in labels, parent

    async def labels_parent(
        self, *, ref: ScopeRef
    ) -> tuple[frozenset[str], ScopeRef | None]:
        """Read labels and the same native parent edge used by metadata.

        Display URLs are not approval evidence. Milestones have no label
        capability; their issue members already report the owning project.
        """
        match ref.kind:
            case ScopeKind.PROJECT:
                project = await self._read(
                    LinearApprovalProjectWire, _TOOL_GET_PROJECT, {"query": ref.key}
                )
                key, labels = project.id, project.labels
                parent = self._parent(project.initiatives, ref)
            case ScopeKind.INITIATIVE:
                initiative = await self._read(
                    LinearApprovalInitiativeWire,
                    _TOOL_GET_INITIATIVE,
                    {"query": ref.key, "includeSubInitiatives": True},
                )
                key, labels = initiative.id, initiative.labels
                parent = self._parent(initiative.parent_initiatives, ref)
            case ScopeKind.ISSUE | ScopeKind.MILESTONE:
                raise ScopeReadError("not an approval container", ref=ref)
            case _:
                assert_never(ref.kind)
        if key != ref.key:
            raise ScopeReadError("container approval identity changed", ref=ref)
        return frozenset(labels), parent

    async def project_milestones(
        self, *, project_key: str
    ) -> tuple[ScopeContainer, ...]:
        ref = ScopeRef(kind=ScopeKind.PROJECT, key=project_key)
        project = await self._project(project_key)
        if project.id != project_key:
            raise ScopeReadError(
                "project milestone lookup returned another identity", ref=ref
            )
        # This tool explicitly returns all milestones and has no cursor input.
        listed = await self._read(
            LinearScopeMilestonesWire, _TOOL_LIST_MILESTONES, {"project": project.id}
        )
        keys = [item.id for item in listed.milestones]
        if len(set(keys)) != len(keys):
            raise ScopeReadError("project milestones repeat a native identity", ref=ref)
        milestones: list[ScopeContainer] = []
        for key in sorted(keys):
            current = await self._read(
                LinearScopeMetadataWire,
                _TOOL_GET_MILESTONE,
                {"project": project.id, "query": key},
            )
            if current.id != key:
                raise ScopeReadError(
                    "milestone detail returned another identity", ref=ref
                )
            milestones.append(
                ScopeContainer(
                    ref=ScopeRef(kind=ScopeKind.MILESTONE, key=key),
                    name=current.name,
                    description=current.description or "",
                    url=None,
                    parent=ref,
                )
            )
        repeated = await self._read(
            LinearScopeMilestonesWire, _TOOL_LIST_MILESTONES, {"project": project.id}
        )
        if sorted(item.id for item in repeated.milestones) != sorted(keys):
            raise ScopeReadError(
                "project milestone membership changed while reading", ref=ref
            )
        return tuple(milestones)

    async def container_metadata(self, *, ref: ScopeRef) -> ScopeContainer:
        if ref.kind is ScopeKind.ISSUE:
            raise ScopeReadError(
                "issue metadata must be read through read_issue", ref=ref
            )
        wire, parent = await self._metadata(ref)
        canonical = ScopeRef(kind=ref.kind, key=wire.id)
        seen = {canonical}
        ancestor = parent
        while ancestor is not None:
            if ancestor in seen:
                raise ScopeReadError("container parent cycle", ref=ref)
            seen.add(ancestor)
            _, ancestor = await self._metadata(ancestor)
        # Linear exposes no canonical milestone URL. Keep its
        # absence rather than borrowing a containing project's address.
        url = None if ref.kind is ScopeKind.MILESTONE else wire.url
        if ref.kind is not ScopeKind.MILESTONE and not url:
            raise ScopeReadError("container URL was not reported", ref=ref)
        return ScopeContainer(
            ref=canonical,
            name=wire.name,
            description=wire.description if wire.description is not None else "",
            url=url,
            parent=parent,
        )

    async def _metadata(
        self,
        ref: ScopeRef,
    ) -> tuple[LinearScopeMetadataWire, ScopeRef | None]:
        match ref.kind:
            case ScopeKind.PROJECT:
                project = await self._project(ref.key)
                return project, self._parent(project.initiatives, ref)
            case ScopeKind.INITIATIVE:
                initiative = await self._initiative(ref.key)
                return initiative, self._parent(initiative.parent_initiatives, ref)
            case ScopeKind.MILESTONE:
                project_key, milestone = await self._milestone(ref)
                return milestone, ScopeRef(kind=ScopeKind.PROJECT, key=project_key)
            case ScopeKind.ISSUE:
                raise ScopeReadError("an issue is not a scope container", ref=ref)
            case _:
                assert_never(ref.kind)

    def _parent(
        self,
        parents: Sequence[LinearScopeIdentityWire],
        ref: ScopeRef,
    ) -> ScopeRef | None:
        keys = {parent.id for parent in parents}
        if len(keys) > 1:
            raise ScopeReadError(
                f"container has multiple parents: {', '.join(sorted(keys))}",
                ref=ref,
            )
        return ScopeRef(kind=ScopeKind.INITIATIVE, key=keys.pop()) if keys else None

    async def _project(self, key: str) -> LinearScopeProjectWire:
        return await self._read(
            LinearScopeProjectWire, _TOOL_GET_PROJECT, {"query": key}
        )

    async def _initiative(self, key: str) -> LinearScopeInitiativeWire:
        return await self._read(
            LinearScopeInitiativeWire,
            _TOOL_GET_INITIATIVE,
            {"query": key, "includeSubInitiatives": True},
        )

    async def _subtree(self, ref: ScopeRef) -> dict[str, TrackerIssue]:
        root = await self._read_issue(issue_key=ref.key)
        found = {root.issue_key: root}
        pending = [root.issue_key]
        for parent_key in pending:
            async for page in self._pages(
                LinearScopeIssuesWire,
                _TOOL_LIST_ISSUES,
                {
                    "parentId": parent_key,
                    "includeArchived": True,
                    "limit": _ISSUE_PAGE_SIZE,
                },
            ):
                for child in page.issues:
                    issue = found.get(child.id)
                    if issue is None:
                        issue = await self._read_issue(issue_key=child.id)
                    if issue.issue_key != child.id:
                        raise ScopeReadError(
                            "issue detail disagrees with its listed identity", ref=ref
                        )
                    if issue.parent_key != parent_key:
                        raise ScopeReadError(
                            "child listing disagrees with its parent", ref=ref
                        )
                    if issue.issue_key in found:
                        continue
                    found[issue.issue_key] = issue
                    pending.append(issue.issue_key)
        return found

    async def _project_issues(
        self,
        project: str,
        *,
        ref: ScopeRef,
        milestone: str | None = None,
    ) -> dict[str, TrackerIssue]:
        found: dict[str, TrackerIssue] = {}
        hydrated_issues: dict[str, TrackerIssue] = {}
        async for page in self._pages(
            LinearScopeIssuesWire,
            _TOOL_LIST_ISSUES,
            {"project": project, "includeArchived": True, "limit": _ISSUE_PAGE_SIZE},
        ):
            for issue in page.issues:
                hydrated = hydrated_issues.get(issue.id)
                if hydrated is None:
                    hydrated = await self._read_issue(issue_key=issue.id)
                    hydrated_issues[issue.id] = hydrated
                if hydrated.issue_key != issue.id:
                    raise ScopeReadError(
                        "issue detail disagrees with its listed identity", ref=ref
                    )
                if hydrated.project_id != project:
                    raise ScopeReadError(
                        "issue moved out of the listed project during the scope read",
                        ref=ref,
                    )
                if milestone is not None:
                    listed_milestone = (
                        issue.project_milestone.id
                        if issue.project_milestone is not None
                        else None
                    )
                    if hydrated.milestone_key != listed_milestone:
                        raise ScopeReadError(
                            "issue milestone changed between listing and detail reads",
                            ref=ref,
                        )
                    if not self._in_milestone(issue, milestone):
                        continue
                found[hydrated.issue_key] = hydrated
        return found

    @staticmethod
    def _in_milestone(issue: LinearScopeIssueWire, milestone: str | None) -> bool:
        return milestone is None or (
            issue.project_milestone is not None
            and issue.project_milestone.id == milestone
        )

    async def _initiative_issues(self, ref: ScopeRef) -> dict[str, TrackerIssue]:
        found: dict[str, TrackerIssue] = {}
        pending = [ref.key]
        seen: set[str] = set()
        projects: set[str] = set()
        children: dict[str, set[str]] = {}
        for key in pending:
            if key in seen:
                continue
            initiative = await self._initiative(key)
            # The requested root may be a name; traversed child links carry IDs.
            if seen and initiative.id != key:
                raise ScopeReadError(
                    "initiative child lookup returned a different identity", ref=ref
                )
            if initiative.id in seen:
                continue
            seen.add(initiative.id)
            children[initiative.id] = {child.id for child in initiative.sub_initiatives}
            pending.extend(child.id for child in initiative.sub_initiatives)
            async for page in self._pages(
                LinearScopeProjectsWire,
                _TOOL_LIST_PROJECTS,
                {
                    "initiative": initiative.id,
                    "includeArchived": True,
                    "limit": _PROJECT_PAGE_SIZE,
                },
            ):
                for project in page.projects:
                    if project.id not in projects:
                        projects.add(project.id)
                        found.update(await self._project_issues(project.id, ref=ref))
        checked: set[str] = set()
        for start in children:
            active: set[str] = set()
            chain = [(start, False)]
            while chain:
                current, leaving = chain.pop()
                if leaving:
                    active.remove(current)
                    checked.add(current)
                elif current in active:
                    raise ScopeReadError("initiative child cycle", ref=ref)
                elif current not in checked:
                    active.add(current)
                    chain.append((current, True))
                    chain.extend((child, False) for child in children[current])
        return found

    async def _milestone(self, ref: ScopeRef) -> tuple[str, LinearScopeMetadataWire]:
        matches: set[tuple[str, str]] = set()
        seen_projects: set[str] = set()
        async for page in self._pages(
            LinearScopeProjectsWire,
            _TOOL_LIST_PROJECTS,
            {"includeArchived": True, "limit": _PROJECT_PAGE_SIZE},
        ):
            for project in page.projects:
                if project.id in seen_projects:
                    continue
                seen_projects.add(project.id)
                milestones = await self._read(
                    LinearScopeMilestonesWire,
                    _TOOL_LIST_MILESTONES,
                    {"project": project.id},
                )
                matches.update(
                    (project.id, milestone.id)
                    for milestone in milestones.milestones
                    if ref.key in {milestone.id, milestone.name}
                )
        if len(matches) != 1:
            raise ScopeReadError(
                "milestone is missing"
                if not matches
                else "milestone address is ambiguous",
                ref=ref,
            )
        project_key, key = matches.pop()
        milestone = await self._read(
            LinearScopeMetadataWire,
            _TOOL_GET_MILESTONE,
            {"project": project_key, "query": key},
        )
        if milestone.id != key:
            raise ScopeReadError(
                "milestone lookup returned a different identity", ref=ref
            )
        return project_key, milestone

    @staticmethod
    def _check_issue_parents(issues: Mapping[str, TrackerIssue], ref: ScopeRef) -> None:
        checked: set[str] = set()
        for issue in issues.values():
            path: set[str] = set()
            key: str | None = issue.issue_key
            while key is not None and key in issues and key not in checked:
                if key in path:
                    raise ScopeReadError("issue parent cycle", ref=ref)
                path.add(key)
                key = issues[key].parent_key
            checked.update(path)

    async def _pages[PageT: LinearScopePageWire](
        self,
        shape: type[PageT],
        tool: str,
        arguments: Mapping[str, object],
    ) -> AsyncIterator[PageT]:
        async def read(request: Mapping[str, object]) -> tuple[PageT, bool, str | None]:
            page = await self._read(shape, tool, request)
            return page, page.has_next_page, page.cursor

        async for page in cursor_pages(
            read,
            arguments=arguments,
            refusal=lambda _: TrackerProtocolError(
                "scope pagination cannot advance",
                tool=tool,
                detail="missing or repeated cursor",
            ),
        ):
            yield page

    async def _read[WireT: LinearWireModel](
        self,
        shape: type[WireT],
        tool: str,
        arguments: Mapping[str, object],
    ) -> WireT:
        payload = await self._call(tool, arguments)
        try:
            return shape.model_validate(payload)
        except ValidationError as exc:
            raise TrackerProtocolError(
                "scope response does not match its declared shape",
                tool=tool,
                detail=str(exc),
            ) from exc
