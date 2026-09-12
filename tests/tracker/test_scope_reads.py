"""Scope reads preserve membership and refuse incomplete backend evidence.

The local MCP fixture uses the connected-app read shapes measured on
2026-09-07. This is not a fresh capture using the service credential.
Milestone payloads deliberately carry no URL, as in that measurement.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pytest

from kodezart.core.errors import (
    McpTransportError,
    TrackerProtocolError,
    TrackerUnavailableError,
)
from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import ScopeReadError
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.tracker import (
    IssuePriority,
    IssueRelation,
    IssueRelationKind,
    TrackerIssue,
    WorkflowStateKind,
)
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue, FakeTrackerPort
from tests.tracker.conftest import FIXTURE_NOW, linear_over_fake_mcp

INITIATIVE = ScopeRef(kind=ScopeKind.INITIATIVE, key="initiative-one")
PROJECT = ScopeRef(kind=ScopeKind.PROJECT, key="project-one")
MILESTONE = ScopeRef(kind=ScopeKind.MILESTONE, key="milestone-one")
ROOT = ScopeRef(kind=ScopeKind.ISSUE, key="FIX-1")
OTHER_PROJECT = "project-two"
EMPTY_PROJECT = ScopeRef(kind=ScopeKind.PROJECT, key="empty-project")
EMPTY_INITIATIVE = ScopeRef(kind=ScopeKind.INITIATIVE, key="empty-initiative")
PAGE_SIZE = 2


@dataclass
class ScopeMcpIssue(FakeMcpIssue):
    """Membership fields observed on issue listings, absent from the old fake."""

    project_key: str = PROJECT.key
    milestone_key: str | None = MILESTONE.key

    def entry(self) -> dict[str, object]:
        return {
            **super().entry(),
            "project": self.project_key,
            "projectId": self.project_key,
            "projectMilestone": (
                None
                if self.milestone_key is None
                else {"id": self.milestone_key, "name": self.milestone_key}
            ),
        }


def _container(ref: ScopeRef, parent: ScopeRef | None = None) -> ScopeContainer:
    """Backend-neutral metadata for the domain double, not a Linear URL claim."""
    return ScopeContainer(
        ref=ref,
        name=ref.key,
        description=f"Complete description of {ref.key}",
        url=(
            None
            if ref.kind is ScopeKind.MILESTONE
            else f"https://tracker.invalid/{ref.kind.value}/{ref.key}"
        ),
        parent=parent,
    )


def _project(ref: ScopeRef, initiatives: Sequence[ScopeRef]) -> dict[str, object]:
    container = _container(ref)
    return {
        "id": ref.key,
        "name": container.name,
        "description": container.description,
        "url": container.url,
        "initiatives": [{"id": item.key, "name": item.key} for item in initiatives],
        "labels": [],
    }


def _initiative(ref: ScopeRef, projects: Sequence[str]) -> dict[str, object]:
    container = _container(ref)
    return {
        "id": ref.key,
        "name": container.name,
        "description": container.description,
        "url": container.url,
        "parentInitiatives": [],
        "projects": [{"id": key, "name": key} for key in projects],
        "subInitiatives": [],
        "labels": [],
    }


def _milestone(key: str) -> dict[str, object]:
    return {
        "id": key,
        "name": key,
        "description": f"Complete description of {key}",
        "progress": 0,
        "sortOrder": 0,
    }


class ScopeMcpServer(FakeLinearMcpServer):
    """Only the scope-related reads missing from the shared MCP double."""

    def __init__(self) -> None:
        issues = [
            ScopeMcpIssue(
                id=ROOT.key,
                description="The complete root body",
                relations=[("blocks", "FIX-4")],
            ),
            ScopeMcpIssue(id="FIX-2", parent_id=ROOT.key),
            ScopeMcpIssue(
                id="FIX-3",
                parent_id="FIX-2",
                project_key=OTHER_PROJECT,
                milestone_key="milestone-two",
            ),
            ScopeMcpIssue(id="FIX-4", parent_id="FOREIGN-ROOT", milestone_key=None),
            ScopeMcpIssue(
                id="FIX-5",
                project_key=OTHER_PROJECT,
                milestone_key="milestone-two",
            ),
        ]
        super().__init__(
            issues=issues,
            projects={
                PROJECT.key: _project(PROJECT, [INITIATIVE]),
                OTHER_PROJECT: _project(
                    ScopeRef(kind=ScopeKind.PROJECT, key=OTHER_PROJECT),
                    [INITIATIVE],
                ),
                EMPTY_PROJECT.key: _project(EMPTY_PROJECT, []),
            },
        )
        self.initiatives: dict[str, dict[str, object]] = {
            INITIATIVE.key: _initiative(INITIATIVE, [PROJECT.key, OTHER_PROJECT]),
            EMPTY_INITIATIVE.key: _initiative(EMPTY_INITIATIVE, []),
        }
        self.milestones: dict[str, list[dict[str, object]]] = {
            PROJECT.key: [_milestone(MILESTONE.key)],
            OTHER_PROJECT: [_milestone("milestone-two")],
            EMPTY_PROJECT.key: [],
        }
        self.overlap_pages = False
        self.repeat_cursor = False
        self.omit_cursor = False
        self.issue_detail_updates: dict[str, Mapping[str, object]] = {}

    def _page(
        self,
        name: str,
        rows: Sequence[Mapping[str, object]],
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        cursor = arguments.get("cursor")
        assert cursor is None or isinstance(cursor, str)
        start = 0 if cursor is None else int(cursor.removeprefix("offset:"))
        stop = start + PAGE_SIZE
        entries = list(rows[start:stop])
        if self.overlap_pages and start:
            entries.insert(0, rows[start - 1])
        result: dict[str, object] = {
            name: entries,
            "hasNextPage": stop < len(rows),
        }
        if stop < len(rows):
            if self.repeat_cursor:
                result["cursor"] = "offset:0"
            elif not self.omit_cursor:
                result["cursor"] = f"offset:{stop}"
        return result

    def _tool_list_issues(
        self, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        rows = [issue.entry() for issue in self.issues.values()]
        if "project" in arguments:
            rows = [row for row in rows if row["projectId"] == arguments["project"]]
        if "parentId" in arguments:
            rows = [row for row in rows if row["parentId"] == arguments["parentId"]]
        # Lists may abbreviate descriptions and never report relations.
        return self._page(
            "issues",
            [{**row, "description": "truncated list body"} for row in rows],
            arguments,
        )

    def _tool_list_projects(
        self, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        rows = list(self.projects.values())
        if "initiative" in arguments:
            initiative = self.initiatives[str(arguments["initiative"])]
            members = initiative["projects"]
            assert isinstance(members, list)
            keys = {member["id"] for member in members if isinstance(member, dict)}
            rows = [row for row in rows if row["id"] in keys]
        return self._page("projects", rows, arguments)

    def _tool_get_initiative(
        self, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        query = str(arguments["query"])
        if query not in self.initiatives:
            raise McpTransportError(
                "the vendor refused the requested initiative",
                server_name="fixture-linear",
                tool_name="get_initiative",
            )
        return self.initiatives[query]

    def _tool_get_project(
        self, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        if arguments["query"] not in self.projects:
            raise McpTransportError(
                "the vendor refused the requested project",
                server_name="fixture-linear",
                tool_name="get_project",
            )
        return super()._tool_get_project(arguments)

    def _tool_get_issue(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        if arguments["id"] not in self.issues:
            raise McpTransportError(
                "the vendor refused the requested issue",
                server_name="fixture-linear",
                tool_name="get_issue",
            )
        return {
            **super()._tool_get_issue(arguments),
            **self.issue_detail_updates.get(str(arguments["id"]), {}),
        }

    def _tool_list_milestones(
        self, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        return {"milestones": list(self.milestones[str(arguments["project"])])}

    def _tool_get_milestone(
        self, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        query = arguments["query"]
        for milestone in self.milestones[str(arguments["project"])]:
            if query in (milestone["id"], milestone["name"]):
                return milestone
        raise LookupError(f"No fixture milestone {query!r}")


def _domain_issue(issue: FakeMcpIssue) -> TrackerIssue:
    return TrackerIssue(
        issue_key=issue.id,
        title=issue.title,
        body=issue.description,
        priority=IssuePriority.NONE,
        state_name=issue.status,
        state_kind=WorkflowStateKind.BACKLOG,
        queue_states=frozenset(),
        team_key="engineering",
        parent_key=issue.parent_id,
        relations=(
            (IssueRelation(kind=IssueRelationKind.BLOCKS, issue_key="FIX-4"),)
            if issue.id == ROOT.key
            else ()
        ),
        created_at=FIXTURE_NOW,
        updated_at=FIXTURE_NOW,
        url=f"https://tracker.invalid/issue/{issue.id}",
    )


@dataclass
class ScopeFixture:
    tracker: TrackerPort
    server: ScopeMcpServer
    fake: FakeTrackerPort


@pytest.fixture(params=["linear", "fake"])
def scope_fixture(request: pytest.FixtureRequest) -> ScopeFixture:
    server = ScopeMcpServer()
    fake = FakeTrackerPort(
        issues=[_domain_issue(issue) for issue in server.issues.values()],
        scope_containers=[
            _container(INITIATIVE),
            _container(PROJECT, INITIATIVE),
            _container(MILESTONE, PROJECT),
            _container(EMPTY_PROJECT),
            _container(EMPTY_INITIATIVE),
        ],
        scope_memberships={
            INITIATIVE: list(server.issues),
            PROJECT: [ROOT.key, "FIX-2", "FIX-4"],
            MILESTONE: [ROOT.key, "FIX-2"],
            EMPTY_PROJECT: [],
            EMPTY_INITIATIVE: [],
        },
    )
    tracker = linear_over_fake_mcp(server) if request.param == "linear" else fake
    return ScopeFixture(tracker=tracker, server=server, fake=fake)


async def test_issue_scope_includes_root_and_all_descendants_across_containers(
    scope_fixture: ScopeFixture,
) -> None:
    issues = await scope_fixture.tracker.scope_issues(ref=ROOT)

    assert {issue.issue_key for issue in issues} == {ROOT.key, "FIX-2", "FIX-3"}
    assert len(issues) == len({issue.issue_key for issue in issues})
    root = next(issue for issue in issues if issue.issue_key == ROOT.key)
    assert root.body == "The complete root body"
    assert root.relations == (
        IssueRelation(kind=IssueRelationKind.BLOCKS, issue_key="FIX-4"),
    )


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (PROJECT, {ROOT.key, "FIX-2", "FIX-4"}),
        (MILESTONE, {ROOT.key, "FIX-2"}),
        (INITIATIVE, {ROOT.key, "FIX-2", "FIX-3", "FIX-4", "FIX-5"}),
    ],
)
async def test_container_membership_is_independent_of_issue_parent_edges(
    scope_fixture: ScopeFixture, ref: ScopeRef, expected: set[str]
) -> None:
    issues = await scope_fixture.tracker.scope_issues(ref=ref)

    assert {issue.issue_key for issue in issues} == expected
    assert len(issues) == len(expected)
    root = next(issue for issue in issues if issue.issue_key == ROOT.key)
    assert root.body == "The complete root body"
    assert root.relations == (
        IssueRelation(kind=IssueRelationKind.BLOCKS, issue_key="FIX-4"),
    )


@pytest.mark.parametrize("ref", [EMPTY_PROJECT, EMPTY_INITIATIVE])
async def test_existing_empty_container_returns_an_empty_issue_set(
    scope_fixture: ScopeFixture, ref: ScopeRef
) -> None:
    assert tuple(await scope_fixture.tracker.scope_issues(ref=ref)) == ()


@pytest.mark.parametrize("kind", list(ScopeKind))
async def test_missing_scope_refuses_with_the_requested_address(
    scope_fixture: ScopeFixture, kind: ScopeKind
) -> None:
    ref = ScopeRef(kind=kind, key="absent-scope")

    with pytest.raises((ScopeReadError, TrackerUnavailableError)) as caught:
        await scope_fixture.tracker.scope_issues(ref=ref)

    failure = caught.value
    if isinstance(failure, ScopeReadError):
        assert failure.ref == ref
    else:
        assert isinstance(failure.__cause__, McpTransportError)
        assert failure.__cause__.tool_name in {
            "get_issue",
            "get_project",
            "get_initiative",
        }


@pytest.mark.parametrize("ref", [INITIATIVE, PROJECT, MILESTONE])
async def test_container_metadata_returns_only_the_five_domain_fields(
    scope_fixture: ScopeFixture, ref: ScopeRef
) -> None:
    container = await scope_fixture.tracker.container_metadata(ref=ref)

    expected_parent = (
        PROJECT if ref == MILESTONE else INITIATIVE if ref == PROJECT else None
    )
    assert container == _container(ref, expected_parent)
    assert set(container.model_dump()) == {
        "ref",
        "name",
        "description",
        "url",
        "parent",
    }


async def test_issue_metadata_uses_a_typed_refusal(
    scope_fixture: ScopeFixture,
) -> None:
    with pytest.raises(ScopeReadError) as caught:
        await scope_fixture.tracker.container_metadata(ref=ROOT)

    assert caught.value.ref == ROOT


async def test_issue_parent_cycle_refuses_instead_of_returning_partial_membership(
    scope_fixture: ScopeFixture,
) -> None:
    scope_fixture.server.issues[ROOT.key].parent_id = "FIX-2"
    scope_fixture.fake.issues[ROOT.key] = scope_fixture.fake.issues[
        ROOT.key
    ].model_copy(update={"parent_key": "FIX-2"})

    with pytest.raises(ScopeReadError) as caught:
        await scope_fixture.tracker.scope_issues(ref=ROOT)

    assert caught.value.ref == ROOT


@pytest.mark.parametrize("ref", [PROJECT, MILESTONE])
async def test_linear_exhausts_pages_deduplicates_and_hydrates_each_issue(
    ref: ScopeRef,
) -> None:
    server = ScopeMcpServer()
    server.overlap_pages = True

    issues = await linear_over_fake_mcp(server).scope_issues(ref=ref)

    expected = {ROOT.key, "FIX-2", "FIX-4"} if ref == PROJECT else {ROOT.key, "FIX-2"}
    assert {issue.issue_key for issue in issues} == expected
    assert len(issues) == len({issue.issue_key for issue in issues})
    assert any("cursor" in args for args in server.tool_calls("list_issues"))
    reads = server.tool_calls("get_issue")
    assert {args["id"] for args in reads} == {ROOT.key, "FIX-2", "FIX-4"}
    assert len(reads) == 3
    assert all(args.get("includeRelations") is True for args in reads)


@pytest.mark.parametrize("fault", ["repeat_cursor", "omit_cursor"])
async def test_linear_refuses_unusable_pagination(fault: str) -> None:
    server = ScopeMcpServer()
    setattr(server, fault, True)

    with pytest.raises(TrackerProtocolError) as caught:
        await linear_over_fake_mcp(server).scope_issues(ref=PROJECT)

    assert caught.value.tool == "list_issues"


@pytest.mark.parametrize("ref", [PROJECT, INITIATIVE])
async def test_linear_refuses_multiple_metadata_parents(ref: ScopeRef) -> None:
    server = ScopeMcpServer()
    parents = [{"id": "parent-one", "name": "one"}, {"id": "parent-two", "name": "two"}]
    if ref == PROJECT:
        server.projects[ref.key] = {**server.projects[ref.key], "initiatives": parents}
    else:
        server.initiatives[ref.key]["parentInitiatives"] = parents

    with pytest.raises(ScopeReadError) as caught:
        await linear_over_fake_mcp(server).container_metadata(ref=ref)

    assert caught.value.ref == ref


async def test_linear_milestone_metadata_preserves_the_measured_missing_url() -> None:
    server = ScopeMcpServer()

    result = await linear_over_fake_mcp(server).container_metadata(ref=MILESTONE)
    assert result.ref == MILESTONE
    assert result.parent == PROJECT
    assert result.url is None
    assert result.model_dump(mode="json")["url"] is None
    assert "url" not in server.milestones[PROJECT.key][0]


@pytest.mark.parametrize("ref", [PROJECT, INITIATIVE])
@pytest.mark.parametrize("url", [None, ""])
async def test_other_container_kinds_still_require_their_native_url(ref, url):
    server = ScopeMcpServer()
    table = server.projects if ref == PROJECT else server.initiatives
    table[ref.key]["url"] = url
    with pytest.raises(ScopeReadError, match="URL"):
        await linear_over_fake_mcp(server).container_metadata(ref=ref)


async def test_milestone_never_substitutes_an_unsupported_payload_url():
    server = ScopeMcpServer()
    server.milestones[PROJECT.key][0]["url"] = server.projects[PROJECT.key]["url"]
    result = await linear_over_fake_mcp(server).container_metadata(ref=MILESTONE)
    assert result.url is None


async def test_linear_resolves_milestones_beyond_the_first_project_page() -> None:
    server = ScopeMcpServer()
    server.milestones[PROJECT.key] = []
    server.milestones[EMPTY_PROJECT.key] = [_milestone(MILESTONE.key)]
    server.issues["FIX-4"] = ScopeMcpIssue(
        id="FIX-4", project_key=EMPTY_PROJECT.key, milestone_key=MILESTONE.key
    )

    issues = await linear_over_fake_mcp(server).scope_issues(ref=MILESTONE)

    assert {issue.issue_key for issue in issues} == {"FIX-4"}
    assert any("cursor" in args for args in server.tool_calls("list_projects"))
    assert {args["project"] for args in server.tool_calls("list_milestones")} == {
        PROJECT.key,
        OTHER_PROJECT,
        EMPTY_PROJECT.key,
    }


async def test_linear_resolves_changed_milestone_membership_on_each_call() -> None:
    server = ScopeMcpServer()
    tracker = linear_over_fake_mcp(server)
    assert {issue.issue_key for issue in await tracker.scope_issues(ref=MILESTONE)} == {
        ROOT.key,
        "FIX-2",
    }
    server.milestones[PROJECT.key] = []
    server.milestones[OTHER_PROJECT] = [_milestone(MILESTONE.key)]
    server.issues["FIX-5"] = ScopeMcpIssue(
        id="FIX-5", project_key=OTHER_PROJECT, milestone_key=MILESTONE.key
    )

    issues = await tracker.scope_issues(ref=MILESTONE)

    assert {issue.issue_key for issue in issues} == {"FIX-5"}


@pytest.mark.parametrize("ref", [PROJECT, MILESTONE, INITIATIVE])
@pytest.mark.parametrize("project_id", [OTHER_PROJECT, None])
async def test_linear_refuses_project_membership_changes_between_list_and_detail(
    ref: ScopeRef, project_id: str | None
) -> None:
    server = ScopeMcpServer()
    server.issue_detail_updates[ROOT.key] = {"projectId": project_id}

    with pytest.raises(ScopeReadError, match="project") as caught:
        await linear_over_fake_mcp(server).scope_issues(ref=ref)

    assert caught.value.ref == ref


@pytest.mark.parametrize(
    ("listed_milestone", "detail_milestone"),
    [
        (MILESTONE.key, None),
        (MILESTONE.key, "milestone-two"),
        (None, MILESTONE.key),
        ("milestone-two", MILESTONE.key),
    ],
)
async def test_linear_refuses_milestone_membership_changes_between_list_and_detail(
    listed_milestone: str | None, detail_milestone: str | None
) -> None:
    server = ScopeMcpServer()
    root = server.issues[ROOT.key]
    assert isinstance(root, ScopeMcpIssue)
    root.milestone_key = listed_milestone
    server.issue_detail_updates[ROOT.key] = {
        "projectMilestone": (
            None if detail_milestone is None else {"id": detail_milestone}
        ),
    }

    with pytest.raises(ScopeReadError, match="milestone") as caught:
        await linear_over_fake_mcp(server).scope_issues(ref=MILESTONE)

    assert caught.value.ref == MILESTONE


async def test_linear_empty_milestone_verifies_the_project_issue_details() -> None:
    server = ScopeMcpServer()
    for issue in server.issues.values():
        assert isinstance(issue, ScopeMcpIssue)
        issue.milestone_key = None

    assert tuple(await linear_over_fake_mcp(server).scope_issues(ref=MILESTONE)) == ()
    assert {args["id"] for args in server.tool_calls("get_issue")} == {
        ROOT.key,
        "FIX-2",
        "FIX-4",
    }


async def test_linear_refuses_issue_detail_with_a_different_identity() -> None:
    server = ScopeMcpServer()
    server.issue_detail_updates[ROOT.key] = {"id": "different-issue"}

    with pytest.raises(ScopeReadError, match="identity") as caught:
        await linear_over_fake_mcp(server).scope_issues(ref=PROJECT)

    assert caught.value.ref == PROJECT


async def test_linear_resolves_changed_project_membership_on_each_call() -> None:
    server = ScopeMcpServer()
    tracker = linear_over_fake_mcp(server)
    assert {issue.issue_key for issue in await tracker.scope_issues(ref=PROJECT)} == {
        ROOT.key,
        "FIX-2",
        "FIX-4",
    }
    leaving = server.issues["FIX-2"]
    entering = server.issues["FIX-3"]
    assert isinstance(leaving, ScopeMcpIssue)
    assert isinstance(entering, ScopeMcpIssue)
    leaving.project_key = OTHER_PROJECT
    entering.project_key = PROJECT.key

    issues = await tracker.scope_issues(ref=PROJECT)

    assert {issue.issue_key for issue in issues} == {ROOT.key, "FIX-3", "FIX-4"}


@pytest.mark.parametrize(
    "edges",
    [
        {INITIATIVE.key: [INITIATIVE.key]},
        {INITIATIVE.key: ["child"], "child": [INITIATIVE.key]},
        {
            INITIATIVE.key: ["child"],
            "child": ["grandchild"],
            "grandchild": ["child"],
        },
    ],
)
async def test_linear_refuses_initiative_cycles_visible_only_in_child_links(
    edges: Mapping[str, Sequence[str]],
) -> None:
    server = ScopeMcpServer()
    for key, children in edges.items():
        ref = ScopeRef(kind=ScopeKind.INITIATIVE, key=key)
        server.initiatives[key] = {
            **_initiative(ref, []),
            "subInitiatives": [{"id": child} for child in children],
        }

    with pytest.raises(ScopeReadError, match="initiative child cycle") as caught:
        await linear_over_fake_mcp(server).scope_issues(ref=INITIATIVE)

    assert caught.value.ref == INITIATIVE
    assert all(not wire["parentInitiatives"] for wire in server.initiatives.values())
    assert len(server.tool_calls("get_initiative")) == len(edges)


async def test_linear_initiative_membership_allows_shared_children_and_projects() -> (
    None
):
    server = ScopeMcpServer()
    edges = {
        INITIATIVE.key: ["left", "right"],
        "left": ["shared"],
        "right": ["shared"],
        "shared": [],
    }
    projects = {
        INITIATIVE.key: [PROJECT.key],
        "left": [PROJECT.key],
        "right": [],
        "shared": [OTHER_PROJECT],
    }
    for key, children in edges.items():
        ref = ScopeRef(kind=ScopeKind.INITIATIVE, key=key)
        server.initiatives[key] = {
            **_initiative(ref, projects[key]),
            "subInitiatives": [{"id": child} for child in children],
        }
    server.initiatives["shared"]["parentInitiatives"] = [
        {"id": "left"},
        {"id": "right"},
    ]
    tracker = linear_over_fake_mcp(server)

    issues = await tracker.scope_issues(ref=INITIATIVE)

    assert {issue.issue_key for issue in issues} == set(server.issues)
    assert len(issues) == len(server.issues)
    initiative_reads = server.tool_calls("get_initiative")
    assert {args["query"] for args in initiative_reads} == set(edges)
    assert len(initiative_reads) == len(edges)
    assert len(server.tool_calls("get_issue")) == len(server.issues)
    shared = ScopeRef(kind=ScopeKind.INITIATIVE, key="shared")
    with pytest.raises(ScopeReadError, match="multiple parents") as caught:
        await tracker.container_metadata(ref=shared)
    assert caught.value.ref == shared


async def test_linear_refuses_a_child_issue_with_a_different_identity() -> None:
    server = ScopeMcpServer()
    server.issue_detail_updates["FIX-2"] = {"id": "different-issue"}

    with pytest.raises(ScopeReadError, match="identity") as caught:
        await linear_over_fake_mcp(server).scope_issues(ref=ROOT)

    assert caught.value.ref == ROOT


async def test_linear_refuses_a_child_initiative_with_a_different_identity() -> None:
    server = ScopeMcpServer()
    server.initiatives[INITIATIVE.key]["subInitiatives"] = [{"id": "child"}]
    server.initiatives["child"] = _initiative(EMPTY_INITIATIVE, [])

    with pytest.raises(ScopeReadError, match="different identity") as caught:
        await linear_over_fake_mcp(server).scope_issues(ref=INITIATIVE)

    assert caught.value.ref == INITIATIVE


async def test_linear_initiative_root_may_be_addressed_by_name() -> None:
    server = ScopeMcpServer()
    named_ref = ScopeRef(kind=ScopeKind.INITIATIVE, key="Initiative name")
    server.initiatives[named_ref.key] = server.initiatives[INITIATIVE.key]
    server.initiatives[INITIATIVE.key]["subInitiatives"] = [
        {"id": EMPTY_INITIATIVE.key},
    ]

    issues = await linear_over_fake_mcp(server).scope_issues(ref=named_ref)

    assert {issue.issue_key for issue in issues} == set(server.issues)


async def test_linear_resolves_changed_initiative_children_on_each_call() -> None:
    server = ScopeMcpServer()
    server.initiatives[INITIATIVE.key] = _initiative(INITIATIVE, [PROJECT.key])
    server.initiatives[EMPTY_INITIATIVE.key] = _initiative(
        EMPTY_INITIATIVE, [OTHER_PROJECT]
    )
    tracker = linear_over_fake_mcp(server)
    assert {
        issue.issue_key for issue in await tracker.scope_issues(ref=INITIATIVE)
    } == {
        ROOT.key,
        "FIX-2",
        "FIX-4",
    }
    server.initiatives[INITIATIVE.key]["subInitiatives"] = [
        {"id": EMPTY_INITIATIVE.key},
    ]

    issues = await tracker.scope_issues(ref=INITIATIVE)

    assert {issue.issue_key for issue in issues} == set(server.issues)


@pytest.mark.parametrize("ref", [PROJECT, INITIATIVE])
async def test_linear_container_metadata_refuses_a_parent_cycle(ref: ScopeRef) -> None:
    server = ScopeMcpServer()
    server.initiatives[INITIATIVE.key]["parentInitiatives"] = [
        {"id": EMPTY_INITIATIVE.key},
    ]
    server.initiatives[EMPTY_INITIATIVE.key]["parentInitiatives"] = [
        {"id": INITIATIVE.key},
    ]

    with pytest.raises(ScopeReadError, match="container parent cycle") as caught:
        await linear_over_fake_mcp(server).container_metadata(ref=ref)

    assert caught.value.ref == ref
