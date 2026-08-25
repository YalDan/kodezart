"""Every argument the adapter sends is one the live tool declares (KOD-143).

Result shapes had to be measured because no tool on this server declares
an ``outputSchema``.  Arguments are the opposite case: every tool declares
an ``inputSchema``, so a wrong argument name or unit is checkable before it
is ever sent — and the one that was not checked cost a boot cycle, because
``create_issue_label.teamId`` says "Team UUID" and the adapter was sending
a team NAME.

``LIVE_INPUT_SCHEMAS`` is a projection of the server's own dump: the
property NAMES and the ``required`` list of each tool this adapter calls,
copied, nothing synthesized and nothing invented.  Descriptions and types
are left in the dump — what this module checks is the key set, which is
what a 400 for an unknown argument turns on.

The sent side is OBSERVED, not parsed: the adapter is driven over every
port method and the arguments it actually handed the transport are read
back.  A source scan would check the call sites someone remembered to
write down; this checks the calls that happen.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta

import pytest

from kodezart.types.domain.branch import BaseSpec, WorkRef, WorkRefRole
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.tracker import (
    IssuePriority,
    IssueQuery,
    MappingKind,
    MappingRef,
)
from tests.fakes import FakeLinearMcpServer
from tests.tracker.conftest import (
    CLAIMED_ISSUE,
    DOCUMENT_KEY,
    FIXTURE_NOW,
    fixture_server,
    linear_over_fake_mcp,
)


@dataclass(frozen=True)
class ToolSchema:
    """One tool's declared argument surface, as the server states it."""

    properties: frozenset[str]
    required: frozenset[str]


#: The server's OWN declared argument surface for every tool this
#: adapter calls — property names and required list, projected from the
#: live dump of 2026-08-25. Nothing here is synthesized.
LIVE_INPUT_SCHEMAS: Mapping[str, ToolSchema] = {
    "create_issue_label": ToolSchema(
        properties=frozenset(
            {
                "color",
                "description",
                "isGroup",
                "name",
                "parent",
                "teamId",
            },
        ),
        required=frozenset({"name"}),
    ),
    "delete_comment": ToolSchema(
        properties=frozenset(
            {
                "id",
            },
        ),
        required=frozenset({"id"}),
    ),
    "get_document": ToolSchema(
        properties=frozenset(
            {
                "id",
            },
        ),
        required=frozenset({"id"}),
    ),
    "get_issue": ToolSchema(
        properties=frozenset(
            {
                "id",
                "includeCustomerNeeds",
                "includeRelations",
                "includeReleases",
            },
        ),
        required=frozenset({"id"}),
    ),
    "list_comments": ToolSchema(
        properties=frozenset(
            {
                "cursor",
                "documentId",
                "initiativeId",
                "issueId",
                "limit",
                "milestoneId",
                "orderBy",
                "projectId",
                "statusUpdateId",
                "statusUpdateType",
            },
        ),
        required=frozenset(),
    ),
    "list_documents": ToolSchema(
        properties=frozenset(
            {
                "createdAt",
                "creatorId",
                "cursor",
                "fields",
                "includeArchived",
                "initiativeId",
                "limit",
                "orderBy",
                "projectId",
                "query",
                "teamId",
                "updatedAt",
            },
        ),
        required=frozenset(),
    ),
    "list_issue_labels": ToolSchema(
        properties=frozenset(
            {
                "cursor",
                "limit",
                "name",
                "orderBy",
                "team",
            },
        ),
        required=frozenset(),
    ),
    "list_issue_statuses": ToolSchema(
        properties=frozenset(
            {
                "team",
            },
        ),
        required=frozenset({"team"}),
    ),
    "list_issues": ToolSchema(
        properties=frozenset(
            {
                "assignee",
                "createdAt",
                "cursor",
                "cycle",
                "delegate",
                "fields",
                "includeArchived",
                "label",
                "limit",
                "orderBy",
                "parentId",
                "priority",
                "project",
                "query",
                "release",
                "state",
                "team",
                "updatedAt",
            },
        ),
        required=frozenset(),
    ),
    "list_teams": ToolSchema(
        properties=frozenset(
            {
                "createdAt",
                "cursor",
                "includeArchived",
                "limit",
                "orderBy",
                "query",
                "updatedAt",
            },
        ),
        required=frozenset(),
    ),
    "list_users": ToolSchema(
        properties=frozenset(
            {
                "cursor",
                "limit",
                "orderBy",
                "query",
                "team",
            },
        ),
        required=frozenset(),
    ),
    "save_comment": ToolSchema(
        properties=frozenset(
            {
                "body",
                "documentId",
                "id",
                "initiativeId",
                "issueId",
                "milestoneId",
                "parentId",
                "projectId",
                "statusUpdateId",
                "statusUpdateType",
            },
        ),
        required=frozenset({"body"}),
    ),
    "save_document": ToolSchema(
        properties=frozenset(
            {
                "color",
                "content",
                "cycle",
                "icon",
                "id",
                "initiative",
                "issue",
                "patch",
                "project",
                "team",
                "title",
            },
        ),
        required=frozenset(),
    ),
    "save_issue": ToolSchema(
        properties=frozenset(
            {
                "addReleases",
                "assignee",
                "blockedBy",
                "blocks",
                "cycle",
                "delegate",
                "description",
                "dueDate",
                "duplicateOf",
                "estimate",
                "id",
                "labels",
                "links",
                "milestone",
                "parentId",
                "patch",
                "priority",
                "project",
                "relatedTo",
                "removeBlockedBy",
                "removeBlocks",
                "removeRelatedTo",
                "removeReleases",
                "setReleases",
                "slaBreachesAt",
                "slaType",
                "state",
                "team",
                "title",
            },
        ),
        required=frozenset(),
    ),
}


#: Every required argument of every tool the adapter calls is sent.  The
#: set is EMPTY and the tripwire asserts exactly that, rather than holding
#: a list of forgiven omissions that a second omission could join
#: unnoticed.
#:
#: It held one entry until the fire-ruling of 2026-08-25 on KOD-143 gave
#: ``list_issue_statuses`` the semantics its ``team`` argument needs: the
#: vocabulary resolves per declared team, all-must-resolve, because it is
#: a WRITE contract — the lifecycle writer sets these states on whichever
#: declared team's issue was dispatched.
KNOWN_UNMET_REQUIREMENTS: Mapping[str, frozenset[str]] = {}


async def sent_arguments() -> Mapping[str, set[str]]:
    """Every argument key the adapter hands the transport, by tool.

    Driven through the port rather than assembled here: the question is
    what the adapter sends, and only the adapter knows that.
    """
    server: FakeLinearMcpServer = fixture_server()
    tracker = linear_over_fake_mcp(server)
    await tracker.scan_issues(
        query=IssueQuery(
            queue_state=QueueState.APPROVED,
            team_key="engineering",
            page_size=5,
            updated_since=FIXTURE_NOW - timedelta(days=1),
        ),
    )
    await tracker.read_issue(issue_key=CLAIMED_ISSUE)
    await tracker.create_issue(
        title="t",
        body="b",
        team_key="engineering",
        priority=IssuePriority.LOW,
    )
    await tracker.update_issue(issue_key=CLAIMED_ISSUE, title="x", body="y")
    await tracker.set_workflow_state(
        issue_key=CLAIMED_ISSUE,
        stage=LifecycleStage.DONE,
    )
    await tracker.set_queue_state(issue_key=CLAIMED_ISSUE, state=QueueState.DONE)
    await tracker.post_comment(issue_key=CLAIMED_ISSUE, body="hi")
    await tracker.list_comments(issue_key=CLAIMED_ISSUE)
    await tracker.claim_issue(
        issue_key=CLAIMED_ISSUE,
        holder="holder",
        lease_seconds=60.0,
    )
    await tracker.active_claim(issue_key=CLAIMED_ISSUE)
    await tracker.release_claim(issue_key=CLAIMED_ISSUE, holder="holder")
    await tracker.list_issue_assets(issue_key=CLAIMED_ISSUE)
    await tracker.read_document(document_key=DOCUMENT_KEY)
    await tracker.record_work_ref(
        ref=WorkRef(
            issue_id=CLAIMED_ISSUE,
            role=WorkRefRole.DELIVERABLE,
            branch="branch",
            recorded_at=FIXTURE_NOW,
        ),
    )
    await tracker.work_refs(issue_key=CLAIMED_ISSUE)
    await tracker.record_base_spec(
        issue_key=CLAIMED_ISSUE,
        spec=BaseSpec(inputs=(), base_branch="main"),
    )
    await tracker.read_base_spec(issue_key=CLAIMED_ISSUE)
    await tracker.resolve_mappings(
        refs=[
            MappingRef(kind=kind, name="n", identifier="i")
            for kind in sorted(MappingKind)
        ],
    )
    await tracker.ensure_mappings(
        refs=[
            MappingRef(
                kind=MappingKind.QUEUE_STATE,
                name="n",
                identifier="queue:brand-new",
                scope="fixture-team",
            ),
            MappingRef(kind=MappingKind.DOCUMENT, name="a document nobody holds"),
        ],
    )
    keys: dict[str, set[str]] = {}
    for tool, arguments in server.calls:
        keys.setdefault(tool, set()).update(arguments)
    return keys


@pytest.fixture(scope="module")
async def sent() -> Mapping[str, set[str]]:
    return await sent_arguments()


async def test_the_sweep_reaches_every_tool_the_adapter_names(
    sent: Mapping[str, set[str]],
) -> None:
    """A vacuous sweep would pass every assertion below it."""
    assert set(sent) == set(LIVE_INPUT_SCHEMAS)


async def test_every_argument_key_is_a_declared_property(
    sent: Mapping[str, set[str]],
) -> None:
    """The check that would have caught the boot failure before sending it."""
    undeclared: dict[str, Sequence[str]] = {
        tool: sorted(keys - LIVE_INPUT_SCHEMAS[tool].properties)
        for tool, keys in sorted(sent.items())
        if keys - LIVE_INPUT_SCHEMAS[tool].properties
    }
    assert undeclared == {}


async def test_every_required_argument_is_sent(
    sent: Mapping[str, set[str]],
) -> None:
    """No tool is called short of what its own schema demands.

    The last gap — ``list_issue_statuses`` called with ``{}`` against a
    schema requiring ``team`` — was the next boot wall, and it is closed.
    Asserted against the empty set rather than against a forgiven list, so
    a new omission fails here instead of on the workspace.
    """
    unmet = {
        tool: LIVE_INPUT_SCHEMAS[tool].required - keys
        for tool, keys in sorted(sent.items())
        if LIVE_INPUT_SCHEMAS[tool].required - keys
    }
    assert unmet == {}
    assert KNOWN_UNMET_REQUIREMENTS == {}


async def test_the_team_container_argument_is_sent_as_a_uuid(
    sent: Mapping[str, set[str]],
) -> None:
    """``teamId`` is the one team argument that refuses a name.

    Every other team argument this adapter sends is declared "Team name or
    ID", which is why the operation config names teams readably.
    """
    assert "teamId" in sent["create_issue_label"]
    server = fixture_server()
    tracker = linear_over_fake_mcp(server)
    await tracker.ensure_mappings(
        refs=[
            MappingRef(
                kind=MappingKind.QUEUE_STATE,
                name="n",
                identifier="queue:brand-new",
                scope="fixture-team",
            ),
        ],
    )
    (created,) = server.tool_calls("create_issue_label")
    assert created["teamId"] == "fixture-team-id"
    assert created["teamId"] != "fixture-team"


async def test_a_team_the_workspace_does_not_hold_is_refused_by_name() -> None:
    from kodezart.core.errors import TrackerProtocolError

    server = fixture_server()
    tracker = linear_over_fake_mcp(server)
    with pytest.raises(TrackerProtocolError) as caught:
        await tracker.ensure_mappings(
            refs=[
                MappingRef(
                    kind=MappingKind.QUEUE_STATE,
                    name="n",
                    identifier="queue:brand-new",
                    scope="a-team-nobody-has",
                ),
            ],
        )
    assert caught.value.tool == "create_issue_label"
    assert server.tool_calls("create_issue_label") == []
