"""The handover checks the write-nothing passes are held to, checked themselves.

``tests.fakes.handed_over`` and ``tests.fakes.nothing_written`` are what every
"this pass wrote nothing to the board" assertion in the suite comes down to:
the first renders the double's whole surface when the board is handed over and
compares it when the answer is asked for; the second compares the same
rendering projected onto the journals a write can land in, which is the claim
a consumer that legitimately reads the board can still make. An answer of
``True`` for a board something DID write to would make all of those assertions
agree with the write they exist to catch, and none of them would report it —
the failure is silent by construction, because a check that always answers
"untouched" looks exactly like a consumer that touched nothing.

So the answer is exercised here directly, over EVERY MEMBER THE PORT
DECLARES, read off the port's whole class line — the port and the reader
roles it extends — with no list of verbs deciding which of them count as
writes.  Each member is classified by a case that RUNS it: a write case
declares the journals its write fills and they are compared exactly, and a
read case declares none and is shown to fill none.  A member added to the
port under any name arrives with no case and fails here, naming it; a write
the double reaches through no journal fails its case; a read whose double
writes a journal fails its case; and a journal no write fills fails here too.
None of that can hide in a list that drifted from the port.

The one write that answers ``True`` is a mapping ensure that ADOPTS what the
workspace already defines, and it answers True because it writes nothing:
that case is below as well, with its journal shown empty.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

import pytest

from kodezart.core.protocols import TrackerPort
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.criterion_creation import criterion_body
from kodezart.domain.organize_graph import graph_snapshot
from kodezart.domain.run_alarm_record import run_alarm_marker, run_alarm_surface
from kodezart.domain.run_event_stream import LaneRunEvent
from kodezart.types.domain.branch import WorkRef, WorkRefRole, trunk_base
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.operation import LifecycleStage, QueueState, ScopeLabel
from kodezart.types.domain.organize_graph import PriorityChange
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    CountEvidence,
    RunAlarm,
    ScopeSubject,
)
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import (
    IssuePriority,
    IssueQuery,
    MappingKind,
    MappingRef,
    ReviewQuery,
    WorkflowStateKind,
)
from tests.fakes import (
    FIXTURE_EPOCH,
    FIXTURE_TEAM_KEY,
    TRACKER_WRITE_JOURNALS,
    FakeTrackerPort,
    handed_over,
    make_tracker_issue,
    nothing_written,
    tracker_state,
)

ISSUE = "KOD-1"
#: A second issue, in a state of its own, so the state a put-back names is
#: one the board already defines: a put-back to the issue's own state is
#: the double's no-op and would fill no journal.
OTHER = "KOD-2"
#: A third key, deliberately NOT on the board: a ref recorded against an
#: issue the board does not hold moves no stamp, so the journal is the write's
#: only trace and the case below is about that journal and no other.
ABSENT = "KOD-3"
#: A criterion sub-issue of the first, carrying a criterion's own body: the
#: subject of the two writes that address a criterion rather than an issue.
CRITERION = "KOD-4"
STARTED_STATE = "In Progress"
HOLDER = "fixture-holder"
LEASE_SECONDS = 60.0
CONTAINER = "fixture-container"
#: The marker deployment the record writes and the reads of records compose
#: their markers from.  Declared here rather than borrowed, because the only
#: thing asked of it is that a record HAS an address on this board.
PREFIXES = {
    "run_event": "fixture-runevent",
    "run_alarm": "fixture-alarm",
    "escalation": "fixture-escalation",
    "decision": "fixture-decision",
}
#: The page the two scans ask for: any positive size is a valid scan.
PAGE = 10
#: The alarm the record case keeps, in its smallest valid shape: what is asked
#: of it is where the record lands, not what it says.
ALARM = RunAlarm(
    subject=ScopeSubject(scope_key="scope/fixture"),
    signal=AlarmSignal.SURFACE_CONTENDED,
    readings=(
        AlarmReading(source_ref="fixture/reading", value=CountEvidence(value=1)),
    ),
    bound=None,
    raised_at_sha="a" * 40,
    raised_by=HOLDER,
)
ALARM_MARKER = run_alarm_marker(
    subject=ALARM.subject, signal=ALARM.signal, marker_prefixes=PREFIXES
)


def criterion_source() -> str:
    """A criterion body the double will consume as one.

    Composed by the shipped composer rather than typed out, so the fixture
    cannot describe a shape a criterion reader would refuse.
    """
    return criterion_body(parent_key=ISSUE, check="a criterion holds", do="hold it")


def board() -> FakeTrackerPort:
    """A board handed over with three issues on it and nothing else seeded.

    No issue carries a queue state or a classification, so the writes below
    are real writes rather than the double's no-ops, and no document and no
    mapping value is held, so the ensures create rather than adopting.  The
    two started issues leave exactly one unstarted team state, which is what
    a criterion put back to pending resolves to.
    """
    return FakeTrackerPort(
        issues=[
            make_tracker_issue(ISSUE, queue_states=()),
            make_tracker_issue(
                OTHER,
                state_name=STARTED_STATE,
                state_kind=WorkflowStateKind.STARTED,
                queue_states=(),
            ),
            make_tracker_issue(
                CRITERION,
                parent_key=ISSUE,
                issue_labels=frozenset({"criterion"}),
                body=criterion_source(),
                state_name=STARTED_STATE,
                state_kind=WorkflowStateKind.STARTED,
                queue_states=(),
            ),
        ],
        marker_prefixes=PREFIXES,
    )


def issue_surface(kind: SurfaceKind, issue_key: str = ISSUE) -> WritableSurface:
    """One leased address on an issue, by the kind of write it grants."""
    return WritableSurface(kind=kind, ref=ScopeRef(kind=ScopeKind.ISSUE, key=issue_key))


def leased_surface() -> WritableSurface:
    """The one surface the lease rows below take and give back."""
    return issue_surface(SurfaceKind.ISSUE_DESCRIPTION)


async def hold(port: FakeTrackerPort, surface: WritableSurface) -> None:
    """Take the grant a write requires, BEFORE the board is handed over."""
    await port.acquire_surfaces(
        surfaces=frozenset({surface}), holder=HOLDER, lease_seconds=LEASE_SECONDS
    )


async def hold_graph(port: FakeTrackerPort) -> None:
    await hold(port, issue_surface(SurfaceKind.ISSUE_GRAPH))


async def hold_split_set(port: FakeTrackerPort) -> None:
    await hold(port, issue_surface(SurfaceKind.ISSUE_SPLIT_SET))


async def hold_child_set(port: FakeTrackerPort) -> None:
    await hold(port, issue_surface(SurfaceKind.CRITERION_CHILD_SET))


async def hold_description(port: FakeTrackerPort) -> None:
    await hold(port, leased_surface())


async def hold_alarm_record(port: FakeTrackerPort) -> None:
    await hold(port, run_alarm_surface(issue_key=ISSUE, marker=ALARM_MARKER))


async def write_issue(port: FakeTrackerPort) -> None:
    await port.update_issue(issue_key=ISSUE, title="a retitled issue")


async def write_description(port: FakeTrackerPort) -> None:
    await port.edit_description(
        target=ISSUE, expected="fixture body", replacement="another body"
    )


async def write_classification(port: FakeTrackerPort) -> None:
    await port.set_issue_classification(issue_key=ISSUE, classification="criterion")


async def write_comment(port: FakeTrackerPort) -> None:
    await port.post_comment(issue_key=ISSUE, body="a comment nobody asked for")


async def write_marked_comment(port: FakeTrackerPort) -> None:
    await port.upsert_comment(target=ISSUE, marker="fixture-marker", body="a body")


async def write_run_event(port: FakeTrackerPort) -> None:
    await port.post_run_event(
        issue_key=ISSUE,
        event=LaneRunEvent(kind=RunEventKind.LANE_DISPATCHED, lane_key="lane-alpha"),
    )


async def write_run_alarm(port: FakeTrackerPort) -> None:
    await port.record_run_alarm(issue_key=ISSUE, alarm=ALARM, holder=HOLDER)


async def write_workflow_state(port: FakeTrackerPort) -> None:
    await port.set_workflow_state(issue_key=ISSUE, stage=LifecycleStage.IN_PROGRESS)


async def write_claim(port: FakeTrackerPort) -> None:
    await port.claim_issue(issue_key=ISSUE, holder=HOLDER, lease_seconds=LEASE_SECONDS)


async def write_renewal(port: FakeTrackerPort) -> None:
    # Journalled whether or not it is granted: an attempt is the write.
    await port.renew_claim(issue_key=ISSUE, holder=HOLDER, lease_seconds=LEASE_SECONDS)


async def write_surface_lease(port: FakeTrackerPort) -> None:
    await port.acquire_surfaces(
        surfaces=frozenset({leased_surface()}),
        holder=HOLDER,
        lease_seconds=LEASE_SECONDS,
    )


async def write_surface_renewal(port: FakeTrackerPort) -> None:
    # The grant is taken in this case's setup, so what is measured here is
    # the renewal and not the acquisition that made one possible.
    await port.renew_surfaces(
        surfaces=frozenset({leased_surface()}),
        holder=HOLDER,
        lease_seconds=LEASE_SECONDS,
    )


async def write_claim_release(port: FakeTrackerPort) -> None:
    # Journalled whether or not anything was held: an attempt is the write.
    # This board holds no claim, so the release moves nothing else at all —
    # which is what makes the case about this journal and no other.
    await port.release_claim(issue_key=ISSUE, holder=HOLDER)


async def write_lease_release(port: FakeTrackerPort) -> None:
    # The same for a surface set: nothing is leased here, so giving one back
    # leaves ``leases`` where it was and the attempt is the only trace.
    await port.release_surfaces(surfaces=frozenset({leased_surface()}), holder=HOLDER)


async def write_issue_creation(port: FakeTrackerPort) -> None:
    await port.create_issue(
        title="a new issue",
        body="a body",
        team_key=FIXTURE_TEAM_KEY,
        priority=IssuePriority.NONE,
    )


async def write_identified_issue(port: FakeTrackerPort) -> None:
    # No issue on this board carries the identity, so the upsert creates.
    await port.upsert_issue(
        scope_key=ScopeRef(kind=ScopeKind.ISSUE, key=OTHER),
        deliverable_key="a-deliverable",
        title="an identified issue",
        body="a body",
        team_key=FIXTURE_TEAM_KEY,
        priority=IssuePriority.NONE,
    )


async def write_split(port: FakeTrackerPort) -> None:
    issue = await port.read_issue(issue_key=ISSUE)
    await port.create_split_if_absent(
        source_key=ISSUE,
        deliverable_key="a-split",
        title="a split child",
        body="a body",
        holder=HOLDER,
        expected=(graph_snapshot(issue),),
    )


async def write_criterion(port: FakeTrackerPort) -> None:
    await port.create_criterion_if_absent(
        parent_key=ISSUE,
        title="a second criterion",
        check="another criterion holds",
        do="hold it too",
        holder=HOLDER,
    )


async def write_criterion_reset(port: FakeTrackerPort) -> None:
    expected = await port.read_issue(issue_key=CRITERION)
    await port.reset_criterion_pending(expected=expected)


async def write_graph(port: FakeTrackerPort) -> None:
    issue = await port.read_issue(issue_key=ISSUE)
    await port.update_issue_graph(
        issue_key=ISSUE,
        expected=(graph_snapshot(issue),),
        changes=(PriorityChange(kind="priority", priority=IssuePriority.HIGH),),
        holder=HOLDER,
    )


async def write_base_spec(port: FakeTrackerPort) -> None:
    await port.record_base_spec(issue_key=ISSUE, spec=trunk_base("fixture-trunk"))


async def write_queue_state(port: FakeTrackerPort) -> None:
    await port.set_queue_state(issue_key=ISSUE, state=QueueState.APPROVED)


async def write_restored_state(port: FakeTrackerPort) -> None:
    await port.restore_workflow_state(issue_key=ISSUE, state_name=STARTED_STATE)


async def write_document(port: FakeTrackerPort) -> None:
    # One ensure fills both halves of a document: its title and its body.
    await port.ensure_mappings(
        refs=[
            MappingRef(kind=MappingKind.DOCUMENT, name="a record", scope=CONTAINER),
        ],
    )


async def write_work_ref(port: FakeTrackerPort) -> None:
    await port.record_work_ref(
        ref=WorkRef(
            issue_id=ABSENT,
            role=WorkRefRole.DELIVERABLE,
            branch="fixture-work-branch",
            recorded_at=FIXTURE_EPOCH,
        ),
    )


async def write_scope_label(port: FakeTrackerPort) -> None:
    # A scope label is defined on the workspace, so this ensure stamps no
    # issue, and the three attributes it moves are state a consumer reads
    # back rather than journals — and on a second ensure of the same
    # identifier they would not move at all. The attempt is the only trace.
    await port.ensure_mappings(
        refs=[
            MappingRef(
                kind=MappingKind.SCOPE_LABEL,
                name="a-scope-label",
                identifier="fixture-scope-label",
            ),
        ],
    )


async def write_queue_state_mapping(port: FakeTrackerPort) -> None:
    # The other instatable kinds address the workspace too, and the two
    # attributes this arm fills are read back rather than journalled, so the
    # instatement is the only trace a write-set check can read.
    await port.ensure_mappings(
        refs=[
            MappingRef(
                kind=MappingKind.QUEUE_STATE,
                name="approved",
                identifier="fixture-queue-state",
                scope=FIXTURE_TEAM_KEY,
            ),
        ],
    )


#: The project the two container reads address, seeded before the handover.
PROJECT = ScopeRef(kind=ScopeKind.PROJECT, key="fixture-project")
#: The label that marks a subject's criteria stage complete, for the one read
#: that refuses a subject without it.
CRITERIA_STAGED = "criteria-staged"
LANE = "lane-alpha"
ESCALATION = "fixture-escalation"


async def hold_project(port: FakeTrackerPort) -> None:
    port.scope_containers[PROJECT] = ScopeContainer(
        ref=PROJECT,
        name="a project",
        description="a project body",
        url="https://example.invalid/project/fixture-project",
    )


async def hold_fire_entry(port: FakeTrackerPort) -> None:
    # The two facts a fire subject is refused without: the criteria stage
    # marked complete on the subject, and an approval covering it.
    port.criteria_stage_label_key = CRITERIA_STAGED
    issue = port.issues[ISSUE]
    port.issues[ISSUE] = issue.model_copy(
        update={"issue_labels": issue.issue_labels | {CRITERIA_STAGED}}
    )
    port.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key=ISSUE)] = frozenset(
        {ScopeLabel.APPROVED}
    )


async def hold_escalation(port: FakeTrackerPort) -> None:
    # The one escalation record the resolution read requires, with no
    # decision under it yet.
    marker = compose_comment_marker(
        prefixes=PREFIXES, purpose="escalation", lane=LANE, occurrence_key=ESCALATION
    )
    await port.post_comment(issue_key=ISSUE, body=f"{marker}\nan escalation")


async def read_active_claim(port: FakeTrackerPort) -> None:
    await port.active_claim(issue_key=ISSUE)


async def read_container(port: FakeTrackerPort) -> None:
    await port.container_metadata(ref=PROJECT)


async def read_execution_approval(port: FakeTrackerPort) -> None:
    await port.execution_approved(issue_key=ISSUE)


async def read_initiatives(port: FakeTrackerPort) -> None:
    await port.initiative_identifiers(project_id=PROJECT.key)


async def read_lane_events(port: FakeTrackerPort) -> None:
    await port.lane_run_events(issue_key=ISSUE, lane_key=LANE)


async def read_comments(port: FakeTrackerPort) -> None:
    await port.list_comments(issue_key=ISSUE)


async def read_assets(port: FakeTrackerPort) -> None:
    await port.list_issue_assets(issue_key=ISSUE)


async def read_milestones(port: FakeTrackerPort) -> None:
    await port.project_milestones(project_key=PROJECT.key)


async def read_recorded_base_spec(port: FakeTrackerPort) -> None:
    await port.read_base_spec(issue_key=ISSUE)


async def read_criterion_family(port: FakeTrackerPort) -> None:
    await port.read_criteria(issue_key=ISSUE)


async def read_ensured_document(port: FakeTrackerPort) -> None:
    # The document the setup's ensure created, read back by its key.
    (document_key,) = port.document_titles
    await port.read_document(document_key=document_key)


async def read_escalation(port: FakeTrackerPort) -> None:
    await port.read_escalation_resolution(
        issue_key=ISSUE, lane_key=LANE, escalation_key=ESCALATION
    )


async def read_subject(port: FakeTrackerPort) -> None:
    await port.read_fire_subject(issue_key=ISSUE)


async def read_one_issue(port: FakeTrackerPort) -> None:
    await port.read_issue(issue_key=ISSUE)


async def read_identity(port: FakeTrackerPort) -> None:
    await port.read_issue_identity(issue_key=ISSUE)


async def read_movement(port: FakeTrackerPort) -> None:
    await port.read_issue_movement(issue_key=ISSUE)


async def read_revision(port: FakeTrackerPort) -> None:
    await port.read_issue_revision(issue_key=ISSUE)


async def read_state_change(port: FakeTrackerPort) -> None:
    await port.read_issue_state_change(issue_key=ISSUE)


async def read_labelled(port: FakeTrackerPort) -> None:
    await port.read_labeled_issues(classification="criterion")


async def read_planning_issue(port: FakeTrackerPort) -> None:
    await port.read_planning_issue(issue_key=ISSUE)


async def read_alarm(port: FakeTrackerPort) -> None:
    await port.read_run_alarm(
        issue_key=ISSUE, subject=ALARM.subject, signal=ALARM.signal
    )


async def read_scope_labels(port: FakeTrackerPort) -> None:
    await port.read_scope_labels(ref=ScopeRef(kind=ScopeKind.ISSUE, key=ISSUE))


async def read_split_children(port: FakeTrackerPort) -> None:
    await port.read_split_children(source_key=ISSUE)


async def read_authorship(port: FakeTrackerPort) -> None:
    await port.read_surface_authorship(surface=leased_surface())


async def read_repository(port: FakeTrackerPort) -> None:
    await port.recorded_repository(issue_key=ISSUE)


async def require_classification_reads(port: FakeTrackerPort) -> None:
    port.require_issue_classification_reads()


async def require_plan_reads(port: FakeTrackerPort) -> None:
    port.require_scope_plan_reads()


async def read_unresolved_mappings(port: FakeTrackerPort) -> None:
    await port.resolve_mappings(
        refs=[
            MappingRef(
                kind=MappingKind.QUEUE_STATE,
                name="approved",
                identifier="fixture-queue-state",
                scope=FIXTURE_TEAM_KEY,
            ),
        ],
    )


async def read_issue_scan(port: FakeTrackerPort) -> None:
    await port.scan_issues(query=IssueQuery(page_size=PAGE))


async def read_review_scan(port: FakeTrackerPort) -> None:
    await port.scan_reviews(query=ReviewQuery(page_size=PAGE))


async def read_scope(port: FakeTrackerPort) -> None:
    await port.scope_issues(ref=ScopeRef(kind=ScopeKind.ISSUE, key=ISSUE))


async def read_scan_capability(port: FakeTrackerPort) -> None:
    await port.verify_scan_capability(signals=tuple(PassSignal))


async def read_work_refs(port: FakeTrackerPort) -> None:
    await port.work_refs(issue_key=ISSUE)


async def read_writer_identity(port: FakeTrackerPort) -> None:
    await port.writer_identity()


@dataclass(frozen=True)
class Case:
    """One port member, run: the method, how it is called, and where it lands.

    A write declares the journals it fills; a read declares none, and that
    empty set is what classifies it.
    """

    method: str
    call: Callable[[FakeTrackerPort], Awaitable[None]]
    journals: frozenset[str]
    setup: Callable[[FakeTrackerPort], Awaitable[None]] | None = None


#: One case per member the port declares, through the method itself: each
#: write with the journals it fills, each read with none.  Several cases may
#: name one method — the mapping ensure has an arm per kind and each lands
#: somewhere else — and every member the port declares must be named by one,
#: which the census below is what enforces.
CASES: Mapping[str, Case] = {
    "an issue retitled": Case(
        method="update_issue",
        call=write_issue,
        journals=frozenset({"issue_writes", "self_writes"}),
    ),
    "a description replaced": Case(
        method="edit_description",
        call=write_description,
        journals=frozenset({"issue_writes", "self_writes"}),
    ),
    "a classification set": Case(
        method="set_issue_classification",
        call=write_classification,
        journals=frozenset({"classification_writes", "self_writes"}),
    ),
    "a comment posted": Case(
        method="post_comment",
        call=write_comment,
        journals=frozenset({"comment_writes", "self_writes"}),
    ),
    "a marked comment upserted": Case(
        method="upsert_comment",
        call=write_marked_comment,
        journals=frozenset({"comment_writes", "self_writes"}),
    ),
    "a run event posted": Case(
        method="post_run_event",
        call=write_run_event,
        journals=frozenset({"comment_writes", "self_writes"}),
    ),
    "a run alarm recorded": Case(
        method="record_run_alarm",
        call=write_run_alarm,
        journals=frozenset({"comment_writes", "self_writes"}),
        setup=hold_alarm_record,
    ),
    "a workflow state set": Case(
        method="set_workflow_state",
        call=write_workflow_state,
        journals=frozenset({"workflow_writes", "self_writes"}),
    ),
    "a queue state set": Case(
        method="set_queue_state",
        call=write_queue_state,
        journals=frozenset({"queue_writes", "self_writes"}),
    ),
    "a workflow state put back": Case(
        method="restore_workflow_state",
        call=write_restored_state,
        journals=frozenset({"restored_states", "self_writes"}),
    ),
    "a claim taken": Case(
        method="claim_issue",
        call=write_claim,
        journals=frozenset({"claim_writes", "self_writes"}),
    ),
    "a claim renewed": Case(
        method="renew_claim",
        call=write_renewal,
        journals=frozenset({"renewals"}),
    ),
    "a claim released": Case(
        method="release_claim",
        call=write_claim_release,
        journals=frozenset({"claim_releases"}),
    ),
    "a surface set leased": Case(
        method="acquire_surfaces",
        call=write_surface_lease,
        journals=frozenset({"lease_writes"}),
    ),
    "a surface lease renewed": Case(
        method="renew_surfaces",
        call=write_surface_renewal,
        journals=frozenset({"lease_writes"}),
        setup=hold_description,
    ),
    "a surface set released": Case(
        method="release_surfaces",
        call=write_lease_release,
        journals=frozenset({"lease_releases"}),
    ),
    "an issue created": Case(
        method="create_issue",
        call=write_issue_creation,
        journals=frozenset({"issue_creations"}),
    ),
    "an identified issue upserted": Case(
        method="upsert_issue",
        call=write_identified_issue,
        journals=frozenset({"issue_creations"}),
    ),
    "a split child created": Case(
        method="create_split_if_absent",
        call=write_split,
        journals=frozenset({"issue_creations"}),
        setup=hold_split_set,
    ),
    "a criterion created": Case(
        method="create_criterion_if_absent",
        call=write_criterion,
        journals=frozenset({"issue_creations"}),
        setup=hold_child_set,
    ),
    "a criterion put back to pending": Case(
        method="reset_criterion_pending",
        call=write_criterion_reset,
        journals=frozenset({"self_writes"}),
    ),
    "a graph changed": Case(
        method="update_issue_graph",
        call=write_graph,
        journals=frozenset({"graph_writes"}),
        setup=hold_graph,
    ),
    "a base spec recorded": Case(
        method="record_base_spec",
        call=write_base_spec,
        journals=frozenset({"recorded_base_specs", "self_writes"}),
    ),
    "a work ref recorded": Case(
        method="record_work_ref",
        call=write_work_ref,
        journals=frozenset({"recorded_work_refs"}),
    ),
    "a document ensured": Case(
        method="ensure_mappings",
        call=write_document,
        journals=frozenset({"_documents", "document_titles"}),
    ),
    "a scope label ensured": Case(
        method="ensure_mappings",
        call=write_scope_label,
        journals=frozenset({"label_writes"}),
    ),
    "a queue state instated": Case(
        method="ensure_mappings",
        call=write_queue_state_mapping,
        journals=frozenset({"mapping_instatements"}),
    ),
    "an active claim read": Case(
        method="active_claim",
        call=read_active_claim,
        journals=frozenset(),
    ),
    "a container read": Case(
        method="container_metadata",
        call=read_container,
        journals=frozenset(),
        setup=hold_project,
    ),
    "an execution approval read": Case(
        method="execution_approved",
        call=read_execution_approval,
        journals=frozenset(),
    ),
    "a project's initiatives read": Case(
        method="initiative_identifiers",
        call=read_initiatives,
        journals=frozenset(),
    ),
    "a lane's run events read": Case(
        method="lane_run_events",
        call=read_lane_events,
        journals=frozenset(),
    ),
    "the comments read": Case(
        method="list_comments",
        call=read_comments,
        journals=frozenset(),
    ),
    "the assets read": Case(
        method="list_issue_assets",
        call=read_assets,
        journals=frozenset(),
    ),
    "a project's milestones read": Case(
        method="project_milestones",
        call=read_milestones,
        journals=frozenset(),
        setup=hold_project,
    ),
    "a base spec read": Case(
        method="read_base_spec",
        call=read_recorded_base_spec,
        journals=frozenset(),
    ),
    "a criterion family read": Case(
        method="read_criteria",
        call=read_criterion_family,
        journals=frozenset(),
    ),
    "a document read": Case(
        method="read_document",
        call=read_ensured_document,
        journals=frozenset(),
        setup=write_document,
    ),
    "an escalation read": Case(
        method="read_escalation_resolution",
        call=read_escalation,
        journals=frozenset(),
        setup=hold_escalation,
    ),
    "a fire subject read": Case(
        method="read_fire_subject",
        call=read_subject,
        journals=frozenset(),
        setup=hold_fire_entry,
    ),
    "an issue read": Case(
        method="read_issue",
        call=read_one_issue,
        journals=frozenset(),
    ),
    "an issue identity read": Case(
        method="read_issue_identity",
        call=read_identity,
        journals=frozenset(),
    ),
    "an issue's movement read": Case(
        method="read_issue_movement",
        call=read_movement,
        journals=frozenset(),
    ),
    "an issue revision read": Case(
        method="read_issue_revision",
        call=read_revision,
        journals=frozenset(),
    ),
    "an issue state change read": Case(
        method="read_issue_state_change",
        call=read_state_change,
        journals=frozenset(),
    ),
    "the labelled issues read": Case(
        method="read_labeled_issues",
        call=read_labelled,
        journals=frozenset(),
    ),
    "a planning issue read": Case(
        method="read_planning_issue",
        call=read_planning_issue,
        journals=frozenset(),
    ),
    "a run alarm read": Case(
        method="read_run_alarm",
        call=read_alarm,
        journals=frozenset(),
    ),
    "the scope labels read": Case(
        method="read_scope_labels",
        call=read_scope_labels,
        journals=frozenset(),
    ),
    "the split children read": Case(
        method="read_split_children",
        call=read_split_children,
        journals=frozenset(),
    ),
    "a body's authorship read": Case(
        method="read_surface_authorship",
        call=read_authorship,
        journals=frozenset(),
    ),
    "a recorded repository read": Case(
        method="recorded_repository",
        call=read_repository,
        journals=frozenset(),
    ),
    "the classification reads required": Case(
        method="require_issue_classification_reads",
        call=require_classification_reads,
        journals=frozenset(),
    ),
    "the scope plan reads required": Case(
        method="require_scope_plan_reads",
        call=require_plan_reads,
        journals=frozenset(),
    ),
    "the unresolved mappings read": Case(
        method="resolve_mappings",
        call=read_unresolved_mappings,
        journals=frozenset(),
    ),
    "an issue scan": Case(
        method="scan_issues",
        call=read_issue_scan,
        journals=frozenset(),
    ),
    "a review scan": Case(
        method="scan_reviews",
        call=read_review_scan,
        journals=frozenset(),
    ),
    "a scope's issues read": Case(
        method="scope_issues",
        call=read_scope,
        journals=frozenset(),
    ),
    "the scan capability verified": Case(
        method="verify_scan_capability",
        call=read_scan_capability,
        journals=frozenset(),
    ),
    "the work refs read": Case(
        method="work_refs",
        call=read_work_refs,
        journals=frozenset(),
    ),
    "the writer identity read": Case(
        method="writer_identity",
        call=read_writer_identity,
        journals=frozenset(),
    ),
}


def port_members() -> frozenset[str]:
    """Every public member the port declares, read off its whole class line.

    The port and each reader role it extends, ``object`` left out; bounded by
    the line.  A name is counted whatever it is and whatever it is called:
    nothing here asks whether it starts with a verb.
    """
    return frozenset(
        name
        for role in TrackerPort.__mro__
        if role is not object
        for name in vars(role)
        if not name.startswith("_")
    )


def test_every_member_the_port_declares_is_driven_here() -> None:
    """The census is the PORT's whole surface, not a list this module keeps.

    Read off ``TrackerPort`` alone, because this double is what stands in for
    that port: the roles dialled beside it in the shipped tree write the same
    backend through doubles of their own, and a case here could not drive one.
    Every member counts, read or write, so a member added under ANY name
    arrives with no case, and that is this test.  It does not use
    ``write_methods`` or ``WRITE_VERBS``: those still serve the other register
    checks in the write-back adoption module, and a write named with a verb
    they do not list is exactly what they would miss.
    """
    assert {case.method for case in CASES.values()} == port_members()


def test_every_journal_the_check_reaches_is_filled_by_one_of_these_writes() -> None:
    """And the other direction: no journal in the projection is unexercised.

    A journal nothing fills is a journal whose coverage is asserted and never
    observed, which is how a projection comes to name an attribute the double
    no longer moves.
    """
    filled = frozenset().union(*(case.journals for case in CASES.values()))
    assert filled == TRACKER_WRITE_JOURNALS


@pytest.mark.parametrize(
    "case", sorted(name for name, row in CASES.items() if row.journals)
)
async def test_a_write_through_any_port_method_answers_that_the_board_was_touched(
    case: str,
) -> None:
    """One write, through one port method, and both answers are False."""
    row = CASES[case]
    port = board()
    if row.setup is not None:
        await row.setup(port)
    untouched = handed_over(port)
    unwritten = nothing_written(port)
    before = tracker_state(port)

    await row.call(port)

    # The write landed in the journals this case declares and in no others,
    # so a False answer below is this case's coverage rather than some other
    # write's — including the grant its setup took before the handover.
    after = tracker_state(port)
    moved = {name for name, value in after.items() if before[name] != value}
    assert moved & TRACKER_WRITE_JOURNALS == row.journals
    assert untouched() is False
    # The projection reaches those journals too: a narrowed one would agree.
    assert unwritten() is False


@pytest.mark.parametrize(
    "case", sorted(name for name, row in CASES.items() if not row.journals)
)
async def test_a_read_through_any_port_method_moves_no_journal(case: str) -> None:
    """One read, through one port method, and the write set stands still.

    A read declares no journal, and that is checked by running it: a double
    whose read fills a journal would make every write-set claim over a
    consumer that reads the board answer False for a write nobody made, and
    it fails here instead, naming the read.
    """
    row = CASES[case]
    port = board()
    if row.setup is not None:
        await row.setup(port)
    unwritten = nothing_written(port)
    before = tracker_state(port)

    await row.call(port)

    after = tracker_state(port)
    moved = {name for name, value in after.items() if before[name] != value}
    assert moved & TRACKER_WRITE_JOURNALS == frozenset()
    assert unwritten() is True


async def test_an_ensure_that_adopts_a_defined_value_writes_nothing() -> None:
    """The ensure that genuinely writes nothing, shown by its empty journal.

    The write-set claim exempts no ensure.  This one answers True because the
    arm returns the identifier the workspace already defines and touches no
    attribute at all — which is stated here as an observation, with the
    journal read back empty, rather than as an exemption in a docstring.
    """
    port = board()
    port.known_identifiers.add("fixture-queue-state")
    port.mapping_containers["fixture-queue-state"] = {None}
    untouched = handed_over(port)
    unwritten = nothing_written(port)

    await write_queue_state_mapping(port)

    assert port.mapping_instatements == []
    assert untouched() is True
    assert unwritten() is True


async def test_a_board_nothing_wrote_to_answers_that_it_is_untouched() -> None:
    """The other half: the checks are observations, not standing refusals.

    Answering False for an untouched board would fail every write-nothing
    case in the suite instead of the writes they are about.
    """
    port = board()

    untouched = handed_over(port)

    assert untouched() is True
    assert nothing_written(port)() is True


async def test_a_read_moves_the_whole_surface_but_not_the_write_set() -> None:
    """Where the two answerers part: a read is a touch, and it is not a write.

    A consumer that re-reads the board before every barrier cannot claim the
    whole surface stood still, and still owes the narrower claim.
    """
    port = board()
    untouched = handed_over(port)
    unwritten = nothing_written(port)

    await port.read_issue(issue_key=ISSUE)

    assert untouched() is False
    assert unwritten() is True
