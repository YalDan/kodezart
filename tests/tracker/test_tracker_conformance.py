"""Port-level conformance suite — written once, run against every adapter.

Passing this module IS the definition of conforming.  Nothing here names a
vendor, a tool, or a vendor identifier format: an adapter that needed a
special case in this file would not be substitutable, which is the failure
this suite exists to catch.

Every case runs over the in-process fake MCP server.  There is no live
workspace anywhere in this module and none may be introduced.
"""

import asyncio
from collections.abc import Callable
from datetime import timedelta

import pytest

from kodezart.core.errors import TrackerEnsureConflictError
from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import DuplicateWorkRefError, SurfaceLeaseError
from kodezart.domain.run_event_stream import render_run_event, run_event_marker
from kodezart.domain.tracker_writes import marked_comment_body
from kodezart.types.domain.branch import BaseInput, BaseSpec, WorkRef, WorkRefRole
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.run_alarm import (
    AlarmBound,
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    AlarmSubjectKind,
    RunAlarm,
    surface_alarm_member_id,
)
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.run_event_record import RunEventRecord
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, SurfaceLease, WritableSurface
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
from tests.fakes import FakeLinearMcpServer, FakeMcpComment
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
    SCOPE_DIAGNOSIS,
    TEAM_IDENTIFIERS,
    FixtureClock,
)
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
#: Holders are shaped as the identities the contract names: a lease is held
#: under the writing run's job id.
JOB_A = "job-a"
JOB_B = "job-b"
#: The two holder vocabularies, side by side: a deployment's process
#: identity holds a fire claim, a run's job id holds a write lease.
PROCESS_HOLDER = "kodezart-process"
JOB_HOLDER = "job-17"


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


class TestRunAlarmRecords:
    """P1 — an alarm's identity is the complete ``(subject, signal)`` pair.

    An implementation addressing the record by a lane key alone passes the
    single-subject round trip and fails here: two writable surfaces on one
    issue in one lane under one signal collapse onto one record, and a
    scope subject has no lane key to address at all.
    """

    SCOPE_KEY = "fixture-scope"
    LANE_KEY = CLAIMED_ISSUE

    def _surface_subject(self, surface: WritableSurface) -> AlarmSubject:
        return AlarmSubject(
            kind=AlarmSubjectKind.SURFACE,
            scope_key=self.SCOPE_KEY,
            lane_key=self.LANE_KEY,
            member_id=surface_alarm_member_id(surface),
        )

    def _contended(self, surface: WritableSurface, *, holder: str) -> RunAlarm:
        return RunAlarm(
            subject=self._surface_subject(surface),
            signal=AlarmSignal.SURFACE_CONTENDED,
            readings=(
                AlarmReading(source_ref=f"{holder}/lease", value=holder),
                AlarmReading(source_ref=f"{holder}/holders", value="2"),
            ),
            bound=AlarmBound(
                config_field="run_alarm_max_surface_holders",
                configured_value=1,
                observed_value=2,
            ),
            raised_at_sha="c" * 40,
            raised_by="fixture-supervisor",
        )

    def _scope_alarm(self) -> RunAlarm:
        return RunAlarm(
            subject=AlarmSubject(
                kind=AlarmSubjectKind.SCOPE,
                scope_key=self.SCOPE_KEY,
            ),
            signal=AlarmSignal.RULINGS_OUTPACE_CLOSURES,
            readings=(
                AlarmReading(source_ref="rulings", value="7"),
                AlarmReading(source_ref="closures", value="1"),
            ),
            bound=None,
            raised_at_sha="d" * 40,
            raised_by="fixture-supervisor",
        )

    async def test_an_alarm_round_trips_field_for_field_with_its_reading_order(
        self,
        tracker: TrackerPort,
    ) -> None:
        alarm = self._contended(MARKER_A, holder=JOB_A)

        await tracker.record_run_alarm(issue_key=CLAIMED_ISSUE, alarm=alarm)

        stored = await tracker.read_run_alarm(
            issue_key=CLAIMED_ISSUE,
            subject=alarm.subject,
            signal=alarm.signal,
        )
        assert stored == alarm
        assert stored is not None
        assert [reading.source_ref for reading in stored.readings] == [
            f"{JOB_A}/lease",
            f"{JOB_A}/holders",
        ]

    async def test_recording_the_identical_alarm_again_writes_no_second_record(
        self,
        tracker: TrackerPort,
    ) -> None:
        alarm = self._contended(MARKER_A, holder=JOB_A)
        before = await tracker.list_comments(issue_key=CLAIMED_ISSUE)

        await tracker.record_run_alarm(issue_key=CLAIMED_ISSUE, alarm=alarm)
        once = await tracker.list_comments(issue_key=CLAIMED_ISSUE)
        await tracker.record_run_alarm(issue_key=CLAIMED_ISSUE, alarm=alarm)
        twice = await tracker.list_comments(issue_key=CLAIMED_ISSUE)

        assert len(once) == len(before) + 1
        assert list(twice) == list(once)
        assert (
            await tracker.read_run_alarm(
                issue_key=CLAIMED_ISSUE,
                subject=alarm.subject,
                signal=alarm.signal,
            )
            == alarm
        )

    async def test_two_surfaces_in_one_lane_under_one_signal_are_two_records(
        self,
        tracker: TrackerPort,
    ) -> None:
        """The collision D5's lane-keyed marker could not tell apart."""
        first = self._contended(MARKER_A, holder=JOB_A)
        second = self._contended(MARKER_B, holder=JOB_B)
        before = await tracker.list_comments(issue_key=CLAIMED_ISSUE)

        await tracker.record_run_alarm(issue_key=CLAIMED_ISSUE, alarm=first)
        await tracker.record_run_alarm(issue_key=CLAIMED_ISSUE, alarm=second)

        assert first.subject != second.subject
        assert first.signal is second.signal
        assert (
            await tracker.read_run_alarm(
                issue_key=CLAIMED_ISSUE,
                subject=first.subject,
                signal=first.signal,
            )
            == first
        )
        assert (
            await tracker.read_run_alarm(
                issue_key=CLAIMED_ISSUE,
                subject=second.subject,
                signal=second.signal,
            )
            == second
        )
        after = await tracker.list_comments(issue_key=CLAIMED_ISSUE)
        assert len(after) == len(before) + 2

    async def test_a_scope_subject_carrying_no_lane_key_round_trips(
        self,
        tracker: TrackerPort,
    ) -> None:
        alarm = self._scope_alarm()
        assert alarm.subject.lane_key is None

        await tracker.record_run_alarm(issue_key=CLAIMED_ISSUE, alarm=alarm)

        assert (
            await tracker.read_run_alarm(
                issue_key=CLAIMED_ISSUE,
                subject=alarm.subject,
                signal=alarm.signal,
            )
            == alarm
        )

    async def test_an_address_no_record_carries_reads_as_none(
        self,
        tracker: TrackerPort,
    ) -> None:
        """No latest-record fallback: a miss is a miss, not the last alarm."""
        recorded = self._contended(MARKER_A, holder=JOB_A)
        await tracker.record_run_alarm(issue_key=CLAIMED_ISSUE, alarm=recorded)

        assert (
            await tracker.read_run_alarm(
                issue_key=CLAIMED_ISSUE,
                subject=self._surface_subject(MARKER_B),
                signal=AlarmSignal.SURFACE_CONTENDED,
            )
            is None
        )
        assert (
            await tracker.read_run_alarm(
                issue_key=CLAIMED_ISSUE,
                subject=recorded.subject,
                signal=AlarmSignal.RECORD_SUPERSEDED,
            )
            is None
        )

    async def test_a_record_is_scoped_to_the_issue_that_carries_it(
        self,
        tracker: TrackerPort,
    ) -> None:
        alarm = self._contended(MARKER_A, holder=JOB_A)
        await tracker.record_run_alarm(issue_key=CLAIMED_ISSUE, alarm=alarm)

        assert (
            await tracker.read_run_alarm(
                issue_key=APPROVED_ISSUE,
                subject=alarm.subject,
                signal=alarm.signal,
            )
            is None
        )


class TestRunEventStream:
    """P3 — the lane's stream is what was POSTED, in the order it landed.

    The split the port draws between its two comment writes: a post adds
    one entry to an order, an upsert keeps one fact current. An
    implementation that enumerated the log would report the run's own
    records as things that happened to the lane, and would report a
    threaded reply — an answer to a comment — as a second event.
    """

    LANE = "lane-seven"
    EVENT_MARKER = run_event_marker(lane_key=LANE, marker_prefixes=MARKER_PREFIXES)

    def _event(self, kind: RunEventKind, subject_ref: str) -> RunEventRecord:
        return RunEventRecord(lane_key=self.LANE, kind=kind, subject_ref=subject_ref)

    def _stream(self) -> tuple[RunEventRecord, ...]:
        """Four posts, one of them a repeat: a stream is an order, not a set."""
        return (
            self._event(RunEventKind.FIRST_PUSH, "kodezart/lane-seven"),
            self._event(RunEventKind.GATE_RED, "check-chain"),
            self._event(RunEventKind.GATE_GREEN, "check-chain"),
            self._event(RunEventKind.FIRST_PUSH, "kodezart/lane-seven"),
        )

    def _alarm(self, *, observed: int) -> RunAlarm:
        return RunAlarm(
            subject=AlarmSubject(
                kind=AlarmSubjectKind.LANE,
                scope_key="fixture-scope",
                lane_key=self.LANE,
            ),
            signal=AlarmSignal.TALLY_UNMOVED,
            readings=(AlarmReading(source_ref="tally", value=str(observed)),),
            bound=None,
            raised_at_sha="e" * 40,
            raised_by="fixture-supervisor",
        )

    async def test_the_stream_is_exactly_the_posted_events_in_write_order(
        self,
        tracker: TrackerPort,
    ) -> None:
        posted = self._stream()

        for event in posted:
            await tracker.post_run_event(issue_key=CLAIMED_ISSUE, event=event)

        read = await tracker.run_events(issue_key=CLAIMED_ISSUE, lane_key=self.LANE)
        assert tuple(read) == posted

    async def test_a_record_edited_in_place_never_appears_in_the_stream(
        self,
        tracker: TrackerPort,
    ) -> None:
        posted = self._stream()

        await tracker.record_run_alarm(
            issue_key=CLAIMED_ISSUE, alarm=self._alarm(observed=3)
        )
        for event in posted:
            await tracker.post_run_event(issue_key=CLAIMED_ISSUE, event=event)
        await tracker.record_run_alarm(
            issue_key=CLAIMED_ISSUE, alarm=self._alarm(observed=4)
        )

        read = await tracker.run_events(issue_key=CLAIMED_ISSUE, lane_key=self.LANE)
        assert tuple(read) == posted
        # The record IS on the same log and IS about the same lane: the
        # stream excludes it because it is edited, not because it is absent.
        log = await tracker.list_comments(issue_key=CLAIMED_ISSUE)
        assert len(log) == len(posted) + 1

    async def test_the_stream_is_scoped_to_its_lane_and_its_issue(
        self,
        tracker: TrackerPort,
    ) -> None:
        event = self._event(RunEventKind.FIRST_PUSH, "kodezart/lane-seven")
        await tracker.post_run_event(issue_key=CLAIMED_ISSUE, event=event)

        assert (
            await tracker.run_events(issue_key=CLAIMED_ISSUE, lane_key="lane-other")
            == ()
        )
        assert (
            await tracker.run_events(issue_key=APPROVED_ISSUE, lane_key=self.LANE) == ()
        )

    async def test_a_threaded_record_is_not_an_event(
        self,
        server: FakeLinearMcpServer,
        adapter: TrackerPort,
    ) -> None:
        """A reply carrying a whole, well-formed event body is still a reply.

        Seeded through the fake MCP server, which is the adapters' input:
        the port offers no reply write, and a decision recorded under a
        thread is exactly the shape that reaches this read in the field.
        The paired top-level post proves the exclusion is the parent link
        and not the body.
        """
        threaded = self._event(RunEventKind.ESCALATION_RAISED, "escalation-4")
        body = marked_comment_body(
            marker=self.EVENT_MARKER, body=render_run_event(event=threaded)
        )
        server.comments.append(
            FakeMcpComment(
                id="comment-threaded-decision",
                issue_id=CLAIMED_ISSUE,
                author=APPROVER,
                body=body,
                created_at=FIXTURE_NOW - timedelta(days=1),
                parent_id="comment-some-thread-root",
            ),
        )
        posted = self._stream()
        for event in posted:
            await adapter.post_run_event(issue_key=CLAIMED_ISSUE, event=event)

        read = await adapter.run_events(issue_key=CLAIMED_ISSUE, lane_key=self.LANE)

        assert tuple(read) == posted

    async def test_the_same_body_posted_top_level_is_an_event(
        self,
        server: FakeLinearMcpServer,
        adapter: TrackerPort,
    ) -> None:
        """The paired positive for the reply exclusion."""
        top_level = self._event(RunEventKind.ESCALATION_RAISED, "escalation-4")
        server.comments.append(
            FakeMcpComment(
                id="comment-top-level-event",
                issue_id=CLAIMED_ISSUE,
                author=APPROVER,
                body=marked_comment_body(
                    marker=self.EVENT_MARKER, body=render_run_event(event=top_level)
                ),
                created_at=FIXTURE_NOW - timedelta(days=1),
            ),
        )

        read = await adapter.run_events(issue_key=CLAIMED_ISSUE, lane_key=self.LANE)

        assert tuple(read) == (top_level,)
