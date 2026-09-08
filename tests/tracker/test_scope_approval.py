"""Approval conformance over live label presence and current ancestry."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

import pytest

from kodezart.composition.tracker import build_tracker
from kodezart.core.config import AppConfig
from kodezart.core.errors import McpTransportError, TrackerProtocolError
from kodezart.core.protocols import McpToolResult, TrackerPort
from kodezart.domain.errors import ScopeReadError
from kodezart.types.domain.operation import OperationMemberAbsentError, ScopeLabel
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from tests.fakes import FakeTrackerPort
from tests.tracker.conftest import linear_over_fake_mcp
from tests.tracker.test_scope_reads import (
    INITIATIVE,
    OTHER_PROJECT,
    PROJECT,
    ROOT,
    ScopeMcpIssue,
    ScopeMcpServer,
    _container,
    _domain_issue,
    _initiative,
    _project,
)
from tests.tracker.test_scope_tool_arguments import SCOPE_INPUT_SCHEMAS
from tests.tracker.test_tracker_boot import operation_config

APPROVAL_LABELS = {member.value: f"admission/{member.value}" for member in ScopeLabel}
CHILD = ScopeRef(kind=ScopeKind.ISSUE, key="FIX-3")
PARENT = ScopeRef(kind=ScopeKind.ISSUE, key="FIX-2")
OTHER = ScopeRef(kind=ScopeKind.PROJECT, key=OTHER_PROJECT)
OUTER = ScopeRef(kind=ScopeKind.INITIATIVE, key="outer-initiative")


@dataclass
class ApprovalFixture:
    tracker: TrackerPort
    server: ScopeMcpServer
    fake: FakeTrackerPort

    def labels(self, ref: ScopeRef, *members: ScopeLabel) -> None:
        labels = [APPROVAL_LABELS[member.value] for member in members]
        if ref.kind is ScopeKind.ISSUE:
            self.server.issues[ref.key].labels = labels
        elif ref.kind is ScopeKind.PROJECT:
            self.server.projects[ref.key]["labels"] = labels
        else:
            assert ref.kind is ScopeKind.INITIATIVE
            self.server.initiatives[ref.key]["labels"] = labels
        self.fake.scope_label_members[ref] = frozenset(members)

    def parent(self, key: str, parent: str | None) -> None:
        self.server.issues[key].parent_id = parent
        self.fake.issues[key] = self.fake.issues[key].model_copy(
            update={"parent_key": parent}
        )

    def project(self, key: str, project: str | None) -> None:
        self.server.issue_detail_updates[key] = {
            "project": project,
            "projectId": project,
            "projectMilestone": None,
        }
        self.fake.issues[key] = self.fake.issues[key].model_copy(
            update={"project": project, "project_id": project, "milestone_key": None}
        )


@pytest.fixture(params=["linear", "fake"])
def approval(request: pytest.FixtureRequest) -> ApprovalFixture:
    server = ScopeMcpServer()
    issues = []
    for native in server.issues.values():
        assert isinstance(native, ScopeMcpIssue)
        issues.append(
            _domain_issue(native).model_copy(
                update={
                    "project_id": native.project_key,
                    "milestone_key": native.milestone_key,
                }
            )
        )
    fake = FakeTrackerPort(
        issues=issues,
        scope_containers=[
            _container(PROJECT, INITIATIVE),
            _container(OTHER, INITIATIVE),
            _container(INITIATIVE),
        ],
    )
    tracker = (
        linear_over_fake_mcp(server, scope_labels=APPROVAL_LABELS)
        if request.param == "linear"
        else fake
    )
    return ApprovalFixture(tracker=tracker, server=server, fake=fake)


@pytest.mark.parametrize("ref", [CHILD, PARENT, ROOT, OTHER, INITIATIVE])
async def test_approval_at_each_real_ancestor_admits_deepest_issue(
    approval: ApprovalFixture, ref: ScopeRef
) -> None:
    approval.labels(ref, ScopeLabel.APPROVED)

    assert await approval.tracker.execution_approved(issue_key=CHILD.key) is True
    assert not approval.fake.issue_writes
    assert not approval.fake.queue_writes
    assert not approval.fake.comment_writes
    assert not approval.fake.claim_writes
    for tool, arguments in approval.server.calls:
        assert tool in {"get_issue", "get_project", "get_initiative"}
        schema = SCOPE_INPUT_SCHEMAS[tool]
        assert set(arguments) <= schema.properties
        assert schema.required <= set(arguments)


@pytest.mark.parametrize("member", [None, ScopeLabel.TRIAGE, ScopeLabel.PROPOSED])
async def test_absent_approval_or_another_meta_member_is_not_approval(
    approval: ApprovalFixture, member: ScopeLabel | None
) -> None:
    if member is not None:
        for ref in (CHILD, PARENT, ROOT, OTHER, INITIATIVE):
            approval.labels(ref, member)
    assert await approval.tracker.execution_approved(issue_key=CHILD.key) is False


async def test_current_reparenting_and_revocation_need_no_copied_label(
    approval: ApprovalFixture,
) -> None:
    approval.labels(ROOT, ScopeLabel.APPROVED)
    approval.parent(CHILD.key, None)
    assert await approval.tracker.execution_approved(issue_key=CHILD.key) is False
    approval.parent(CHILD.key, PARENT.key)
    assert await approval.tracker.execution_approved(issue_key=CHILD.key) is True
    approval.labels(ROOT)
    assert await approval.tracker.execution_approved(issue_key=CHILD.key) is False
    assert approval.server.issues[CHILD.key].labels == []
    assert not approval.fake.issue_writes


async def test_container_label_removal_is_visible_on_the_same_adapter(
    approval: ApprovalFixture,
) -> None:
    approval.labels(INITIATIVE, ScopeLabel.APPROVED)
    assert await approval.tracker.execution_approved(issue_key=CHILD.key) is True
    approval.labels(INITIATIVE)
    assert await approval.tracker.execution_approved(issue_key=CHILD.key) is False


async def test_issue_parent_project_does_not_replace_child_membership(
    approval: ApprovalFixture,
) -> None:
    approval.labels(PROJECT, ScopeLabel.APPROVED)
    assert await approval.tracker.execution_approved(issue_key=CHILD.key) is False
    assert await approval.tracker.execution_approved(issue_key=PARENT.key) is True
    approval.project(CHILD.key, PROJECT.key)
    assert await approval.tracker.execution_approved(issue_key=CHILD.key) is True


async def test_issue_without_container_can_be_unapproved_or_inherit_issue_label(
    approval: ApprovalFixture,
) -> None:
    approval.project(CHILD.key, None)
    assert await approval.tracker.execution_approved(issue_key=CHILD.key) is False
    approval.labels(ROOT, ScopeLabel.APPROVED)
    assert await approval.tracker.execution_approved(issue_key=CHILD.key) is True


async def test_reported_project_without_canonical_key_refuses(
    approval: ApprovalFixture,
) -> None:
    approval.project(CHILD.key, None)
    approval.server.issue_detail_updates[CHILD.key]["project"] = "Reported project"
    approval.fake.issues[CHILD.key] = approval.fake.issues[CHILD.key].model_copy(
        update={"project": "Reported project"}
    )
    with pytest.raises(ScopeReadError, match="canonical key"):
        await approval.tracker.execution_approved(issue_key=CHILD.key)


@pytest.mark.parametrize("omitted", [False, True])
async def test_native_no_project_retains_omitted_and_null_compatibility(
    omitted: bool,
) -> None:
    class UnassignedServer(ScopeMcpServer):
        def _tool_get_issue(
            self, arguments: Mapping[str, object]
        ) -> Mapping[str, object]:
            result = dict(super()._tool_get_issue(arguments))
            for field in ("project", "projectId", "projectMilestone"):
                if omitted:
                    result.pop(field)
                else:
                    result[field] = None
            return result

    tracker = linear_over_fake_mcp(UnassignedServer(), scope_labels=APPROVAL_LABELS)
    assert await tracker.execution_approved(issue_key=CHILD.key) is False


async def test_nested_initiative_is_read_and_reparenting_changes_next_resolution(
    approval: ApprovalFixture,
) -> None:
    approval.server.initiatives[OUTER.key] = _initiative(OUTER, [])
    approval.fake.scope_containers[OUTER] = _container(OUTER)
    approval.labels(OUTER, ScopeLabel.APPROVED)
    assert await approval.tracker.execution_approved(issue_key=CHILD.key) is False
    approval.server.initiatives[INITIATIVE.key]["parentInitiatives"] = [
        {"id": OUTER.key}
    ]
    approval.fake.scope_containers[INITIATIVE] = _container(INITIATIVE, OUTER)
    assert await approval.tracker.execution_approved(issue_key=CHILD.key) is True


@pytest.mark.parametrize("layer", ["issue", "container"])
async def test_cyclic_ancestry_is_not_a_negative_answer(
    approval: ApprovalFixture, layer: str
) -> None:
    if layer == "issue":
        approval.parent(ROOT.key, CHILD.key)
    else:
        approval.server.initiatives[INITIATIVE.key]["parentInitiatives"] = [
            {"id": INITIATIVE.key}
        ]
        approval.fake.scope_containers[INITIATIVE] = _container(INITIATIVE, INITIATIVE)
    with pytest.raises(ScopeReadError, match="cycle"):
        await approval.tracker.execution_approved(issue_key=CHILD.key)


@pytest.mark.parametrize("layer", ["issue", "project", "initiative"])
async def test_missing_ancestry_refuses_instead_of_returning_false(
    approval: ApprovalFixture, layer: str
) -> None:
    if layer == "issue":
        del approval.server.issues[ROOT.key]
        del approval.fake.issues[ROOT.key]
    elif layer == "project":
        del approval.server.projects[OTHER.key]
        del approval.fake.scope_containers[OTHER]
    else:
        del approval.server.initiatives[INITIATIVE.key]
        del approval.fake.scope_containers[INITIATIVE]
    with pytest.raises((ScopeReadError, McpTransportError)):
        await approval.tracker.execution_approved(issue_key=CHILD.key)


@pytest.mark.parametrize("ref", [CHILD, OTHER, INITIATIVE])
async def test_returned_identity_must_match_the_address(
    approval: ApprovalFixture, ref: ScopeRef
) -> None:
    if ref.kind is ScopeKind.ISSUE:
        approval.server.issue_detail_updates[ref.key] = {"id": "impostor"}
        approval.fake.issues[ref.key] = approval.fake.issues[ref.key].model_copy(
            update={"issue_key": "impostor"}
        )
    else:
        collection = (
            approval.server.projects
            if ref.kind is ScopeKind.PROJECT
            else approval.server.initiatives
        )
        collection[ref.key]["id"] = "impostor"
        approval.fake.scope_containers[ref] = _container(
            ScopeRef(kind=ref.kind, key="impostor")
        )
    with pytest.raises(ScopeReadError, match="identity"):
        await approval.tracker.execution_approved(issue_key=CHILD.key)


async def test_unmapped_approval_refuses_before_any_native_read() -> None:
    server = ScopeMcpServer()
    with pytest.raises(OperationMemberAbsentError, match=r"scope_labels\.approved"):
        await linear_over_fake_mcp(server, scope_labels={}).execution_approved(
            issue_key=CHILD.key
        )
    assert server.calls == []


async def test_actual_composition_passes_the_remapped_scope_vocabulary() -> None:
    server = ScopeMcpServer()
    server.initiatives[INITIATIVE.key]["labels"] = [APPROVAL_LABELS["approved"]]
    tracker, _ = build_tracker(
        config=AppConfig(),
        operation=operation_config().model_copy(
            update={"scope_labels": APPROVAL_LABELS}
        ),
        caller=server,
    )
    assert await tracker.execution_approved(issue_key=CHILD.key) is True


@pytest.mark.parametrize("member", ["queue:approved", "scope:approved", "approved"])
async def test_unconfigured_names_never_substitute_for_configured_approval(
    member: str,
) -> None:
    server = ScopeMcpServer()
    server.issues[CHILD.key].labels = [member]
    server.initiatives[INITIATIVE.key]["labels"] = [member]
    tracker = linear_over_fake_mcp(server, scope_labels=APPROVAL_LABELS)
    assert await tracker.execution_approved(issue_key=CHILD.key) is False


@pytest.mark.parametrize("field", ["labels", "parentId"])
async def test_issue_membership_omission_is_not_an_empty_answer(field: str) -> None:
    class OmittedServer(ScopeMcpServer):
        def _tool_get_issue(
            self, arguments: Mapping[str, object]
        ) -> Mapping[str, object]:
            result = dict(super()._tool_get_issue(arguments))
            result.pop(field)
            return result

    tracker = linear_over_fake_mcp(OmittedServer(), scope_labels=APPROVAL_LABELS)
    with pytest.raises(TrackerProtocolError):
        await tracker.execution_approved(issue_key=CHILD.key)


@pytest.mark.parametrize("layer", ["issue", "project", "initiative"])
@pytest.mark.parametrize("labels", [None, "approved", [None]])
async def test_unreadable_native_labels_do_not_become_absence(
    layer: str, labels: object
) -> None:
    server = ScopeMcpServer()
    if layer == "issue":
        server.issue_detail_updates[CHILD.key] = {"labels": labels}
    elif layer == "project":
        server.projects[OTHER.key]["labels"] = labels
    else:
        server.initiatives[INITIATIVE.key]["labels"] = labels
    with pytest.raises(TrackerProtocolError):
        await linear_over_fake_mcp(
            server, scope_labels=APPROVAL_LABELS
        ).execution_approved(issue_key=CHILD.key)


@pytest.mark.parametrize("layer", ["project", "initiative"])
async def test_omitted_native_labels_refuse(layer: str) -> None:
    server = ScopeMcpServer()
    row = (
        server.projects[OTHER.key]
        if layer == "project"
        else server.initiatives[INITIATIVE.key]
    )
    del row["labels"]
    with pytest.raises(TrackerProtocolError):
        await linear_over_fake_mcp(
            server, scope_labels=APPROVAL_LABELS
        ).execution_approved(issue_key=CHILD.key)


async def test_multiple_container_parents_refuse_even_with_an_approval_label() -> None:
    server = ScopeMcpServer()
    server.projects[OTHER.key] = _project(OTHER, [INITIATIVE, OUTER])
    server.projects[OTHER.key]["labels"] = [APPROVAL_LABELS["approved"]]
    with pytest.raises(ScopeReadError, match="multiple parents"):
        await linear_over_fake_mcp(
            server, scope_labels=APPROVAL_LABELS
        ).execution_approved(issue_key=CHILD.key)


@pytest.mark.parametrize("tool", ["get_issue", "get_project", "get_initiative"])
async def test_native_cancellation_propagates_without_answer_or_write(
    tool: str,
) -> None:
    class CanceledServer(ScopeMcpServer):
        async def call_tool(
            self, *, name: str, arguments: Mapping[str, object]
        ) -> McpToolResult:
            if name == tool:
                raise asyncio.CancelledError
            return await super().call_tool(name=name, arguments=arguments)

    server = CanceledServer()
    with pytest.raises(asyncio.CancelledError):
        await linear_over_fake_mcp(
            server, scope_labels=APPROVAL_LABELS
        ).execution_approved(issue_key=CHILD.key)
    assert all(name.startswith("get_") for name, _ in server.calls)
