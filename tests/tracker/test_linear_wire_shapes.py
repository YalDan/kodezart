"""The wire models, against payloads shaped like the LIVE server's (KOD-143).

Every fixture here is derived from a capture of the real tool, probed
against the live workspace on 2026-08-25: the KEYS, the nesting and the
null-vs-absent distinctions are the vendor's, and only the values are
synthesized.  That split is the whole point of the module.  The first
version of these models was authored from the vendor's documentation and
five of its shapes were structurally wrong, so a fixture written from the
model rather than from a capture would agree with the model and disagree
with the workspace — which is exactly the failure that shipped.

The server declares no ``outputSchema`` for any of its sixty tools, so
measurement is not a phase that ends: it is the only way these shapes can
ever be known, and re-measuring is what changing one costs.

Refusal tests run in the surviving direction.  The old invented shapes
cannot be instantiated any more, so what is asserted is that a payload in
the OLD shape no longer validates: the ``entries`` envelope and the
``user``/``issueId`` comment are gone rather than merely unused.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.core.protocols import McpToolResult
from kodezart.types.domain.linear_mcp import (
    LINEAR_NAMED_ARRAY,
    LinearCommentListWire,
    LinearCommentWire,
    LinearDocumentListWire,
    LinearIssueDetailWire,
    LinearIssueListWire,
    LinearIssueWire,
    LinearLabelListWire,
    LinearTeamListWire,
    LinearUserListWire,
)
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.tracker import (
    IssueQuery,
    IssueRelationKind,
    MappingKind,
    MappingRef,
)

# --------------------------------------------------------------------------
# Captures — vendor keys, synthesized values.
# --------------------------------------------------------------------------

TEAM_NAME = "fixture-board"
TEAM_ID = "1ea11111-0000-4000-8000-000000000001"
APPROVER_NAME = "Fixture Approver"
APPROVER_ID = "1ea11111-0000-4000-8000-000000000002"

LIST_ISSUE_LABELS: Mapping[str, object] = {
    "labels": [
        {
            "id": "1ab11111-0000-4000-8000-000000000001",
            "name": "queue:approved",
            "color": "#4cb782",
            "description": "Approved to fire. Promotion = fire authorization.",
        },
        {
            "id": "1ab11111-0000-4000-8000-000000000002",
            "name": "queue:proposed",
            "color": "#e2a336",
            "description": None,
        },
    ],
    "hasNextPage": False,
}

LIST_TEAMS: Mapping[str, object] = {
    "teams": [
        {
            "id": TEAM_ID,
            "name": TEAM_NAME,
            "createdAt": "2026-07-14T12:42:41.420Z",
            "updatedAt": "2026-08-25T00:18:46.363Z",
        },
    ],
    "hasNextPage": False,
}

LIST_USERS: Mapping[str, object] = {
    "users": [
        {
            "id": APPROVER_ID,
            "name": APPROVER_NAME,
            "email": "approver@example.invalid",
            "displayName": "fixture.approver",
            "isAdmin": True,
            "isGuest": False,
            "isActive": True,
            "createdAt": "2026-07-13T10:49:05.790Z",
            "updatedAt": "2026-08-23T21:29:02.673Z",
            "status": "Offline (last seen 2026-08-24T20:34:31.103Z)",
        },
    ],
    "hasNextPage": False,
}

#: A BARE ARRAY. Not an envelope, not a key — the payload IS the list.
LIST_ISSUE_STATUSES: Sequence[Mapping[str, object]] = [
    {
        "id": "1a511111-0000-4000-8000-000000000001",
        "type": "backlog",
        "name": "Backlog",
    },
    {
        "id": "1a511111-0000-4000-8000-000000000002",
        "type": "started",
        "name": "In Progress",
    },
    {
        "id": "1a511111-0000-4000-8000-000000000003",
        "type": "completed",
        "name": "Done",
    },
]

#: One ``list_issues`` entry, whole. No relations, no attachments, no
#: documents: the listing carries none of the three.
LIST_ISSUES_ENTRY: Mapping[str, object] = {
    "id": "FIX-11",
    "title": "a fixture issue",
    "description": "the body, as the listing truncates it… (truncated)",
    "projectMilestone": {
        "id": "1a411111-0000-4000-8000-000000000001",
        "name": "1 · a milestone",
    },
    "priority": {"value": 2, "name": "High"},
    "url": "https://tracker.invalid/issue/fix-11",
    "gitBranchName": "fixture/fix-11-a-fixture-issue",
    "createdAt": "2026-08-12T18:37:24.011Z",
    "updatedAt": "2026-08-25T09:21:51.622Z",
    "archivedAt": None,
    "completedAt": None,
    "startedAt": "2026-08-17T06:52:52.439Z",
    "canceledAt": None,
    "dueDate": None,
    "slaStartedAt": None,
    "slaMediumRiskAt": None,
    "slaHighRiskAt": None,
    "slaBreachesAt": None,
    "status": "In Progress",
    "statusType": "started",
    "labels": ["queue:approved"],
    "createdBy": APPROVER_NAME,
    "createdById": APPROVER_ID,
    "assignee": APPROVER_NAME,
    "assigneeId": APPROVER_ID,
    "project": "a delivery project",
    "projectId": "1a311111-0000-4000-8000-000000000001",
    "parentId": "FIX-10",
    "team": TEAM_NAME,
    "teamId": TEAM_ID,
}

LIST_ISSUES: Mapping[str, object] = {
    "issues": [LIST_ISSUES_ENTRY],
    "hasNextPage": True,
    "cursor": "1a211111-0000-4000-8000-000000000001",
}

#: ``get_issue`` with ``includeRelations``. The relations object carries
#: all four arms; ``duplicateOf`` is single-valued and null when unset.
GET_ISSUE: Mapping[str, object] = {
    "id": "FIX-12",
    "title": "a fixture issue with relations",
    "description": "the body, whole",
    "priority": {"value": 1, "name": "Urgent"},
    "url": "https://tracker.invalid/issue/fix-12",
    "gitBranchName": "fixture/fix-12-a-fixture-issue-with-relations",
    "createdAt": "2026-07-24T12:56:00.185Z",
    "updatedAt": "2026-08-25T09:26:39.217Z",
    "archivedAt": None,
    "completedAt": None,
    "startedAt": "2026-08-09T13:19:28.435Z",
    "canceledAt": None,
    "dueDate": None,
    "slaStartedAt": None,
    "slaMediumRiskAt": None,
    "slaHighRiskAt": None,
    "slaBreachesAt": None,
    "status": "Backlog",
    "statusType": "backlog",
    "labels": ["queue:approved"],
    "attachments": [
        {
            "id": "1a111111-0000-4000-8000-000000000001",
            "title": "a linked pull request",
            "subtitle": None,
            "url": "https://forge.invalid/fixture/pull/1",
        },
    ],
    "documents": [],
    "stateHistory": [
        {
            "state": {
                "id": "1a511111-0000-4000-8000-000000000001",
                "name": "Backlog",
                "type": "backlog",
            },
            "startedAt": "2026-07-24T12:56:00.185Z",
            "endedAt": None,
        },
    ],
    "createdBy": APPROVER_NAME,
    "createdById": APPROVER_ID,
    "team": TEAM_NAME,
    "teamId": TEAM_ID,
    "relations": {
        "blocks": [{"id": "FIX-13", "title": "a blocked issue"}],
        "blockedBy": [{"id": "FIX-14", "title": "a blocking issue"}],
        "relatedTo": [{"id": "FIX-15", "title": "a related issue"}],
        "duplicateOf": None,
    },
}

LIST_COMMENTS: Mapping[str, object] = {
    "comments": [
        {
            "id": "1ac11111-0000-4000-8000-000000000001",
            "body": "a fixture comment",
            "createdAt": "2026-08-25T09:28:45.273Z",
            "updatedAt": "2026-08-25T09:28:45.260Z",
            "parentId": None,
            "resolvedAt": None,
            "quotedText": None,
            "author": {"id": APPROVER_ID, "name": APPROVER_NAME},
            "onBehalfOf": None,
        },
    ],
    "hasNextPage": False,
}

LIST_DOCUMENTS: Mapping[str, object] = {
    "documents": [
        {
            "id": "1ad11111-0000-4000-8000-000000000001",
            "title": "a fixture document",
            "content": "the body, as the listing truncates it… (truncated)",
            "icon": "Book",
            "color": None,
            "url": "https://tracker.invalid/document/fixture",
            "slugId": "1ad11111",
            "createdAt": "2026-08-17T18:59:02.853Z",
            "updatedAt": "2026-08-18T16:08:09.276Z",
            "archivedAt": None,
            "creator": {"id": APPROVER_ID, "name": APPROVER_NAME},
            "updatedBy": {"id": APPROVER_ID, "name": APPROVER_NAME},
            "project": None,
            "initiative": None,
            "team": None,
            "issue": None,
        },
    ],
    "hasNextPage": False,
}

CAPTURES: Mapping[str, McpToolResult] = {
    "list_issue_labels": LIST_ISSUE_LABELS,
    "list_teams": LIST_TEAMS,
    "list_users": LIST_USERS,
    "list_issue_statuses": LIST_ISSUE_STATUSES,
    "list_issues": LIST_ISSUES,
    "get_issue": GET_ISSUE,
    "list_comments": LIST_COMMENTS,
    "list_documents": LIST_DOCUMENTS,
}


class CaptureCaller:
    """An ``McpToolCaller`` that replays the captures, and nothing else.

    A tool with no capture raises rather than answers: a read this module
    has not measured must not pass silently through a test that claims the
    wire is conformed.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    async def call_tool(
        self,
        *,
        name: str,
        arguments: Mapping[str, object],
    ) -> McpToolResult:
        self.calls.append((name, dict(arguments)))
        capture = CAPTURES.get(name)
        if capture is None:
            msg = f"no live capture was taken for the tool {name!r}"
            raise LookupError(msg)
        return capture


def tracker_over(caller: CaptureCaller) -> LinearMcpTracker:
    return LinearMcpTracker(
        caller=caller,
        queue_state_labels={
            QueueState.APPROVED.value: "queue:approved",
            QueueState.PROPOSED.value: "queue:proposed",
        },
        workflow_state_names={LifecycleStage.DONE: "Done"},
        team_identifiers={"board": TEAM_NAME},
        max_retries=0,
        retry_backoff_factor=0.0,
    )


class TestListingEnvelopes:
    """Every list tool keys its array after ITSELF. There is no shared key."""

    def test_the_label_listing_is_keyed_labels(self) -> None:
        listing = LinearLabelListWire.model_validate(LIST_ISSUE_LABELS)
        assert [entry.name for entry in listing.labels] == [
            "queue:approved",
            "queue:proposed",
        ]

    def test_the_label_listing_reports_no_container_at_all(self) -> None:
        """Live labels carry no ``teamId``, so every container reads absent.

        Absent is not "defined at workspace scope": it is the listing
        declining to say, and the ensure path adopts rather than refusing
        on the backend's silence.
        """
        listing = LinearLabelListWire.model_validate(LIST_ISSUE_LABELS)
        assert [entry.team_id for entry in listing.labels] == [None, None]

    def test_the_team_listing_is_keyed_teams(self) -> None:
        listing = LinearTeamListWire.model_validate(LIST_TEAMS)
        assert [entry.name for entry in listing.teams] == [TEAM_NAME]

    def test_the_user_listing_is_keyed_users(self) -> None:
        listing = LinearUserListWire.model_validate(LIST_USERS)
        assert [entry.name for entry in listing.users] == [APPROVER_NAME]

    def test_the_document_listing_is_keyed_documents(self) -> None:
        listing = LinearDocumentListWire.model_validate(LIST_DOCUMENTS)
        assert [entry.title for entry in listing.documents] == ["a fixture document"]

    @pytest.mark.parametrize(
        "shape",
        [LinearLabelListWire, LinearTeamListWire, LinearUserListWire],
    )
    def test_the_invented_entries_envelope_no_longer_validates(
        self,
        shape: type[LinearLabelListWire | LinearTeamListWire | LinearUserListWire],
    ) -> None:
        """The old shape is GONE, not merely unused.

        ``LinearNamedListWire{entries}`` was the shape boot died on: no
        tool on this server answers under that key, so a payload carrying
        it is refused wherever the real key is required.
        """
        with pytest.raises(ValidationError):
            shape.model_validate({"entries": [{"name": "queue:approved"}]})


class TestBareArrayStatuses:
    """``list_issue_statuses`` is a different shape CLASS, not a different key."""

    def test_the_statuses_payload_validates_as_a_bare_list(self) -> None:
        entries = LINEAR_NAMED_ARRAY.validate_python(LIST_ISSUE_STATUSES)
        assert [entry.name for entry in entries] == ["Backlog", "In Progress", "Done"]

    def test_an_envelope_is_not_a_bare_array(self) -> None:
        with pytest.raises(ValidationError):
            LINEAR_NAMED_ARRAY.validate_python({"entries": LIST_ISSUE_STATUSES})

    def test_a_bare_array_is_not_an_envelope(self) -> None:
        with pytest.raises(ValidationError):
            LinearLabelListWire.model_validate(LIST_ISSUE_STATUSES)


class TestIssueShapes:
    """What a list entry carries, and what only the full read carries."""

    def test_a_list_entry_validates_and_reports_no_relations(self) -> None:
        listing = LinearIssueListWire.model_validate(LIST_ISSUES)
        (entry,) = listing.issues
        assert entry.id == "FIX-11"
        assert entry.team == TEAM_NAME
        assert entry.priority.value == 2
        assert entry.status_type == "started"
        assert entry.labels == ["queue:approved"]
        assert entry.parent_id == "FIX-10"
        assert entry.assignee == APPROVER_NAME
        assert entry.relations is None, "the listing does not report relations"

    def test_a_list_entry_is_not_a_full_read(self) -> None:
        """The asset arrays are absent from the listing, so the detail refuses.

        The distinction is load-bearing: an empty asset list read off a
        listing would answer "nothing is attached" from a payload that was
        never asked the question.
        """
        with pytest.raises(ValidationError):
            LinearIssueDetailWire.model_validate(LIST_ISSUES_ENTRY)

    def test_the_full_read_carries_the_asset_arrays(self) -> None:
        issue = LinearIssueDetailWire.model_validate(GET_ISSUE)
        (attachment,) = issue.attachments
        assert attachment.id == "1a111111-0000-4000-8000-000000000001"
        assert attachment.title == "a linked pull request"
        assert attachment.content_type is None
        assert attachment.size is None
        assert issue.documents == []

    def test_the_relations_object_reports_every_arm_by_its_vendor_key(self) -> None:
        issue = LinearIssueDetailWire.model_validate(GET_ISSUE)
        assert issue.relations is not None
        assert [
            (arm, [edge.id for edge in edges]) for arm, edges in issue.relations.arms()
        ] == [
            ("blocks", ["FIX-13"]),
            ("blockedBy", ["FIX-14"]),
            ("relatedTo", ["FIX-15"]),
            ("duplicateOf", []),
        ]

    def test_a_list_of_typed_edges_no_longer_validates(self) -> None:
        """The invented ``[{type, identifier}]`` relations shape is gone."""
        with pytest.raises(ValidationError):
            LinearIssueWire.model_validate(
                {
                    **LIST_ISSUES_ENTRY,
                    "relations": [{"type": "blocks", "identifier": "FIX-13"}],
                },
            )


class TestCommentShape:
    """The comment names its author and does not name its issue."""

    def test_the_comment_listing_validates_and_carries_an_author_object(self) -> None:
        listing = LinearCommentListWire.model_validate(LIST_COMMENTS)
        (comment,) = listing.comments
        assert comment.author.name == APPROVER_NAME
        assert comment.author.id == APPROVER_ID
        assert comment.body == "a fixture comment"
        assert comment.created_at == datetime(
            2026, 8, 25, 9, 28, 45, 273000, tzinfo=UTC
        )

    def test_the_invented_user_and_issue_id_comment_no_longer_validates(self) -> None:
        """Two required fields the vendor never sends — the boot-killer class."""
        with pytest.raises(ValidationError):
            LinearCommentWire.model_validate(
                {
                    "id": "1ac11111-0000-4000-8000-000000000001",
                    "issueId": "FIX-12",
                    "user": APPROVER_NAME,
                    "body": "a fixture comment",
                    "createdAt": "2026-08-25T09:28:45.273Z",
                },
            )


class TestTheAdapterOverTheCaptures:
    """The adapter reads the captured payloads into domain objects."""

    async def test_the_scan_reads_the_listing(self) -> None:
        issues = await tracker_over(CaptureCaller()).scan_issues(
            query=IssueQuery(queue_state=QueueState.APPROVED, page_size=50),
        )
        (issue,) = issues
        assert issue.issue_key == "FIX-11"
        assert issue.queue_states == frozenset({QueueState.APPROVED})
        assert issue.team_key == "board"
        assert issue.relations == ()

    async def test_the_full_read_carries_the_relations_and_the_assets(self) -> None:
        tracker = tracker_over(CaptureCaller())
        issue = await tracker.read_issue(issue_key="FIX-12")
        assert {
            (relation.kind, relation.issue_key) for relation in issue.relations
        } == {
            (IssueRelationKind.BLOCKS, "FIX-13"),
            (IssueRelationKind.BLOCKED_BY, "FIX-14"),
            (IssueRelationKind.RELATED, "FIX-15"),
        }
        assets = await tracker.list_issue_assets(issue_key="FIX-12")
        assert [asset.asset_key for asset in assets] == [
            "1a111111-0000-4000-8000-000000000001",
        ]

    async def test_a_comment_is_attributed_to_its_author_and_to_the_issue_asked(
        self,
    ) -> None:
        comments = await tracker_over(CaptureCaller()).list_comments(
            issue_key="FIX-12",
        )
        (comment,) = comments
        assert comment.author_key == APPROVER_NAME
        assert comment.issue_key == "FIX-12"

    async def test_every_mapping_kind_resolves_against_the_captures(self) -> None:
        """The read that boot died on, over the shapes the server sends."""
        unresolved = await tracker_over(CaptureCaller()).resolve_mappings(
            refs=[
                MappingRef(
                    kind=MappingKind.USER,
                    name="approver",
                    identifier=APPROVER_NAME,
                ),
                MappingRef(
                    kind=MappingKind.TEAM,
                    name="board",
                    identifier=TEAM_NAME,
                ),
                MappingRef(
                    kind=MappingKind.QUEUE_STATE,
                    name="approved",
                    identifier="queue:approved",
                ),
                MappingRef(
                    kind=MappingKind.WORKFLOW_STATE,
                    name="done",
                    identifier="Done",
                ),
                MappingRef(
                    kind=MappingKind.DOCUMENT,
                    name="a fixture document",
                    identifier="1ad11111-0000-4000-8000-000000000001",
                ),
            ],
        )
        assert unresolved == ()
