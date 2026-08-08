"""One dispatch pass, end to end, over the in-process fake tracker.

No live workspace, no live remote, no model.  The dispatcher is written
against ``TrackerPort`` alone, and this module proves it: the fixtures are
domain objects and no vendor name appears anywhere below.
"""

import asyncio
import re
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import structlog

from kodezart.domain.dispatch import DOMAIN_PRIORITY_ORDER
from kodezart.services.fire_context import FireContextAssembler
from kodezart.services.fire_dispatcher import FireDispatcher
from kodezart.types.domain.dispatch import DispatchOutcome, ExclusionClause
from kodezart.types.domain.job import JobState
from kodezart.types.domain.operation import (
    CheckStep,
    DocumentEntry,
    DocumentSystem,
    Initiative,
    LifecycleStage,
    OperationConfig,
    Principal,
    PrincipalRole,
    QueueState,
    RecordDestination,
    RepoEntry,
)
from kodezart.types.domain.tracker import IssuePriority, WorkflowStateKind
from tests.fakes import (
    FIXTURE_EPOCH,
    FakeDeliveryProbe,
    FakeJobQueue,
    FakeTracker,
    approved_by,
    make_tracker_issue,
)

APPROVER = "the-approver"
IMPOSTOR = "not-the-approver"
REPO_URL = "https://example.invalid/owner/repo"
LANE = "tracker"
HOLDER = "pass-a"
LEASE_SECONDS = 600.0
PAGE_SIZE = 50
ASSET_MAX_COUNT = 20
ASSET_MAX_BYTES = 262144
ASSET_FETCH_TIMEOUT_SECONDS = 30.0

# Raw values as the vendor encodes priority: 0 is "no priority" and would
# sort FIRST under an ascending numeric sort.  The fixture is stated in raw
# form so the mapping is what is under test, not the fixture's own opinion.
RAW_PRIORITY_FIXTURE: dict[str, tuple[int, IssuePriority]] = {
    "RAW-NONE": (0, IssuePriority.NONE),
    "RAW-URGENT": (1, IssuePriority.URGENT),
    "RAW-HIGH": (2, IssuePriority.HIGH),
    "RAW-MEDIUM": (3, IssuePriority.MEDIUM),
    "RAW-LOW": (4, IssuePriority.LOW),
}


def operation_config() -> OperationConfig:
    return OperationConfig(
        operation_name="fixture",
        workspace="fixture-workspace",
        principals=[
            Principal(tracker_user=APPROVER, role=PrincipalRole.APPROVER),
            Principal(tracker_user=IMPOSTOR, role=PrincipalRole.PRINCIPAL),
        ],
        agent_identities=[],
        teams={"engineering": "fixture-team"},
        queue_states={member.value: f"queue:{member.value}" for member in QueueState},
        workflow_states={
            LifecycleStage.IN_PROGRESS: "In Progress",
            LifecycleStage.IN_REVIEW: "In Review",
            LifecycleStage.DONE: "Done",
        },
        repos=[
            RepoEntry(
                url=REPO_URL,
                check_commands=[CheckStep(name="check", command="make check")],
            )
        ],
        documents={
            "checkpoint": DocumentEntry(
                system=DocumentSystem.TRACKER,
                id="doc-1",
            ),
        },
        records={
            "run_log": RecordDestination(
                system=DocumentSystem.KNOWLEDGE,
                id="record-1",
                append_only=True,
            ),
        },
        knowledge={},
        endpoints={},
        initiatives=[Initiative(id="init-1")],
    )


def dispatcher(
    tracker: FakeTracker,
    *,
    queue: FakeJobQueue | None = None,
    delivery: FakeDeliveryProbe | None = None,
    holder: str = HOLDER,
    draw: object = None,
) -> tuple[FireDispatcher, FakeJobQueue, FakeDeliveryProbe]:
    """A dispatcher plus the doubles a test asserts against."""
    the_queue = queue or FakeJobQueue()
    the_delivery = delivery or FakeDeliveryProbe()
    kwargs: dict[str, object] = {
        "tracker": tracker,
        "queue": the_queue,
        "registry": the_queue,
        "delivery": the_delivery,
        "operation": operation_config(),
        "repo_url": REPO_URL,
        "lane": LANE,
        "holder": holder,
        "claim_lease_seconds": LEASE_SECONDS,
        "query_page_size": PAGE_SIZE,
        "assembler": FireContextAssembler(
            tracker=tracker,
            max_count=ASSET_MAX_COUNT,
            max_bytes=ASSET_MAX_BYTES,
            fetch_timeout_seconds=ASSET_FETCH_TIMEOUT_SECONDS,
        ),
    }
    if draw is not None:
        kwargs["draw"] = draw
    return FireDispatcher(**kwargs), the_queue, the_delivery  # type: ignore[arg-type]


class TestClauseDrivenExclusion:
    """Each clause excludes, and the report names which one."""

    async def test_an_eligible_issue_is_enqueued(self) -> None:
        tracker = FakeTracker(
            issues=[make_tracker_issue("K-1")],
            provenance=dict([approved_by("K-1", APPROVER)]),
        )
        fire, queue, _ = dispatcher(tracker)
        report = await fire.run_pass()
        assert report.outcome is DispatchOutcome.fire_enqueued
        assert report.claimed_issue_key == "K-1"
        assert len(queue.submissions) == 1
        assert queue.submissions[0][0] == LANE

    async def test_clause_one_excludes_a_state_set_by_a_non_approver(self) -> None:
        tracker = FakeTracker(
            issues=[make_tracker_issue("K-1")],
            provenance=dict([approved_by("K-1", IMPOSTOR)]),
        )
        fire, queue, _ = dispatcher(tracker)
        report = await fire.run_pass()
        assert report.outcome is DispatchOutcome.empty_eligible_set
        assert report.exclusions[0].clause is ExclusionClause.NOT_APPROVED
        assert report.exclusions[0].detail == IMPOSTOR
        assert queue.submissions == []

    async def test_clause_two_excludes_a_closed_issue(self) -> None:
        tracker = FakeTracker(
            issues=[
                make_tracker_issue(
                    "K-1",
                    state_kind=WorkflowStateKind.COMPLETED,
                    state_name="Done",
                ),
            ],
            provenance=dict([approved_by("K-1", APPROVER)]),
        )
        fire, _, _ = dispatcher(tracker)
        report = await fire.run_pass()
        assert report.exclusions[0].clause is ExclusionClause.NOT_OPEN
        assert report.exclusions[0].detail == "Done"

    async def test_clause_three_excludes_an_issue_with_a_live_blocker(self) -> None:
        tracker = FakeTracker(
            issues=[
                make_tracker_issue("K-1", blocked_by=["K-2"]),
                make_tracker_issue("K-2", queue_states=[QueueState.TRIAGE]),
            ],
            provenance=dict([approved_by("K-1", APPROVER)]),
        )
        fire, _, _ = dispatcher(tracker)
        report = await fire.run_pass()
        assert report.exclusions[0].clause is ExclusionClause.LIVE_BLOCKER
        assert report.exclusions[0].detail == "K-2"

    async def test_clause_three_admits_an_issue_whose_blocker_is_closed(self) -> None:
        tracker = FakeTracker(
            issues=[
                make_tracker_issue("K-1", blocked_by=["K-2"]),
                make_tracker_issue(
                    "K-2",
                    queue_states=[QueueState.DONE],
                    state_kind=WorkflowStateKind.COMPLETED,
                ),
            ],
            provenance=dict([approved_by("K-1", APPROVER)]),
        )
        fire, _, _ = dispatcher(tracker)
        report = await fire.run_pass()
        assert report.outcome is DispatchOutcome.fire_enqueued
        assert report.claimed_issue_key == "K-1"

    async def test_clause_four_excludes_an_issue_another_pass_holds(self) -> None:
        tracker = FakeTracker(
            issues=[make_tracker_issue("K-1")],
            provenance=dict([approved_by("K-1", APPROVER)]),
        )
        await tracker.claim_issue(
            issue_key="K-1",
            holder="another-pass",
            lease_seconds=LEASE_SECONDS,
        )
        fire, queue, _ = dispatcher(tracker)
        report = await fire.run_pass()
        assert report.exclusions[0].clause is ExclusionClause.CLAIMED_OR_IN_FLIGHT
        assert report.exclusions[0].detail == "another-pass"
        assert queue.submissions == []

    async def test_clause_four_excludes_an_issue_with_a_live_run(self) -> None:
        tracker = FakeTracker(
            issues=[make_tracker_issue("K-1")],
            provenance=dict([approved_by("K-1", APPROVER)]),
        )
        fire, queue, _ = dispatcher(tracker)
        first = await fire.run_pass()
        assert first.outcome is DispatchOutcome.fire_enqueued
        await tracker.release_claim(issue_key="K-1", holder=HOLDER)
        second = await fire.run_pass()
        assert second.exclusions[0].clause is ExclusionClause.CLAIMED_OR_IN_FLIGHT
        assert len(queue.submissions) == 1

    async def test_clause_five_excludes_an_issue_an_open_pr_delivers(self) -> None:
        tracker = FakeTracker(
            issues=[make_tracker_issue("K-1")],
            provenance=dict([approved_by("K-1", APPROVER)]),
        )
        fire, queue, delivery = dispatcher(
            tracker,
            delivery=FakeDeliveryProbe(delivered=["K-1"]),
        )
        report = await fire.run_pass()
        assert report.exclusions[0].clause is ExclusionClause.OPEN_DELIVERY
        assert queue.submissions == []
        assert delivery.calls == ["K-1"]


class TestCrashedVersusDeliveredInReview:
    """The clause-5 discrimination, stated as the two cases it separates."""

    def _started_issue(self) -> object:
        return make_tracker_issue(
            "K-1",
            state_kind=WorkflowStateKind.STARTED,
            state_name="In Review",
        )

    async def test_delivered_in_review_is_excluded(self) -> None:
        """An open PR means the work is delivered; re-firing would duplicate."""
        tracker = FakeTracker(
            issues=[
                make_tracker_issue(
                    "K-1",
                    state_kind=WorkflowStateKind.STARTED,
                    state_name="In Review",
                ),
            ],
            provenance=dict([approved_by("K-1", APPROVER)]),
        )
        fire, queue, _ = dispatcher(
            tracker,
            delivery=FakeDeliveryProbe(delivered=["K-1"]),
        )
        report = await fire.run_pass()
        assert report.outcome is DispatchOutcome.empty_eligible_set
        assert report.exclusions[0].clause is ExclusionClause.OPEN_DELIVERY
        assert queue.submissions == []

    async def test_crashed_remains_eligible_and_is_reselected_next_pass(
        self,
    ) -> None:
        """Same workflow state, no open PR, no live run: still selectable."""
        tracker = FakeTracker(
            issues=[
                make_tracker_issue(
                    "K-1",
                    state_kind=WorkflowStateKind.STARTED,
                    state_name="In Progress",
                ),
            ],
            provenance=dict([approved_by("K-1", APPROVER)]),
        )
        fire, queue, _ = dispatcher(tracker)
        first = await fire.run_pass()
        assert first.outcome is DispatchOutcome.fire_enqueued

        # The run crashes: its job goes terminal and the claim lapses.
        queue.mark(first.job_id or "", JobState.TERMINAL)
        await tracker.release_claim(issue_key="K-1", holder=HOLDER)

        second = await fire.run_pass()
        assert second.outcome is DispatchOutcome.fire_enqueued
        assert second.claimed_issue_key == "K-1"
        assert len(queue.submissions) == 2

    async def test_the_two_cases_differ_only_by_the_open_pull_request(self) -> None:
        """Workflow state alone cannot tell them apart — the PR is the seam."""
        crashed_tracker = FakeTracker(
            issues=[
                make_tracker_issue(
                    "K-1",
                    state_kind=WorkflowStateKind.STARTED,
                    state_name="In Review",
                ),
            ],
            provenance=dict([approved_by("K-1", APPROVER)]),
        )
        delivered_tracker = FakeTracker(
            issues=[
                make_tracker_issue(
                    "K-1",
                    state_kind=WorkflowStateKind.STARTED,
                    state_name="In Review",
                ),
            ],
            provenance=dict([approved_by("K-1", APPROVER)]),
        )
        crashed, _, _ = dispatcher(crashed_tracker)
        delivered, _, _ = dispatcher(
            delivered_tracker,
            delivery=FakeDeliveryProbe(delivered=["K-1"]),
        )
        assert (await crashed.run_pass()).outcome is DispatchOutcome.fire_enqueued
        assert (
            await delivered.run_pass()
        ).outcome is DispatchOutcome.empty_eligible_set


class TestPriorityRanking:
    """Raw backend numerics never reach a comparison."""

    async def test_urgent_ranks_first_and_none_ranks_last(self) -> None:
        issues = [
            make_tracker_issue(key, priority=priority)
            for key, (_, priority) in RAW_PRIORITY_FIXTURE.items()
        ]
        tracker = FakeTracker(
            issues=issues,
            provenance=dict(approved_by(issue.issue_key, APPROVER) for issue in issues),
        )
        fire, _, _ = dispatcher(tracker)
        report = await fire.run_pass()
        assert report.claimed_issue_key == "RAW-URGENT"

        raw_ascending = sorted(
            RAW_PRIORITY_FIXTURE,
            key=lambda key: RAW_PRIORITY_FIXTURE[key][0],
        )
        assert raw_ascending[0] == "RAW-NONE", "the failure mode being prevented"
        assert report.claimed_issue_key != raw_ascending[0]

    async def test_no_priority_is_selected_only_when_nothing_else_remains(
        self,
    ) -> None:
        issues = [
            make_tracker_issue("RAW-NONE", priority=IssuePriority.NONE),
            make_tracker_issue("RAW-LOW", priority=IssuePriority.LOW),
        ]
        tracker = FakeTracker(
            issues=issues,
            provenance=dict(approved_by(issue.issue_key, APPROVER) for issue in issues),
        )
        fire, _, _ = dispatcher(tracker)
        assert (await fire.run_pass()).claimed_issue_key == "RAW-LOW"

    def test_no_module_outside_the_adapter_pairs_priority_with_a_number(
        self,
    ) -> None:
        """A raw-numeric sort is asserted absent, mechanically.

        The vendor's numeric encoding is allowed to exist in exactly two
        modules — the adapter that maps it and the wire shape that declares
        it.  Anywhere else, a line mentioning priority alongside a digit is
        a raw-numeric comparison waiting to happen.
        """
        root = Path(__file__).resolve().parents[2] / "src" / "kodezart"
        allowed = {
            root / "adapters" / "linear_mcp_tracker.py",
            root / "types" / "domain" / "linear_mcp.py",
        }
        offenders: list[str] = []
        for path in sorted(root.rglob("*.py")):
            if path in allowed:
                continue
            for number, line in enumerate(
                path.read_text().splitlines(),
                start=1,
            ):
                lowered = line.lower()
                if "priority" in lowered and re.search(r"\d", lowered):
                    offenders.append(f"{path.name}:{number}: {line.strip()}")
        assert offenders == []

    def test_the_domain_priority_order_is_the_only_order(self) -> None:
        assert not issubclass(IssuePriority, int)
        assert DOMAIN_PRIORITY_ORDER[0] is IssuePriority.URGENT
        assert DOMAIN_PRIORITY_ORDER[-1] is IssuePriority.NONE


class TestTieBreak:
    """Deterministic below the tie; logged and reconstructable at the tie."""

    async def test_the_order_below_a_tie_is_deterministic(self) -> None:
        issues = [
            make_tracker_issue(
                "YOUNGER",
                priority=IssuePriority.HIGH,
                created_at=FIXTURE_EPOCH + timedelta(days=1),
            ),
            make_tracker_issue(
                "OLDER",
                priority=IssuePriority.HIGH,
                created_at=FIXTURE_EPOCH,
            ),
        ]
        tracker = FakeTracker(
            issues=issues,
            provenance=dict(approved_by(issue.issue_key, APPROVER) for issue in issues),
        )
        fire, _, _ = dispatcher(tracker, draw=_forbidden_draw)
        report = await fire.run_pass()
        assert report.claimed_issue_key == "OLDER"
        assert report.tied_candidates == ("OLDER",)

    async def test_an_exact_tie_logs_the_tied_set_and_the_drawn_winner(
        self,
    ) -> None:
        issues = [
            make_tracker_issue(key, priority=IssuePriority.HIGH)
            for key in ("TIE-A", "TIE-B", "TIE-C")
        ]
        tracker = FakeTracker(
            issues=issues,
            provenance=dict(approved_by(issue.issue_key, APPROVER) for issue in issues),
        )
        fire, _, _ = dispatcher(tracker, draw=lambda keys: keys[1])
        with structlog.testing.capture_logs() as logs:
            report = await fire.run_pass()
        ranked = next(entry for entry in logs if entry["event"] == "dispatch_ranked")
        assert ranked["tied"] == ["TIE-A", "TIE-B", "TIE-C"]
        assert ranked["winner"] == "TIE-B"
        assert report.claimed_issue_key == "TIE-B"

    async def test_the_pass_outcome_is_reconstructable_from_the_log(self) -> None:
        issues = [
            make_tracker_issue(key, priority=IssuePriority.HIGH)
            for key in ("TIE-A", "TIE-B")
        ]
        tracker = FakeTracker(
            issues=issues,
            provenance=dict(approved_by(issue.issue_key, APPROVER) for issue in issues),
        )
        fire, _, _ = dispatcher(tracker, draw=lambda keys: keys[0])
        with structlog.testing.capture_logs() as logs:
            report = await fire.run_pass()
        ranked = next(entry for entry in logs if entry["event"] == "dispatch_ranked")
        enqueued = next(
            entry for entry in logs if entry["event"] == "dispatch_fire_enqueued"
        )
        assert ranked["order"] == ["TIE-A", "TIE-B"]
        assert enqueued["issue_key"] == ranked["winner"]
        assert enqueued["job_id"] == report.job_id
        assert enqueued["outcome"] == DispatchOutcome.fire_enqueued.value


class TestClaimRace:
    """Two overlapping passes over one eligible issue."""

    async def test_exactly_one_enqueue_and_the_loser_reports_claim_lost(
        self,
    ) -> None:
        tracker = FakeTracker(
            issues=[make_tracker_issue("K-1")],
            provenance=dict([approved_by("K-1", APPROVER)]),
        )
        queue = FakeJobQueue()
        first, _, _ = dispatcher(tracker, queue=queue, holder="pass-a")
        second, _, _ = dispatcher(tracker, queue=queue, holder="pass-b")
        reports = await asyncio.gather(first.run_pass(), second.run_pass())
        outcomes = [report.outcome for report in reports]
        assert outcomes.count(DispatchOutcome.fire_enqueued) == 1
        assert outcomes.count(DispatchOutcome.claim_lost) == 1
        assert len(queue.submissions) == 1

    async def test_the_loser_does_not_fall_through_to_the_next_ranked_issue(
        self,
    ) -> None:
        """A stale snapshot is never mined for a consolation prize.

        The eligible set here holds a second, genuinely eligible issue.  A
        pass that lost its claim must still end — the next-ranked issue in
        the same snapshot is not a fallback, because the snapshot is now
        known to be stale about at least one row.
        """
        issues = [
            make_tracker_issue("TOP", priority=IssuePriority.URGENT),
            make_tracker_issue("NEXT", priority=IssuePriority.HIGH),
        ]
        tracker = FakeTracker(
            issues=issues,
            provenance=dict(approved_by(issue.issue_key, APPROVER) for issue in issues),
        )
        queue = FakeJobQueue()
        fire, _, _ = dispatcher(tracker, queue=queue, holder="loser")

        original_claim = tracker.claim_issue
        claimed_by_the_pass: list[str] = []

        async def stolen_claim(
            *,
            issue_key: str,
            holder: str,
            lease_seconds: float,
        ) -> object:
            claimed_by_the_pass.append(issue_key)
            await original_claim(
                issue_key=issue_key,
                holder="thief",
                lease_seconds=lease_seconds,
            )
            return await original_claim(
                issue_key=issue_key,
                holder=holder,
                lease_seconds=lease_seconds,
            )

        tracker.claim_issue = stolen_claim  # type: ignore[method-assign]
        report = await fire.run_pass()
        assert report.outcome is DispatchOutcome.claim_lost
        assert report.eligible == ("TOP", "NEXT")
        assert claimed_by_the_pass == ["TOP"], "no fall-through to NEXT"
        assert queue.submissions == []

    async def test_the_next_pass_recomputes_from_fresh_data(self) -> None:
        """Throughput comes from successive passes, not from a fallback."""
        issues = [
            make_tracker_issue("TOP", priority=IssuePriority.URGENT),
            make_tracker_issue("NEXT", priority=IssuePriority.HIGH),
        ]
        tracker = FakeTracker(
            issues=issues,
            provenance=dict(approved_by(issue.issue_key, APPROVER) for issue in issues),
        )
        await tracker.claim_issue(
            issue_key="TOP",
            holder="another-pass",
            lease_seconds=LEASE_SECONDS,
        )
        fire, _, _ = dispatcher(tracker, draw=_forbidden_draw)
        report = await fire.run_pass()
        assert report.claimed_issue_key == "NEXT"
        assert report.exclusions[0].clause is ExclusionClause.CLAIMED_OR_IN_FLIGHT

    async def test_the_loser_reports_claim_lost_without_retrying(self) -> None:
        tracker = FakeTracker(
            issues=[make_tracker_issue("K-1")],
            provenance=dict([approved_by("K-1", APPROVER)]),
        )
        queue = FakeJobQueue()
        fire, _, _ = dispatcher(tracker, queue=queue, holder="loser")

        original_claim = tracker.claim_issue
        attempts: list[str] = []

        async def stolen_claim(
            *,
            issue_key: str,
            holder: str,
            lease_seconds: float,
        ) -> object:
            attempts.append(holder)
            if len(attempts) == 1:
                await original_claim(
                    issue_key=issue_key,
                    holder="thief",
                    lease_seconds=lease_seconds,
                )
            return await original_claim(
                issue_key=issue_key,
                holder=holder,
                lease_seconds=lease_seconds,
            )

        tracker.claim_issue = stolen_claim  # type: ignore[method-assign]
        report = await fire.run_pass()
        assert report.outcome is DispatchOutcome.claim_lost
        assert report.claimed_issue_key is None
        assert attempts == ["loser"], "no retry"
        assert queue.submissions == []


class TestEmptyEligibleSet:
    """The machine-readable report — never a free-text judgment."""

    async def test_every_excluded_issue_carries_its_clause(self) -> None:
        issues = [
            make_tracker_issue("BLOCKED", blocked_by=["LIVE"]),
            make_tracker_issue("LIVE", queue_states=[QueueState.TRIAGE]),
            make_tracker_issue(
                "CLOSED",
                state_kind=WorkflowStateKind.CANCELED,
                state_name="Canceled",
            ),
            make_tracker_issue("UNAPPROVED"),
        ]
        tracker = FakeTracker(
            issues=issues,
            provenance=dict(
                [
                    approved_by("BLOCKED", APPROVER),
                    approved_by("CLOSED", APPROVER),
                    approved_by("UNAPPROVED", IMPOSTOR),
                ]
            ),
        )
        fire, queue, _ = dispatcher(tracker)
        report = await fire.run_pass()
        assert report.outcome is DispatchOutcome.empty_eligible_set
        assert queue.submissions == []
        by_key = {item.issue_key: item.clause for item in report.exclusions}
        assert by_key == {
            "BLOCKED": ExclusionClause.LIVE_BLOCKER,
            "CLOSED": ExclusionClause.NOT_OPEN,
            "UNAPPROVED": ExclusionClause.NOT_APPROVED,
        }

    async def test_the_report_carries_the_raw_query_snapshot(self) -> None:
        issues = [
            make_tracker_issue("BLOCKED", blocked_by=["LIVE"]),
            make_tracker_issue("LIVE", queue_states=[QueueState.TRIAGE]),
        ]
        tracker = FakeTracker(
            issues=issues,
            provenance=dict([approved_by("BLOCKED", APPROVER)]),
        )
        fire, _, _ = dispatcher(tracker)
        report = await fire.run_pass()
        assert [row.issue_key for row in report.snapshot] == ["BLOCKED"]
        assert report.snapshot[0].state_name == "Todo"
        assert report.snapshot[0].priority is IssuePriority.NONE

    async def test_the_outcome_uses_the_shared_discriminator(self) -> None:
        tracker = FakeTracker(issues=[], provenance={})
        fire, _, _ = dispatcher(tracker)
        report = await fire.run_pass()
        assert report.outcome is DispatchOutcome.empty_eligible_set
        assert report.exclusions == ()
        assert report.eligible == ()

    async def test_the_empty_set_is_logged_machine_readably(self) -> None:
        tracker = FakeTracker(
            issues=[make_tracker_issue("UNAPPROVED")],
            provenance=dict([approved_by("UNAPPROVED", IMPOSTOR)]),
        )
        fire, _, _ = dispatcher(tracker)
        with structlog.testing.capture_logs() as logs:
            await fire.run_pass()
        entry = next(
            item for item in logs if item["event"] == "dispatch_empty_eligible_set"
        )
        assert entry["outcome"] == DispatchOutcome.empty_eligible_set.value
        assert entry["exclusions"] == [
            {"issueKey": "UNAPPROVED", "clause": ExclusionClause.NOT_APPROVED.value},
        ]


class TestSingleWinner:
    """One pass claims exactly one issue."""

    async def test_a_pass_never_claims_more_than_one(self) -> None:
        issues = [
            make_tracker_issue(key, priority=IssuePriority.URGENT)
            for key in ("A", "B", "C")
        ]
        tracker = FakeTracker(
            issues=issues,
            provenance=dict(approved_by(issue.issue_key, APPROVER) for issue in issues),
        )
        fire, queue, _ = dispatcher(tracker, draw=lambda keys: keys[0])
        await fire.run_pass()
        assert len(queue.submissions) == 1
        assert len(tracker.claims) == 1

    async def test_the_query_uses_the_configured_page_size(self) -> None:
        tracker = FakeTracker(issues=[], provenance={})
        fire, _, _ = dispatcher(tracker)
        await fire.run_pass()
        assert tracker.scans[0].page_size == PAGE_SIZE
        assert tracker.scans[0].queue_state is QueueState.APPROVED


def _forbidden_draw(candidates: Sequence[str]) -> str:
    raise AssertionError("the draw ran without an exact timestamp tie")
