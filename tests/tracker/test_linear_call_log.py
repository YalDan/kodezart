"""What the Linear adapter sends, in order, for one flow per tracker role (KOD-837).

A fixed script drives the shipped adapter over the in-process MCP server:
at least one flow through every role that declares a member, plus the claim
and surface-lease grant paths and a body edit, because those share the
adapter's private machinery. Every tool call it makes is recorded with its
argument keys, in the order it made them, and compared with the log below,
which was read off the adapter before it was split into one class per role.
The split moves code and changes no call, so the same script sends the same
log after it; a flow that raised is recorded by the error it raised, so a
refusal that moves is a difference too. Each flow is held to the role it is
labelled with by the members it calls on the adapter, a member only read not
counting, so a label is never coverage a flow does not give.

Where a step calls a member of its role that no conformance module names,
the per-case golden never sees what it sends, so the values are held here:
the digest of that step's calls with their values, only the nonce erased,
read off the adapter before the split like the log.
"""

import ast
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta

from kodezart.domain.run_event_stream import LaneRunEvent
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.branch import BaseSpec, WorkRef, WorkRefRole
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import (
    IssueQuery,
    MappingKind,
    MappingRef,
    ReviewQuery,
)
from tests.tracker.conformance_call_log import (
    CONFORMANCE_PATHS,
    REPOSITORY,
    call_log_digest,
    logged_call,
)
from tests.tracker.conftest import (
    APPROVED_ISSUE,
    ASSET_ISSUE,
    CLAIMED_ISSUE,
    DOCUMENT_KEY,
    FIXTURE_NOW,
    linear_over_fake_mcp,
)
from tests.tracker.lease_fixtures import leased_comment
from tests.tracker.role_register import declared_by_role, port_members
from tests.tracker.test_linear_mcp_tracker import tracker_over
from tests.tracker.test_organize_graph_writes import (
    address,
    changes,
    expected,
    fixture,
)
from tests.tracker.test_run_alarm_records import PREFIXES, alarm
from tests.tracker.test_run_alarm_records import address as alarm_address
from tests.tracker.test_scope_reads import PROJECT, ScopeMcpServer

HOLDER = "call-log-job"
LANE = "lane:call-log"

type Step = Callable[[object], Awaitable[object]]


def issue(key: str) -> ScopeRef:
    return ScopeRef(kind=ScopeKind.ISSUE, key=key)


async def graph_and_split(tracker) -> None:
    surfaces = frozenset(
        {
            address("child"),
            address("peer"),
            address(CLAIMED_ISSUE, SurfaceKind.ISSUE_SPLIT_SET),
        }
    )
    async with RunSurfaceLease(
        tracker=tracker, job_id=HOLDER, surfaces=surfaces, lease_seconds=300
    ):
        await tracker.update_issue_graph(
            issue_key="child",
            expected=await expected(tracker),
            changes=changes({"kind": "blocked_by", "add": ["peer"]}),
            holder=HOLDER,
        )
        await tracker.create_split_if_absent(
            source_key=CLAIMED_ISSUE,
            deliverable_key="call-log/child",
            title="Split child",
            body="Independent child specification",
            holder=HOLDER,
            expected=await expected(tracker),
        )


async def criterion_mint(tracker) -> None:
    async with RunSurfaceLease(
        tracker=tracker,
        job_id=HOLDER,
        surfaces=frozenset({address(CLAIMED_ISSUE, SurfaceKind.CRITERION_CHILD_SET)}),
        lease_seconds=300,
    ):
        await tracker.create_criterion_if_absent(
            parent_key=CLAIMED_ISSUE,
            title="Preserve input bytes",
            check="The prepared artifact is byte-identical to its input.",
            do="Compare the committed bytes to the input.",
            holder=HOLDER,
        )


async def body_edit(tracker) -> None:
    current = await tracker.read_issue(issue_key=CLAIMED_ISSUE)
    await tracker.edit_description(
        target=CLAIMED_ISSUE, expected=current.body, replacement="edited body"
    )


async def criterion_reopen(tracker) -> None:
    (criterion, *_) = await tracker.read_criteria(issue_key=CLAIMED_ISSUE)
    await tracker.reset_criterion_pending(expected=criterion)


async def run_alarm(tracker) -> None:
    value = alarm()
    await tracker.read_run_alarms(issue_key=APPROVED_ISSUE)
    async with RunSurfaceLease(
        tracker=tracker,
        job_id=HOLDER,
        surfaces=frozenset({alarm_address(value)}),
        lease_seconds=300,
    ):
        await tracker.record_run_alarm(
            issue_key=APPROVED_ISSUE, alarm=value, holder=HOLDER
        )


#: The script, one flow per step, each named by the role whose member it
#: drives. The organize writes come first so the criterion the reopen step
#: reads exists; everything else reads the fixture workspace as it stands.
#: The adapter is dialled with the run-alarm marker as well, so the alarm
#: flow reaches its write rather than refusing for want of a prefix.
STEPS: tuple[tuple[str, Step], ...] = (
    ("OrganizeOwnerTracker", graph_and_split),
    ("CriterionMintWriter", criterion_mint),
    ("TrackerCommentReader", lambda t: t.list_comments(issue_key=CLAIMED_ISSUE)),
    ("TrackerCriteriaReader", lambda t: t.read_criteria(issue_key=APPROVED_ISSUE)),
    ("ModelMemberReader", lambda t: t.read_labeled_issues(classification="criterion")),
    ("TrackerContextReader", lambda t: t.list_issue_assets(issue_key=ASSET_ISSUE)),
    ("TrackerContextReader", lambda t: t.read_document(document_key=DOCUMENT_KEY)),
    (
        "ExecutionApprovalReader",
        lambda t: t.execution_approved(issue_key=CLAIMED_ISSUE),
    ),
    ("IssueReader", lambda t: t.read_issue(issue_key=APPROVED_ISSUE)),
    ("PlanningIssueReader", lambda t: t.read_planning_issue(issue_key=APPROVED_ISSUE)),
    ("IssueRevisionReader", lambda t: t.read_issue_revision(issue_key=APPROVED_ISSUE)),
    (
        "IssueScanReader",
        lambda t: t.scan_issues(
            query=IssueQuery(
                queue_state=QueueState.APPROVED,
                page_size=5,
                updated_since=FIXTURE_NOW - timedelta(days=1),
            )
        ),
    ),
    ("ScopeFamilyReader", lambda t: t.scope_issues(ref=issue(CLAIMED_ISSUE))),
    (
        "StateHistoryReader",
        lambda t: t.read_issue_state_change(issue_key=APPROVED_ISSUE),
    ),
    (
        "EscalationResolutionReader",
        lambda t: t.read_escalation_resolution(
            issue_key=APPROVED_ISSUE, lane_key=LANE, escalation_key="question-1"
        ),
    ),
    (
        "RecordedRepositoryReader",
        lambda t: t.recorded_repository(issue_key=APPROVED_ISSUE),
    ),
    ("WriterIdentityReader", lambda t: t.writer_identity()),
    (
        "ScanCapabilityReader",
        lambda t: t.verify_scan_capability(signals=tuple(PassSignal)),
    ),
    (
        "SurfaceAuthorshipReader",
        lambda t: t.read_surface_authorship(
            surface=WritableSurface(
                kind=SurfaceKind.ISSUE_DESCRIPTION, ref=issue(ASSET_ISSUE)
            )
        ),
    ),
    ("ScopeReadPreflight", lambda t: _sync(t.require_scope_plan_reads)),
    (
        "TrackerVocabulary",
        lambda t: t.resolve_mappings(
            refs=[
                MappingRef(kind=MappingKind.QUEUE_STATE, name="n", identifier="i"),
            ]
        ),
    ),
    (
        "TrackerVocabulary",
        lambda t: t.ensure_mappings(
            refs=[
                MappingRef(
                    kind=MappingKind.QUEUE_STATE,
                    name="n",
                    identifier="queue:brand-new",
                    scope="fixture-team",
                )
            ]
        ),
    ),
    ("DescriptionWriter", body_edit),
    (
        "WorkflowStateWriter",
        lambda t: t.set_workflow_state(
            issue_key=APPROVED_ISSUE, stage=LifecycleStage.IN_PROGRESS
        ),
    ),
    (
        "StateRestorer",
        lambda t: t.restore_workflow_state(issue_key=APPROVED_ISSUE, state_name="Todo"),
    ),
    (
        "ClassificationWriter",
        lambda t: t.set_issue_classification(
            issue_key=APPROVED_ISSUE, classification="criterion"
        ),
    ),
    (
        "CommentRecordWriter",
        lambda t: leased_comment(
            t, target=CLAIMED_ISSUE, marker="[fixture:call-log]", body="first"
        ),
    ),
    (
        "LaneEventWriter",
        lambda t: t.post_run_event(
            issue_key=APPROVED_ISSUE,
            event=LaneRunEvent(kind=RunEventKind.LANE_DISPATCHED, lane_key=LANE),
        ),
    ),
    (
        "LaneEventHistory",
        lambda t: t.lane_run_events(issue_key=APPROVED_ISSUE, lane_key=LANE),
    ),
    ("CriterionReopener", criterion_reopen),
    ("RunAlarmTracker", run_alarm),
    (
        "FireDispatchTracker",
        lambda t: t.claim_issue(
            issue_key=APPROVED_ISSUE, holder=HOLDER, lease_seconds=60.0
        ),
    ),
    (
        "ClaimHolder",
        lambda t: t.renew_claim(
            issue_key=APPROVED_ISSUE, holder=HOLDER, lease_seconds=120.0
        ),
    ),
    ("FireDispatchTracker", lambda t: t.active_claim(issue_key=APPROVED_ISSUE)),
    (
        "ClaimHolder",
        lambda t: t.release_claim(issue_key=APPROVED_ISSUE, holder=HOLDER),
    ),
    (
        "TrackerScopeApprovalReader",
        lambda t: t.read_scope_labels(ref=issue(APPROVED_ISSUE)),
    ),
    (
        "WorkRefRecorder",
        lambda t: t.record_work_ref(
            ref=WorkRef(
                issue_id=APPROVED_ISSUE,
                role=WorkRefRole.DELIVERABLE,
                branch="call-log/branch",
                recorded_at=FIXTURE_NOW,
            )
        ),
    ),
    ("WorkRefReader", lambda t: t.work_refs(issue_key=APPROVED_ISSUE)),
    (
        "FireDispatchTracker",
        lambda t: t.record_base_spec(
            issue_key=APPROVED_ISSUE, spec=BaseSpec(inputs=(), base_branch="main")
        ),
    ),
    ("FireDispatchTracker", lambda t: t.read_base_spec(issue_key=APPROVED_ISSUE)),
    ("FireSubjectReader", lambda t: t.read_fire_subject(issue_key=CLAIMED_ISSUE)),
    (
        "PassGateReader",
        lambda t: t.scan_reviews(
            query=ReviewQuery(
                repo_url="https://example.invalid/fixture-owner/fixture-repo",
                page_size=5,
                updated_since=FIXTURE_NOW - timedelta(days=1),
            )
        ),
    ),
    ("PassGateReader", lambda t: t.read_issue_movement(issue_key=APPROVED_ISSUE)),
    ("TrackerArtifactReader", lambda t: t.read_issue_identity(issue_key=CLAIMED_ISSUE)),
    (
        "TrackerArtifactReader",
        lambda t: t.read_split_children(source_key=CLAIMED_ISSUE),
    ),
    (
        "SurfaceLeaseTracker",
        lambda t: t.acquire_surfaces(
            surfaces=frozenset({address(APPROVED_ISSUE)}),
            holder=HOLDER,
            lease_seconds=60.0,
        ),
    ),
    (
        "SurfaceLeaseTracker",
        lambda t: t.renew_surfaces(
            surfaces=frozenset({address(APPROVED_ISSUE)}),
            holder=HOLDER,
            lease_seconds=120.0,
        ),
    ),
    (
        "SurfaceLeaseTracker",
        lambda t: t.release_surfaces(
            surfaces=frozenset({address(APPROVED_ISSUE)}), holder=HOLDER
        ),
    ),
    (
        "LifecycleStateWriter",
        lambda t: t.set_queue_state(issue_key=APPROVED_ISSUE, state=QueueState.DONE),
    ),
    (
        "LifecycleStateWriter",
        lambda t: t.post_comment(issue_key=APPROVED_ISSUE, body="call log"),
    ),
)


#: The flows that read a project, run over a workspace that has one.
SCOPE_STEPS: tuple[tuple[str, Step], ...] = (
    ("ContainerMetadataReader", lambda t: t.container_metadata(ref=PROJECT)),
    (
        "OrganizeContextTracker",
        lambda t: t.project_milestones(project_key=PROJECT.key),
    ),
    (
        "FireDispatchTracker",
        lambda t: t.initiative_identifiers(project_id=PROJECT.key),
    ),
)


async def _sync(call: Callable[[], object]) -> object:
    return call()


type Entry = tuple[str, str, tuple[str, ...]]


class Entered:
    """The adapter, recording every public member a step calls on it.

    A member read and never called is not entered: the view hands back a
    wrapper that records the name only when it is called.
    """

    def __init__(self, tracker: object) -> None:
        self._tracker = tracker
        self._reached: set[str] = set()

    def __getattr__(self, name: str) -> object:
        value = getattr(self._tracker, name)
        if name.startswith("_") or not callable(value):
            return value
        reached = self._reached

        def entered(*args: object, **kwargs: object) -> object:
            reached.add(name)
            return value(*args, **kwargs)

        return entered


async def run(
    steps: tuple[tuple[str, Step], ...],
    tracker: object,
    calls: list[tuple[str, Mapping[str, object]]],
    entered: list[tuple[str, frozenset[str]]] | None = None,
    sent: list[tuple[str, ...]] | None = None,
) -> list[Entry]:
    """Every tool call *steps* make, then how each step ended, in order.

    Each step runs over its own ``Entered`` view of the adapter; the members
    it called are appended to *entered* beside its label, and the calls it
    made, with their values and only the nonce erased, to *sent*.
    """
    log: list[Entry] = []
    for role, step in steps:
        start = len(calls)
        view = Entered(tracker)
        try:
            await step(view)
        except Exception as error:
            ending: Entry = (role, "raised", (type(error).__name__,))
        else:
            ending = (role, "returned", ())
        log.extend(
            (role, tool, tuple(sorted(arguments))) for tool, arguments in calls[start:]
        )
        log.append(ending)
        if entered is not None:
            entered.append((role, frozenset(view._reached)))
        if sent is not None:
            sent.append(
                tuple(logged_call(tool, arguments) for tool, arguments in calls[start:])
            )
    return log


def scripted_adapter() -> tuple[object, object, list[tuple[str, Mapping[str, object]]]]:
    """The adapter the script drives, the caller it was given, and its call list."""
    board, _ = fixture()
    tracker = tracker_over(
        board.server,
        caller=board,
        clock=lambda: board.now,
        ledger=board.ledger,
        marker_prefixes=PREFIXES,
    )
    return tracker, board, board.calls


async def call_log(
    entered: list[tuple[str, frozenset[str]]] | None = None,
    sent: list[tuple[str, ...]] | None = None,
) -> tuple[Entry, ...]:
    """Run the script and return every tool call as (step, tool, argument keys)."""
    tracker, _, calls = scripted_adapter()
    scope = ScopeMcpServer()
    return (
        *await run(STEPS, tracker, calls, entered, sent),
        *await run(
            SCOPE_STEPS, linear_over_fake_mcp(scope), scope.calls, entered, sent
        ),
    )


def test_every_role_that_declares_a_member_has_a_flow():
    assert {role for role, _ in (*STEPS, *SCOPE_STEPS)} == set(declared_by_role())


async def test_each_flow_enters_a_member_of_the_role_it_is_labelled_with():
    """A label is only as good as the member the flow reaches for.

    Each step runs over a view of the adapter that records the public
    members it touches; one of them must be declared on the step's role.
    """
    entered: list[tuple[str, frozenset[str]]] = []
    await call_log(entered)
    roles = declared_by_role()

    assert len(entered) == len((*STEPS, *SCOPE_STEPS))
    assert [role for role, reached in entered if not reached & roles[role]] == []


async def test_the_adapter_sends_the_recorded_call_log():
    assert await call_log() == RECORDED_CALL_LOG


def conformance_members() -> frozenset[str]:
    """Every port member a conformance module names, read off its parsed text."""
    members = port_members()
    return frozenset(
        node.attr
        for path in CONFORMANCE_PATHS
        for node in ast.walk(ast.parse((REPOSITORY / path).read_text()))
        if isinstance(node, ast.Attribute) and node.attr in members
    )


async def sent_where_no_conformance_case_looks() -> dict[int, str]:
    """Each step calling a member of its role no conformance case names, by index.

    Held as the digest of the calls the step made, with their values and
    only the nonce erased.
    """
    entered: list[tuple[str, frozenset[str]]] = []
    sent: list[tuple[str, ...]] = []
    await call_log(entered, sent)
    roles = declared_by_role()
    looked = conformance_members()
    return {
        index: call_log_digest(calls)
        for index, ((role, reached), calls) in enumerate(
            zip(entered, sent, strict=True)
        )
        if reached & roles[role] - looked
    }


async def test_the_adapter_sends_the_recorded_values_where_no_conformance_case_looks():
    """The values of every call the per-case golden never sees are held here."""
    sent = await sent_where_no_conformance_case_looks()

    assert sent
    assert sent == RECORDED_VALUES


#: Read off the adapter before the split, by running ``call_log`` once.
RECORDED_CALL_LOG: tuple[Entry, ...] = (
    ("OrganizeOwnerTracker", "save_comment", ("body", "issueId")),
    ("OrganizeOwnerTracker", "save_comment", ("body", "issueId")),
    ("OrganizeOwnerTracker", "save_comment", ("body", "issueId")),
    ("OrganizeOwnerTracker", "list_comments", ("issueId",)),
    ("OrganizeOwnerTracker", "list_comments", ("issueId",)),
    ("OrganizeOwnerTracker", "list_comments", ("issueId",)),
    ("OrganizeOwnerTracker", "save_comment", ("body", "id")),
    ("OrganizeOwnerTracker", "save_comment", ("body", "id")),
    ("OrganizeOwnerTracker", "save_comment", ("body", "id")),
    ("OrganizeOwnerTracker", "list_comments", ("issueId",)),
    ("OrganizeOwnerTracker", "list_comments", ("issueId",)),
    ("OrganizeOwnerTracker", "list_comments", ("issueId",)),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "list_comments", ("issueId",)),
    ("OrganizeOwnerTracker", "list_comments", ("issueId",)),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "save_issue", ("blockedBy", "id")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "list_issues", ("fields", "includeArchived", "limit")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "list_issue_statuses", ("team",)),
    ("OrganizeOwnerTracker", "list_comments", ("issueId",)),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "list_issues", ("fields", "includeArchived", "limit")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    (
        "OrganizeOwnerTracker",
        "save_issue",
        ("description", "parentId", "state", "team", "title"),
    ),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "get_issue", ("id", "includeRelations")),
    ("OrganizeOwnerTracker", "list_comments", ("issueId",)),
    ("OrganizeOwnerTracker", "list_comments", ("issueId",)),
    ("OrganizeOwnerTracker", "list_comments", ("issueId",)),
    ("OrganizeOwnerTracker", "delete_comment", ("id",)),
    ("OrganizeOwnerTracker", "delete_comment", ("id",)),
    ("OrganizeOwnerTracker", "delete_comment", ("id",)),
    ("OrganizeOwnerTracker", "returned", ()),
    ("CriterionMintWriter", "save_comment", ("body", "issueId")),
    ("CriterionMintWriter", "list_comments", ("issueId",)),
    ("CriterionMintWriter", "save_comment", ("body", "id")),
    ("CriterionMintWriter", "list_comments", ("issueId",)),
    ("CriterionMintWriter", "get_issue", ("id", "includeRelations")),
    (
        "CriterionMintWriter",
        "list_issues",
        ("fields", "includeArchived", "limit", "parentId"),
    ),
    ("CriterionMintWriter", "get_issue", ("id", "includeRelations")),
    ("CriterionMintWriter", "get_issue", ("id", "includeRelations")),
    ("CriterionMintWriter", "get_issue", ("id", "includeRelations")),
    ("CriterionMintWriter", "get_issue", ("id", "includeRelations")),
    ("CriterionMintWriter", "list_issue_statuses", ("team",)),
    ("CriterionMintWriter", "list_comments", ("issueId",)),
    (
        "CriterionMintWriter",
        "save_issue",
        ("description", "labels", "parentId", "state", "team", "title"),
    ),
    ("CriterionMintWriter", "get_issue", ("id", "includeRelations")),
    ("CriterionMintWriter", "list_comments", ("issueId",)),
    ("CriterionMintWriter", "delete_comment", ("id",)),
    ("CriterionMintWriter", "returned", ()),
    ("TrackerCommentReader", "list_comments", ("issueId",)),
    ("TrackerCommentReader", "returned", ()),
    ("TrackerCriteriaReader", "get_issue", ("id", "includeRelations")),
    (
        "TrackerCriteriaReader",
        "list_issues",
        ("fields", "includeArchived", "limit", "parentId"),
    ),
    ("TrackerCriteriaReader", "returned", ()),
    (
        "ModelMemberReader",
        "list_issues",
        ("fields", "includeArchived", "label", "limit"),
    ),
    ("ModelMemberReader", "get_issue", ("id", "includeRelations")),
    ("ModelMemberReader", "returned", ()),
    ("TrackerContextReader", "get_issue", ("id", "includeRelations")),
    ("TrackerContextReader", "returned", ()),
    ("TrackerContextReader", "get_document", ("id",)),
    ("TrackerContextReader", "returned", ()),
    ("ExecutionApprovalReader", "get_issue", ("id", "includeRelations")),
    ("ExecutionApprovalReader", "returned", ()),
    ("IssueReader", "get_issue", ("id", "includeRelations")),
    ("IssueReader", "returned", ()),
    ("PlanningIssueReader", "get_issue", ("id", "includeRelations")),
    ("PlanningIssueReader", "returned", ()),
    ("IssueRevisionReader", "get_issue", ("id", "includeRelations")),
    ("IssueRevisionReader", "returned", ()),
    ("IssueScanReader", "list_issues", ("label", "limit", "updatedAt")),
    ("IssueScanReader", "returned", ()),
    ("ScopeFamilyReader", "get_issue", ("id", "includeRelations")),
    ("ScopeFamilyReader", "list_issues", ("includeArchived", "limit", "parentId")),
    ("ScopeFamilyReader", "get_issue", ("id", "includeRelations")),
    ("ScopeFamilyReader", "get_issue", ("id", "includeRelations")),
    ("ScopeFamilyReader", "get_issue", ("id", "includeRelations")),
    ("ScopeFamilyReader", "get_issue", ("id", "includeRelations")),
    ("ScopeFamilyReader", "list_issues", ("includeArchived", "limit", "parentId")),
    ("ScopeFamilyReader", "list_issues", ("includeArchived", "limit", "parentId")),
    ("ScopeFamilyReader", "list_issues", ("includeArchived", "limit", "parentId")),
    ("ScopeFamilyReader", "list_issues", ("includeArchived", "limit", "parentId")),
    ("ScopeFamilyReader", "returned", ()),
    ("StateHistoryReader", "get_issue", ("id", "includeRelations")),
    ("StateHistoryReader", "returned", ()),
    ("EscalationResolutionReader", "list_comments", ("issueId",)),
    ("EscalationResolutionReader", "raised", ("EscalationReadError",)),
    ("RecordedRepositoryReader", "list_comments", ("issueId",)),
    ("RecordedRepositoryReader", "returned", ()),
    ("WriterIdentityReader", "get_user", ("query",)),
    ("WriterIdentityReader", "returned", ()),
    ("ScanCapabilityReader", "list_issues", ("limit",)),
    ("ScanCapabilityReader", "list_diffs", ("limit",)),
    ("ScanCapabilityReader", "returned", ()),
    ("SurfaceAuthorshipReader", "get_issue", ("id", "includeRelations")),
    ("SurfaceAuthorshipReader", "get_user", ("query",)),
    ("SurfaceAuthorshipReader", "get_user", ("query",)),
    ("SurfaceAuthorshipReader", "list_comments", ("issueId",)),
    ("SurfaceAuthorshipReader", "returned", ()),
    ("ScopeReadPreflight", "raised", ("OperationMemberAbsentError",)),
    ("TrackerVocabulary", "list_issue_labels", ()),
    ("TrackerVocabulary", "list_issue_labels", ("team",)),
    ("TrackerVocabulary", "returned", ()),
    ("TrackerVocabulary", "list_issue_labels", ()),
    ("TrackerVocabulary", "list_issue_labels", ("team",)),
    ("TrackerVocabulary", "list_teams", ()),
    ("TrackerVocabulary", "create_issue_label", ("name", "teamId")),
    ("TrackerVocabulary", "returned", ()),
    ("DescriptionWriter", "get_issue", ("id", "includeRelations")),
    ("DescriptionWriter", "get_issue", ("id", "includeRelations")),
    ("DescriptionWriter", "get_issue", ("id", "includeRelations")),
    ("DescriptionWriter", "get_user", ("query",)),
    ("DescriptionWriter", "save_issue", ("description", "id")),
    ("DescriptionWriter", "returned", ()),
    ("WorkflowStateWriter", "get_issue", ("id", "includeRelations")),
    ("WorkflowStateWriter", "save_issue", ("id", "state")),
    ("WorkflowStateWriter", "returned", ()),
    ("StateRestorer", "get_issue", ("id", "includeRelations")),
    ("StateRestorer", "save_issue", ("id", "state")),
    ("StateRestorer", "returned", ()),
    ("ClassificationWriter", "get_issue", ("id", "includeRelations")),
    ("ClassificationWriter", "save_issue", ("addLabels", "id")),
    ("ClassificationWriter", "returned", ()),
    ("CommentRecordWriter", "save_comment", ("body", "issueId")),
    ("CommentRecordWriter", "list_comments", ("issueId",)),
    ("CommentRecordWriter", "save_comment", ("body", "id")),
    ("CommentRecordWriter", "list_comments", ("issueId",)),
    ("CommentRecordWriter", "list_comments", ("issueId",)),
    ("CommentRecordWriter", "list_comments", ("issueId",)),
    ("CommentRecordWriter", "save_comment", ("body", "issueId")),
    ("CommentRecordWriter", "list_comments", ("issueId",)),
    ("CommentRecordWriter", "delete_comment", ("id",)),
    ("CommentRecordWriter", "returned", ()),
    ("LaneEventWriter", "save_comment", ("body", "issueId")),
    ("LaneEventWriter", "returned", ()),
    ("LaneEventHistory", "list_comments", ("issueId",)),
    ("LaneEventHistory", "returned", ()),
    ("CriterionReopener", "get_issue", ("id", "includeRelations")),
    (
        "CriterionReopener",
        "list_issues",
        ("fields", "includeArchived", "limit", "parentId"),
    ),
    ("CriterionReopener", "get_issue", ("id", "includeRelations")),
    ("CriterionReopener", "get_issue", ("id", "includeRelations")),
    ("CriterionReopener", "get_issue", ("id", "includeRelations")),
    ("CriterionReopener", "get_issue", ("id", "includeRelations")),
    ("CriterionReopener", "get_issue", ("id", "includeRelations")),
    ("CriterionReopener", "list_issue_statuses", ("team",)),
    ("CriterionReopener", "get_issue", ("id", "includeRelations")),
    ("CriterionReopener", "get_issue", ("id", "includeRelations")),
    ("CriterionReopener", "returned", ()),
    ("RunAlarmTracker", "list_comments", ("issueId",)),
    ("RunAlarmTracker", "save_comment", ("body", "issueId")),
    ("RunAlarmTracker", "list_comments", ("issueId",)),
    ("RunAlarmTracker", "save_comment", ("body", "id")),
    ("RunAlarmTracker", "list_comments", ("issueId",)),
    ("RunAlarmTracker", "list_comments", ("issueId",)),
    ("RunAlarmTracker", "list_comments", ("issueId",)),
    ("RunAlarmTracker", "list_comments", ("issueId",)),
    ("RunAlarmTracker", "save_comment", ("body", "issueId")),
    ("RunAlarmTracker", "list_comments", ("issueId",)),
    ("RunAlarmTracker", "delete_comment", ("id",)),
    ("RunAlarmTracker", "returned", ()),
    ("FireDispatchTracker", "save_comment", ("body", "issueId")),
    ("FireDispatchTracker", "list_comments", ("issueId",)),
    ("FireDispatchTracker", "save_comment", ("body", "id")),
    ("FireDispatchTracker", "list_comments", ("issueId",)),
    ("FireDispatchTracker", "returned", ()),
    ("ClaimHolder", "list_comments", ("issueId",)),
    ("ClaimHolder", "save_comment", ("body", "id")),
    ("ClaimHolder", "list_comments", ("issueId",)),
    ("ClaimHolder", "returned", ()),
    ("FireDispatchTracker", "list_comments", ("issueId",)),
    ("FireDispatchTracker", "returned", ()),
    ("ClaimHolder", "list_comments", ("issueId",)),
    ("ClaimHolder", "delete_comment", ("id",)),
    ("ClaimHolder", "returned", ()),
    ("TrackerScopeApprovalReader", "get_issue", ("id", "includeRelations")),
    ("TrackerScopeApprovalReader", "returned", ()),
    ("WorkRefRecorder", "list_comments", ("issueId",)),
    ("WorkRefRecorder", "save_comment", ("body", "issueId")),
    ("WorkRefRecorder", "returned", ()),
    ("WorkRefReader", "list_comments", ("issueId",)),
    ("WorkRefReader", "returned", ()),
    ("FireDispatchTracker", "list_comments", ("issueId",)),
    ("FireDispatchTracker", "save_comment", ("body", "issueId")),
    ("FireDispatchTracker", "returned", ()),
    ("FireDispatchTracker", "list_comments", ("issueId",)),
    ("FireDispatchTracker", "returned", ()),
    ("FireSubjectReader", "get_issue", ("id", "includeRelations")),
    ("FireSubjectReader", "raised", ("FireSpecEntryError",)),
    ("PassGateReader", "list_diffs", ("limit", "orderBy", "owner", "repo")),
    ("PassGateReader", "returned", ()),
    ("PassGateReader", "get_issue", ("id", "includeRelations")),
    ("PassGateReader", "list_comments", ("issueId",)),
    ("PassGateReader", "list_comments", ("issueId",)),
    ("PassGateReader", "get_issue", ("id", "includeRelations")),
    ("PassGateReader", "returned", ()),
    ("TrackerArtifactReader", "get_issue", ("id", "includeRelations")),
    ("TrackerArtifactReader", "returned", ()),
    ("TrackerArtifactReader", "list_issues", ("fields", "includeArchived", "limit")),
    ("TrackerArtifactReader", "get_issue", ("id", "includeRelations")),
    ("TrackerArtifactReader", "get_issue", ("id", "includeRelations")),
    ("TrackerArtifactReader", "get_issue", ("id", "includeRelations")),
    ("TrackerArtifactReader", "get_issue", ("id", "includeRelations")),
    ("TrackerArtifactReader", "get_issue", ("id", "includeRelations")),
    ("TrackerArtifactReader", "get_issue", ("id", "includeRelations")),
    ("TrackerArtifactReader", "get_issue", ("id", "includeRelations")),
    ("TrackerArtifactReader", "get_issue", ("id", "includeRelations")),
    ("TrackerArtifactReader", "returned", ()),
    ("SurfaceLeaseTracker", "save_comment", ("body", "issueId")),
    ("SurfaceLeaseTracker", "list_comments", ("issueId",)),
    ("SurfaceLeaseTracker", "save_comment", ("body", "id")),
    ("SurfaceLeaseTracker", "list_comments", ("issueId",)),
    ("SurfaceLeaseTracker", "returned", ()),
    ("SurfaceLeaseTracker", "list_comments", ("issueId",)),
    ("SurfaceLeaseTracker", "save_comment", ("body", "id")),
    ("SurfaceLeaseTracker", "list_comments", ("issueId",)),
    ("SurfaceLeaseTracker", "returned", ()),
    ("SurfaceLeaseTracker", "list_comments", ("issueId",)),
    ("SurfaceLeaseTracker", "delete_comment", ("id",)),
    ("SurfaceLeaseTracker", "returned", ()),
    ("LifecycleStateWriter", "get_issue", ("id", "includeRelations")),
    ("LifecycleStateWriter", "save_issue", ("id", "labels")),
    ("LifecycleStateWriter", "returned", ()),
    ("LifecycleStateWriter", "save_comment", ("body", "issueId")),
    ("LifecycleStateWriter", "returned", ()),
    ("ContainerMetadataReader", "get_project", ("query",)),
    ("ContainerMetadataReader", "get_initiative", ("includeSubInitiatives", "query")),
    ("ContainerMetadataReader", "returned", ()),
    ("OrganizeContextTracker", "get_project", ("query",)),
    ("OrganizeContextTracker", "list_milestones", ("project",)),
    ("OrganizeContextTracker", "get_milestone", ("project", "query")),
    ("OrganizeContextTracker", "list_milestones", ("project",)),
    ("OrganizeContextTracker", "returned", ()),
    ("FireDispatchTracker", "get_project", ("query",)),
    ("FireDispatchTracker", "returned", ()),
)


#: Read off the adapter before the split, by running
#: ``sent_where_no_conformance_case_looks`` once: step index to the digest of
#: the calls that step made, values kept and only the nonce erased.
RECORDED_VALUES: dict[int, str] = {
    4: "c373a4efa64a68c921e6f83bdf09adfb6b98d4a335014aeb8521bd58b7bf3b5d",
    9: "0e1b12ce640e6fde4996850687b89d4dc0025581b6630d888dd0dbce23c3f14e",
    13: "0e1b12ce640e6fde4996850687b89d4dc0025581b6630d888dd0dbce23c3f14e",
    14: "81ad9c7fb9e38233a335e8173a9145adbc64a31824a8eb79c078fb1eedebcabe",
    15: "81ad9c7fb9e38233a335e8173a9145adbc64a31824a8eb79c078fb1eedebcabe",
    19: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    35: "0e1b12ce640e6fde4996850687b89d4dc0025581b6630d888dd0dbce23c3f14e",
    40: "97fae04e8892539e9a2c1193b4ca16d2fbf093b3c8971b32134b394e70e3efbb",
    42: "2edf077cca2996aa93c16223006fb01f87a85b64143bc14bc2d0fda787c915d8",
    43: "97fae04e8892539e9a2c1193b4ca16d2fbf093b3c8971b32134b394e70e3efbb",
    51: "22124195923ca9ec43e5c1718cc24e9a8d8d31d25cd0148e41148558060cb2b1",
    52: "a37b28ca3a08be52cdedf1178ad905c730119c3d7c78e759ab3df119030edd28",
}
