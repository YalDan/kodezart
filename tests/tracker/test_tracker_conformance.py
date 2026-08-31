"""Port-level conformance suite — written once, run against every adapter.

Passing this module IS the definition of conforming.  Nothing here names a
vendor, a tool, or a vendor identifier format: an adapter that needed a
special case in this file would not be substitutable, which is the failure
this suite exists to catch.

Every case runs over the in-process fake MCP server.  There is no live
workspace anywhere in this module and none may be introduced.
"""

import asyncio
from datetime import timedelta

import pytest

from kodezart.core.errors import TrackerEnsureConflictError
from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import DuplicateWorkRefError
from kodezart.types.domain.branch import BaseInput, BaseSpec, WorkRef, WorkRefRole
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.tracker import (
    INSTATABLE_MAPPING_KINDS,
    ClaimStatus,
    EnsureAction,
    IssuePriority,
    IssueQuery,
    IssueRelationKind,
    MappingKind,
    MappingRef,
    WorkflowStateKind,
    is_open,
)
from tests.tracker.conftest import (
    APPROVED_ISSUE,
    APPROVER,
    ASSET_ISSUE,
    BYSTANDER,
    CLAIMED_ISSUE,
    DOCUMENT_CONTENT,
    DOCUMENT_KEY,
    FIXTURE_NOW,
    FOREIGN_ISSUE,
    TEAM_IDENTIFIERS,
)

LEASE_SECONDS = 600.0
TEAM = TEAM_IDENTIFIERS["engineering"]


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
        """AC: two simultaneous claimants -> one wins, the loser sees LOST."""
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
        assert statuses.count(ClaimStatus.LOST) == 1

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

    #: A container no fixture value is defined in, so a refusal here is
    #: about the DECLARED container disagreeing rather than about a name.
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

    async def test_a_value_the_workspace_holds_elsewhere_refuses_and_writes_nothing(
        self,
        tracker: TrackerPort,
    ) -> None:
        """R7(c): an ensure that would ALTER a definition is a typed error."""
        identifier = "queue:conformance-contested"
        await tracker.ensure_mappings(refs=[self._ref(identifier, TEAM)])

        with pytest.raises(TrackerEnsureConflictError) as caught:
            await tracker.ensure_mappings(
                refs=[self._ref(identifier, self.OTHER_CONTAINER)],
            )

        assert identifier in caught.value.entry
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
                refs=[self._ref(contested, self.OTHER_CONTAINER), follower],
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

    def _ref(self, title: str, identifier: str | None = None) -> MappingRef:
        return MappingRef(
            kind=MappingKind.DOCUMENT,
            name=title,
            identifier=identifier,
        )

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
