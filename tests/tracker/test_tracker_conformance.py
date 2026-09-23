"""Port-level conformance suite — written once, run against every adapter.

Passing this module IS the definition of conforming.  Nothing here names a
vendor, a tool, or a vendor identifier format: an adapter that needed a
special case in this file would not be substitutable, which is the failure
this suite exists to catch.

Every case runs over the in-process fake MCP server.  There is no live
workspace anywhere in this module and none may be introduced.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from inspect import isawaitable, signature

import pytest

from kodezart.core.errors import TrackerEnsureConflictError
from kodezart.core.protocols import TrackerPort
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.errors import (
    ApprovalLabelWriteError,
    DuplicateWorkRefError,
    PrincipalAuthoredSurfaceError,
    StaleWriteError,
    SurfaceLeaseError,
)
from kodezart.domain.organize_graph import graph_snapshot
from kodezart.domain.run_alarm_record import run_alarm_marker, run_alarm_surface
from kodezart.domain.run_event_stream import LaneRunEvent
from kodezart.types.domain.branch import BaseInput, BaseSpec, WorkRef, WorkRefRole
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.operation import LifecycleStage, QueueState, ScopeLabel
from kodezart.types.domain.organize_graph import GraphProposal
from kodezart.types.domain.run_alarm import (
    AlarmBound,
    AlarmReading,
    AlarmSignal,
    CountEvidence,
    RunAlarm,
    SurfaceSubject,
)
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import (
    BODY_AUTHORSHIP_SURFACES,
    DescriptionWriteAuthority,
    SurfaceAuthorship,
    SurfaceKind,
    SurfaceLease,
    WritableSurface,
)
from kodezart.types.domain.tracker import (
    INSTATABLE_MAPPING_KINDS,
    ClaimStatus,
    EnsureAction,
    IssuePriority,
    IssueQuery,
    IssueRelationKind,
    MappingKind,
    MappingRef,
    ReviewQuery,
    WorkflowStateKind,
    is_open,
)
from kodezart.types.domain.tracker_writes import DescriptionEditResult
from tests.chains.test_write_back_adoption import artifact_writes, parameters
from tests.fakes import FakeLinearMcpServer, FakeMcpComment, FakeMcpIssue
from tests.tracker.conftest import (
    APPROVED_ISSUE,
    APPROVER,
    ASSET_ISSUE,
    BYSTANDER,
    CLAIMED_ISSUE,
    DOCUMENT_CONTENT,
    DOCUMENT_KEY,
    FIXTURE_NOW,
    FIXTURE_REPO_URL,
    FIXTURE_REVIEW,
    FOREIGN_ISSUE,
    FOREIGN_REVIEW,
    ISSUE_LABELS,
    QUEUE_STATE_LABELS,
    SCOPE_DIAGNOSIS,
    TEAM_IDENTIFIERS,
    TRACKER_IMPLEMENTATIONS,
    FixtureClock,
    TrackerWorkspace,
    fixture_server,
    observed_writes,
)
from tests.tracker.lease_fixtures import leased_comment
from tests.tracker.marker_config import MARKER_PREFIXES

LEASE_SECONDS = 600.0
TEAM = TEAM_IDENTIFIERS["engineering"]
#: The signal the refusing cases put out of the credential's reach.
REFUSED = [PassSignal.reviews_changed]

#: The lease fixture's surfaces: one issue carrying a description and two
#: independently addressed marker comments, plus a second issue's
#: description, so a disjoint set exists to acquire alongside.
CLAIMED_REF = ScopeRef(kind=ScopeKind.ISSUE, key=CLAIMED_ISSUE)
APPROVED_REF = ScopeRef(kind=ScopeKind.ISSUE, key=APPROVED_ISSUE)
CLAIMED_DESCRIPTION = WritableSurface(
    kind=SurfaceKind.ISSUE_DESCRIPTION,
    ref=CLAIMED_REF,
)
MARKER_A = WritableSurface(
    kind=SurfaceKind.MARKER_COMMENT,
    ref=CLAIMED_REF,
    marker="A",
)
MARKER_B = WritableSurface(
    kind=SurfaceKind.MARKER_COMMENT,
    ref=CLAIMED_REF,
    marker="B",
)
APPROVED_DESCRIPTION = WritableSurface(
    kind=SurfaceKind.ISSUE_DESCRIPTION,
    ref=APPROVED_REF,
)

#: The surface the principal-authored fixture body lives on, and one the
#: authorship question is not asked of.
PRINCIPAL_REF = ScopeRef(kind=ScopeKind.ISSUE, key=ASSET_ISSUE)
PRINCIPAL_DESCRIPTION = WritableSurface(
    kind=SurfaceKind.ISSUE_DESCRIPTION,
    ref=PRINCIPAL_REF,
)
CLAIMED_LABEL_SET = WritableSurface(
    kind=SurfaceKind.ISSUE_LABEL_SET,
    ref=CLAIMED_REF,
)


@pytest.fixture(params=sorted(TRACKER_IMPLEMENTATIONS))
async def aliasing_tracker(
    request: pytest.FixtureRequest,
    server: FakeLinearMcpServer,
    clock: FixtureClock,
) -> TrackerPort:
    """Every implementation, over a workspace that spells one label twice.

    The operation's criterion classification and the admission
    vocabulary's approved member resolve to the SAME tracker label here.
    That is the only shape in which an ordinary classification write can
    name the approver's own member at all: a workspace where the two
    namespaces spell different labels cannot express the write this
    refusal exists for, so a case stated over it would pass without
    exercising anything.
    """
    factory = TRACKER_IMPLEMENTATIONS[request.param]
    port = factory(
        TrackerWorkspace(
            server=server,
            clock=clock,
            scope_labels={ScopeLabel.APPROVED.value: ISSUE_LABELS["criterion"]},
        ),
    )
    return await port if isawaitable(port) else port


@pytest.fixture
def aliasing_writes(
    aliasing_tracker: TrackerPort, server: FakeLinearMcpServer
) -> Callable[[], tuple[object, ...]]:
    """Mutations made against the aliased workspace, by either arm."""
    return observed_writes(aliasing_tracker, server)


async def queue_aliasing_port(
    implementation: str,
    server: FakeLinearMcpServer,
    clock: FixtureClock,
    *,
    member: str,
) -> TrackerPort:
    """One implementation, over a workspace whose queue *member* spells approval.

    The admission vocabulary's approved member and the named issue-queue
    member resolve to the SAME tracker label, which is the only shape in which
    a queue-state write can name the approver's member at all. The admission
    mapping is what moves, because the queue mapping is the fixture's constant
    and a workspace that changed both would not say which of the two the rule
    is about.

    Stated as a function because *member* is what a case varies: whichever
    queue member a workspace dials the approval label onto is the member whose
    write is refused, and a case that could only ever dial ``approved`` could
    not tell a port refusing by configured label from one refusing by the
    enum member's own name.
    """
    factory = TRACKER_IMPLEMENTATIONS[implementation]
    port = factory(
        TrackerWorkspace(
            server=server,
            clock=clock,
            scope_labels={ScopeLabel.APPROVED.value: QUEUE_STATE_LABELS[member]},
        ),
    )
    return await port if isawaitable(port) else port


@pytest.fixture(params=sorted(TRACKER_IMPLEMENTATIONS))
async def queue_aliasing_tracker(
    request: pytest.FixtureRequest,
    server: FakeLinearMcpServer,
    clock: FixtureClock,
) -> TrackerPort:
    """Every implementation, over a workspace whose queue spells approval."""
    return await queue_aliasing_port(request.param, server, clock, member="approved")


#: Holders are shaped as the identities the contract names: a lease is held
#: under the writing run's job id.
JOB_A = "job-a"
JOB_B = "job-b"
#: A third, so a case about surfaces of ONE issue held apart has a holder
#: per surface and never has to reuse one to make its point.
JOB_C = "job-c"
#: The two holder vocabularies, side by side: a deployment's process
#: identity holds a fire claim, a run's job id holds a write lease.
PROCESS_HOLDER = "kodezart-process"
JOB_HOLDER = "job-17"

#: The lane whose event stream the cases post to, and a second lane on the
#: same issue, so lane scoping is a boundary rather than a tautology.
EVENT_LANE = "lane-alpha"
OTHER_EVENT_LANE = "lane-beta"

DISPATCHED = LaneRunEvent(kind=RunEventKind.LANE_DISPATCHED, lane_key=EVENT_LANE)
PUSHED = LaneRunEvent(kind=RunEventKind.FIRST_PUSH, lane_key=EVENT_LANE)
REFUTED = LaneRunEvent(
    kind=RunEventKind.CRITERION_REFUTED,
    lane_key=EVENT_LANE,
    subject_key=ASSET_ISSUE,
)
GREEN = LaneRunEvent(kind=RunEventKind.GATE_GREEN, lane_key=EVENT_LANE)
NEIGHBOUR_EVENT = LaneRunEvent(kind=RunEventKind.FIRST_PUSH, lane_key=OTHER_EVENT_LANE)

#: The four events, in the order the posting calls land.
POSTED_EVENTS = (DISPATCHED, PUSHED, REFUTED, GREEN)

#: What the backend stamps each of those four writes with, in that same
#: order.  They do NOT rise with the log: a backend settles the order of
#: writes reaching it and its stamp is that settlement, so the log order
#: and the write order are two different things and a fixture where they
#: agreed would leave the stream's ordering untested.  Ascending, the
#: stamps put the events back in the order asserted on: PUSHED, GREEN,
#: REFUTED, DISPATCHED — which is neither the log's order nor its reverse.
EVENT_INSTANTS = (
    FIXTURE_NOW + timedelta(seconds=40),
    FIXTURE_NOW + timedelta(seconds=10),
    FIXTURE_NOW + timedelta(seconds=30),
    FIXTURE_NOW + timedelta(seconds=20),
)

#: A record's marker on the same log — a surface written once and then
#: rewritten, which is what the stream must not mistake for an event.
RECORD_MARKER = compose_comment_marker(
    prefixes=MARKER_PREFIXES, purpose="decision", lane=EVENT_LANE
)


class TestScanAndRead:
    """Scanning by queue state and reading a full issue."""

    async def test_scan_by_queue_state_returns_only_that_state(
        self,
        tracker: TrackerPort,
    ) -> None:
        found = await tracker.scan_issues(
            query=IssueQuery(queue_state=QueueState.APPROVED, page_size=10),
        )
        assert {issue.issue_key for issue in found} == {
            CLAIMED_ISSUE,
            APPROVED_ISSUE,
            FOREIGN_ISSUE,
        }
        for issue in found:
            assert QueueState.APPROVED in issue.queue_states

    async def test_a_scan_scoped_to_a_team_returns_only_that_team(
        self,
        tracker: TrackerPort,
    ) -> None:
        found = await tracker.scan_issues(
            query=IssueQuery(
                queue_state=QueueState.APPROVED,
                team_key="engineering",
                page_size=10,
            ),
        )
        assert {issue.issue_key for issue in found} == {
            CLAIMED_ISSUE,
            APPROVED_ISSUE,
        }

    async def test_an_issue_carries_the_configured_key_of_its_team(
        self,
        tracker: TrackerPort,
    ) -> None:
        issue = await tracker.read_issue(issue_key=APPROVED_ISSUE)
        assert issue.team_key == "engineering"

    async def test_an_issue_on_an_undeclared_team_carries_no_key(
        self,
        tracker: TrackerPort,
    ) -> None:
        issue = await tracker.read_issue(issue_key=FOREIGN_ISSUE)
        assert issue.team_key is None

    async def test_scan_page_size_bounds_the_result(
        self,
        tracker: TrackerPort,
    ) -> None:
        found = await tracker.scan_issues(query=IssueQuery(page_size=1))
        assert len(found) == 1

    async def test_read_issue_carries_the_whole_domain_shape(
        self,
        tracker: TrackerPort,
    ) -> None:
        issue = await tracker.read_issue(issue_key=APPROVED_ISSUE)
        assert issue.issue_key == APPROVED_ISSUE
        assert issue.title == "approved with a blocker"
        assert issue.body == "body"
        assert issue.priority is IssuePriority.URGENT
        assert issue.state_kind is WorkflowStateKind.BACKLOG
        assert issue.state_name == "Backlog"
        assert issue.queue_states == frozenset({QueueState.APPROVED})
        assert issue.parent_key == "FIX-0"
        assert issue.assignee_key == BYSTANDER
        assert issue.url

    async def test_relations_are_domain_kinds(self, tracker: TrackerPort) -> None:
        issue = await tracker.read_issue(issue_key=APPROVED_ISSUE)
        assert (
            IssueRelationKind.BLOCKED_BY,
            CLAIMED_ISSUE,
        ) in {(relation.kind, relation.issue_key) for relation in issue.relations}

    async def test_unmapped_marks_are_not_queue_states(
        self,
        tracker: TrackerPort,
    ) -> None:
        """A mark the configuration does not name is not a semantic state."""
        issue = await tracker.read_issue(issue_key=CLAIMED_ISSUE)
        assert issue.queue_states == frozenset({QueueState.APPROVED})

    async def test_state_kind_partitions_open_from_closed(
        self,
        tracker: TrackerPort,
    ) -> None:
        assert is_open((await tracker.read_issue(issue_key=CLAIMED_ISSUE)).state_kind)
        assert not is_open(
            (await tracker.read_issue(issue_key=ASSET_ISSUE)).state_kind,
        )


class TestPriorityMapping:
    """Priority crosses the port as an ordered domain enum, never a number."""

    async def test_every_fixture_priority_maps_to_its_domain_member(
        self,
        tracker: TrackerPort,
    ) -> None:
        by_key = {
            issue.issue_key: issue.priority
            for issue in await tracker.scan_issues(query=IssueQuery(page_size=10))
        }
        assert by_key[APPROVED_ISSUE] is IssuePriority.URGENT
        assert by_key[CLAIMED_ISSUE] is IssuePriority.HIGH
        assert by_key[ASSET_ISSUE] is IssuePriority.NONE


class TestWrites:
    """Create, update, and the two state writes."""

    async def test_create_issue_round_trips(self, tracker: TrackerPort) -> None:
        created = await tracker.create_issue(
            title="created by the port",
            body="body",
            team_key="engineering",
            priority=IssuePriority.LOW,
        )
        assert created.title == "created by the port"
        assert created.priority is IssuePriority.LOW

    async def test_update_issue_leaves_omitted_fields_untouched(
        self,
        tracker: TrackerPort,
    ) -> None:
        before = await tracker.read_issue(issue_key=APPROVED_ISSUE)
        after = await tracker.update_issue(
            issue_key=APPROVED_ISSUE,
            body="rewritten",
        )
        assert after.body == "rewritten"
        assert after.title == before.title

    async def test_set_workflow_state_resolves_the_stage(
        self,
        tracker: TrackerPort,
    ) -> None:
        updated = await tracker.set_workflow_state(
            issue_key=APPROVED_ISSUE,
            stage=LifecycleStage.IN_PROGRESS,
        )
        assert updated.state_kind is WorkflowStateKind.STARTED

    async def test_restore_workflow_state_puts_back_what_a_reader_read(
        self,
        tracker: TrackerPort,
    ) -> None:
        """The undo the failure arm needs, over a state no stage names."""
        before = await tracker.read_issue(issue_key=APPROVED_ISSUE)
        await tracker.set_workflow_state(
            issue_key=APPROVED_ISSUE,
            stage=LifecycleStage.IN_PROGRESS,
        )

        restored = await tracker.restore_workflow_state(
            issue_key=APPROVED_ISSUE,
            state_name=before.state_name,
        )

        assert restored.state_name == before.state_name
        assert restored.state_kind is before.state_kind

    async def test_set_queue_state_replaces_the_previous_member(
        self,
        tracker: TrackerPort,
    ) -> None:
        updated = await tracker.set_queue_state(
            issue_key=APPROVED_ISSUE,
            state=QueueState.DONE,
        )
        assert updated.queue_states == frozenset({QueueState.DONE})

    async def test_set_queue_state_preserves_unrelated_marks(
        self,
        tracker: TrackerPort,
    ) -> None:
        """Only queue-state marks are the port's to rewrite."""
        await tracker.set_queue_state(
            issue_key=CLAIMED_ISSUE,
            state=QueueState.DONE,
        )
        again = await tracker.read_issue(issue_key=CLAIMED_ISSUE)
        assert again.queue_states == frozenset({QueueState.DONE})


class TestComments:
    """Comments round-trip through the port."""

    async def test_post_then_list(self, tracker: TrackerPort) -> None:
        posted = await tracker.post_comment(
            issue_key=APPROVED_ISSUE,
            body="a terminal outcome",
        )
        listed = await tracker.list_comments(issue_key=APPROVED_ISSUE)
        assert posted.body == "a terminal outcome"
        assert posted.issue_key == APPROVED_ISSUE
        assert [comment.comment_key for comment in listed] == [posted.comment_key]

    async def test_comments_are_scoped_to_their_issue(
        self,
        tracker: TrackerPort,
    ) -> None:
        await tracker.post_comment(issue_key=APPROVED_ISSUE, body="one")
        assert await tracker.list_comments(issue_key=ASSET_ISSUE) == ()


class TestAnUnattributedComment:
    """A comment the backend attributes to nobody, at the PORT (KOD-280).

    Measured 2026-09-01 17:52Z: a listing carried a comment whose author
    the vendor reported as null — a removed user or an integration — and
    the dispatch tick that read it died.  The wire model and the adapter
    were taught the state; this pins it where every adapter has to answer
    for it, because "no author reported" is a fact about the backend and
    not about one vendor's JSON.

    Over ``TRACKER_ADAPTERS`` rather than the doubles: the workspace is
    seeded through the fake MCP server, which is the adapters' input.
    """

    async def test_an_unattributed_comment_stays_unattributed(
        self,
        server: FakeLinearMcpServer,
        adapter: TrackerPort,
    ) -> None:
        server.comments.append(
            FakeMcpComment(
                id="comment-unattributed",
                issue_id=APPROVED_ISSUE,
                author=None,
                body="left behind by an account nobody can name",
                created_at=FIXTURE_NOW,
            ),
        )

        listed = await adapter.list_comments(issue_key=APPROVED_ISSUE)

        assert [(item.comment_key, item.author_key) for item in listed] == [
            ("comment-unattributed", None),
        ]

    async def test_an_attributed_comment_still_names_its_author(
        self,
        server: FakeLinearMcpServer,
        adapter: TrackerPort,
    ) -> None:
        """The paired positive: the state is one comment's, not the log's."""
        server.comments.append(
            FakeMcpComment(
                id="comment-attributed",
                issue_id=APPROVED_ISSUE,
                author=APPROVER,
                body="left by somebody the workspace still knows",
                created_at=FIXTURE_NOW,
            ),
        )

        listed = await adapter.list_comments(issue_key=APPROVED_ISSUE)

        assert [item.author_key for item in listed] == [APPROVER]


class TestAtomicClaim:
    """Exactly-once claim semantics — the concurrency contract."""

    async def test_claim_grants_then_reads_back(self, tracker: TrackerPort) -> None:
        granted = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
        )
        assert granted.status is ClaimStatus.GRANTED
        held = await tracker.active_claim(issue_key=CLAIMED_ISSUE)
        assert held is not None
        assert held.holder == "pass-a"

    async def test_unclaimed_issue_has_no_active_claim(
        self,
        tracker: TrackerPort,
    ) -> None:
        assert await tracker.active_claim(issue_key=CLAIMED_ISSUE) is None

    async def test_two_simultaneous_claimants_produce_exactly_one_winner(
        self,
        tracker: TrackerPort,
    ) -> None:
        """AC: two simultaneous claimants -> one wins, the other holds nothing.

        The loser is told which: ``LOST`` when the backend settled an
        owner, naming it, and ``CONTENDED`` when it settled nobody — the
        loser met the winner mid-race, before the winner's own read-back
        had confirmed it, and neither of them owned the issue at the
        instant the loser was weighed.  Both are refusals and neither is
        ownership; what may never happen is two grants.
        """
        first, second = await asyncio.gather(
            tracker.claim_issue(
                issue_key=CLAIMED_ISSUE,
                holder="pass-a",
                lease_seconds=LEASE_SECONDS,
            ),
            tracker.claim_issue(
                issue_key=CLAIMED_ISSUE,
                holder="pass-b",
                lease_seconds=LEASE_SECONDS,
            ),
        )
        statuses = [first.status, second.status]
        assert statuses.count(ClaimStatus.GRANTED) == 1
        granted = next(
            one for one in (first, second) if one.status is ClaimStatus.GRANTED
        )
        refused = next(
            one for one in (first, second) if one.status is not ClaimStatus.GRANTED
        )
        assert refused.status in {ClaimStatus.LOST, ClaimStatus.CONTENDED}
        assert refused.current_holder == (
            granted.holder if refused.status is ClaimStatus.LOST else None
        )
        held = await tracker.active_claim(issue_key=CLAIMED_ISSUE)
        assert held is not None
        assert held.holder == granted.holder

    async def test_one_holder_claiming_twice_at_once_ends_holding_the_issue(
        self,
        tracker: TrackerPort,
    ) -> None:
        """One identity cannot lose the issue to itself.

        The realistic restart: a redeployed process claims what its
        predecessor already holds, and both calls are in flight at once.
        A holder identity is what the arbitration is over, so a second
        grant to it is the same ownership observed twice rather than a
        conflict to break: both are granted, the issue reads as that
        holder's, and the holder can still renew — what may never happen
        is that a holder meeting itself ends up owning nothing.
        """
        outcomes = await asyncio.gather(
            tracker.claim_issue(
                issue_key=CLAIMED_ISSUE,
                holder="pass-a",
                lease_seconds=LEASE_SECONDS,
            ),
            tracker.claim_issue(
                issue_key=CLAIMED_ISSUE,
                holder="pass-a",
                lease_seconds=LEASE_SECONDS,
            ),
        )

        assert [one.status for one in outcomes] == [ClaimStatus.GRANTED] * 2
        held = await tracker.active_claim(issue_key=CLAIMED_ISSUE)
        assert held is not None
        assert held.holder == "pass-a"
        rival = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-b",
            lease_seconds=LEASE_SECONDS,
        )
        assert rival.status is ClaimStatus.LOST
        assert rival.current_holder == "pass-a"
        renewed = await tracker.renew_claim(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
        )
        assert renewed is not None
        assert renewed.status is ClaimStatus.GRANTED

    async def test_the_loser_observes_a_distinct_typed_result_not_an_exception(
        self,
        tracker: TrackerPort,
    ) -> None:
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
        )
        loser = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-b",
            lease_seconds=LEASE_SECONDS,
        )
        assert loser.status is ClaimStatus.LOST
        assert loser.holder == "pass-b"
        assert loser.current_holder == "pass-a"

    async def test_release_frees_the_issue_for_the_next_claimant(
        self,
        tracker: TrackerPort,
    ) -> None:
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
        )
        await tracker.release_claim(issue_key=CLAIMED_ISSUE, holder="pass-a")
        assert await tracker.active_claim(issue_key=CLAIMED_ISSUE) is None
        again = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-b",
            lease_seconds=LEASE_SECONDS,
        )
        assert again.status is ClaimStatus.GRANTED

    async def test_release_by_a_non_holder_is_a_no_op(
        self,
        tracker: TrackerPort,
    ) -> None:
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
        )
        await tracker.release_claim(issue_key=CLAIMED_ISSUE, holder="pass-b")
        held = await tracker.active_claim(issue_key=CLAIMED_ISSUE)
        assert held is not None
        assert held.holder == "pass-a"

    async def test_releasing_twice_is_the_same_as_releasing_once(
        self,
        tracker: TrackerPort,
    ) -> None:
        """Idempotent: the caller cannot know which arm already released.

        The dispatcher releases what it could not resolve a base for and
        the watch releases what its job finished with; a second release is
        an ordinary state and not an error.
        """
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
        )
        await tracker.release_claim(issue_key=CLAIMED_ISSUE, holder="pass-a")
        await tracker.release_claim(issue_key=CLAIMED_ISSUE, holder="pass-a")

        assert await tracker.active_claim(issue_key=CLAIMED_ISSUE) is None
        again = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-b",
            lease_seconds=LEASE_SECONDS,
        )
        assert again.status is ClaimStatus.GRANTED

    async def test_a_second_release_never_frees_the_next_holders_claim(
        self,
        tracker: TrackerPort,
    ) -> None:
        """Idempotence is not amnesia: a release frees THIS holder's claim."""
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
        )
        await tracker.release_claim(issue_key=CLAIMED_ISSUE, holder="pass-a")
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-b",
            lease_seconds=LEASE_SECONDS,
        )

        await tracker.release_claim(issue_key=CLAIMED_ISSUE, holder="pass-a")

        held = await tracker.active_claim(issue_key=CLAIMED_ISSUE)
        assert held is not None
        assert held.holder == "pass-b"

    async def test_a_losing_claimant_leaves_nothing_that_outlives_the_winner(
        self,
        tracker: TrackerPort,
    ) -> None:
        """A claim that was refused is not a claim, and holds nothing.

        The measured failure was in the winner's release: the loser's
        attempt had left something behind that went on excluding every
        later claimant, for the whole of a lease nobody was renewing
        (KOD-152).
        """
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
        )
        lost = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-b",
            lease_seconds=LEASE_SECONDS,
        )
        assert lost.status is ClaimStatus.LOST

        await tracker.release_claim(issue_key=CLAIMED_ISSUE, holder="pass-a")

        assert await tracker.active_claim(issue_key=CLAIMED_ISSUE) is None
        next_pass = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-c",
            lease_seconds=LEASE_SECONDS,
        )
        assert next_pass.status is ClaimStatus.GRANTED

    async def test_the_lease_bounds_the_claim(self, tracker: TrackerPort) -> None:
        granted = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
        )
        assert granted.expires_at == FIXTURE_NOW + timedelta(seconds=LEASE_SECONDS)


class TestRenewingAClaim:
    """Renewal extends a live claim and never acquires a lapsed one.

    The clock these implementations run on is frozen, so a renewal is
    observed by asking for a LONGER lease than the claim carried rather
    than by waiting: what an advancing clock would show as an expiry moving
    ahead of the wall is shown here as an expiry moving ahead of the one
    the claim was granted with.  The temporal half — a job outliving its
    lease keeping a live claim — is asserted over the heartbeat that drives
    these calls, where the clock is a collaborator.
    """

    async def test_renewal_extends_a_claim_the_holder_already_holds(
        self,
        tracker: TrackerPort,
    ) -> None:
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
        )

        renewed = await tracker.renew_claim(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS * 2,
        )

        assert renewed is not None
        assert renewed.status is ClaimStatus.GRANTED
        assert renewed.expires_at == FIXTURE_NOW + timedelta(
            seconds=LEASE_SECONDS * 2,
        )

    async def test_the_extension_is_what_a_later_reader_sees(
        self,
        tracker: TrackerPort,
    ) -> None:
        """A renewal nobody else can observe protects nothing."""
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
        )
        renewed = await tracker.renew_claim(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS * 2,
        )

        held = await tracker.active_claim(issue_key=CLAIMED_ISSUE)

        assert renewed is not None
        assert held is not None
        assert held.holder == "pass-a"
        assert held.expires_at == renewed.expires_at

    async def test_renewal_never_acquires_an_unclaimed_issue(
        self,
        tracker: TrackerPort,
    ) -> None:
        """The crash arm: a lapsed claim stays lapsed and stays claimable."""
        assert (
            await tracker.renew_claim(
                issue_key=CLAIMED_ISSUE,
                holder="pass-a",
                lease_seconds=LEASE_SECONDS,
            )
            is None
        )
        assert await tracker.active_claim(issue_key=CLAIMED_ISSUE) is None
        taken = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-b",
            lease_seconds=LEASE_SECONDS,
        )
        assert taken.status is ClaimStatus.GRANTED

    async def test_a_non_holder_renews_nothing_and_moves_nothing(
        self,
        tracker: TrackerPort,
    ) -> None:
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
        )

        refused = await tracker.renew_claim(
            issue_key=CLAIMED_ISSUE,
            holder="pass-b",
            lease_seconds=LEASE_SECONDS * 2,
        )

        assert refused is None
        held = await tracker.active_claim(issue_key=CLAIMED_ISSUE)
        assert held is not None
        assert held.holder == "pass-a"
        assert held.expires_at == FIXTURE_NOW + timedelta(seconds=LEASE_SECONDS)

    async def test_a_renewed_claim_still_defeats_a_second_claimant(
        self,
        tracker: TrackerPort,
    ) -> None:
        """The whole point: the TRACKER excludes the second claimant."""
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
        )
        await tracker.renew_claim(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS * 2,
        )

        loser = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-b",
            lease_seconds=LEASE_SECONDS,
        )

        assert loser.status is ClaimStatus.LOST

    async def test_a_renewal_across_a_competitors_claim_still_holds_the_issue(
        self,
        tracker: TrackerPort,
    ) -> None:
        """The competitor arrives BETWEEN renewals and changes nothing.

        A renewal may not cost the holder its place: whatever a refused
        claimant did while the run was working, the run that is still
        working is the one holding the issue.
        """
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
        )
        await tracker.renew_claim(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
        )
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-b",
            lease_seconds=LEASE_SECONDS,
        )
        renewed = await tracker.renew_claim(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS * 2,
        )

        held = await tracker.active_claim(issue_key=CLAIMED_ISSUE)

        assert renewed is not None
        assert held is not None
        assert held.holder == "pass-a"
        assert held.expires_at == renewed.expires_at

    async def test_release_frees_a_renewed_claim_whole(
        self,
        tracker: TrackerPort,
    ) -> None:
        """Every write the renewals made goes, not merely the newest."""
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
        )
        await tracker.renew_claim(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS * 2,
        )

        await tracker.release_claim(issue_key=CLAIMED_ISSUE, holder="pass-a")

        assert await tracker.active_claim(issue_key=CLAIMED_ISSUE) is None


class TestSurfaceLease:
    """All-or-nothing exclusion over a SET of write surfaces.

    Extends the claim cases directly above: a claim answers which
    deployment may fire an issue, a lease answers which run may write a
    surface. An implementation that cannot fence set ownership refuses
    before it takes anything, which is what the refusal contract observes.
    """

    async def test_acquire_grants_the_whole_set_and_reads_back(
        self,
        tracker: TrackerPort,
    ) -> None:
        requested = frozenset({CLAIMED_DESCRIPTION, MARKER_A})

        lease = await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )

        assert lease.holder == JOB_A
        assert lease.surfaces == requested
        assert lease.expires_at == FIXTURE_NOW + timedelta(seconds=LEASE_SECONDS)

    async def test_an_intersecting_set_is_refused_naming_the_surface_and_holder(
        self,
        tracker: TrackerPort,
    ) -> None:
        await tracker.acquire_surfaces(
            surfaces=frozenset({CLAIMED_DESCRIPTION, MARKER_A}),
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )

        with pytest.raises(SurfaceLeaseError) as refused:
            await tracker.acquire_surfaces(
                surfaces=frozenset({MARKER_A, MARKER_B}),
                holder=JOB_B,
                lease_seconds=LEASE_SECONDS,
            )

        assert refused.value.surface_kind == "marker_comment"
        assert refused.value.marker == "A"
        assert refused.value.scope_key == CLAIMED_ISSUE
        assert refused.value.current_holder == JOB_A

    async def test_a_failed_acquisition_holds_nothing(
        self,
        tracker: TrackerPort,
    ) -> None:
        """The refused holder took no part of the set it asked for."""
        await tracker.acquire_surfaces(
            surfaces=frozenset({CLAIMED_DESCRIPTION, MARKER_A}),
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )
        with pytest.raises(SurfaceLeaseError):
            await tracker.acquire_surfaces(
                surfaces=frozenset({MARKER_A, MARKER_B}),
                holder=JOB_B,
                lease_seconds=LEASE_SECONDS,
            )

        taken = await tracker.acquire_surfaces(
            surfaces=frozenset({MARKER_B}),
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )

        assert taken.holder == JOB_A
        with pytest.raises(SurfaceLeaseError) as refused:
            await tracker.acquire_surfaces(
                surfaces=frozenset({MARKER_B}),
                holder=JOB_B,
                lease_seconds=LEASE_SECONDS,
            )
        assert refused.value.current_holder == JOB_A

    async def test_two_holders_with_disjoint_sets_both_acquire(
        self,
        tracker: TrackerPort,
    ) -> None:
        first = await tracker.acquire_surfaces(
            surfaces=frozenset({CLAIMED_DESCRIPTION}),
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )
        second = await tracker.acquire_surfaces(
            surfaces=frozenset({APPROVED_DESCRIPTION}),
            holder=JOB_B,
            lease_seconds=LEASE_SECONDS,
        )

        assert first.holder == JOB_A
        assert second.holder == JOB_B

    async def test_two_simultaneous_claimants_on_one_set_produce_exactly_one_lease(
        self,
        tracker: TrackerPort,
    ) -> None:
        requested = frozenset({CLAIMED_DESCRIPTION, MARKER_A})
        outcomes = await asyncio.gather(
            tracker.acquire_surfaces(
                surfaces=requested,
                holder=JOB_A,
                lease_seconds=LEASE_SECONDS,
            ),
            tracker.acquire_surfaces(
                surfaces=requested,
                holder=JOB_B,
                lease_seconds=LEASE_SECONDS,
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(one, SurfaceLease) for one in outcomes) == 1
        assert sum(isinstance(one, SurfaceLeaseError) for one in outcomes) == 1

    async def test_one_holder_acquiring_a_set_twice_at_once_ends_holding_it(
        self,
        tracker: TrackerPort,
    ) -> None:
        """The same identity rule over the lease vocabulary, all-or-nothing.

        Two overlapping acquisitions for ONE holder over one set are one
        ownership observed twice.  Both are leases, no other holder can
        take any part of the set afterwards, and the holder can renew the
        whole of it — the acquisition never leaves it holding nothing.
        """
        requested = frozenset({CLAIMED_DESCRIPTION, MARKER_A})

        outcomes = await asyncio.gather(
            tracker.acquire_surfaces(
                surfaces=requested,
                holder=JOB_A,
                lease_seconds=LEASE_SECONDS,
            ),
            tracker.acquire_surfaces(
                surfaces=requested,
                holder=JOB_A,
                lease_seconds=LEASE_SECONDS,
            ),
        )

        assert [one.surfaces for one in outcomes] == [requested] * 2
        with pytest.raises(SurfaceLeaseError) as refused:
            await tracker.acquire_surfaces(
                surfaces=requested,
                holder=JOB_B,
                lease_seconds=LEASE_SECONDS,
            )
        assert refused.value.current_holder == JOB_A
        renewed = await tracker.renew_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )
        assert renewed is not None
        assert renewed.surfaces == requested

    async def test_one_marker_is_refused_while_the_rest_of_the_issue_stays_acquirable(
        self,
        tracker: TrackerPort,
    ) -> None:
        """Surfaces on one issue are independent addresses, not one lock."""
        await tracker.acquire_surfaces(
            surfaces=frozenset({MARKER_A}),
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )

        rest = await tracker.acquire_surfaces(
            surfaces=frozenset({CLAIMED_DESCRIPTION, MARKER_B}),
            holder=JOB_B,
            lease_seconds=LEASE_SECONDS,
        )

        assert rest.holder == JOB_B
        with pytest.raises(SurfaceLeaseError) as refused:
            await tracker.acquire_surfaces(
                surfaces=frozenset({MARKER_A}),
                holder=JOB_B,
                lease_seconds=LEASE_SECONDS,
            )
        assert refused.value.current_holder == JOB_A

    async def test_release_frees_the_set_for_the_next_holder(
        self,
        tracker: TrackerPort,
    ) -> None:
        requested = frozenset({CLAIMED_DESCRIPTION, MARKER_A})
        await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )

        await tracker.release_surfaces(surfaces=requested, holder=JOB_A)

        taken = await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_B,
            lease_seconds=LEASE_SECONDS,
        )
        assert taken.holder == JOB_B

    async def test_release_by_a_non_holder_is_a_no_op(
        self,
        tracker: TrackerPort,
    ) -> None:
        requested = frozenset({CLAIMED_DESCRIPTION, MARKER_A})
        await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )

        await tracker.release_surfaces(surfaces=requested, holder=JOB_B)

        with pytest.raises(SurfaceLeaseError) as refused:
            await tracker.acquire_surfaces(
                surfaces=requested,
                holder=JOB_B,
                lease_seconds=LEASE_SECONDS,
            )
        assert refused.value.current_holder == JOB_A

    async def test_renewal_extends_a_lease_the_holder_holds(
        self,
        tracker: TrackerPort,
    ) -> None:
        requested = frozenset({CLAIMED_DESCRIPTION, MARKER_A})
        await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )

        renewed = await tracker.renew_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS * 2,
        )

        assert renewed is not None
        assert renewed.expires_at == FIXTURE_NOW + timedelta(
            seconds=LEASE_SECONDS * 2,
        )

    async def test_renewal_by_a_non_holder_renews_nothing(
        self,
        tracker: TrackerPort,
    ) -> None:
        requested = frozenset({CLAIMED_DESCRIPTION, MARKER_A})
        await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )

        refused_renewal = await tracker.renew_surfaces(
            surfaces=requested,
            holder=JOB_B,
            lease_seconds=LEASE_SECONDS * 2,
        )

        assert refused_renewal is None
        with pytest.raises(SurfaceLeaseError) as refused:
            await tracker.acquire_surfaces(
                surfaces=requested,
                holder=JOB_B,
                lease_seconds=LEASE_SECONDS,
            )
        assert refused.value.current_holder == JOB_A

    async def test_renewal_of_a_partially_held_set_renews_nothing(
        self,
        tracker: TrackerPort,
    ) -> None:
        """Renewal extends what the holder holds WHOLE, and never acquires."""
        await tracker.acquire_surfaces(
            surfaces=frozenset({CLAIMED_DESCRIPTION}),
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )

        renewed = await tracker.renew_surfaces(
            surfaces=frozenset({CLAIMED_DESCRIPTION, MARKER_A}),
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS * 2,
        )

        assert renewed is None
        taken = await tracker.acquire_surfaces(
            surfaces=frozenset({MARKER_A}),
            holder=JOB_B,
            lease_seconds=LEASE_SECONDS,
        )
        assert taken.holder == JOB_B

    async def test_a_refused_renewal_leaves_the_holder_nothing_of_that_set(
        self,
        tracker: TrackerPort,
    ) -> None:
        """A hold on part of a set is no hold, and the refusal withdraws it.

        The holder takes a pair and gives one of them back, so what stands
        of the set is its remnant on the other.  Renewing the pair is
        refused, and the refusal leaves the holder holding nothing of it:
        another holder asking for the remnant's surface is granted.
        """
        pair = frozenset({CLAIMED_DESCRIPTION, MARKER_A})
        await tracker.acquire_surfaces(
            surfaces=pair, holder=JOB_A, lease_seconds=LEASE_SECONDS
        )
        await tracker.release_surfaces(surfaces=frozenset({MARKER_A}), holder=JOB_A)

        renewed = await tracker.renew_surfaces(
            surfaces=pair, holder=JOB_A, lease_seconds=LEASE_SECONDS
        )

        assert renewed is None
        taken = await tracker.acquire_surfaces(
            surfaces=frozenset({CLAIMED_DESCRIPTION}),
            holder=JOB_B,
            lease_seconds=LEASE_SECONDS,
        )
        assert taken.holder == JOB_B

    async def test_same_holder_reacquisition_is_not_contention(
        self,
        tracker: TrackerPort,
    ) -> None:
        requested = frozenset({CLAIMED_DESCRIPTION, MARKER_A})
        await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )

        again = await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )

        assert again.holder == JOB_A
        assert again.surfaces == requested

    async def test_a_non_default_duration_is_the_one_honored(
        self,
        tracker: TrackerPort,
        clock: FixtureClock,
    ) -> None:
        """The duration the CALL supplies is the one the lease expires on."""
        requested = frozenset({CLAIMED_DESCRIPTION, MARKER_A})
        lease = await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS * 3,
        )

        clock.advance(seconds=LEASE_SECONDS * 2)

        with pytest.raises(SurfaceLeaseError) as refused:
            await tracker.acquire_surfaces(
                surfaces=requested,
                holder=JOB_B,
                lease_seconds=LEASE_SECONDS,
            )
        assert refused.value.current_holder == JOB_A
        assert lease.expires_at == FIXTURE_NOW + timedelta(
            seconds=LEASE_SECONDS * 3,
        )

    async def test_an_expired_lease_is_free_for_another_holder(
        self,
        tracker: TrackerPort,
        clock: FixtureClock,
    ) -> None:
        requested = frozenset({CLAIMED_DESCRIPTION, MARKER_A})
        await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )

        clock.advance(seconds=LEASE_SECONDS + 1)

        taken = await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_B,
            lease_seconds=LEASE_SECONDS,
        )
        assert taken.holder == JOB_B

    async def test_the_original_holder_reacquires_an_expired_lease_without_contention(
        self,
        tracker: TrackerPort,
        clock: FixtureClock,
    ) -> None:
        """A run coming back to its own lapsed surfaces contends with nobody."""
        requested = frozenset({CLAIMED_DESCRIPTION, MARKER_A})
        await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )

        clock.advance(seconds=LEASE_SECONDS + 1)

        again = await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )
        assert again.holder == JOB_A
        assert again.expires_at == clock.now + timedelta(seconds=LEASE_SECONDS)

    async def test_a_renewal_after_expiry_and_reacquisition_cannot_steal(
        self,
        tracker: TrackerPort,
        clock: FixtureClock,
    ) -> None:
        """The delayed renewal arrives after the set changed hands, and loses."""
        requested = frozenset({CLAIMED_DESCRIPTION, MARKER_A})
        await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )
        clock.advance(seconds=LEASE_SECONDS + 1)
        await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_B,
            lease_seconds=LEASE_SECONDS,
        )

        stale = await tracker.renew_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )

        assert stale is None
        held = await tracker.renew_surfaces(
            surfaces=requested,
            holder=JOB_B,
            lease_seconds=LEASE_SECONDS,
        )
        assert held is not None
        with pytest.raises(SurfaceLeaseError) as refused:
            await tracker.acquire_surfaces(
                surfaces=requested,
                holder=JOB_A,
                lease_seconds=LEASE_SECONDS,
            )
        assert refused.value.current_holder == JOB_B

    async def test_a_renewal_before_expiry_keeps_the_holder_past_the_original_bound(
        self,
        tracker: TrackerPort,
        clock: FixtureClock,
    ) -> None:
        requested = frozenset({CLAIMED_DESCRIPTION, MARKER_A})
        await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )
        clock.advance(seconds=LEASE_SECONDS / 2)
        await tracker.renew_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )

        clock.advance(seconds=LEASE_SECONDS / 2 + 1)

        with pytest.raises(SurfaceLeaseError) as refused:
            await tracker.acquire_surfaces(
                surfaces=requested,
                holder=JOB_B,
                lease_seconds=LEASE_SECONDS,
            )
        assert refused.value.current_holder == JOB_A

    async def test_a_lapsed_lease_is_not_renewable(
        self,
        tracker: TrackerPort,
        clock: FixtureClock,
    ) -> None:
        """A lapse hands the surfaces back; renewal may not take them again."""
        requested = frozenset({CLAIMED_DESCRIPTION, MARKER_A})
        await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )

        clock.advance(seconds=LEASE_SECONDS + 1)

        assert (
            await tracker.renew_surfaces(
                surfaces=requested,
                holder=JOB_A,
                lease_seconds=LEASE_SECONDS,
            )
            is None
        )
        taken = await tracker.acquire_surfaces(
            surfaces=requested,
            holder=JOB_B,
            lease_seconds=LEASE_SECONDS,
        )
        assert taken.holder == JOB_B

    async def test_a_fire_claim_and_a_write_lease_are_held_under_distinct_identities(
        self,
        tracker: TrackerPort,
    ) -> None:
        """Two questions, two vocabularies, neither derived from the other.

        A claim answers which DEPLOYMENT may fire an issue and is held
        under a process identity; a lease answers which RUN may write a
        surface and is held under a job id. Holding one confers nothing
        about the other, on the same issue.
        """
        claimed = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder=PROCESS_HOLDER,
            lease_seconds=LEASE_SECONDS,
        )
        lease = await tracker.acquire_surfaces(
            surfaces=frozenset({CLAIMED_DESCRIPTION}),
            holder=JOB_HOLDER,
            lease_seconds=LEASE_SECONDS,
        )

        assert claimed.status is ClaimStatus.GRANTED
        held = await tracker.active_claim(issue_key=CLAIMED_ISSUE)
        assert held is not None
        assert held.holder == PROCESS_HOLDER
        assert lease.holder == JOB_HOLDER

        with pytest.raises(SurfaceLeaseError) as refused:
            await tracker.acquire_surfaces(
                surfaces=frozenset({CLAIMED_DESCRIPTION}),
                holder=PROCESS_HOLDER,
                lease_seconds=LEASE_SECONDS,
            )
        assert refused.value.current_holder == JOB_HOLDER
        lost = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder=JOB_HOLDER,
            lease_seconds=LEASE_SECONDS,
        )
        assert lost.status is ClaimStatus.LOST

    async def test_a_write_lease_alone_is_no_claim_and_a_claim_beside_it_is_no_lease(
        self,
        tracker: TrackerPort,
    ) -> None:
        """A lease on an issue's surface grants nothing over the issue itself.

        The two grants live on the same issue and are told apart by their kind
        alone, so a lease taken by itself must leave the issue unclaimed: a
        reader asking who may fire it gets nobody. The claim that follows is
        granted although the lease stands, reports its own process holder, and
        neither grant disturbs the other's lifetime.
        """
        lease = await tracker.acquire_surfaces(
            surfaces=frozenset({CLAIMED_DESCRIPTION}),
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )
        assert lease.holder == JOB_A
        assert await tracker.active_claim(issue_key=CLAIMED_ISSUE) is None

        claimed = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder=PROCESS_HOLDER,
            lease_seconds=LEASE_SECONDS,
        )
        assert claimed.status is ClaimStatus.GRANTED
        held = await tracker.active_claim(issue_key=CLAIMED_ISSUE)
        assert held is not None
        assert held.holder == PROCESS_HOLDER

        renewed = await tracker.renew_surfaces(
            surfaces=frozenset({CLAIMED_DESCRIPTION}),
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )
        assert renewed is not None
        assert renewed.holder == JOB_A

        await tracker.release_surfaces(
            surfaces=frozenset({CLAIMED_DESCRIPTION}), holder=JOB_A
        )
        still_held = await tracker.active_claim(issue_key=CLAIMED_ISSUE)
        assert still_held is not None
        assert still_held.holder == PROCESS_HOLDER


class TestAssets:
    """Attachment and document metadata, and document reads."""

    async def test_issue_assets_carry_metadata(self, tracker: TrackerPort) -> None:
        """Identity and title reach the consumer; type and size read absent.

        ``content_type`` and ``size_bytes`` are ``None`` because no
        captured vendor payload carries either field: every measured asset
        array holds ``id``, ``title``, ``subtitle`` and ``url`` and nothing
        else.  The fixture used to supply both values and this row used to
        assert they arrived — a pass-through pinned over an input
        production cannot produce.  ``None`` is what the workspace
        actually reports, and :class:`TrackerAsset` owns what it means:
        "the tracker did not report one", which is not any particular
        value.

        The pass-through CODE is untouched, so a vendor that starts
        sending either field delivers it here unchanged; the wire model
        keeps both optional for exactly that (KOD-143 fire-ruling,
        2026-08-25).
        """
        assets = await tracker.list_issue_assets(issue_key=ASSET_ISSUE)
        by_key = {asset.asset_key: asset for asset in assets}
        assert by_key["asset-1"].title == "spec.pdf"
        assert by_key["asset-1"].content_type is None
        assert by_key["asset-1"].size_bytes is None
        assert DOCUMENT_KEY in by_key

    async def test_an_issue_without_assets_reports_none(
        self,
        tracker: TrackerPort,
    ) -> None:
        assert await tracker.list_issue_assets(issue_key=CLAIMED_ISSUE) == ()

    async def test_document_content_is_readable(self, tracker: TrackerPort) -> None:
        assert await tracker.read_document(document_key=DOCUMENT_KEY) == (
            DOCUMENT_CONTENT
        )


class TestMappingResolution:
    """Configured mappings resolve, or are named."""

    async def test_resolvable_mappings_report_nothing(
        self,
        tracker: TrackerPort,
    ) -> None:
        unresolved = await tracker.resolve_mappings(
            refs=[
                MappingRef(
                    kind=MappingKind.USER,
                    name="approver",
                    identifier=APPROVER,
                ),
                MappingRef(
                    kind=MappingKind.TEAM,
                    name="engineering",
                    identifier="fixture-team",
                ),
                MappingRef(
                    kind=MappingKind.QUEUE_STATE,
                    name="approved",
                    identifier="queue:approved",
                ),
                MappingRef(
                    kind=MappingKind.WORKFLOW_STATE,
                    name="in_review",
                    identifier="In Review",
                ),
            ],
        )
        assert unresolved == ()

    @pytest.mark.parametrize("kind", sorted(MappingKind))
    async def test_an_unknown_identifier_is_reported_for_every_kind(
        self,
        tracker: TrackerPort,
        kind: MappingKind,
    ) -> None:
        ref = MappingRef(kind=kind, name="whatever", identifier="absent-from-fixture")
        assert await tracker.resolve_mappings(refs=[ref]) == (ref,)

    async def test_only_the_unresolvable_subset_is_returned(
        self,
        tracker: TrackerPort,
    ) -> None:
        good = MappingRef(
            kind=MappingKind.USER,
            name="approver",
            identifier=APPROVER,
        )
        bad = MappingRef(
            kind=MappingKind.USER,
            name="ghost",
            identifier="never-existed",
        )
        assert await tracker.resolve_mappings(refs=[good, bad]) == (bad,)


class TestMappingEnsure:
    """The three ensure outcomes, plus the two refusals, over EVERY port.

    ``ensure_mappings`` carried no conformance row at all before this class,
    and the adapter and the consumer double had already drifted apart over
    it — one refused every kind but ``QUEUE_STATE``, the other accepted and
    created all four.  KOD-57 R6's premise is that the suite is what keeps
    the double honest; it does not reach a method the suite never calls.
    """

    #: A second container of the same workspace.  A queue member defined in
    #: one container says nothing about another, so this is what a ref
    #: declaring a board the value is not on yet looks like.
    OTHER_CONTAINER = "fixture-other-team"

    def _ref(self, identifier: str, scope: str | None) -> MappingRef:
        return MappingRef(
            kind=MappingKind.QUEUE_STATE,
            name="conformance",
            identifier=identifier,
            scope=scope,
        )

    async def test_an_absent_value_is_created_and_then_resolves(
        self,
        tracker: TrackerPort,
    ) -> None:
        ref = self._ref("queue:conformance-created", TEAM)
        assert await tracker.resolve_mappings(refs=[ref]) == (ref,)

        outcomes = await tracker.ensure_mappings(refs=[ref])

        assert [outcome.action for outcome in outcomes] == [EnsureAction.CREATED]
        assert await tracker.resolve_mappings(refs=[ref]) == ()

    async def test_a_value_already_present_is_adopted_and_not_rewritten(
        self,
        tracker: TrackerPort,
    ) -> None:
        """A second boot over the same workspace writes nothing at all."""
        ref = self._ref("queue:conformance-adopted", TEAM)
        await tracker.ensure_mappings(refs=[ref])

        outcomes = await tracker.ensure_mappings(refs=[ref])

        assert [outcome.action for outcome in outcomes] == [EnsureAction.ADOPTED]

    async def test_a_second_container_missing_the_value_gets_its_own(
        self,
        tracker: TrackerPort,
    ) -> None:
        """KOD-167: a member resolves WITHIN each container, never across them.

        The measured shape that could not boot: two declared boards, each
        carrying its own copy of the queue vocabulary, neither of them the
        other's to move.  A ref for the board that lacks the member is
        answered by making that board its own — reading the first board's
        copy as a conflict is what refused the boot.
        """
        identifier = "queue:conformance-per-board"
        await tracker.ensure_mappings(refs=[self._ref(identifier, TEAM)])

        outcomes = await tracker.ensure_mappings(
            refs=[self._ref(identifier, self.OTHER_CONTAINER)],
        )

        assert [outcome.action for outcome in outcomes] == [EnsureAction.CREATED]
        # And the first board's definition still stands, untouched: the
        # second board's copy was added beside it, never in place of it.
        again = await tracker.ensure_mappings(refs=[self._ref(identifier, TEAM)])
        assert [outcome.action for outcome in again] == [EnsureAction.ADOPTED]

    async def test_a_workspace_ref_beside_a_container_definition_refuses(
        self,
        tracker: TrackerPort,
    ) -> None:
        """The surviving refusal: a shape in which adoption is ill-defined.

        A ref belonging to the WORKSPACE while the value is defined inside
        a container has no defensible answer — adopting the container's
        copy would claim it serves every board, and creating a second one
        beside it would leave two definitions of one member with nothing to
        say which a write resolves to.
        """
        identifier = "queue:conformance-contested"
        await tracker.ensure_mappings(refs=[self._ref(identifier, TEAM)])

        with pytest.raises(TrackerEnsureConflictError) as caught:
            await tracker.ensure_mappings(refs=[self._ref(identifier, None)])

        assert identifier in caught.value.entry
        assert TEAM in str(caught.value)
        # The refusal wrote nothing: the value is still the one that was
        # created, adopted unchanged under the container it was made in.
        again = await tracker.ensure_mappings(refs=[self._ref(identifier, TEAM)])
        assert [outcome.action for outcome in again] == [EnsureAction.ADOPTED]

    async def test_the_refusal_aborts_before_the_refs_that_follow_it(
        self,
        tracker: TrackerPort,
    ) -> None:
        """ "Performs no write" is about the whole call, not about one ref."""
        contested = "queue:conformance-first"
        follower = self._ref("queue:conformance-follower", TEAM)
        await tracker.ensure_mappings(refs=[self._ref(contested, TEAM)])

        with pytest.raises(TrackerEnsureConflictError):
            await tracker.ensure_mappings(
                refs=[self._ref(contested, None), follower],
            )

        assert await tracker.resolve_mappings(refs=[follower]) == (follower,)

    @pytest.mark.parametrize(
        "kind",
        sorted(set(MappingKind) - INSTATABLE_MAPPING_KINDS),
    )
    async def test_a_kind_no_owned_field_produces_is_refused(
        self,
        tracker: TrackerPort,
        kind: MappingKind,
    ) -> None:
        """Instatability is the domain's fact, so every port refuses alike."""
        ref = MappingRef(
            kind=kind,
            name="conformance",
            identifier="never-instated",
            scope=TEAM,
        )

        with pytest.raises(TrackerEnsureConflictError):
            await tracker.ensure_mappings(refs=[ref])

        assert await tracker.resolve_mappings(refs=[ref]) == (ref,)


class TestDocumentEnsure:
    """The document arm of the ensure contract, over EVERY port.

    A document is the one owned value whose identifier the WORKSPACE
    assigns, which is why it has its own class: every row here is about a
    ref that names a title and may or may not know an id yet, and none of
    them is expressible over a queue-state ref.
    """

    TITLE = "conformance checkpoint"
    OTHER_TITLE = "conformance elsewhere"

    def _ref(
        self,
        title: str,
        identifier: str | None = None,
        scope: str | None = TEAM,
    ) -> MappingRef:
        """A document ref declaring the fixture team as its container.

        The container rides every ref by default because creation needs
        one (KOD-166) and adoption ignores it; the refusal case passes
        ``scope=None`` explicitly.
        """
        return MappingRef(
            kind=MappingKind.DOCUMENT,
            name=title,
            identifier=identifier,
            scope=scope,
        )

    async def test_a_create_with_no_declared_container_is_refused(
        self,
        tracker: TrackerPort,
    ) -> None:
        """The backend files every document in a container, so a create
        naming none is refused up front, naming what to declare (KOD-166).
        """
        with pytest.raises(TrackerEnsureConflictError) as caught:
            await tracker.ensure_mappings(
                refs=[self._ref(self.TITLE, scope=None)],
            )

        assert "container" in str(caught.value)

    async def _adopted_id(self, tracker: TrackerPort, title: str) -> str:
        (outcome,) = await tracker.ensure_mappings(refs=[self._ref(title)])
        return outcome.identifier

    async def test_a_document_the_workspace_lacks_is_created_and_its_id_reported(
        self,
        tracker: TrackerPort,
    ) -> None:
        """The founder's measured manual step, closed: nothing made by hand."""
        (outcome,) = await tracker.ensure_mappings(refs=[self._ref(self.TITLE)])

        assert outcome.action is EnsureAction.CREATED
        assert outcome.identifier
        assert outcome.ref.identifier is None

    async def test_a_second_boot_adopts_the_document_it_created(
        self,
        tracker: TrackerPort,
    ) -> None:
        """Two boots over one workspace leave ONE document, not two."""
        created = await self._adopted_id(tracker, self.TITLE)

        (outcome,) = await tracker.ensure_mappings(refs=[self._ref(self.TITLE)])

        assert outcome.action is EnsureAction.ADOPTED
        assert outcome.identifier == created

    async def test_a_config_carrying_the_adopted_id_is_adopted_unchanged(
        self,
        tracker: TrackerPort,
    ) -> None:
        """The state after one reconciliation: the id is written back and pins."""
        created = await self._adopted_id(tracker, self.TITLE)

        (outcome,) = await tracker.ensure_mappings(
            refs=[self._ref(self.TITLE, created)],
        )

        assert outcome.action is EnsureAction.ADOPTED
        assert outcome.identifier == created

    async def test_a_declared_id_the_workspace_does_not_hold_is_refused(
        self,
        tracker: TrackerPort,
    ) -> None:
        """Creating a second document would leave the config naming neither."""
        with pytest.raises(TrackerEnsureConflictError) as caught:
            await tracker.ensure_mappings(
                refs=[self._ref(self.TITLE, "never-existed")],
            )

        assert self.TITLE in caught.value.entry

    async def test_a_declared_id_whose_document_has_another_title_is_refused(
        self,
        tracker: TrackerPort,
    ) -> None:
        """R8's rule on this arm: serving the ref would rename a document."""
        created = await self._adopted_id(tracker, self.TITLE)

        with pytest.raises(TrackerEnsureConflictError) as caught:
            await tracker.ensure_mappings(
                refs=[self._ref(self.OTHER_TITLE, created)],
            )

        assert self.OTHER_TITLE in caught.value.entry
        # The refusal wrote nothing: the document is still the one created,
        # still under the title it was created with.
        (again,) = await tracker.ensure_mappings(refs=[self._ref(self.TITLE)])
        assert again.action is EnsureAction.ADOPTED
        assert again.identifier == created

    async def test_a_ref_of_another_kind_carrying_no_identifier_is_refused(
        self,
        tracker: TrackerPort,
    ) -> None:
        """Only a workspace-assigned kind may omit it; the rest name nothing."""
        ref = MappingRef(
            kind=MappingKind.QUEUE_STATE,
            name="conformance",
            identifier=None,
            scope=TEAM,
        )

        with pytest.raises(TrackerEnsureConflictError) as caught:
            await tracker.ensure_mappings(refs=[ref])

        assert "conformance" in caught.value.entry


class TestScanReviews:
    """Reviews are their own object class, scanned within their own container.

    The gate asks the review question once per repository it is scoped to,
    so a scan naming one must answer for that one alone — the same
    containment ``scan_issues`` gives a team, over the object class no
    issue scan reaches.
    """

    async def test_a_scan_scoped_to_a_repository_answers_for_it_alone(
        self,
        tracker: TrackerPort,
    ) -> None:
        found = await tracker.scan_reviews(
            query=ReviewQuery(repo_url=FIXTURE_REPO_URL, page_size=10),
        )

        assert [review.review_key for review in found] == [FIXTURE_REVIEW]

    async def test_reviews_come_back_newest_first(
        self,
        tracker: TrackerPort,
    ) -> None:
        """Contract, not accident: a recency question is answered from one page."""
        found = await tracker.scan_reviews(query=ReviewQuery(page_size=10))

        assert [review.review_key for review in found] == [
            FIXTURE_REVIEW,
            FOREIGN_REVIEW,
        ]


class TestScanCapability:
    """Whether a credential can answer a signal's scan is PROBED, never read.

    A backend offers the scan whatever the credential holds, so nothing a
    listing says distinguishes a scan that will answer from one that will
    refuse.  What the port promises is the answer, not the mechanism: a
    refusal comes back as the backend's own diagnosis, and everything else
    comes back empty.
    """

    async def test_a_credential_that_can_scan_reports_no_refusal_at_all(
        self,
        tracker: TrackerPort,
    ) -> None:
        assert await tracker.verify_scan_capability(signals=list(PassSignal)) == {}

    @pytest.mark.parametrize("refused_signals", [REFUSED], indirect=True)
    async def test_a_refused_scan_comes_back_as_the_backends_own_diagnosis(
        self,
        tracker: TrackerPort,
    ) -> None:
        refusals = await tracker.verify_scan_capability(signals=list(PassSignal))

        assert PassSignal.reviews_changed in refusals
        assert SCOPE_DIAGNOSIS in refusals[PassSignal.reviews_changed]

    @pytest.mark.parametrize("refused_signals", [REFUSED], indirect=True)
    async def test_a_signal_the_credential_can_answer_is_never_reported(
        self,
        tracker: TrackerPort,
    ) -> None:
        """One refusal is a refusal of ONE signal, never of the sweep."""
        refusals = await tracker.verify_scan_capability(signals=list(PassSignal))

        assert PassSignal.issues_changed not in refusals

    @pytest.mark.parametrize("refused_signals", [REFUSED], indirect=True)
    async def test_a_signal_that_was_not_asked_about_is_not_answered(
        self,
        tracker: TrackerPort,
    ) -> None:
        """The sweep answers about what it was handed and nothing else."""
        refusals = await tracker.verify_scan_capability(
            signals=[PassSignal.issues_changed],
        )

        assert refusals == {}


class TestWriterIdentity:
    """Who the backend attributes this adapter's writes to.

    A boot-time read, compared there against the operation's declared
    non-human writer. It is stated at the port so every implementation
    answers the same question, and it is a READ: asking must change
    nothing.
    """

    async def test_the_writer_identity_carries_both_spellings_of_the_actor(
        self,
        tracker: TrackerPort,
        server: FakeLinearMcpServer,
    ) -> None:
        assert await tracker.writer_identity() == {
            server.actor,
            server.display_name(server.actor),
        }

    async def test_the_writer_identity_is_read_not_written(
        self,
        tracker: TrackerPort,
        tracker_writes: Callable[[], tuple[object, ...]],
    ) -> None:
        before = tracker_writes()

        await tracker.writer_identity()

        assert tracker_writes() == before


class TestSubstitutability:
    """No capability flags, no feature detection, no partial adapters."""

    def test_the_adapter_satisfies_the_whole_port(
        self,
        tracker: TrackerPort,
    ) -> None:
        assert isinstance(tracker, TrackerPort)

    def test_the_public_surface_is_exactly_the_port(
        self,
        adapter: TrackerPort,
    ) -> None:
        """No extra public member — nothing for a consumer to discover on.

        Feature detection needs something to detect.  An adapter whose public
        surface is exactly the port's leaves a consumer no way to branch on
        which backend is configured, which is what substitutability means
        here.

        This one case takes the ADAPTER fixture rather than the shared one,
        and it is the only case in the module that does.  The rule is about
        what a deployment can be configured to dial: a test double is never
        a configured backend, and it must expose the writes it recorded or a
        consumer test has nothing to assert on.  Every BEHAVIOURAL case
        below and above runs over the double unchanged, which is what the
        ruling asks for; narrowing this one leaves the adapters it covers
        covered exactly as before.
        """
        tracker = adapter
        port_members = {
            name
            for name in dir(TrackerPort)
            if not name.startswith("_") and callable(getattr(TrackerPort, name))
        }
        adapter_members = {name for name in dir(tracker) if not name.startswith("_")}
        assert adapter_members == port_members


class TestWorkRefs:
    """Work refs round-trip through the port, at every role.

    Added here rather than beside the resolver so it binds every FUTURE
    adapter rather than today's: an adapter that cannot carry a role, or
    that recovers an unpushed ref as anything other than ``None``, fails
    the suite that defines conforming.
    """

    @pytest.mark.parametrize("role", list(WorkRefRole))
    async def test_every_role_round_trips_byte_identical(
        self,
        tracker: TrackerPort,
        role: WorkRefRole,
    ) -> None:
        ref = WorkRef(
            issue_id=APPROVED_ISSUE,
            role=role,
            branch=f"kodezart/fixture-{role.value}",
            pushed_head_sha="0" * 40,
            recorded_at=FIXTURE_NOW,
        )
        await tracker.record_work_ref(ref=ref)
        stored = await tracker.work_refs(issue_key=APPROVED_ISSUE)
        assert [(r.role, r.branch, r.pushed_head_sha) for r in stored] == [
            (role, ref.branch, ref.pushed_head_sha),
        ]

    async def test_an_unpushed_ref_recovers_as_none_and_never_as_false(
        self,
        tracker: TrackerPort,
    ) -> None:
        """Three-state, preserved across the wire.

        ``False`` and ``""`` are both wrong answers here: an adapter that
        collapsed the field to a boolean would let a resolution treat an
        unpushed ref as present.
        """
        await tracker.record_work_ref(
            ref=WorkRef(
                issue_id=APPROVED_ISSUE,
                role=WorkRefRole.DELIVERABLE,
                branch="kodezart/unpushed",
                pushed_head_sha=None,
                recorded_at=FIXTURE_NOW,
            ),
        )
        (stored,) = await tracker.work_refs(issue_key=APPROVED_ISSUE)
        assert stored.pushed_head_sha is None
        assert stored.pushed_head_sha is not False

    async def test_an_issue_with_no_recorded_refs_reads_empty(
        self,
        tracker: TrackerPort,
    ) -> None:
        assert await tracker.work_refs(issue_key=CLAIMED_ISSUE) == ()

    async def test_a_second_deliverable_ref_raises_and_replaces_nothing(
        self,
        tracker: TrackerPort,
    ) -> None:
        first = WorkRef(
            issue_id=APPROVED_ISSUE,
            role=WorkRefRole.DELIVERABLE,
            branch="kodezart/first",
            pushed_head_sha="a" * 40,
            recorded_at=FIXTURE_NOW,
        )
        await tracker.record_work_ref(ref=first)
        with pytest.raises(DuplicateWorkRefError):
            await tracker.record_work_ref(
                ref=first.model_copy(update={"branch": "kodezart/second"}),
            )
        stored = await tracker.work_refs(issue_key=APPROVED_ISSUE)
        assert [r.branch for r in stored] == ["kodezart/first"]

    async def test_recording_the_same_ref_twice_is_idempotent(
        self,
        tracker: TrackerPort,
    ) -> None:
        ref = WorkRef(
            issue_id=APPROVED_ISSUE,
            role=WorkRefRole.DELIVERABLE,
            branch="kodezart/first",
            pushed_head_sha="a" * 40,
            recorded_at=FIXTURE_NOW,
        )
        await tracker.record_work_ref(ref=ref)
        await tracker.record_work_ref(ref=ref)
        assert len(await tracker.work_refs(issue_key=APPROVED_ISSUE)) == 1

    async def test_work_refs_and_claims_do_not_read_each_other(
        self,
        tracker: TrackerPort,
    ) -> None:
        """Both live on the comment log; neither may see the other's markers."""
        await tracker.claim_issue(
            issue_key=APPROVED_ISSUE,
            holder="fixture-holder",
            lease_seconds=LEASE_SECONDS,
        )
        await tracker.record_work_ref(
            ref=WorkRef(
                issue_id=APPROVED_ISSUE,
                role=WorkRefRole.ITERATION,
                branch="kodezart/iteration",
                recorded_at=FIXTURE_NOW,
            ),
        )
        claim = await tracker.active_claim(issue_key=APPROVED_ISSUE)
        assert claim is not None and claim.holder == "fixture-holder"
        refs = await tracker.work_refs(issue_key=APPROVED_ISSUE)
        assert [r.role for r in refs] == [WorkRefRole.ITERATION]


class TestRecordedBaseSpec:
    """The dispatched base round-trips through the port, at every arm.

    KOD-67 R3 puts the recorded ``BaseSpec`` on the dependent issue THROUGH
    the port, and staleness compares a recorded spec against the one the
    blockers imply now.  Without these two methods the arithmetic could
    only ever compare a value with itself, which is why
    ``domain/base_staleness`` had no production caller.
    """

    def _spec(self, branch: str, *, sha: str = "a" * 40) -> BaseSpec:
        return BaseSpec(
            inputs=(
                BaseInput(
                    blocker_issue_id=CLAIMED_ISSUE,
                    branch="kodezart/blocker",
                    sha=sha,
                ),
            ),
            base_branch=branch,
            base_role=WorkRefRole.DELIVERABLE,
        )

    async def test_an_issue_with_no_dispatch_records_nothing(
        self,
        tracker: TrackerPort,
    ) -> None:
        """ "Never dispatched" is ``None``, and is not a stale base."""
        assert await tracker.read_base_spec(issue_key=APPROVED_ISSUE) is None

    async def test_a_recorded_spec_reads_back_whole(
        self,
        tracker: TrackerPort,
    ) -> None:
        """Every field, including the inputs the equality test is over."""
        spec = self._spec("kodezart/blocker")

        await tracker.record_base_spec(issue_key=APPROVED_ISSUE, spec=spec)

        assert await tracker.read_base_spec(issue_key=APPROVED_ISSUE) == spec

    async def test_the_trunk_arm_round_trips_with_no_role(
        self,
        tracker: TrackerPort,
    ) -> None:
        """``base_role`` is ``None`` on the trunk arm and only there."""
        spec = BaseSpec(inputs=(), base_branch="main")

        await tracker.record_base_spec(issue_key=APPROVED_ISSUE, spec=spec)

        assert await tracker.read_base_spec(issue_key=APPROVED_ISSUE) == spec

    async def test_recording_the_same_spec_twice_is_idempotent(
        self,
        tracker: TrackerPort,
    ) -> None:
        spec = self._spec("kodezart/blocker")
        await tracker.record_base_spec(issue_key=APPROVED_ISSUE, spec=spec)

        await tracker.record_base_spec(issue_key=APPROVED_ISSUE, spec=spec)

        assert await tracker.read_base_spec(issue_key=APPROVED_ISSUE) == spec

    async def test_a_second_dispatch_supersedes_the_first(
        self,
        tracker: TrackerPort,
    ) -> None:
        """A lane dispatched again was dispatched on the base of that dispatch."""
        await tracker.record_base_spec(
            issue_key=APPROVED_ISSUE,
            spec=self._spec("kodezart/first"),
        )
        second = self._spec("kodezart/second", sha="b" * 40)

        await tracker.record_base_spec(issue_key=APPROVED_ISSUE, spec=second)

        assert await tracker.read_base_spec(issue_key=APPROVED_ISSUE) == second

    async def test_the_spec_is_scoped_to_its_issue(
        self,
        tracker: TrackerPort,
    ) -> None:
        await tracker.record_base_spec(
            issue_key=APPROVED_ISSUE,
            spec=self._spec("kodezart/blocker"),
        )

        assert await tracker.read_base_spec(issue_key=ASSET_ISSUE) is None

    async def test_the_base_spec_and_the_work_refs_do_not_see_each_other(
        self,
        tracker: TrackerPort,
    ) -> None:
        """A third marker on one log must not be readable as either other."""
        await tracker.record_base_spec(
            issue_key=APPROVED_ISSUE,
            spec=self._spec("kodezart/blocker"),
        )
        await tracker.record_work_ref(
            ref=WorkRef(
                issue_id=APPROVED_ISSUE,
                role=WorkRefRole.DELIVERABLE,
                branch="kodezart/deliverable",
                recorded_at=FIXTURE_NOW,
            ),
        )

        refs = await tracker.work_refs(issue_key=APPROVED_ISSUE)
        assert [ref.role for ref in refs] == [WorkRefRole.DELIVERABLE]
        recorded = await tracker.read_base_spec(issue_key=APPROVED_ISSUE)
        assert recorded is not None and recorded.base_branch == "kodezart/blocker"


class TestRunEventStream:
    """W3 under P3 — the stream is ordered, and records are not events.

    A lane's comment log carries two different kinds of thing.  A RECORD is
    one surface rewritten in place, so its text is the present answer and
    says nothing about when that answer became true.  An EVENT is appended
    once and never touched, so a series of them is history.  The stream is
    exactly the second kind, in the order the backend recorded the writes.
    """

    async def test_the_stream_is_exactly_the_posted_events_in_write_order(
        self,
        tracker: TrackerPort,
        server: FakeLinearMcpServer,
        clock: FixtureClock,
    ) -> None:
        """Ordered by the backend's stamp, which the log order is not.

        The four stamps this case states do not rise with the log.  They
        are what the backend records each write as having happened at,
        which is the only write order anything reading a tracker can
        observe — a listing's own order is the vendor's business and its
        default ordering is not creation at all.

        Stated that way for a measured reason: an earlier ordering fixture
        here posted onto a monotonically stamped log, so write order, log
        order and stamp order agreed by construction, and an
        implementation with its whole sort DELETED passed it.
        """
        server.comment_instants = list(EVENT_INSTANTS)
        for event, instant in zip(POSTED_EVENTS, EVENT_INSTANTS, strict=True):
            clock.now = instant
            assert (
                await tracker.post_run_event(issue_key=CLAIMED_ISSUE, event=event)
                == event
            )
        await tracker.post_comment(issue_key=CLAIMED_ISSUE, body="not an event")

        stream = await tracker.lane_run_events(
            issue_key=CLAIMED_ISSUE, lane_key=EVENT_LANE
        )

        assert list(stream) == [PUSHED, GREEN, REFUTED, DISPATCHED]

    async def test_a_record_edited_in_place_is_never_an_event(
        self,
        tracker: TrackerPort,
    ) -> None:
        """A rewritten surface stays a record however recently it moved."""
        await tracker.post_run_event(issue_key=CLAIMED_ISSUE, event=DISPATCHED)
        first = await leased_comment(
            tracker,
            target=CLAIMED_ISSUE,
            marker=RECORD_MARKER,
            body="the first reading",
        )
        edited = await leased_comment(
            tracker,
            target=CLAIMED_ISSUE,
            marker=RECORD_MARKER,
            body="the reading after",
        )
        assert edited.comment_key == first.comment_key
        assert edited.body != first.body

        stream = await tracker.lane_run_events(
            issue_key=CLAIMED_ISSUE, lane_key=EVENT_LANE
        )

        assert list(stream) == [DISPATCHED]

    async def test_a_lanes_stream_holds_only_that_lanes_events(
        self,
        tracker: TrackerPort,
    ) -> None:
        await tracker.post_run_event(issue_key=CLAIMED_ISSUE, event=DISPATCHED)
        await tracker.post_run_event(issue_key=CLAIMED_ISSUE, event=NEIGHBOUR_EVENT)

        assert list(
            await tracker.lane_run_events(issue_key=CLAIMED_ISSUE, lane_key=EVENT_LANE)
        ) == [DISPATCHED]
        assert list(
            await tracker.lane_run_events(
                issue_key=CLAIMED_ISSUE, lane_key=OTHER_EVENT_LANE
            )
        ) == [NEIGHBOUR_EVENT]

    async def test_a_stream_with_nothing_posted_to_it_is_empty(
        self,
        tracker: TrackerPort,
    ) -> None:
        """Scoped to its issue, and an empty read is a successful one."""
        await tracker.post_run_event(issue_key=CLAIMED_ISSUE, event=DISPATCHED)

        assert (
            await tracker.lane_run_events(issue_key=ASSET_ISSUE, lane_key=EVENT_LANE)
            == ()
        )


class TestAThreadedRecordIsNotAnEvent:
    """The reply link decides, not the text — over ``TRACKER_ADAPTERS``.

    A decision record is written as a reply in the thread it answers, and
    the workspace is seeded through the fake MCP server because that is the
    adapters' input: no port write takes a parent, so a threaded comment
    cannot be produced through the surface under test.

    Both cases seed the SAME bytes — the body a real posted event was
    written with, read back off the log — so the only difference between
    them is the reply link.  Nothing else can be what excluded it.
    """

    async def test_a_threaded_record_is_not_an_event(
        self,
        server: FakeLinearMcpServer,
        adapter: TrackerPort,
    ) -> None:
        await adapter.post_run_event(issue_key=CLAIMED_ISSUE, event=DISPATCHED)
        posted = server.comments[-1]
        server.comments.append(
            FakeMcpComment(
                id="comment-threaded",
                issue_id=CLAIMED_ISSUE,
                author=APPROVER,
                body=posted.body,
                created_at=FIXTURE_NOW + timedelta(seconds=5),
                parent_id=posted.id,
            ),
        )

        stream = await adapter.lane_run_events(
            issue_key=CLAIMED_ISSUE, lane_key=EVENT_LANE
        )

        assert list(stream) == [DISPATCHED]

    async def test_the_same_bytes_at_top_level_are_an_event(
        self,
        server: FakeLinearMcpServer,
        adapter: TrackerPort,
    ) -> None:
        """The pair: identical content, unthreaded, and it is in the stream."""
        await adapter.post_run_event(issue_key=CLAIMED_ISSUE, event=DISPATCHED)
        posted = server.comments[-1]
        server.comments.append(
            FakeMcpComment(
                id="comment-top-level",
                issue_id=CLAIMED_ISSUE,
                author=APPROVER,
                body=posted.body,
                created_at=FIXTURE_NOW + timedelta(seconds=5),
                parent_id=None,
            ),
        )

        stream = await adapter.lane_run_events(
            issue_key=CLAIMED_ISSUE, lane_key=EVENT_LANE
        )

        assert list(stream) == [DISPATCHED, DISPATCHED]


class TestTheEditAndTheTransitionAreSeparateWrites:
    """Two writes in one order, so a refused edit leaves the state alone.

    Each of the two touches its own surface and nothing else.  The pair
    matters because the alternative — one act carrying both — cannot be
    ordered and cannot be half-undone: an issue would read as reviewed
    while carrying the text that failed to land.
    """

    async def test_an_edit_moves_no_workflow_state(
        self,
        tracker: TrackerPort,
    ) -> None:
        before = await tracker.read_issue(issue_key=APPROVED_ISSUE)

        edited = await tracker.edit_description(
            target=APPROVED_ISSUE,
            expected=before.body,
            replacement="a body written by its owner",
        )

        assert edited is DescriptionEditResult.EDITED
        after = await tracker.read_issue(issue_key=APPROVED_ISSUE)
        assert after.body == "a body written by its owner"
        assert after.state_name == before.state_name

    async def test_a_transition_rewrites_no_description(
        self,
        tracker: TrackerPort,
    ) -> None:
        before = await tracker.read_issue(issue_key=APPROVED_ISSUE)

        moved = await tracker.set_workflow_state(
            issue_key=APPROVED_ISSUE, stage=LifecycleStage.IN_REVIEW
        )

        assert moved.state_name != before.state_name
        assert moved.body == before.body
        assert (await tracker.read_issue(issue_key=APPROVED_ISSUE)).body == before.body

    async def test_a_refused_edit_leaves_the_state_where_it_was(
        self,
        tracker: TrackerPort,
        tracker_writes: Callable[[], tuple[object, ...]],
    ) -> None:
        """The edit goes first precisely so its refusal can stop the pair."""
        before = await tracker.read_issue(issue_key=APPROVED_ISSUE)
        written = tracker_writes()

        with pytest.raises(StaleWriteError):
            await tracker.edit_description(
                target=APPROVED_ISSUE,
                expected="a body nobody ever wrote",
                replacement="a body written by its owner",
            )

        after = await tracker.read_issue(issue_key=APPROVED_ISSUE)
        assert (after.body, after.state_name) == (before.body, before.state_name)
        assert tracker_writes() == written


class TestApprovalLabelWrites:
    """Admission is the approver's act, and no label write may perform it.

    A run holds leases over surfaces so it can write its own records; the
    member that says a human authorized this work is not one of them.
    Where a configuration spells that member the same as an ordinary
    semantic classification, the port refuses the write rather than
    granting the run its own authorization.
    """

    async def test_a_run_holders_label_write_naming_approval_is_refused(
        self,
        aliasing_tracker: TrackerPort,
        aliasing_writes: Callable[[], tuple[object, ...]],
        server: FakeLinearMcpServer,
    ) -> None:
        """Holding the label surface is authority over labels, not admission.

        The refusal arrives before the backend hears anything at all, not
        merely before it is asked to write: a run that got as far as a
        read has already spent the authority this refusal denies it.
        """
        before = await aliasing_tracker.read_issue(issue_key=CLAIMED_ISSUE)
        await aliasing_tracker.acquire_surfaces(
            surfaces=frozenset({CLAIMED_LABEL_SET}),
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )
        written = aliasing_writes()
        asked = len(server.calls)

        with pytest.raises(ApprovalLabelWriteError) as refused:
            await aliasing_tracker.set_issue_classification(
                issue_key=CLAIMED_ISSUE,
                classification="criterion",
                holder=JOB_A,
            )

        assert len(server.calls) == asked
        assert (refused.value.issue_key, refused.value.classification) == (
            CLAIMED_ISSUE,
            "criterion",
        )
        after = await aliasing_tracker.read_issue(issue_key=CLAIMED_ISSUE)
        assert after.issue_labels == before.issue_labels
        assert aliasing_writes() == written

    async def test_the_same_write_under_no_holder_at_all_is_refused_too(
        self,
        aliasing_tracker: TrackerPort,
        aliasing_writes: Callable[[], tuple[object, ...]],
        server: FakeLinearMcpServer,
    ) -> None:
        """The refusal is about the member, not about who asked for it."""
        before = await aliasing_tracker.read_issue(issue_key=CLAIMED_ISSUE)
        written = aliasing_writes()
        asked = len(server.calls)

        with pytest.raises(ApprovalLabelWriteError):
            await aliasing_tracker.set_issue_classification(
                issue_key=CLAIMED_ISSUE,
                classification="criterion",
            )

        assert len(server.calls) == asked
        after = await aliasing_tracker.read_issue(issue_key=CLAIMED_ISSUE)
        assert after.issue_labels == before.issue_labels
        assert aliasing_writes() == written

    async def test_a_classification_that_is_not_the_approval_member_still_writes(
        self,
        tracker: TrackerPort,
    ) -> None:
        """The ordinary vocabulary is untouched: only the collision refuses."""
        await tracker.acquire_surfaces(
            surfaces=frozenset({CLAIMED_LABEL_SET}),
            holder=JOB_A,
            lease_seconds=LEASE_SECONDS,
        )

        updated = await tracker.set_issue_classification(
            issue_key=CLAIMED_ISSUE,
            classification="criterion",
            holder=JOB_A,
        )

        assert "criterion" in updated.issue_labels

    @pytest.mark.parametrize(
        ("dialled_member", "written_state", "subject", "resting"),
        [
            (
                "approved",
                QueueState.APPROVED,
                ASSET_ISSUE,
                frozenset({QueueState.DONE}),
            ),
            (
                "done",
                QueueState.DONE,
                CLAIMED_ISSUE,
                frozenset({QueueState.APPROVED}),
            ),
        ],
        ids=["approved", "done"],
    )
    @pytest.mark.parametrize("implementation", sorted(TRACKER_IMPLEMENTATIONS))
    async def test_a_queue_state_write_naming_approval_is_refused_before_any_request(
        self,
        implementation: str,
        dialled_member: str,
        written_state: QueueState,
        subject: str,
        resting: frozenset[QueueState],
        server: FakeLinearMcpServer,
        clock: FixtureClock,
    ) -> None:
        """The queue vocabulary cannot express an approval either.

        Which write is refused follows the label the operation loaded, not the
        name of the queue member: the second arm dials the approval label onto
        ``done``, and there it is the DONE write that is refused while an
        APPROVED write is an ordinary move. A port that read the member's own
        name instead would refuse the wrong one of the two.

        The subject is an issue in another queue member, so the write is a real
        move: a subject already carrying the approved label would return
        unchanged before any refusal could be asked for, and moving it out of
        that member first would itself revoke the approval under this mapping.
        """
        port = await queue_aliasing_port(
            implementation, server, clock, member=dialled_member
        )
        writes = observed_writes(port, server)
        before = await port.read_issue(issue_key=subject)
        labels = list(server.issues[subject].labels)
        written = writes()
        asked = len(server.calls)

        with pytest.raises(ApprovalLabelWriteError) as refused:
            await port.set_queue_state(issue_key=subject, state=written_state)

        assert len(server.calls) == asked
        assert (refused.value.issue_key, refused.value.classification) == (
            subject,
            written_state.value,
        )
        after = await port.read_issue(issue_key=subject)
        assert after.issue_labels == before.issue_labels
        assert after.queue_states == before.queue_states == resting
        assert writes() == written
        assert server.issues[subject].labels == labels

    async def test_a_queue_state_that_is_not_the_approval_member_still_writes(
        self,
        queue_aliasing_tracker: TrackerPort,
    ) -> None:
        """Only the colliding member refuses; the rest of the queue is untouched."""
        updated = await queue_aliasing_tracker.set_queue_state(
            issue_key=ASSET_ISSUE, state=QueueState.PROPOSED
        )

        assert updated.queue_states == frozenset({QueueState.PROPOSED})


class TestPrincipalAuthoredBodies:
    """What the machine may rewrite is what the tracker records as its own.

    The attribution is the backend's, never a judgement about the prose:
    a body reads as a principal's because the workspace says a member
    other than this writer put it there.  A replacement of such a body is
    refused at the port, and the bytes standing there do not move.
    """

    async def test_a_body_another_member_wrote_reads_as_principal_authored(
        self,
        tracker: TrackerPort,
    ) -> None:
        assert (
            await tracker.read_surface_authorship(surface=PRINCIPAL_DESCRIPTION)
        ).authorship is SurfaceAuthorship.PRINCIPAL_AUTHORED

    async def test_a_body_this_writer_is_attributed_reads_as_machine_authored(
        self,
        tracker: TrackerPort,
    ) -> None:
        assert (
            await tracker.read_surface_authorship(surface=CLAIMED_DESCRIPTION)
        ).authorship is SurfaceAuthorship.MACHINE_AUTHORED

    async def test_authorship_is_not_answered_for_a_body_this_port_cannot_write(
        self,
        tracker: TrackerPort,
    ) -> None:
        """A comment carries its own attribution and its own write seam."""
        with pytest.raises(ValueError):
            await tracker.read_surface_authorship(surface=MARKER_A)

    async def test_replacing_a_principal_authored_body_is_refused_intact(
        self,
        tracker: TrackerPort,
        tracker_writes: Callable[[], tuple[object, ...]],
    ) -> None:
        """The anchor matches, the lease is irrelevant, and nothing moves."""
        before = await tracker.read_issue(issue_key=ASSET_ISSUE)
        written = tracker_writes()

        with pytest.raises(PrincipalAuthoredSurfaceError) as refused:
            await tracker.edit_description(
                target=ASSET_ISSUE,
                expected=before.body,
                replacement="a body this writer would have preferred",
            )

        assert refused.value.scope_key == ASSET_ISSUE
        after = await tracker.read_issue(issue_key=ASSET_ISSUE)
        assert after.body == before.body
        assert tracker_writes() == written

    async def test_a_raw_body_update_cannot_replace_it_either(
        self,
        tracker: TrackerPort,
        tracker_writes: Callable[[], tuple[object, ...]],
    ) -> None:
        """The refusal is at the write, not at one caller's way in."""
        before = await tracker.read_issue(issue_key=ASSET_ISSUE)
        written = tracker_writes()

        with pytest.raises(PrincipalAuthoredSurfaceError):
            await tracker.update_issue(issue_key=ASSET_ISSUE, body="replaced")

        after = await tracker.read_issue(issue_key=ASSET_ISSUE)
        assert after.body == before.body
        assert tracker_writes() == written

    async def test_a_replay_that_replaces_nothing_is_not_a_replacement(
        self,
        tracker: TrackerPort,
        tracker_writes: Callable[[], tuple[object, ...]],
    ) -> None:
        """Writing the bytes already there takes nothing from their author."""
        before = await tracker.read_issue(issue_key=ASSET_ISSUE)
        written = tracker_writes()

        result = await tracker.edit_description(
            target=ASSET_ISSUE,
            expected=before.body,
            replacement=before.body,
        )

        assert result is DescriptionEditResult.UNCHANGED
        assert (await tracker.read_issue(issue_key=ASSET_ISSUE)).body == before.body
        assert tracker_writes() == written

    async def test_a_machine_authored_body_is_still_replaceable(
        self,
        tracker: TrackerPort,
    ) -> None:
        """The refusal reaches one surface, not every description write."""
        before = await tracker.read_issue(issue_key=CLAIMED_ISSUE)

        result = await tracker.edit_description(
            target=CLAIMED_ISSUE,
            expected=before.body,
            replacement="a body this writer wrote and may rewrite",
        )

        assert result is DescriptionEditResult.EDITED
        assert (await tracker.read_issue(issue_key=CLAIMED_ISSUE)).body == (
            "a body this writer wrote and may rewrite"
        )


#: A criterion sub-issue this writer authored and still owes: the one row
#: the addressing property reads a key off and writes back through.
OWED_CRITERION = "FIX-6"
#: A second criterion under the SAME parent, so "one surface per criterion
#: sub-issue" is a statement about two addresses rather than about one.
OTHER_CRITERION = "FIX-7"


def criterion_sub_issue(
    key: str,
    *,
    title: str,
    status: str = "Todo",
    status_type: str = "unstarted",
    created_by: str | None = None,
) -> FakeMcpIssue:
    """One criterion sub-issue of the claimable issue, in vendor shape.

    Stated once because several workspaces seed the same shape: the body
    carries the rows the criterion family read parses, the parentage is
    what makes the member a criterion of ``CLAIMED_ISSUE``, and the
    classification is the configured criterion label.  ``created_by``
    unset leaves the workspace attributing the body to the dialled
    account, so an ordinary description edit is the ordinary write rather
    than the principal-authorship refusal; a case about a principal's
    words names the member that wrote them.
    """
    return FakeMcpIssue(
        id=key,
        title=title,
        description=(
            f"**Check:** the check {key} states\n\n"
            f"**Do:** the build {key} names\n\n"
            "**Evidence:**\n"
        ),
        parent_id=CLAIMED_ISSUE,
        status=status,
        status_type=status_type,
        labels=[ISSUE_LABELS["criterion"]],
        created_by=created_by,
        created_at=FIXTURE_NOW - timedelta(days=2),
        updated_at=FIXTURE_NOW,
    )


def criterion_surface(key: str) -> WritableSurface:
    """The one address a criterion's body, state and labels share.

    Composed rather than listed, because a case about two criteria needs
    two of these and a subtree address exists nowhere to compose from.
    """
    return WritableSurface(
        kind=SurfaceKind.CRITERION_SUB_ISSUE,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=key),
    )


class TestACriterionKeyReadThroughThePortAddressesItsWrites:
    """A criterion sub-issue key the port hands out is the key its writes take.

    The three writes the lane's own evaluator makes, in the order and with
    the signatures it makes them: the Evidence row edit under the read-back
    body, the finish, and the move back.  Every assertion is a read-back of
    state, never a look at a write log: the two implementations spell their
    state names differently and log a move back differently, and
    ``state_kind`` and ``body`` are what both answer the same.
    """

    @pytest.fixture
    def server(self, clock: FixtureClock) -> FakeLinearMcpServer:
        """The fixture workspace plus the one criterion sub-issue it lacks.

        Dialled here rather than in the shared workspace so no module built
        on that workspace sees an extra issue.  ``created_by`` stays unset:
        the workspace then attributes the body to the dialled account, and
        the bare description edit production makes is the ordinary write
        rather than the principal-authorship refusal.
        """
        value = fixture_server(clock=clock)
        value.issues[OWED_CRITERION] = criterion_sub_issue(
            OWED_CRITERION, title="a criterion this writer owes"
        )
        return value

    async def test_a_criterion_key_read_through_the_port_is_taken_by_its_three_writes(
        self,
        tracker: TrackerPort,
    ) -> None:
        """The key the read hands out addresses all three writes that follow.

        Nothing is composed from a title or a body here: the key the family
        read reported is handed straight to the Evidence edit, to the finish
        and to the move back, and each write's effect is read back off the
        same key.  An implementation whose read answered with a key its own
        writes did not accept would fail on the first of the three.
        """
        rows = await tracker.read_criteria(issue_key=CLAIMED_ISSUE)
        assert {row.issue_key for row in rows} == {OWED_CRITERION}
        row = next(r for r in rows if r.issue_key == OWED_CRITERION)
        assert row.state_kind is WorkflowStateKind.UNSTARTED
        stamped = row.body.replace(
            "**Evidence:**", f"**Evidence:** graded at {'a' * 40}"
        )

        edited = await tracker.edit_description(
            target=row.issue_key, expected=row.body, replacement=stamped
        )

        assert edited is DescriptionEditResult.EDITED
        assert (
            await tracker.read_issue(issue_key=row.issue_key)
        ).state_kind is WorkflowStateKind.UNSTARTED

        await tracker.set_workflow_state(
            issue_key=row.issue_key, stage=LifecycleStage.DONE
        )

        finished = await tracker.read_issue(issue_key=row.issue_key)
        assert finished.state_kind is WorkflowStateKind.COMPLETED
        assert finished.body == stamped

        await tracker.reset_criterion_pending(expected=finished, holder=None)

        after = await tracker.read_issue(issue_key=row.issue_key)
        assert after.state_kind is WorkflowStateKind.UNSTARTED
        assert after.body == stamped
        assert {
            r.issue_key for r in await tracker.read_criteria(issue_key=CLAIMED_ISSUE)
        } == {OWED_CRITERION}

    async def test_a_repeated_finish_and_a_repeated_move_back_write_nothing(
        self,
        tracker: TrackerPort,
        tracker_writes: Callable[[], tuple[object, ...]],
    ) -> None:
        """A write already standing is not written again, on either half.

        The write-log half of this is decisive on the adapter, which sends a
        state save for every move it makes, and vacuous on the double for
        the move back, which enters no write tuple at all; the read-back
        half holds on both.  This is the re-tick rider of the addressing
        property, not a statement about how a grading reaches the row.
        """
        rows = await tracker.read_criteria(issue_key=CLAIMED_ISSUE)
        row = next(r for r in rows if r.issue_key == OWED_CRITERION)
        stamped = row.body.replace(
            "**Evidence:**", f"**Evidence:** graded at {'b' * 40}"
        )
        await tracker.edit_description(
            target=row.issue_key, expected=row.body, replacement=stamped
        )
        await tracker.set_workflow_state(
            issue_key=row.issue_key, stage=LifecycleStage.DONE
        )
        finished = await tracker.read_issue(issue_key=row.issue_key)

        written = tracker_writes()
        await tracker.set_workflow_state(
            issue_key=row.issue_key, stage=LifecycleStage.DONE
        )

        assert tracker_writes() == written
        assert (
            await tracker.read_issue(issue_key=row.issue_key)
        ).state_kind is WorkflowStateKind.COMPLETED

        await tracker.reset_criterion_pending(expected=finished, holder=None)
        unstarted = await tracker.read_issue(issue_key=row.issue_key)
        assert unstarted.state_kind is WorkflowStateKind.UNSTARTED

        written = tracker_writes()
        await tracker.reset_criterion_pending(expected=unstarted, holder=None)

        assert tracker_writes() == written
        replayed = await tracker.read_issue(issue_key=row.issue_key)
        assert replayed.state_kind is WorkflowStateKind.UNSTARTED
        assert replayed.body == stamped

    @pytest.mark.parametrize(
        "holder",
        [pytest.param("", id="blank"), pytest.param("another-job", id="foreign")],
    )
    async def test_a_supplied_holder_nobody_granted_refuses_the_move_back(
        self,
        tracker: TrackerPort,
        tracker_writes: Callable[[], tuple[object, ...]],
        holder: str,
    ) -> None:
        """A holder that was supplied is a holder, and it has to hold live.

        The absent holder of the case above is the single writer's own act
        over its own lane and consults no grant; a supplied one that nobody
        granted is refused on every implementation, with nothing written and
        the criterion left finished.
        """
        rows = await tracker.read_criteria(issue_key=CLAIMED_ISSUE)
        row = next(r for r in rows if r.issue_key == OWED_CRITERION)
        await tracker.set_workflow_state(
            issue_key=row.issue_key, stage=LifecycleStage.DONE
        )
        finished = await tracker.read_issue(issue_key=row.issue_key)
        written = tracker_writes()

        with pytest.raises(SurfaceLeaseError):
            await tracker.reset_criterion_pending(expected=finished, holder=holder)

        assert tracker_writes() == written
        assert (
            await tracker.read_issue(issue_key=row.issue_key)
        ).state_kind is WorkflowStateKind.COMPLETED


class TestIndependentlyHeldSurfaces:
    """Addresses of one issue, and criteria of one parent, are held apart.

    Two properties of the address vocabulary, each stated over holders
    that ARE supplied: an issue's description and its two marker-keyed
    comments are three surfaces, so three holders take them at once and
    none of them may write the others; and two criteria under one parent
    are two surfaces, so the holder of one moves that criterion's state
    and is refused on its sibling's.  Nothing here acquires a subtree or
    a parent's criterion-child set: there is no such grant to lean on.
    """

    @pytest.fixture
    def server(self, clock: FixtureClock) -> FakeLinearMcpServer:
        """The fixture workspace plus the two criteria these cases address.

        Dialled here rather than in the shared workspace for the reason
        the addressing property's is: a member added there would move
        answers no case in this class is about.
        """
        value = fixture_server(clock=clock)
        value.issues[OWED_CRITERION] = criterion_sub_issue(
            OWED_CRITERION, title="a criterion this writer owes"
        )
        value.issues[OTHER_CRITERION] = criterion_sub_issue(
            OTHER_CRITERION, title="a criterion of the same parent"
        )
        return value

    async def test_one_issues_three_surfaces_are_held_apart_and_each_writes_its_own(
        self,
        tracker: TrackerPort,
        tracker_writes: Callable[[], tuple[object, ...]],
    ) -> None:
        """Three grants over one issue, and six refusals across them.

        The three acquisitions all succeed, which is what "independently
        held" is; each holder then writes ITS surface, so the refusals
        that follow are not vacuous.  Every holder is then tried against
        every surface it does not hold — six attempts, the complete
        symmetric statement — and each is refused naming the surface
        written to and the holder that has it, with the write log
        untouched over all six.
        """
        owners = {CLAIMED_DESCRIPTION: JOB_A, MARKER_A: JOB_B, MARKER_B: JOB_C}
        for surface, owner in owners.items():
            granted = await tracker.acquire_surfaces(
                surfaces=frozenset({surface}),
                holder=owner,
                lease_seconds=LEASE_SECONDS,
            )
            assert granted.surfaces == frozenset({surface})

        opening = await tracker.read_issue(issue_key=CLAIMED_ISSUE)
        edited = await tracker.edit_description(
            target=CLAIMED_ISSUE,
            expected=opening.body,
            replacement="a body the description's own holder replaced",
            authorization=DescriptionWriteAuthority(
                holder=JOB_A, surface=CLAIMED_DESCRIPTION
            ),
        )
        assert edited is DescriptionEditResult.EDITED
        for surface, owner in ((MARKER_A, JOB_B), (MARKER_B, JOB_C)):
            assert surface.marker is not None
            posted = await tracker.upsert_comment(
                target=CLAIMED_ISSUE,
                marker=surface.marker,
                body=f"a record {owner} wrote under its own marker",
                holder=owner,
            )
            assert posted.body.endswith(f"a record {owner} wrote under its own marker")

        standing = await tracker.read_issue(issue_key=CLAIMED_ISSUE)
        assert standing.body == "a body the description's own holder replaced"
        comments = {
            comment.comment_key: comment.body
            for comment in await tracker.list_comments(issue_key=CLAIMED_ISSUE)
        }
        for owner in (JOB_B, JOB_C):
            own_records = [
                body
                for body in comments.values()
                if body.endswith(f"a record {owner} wrote under its own marker")
            ]
            assert len(own_records) == 1, (owner, own_records)

        async def replace_the_body(holder: str) -> object:
            return await tracker.edit_description(
                target=CLAIMED_ISSUE,
                expected=standing.body,
                replacement=f"a body {holder} does not hold the address for",
                authorization=DescriptionWriteAuthority(
                    holder=holder, surface=CLAIMED_DESCRIPTION
                ),
            )

        def replace_the_comment(marker: str) -> Callable[[str], Awaitable[object]]:
            async def write(holder: str) -> object:
                return await tracker.upsert_comment(
                    target=CLAIMED_ISSUE,
                    marker=marker,
                    body=f"a record {holder} does not hold the address for",
                    holder=holder,
                )

            return write

        rows: tuple[tuple[WritableSurface, Callable[[str], Awaitable[object]]], ...] = (
            (CLAIMED_DESCRIPTION, replace_the_body),
            (MARKER_A, replace_the_comment("A")),
            (MARKER_B, replace_the_comment("B")),
        )
        written = tracker_writes()

        for surface, write in rows:
            for foreign in sorted(set(owners.values()) - {owners[surface]}):
                with pytest.raises(SurfaceLeaseError) as refused:
                    await write(foreign)
                assert (
                    refused.value.surface_kind,
                    refused.value.scope_key,
                    refused.value.marker,
                    refused.value.current_holder,
                ) == (
                    surface.kind.value,
                    surface.ref.key,
                    surface.marker,
                    owners[surface],
                )

        assert tracker_writes() == written
        after = await tracker.read_issue(issue_key=CLAIMED_ISSUE)
        assert after.body == standing.body
        assert {
            comment.comment_key: comment.body
            for comment in await tracker.list_comments(issue_key=CLAIMED_ISSUE)
        } == comments

    async def test_a_criterion_holder_moves_its_own_state_and_not_another_criterions(
        self,
        tracker: TrackerPort,
        tracker_writes: Callable[[], tuple[object, ...]],
    ) -> None:
        """One surface per criterion, and no grant over the parent's subtree.

        Both criteria are finished through the single-writer state move,
        then each is taken by a holder of its own — both granted, because
        two criteria of one parent are two addresses.  The holder of the
        first moves that criterion back and is refused on the second's,
        naming the second's key and its holder; the second's own holder
        then moves it back, so the refusal was about the address and not
        about the call.
        """
        for key in (OWED_CRITERION, OTHER_CRITERION):
            await tracker.set_workflow_state(issue_key=key, stage=LifecycleStage.DONE)
        owed = await tracker.read_issue(issue_key=OWED_CRITERION)
        other = await tracker.read_issue(issue_key=OTHER_CRITERION)
        assert owed.state_kind is WorkflowStateKind.COMPLETED
        assert other.state_kind is WorkflowStateKind.COMPLETED
        for key, holder in ((OWED_CRITERION, JOB_A), (OTHER_CRITERION, JOB_B)):
            granted = await tracker.acquire_surfaces(
                surfaces=frozenset({criterion_surface(key)}),
                holder=holder,
                lease_seconds=LEASE_SECONDS,
            )
            assert granted.holder == holder

        moved = await tracker.reset_criterion_pending(expected=owed, holder=JOB_A)
        assert moved.state_kind is WorkflowStateKind.UNSTARTED

        written = tracker_writes()
        with pytest.raises(SurfaceLeaseError) as refused:
            await tracker.reset_criterion_pending(expected=other, holder=JOB_A)

        assert (
            refused.value.surface_kind,
            refused.value.scope_key,
            refused.value.current_holder,
        ) == (SurfaceKind.CRITERION_SUB_ISSUE.value, OTHER_CRITERION, JOB_B)
        assert tracker_writes() == written
        assert (
            await tracker.read_issue(issue_key=OTHER_CRITERION)
        ).state_kind is WorkflowStateKind.COMPLETED

        symmetric = await tracker.reset_criterion_pending(expected=other, holder=JOB_B)

        assert symmetric.state_kind is WorkflowStateKind.UNSTARTED

    async def test_a_parents_criterion_child_grant_moves_no_criterion_under_it(
        self,
        tracker: TrackerPort,
        tracker_writes: Callable[[], tuple[object, ...]],
    ) -> None:
        """The parent's criterion-child grant is not a grant over its criteria.

        The parent's child-set address grants creating a criterion under
        it and nothing else: the criterion already there is its own
        address, held by no one, so moving it back under the child-set
        holder is refused naming that criterion and no holder, with
        nothing written and the criterion still finished.  One surface
        per criterion sub-issue, not one lease per subtree.
        """
        await tracker.set_workflow_state(
            issue_key=OTHER_CRITERION, stage=LifecycleStage.DONE
        )
        other = await tracker.read_issue(issue_key=OTHER_CRITERION)
        assert other.state_kind is WorkflowStateKind.COMPLETED
        granted = await tracker.acquire_surfaces(
            surfaces=frozenset({CLAIMED_CRITERION_CHILD_SET}),
            holder=JOB_C,
            lease_seconds=LEASE_SECONDS,
        )
        assert granted.holder == JOB_C

        written = tracker_writes()
        with pytest.raises(SurfaceLeaseError) as refused:
            await tracker.reset_criterion_pending(expected=other, holder=JOB_C)

        assert (
            refused.value.surface_kind,
            refused.value.scope_key,
            refused.value.current_holder,
        ) == (SurfaceKind.CRITERION_SUB_ISSUE.value, OTHER_CRITERION, None)
        assert tracker_writes() == written
        assert (
            await tracker.read_issue(issue_key=OTHER_CRITERION)
        ).state_kind is WorkflowStateKind.COMPLETED


#: The two parameters through which a caller can name the holder it writes
#: under: one of them names the identity directly, the other carries the
#: grant the identity holds.
HOLDER_PARAMETERS = frozenset({"holder", "authorization"})


def supplied_holder_writes() -> frozenset[str]:
    """Every port write that can supply a holder, read off the port itself.

    Derived rather than listed for the reason the adoption check's own
    surface is: a write that grows a holder parameter and is left out of a
    table written by hand would be the one nothing ever refused.
    """
    return frozenset(
        method
        for method in artifact_writes(TrackerPort)
        if HOLDER_PARAMETERS & set(parameters(method, TrackerPort))
    )


def single_writer_writes() -> frozenset[str]:
    """Those whose holder is OPTIONAL: an absent one is the writer's own act.

    Read off the signature's default rather than declared per row, so a
    seam that stops accepting an absent holder — or starts — moves this
    set by itself instead of drifting from a flag beside it.
    """
    return frozenset(
        method
        for method in supplied_holder_writes()
        if any(
            signature(getattr(TrackerPort, method)).parameters[name].default is None
            for name in HOLDER_PARAMETERS & set(parameters(method, TrackerPort))
        )
    )


#: A child and a peer under the claimable issue: the graph row addresses the
#: child's own graph surface, and the criterion and split rows create under
#: the parent's own creation surfaces.
GRAPH_CHILD = "FIX-9"
GRAPH_PEER = "FIX-10"
#: The two addresses for creating membership under one issue, which grant no
#: edit of any child that already exists there.
CLAIMED_CRITERION_CHILD_SET = WritableSurface(
    kind=SurfaceKind.CRITERION_CHILD_SET,
    ref=CLAIMED_REF,
)
CLAIMED_SPLIT_SET = WritableSurface(
    kind=SurfaceKind.ISSUE_SPLIT_SET,
    ref=CLAIMED_REF,
)
CHILD_GRAPH = WritableSurface(
    kind=SurfaceKind.ISSUE_GRAPH,
    ref=ScopeRef(kind=ScopeKind.ISSUE, key=GRAPH_CHILD),
)
#: The alarm record one row writes, and the marker its own address is
#: composed from: the seam derives the whole address from the subject and
#: the signal, so the row names it the way the writer does.
ALARM = RunAlarm(
    subject=SurfaceSubject(
        scope_key="fixture-scope",
        lane_key=EVENT_LANE,
        surface=MARKER_A,
    ),
    signal=AlarmSignal.SURFACE_CONTENDED,
    readings=(
        AlarmReading(source_ref="fixture/holders", value=CountEvidence(value=2)),
    ),
    bound=AlarmBound(
        config_field="run_alarm_max_surface_holders",
        configured_value=1,
        observed_value=2,
    ),
    raised_at_sha="fixture-head",
    raised_by="the-raising-job",
)
ALARM_SURFACE = run_alarm_surface(
    issue_key=APPROVED_ISSUE,
    marker=run_alarm_marker(
        subject=ALARM.subject, signal=ALARM.signal, marker_prefixes=MARKER_PREFIXES
    ),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class HolderWrite:
    """One port write, the address it asserts, and how a case makes it.

    ``write`` takes the holder to supply — ``None`` for the single-writer
    write — so one statement of a seam serves both the refusing arms and
    the arm that supplies nobody. ``effect`` is what the seam MOVES, read
    back through the port, so "it wrote" and "it wrote nothing" are both
    readings rather than a trusted return value. ``prepare`` is for a seam
    whose precondition is not the workspace's resting state; a seam that
    will not fit is a finding, never a row left out.
    """

    surface: WritableSurface
    write: Callable[[TrackerPort, str | None], Awaitable[object]]
    effect: Callable[[TrackerPort], Awaitable[object]]
    prepare: Callable[[TrackerPort], Awaitable[None]] | None = None


async def _write_a_comment(tracker: TrackerPort, holder: str | None) -> object:
    return await tracker.upsert_comment(
        target=CLAIMED_ISSUE,
        marker="A",
        body="a record written under the marker's own grant",
        holder=holder,
    )


async def _write_a_description(tracker: TrackerPort, holder: str | None) -> object:
    current = await tracker.read_issue(issue_key=CLAIMED_ISSUE)
    return await tracker.edit_description(
        target=CLAIMED_ISSUE,
        expected=current.body,
        replacement="a body written under the description's own grant",
        authorization=None
        if holder is None
        else DescriptionWriteAuthority(holder=holder, surface=CLAIMED_DESCRIPTION),
    )


async def _write_a_classification(tracker: TrackerPort, holder: str | None) -> object:
    return await tracker.set_issue_classification(
        issue_key=CLAIMED_ISSUE, classification="criterion", holder=holder
    )


async def _move_a_criterion_back(tracker: TrackerPort, holder: str | None) -> object:
    finished = await tracker.read_issue(issue_key=OWED_CRITERION)
    return await tracker.reset_criterion_pending(expected=finished, holder=holder)


async def _change_a_graph(tracker: TrackerPort, holder: str | None) -> object:
    assert holder is not None
    proposed = GraphProposal.model_validate(
        {
            "kind": "graph",
            "issue_id": GRAPH_CHILD,
            "changes": [{"kind": "priority", "priority": IssuePriority.HIGH.value}],
        }
    )
    # The child's own ancestry is part of the snapshot the seam verifies,
    # so the parent is read with it; only the child is affected, so only
    # the child's graph address is a grant this write needs.
    return await tracker.update_issue_graph(
        issue_key=GRAPH_CHILD,
        expected=tuple(
            [
                graph_snapshot(await tracker.read_issue(issue_key=key))
                for key in (CLAIMED_ISSUE, GRAPH_CHILD)
            ]
        ),
        changes=proposed.changes,
        holder=holder,
    )


async def _create_a_split(tracker: TrackerPort, holder: str | None) -> object:
    assert holder is not None
    source = await tracker.read_issue(issue_key=CLAIMED_ISSUE)
    return await tracker.create_split_if_absent(
        source_key=CLAIMED_ISSUE,
        deliverable_key="fixture/split",
        title="a split of the claimable issue",
        body="the independent child specification",
        holder=holder,
        expected=(graph_snapshot(source),),
    )


async def _create_a_criterion(tracker: TrackerPort, holder: str | None) -> object:
    assert holder is not None
    return await tracker.create_criterion_if_absent(
        parent_key=CLAIMED_ISSUE,
        title="a criterion this table mints",
        check="the minted criterion states this check and no other",
        do="mint it under the parent's own criterion-child grant",
        holder=holder,
    )


async def _record_an_alarm(tracker: TrackerPort, holder: str | None) -> object:
    assert holder is not None
    return await tracker.record_run_alarm(
        issue_key=APPROVED_ISSUE, alarm=ALARM, holder=holder
    )


async def _the_issues_comments(tracker: TrackerPort) -> object:
    return tuple(
        comment.body for comment in await tracker.list_comments(issue_key=CLAIMED_ISSUE)
    )


async def _the_issues_body(tracker: TrackerPort) -> object:
    return (await tracker.read_issue(issue_key=CLAIMED_ISSUE)).body


async def _the_issues_labels(tracker: TrackerPort) -> object:
    return (await tracker.read_issue(issue_key=CLAIMED_ISSUE)).issue_labels


async def _the_criterions_state(tracker: TrackerPort) -> object:
    return (await tracker.read_issue(issue_key=OWED_CRITERION)).state_kind


async def _the_childs_priority(tracker: TrackerPort) -> object:
    return (await tracker.read_issue(issue_key=GRAPH_CHILD)).priority


async def _the_issues_split_children(tracker: TrackerPort) -> object:
    return tuple(
        child.issue_key
        for child in await tracker.read_split_children(source_key=CLAIMED_ISSUE)
    )


async def _the_issues_criteria(tracker: TrackerPort) -> object:
    return frozenset(
        row.issue_key for row in await tracker.read_criteria(issue_key=CLAIMED_ISSUE)
    )


async def _the_recorded_alarm(tracker: TrackerPort) -> object:
    return await tracker.read_run_alarm(
        issue_key=APPROVED_ISSUE, subject=ALARM.subject, signal=ALARM.signal
    )


#: Every supplied-holder port write, with the address it refuses at. The
#: KEYS are compared with the derivation above, so this table cannot fall
#: behind the port without a case going red.
SUPPLIED_HOLDER_WRITES: Mapping[str, HolderWrite] = {
    "upsert_comment": HolderWrite(
        surface=MARKER_A, write=_write_a_comment, effect=_the_issues_comments
    ),
    "edit_description": HolderWrite(
        surface=CLAIMED_DESCRIPTION,
        write=_write_a_description,
        effect=_the_issues_body,
    ),
    "set_issue_classification": HolderWrite(
        surface=CLAIMED_LABEL_SET,
        write=_write_a_classification,
        effect=_the_issues_labels,
    ),
    "reset_criterion_pending": HolderWrite(
        surface=criterion_surface(OWED_CRITERION),
        write=_move_a_criterion_back,
        effect=_the_criterions_state,
    ),
    "update_issue_graph": HolderWrite(
        surface=CHILD_GRAPH, write=_change_a_graph, effect=_the_childs_priority
    ),
    "create_split_if_absent": HolderWrite(
        surface=CLAIMED_SPLIT_SET,
        write=_create_a_split,
        effect=_the_issues_split_children,
    ),
    "create_criterion_if_absent": HolderWrite(
        surface=CLAIMED_CRITERION_CHILD_SET,
        write=_create_a_criterion,
        effect=_the_issues_criteria,
    ),
    "record_run_alarm": HolderWrite(
        surface=ALARM_SURFACE, write=_record_an_alarm, effect=_the_recorded_alarm
    ),
}


class TestSuppliedHolderWrites:
    """Every port write that supplies a holder refuses one it does not hold.

    One statement over the whole holder-taking surface of the port, with
    the surface itself derived from the port's own members: a write that
    gains a holder and no row here reds the first case, and a row for a
    write that no longer takes one reds it too.

    The refusal is asserted before any backend MUTATION rather than before
    any backend call: a backend with no conditional write cannot know a
    surface is unheld without reading the log it is recorded on. The one
    refusal that needs no read at all — the approval member — is asserted
    before any request, in its own class above.
    """

    @pytest.fixture
    def server(self, clock: FixtureClock) -> FakeLinearMcpServer:
        """The fixture workspace plus the members these rows address.

        A finished criterion for the move back, and a child and a peer of
        the claimable issue for the graph and split rows. Seeded here, so
        no module built on the shared workspace sees them.
        """
        value = fixture_server(clock=clock)
        value.issues[OWED_CRITERION] = criterion_sub_issue(
            OWED_CRITERION,
            title="a criterion this writer owes",
            status="Done",
            status_type="completed",
        )
        for key in (GRAPH_CHILD, GRAPH_PEER):
            value.issues[key] = FakeMcpIssue(
                id=key,
                title=f"an ordinary child {key}",
                description=f"the body of {key}",
                parent_id=CLAIMED_ISSUE,
                status="Todo",
                status_type="unstarted",
                created_at=FIXTURE_NOW - timedelta(days=2),
                updated_at=FIXTURE_NOW,
            )
        return value

    def test_the_table_is_the_derived_holder_taking_write_surface(self) -> None:
        """The rows ARE the port's holder-taking writes, neither more nor less."""
        assert supplied_holder_writes()
        assert frozenset(SUPPLIED_HOLDER_WRITES) == supplied_holder_writes()
        assert single_writer_writes() <= supplied_holder_writes()
        assert single_writer_writes()

    @pytest.mark.parametrize("method", sorted(SUPPLIED_HOLDER_WRITES))
    @pytest.mark.parametrize("standing", ["unheld", "expired", "foreign"])
    async def test_a_supplied_holder_that_does_not_hold_is_refused_with_nothing_written(
        self,
        tracker: TrackerPort,
        tracker_writes: Callable[[], tuple[object, ...]],
        clock: FixtureClock,
        method: str,
        standing: str,
    ) -> None:
        """Three ways not to hold a surface, and one refusal for all of them.

        Nobody holding it, this holder's own grant lapsed, and a rival
        holding it live: each raises the typed lease error carrying the
        whole address as primitives, and the no-lease and lapsed arms name
        no current holder rather than inventing one. The observed write
        log is taken AFTER the acquisition, so the marker writes the
        arrangement itself makes are inside the baseline and the refusal
        is shown to add nothing to it; what the seam would have moved is
        read back as well, so the refusal is also shown to have left the
        surface where its reader found it.
        """
        row = SUPPLIED_HOLDER_WRITES[method]
        if row.prepare is not None:
            await row.prepare(tracker)
        held = frozenset({row.surface})
        if standing == "expired":
            await tracker.acquire_surfaces(
                surfaces=held, holder=JOB_A, lease_seconds=LEASE_SECONDS
            )
            clock.advance(seconds=LEASE_SECONDS + 1)
        elif standing == "foreign":
            await tracker.acquire_surfaces(
                surfaces=held, holder=JOB_B, lease_seconds=LEASE_SECONDS
            )
        before = await row.effect(tracker)
        written = tracker_writes()

        with pytest.raises(SurfaceLeaseError) as refused:
            await row.write(tracker, JOB_A)

        assert (
            refused.value.surface_kind,
            refused.value.scope_kind,
            refused.value.scope_key,
            refused.value.marker,
        ) == (
            row.surface.kind.value,
            row.surface.ref.kind.value,
            row.surface.ref.key,
            row.surface.marker,
        )
        assert refused.value.current_holder == (
            JOB_B if standing == "foreign" else None
        )
        assert tracker_writes() == written
        assert await row.effect(tracker) == before

    @pytest.mark.parametrize("method", sorted(single_writer_writes()))
    async def test_a_write_that_supplies_no_holder_consults_no_lease(
        self,
        tracker: TrackerPort,
        method: str,
    ) -> None:
        """An absent holder is the single writer's own act, and it writes.

        The surface is held LIVE by a rival while this write is made, so
        the day a holder-less write starts consulting a lease this is the
        case that reds: an arm that left the surface unheld would still
        pass. The write lands, read back off what the seam moves rather
        than off a write log — the two implementations log a state move
        differently — because an absent holder is by design not an unheld
        one. The rival's grant is untouched by it: the lease records who
        is writing, and this write did not claim to be that writer.
        """
        row = SUPPLIED_HOLDER_WRITES[method]
        if row.prepare is not None:
            await row.prepare(tracker)
        await tracker.acquire_surfaces(
            surfaces=frozenset({row.surface}),
            holder=JOB_B,
            lease_seconds=LEASE_SECONDS,
        )
        before = await row.effect(tracker)

        await row.write(tracker, None)

        assert await row.effect(tracker) != before
        with pytest.raises(SurfaceLeaseError) as refused:
            await tracker.acquire_surfaces(
                surfaces=frozenset({row.surface}),
                holder=JOB_A,
                lease_seconds=LEASE_SECONDS,
            )
        assert refused.value.current_holder == JOB_B


#: A criterion sub-issue whose body a person wrote.  Its own key, because a
#: criterion's words are answerable at the criterion's own address and not
#: at its parent's.
PRINCIPAL_CRITERION = "FIX-8"


class TestAPrincipalAuthoredCriterionBody:
    """A grant over a criterion is authority to write, not to overwrite.

    The grant and the authorship answer two different questions, and the
    criterion's own surface is where both are asked: holding it says no
    other writer may move these words, and the attribution says these
    words are not this writer's to move.  The surface under test is the
    criterion's complete address, which the amendment write-back addresses
    when it edits a native criterion body, so the refusal is the one that
    production meets there.
    """

    @pytest.fixture
    def server(self, clock: FixtureClock) -> FakeLinearMcpServer:
        """The fixture workspace plus one criterion a person wrote.

        ``created_by`` names a member that is not the dialled account, so
        both implementations read the body as principal-authored from the
        workspace's own attribution rather than from a second statement of
        the fixture.  Seeded here so no module built on the shared
        workspace sees the member.
        """
        value = fixture_server(clock=clock)
        value.issues[PRINCIPAL_CRITERION] = criterion_sub_issue(
            PRINCIPAL_CRITERION,
            title="a criterion a person wrote",
            created_by=BYSTANDER,
        )
        return value

    async def test_a_principal_authored_criterion_body_is_refused_under_its_own_grant(
        self,
        tracker: TrackerPort,
        tracker_writes: Callable[[], tuple[object, ...]],
    ) -> None:
        """The holder holds the criterion, and the words are still not its own.

        Every reason to refuse except the authorship is removed first: the
        grant is over this criterion's own complete surface and it is live,
        the authority addresses that surface and that target, and the
        expected body is the one just read, so no staleness and no missing
        holder can stand in for the refusal under test.  The error names
        the criterion's kind and its own key — not the parent's, and not
        the issue-description kind — because that is the address whose
        author is being answered for.

        The body is read back byte-identical and the observed write log is
        unmoved, so the refusal precedes the save rather than undoing it,
        and no body-write record is left in the holder's name: a refused
        replacement must not make this writer one of the holders that
        replaced these words.
        """
        surface = criterion_surface(PRINCIPAL_CRITERION)
        await tracker.acquire_surfaces(
            surfaces=frozenset({surface}), holder=JOB_A, lease_seconds=LEASE_SECONDS
        )
        before = await tracker.read_issue(issue_key=PRINCIPAL_CRITERION)
        assert (
            await tracker.read_surface_authorship(surface=surface)
        ).authorship is SurfaceAuthorship.PRINCIPAL_AUTHORED
        written = tracker_writes()

        with pytest.raises(PrincipalAuthoredSurfaceError) as refused:
            await tracker.edit_description(
                target=PRINCIPAL_CRITERION,
                expected=before.body,
                replacement=before.body.replace(
                    "**Evidence:**", "**Evidence:** a grading this writer would add"
                ),
                authorization=DescriptionWriteAuthority(holder=JOB_A, surface=surface),
            )

        assert (refused.value.surface_kind, refused.value.scope_key) == (
            SurfaceKind.CRITERION_SUB_ISSUE.value,
            PRINCIPAL_CRITERION,
        )
        after = await tracker.read_issue(issue_key=PRINCIPAL_CRITERION)
        assert after.body == before.body
        assert tracker_writes() == written
        assert (await tracker.read_surface_authorship(surface=surface)).holders == ()


#: The criterion sub-issue the provenance property addresses, and the body
#: it starts from.  Seeded into that property's own workspace rather than
#: into the shared one: every ordinary case reads its scan, its scope and
#: its listings off the shared workspace, and a member added there would
#: move answers nothing in this property is about.
PROVENANCE_CRITERION = "FIX-4"
PROVENANCE_CRITERION_BODY = (
    "**Check:** a stated predicate\n\n**Do:** stated guidance\n\n**Evidence:**\n"
)

#: The two holders the ordered pair is read back as.
FIRST_WRITER = "first-writing-job"
SECOND_WRITER = "second-writing-job"

#: One address per answerable kind, so the property is stated once over the
#: set and the kind under test is the only thing that varies.
PROVENANCE_SURFACES: dict[SurfaceKind, WritableSurface] = {
    SurfaceKind.ISSUE_DESCRIPTION: CLAIMED_DESCRIPTION,
    SurfaceKind.CRITERION_SUB_ISSUE: WritableSurface(
        kind=SurfaceKind.CRITERION_SUB_ISSUE,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=PROVENANCE_CRITERION),
    ),
}


def unanswerable_surface(kind: SurfaceKind) -> WritableSurface:
    """One address of *kind*, under that kind's own addressing rule.

    Built per kind rather than listed, because a container surface needs a
    container reference and a marker-keyed comment needs its marker: an
    address table written out by hand would go stale the moment the
    vocabulary grows a tenth member.
    """
    if kind is SurfaceKind.MARKER_COMMENT:
        return MARKER_A
    if kind in {
        SurfaceKind.CONTAINER_DESCRIPTION,
        SurfaceKind.CONTAINER_STATUS_UPDATE,
    }:
        return WritableSurface(
            kind=kind, ref=ScopeRef(kind=ScopeKind.PROJECT, key="fixture-project")
        )
    return WritableSurface(kind=kind, ref=CLAIMED_REF)


async def held_body_write(
    tracker: TrackerPort, *, surface: WritableSurface, holder: str, replacement: str
) -> DescriptionEditResult:
    """One holder taking the surface, replacing the body, and standing down.

    The lease is released after each write, so the pair the property reads
    back cannot have come from a lease still standing: a record outlives
    the grant that authorized it, which is the whole point of recording it.
    """
    surfaces = frozenset({surface})
    await tracker.acquire_surfaces(
        surfaces=surfaces, holder=holder, lease_seconds=LEASE_SECONDS
    )
    try:
        current = await tracker.read_issue(issue_key=surface.ref.key)
        return await tracker.edit_description(
            target=surface.ref.key,
            expected=current.body,
            replacement=replacement,
            authorization=DescriptionWriteAuthority(holder=holder, surface=surface),
        )
    finally:
        await tracker.release_surfaces(surfaces=surfaces, holder=holder)


class TestSurfaceWriteProvenance:
    """Who has written a body, answered from records and never from a stamp.

    The question is asked of exactly the surfaces whose body this port can
    replace, and it is asked of every one of them: the parameters are read
    off the answerable set itself, so widening that set without widening
    the answer fails here rather than passing unnoticed.  The other kinds
    are unanswerable by design and are refused, not answered.
    """

    @pytest.fixture(params=sorted(TRACKER_IMPLEMENTATIONS))
    async def provenance_tracker(
        self,
        request: pytest.FixtureRequest,
        server: FakeLinearMcpServer,
        clock: FixtureClock,
    ) -> TrackerPort:
        """Every implementation, over a workspace holding a criterion body.

        The shared workspace holds no criterion sub-issue, and one of the
        two answerable kinds has no address without one.  Seeded here so
        the member exists before the port is built, which is what carries
        it into the double as well as the adapter.
        """
        server.issues[PROVENANCE_CRITERION] = FakeMcpIssue(
            id=PROVENANCE_CRITERION,
            title="a criterion of the claimable issue",
            description=PROVENANCE_CRITERION_BODY,
            parent_id=CLAIMED_ISSUE,
            labels=[ISSUE_LABELS["criterion"]],
            status="Todo",
            status_type="unstarted",
            created_at=FIXTURE_NOW - timedelta(days=2),
            updated_at=FIXTURE_NOW,
        )
        factory = TRACKER_IMPLEMENTATIONS[request.param]
        port = factory(TrackerWorkspace(server=server, clock=clock))
        return await port if isawaitable(port) else port

    @pytest.mark.parametrize("kind", sorted(BODY_AUTHORSHIP_SURFACES))
    async def test_two_holders_writing_one_body_read_back_as_that_ordered_pair(
        self,
        provenance_tracker: TrackerPort,
        kind: SurfaceKind,
    ) -> None:
        """The distinct holders, in the order the backend placed them."""
        surface = PROVENANCE_SURFACES[kind]
        for holder in (FIRST_WRITER, SECOND_WRITER):
            assert (
                await held_body_write(
                    provenance_tracker,
                    surface=surface,
                    holder=holder,
                    replacement=f"a body {holder} put there",
                )
                is DescriptionEditResult.EDITED
            )

        answer = await provenance_tracker.read_surface_authorship(surface=surface)

        assert answer.holders == (FIRST_WRITER, SECOND_WRITER)
        assert answer.authorship is SurfaceAuthorship.MACHINE_AUTHORED

    @pytest.mark.parametrize("kind", sorted(BODY_AUTHORSHIP_SURFACES))
    async def test_a_holder_writing_twice_is_named_once(
        self,
        provenance_tracker: TrackerPort,
        kind: SurfaceKind,
    ) -> None:
        """Three writes by two holders answer the pair, at first occurrence."""
        surface = PROVENANCE_SURFACES[kind]
        for round_number, holder in enumerate(
            (FIRST_WRITER, SECOND_WRITER, FIRST_WRITER)
        ):
            await held_body_write(
                provenance_tracker,
                surface=surface,
                holder=holder,
                replacement=f"a body {holder} put there, round {round_number}",
            )

        answer = await provenance_tracker.read_surface_authorship(surface=surface)

        assert answer.holders == (FIRST_WRITER, SECOND_WRITER)

    @pytest.mark.parametrize("kind", sorted(BODY_AUTHORSHIP_SURFACES))
    async def test_a_change_that_is_not_a_body_write_adds_no_holder(
        self,
        provenance_tracker: TrackerPort,
        kind: SurfaceKind,
    ) -> None:
        """A move that touches no body adds nobody, and moves no digest.

        Stated through the port's own body digest rather than through a
        vendor stamp, so both arms answer the same question: the digest is
        what changes when and only when a body changes.
        """
        surface = PROVENANCE_SURFACES[kind]
        for holder in (FIRST_WRITER, SECOND_WRITER):
            await held_body_write(
                provenance_tracker,
                surface=surface,
                holder=holder,
                replacement=f"a body {holder} put there",
            )
        before = await provenance_tracker.read_issue_revision(issue_key=surface.ref.key)
        written = await provenance_tracker.read_surface_authorship(surface=surface)

        await provenance_tracker.set_workflow_state(
            issue_key=surface.ref.key, stage=LifecycleStage.IN_PROGRESS
        )

        after = await provenance_tracker.read_issue_revision(issue_key=surface.ref.key)
        assert after.body_digest == before.body_digest
        assert (
            (await provenance_tracker.read_surface_authorship(surface=surface)).holders
            == written.holders
            == (FIRST_WRITER, SECOND_WRITER)
        )

    @pytest.mark.parametrize("kind", sorted(BODY_AUTHORSHIP_SURFACES))
    async def test_an_unwritten_body_names_no_holder(
        self,
        provenance_tracker: TrackerPort,
        kind: SurfaceKind,
    ) -> None:
        """A body no holder wrote answers the empty set, not the creator."""
        answer = await provenance_tracker.read_surface_authorship(
            surface=PROVENANCE_SURFACES[kind]
        )

        assert answer.holders == ()

    @pytest.mark.parametrize("kind", sorted(BODY_AUTHORSHIP_SURFACES))
    async def test_a_single_writer_body_write_names_no_holder(
        self,
        provenance_tracker: TrackerPort,
        kind: SurfaceKind,
    ) -> None:
        """A write that names no holder has no holder to record."""
        surface = PROVENANCE_SURFACES[kind]
        before = await provenance_tracker.read_issue(issue_key=surface.ref.key)

        result = await provenance_tracker.edit_description(
            target=surface.ref.key,
            expected=before.body,
            replacement="a body the single writer put there",
        )

        assert result is DescriptionEditResult.EDITED
        assert (
            await provenance_tracker.read_surface_authorship(surface=surface)
        ).holders == ()

    @pytest.mark.parametrize(
        "kind", sorted(set(SurfaceKind) - BODY_AUTHORSHIP_SURFACES)
    )
    async def test_provenance_is_not_answered_for_a_body_this_port_cannot_write(
        self,
        provenance_tracker: TrackerPort,
        kind: SurfaceKind,
    ) -> None:
        """Every kind outside the answerable set is refused, not answered."""
        with pytest.raises(ValueError):
            await provenance_tracker.read_surface_authorship(
                surface=unanswerable_surface(kind)
            )
