"""The handover checks the write-nothing passes are held to, checked themselves.

``tests.fakes.handed_over`` and ``tests.fakes.nothing_written`` are what every
"this pass wrote nothing to the board" assertion in the suite comes down to:
the first renders the double's whole surface when the board is handed over and
compares it when the answer is asked for; the second compares a deep copy of
the same whole state with only the read logs left out, which is the claim a
consumer that legitimately reads the board can still make. An answer of
``True`` for a board something DID write to would make all of those assertions
agree with the write they exist to catch, and none of them would report it —
the failure is silent by construction, because a check that always answers
"untouched" looks exactly like a consumer that touched nothing.

So the answer is exercised here directly, over EVERY MEMBER THE PORT
DECLARES, read off the port's whole class line — the port and the reader
roles it extends — with no list of verbs deciding which of them count as
writes.  Each member is classified by a case that RUNS it: a write case
declares the journals its write fills and they are compared exactly, and a
read case declares none and is shown, on a board that holds something in
every attribute, to move no attribute of the double but its own read log.
A member added to the port under any name arrives with no case and fails
here, naming it; a write the double reaches through no journal fails its
case; a read whose double moves anything else — a journal, an issue, a
comment — fails its case; and a journal no write fills fails here too.  The
read logs themselves are derived here from what the reads move, held apart
from every write, and shown read by no method for anything but the record
it appends.  None of that can hide in a list that drifted from the port.

The one write that answers ``True`` is a mapping ensure that ADOPTS what the
workspace already defines, and it answers True because it writes nothing:
that case is below as well, with its journal shown empty.

The reach, stated once.  Every keyed read runs over a fully seeded entry:
the census board fills every field of every model on some instance, and
the entry each keyed read asks for — the issue at the asked key and the
comments on it — fills every field of its own model, held per keyed read
case.  And every read log is trapped at run time: on each census case,
each log the double declares is replaced by a trap that records every
read of itself with the stack that made it, and no read may run under a
method of the double, whatever spelling fetched the log.  The static pass
beside it reads the source and states its own limit; the trap's one limit
is a call on the base type that skips the override, held by a control.
"""

import ast
import importlib
import inspect
import sys
import textwrap
from collections.abc import (
    Awaitable,
    Callable,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
    Sized,
)
from dataclasses import dataclass
from datetime import timedelta
from operator import attrgetter
from pathlib import Path
from types import CodeType, FrameType
from typing import cast, get_type_hints

import pytest
from pydantic import BaseModel

from kodezart.core.protocols import TrackerPort
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.criterion_creation import criterion_body
from kodezart.domain.organize_graph import graph_snapshot
from kodezart.domain.run_alarm_record import run_alarm_marker, run_alarm_surface
from kodezart.domain.run_event_stream import LaneRunEvent
from kodezart.types.domain.branch import (
    BaseInput,
    BaseSpec,
    WorkRef,
    WorkRefRole,
    trunk_base,
)
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
from kodezart.types.domain.surface import (
    SurfaceAuthorship,
    SurfaceKind,
    WritableSurface,
)
from kodezart.types.domain.tracker import (
    ClaimResult,
    ClaimStatus,
    IssuePriority,
    IssueQuery,
    IssueRelation,
    IssueRelationKind,
    MappingKind,
    MappingRef,
    ReviewQuery,
    TrackerAsset,
    TrackerComment,
    TrackerReview,
    WorkflowStateKind,
)
from tests.chains.test_native_fire import CountingTracker
from tests.fakes import (
    FIXTURE_EPOCH,
    FIXTURE_TEAM_KEY,
    TRACKER_WRITE_JOURNALS,
    FakeTrackerPort,
    handed_over,
    make_tracker_issue,
    moved_by_hand,
    nothing_written,
    tracker_state,
    written_state,
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


def board(double: type[FakeTrackerPort] = FakeTrackerPort) -> FakeTrackerPort:
    """A board handed over with three issues on it and nothing else seeded.

    No issue carries a queue state or a classification, so the writes below
    are real writes rather than the double's no-ops, and no document and no
    mapping value is held, so the ensures create rather than adopting.  The
    two started issues leave exactly one unstarted team state, which is what
    a criterion put back to pending resolves to.

    For a subclass *double*, the same board in an instance of that class:
    built the way the class builds itself, with no arguments, then holding
    this board's state in every attribute the double declares, so its own
    attributes stay its own and every read runs through its overrides.
    """
    seed = FakeTrackerPort(
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
    if double is FakeTrackerPort:
        return seed
    port = double()
    vars(port).update(vars(seed))
    return port


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
    # issue. A new one lands in the three attributes a label instatement
    # fills — the scope labels, the identifiers the workspace knows and the
    # containers each value is defined in — and on a second ensure of the
    # same identifier those would not move at all, so the attempt is the one
    # trace every ensure leaves.
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
    # The other instatable kinds address the workspace too: this arm fills
    # the identifiers the workspace knows and the containers each value is
    # defined in, and records the instatement itself.
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


async def write_issue_label(port: FakeTrackerPort) -> None:
    # A label the workspace does not hold yet, so the ensure creates it: its
    # identifier becomes known, defined in the container the ref declares.
    await port.ensure_mappings(
        refs=[
            MappingRef(
                kind=MappingKind.ISSUE_LABEL,
                name="a label",
                identifier="fixture-label",
                scope=CONTAINER,
            ),
        ],
    )


#: The project the two container reads address, seeded before the handover.
PROJECT = ScopeRef(kind=ScopeKind.PROJECT, key="fixture-project")
#: The one milestone the census board's project holds, and the milestone one
#: of its issues belongs to.
MILESTONE = ScopeRef(kind=ScopeKind.MILESTONE, key="fixture-milestone")
#: The holder that lost the census board's one contended claim.
LOSER = "another-holder"
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
    # decision under it yet, and the comment read refusal the census board
    # holds lifted: with it the read answers with that refusal and reads no
    # comment at all.
    port.comment_read_error = None
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
    await port.read_run_alarms(issue_key=ISSUE)


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
        journals=frozenset({"_documents", "document_titles", "known_identifiers"}),
    ),
    "a scope label ensured": Case(
        method="ensure_mappings",
        call=write_scope_label,
        journals=frozenset(
            {
                "label_writes",
                "scope_label_identifiers",
                "known_identifiers",
                "mapping_containers",
            }
        ),
    ),
    "a queue state instated": Case(
        method="ensure_mappings",
        call=write_queue_state_mapping,
        journals=frozenset(
            {"mapping_instatements", "known_identifiers", "mapping_containers"}
        ),
    ),
    "an issue label instated": Case(
        method="ensure_mappings",
        call=write_issue_label,
        journals=frozenset(
            {"mapping_instatements", "known_identifiers", "mapping_containers"}
        ),
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
        method="read_run_alarms",
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


#: The cases, by name, that classify a member as a read (no journal) and as
#: a write (the journals it fills).
READS = sorted(name for name, row in CASES.items() if not row.journals)
WRITES = sorted(name for name, row in CASES.items() if row.journals)

#: The repository the census board's review and recorded repository sit in.
REPO = "https://example.invalid/fixture-repo"


def paged_parameter(method: str) -> tuple[str, type[BaseModel]] | None:
    """The parameter through which the port's *method* asks for a page, if any.

    Read off the port's own annotations: a parameter typed by a model that
    declares a ``page_size``.  Bounded by the method's parameters.
    """
    hints = get_type_hints(getattr(TrackerPort, method))
    for name, kind in hints.items():
        if (
            name != "return"
            and isinstance(kind, type)
            and issubclass(kind, BaseModel)
            and "page_size" in kind.model_fields
        ):
            return name, kind
    return None


async def paged_queries(
    double: type[FakeTrackerPort],
) -> dict[str, tuple[str, BaseModel]]:
    """Each read case that asks for a page, with the query it asks, read off the double.

    Each such case runs alone on a fresh :func:`board` of *double*, and the
    query is what the double's read logs recorded for it: the one entry of
    the parameter's own type.  So the page the census board has to exceed is
    the one the reads ask for, not a size written here.
    """
    found: dict[str, tuple[str, BaseModel]] = {}
    for case in READS:
        row = CASES[case]
        paged = paged_parameter(row.method)
        if paged is None:
            continue
        name, kind = paged
        port = board(double)
        await row.call(port)
        (query,) = [
            entry
            for log in double.READ_LOGS
            if isinstance(logged := getattr(port, log), list)
            for entry in logged
            if isinstance(entry, kind)
        ]
        found[case] = (name, query)
    return found


async def full_board(
    double: type[FakeTrackerPort] = FakeTrackerPort,
) -> FakeTrackerPort:
    """The census board: every attribute, and every field on it, holds something.

    :func:`board` of *double*, then one of each write the census declares,
    each after its own setup, then by hand whatever those writes leave
    empty: the project with a member, an initiative and a milestone, the
    fire entry's stage label and approval, an asset with its type and size,
    the reviews, a recorded repository, a body's authorship and held
    writer, a refused scan, the approval aliases and a comment read
    refusal; and the fields no write fills — on one issue past the seeded
    three, and on the issue every keyed read asks for, a blocked-by
    relation, the milestone, an assignee, the project in both spellings
    and (on the asked one) a parent; a comment on a second issue, in reply
    to one on the first, and a reply on the asked issue itself; a claim
    that names its current holder; a base spec with an input and a role; a
    work ref with a pushed head.  Every paged collection — the
    issues and the reviews — holds one more entry than the largest page any
    read case asks the double for, so a paged read always leaves something
    it paged past.  Every attribute but the read logs holds something, and
    every field of every model on it, at any depth, holds something on at
    least one instance, so a read that moves an attribute — empties the
    reviews, prunes what it paged past, drops a lease, rebinds the clock —
    or erases a field on every instance — every issue's relations, the
    comments on other issues — moves something on it.  Held to that by
    :func:`test_the_census_board_holds_something_in_every_attribute` and
    :func:`test_every_paged_read_leaves_something_past_its_page`.
    """
    page = max(query.page_size for _, query in (await paged_queries(double)).values())
    port = board(double)
    for case in WRITES:
        row = CASES[case]
        if row.setup is not None:
            await row.setup(port)
        await row.call(port)
    await hold_project(port)
    await hold_fire_entry(port)
    port.scope_memberships[PROJECT] = (ISSUE,)
    port.initiative_identifiers_by_project[PROJECT.key] = frozenset({"an initiative"})
    port.scope_containers[MILESTONE] = ScopeContainer(
        ref=MILESTONE,
        name="a milestone",
        description="a milestone body",
        url="https://example.invalid/milestone/fixture-milestone",
        parent=PROJECT,
    )
    port._assets[ISSUE] = (
        TrackerAsset(
            asset_key="fixture-asset",
            title="an asset",
            url="https://example.invalid/asset/fixture-asset",
            content_type="text/plain",
            size_bytes=1,
        ),
    )
    for index in range(page + 1 - len(port.issues)):
        key = f"PAGE-{index}"
        port.issues[key] = make_tracker_issue(key, queue_states=())
    # The fields no write on this board fills, on the first issue past the
    # seeded three; the milestone and the assignee are the two the issue
    # factory does not take.
    filled = "PAGE-0"
    assert filled in port.issues
    port.issues[filled] = make_tracker_issue(
        filled,
        queue_states=(),
        blocked_by=(OTHER,),
        project=PROJECT.key,
        project_id=f"{PROJECT.key}-id",
    ).model_copy(update={"milestone_key": MILESTONE.key, "assignee_key": HOLDER})
    # The key every keyed read asks for fills every field of its model as
    # well, so a read that erases a field on the very entry it was asked
    # for moves it: a blocked-by relation, the project in both spellings,
    # the milestone, an assignee and a parent.  Held to that, per keyed
    # read case, by :func:`test_every_keyed_read_asks_for_a_fully_seeded_entry`.
    asked = port.issues[ISSUE]
    port.issues[ISSUE] = asked.model_copy(
        update={
            "relations": (
                IssueRelation(kind=IssueRelationKind.BLOCKED_BY, issue_key=OTHER),
            ),
            "project": PROJECT.key,
            "project_id": f"{PROJECT.key}-id",
            "milestone_key": MILESTONE.key,
            "assignee_key": HOLDER,
            "parent_key": OTHER,
        }
    )
    (first_comment, *_) = port.comments
    assert first_comment.issue_key == ISSUE
    port.comments.append(
        TrackerComment(
            comment_key="comment-on-another-issue",
            issue_key=OTHER,
            author_key=HOLDER,
            body="a reply on another issue",
            created_at=FIXTURE_EPOCH,
            reply_to=first_comment.comment_key,
        )
    )
    # And a reply on the asked issue itself, so its own comments fill every
    # field of theirs, the reply relation among them.
    port.comments.append(
        TrackerComment(
            comment_key="reply-on-the-asked-issue",
            issue_key=ISSUE,
            author_key=HOLDER,
            body="a reply on the asked issue",
            created_at=FIXTURE_EPOCH,
            reply_to=first_comment.comment_key,
        )
    )
    # The one claim shape that names a current holder: a claim lost to one.
    port.claims[OTHER] = ClaimResult(
        issue_key=OTHER,
        status=ClaimStatus.LOST,
        holder=LOSER,
        expires_at=FIXTURE_EPOCH + timedelta(seconds=LEASE_SECONDS),
        current_holder=HOLDER,
    )
    port.recorded_base_specs[OTHER] = BaseSpec(
        inputs=(
            BaseInput(blocker_issue_id=ISSUE, branch="fixture-blocker", sha="b" * 40),
        ),
        base_branch="fixture-integration",
        base_role=WorkRefRole.INTEGRATION,
    )
    port.recorded_work_refs[OTHER] = [
        WorkRef(
            issue_id=OTHER,
            role=WorkRefRole.DELIVERABLE,
            branch="fixture-pushed-branch",
            pushed_head_sha="e" * 40,
            recorded_at=FIXTURE_EPOCH,
        )
    ]
    port.reviews[REPO] = [
        TrackerReview(
            review_key=f"fixture-review-{index}",
            updated_at=FIXTURE_EPOCH - timedelta(minutes=index),
        )
        for index in range(page + 1)
    ]
    port.recorded_repositories[ISSUE] = REPO
    port.body_authorship[ISSUE] = SurfaceAuthorship.MACHINE_AUTHORED
    port.body_write_holders[leased_surface()] = [HOLDER]
    port.scan_refusals[next(iter(PassSignal))] = "a refused scan"
    port.approval_classifications = frozenset({"fixture-approval"})
    port.approval_queue_states = frozenset({QueueState.DECISION})
    port.comment_read_error = "a refused comment read"
    return port


async def case_board(
    case: str, double: type[FakeTrackerPort] = FakeTrackerPort
) -> FakeTrackerPort:
    """The board *case* runs on, its setup done.

    A read runs on the full census board of *double*, so whatever it could
    move is there to move; a write runs on :func:`board`, whose empty
    journals are what its case's journals are compared against.
    """
    row = CASES[case]
    port = board(double) if row.journals else await full_board(double)
    if row.setup is not None:
        await row.setup(port)
    return port


def declaring_doubles() -> list[type[FakeTrackerPort]]:
    """The double and every subclass of it that declares read logs of its own.

    Walked over ``__subclasses__``, each class once, so bounded by the
    classes defined; the counting board the lane's delivery is driven on is
    imported above and is always among them.
    """
    found: list[type[FakeTrackerPort]] = []
    seen: set[type[FakeTrackerPort]] = set()
    pending: list[type[FakeTrackerPort]] = [FakeTrackerPort]
    while pending:
        double = pending.pop()
        if double in seen:
            continue
        seen.add(double)
        pending.extend(double.__subclasses__())
        if "READ_LOGS" in vars(double):
            found.append(double)
    return found


def written_on() -> set[type[FakeTrackerPort]]:
    """Every double a test module builds where it asks ``nothing_written``.

    Read off the test modules' own source: each module under ``tests/``
    whose code calls ``nothing_written`` is imported, and every class it
    constructs by a name that is the double or a subclass of it is taken.
    Bounded by the files under ``tests/``.
    """
    root = Path(__file__).parent
    found: set[type[FakeTrackerPort]] = set()
    for path in sorted(root.rglob("*.py")):
        source = path.read_text()
        if "nothing_written(" not in source:
            continue
        tree = ast.parse(source)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        if not any(
            isinstance(call.func, ast.Name) and call.func.id == "nothing_written"
            for call in calls
        ):
            continue
        parts = path.relative_to(root.parent).with_suffix("").parts
        module = importlib.import_module(".".join(parts))
        for call in calls:
            if isinstance(call.func, ast.Name):
                built = getattr(module, call.func.id, None)
                if isinstance(built, type) and issubclass(built, FakeTrackerPort):
                    found.add(built)
    return found


def census_doubles() -> list[type[FakeTrackerPort]]:
    """Every double the read census runs on: declaring logs, or claimed over.

    The double and each subclass that declares read logs of its own, and
    every double a test module calls ``nothing_written`` on, the counting
    board the lane's delivery is driven on among them.  In a stable order.
    """
    return sorted(
        {*declaring_doubles(), *written_on()},
        key=lambda double: (double.__module__, double.__qualname__),
    )


def plain_object(value: object) -> bool:
    """Whether *value* is a plain object: compared by identity, with attributes."""
    return (
        type(value).__eq__ is object.__eq__
        and hasattr(value, "__dict__")
        and not callable(value)
    )


def holds_nothing(value: object) -> bool:
    """Whether *value* holds nothing a read could take away.

    ``None``, and a container or a string with no member.  A model holds
    nothing when none of its fields does, and a plain object, one compared
    by identity, when none of its attributes does; bounded by the depth of
    those objects.  A flag and a number hold whichever value they have, and
    so does a callable.
    """
    if isinstance(value, BaseModel):
        return all(
            holds_nothing(getattr(value, name)) for name in type(value).model_fields
        )
    if plain_object(value):
        return all(holds_nothing(item) for item in vars(value).values())
    return value is None or (isinstance(value, Sized) and len(value) == 0)


def fields_held(value: object, found: dict[str, bool] | None = None) -> dict[str, bool]:
    """Every field of every model in *value*, at any depth, and whether one fills it.

    Keyed ``Model.field``.  A field is filled when some instance of the
    model under *value* holds something in it, by :func:`holds_nothing`;
    a field every instance leaves empty is not.  Walked into models field
    by field, the way KOD-313's ``unfilled`` walks the terminal, and
    through mappings (keys and values), sequences, sets and plain objects;
    bounded by the depth of the value.
    """
    if found is None:
        found = {}
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            item = getattr(value, name)
            where = f"{type(value).__qualname__}.{name}"
            found[where] = found.get(where, False) or not holds_nothing(item)
            fields_held(item, found)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            fields_held(key, found)
            fields_held(item, found)
    elif isinstance(value, Sequence | set | frozenset) and not isinstance(
        value, str | bytes
    ):
        for item in value:
            fields_held(item, found)
    elif plain_object(value):
        for item in vars(value).values():
            fields_held(item, found)
    return found


@pytest.mark.parametrize("double", census_doubles(), ids=lambda double: double.__name__)
async def test_the_census_board_holds_something_in_every_attribute(
    double: type[FakeTrackerPort],
) -> None:
    """The board every read case runs on leaves no attribute, and no field, empty.

    The read cases compare the whole state, and that is only as strong as
    the board: a read that empties an attribute the board holds nothing in
    moves nothing, and so does one that erases a field no instance on the
    board fills — every issue's relations, say.  So every attribute of the
    double but its read logs holds something here, and every field of every
    model on it, at any depth, holds something on at least one instance;
    the walk is shown to reach the issues' relations.  Comments sit on at
    least two issues, so a read keyed by issue has entries it must not
    touch.  The only attribute a case's own setup lifts is the comment read
    refusal, for the one read that answers with it.  The read cases hold
    the flag that makes a read move an issue's stamp at its default, off.
    The board is the double's own: every double the census runs on gets
    one of its class.
    """
    port = await full_board(double)
    assert type(port) is double
    held = {
        name: value
        for name, value in vars(port).items()
        if name not in type(port).READ_LOGS
    }
    assert held != {}
    assert sorted(name for name, value in held.items() if holds_nothing(value)) == []
    assert "TrackerIssue.relations" in fields_held(port.issues)
    assert {
        name: sorted(
            field for field, filled in fields_held(value).items() if not filled
        )
        for name, value in held.items()
        if not all(fields_held(value).values())
    } == {}
    assert len({comment.issue_key for comment in port.comments}) >= 2


def keyed_reads() -> list[str]:
    """Every read case whose call names the asked key, read off the call's source.

    A read case addresses the asked key when its call loads the name
    ``ISSUE``; the rest address the workspace, a container or a document.
    Bounded by the read cases.
    """
    found: list[str] = []
    for case in READS:
        tree = ast.parse(textwrap.dedent(inspect.getsource(CASES[case].call)))
        if any(
            isinstance(node, ast.Name)
            and node.id == "ISSUE"
            and isinstance(node.ctx, ast.Load)
            for node in ast.walk(tree)
        ):
            found.append(case)
    return found


def test_the_keyed_reads_are_derived_and_name_the_reads_the_seeds_are_for() -> None:
    """The keyed reads are a non-empty derived list holding the two reads seeded for."""
    keyed = keyed_reads()
    assert {"an issue read", "the comments read"} < set(keyed)
    assert set(keyed) < set(READS)


@pytest.mark.parametrize("double", census_doubles(), ids=lambda double: double.__name__)
@pytest.mark.parametrize("case", keyed_reads())
async def test_every_keyed_read_asks_for_a_fully_seeded_entry(
    case: str, double: type[FakeTrackerPort]
) -> None:
    """The entry each keyed read asks for fills every field of its model.

    The board test above asks that some instance fills each field.  A read
    that rewrites the entry it was asked for — erasing its relations, its
    milestone, its assignee, or the reply relation on its comments —
    moves nothing unless THAT entry filled them.  So on the board each
    keyed read case runs on, its setup done, the issue at the asked key
    fills every field :func:`fields_held` reports for its model, and the
    comments on it, together, fill every field of theirs.
    """
    port = await case_board(case, double)
    issue = port.issues[ISSUE]
    assert (
        sorted(field for field, filled in fields_held(issue).items() if not filled)
        == []
    )
    comments = [comment for comment in port.comments if comment.issue_key == ISSUE]
    assert comments != []
    assert (
        sorted(field for field, filled in fields_held(comments).items() if not filled)
        == []
    )


@pytest.mark.parametrize("double", census_doubles(), ids=lambda double: double.__name__)
async def test_every_paged_read_leaves_something_past_its_page(
    double: type[FakeTrackerPort],
) -> None:
    """Every paged collection on the census board holds more than a page.

    Each read case that asks for a page is asked again, on the double's
    full board, with the query it asks: it answers a whole page, and the
    same query one entry larger answers one more.  So something lies past
    the page each paged read asks for, and a read that prunes what it paged
    past moves it.
    """
    paged = await paged_queries(double)
    assert paged != {}
    for case, (name, query) in paged.items():
        port = await full_board(double)
        read = getattr(port, CASES[case].method)
        page = query.page_size
        wider = query.model_copy(update={"page_size": page + 1})
        assert len(await read(**{name: query})) == page, case
        assert len(await read(**{name: wider})) == page + 1, case


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


def test_every_journal_the_check_reaches_is_written_to_here() -> None:
    """And the other direction: no journal in the projection is unexercised.

    A journal nothing fills is a journal whose coverage is asserted and never
    observed, which is how a projection comes to name an attribute the double
    no longer moves.
    """
    filled = frozenset().union(*(case.journals for case in CASES.values()))
    assert filled == TRACKER_WRITE_JOURNALS


@pytest.mark.parametrize("case", WRITES)
async def test_a_write_on_any_journal_answers_that_the_board_was_touched(
    case: str,
) -> None:
    """One write, through one port method, and both answers are False."""
    row = CASES[case]
    port = await case_board(case)
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


async def moved_by(case: str) -> dict[str, tuple[object, object]]:
    """Every attribute of the case's board its call moves, before and after.

    The WHOLE state, read logs included, rendered on each side, so what
    comes back is everything the call did to the double and nothing its
    setup did before it.
    """
    row = CASES[case]
    port = await case_board(case)
    before = {**written_state(port), **read_logs(port)}
    await row.call(port)
    after = {**written_state(port), **read_logs(port)}
    return {
        name: (before.get(name), value)
        for name, value in after.items()
        if before.get(name) != value
    }


def read_logs(port: FakeTrackerPort) -> dict[str, object]:
    """The read logs of *port*, rendered by the same rule as the rest."""
    return {
        name: rendered
        for name, rendered in tracker_state(port).items()
        if name in FakeTrackerPort.READ_LOGS
    }


@pytest.mark.parametrize("double", census_doubles(), ids=lambda double: double.__name__)
@pytest.mark.parametrize("case", READS)
async def test_a_read_through_any_port_method_moves_no_state(
    case: str, double: type[FakeTrackerPort]
) -> None:
    """One read, through one port method, and the double stands still.

    Run on the full census board, so every attribute holds something the
    read could move.  Every attribute of the instance is compared,
    deep-copied, before and after — not the journals a list names — leaving
    out only the read logs.  A read whose double rewrites an issue, appends
    a comment, empties the reviews, rebinds the clock or moves any other
    attribute fails here, naming the read, and ``nothing_written`` answers
    False for it, because it makes the same comparison.  Run on every
    double the census covers, each on its own full board, so a read a
    subclass overrides is held to the same claim.
    """
    row = CASES[case]
    port = await case_board(case, double)
    unwritten = nothing_written(port)
    before = written_state(port)

    await row.call(port)

    after = written_state(port)
    assert set(after) == set(before)
    assert {name for name, value in after.items() if before[name] != value} == set()
    assert unwritten() is True


async def test_the_read_logs_are_what_the_reads_fill() -> None:
    """The read logs are derived by what they are: what the reads append to.

    Every read the port declares is run on the full census board, and the
    attributes those reads move — the whole state, nothing projected — are exactly the
    logs ``nothing_written`` leaves out, each moved only by appending to
    what it held.  A read that fills anything else widens the first set and
    fails here as well as in its own case; a log no read fills fails here
    too.
    """
    filled: set[str] = set()
    for case in READS:
        for name, (before, after) in (await moved_by(case)).items():
            assert isinstance(before, tuple), (case, name)
            assert isinstance(after, tuple), (case, name)
            assert after[: len(before)] == before, (case, name)
            filled.add(name)
    assert filled == FakeTrackerPort.READ_LOGS


async def test_a_read_log_records_no_write() -> None:
    """No write is recorded only where a read is, and no read log is a journal.

    Every write the port declares is run on a fresh board, and each moves
    the state OUTSIDE the read logs, so the comparison that leaves the logs
    out still sees it; and no write journal is a read log.  A write whose
    only trace is a read log, or a journal named as one, fails here.
    """
    assert FakeTrackerPort.READ_LOGS & TRACKER_WRITE_JOURNALS == frozenset()
    for case in WRITES:
        moved = set(await moved_by(case))
        assert moved - FakeTrackerPort.READ_LOGS, case


#: The calls that move the list, dict or set they are called on.
MUTATORS = frozenset(
    {
        "add",
        "append",
        "clear",
        "discard",
        "extend",
        "insert",
        "pop",
        "popitem",
        "remove",
        "setdefault",
        "update",
    }
)


def own_attribute(node: ast.expr) -> str | None:
    """The ``x`` of ``self.x``, or of ``self.x[...]`` however deep; else None."""
    while isinstance(node, ast.Subscript):
        node = node.value
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        return node.attr
    return None


def moved_in(function: Callable[..., object]) -> frozenset[str]:
    """Every attribute of ``self`` *function*'s own body moves.

    Read off the source: an assignment, an augmented or annotated one, or a
    ``del`` whose target is ``self.x`` or an item of it, and a mutating call
    on ``self.x`` or an item of it.  Bounded by the body's syntax tree.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    moved: set[str] = set()
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign | ast.Delete):
            targets = list(node.targets)
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in MUTATORS
        ):
            targets = [node.func.value]
        for target in targets:
            for part in target.elts if isinstance(target, ast.Tuple) else [target]:
                if (name := own_attribute(part)) is not None:
                    moved.add(name)
    return frozenset(moved)


def test_a_read_log_is_moved_by_the_port_s_reads_alone() -> None:
    """Read in the code: a read log is moved inside a port read and nowhere else.

    For every double the census runs on, every method along its class line
    is read, and each one that moves
    a read log must be a member the census classifies as a read; every log
    it declares must be moved by one of those reads.  A write that records
    itself in a read log — directly, not by calling a read — fails here,
    naming the method and the log, and so does a log nothing fills.
    """
    reads = {CASES[case].method for case in READS}
    assert CountingTracker in census_doubles()
    for double in census_doubles():
        filled: set[str] = set()
        line = [
            cls
            for cls in double.__mro__
            if cls is not object and issubclass(cls, FakeTrackerPort)
        ]
        for cls in line:
            for name, member in vars(cls).items():
                function = getattr(member, "__func__", member)
                if name == "__init__" or not inspect.isfunction(function):
                    continue
                logged = moved_in(function) & double.READ_LOGS
                assert not logged or name in reads, (double, name, logged)
                filled |= logged
        assert filled == double.READ_LOGS, double


#: The calls a read log is recorded with.
RECORDERS = frozenset({"append", "extend"})


def is_instance(node: ast.expr, selves: frozenset[str] | set[str]) -> bool:
    """Whether *node* evaluates to the instance.

    A name in *selves*, or a boolean operation one of whose operands does
    — ``self or None`` is ``self``.  Bounded by the operation's operands.
    """
    if isinstance(node, ast.Name):
        return node.id in selves
    if isinstance(node, ast.BoolOp):
        return any(is_instance(value, selves) for value in node.values)
    return False


def bindings(node: ast.AST) -> list[tuple[ast.expr, ast.expr]]:
    """Every (target, value) pair *node* binds, one per name it binds.

    A plain assignment, each target of a chained one (``port = me = self``),
    an annotated one and a walrus bind their whole value; a tuple or list
    target over a tuple or list value of the same length binds element by
    element (``port, _ = self, None``).  Any other statement binds nothing
    here.  Bounded by the targets.
    """
    if isinstance(node, ast.Assign):
        pairs = [(target, node.value) for target in node.targets]
    elif isinstance(node, ast.AnnAssign | ast.NamedExpr) and node.value is not None:
        pairs = [(node.target, node.value)]
    else:
        return []
    found: list[tuple[ast.expr, ast.expr]] = []
    for target, value in pairs:
        if (
            isinstance(target, ast.Tuple | ast.List)
            and isinstance(value, ast.Tuple | ast.List)
            and len(target.elts) == len(value.elts)
        ):
            found.extend(zip(target.elts, value.elts, strict=True))
        else:
            found.append((target, value))
    return found


def own_log(node: ast.AST, logs: frozenset[str], selves: frozenset[str]) -> str | None:
    """The log *node* is, when it names one of *logs* on the instance.

    ``<self>.<log>``, or ``getattr(<self>, "<log>")`` with the log's name
    written out — a literal name is a reference to the log wherever it
    appears.  *selves* are the names that hold the instance: the one the
    function's definition gives it and every local bound from it.
    """
    if isinstance(node, ast.Attribute) and is_instance(node.value, selves):
        return node.attr if node.attr in logs else None
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and is_instance(node.args[0], selves)
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
        and node.args[1].value in logs
    ):
        return node.args[1].value
    return None


def selves_in(tree: ast.AST, instance: str | None = "self") -> frozenset[str]:
    """*instance* and every local name *tree* binds to it, however many hops.

    *instance* is the name the function's own definition gives the object
    it runs on — ``self`` by convention, whatever it is spelled — and
    ``None`` for a function that runs on no instance.  A name bound from it
    by a plain, a chained or an annotated assignment or a walrus, as an
    element of a tuple or list unpacking, or through a boolean operation
    over it (``self or None``), and a name bound from one of those in turn.
    Each pass over the tree adds a name or ends the walk, so it is bounded
    by the names the tree binds.
    """
    selves: set[str] = set() if instance is None else {instance}
    while True:
        bound = {
            target.id
            for node in ast.walk(tree)
            for target, value in bindings(node)
            if isinstance(target, ast.Name)
            and target.id not in selves
            and is_instance(value, selves)
        }
        if not bound:
            return frozenset(selves)
        selves |= bound


def logs_read_in(
    function: Callable[..., object],
    logs: frozenset[str],
    *,
    instance: str | None = "self",
) -> set[str]:
    """Every one of *logs* *function*'s own body reads for anything but recording.

    Read off the source, with *instance* the name the function gives the
    object it runs on.  A load of ``self.<log>`` is allowed in two places
    only: as the receiver of an ``.append(...)`` or ``.extend(...)`` call,
    and as the whole value of a plain or annotated assignment statement
    to a local name, which then counts as the log, so each load of that
    name is held to the same rule.  A walrus binding a log is not that:
    its value is used where it stands, so ``(x := self.<log>)`` is a read
    of the log, and its target counts as the log after it.  A local bound
    from ``self`` in any of the forms :func:`selves_in` follows counts as
    ``self``, so ``port = self`` and then ``port.<log>`` is a load of the
    log too, and ``getattr(self, "<log>")`` counts as ``self.<log>``.  An
    augmented assignment to ``self.<log>`` — a counter's ``+= 1`` — is a
    store and records.  Every other load is a read of the log.  Bounded by
    the body's syntax tree.

    Outside its reach, held so by :func:`test_the_log_pass_classifies_each_planted_use`:
    a value handed across a function boundary — a module function or a
    nested function handed ``self`` — a name built at run time, a binding
    made only when the function runs (``setattr``, ``self.__dict__``) and
    a binding through a loop or a context-manager target.  This pass reads
    the source; the pin that covers any spelling is the run-time trap,
    :func:`test_no_read_log_is_read_inside_the_double_at_run_time`.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    return logs_read_in_tree(tree, logs, instance=instance)


def logs_read_in_tree(
    tree: ast.AST, logs: frozenset[str], *, instance: str | None
) -> set[str]:
    """:func:`logs_read_in` over a parsed *tree*."""
    selves = selves_in(tree, instance)
    aliases: dict[str, str] = {}
    recording: set[int] = set()
    for node in ast.walk(tree):
        for target, value in bindings(node):
            log = own_log(value, logs, selves)
            if log is not None and isinstance(target, ast.Name):
                aliases[target.id] = log
                # Only the whole value of an assignment STATEMENT is a
                # binding and nothing else.  A walrus is an expression
                # whose value flows on into whatever holds it, so the log
                # it names is read there, whatever its target is later
                # used for.
                if isinstance(node, ast.Assign | ast.AnnAssign):
                    recording.add(id(value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in RECORDERS
        ):
            recording.add(id(node.func.value))
    read: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in recording:
            continue
        if isinstance(node, ast.Attribute | ast.Call):
            if isinstance(node, ast.Attribute) and not isinstance(node.ctx, ast.Load):
                continue
            log = own_log(node, logs, selves)
            if log is not None:
                read.add(log)
        elif (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in aliases
        ):
            read.add(aliases[node.id])
    return read


def instance_name(member: object, function: Callable[..., object]) -> str | None:
    """The name *function* gives the object it runs on, or ``None`` for none.

    A static method and a class method run on no instance.  Every other
    function on a class line — a method, a property's accessor — runs on
    the object its first positional parameter names, whatever the spelling.
    """
    if isinstance(member, staticmethod | classmethod):
        return None
    parameters = list(inspect.signature(function).parameters.values())
    positional = (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )
    if not parameters or parameters[0].kind not in positional:
        return None
    return parameters[0].name


def class_line_functions(
    double: type[FakeTrackerPort],
) -> list[tuple[str, Callable[..., object], str | None]]:
    """Every function on *double*'s class line, with the name of its instance.

    Methods, static and class methods through the function they wrap, and
    each accessor of a property, ``object`` left out; each with the name
    its definition gives the object it runs on, by :func:`instance_name`.
    Bounded by the classes on the line.
    """
    found: list[tuple[str, Callable[..., object], str | None]] = []
    for cls in double.__mro__:
        if cls is object:
            continue
        for name, member in vars(cls).items():
            accessors = (
                [member.fget, member.fset, member.fdel]
                if isinstance(member, property)
                else [getattr(member, "__func__", member)]
            )
            found.extend(
                (
                    f"{cls.__qualname__}.{name}",
                    accessor,
                    instance_name(member, accessor),
                )
                for accessor in accessors
                if inspect.isfunction(accessor)
            )
    return found


def test_a_read_log_decides_no_answer() -> None:
    """Read in the code: a read log is history, and no method reads it.

    For every double the census runs on, every function on its class line
    is read, with the instance under the name its own definition gives it,
    and none may read a log for anything but the append or extend that
    records it.  A read that answers differently once its key is in the
    log — which moves the board's answers while the state the census
    compares stands still — fails here, naming the method and the log.
    """
    doubles = census_doubles()
    assert CountingTracker in doubles
    for double in doubles:
        functions = class_line_functions(double)
        assert functions != [], double
        assert {instance for _, _, instance in functions} == {"self"}, double
        for name, function, instance in functions:
            read = logs_read_in(function, double.READ_LOGS, instance=instance)
            assert read == set(), (double, name)


class _LogUses:
    """Planted uses of a read log, for the pass above to classify."""

    def recorded(self, key: str) -> None:
        self.issue_reads.append(key)
        log = self.issue_reads
        log.extend([key])
        self.spec_reads += 1

    def decided(self, key: str) -> bool:
        self.issue_reads.append(key)
        return key in self.issue_reads[:-1]

    def decided_through_a_local(self, key: str) -> int:
        log = self.issue_reads
        log.append(key)
        return len(log)

    def decided_through_an_alias_of_self(self, key: str) -> int:
        port = self
        me = port
        port.issue_reads.append(key)
        return len(me.issue_reads)

    def decided_through_an_annotated_alias(self, key: str) -> int:
        port: _LogUses = self
        return len(port.issue_reads)

    def decided_through_a_walrus(self, key: str) -> int:
        return len((port := self).issue_reads) + len(port.issue_reads)

    def decided_through_a_walrus_of_a_log(self, key: str) -> bool:
        if x := self.issue_reads:
            return key in x
        return False

    def decided_through_unpacking(self, key: str) -> int:
        port, _ = self, None
        return len(port.issue_reads)

    def decided_through_a_chain(self, key: str) -> int:
        port = me = self
        return len(me.issue_reads) + len(port.issue_reads)

    def decided_through_a_boolean(self, key: str) -> int:
        port = self or None
        return len(port.issue_reads)

    def decided_through_a_helper(self, key: str) -> int:
        return _reads_for(self)

    def decided_through_a_nested_function(self, key: str) -> int:
        def count(port: _LogUses) -> int:
            return len(port.issue_reads)

        return count(self)

    def decided_through_a_run_time_name(self, key: str) -> int:
        return len(getattr(self, "issue_" + key))

    def decided_through_a_loop_target(self, key: str) -> int:
        for port in (self,):
            return len(port.issue_reads)
        return 0


def _reads_for(port: _LogUses) -> int:
    """A module function handed the instance: across the boundary, unseen."""
    return len(port.issue_reads)


def decided_on_another_spelling(port: _LogUses, key: str) -> int:
    """A function whose instance is not spelled ``self``: read off its definition."""
    return len(port.issue_reads)


#: The forms a linter refuses to let a method spell, planted as source: a
#: ``getattr`` with the log's name written out, read and recorded.
GETATTR_USES = textwrap.dedent(
    """
    def decided_through_getattr(self, key):
        return len(getattr(self, "issue_reads"))

    def recorded_through_getattr(self, key):
        getattr(self, "issue_reads").append(key)

    def decided_through_getattr_on_an_alias(self, key):
        port, _ = self, None
        return len(getattr(port, "issue_reads"))
    """
)


def test_the_log_pass_classifies_each_planted_use() -> None:
    """The control for the pass: each planted use is classified as it is.

    Every binding form the pass follows has a use here it flags, the
    instance's name is read off the definition, and each shape of the
    stated limit — a helper or a nested function handed the instance, a
    name built at run time, a loop target — has a use here it does not
    see, so the limit is a fact this test holds.
    """
    logs = frozenset({"issue_reads", "spec_reads"})
    flagged = {"issue_reads"}
    assert logs_read_in(_LogUses.recorded, logs) == set()
    assert logs_read_in(_LogUses.decided, logs) == flagged
    assert logs_read_in(_LogUses.decided_through_a_local, logs) == flagged
    assert logs_read_in(_LogUses.decided_through_an_alias_of_self, logs) == flagged
    assert logs_read_in(_LogUses.decided_through_an_annotated_alias, logs) == flagged
    assert logs_read_in(_LogUses.decided_through_a_walrus, logs) == flagged
    assert logs_read_in(_LogUses.decided_through_a_walrus_of_a_log, logs) == flagged
    assert logs_read_in(_LogUses.decided_through_unpacking, logs) == flagged
    assert logs_read_in(_LogUses.decided_through_a_chain, logs) == flagged
    assert logs_read_in(_LogUses.decided_through_a_boolean, logs) == flagged
    spelling = instance_name(decided_on_another_spelling, decided_on_another_spelling)
    assert spelling == "port"
    assert logs_read_in(decided_on_another_spelling, logs, instance=spelling) == (
        flagged
    )
    assert logs_read_in(decided_on_another_spelling, logs) == set()
    planted = {
        node.name: node
        for node in ast.parse(GETATTR_USES).body
        if isinstance(node, ast.FunctionDef)
    }
    assert set(planted) == {
        "decided_through_getattr",
        "recorded_through_getattr",
        "decided_through_getattr_on_an_alias",
    }
    assert (
        logs_read_in_tree(planted["decided_through_getattr"], logs, instance="self")
        == flagged
    )
    assert (
        logs_read_in_tree(planted["recorded_through_getattr"], logs, instance="self")
        == set()
    )
    assert (
        logs_read_in_tree(
            planted["decided_through_getattr_on_an_alias"], logs, instance="self"
        )
        == flagged
    )
    # The stated limit: each of these decides on the log and is not seen.
    assert logs_read_in(_LogUses.decided_through_a_helper, logs) == set()
    assert logs_read_in(_LogUses.decided_through_a_nested_function, logs) == set()
    assert logs_read_in(_LogUses.decided_through_a_run_time_name, logs) == set()
    assert logs_read_in(_LogUses.decided_through_a_loop_target, logs) == set()


#: One read of a trapped log at run time: the operation's name and the code
#: of every frame on the stack when it ran, innermost first.
Read = tuple[str, tuple[CodeType, ...]]


def stack_here() -> tuple[CodeType, ...]:
    """The code of every frame on the stack at the call, innermost first.

    Bounded by the stack's depth.
    """
    codes: list[CodeType] = []
    frame: FrameType | None = sys._getframe(1)
    while frame is not None:
        codes.append(frame.f_code)
        frame = frame.f_back
    return tuple(codes)


class TrappedLog(list[object]):
    """A read log that records every read of itself, with the stack that made it.

    A list that stands in for a double's read log.  Iteration, ``len``,
    ``bool``, indexing, ``in``, comparison, reversal, ``index``, ``count``,
    ``copy``, concatenation, repetition and ``repr`` each record the
    operation and the stack it ran under — whatever spelling fetched the
    object, because the attribute, ``vars(self)[...]``,
    ``self.__dict__[...]``, ``attrgetter`` and ``cast`` all hand back this
    same object.  Appending and extending are the recorders and are not
    reads.  The one limit: a call on the base type that skips the
    override, ``list.__len__(log)``, is not seen, and
    :func:`test_the_trap_catches_every_spelling_and_holds_its_limit`
    holds it.
    """

    reads: list[Read]

    def __init__(self, held: Iterable[object] = ()) -> None:
        super().__init__(held)
        self.reads = []

    def _read(self, operation: str) -> None:
        self.reads.append((operation, stack_here()))

    def __iter__(self) -> Iterator[object]:
        self._read("iteration")
        return super().__iter__()

    def __len__(self) -> int:
        self._read("len")
        return super().__len__()

    def __bool__(self) -> bool:
        self._read("bool")
        return super().__len__() > 0

    def __getitem__(self, index: object) -> object:
        self._read("indexing")
        return super().__getitem__(index)

    def __contains__(self, item: object) -> bool:
        self._read("in")
        return super().__contains__(item)

    def __eq__(self, other: object) -> bool:
        self._read("comparison")
        return super().__eq__(other)

    def __ne__(self, other: object) -> bool:
        self._read("comparison")
        return super().__ne__(other)

    def __lt__(self, other: list[object]) -> bool:
        self._read("comparison")
        return super().__lt__(other)

    def __le__(self, other: list[object]) -> bool:
        self._read("comparison")
        return super().__le__(other)

    def __gt__(self, other: list[object]) -> bool:
        self._read("comparison")
        return super().__gt__(other)

    def __ge__(self, other: list[object]) -> bool:
        self._read("comparison")
        return super().__ge__(other)

    def __reversed__(self) -> Iterator[object]:
        self._read("reversal")
        return super().__reversed__()

    def index(self, value: object, *bounds: int) -> int:
        self._read("index")
        return super().index(value, *bounds)

    def count(self, value: object) -> int:
        self._read("count")
        return super().count(value)

    def copy(self) -> list[object]:
        self._read("copy")
        return super().copy()

    def __add__(self, other: list[object]) -> list[object]:
        self._read("concatenation")
        return super().__add__(other)

    def __mul__(self, times: int) -> list[object]:
        self._read("repetition")
        return super().__mul__(times)

    def __rmul__(self, times: int) -> list[object]:
        self._read("repetition")
        return super().__rmul__(times)

    def __repr__(self) -> str:
        self._read("repr")
        return super().__repr__()


class TrappedCount(int):
    """A read counter that records every read of itself, and stays trapped when stepped.

    An integer that stands in for a double's read counter.  ``bool``,
    comparison, ``int``, ``hash``, ``repr`` and formatting each record the
    operation and the stack.  The counter's own step, ``+= 1``, is the
    recorder: it answers a new count sharing this one's record and records
    nothing itself.  Indexing with a counter goes to the base type directly
    (the interpreter never asks an integer subclass for ``__index__``), which
    is the base-type limit the control below holds.
    """

    reads: list[Read]

    def __new__(cls, value: int = 0, reads: list[Read] | None = None) -> "TrappedCount":
        count = super().__new__(cls, value)
        count.reads = [] if reads is None else reads
        return count

    def _read(self, operation: str) -> None:
        self.reads.append((operation, stack_here()))

    def __bool__(self) -> bool:
        self._read("bool")
        return super().__index__() != 0

    def __eq__(self, other: object) -> bool:
        self._read("comparison")
        return super().__eq__(other)

    def __ne__(self, other: object) -> bool:
        self._read("comparison")
        return super().__ne__(other)

    def __lt__(self, other: int) -> bool:
        self._read("comparison")
        return super().__lt__(other)

    def __le__(self, other: int) -> bool:
        self._read("comparison")
        return super().__le__(other)

    def __gt__(self, other: int) -> bool:
        self._read("comparison")
        return super().__gt__(other)

    def __ge__(self, other: int) -> bool:
        self._read("comparison")
        return super().__ge__(other)

    def __int__(self) -> int:
        self._read("int")
        return super().__index__()

    def __hash__(self) -> int:
        self._read("hash")
        return super().__hash__()

    def __repr__(self) -> str:
        self._read("repr")
        return super().__repr__()

    def __format__(self, spec: str) -> str:
        self._read("format")
        return super().__format__(spec)

    def __add__(self, other: int) -> "TrappedCount":
        return TrappedCount(super().__index__() + other, self.reads)

    def __radd__(self, other: int) -> "TrappedCount":
        return TrappedCount(other + super().__index__(), self.reads)

    def __sub__(self, other: int) -> "TrappedCount":
        return TrappedCount(super().__index__() - other, self.reads)


Trap = TrappedLog | TrappedCount


def trapped(port: FakeTrackerPort) -> dict[str, Trap]:
    """Every read log of *port* replaced by its trap, holding what it held.

    A list log becomes a :class:`TrappedLog`, a counter a
    :class:`TrappedCount`; any other shape of log reds here.  Keyed by the
    log's name; the trap is what the double's attribute now is, so every
    spelling that fetches the attribute fetches the trap.
    """
    traps: dict[str, Trap] = {}
    for name in sorted(type(port).READ_LOGS):
        held = getattr(port, name)
        trap: Trap
        if isinstance(held, list):
            trap = TrappedLog(held)
        else:
            assert isinstance(held, int) and not isinstance(held, bool), (name, held)
            trap = TrappedCount(held)
        setattr(port, name, trap)
        traps[name] = trap
    return traps


def read_inside(reads: Sequence[Read], line: frozenset[CodeType]) -> list[str]:
    """Each read of *reads* made with a function of the class *line* on its stack.

    Named by the operation and the innermost such function, so a read made
    through a lambda, a nested function or a helper the method called is
    still the method's.  Bounded by the reads and each one's stack.
    """
    return [
        f"{operation} in {next(code.co_qualname for code in stack if code in line)}"
        for operation, stack in reads
        if any(code in line for code in stack)
    ]


def class_line_codes(double: type[FakeTrackerPort]) -> frozenset[CodeType]:
    """The code of every function on *double*'s class line."""
    return frozenset(
        function.__code__ for _, function, _ in class_line_functions(double)
    )


@pytest.mark.parametrize("double", census_doubles(), ids=lambda double: double.__name__)
@pytest.mark.parametrize("case", sorted(CASES))
async def test_no_read_log_is_read_inside_the_double_at_run_time(
    case: str, double: type[FakeTrackerPort]
) -> None:
    """Run: no method of the double reads a read log, under any spelling.

    The static pass above reads the source, and every round has found one
    more spelling it did not follow.  This is the pin no spelling gets
    round: on the board each census case runs on, its setup done, every
    read log the double declares is replaced by a trap that records each
    read of itself with the stack that made it, and the case is run.  No
    recorded read may have a function of the double's class line on its
    stack — a read through ``vars(self)[...]``, ``self.__dict__[...]``,
    ``attrgetter``, ``cast``, a conditional expression, nested unpacking,
    a lambda default, a helper or a nested function all fetch the trap and
    all run under the method's frame.  The recorders — append, extend and
    a counter's step — are not reads.  The one limit is a call on the
    base type that skips the override, held by the control below.
    """
    row = CASES[case]
    port = await case_board(case, double)
    traps = trapped(port)
    assert set(traps) == double.READ_LOGS
    line = class_line_codes(double)

    await row.call(port)

    assert {
        log: found
        for log, trap in traps.items()
        if (found := read_inside(trap.reads, line))
    } == {}


class _TrappedUses:
    """An instance holding one trapped log, read through every spelling."""

    def __init__(self) -> None:
        self.issue_reads = TrappedLog(["KOD-1"])

    def recorded(self) -> None:
        self.issue_reads.append("KOD-2")
        self.issue_reads.extend(["KOD-3"])

    def through_the_attribute(self) -> int:
        return len(self.issue_reads)

    def through_vars(self) -> object:
        return vars(self)["issue_reads"][:-1]

    def through_the_dict(self) -> object:
        return self.__dict__["issue_reads"][:-1]

    def through_attrgetter(self) -> object:
        return attrgetter("issue_reads")(self)[:-1]

    def through_cast(self) -> int:
        return len(cast("_TrappedUses", self).issue_reads)

    def through_a_conditional(self, signals: tuple[str, ...] = ()) -> bool:
        port = self if signals == () else _TrappedUses()
        return bool(port.issue_reads)

    def through_nested_unpacking(self) -> bool:
        (port, _), _ = (self, None), None
        return "KOD-1" in port.issue_reads

    def through_a_lambda_default(self) -> object:
        return (lambda port=self: port.issue_reads[:-1])()

    def through_a_helper(self) -> int:
        return _trapped_reads_of(self)

    def through_a_nested_function(self) -> int:
        def count(port: _TrappedUses) -> int:
            return len(port.issue_reads)

        return count(self)

    def through_a_run_time_name(self) -> int:
        return len(getattr(self, "issue_" + "reads"))

    def through_a_walrus(self) -> int:
        if log := self.issue_reads:
            return len(log)
        return 0

    def through_the_base_type(self) -> int:
        return list.__len__(self.issue_reads)


def _trapped_reads_of(port: _TrappedUses) -> int:
    """A module function handed the instance: the trap catches it all the same."""
    return len(port.issue_reads)


#: An empty list, as a name, for the concatenation use below.
NOTHING: list[object] = []

#: Every operation the list trap records, each as a use of a log.
LOG_OPERATIONS: dict[str, Callable[[TrappedLog], object]] = {
    "iteration": lambda log: list(iter(log)),
    "len": len,
    "bool": bool,
    "indexing": lambda log: log[0],
    "in": lambda log: "KOD-1" in log,
    "comparison": lambda log: log == ["KOD-1"],
    "reversal": reversed,
    "index": lambda log: log.index("KOD-1"),
    "count": lambda log: log.count("KOD-1"),
    "copy": lambda log: log.copy(),
    "concatenation": lambda log: log + NOTHING,
    "repetition": lambda log: log * 2,
    "repr": repr,
}

#: Every operation the counter trap records, each as a use of a counter.
COUNT_OPERATIONS: dict[str, Callable[[TrappedCount], object]] = {
    "bool": bool,
    "comparison": lambda count: count > 0,
    "int": int,
    "hash": hash,
    "repr": repr,
    "format": lambda count: f"{count:d}",
}


def test_the_trap_catches_every_spelling_and_holds_its_limit() -> None:
    """The control for the trap: each spelling is caught, and the one limit is not.

    Every spelling fetches the trapped object itself — the attribute,
    ``vars``, ``__dict__``, ``attrgetter`` and ``cast`` answer one and the
    same object — so each is caught when it reads.  Each read operation
    of a log and of a counter records exactly its own name, the recorders
    record nothing, and a stepped counter is still the trap.  A method
    reading through each planted spelling — the seven shapes the static
    pass does not follow among them — has its own code on the stack of a
    recorded read; the recorder does not; and the stated limit, a call on
    the base type that skips the override, does not either.
    """
    uses = _TrappedUses()
    log = uses.issue_reads
    assert vars(uses)["issue_reads"] is log
    assert uses.__dict__["issue_reads"] is log
    assert attrgetter("issue_reads")(uses) is log
    assert cast("_TrappedUses", uses).issue_reads is log

    for operation, use in LOG_OPERATIONS.items():
        fresh = TrappedLog(["KOD-1"])
        use(fresh)
        assert [name for name, _ in fresh.reads] == [operation], operation
    recorder = TrappedLog(["KOD-1"])
    recorder.append("KOD-2")
    recorder.extend(["KOD-3"])
    assert recorder.reads == []
    assert list.__len__(recorder) == 3

    for operation, count_use in COUNT_OPERATIONS.items():
        fresh_count = TrappedCount(1)
        count_use(fresh_count)
        assert [name for name, _ in fresh_count.reads] == [operation], operation
    stepped = TrappedCount(1)
    stepped += 1
    assert isinstance(stepped, TrappedCount)
    assert int.__index__(stepped) == 2
    assert stepped.reads == []

    methods = {
        name: member
        for name, member in vars(_TrappedUses).items()
        if inspect.isfunction(member) and name != "__init__"
    }
    unseen = {"recorded", "through_the_base_type"}
    assert unseen < set(methods)
    assert {
        "through_vars",
        "through_the_dict",
        "through_attrgetter",
        "through_cast",
        "through_a_conditional",
        "through_nested_unpacking",
        "through_a_lambda_default",
    } < set(methods)
    caught = {}
    for name, method in methods.items():
        uses = _TrappedUses()
        method(uses)
        caught[name] = read_inside(uses.issue_reads.reads, frozenset({method.__code__}))
    assert {name for name, found in caught.items() if found} == set(methods) - unseen


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


async def test_a_board_moved_by_hand_is_not_a_write_and_hides_none() -> None:
    """A fixture's own move of the board is carried; a write beside it shows.

    ``moved_by_hand`` moves the live double and the copy the claim was handed
    alike, so the claim still compares the whole state: the fixture's move
    alone answers True, a write made after it answers False, and the same
    move made by hand without it is a moved board.
    """
    port = board()
    unwritten = nothing_written(port)

    moved_by_hand(port, lambda live: live.issues.pop(OTHER))

    assert unwritten() is True
    await write_comment(port)
    assert unwritten() is False

    undeclared = board()
    unwritten = nothing_written(undeclared)
    undeclared.issues.pop(OTHER)
    assert unwritten() is False
