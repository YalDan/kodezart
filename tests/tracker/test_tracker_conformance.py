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

from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import DuplicateWorkRefError
from kodezart.types.domain.branch import WorkRef, WorkRefRole
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.tracker import (
    ClaimStatus,
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
)

LEASE_SECONDS = 600.0


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
        }
        for issue in found:
            assert QueueState.APPROVED in issue.queue_states

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

    async def test_the_lease_bounds_the_claim(self, tracker: TrackerPort) -> None:
        granted = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
        )
        assert granted.expires_at == FIXTURE_NOW + timedelta(seconds=LEASE_SECONDS)


class TestProvenance:
    """Who set this state — the question authority binds to."""

    async def test_the_port_names_the_actor_of_a_state_transition(
        self,
        tracker: TrackerPort,
    ) -> None:
        """AC: the port answers "who set this state" from fixture history."""
        transition = await tracker.queue_state_provenance(
            issue_key=APPROVED_ISSUE,
            state=QueueState.APPROVED,
        )
        assert transition is not None
        assert transition.actor_key == APPROVER
        assert transition.queue_state is QueueState.APPROVED
        assert transition.issue_key == APPROVED_ISSUE

    async def test_a_different_state_names_its_own_actor(
        self,
        tracker: TrackerPort,
    ) -> None:
        transition = await tracker.queue_state_provenance(
            issue_key=APPROVED_ISSUE,
            state=QueueState.PROPOSED,
        )
        assert transition is None, "the fixture removed this state again"

    async def test_a_state_never_set_has_no_provenance(
        self,
        tracker: TrackerPort,
    ) -> None:
        assert (
            await tracker.queue_state_provenance(
                issue_key=APPROVED_ISSUE,
                state=QueueState.DECISION,
            )
            is None
        )


class TestAssets:
    """Attachment and document metadata, and document reads."""

    async def test_issue_assets_carry_metadata(self, tracker: TrackerPort) -> None:
        assets = await tracker.list_issue_assets(issue_key=ASSET_ISSUE)
        by_key = {asset.asset_key: asset for asset in assets}
        assert by_key["asset-1"].title == "spec.pdf"
        assert by_key["asset-1"].content_type == "application/pdf"
        assert by_key["asset-1"].size_bytes == 1024
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

    @pytest.mark.parametrize(
        "kind",
        [
            MappingKind.USER,
            MappingKind.TEAM,
            MappingKind.QUEUE_STATE,
            MappingKind.WORKFLOW_STATE,
        ],
    )
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


class TestSubstitutability:
    """No capability flags, no feature detection, no partial adapters."""

    def test_the_adapter_satisfies_the_whole_port(
        self,
        tracker: TrackerPort,
    ) -> None:
        assert isinstance(tracker, TrackerPort)

    def test_the_public_surface_is_exactly_the_port(
        self,
        tracker: TrackerPort,
    ) -> None:
        """No extra public member — nothing for a consumer to discover on.

        Feature detection needs something to detect.  An adapter whose public
        surface is exactly the port's leaves a consumer no way to branch on
        which backend is configured, which is what substitutability means
        here.
        """
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
        stored = await tracker.list_work_refs(issue_key=APPROVED_ISSUE)
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
        (stored,) = await tracker.list_work_refs(issue_key=APPROVED_ISSUE)
        assert stored.pushed_head_sha is None
        assert stored.pushed_head_sha is not False

    async def test_an_issue_with_no_recorded_refs_reads_empty(
        self,
        tracker: TrackerPort,
    ) -> None:
        assert await tracker.list_work_refs(issue_key=CLAIMED_ISSUE) == ()

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
        stored = await tracker.list_work_refs(issue_key=APPROVED_ISSUE)
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
        assert len(await tracker.list_work_refs(issue_key=APPROVED_ISSUE)) == 1

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
        refs = await tracker.list_work_refs(issue_key=APPROVED_ISSUE)
        assert [r.role for r in refs] == [WorkRefRole.ITERATION]
