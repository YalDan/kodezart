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

import pytest
import structlog
from pydantic import ValidationError

from kodezart.core.errors import RateLimitedSoftFailureError
from kodezart.domain.dispatch import DOMAIN_PRIORITY_ORDER
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.claim_heartbeat import ClaimHeartbeat
from kodezart.services.fire_context import FireContextAssembler
from kodezart.services.fire_dispatcher import FireDispatcher
from kodezart.types.domain.branch import WorkRef, WorkRefRole
from kodezart.types.domain.dispatch import (
    DispatchOutcome,
    DispatchReport,
    ExclusionClause,
)
from kodezart.types.domain.job import JobState
from kodezart.types.domain.operation import (
    CheckStep,
    DocumentEntry,
    DocumentSystem,
    Initiative,
    LifecycleStage,
    OperationConfig,
    OperationMemberAbsentError,
    Principal,
    PrincipalRole,
    QueueState,
    RecordDestination,
    RepoEntry,
    TeamEntry,
)
from kodezart.types.domain.run_records import RunOutcome
from kodezart.types.domain.tracker import (
    ClaimStatus,
    IssuePriority,
    IssueQuery,
    TrackerIssue,
    WorkflowStateKind,
)
from kodezart.types.requests.agent import WorkflowRequest
from tests.fakes import (
    FIXTURE_EPOCH,
    FakeDeliveryProbe,
    FakeGitService,
    FakeJobQueue,
    FakeRepoCache,
    FakeTrackerPort,
    PassThroughGate,
    make_tracker_issue,
)
from tests.services.test_claim_heartbeat import MovingClock, run_until

APPROVER = "the-approver"
IMPOSTOR = "not-the-approver"
REPO_URL = "https://example.invalid/owner/repo"
TRUNK = "trunk"
REMOTE = "fixture-remote"
INTEGRATION_DIR = "/tmp/fixture-integration"
LANE = "tracker"
HOLDER = "pass-a"
LEASE_SECONDS = 600.0
RENEWAL_FRACTION = 0.25
#: Five renewals of a 600-second lease at a quarter of it puts the run at
#: 750 seconds, which is past the lease it was granted with.
RENEWALS_PAST_THE_LEASE = 5
PAGE_SIZE = 50
RATE_LIMIT_COOLDOWN_SECONDS = 1800.0
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

# The ordering tokens the defect shows up as.  Stated once, here, and read
# by both the predicate test below and the repository scan; the arrow is
# stripped first because ``-> int`` on a signature mentioning priority is
# not a comparison.
_ORDERING_TOKENS = re.compile(r"[0-9]|sort|key\s*=|min\(|max\(|<|>|cmp")


def raw_priority_ordering(line: str) -> bool:
    """Whether *line* lets a priority reach an ordering by any route.

    ``priority_rank`` is the single exemption: it IS the domain order, so a
    line naming it is the one legitimate way priority reaches a comparison.
    """
    lowered = line.lower().replace("->", "")
    if "priority" not in lowered or "priority_rank" in lowered:
        return False
    return _ORDERING_TOKENS.search(lowered) is not None


def operation_config(
    *,
    teams: dict[str, TeamEntry] | None = None,
    repos: list[RepoEntry] | None = None,
) -> OperationConfig:
    return OperationConfig(
        operation_name="fixture",
        workspace="fixture-workspace",
        principals=[
            Principal(
                tracker_user=APPROVER,
                roles=frozenset(
                    {
                        PrincipalRole.APPROVER,
                        PrincipalRole.PRINCIPAL,
                        PrincipalRole.ASSIGNEE,
                    },
                ),
                handle="@approver",
            ),
            Principal(
                tracker_user=IMPOSTOR,
                roles=frozenset({PrincipalRole.PRINCIPAL}),
                handle="@impostor",
            ),
        ],
        agent_identities=[],
        teams=(
            {"engineering": TeamEntry(name="fixture-team", key="ENG")}
            if teams is None
            else teams
        ),
        queue_states={member.value: f"queue:{member.value}" for member in QueueState},
        workflow_states={
            LifecycleStage.IN_PROGRESS: "In Progress",
            LifecycleStage.IN_REVIEW: "In Review",
            LifecycleStage.DONE: "Done",
        },
        repos=(
            [
                RepoEntry(
                    url=REPO_URL,
                    trunk=TRUNK,
                    checks=[CheckStep(name="check", command="make check")],
                )
            ]
            if repos is None
            else repos
        ),
        documents={
            "checkpoint": DocumentEntry(
                system=DocumentSystem.TRACKER,
                name="checkpoint",
                id="doc-1",
            ),
        },
        records={
            "fire_prep": RecordDestination(
                system=DocumentSystem.KNOWLEDGE,
                name="Run log",
                id="record-1",
                append_only=True,
            ),
        },
        knowledge={},
        endpoints={},
        initiatives=[Initiative(id="init-1")],
    )


def dispatcher(
    tracker: FakeTrackerPort,
    *,
    queue: FakeJobQueue | None = None,
    delivery: FakeDeliveryProbe | None = None,
    holder: str = HOLDER,
    draw: object = None,
    git: FakeGitService | None = None,
    operation: OperationConfig | None = None,
    clock: object = None,
) -> tuple[FireDispatcher, FakeJobQueue, FakeDeliveryProbe]:
    """A dispatcher plus the doubles a test asserts against."""
    the_queue = queue or FakeJobQueue()
    the_delivery = delivery or FakeDeliveryProbe()
    git = git or FakeGitService()
    kwargs: dict[str, object] = {
        "tracker": tracker,
        "queue": the_queue,
        "registry": the_queue,
        "delivery": the_delivery,
        "operation": operation or operation_config(),
        "repo_url": REPO_URL,
        "lane": LANE,
        "holder": holder,
        "claim_lease_seconds": LEASE_SECONDS,
        "query_page_size": PAGE_SIZE,
        "rate_limit_cooldown_seconds": RATE_LIMIT_COOLDOWN_SECONDS,
        "assembler": FireContextAssembler(
            tracker=tracker,
            gate=PassThroughGate(),
            max_count=ASSET_MAX_COUNT,
            max_bytes=ASSET_MAX_BYTES,
            fetch_timeout_seconds=ASSET_FETCH_TIMEOUT_SECONDS,
        ),
        "resolver": BaseResolver(tracker=tracker, git=git, remote=REMOTE),
        "cache": FakeRepoCache(),
        "trunk": TRUNK,
        "integration_workspace_dir": INTEGRATION_DIR,
    }
    if draw is not None:
        kwargs["draw"] = draw
    if clock is not None:
        kwargs["clock"] = clock
    return FireDispatcher(**kwargs), the_queue, the_delivery  # type: ignore[arg-type]


BLOCKER_BRANCH = "kodezart/k-2-deliverable"
BLOCKER_SHA = "b" * 40


def deliverable_ref(issue_id: str, branch: str) -> WorkRef:
    """The ref an issue's own lane pushed — the premise a dependent builds on."""
    return WorkRef(
        issue_id=issue_id,
        role=WorkRefRole.DELIVERABLE,
        branch=branch,
        pushed_head_sha=BLOCKER_SHA,
        recorded_at=FIXTURE_EPOCH,
    )


def git_with(*branches: str) -> FakeGitService:
    """A git double where each named branch is present on the remote."""
    return FakeGitService(
        remote_branch_shas=dict.fromkeys(branches, BLOCKER_SHA),
    )


class UnscopedTrackerPort(FakeTrackerPort):
    """A port whose backend does not honour the scan's container scope.

    Not a rigged double: the port contract says a scoped scan is scoped,
    and an adapter over a backend with no server-side container filter has
    to apply it after the fact.  This is what that adapter looks like on the
    day it is written wrong, or the day the vendor stops honouring the
    argument — the exact case the container CLAUSE exists for, as distinct
    from the container-scoped QUERY.
    """

    async def scan_issues(self, *, query: IssueQuery) -> Sequence[TrackerIssue]:
        return await super().scan_issues(
            query=query.model_copy(update={"team_key": None}),
        )


class UnfilteredTrackerPort(FakeTrackerPort):
    """A port whose backend does not honour the scan's queue-state filter.

    The queue-state twin of :class:`UnscopedTrackerPort`, and it exists for
    the same reason: a scoped scan asks the BACKEND to narrow, and clause 2
    decides eligibility over what actually came back. Without a backend
    that ignores the filter there is no way to reach the clause at all, and
    an unreachable clause is an untested one.
    """

    async def scan_issues(self, *, query: IssueQuery) -> Sequence[TrackerIssue]:
        return await super().scan_issues(
            query=query.model_copy(update={"queue_state": None}),
        )


class TestTheContainerBoundary:
    """A pass claims from the operation's own board and from nowhere else."""

    async def test_the_scan_is_scoped_to_every_declared_team(self) -> None:
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("K-1")],
        )
        operation = operation_config(
            teams={
                "engineering": TeamEntry(name="fixture-team", key="ENG"),
                "design": TeamEntry(name="fixture-design", key="DES"),
            },
        )
        fire, _, _ = dispatcher(tracker, operation=operation)
        await fire.run_pass()
        assert [query.team_key for query in tracker.scans] == ["engineering", "design"]
        for query in tracker.scans:
            assert query.queue_state is QueueState.APPROVED

    def test_two_keys_naming_one_team_is_a_load_failure(self) -> None:
        """What makes the per-team scans disjoint, and the reverse map total.

        The adapter resolves an issue's team back onto the key a scan was
        scoped by.  Two keys for one team makes that answer a coin toss, so
        it is refused where every other structural ambiguity is.
        """
        with pytest.raises(ValidationError) as caught:
            operation_config(
                teams={
                    "engineering": TeamEntry(name="fixture-team", key="ENG"),
                    "also-engineering": TeamEntry(name="fixture-team", key="ENG"),
                },
            )
        assert "is not unique" in str(caught.value)

    async def test_clause_one_excludes_an_issue_on_an_undeclared_team(self) -> None:
        """The defect: another board's issue, approved by the same person.

        Both issues carry APPROVED and both were approved by this
        operation's approver, so every clause below the container clause
        passes on the foreign one.  Only the boundary separates them.

        The foreign issue outranks the operation's own, so a pass without
        the boundary claims it outright rather than only sometimes: the
        failure this asserts against is a fact, not a draw.
        """
        tracker = UnscopedTrackerPort(
            issues=[
                make_tracker_issue("K-1"),
                make_tracker_issue(
                    "OTHER-1",
                    priority=IssuePriority.URGENT,
                    team_key="somebody-elses-board",
                ),
            ],
        )
        fire, queue, _ = dispatcher(tracker)
        report = await fire.run_pass()
        assert [row.issue_key for row in report.snapshot] == ["K-1", "OTHER-1"]
        assert report.eligible == ("K-1",)
        assert report.claimed_issue_key == "K-1"
        assert [
            (item.issue_key, item.clause, item.detail) for item in report.exclusions
        ] == [("OTHER-1", ExclusionClause.OUTSIDE_TEAM, "somebody-elses-board")]
        assert len(queue.submissions) == 1

    async def test_clause_one_excludes_an_issue_with_no_configured_team(self) -> None:
        tracker = UnscopedTrackerPort(
            issues=[make_tracker_issue("OTHER-1", team_key=None)],
        )
        fire, queue, _ = dispatcher(tracker)
        report = await fire.run_pass()
        assert report.outcome is DispatchOutcome.empty_eligible_set
        assert report.exclusions[0].clause is ExclusionClause.OUTSIDE_TEAM
        assert report.exclusions[0].detail == ""
        assert queue.submissions == []

    async def test_an_operation_declaring_no_team_refuses_before_scanning(self) -> None:
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("K-1")],
        )
        fire, queue, _ = dispatcher(tracker, operation=operation_config(teams={}))
        with pytest.raises(OperationMemberAbsentError) as caught:
            await fire.run_pass()
        assert caught.value.missing == f"teams entry scanned by {REPO_URL}"
        assert "no issue can be selected" in caught.value.stops
        assert tracker.scans == []
        assert queue.submissions == []


class TestClauseDrivenExclusion:
    """Each clause excludes, and the report names which one."""

    async def test_an_eligible_issue_is_enqueued(self) -> None:
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("K-1")],
        )
        fire, queue, _ = dispatcher(tracker)
        report = await fire.run_pass()
        assert report.outcome is DispatchOutcome.fire_enqueued
        assert report.claimed_issue_key == "K-1"
        assert len(queue.submissions) == 1
        assert queue.submissions[0][0] == LANE

    async def test_clause_two_excludes_an_issue_not_carrying_the_state(self) -> None:
        """The paired negative, over a backend that ignored the scan filter.

        Under the founder's KOD-144 ruling of 2026-08-25 the predicate is
        the approved state's PRESENCE, so its negative is the state's
        absence — and the only way an absent-state issue reaches the clause
        at all is a backend that did not honour the queue-state filter. The
        test that asserted the old actor exclusion is removed under that
        ruling: no tool on the vendor surface attests who set a label.
        """
        tracker = UnfilteredTrackerPort(
            issues=[make_tracker_issue("K-1", queue_states=[QueueState.PROPOSED])],
        )
        fire, queue, _ = dispatcher(tracker)
        report = await fire.run_pass()
        assert report.outcome is DispatchOutcome.empty_eligible_set
        assert report.exclusions[0].clause is ExclusionClause.NOT_APPROVED
        assert queue.submissions == []

    async def test_clause_three_excludes_a_closed_issue(self) -> None:
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue(
                    "K-1",
                    state_kind=WorkflowStateKind.COMPLETED,
                    state_name="Done",
                ),
            ],
        )
        fire, _, _ = dispatcher(tracker)
        report = await fire.run_pass()
        assert report.exclusions[0].clause is ExclusionClause.NOT_OPEN
        assert report.exclusions[0].detail == "Done"

    async def test_the_predicate_reads_no_blocker_over_a_scan_entry(self) -> None:
        """The live-blocker clause is not one of the predicate's (KOD-278).

        A scan entry carries no edges, so asking the clause here could only
        ever pass — and the arm that asked it was green solely because a
        double seeded relations a listing never carries.  The blocked issue
        below is NOT this pass's winner, so nothing reads it or its
        blocker: an eligibility pass that still asked would have to read
        K-2, and reds here.
        """
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue("K-1", priority=IssuePriority.URGENT),
                make_tracker_issue("K-3", blocked_by=["K-2"]),
                make_tracker_issue("K-2", queue_states=[QueueState.TRIAGE]),
            ],
        )
        fire, queue, _ = dispatcher(tracker)

        report = await fire.run_pass()

        assert report.outcome is DispatchOutcome.fire_enqueued
        assert report.claimed_issue_key == "K-1"
        assert report.eligible == ("K-1", "K-3"), (
            "the blocked issue passes every eligibility clause"
        )
        assert "K-2" not in tracker.issue_reads
        assert len(queue.submissions) == 1

    async def test_clause_five_excludes_an_issue_another_pass_holds(self) -> None:
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("K-1")],
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

    async def test_clause_five_excludes_an_issue_with_a_live_run(self) -> None:
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("K-1")],
        )
        fire, queue, _ = dispatcher(tracker)
        first = await fire.run_pass()
        assert first.outcome is DispatchOutcome.fire_enqueued
        await tracker.release_claim(issue_key="K-1", holder=HOLDER)
        second = await fire.run_pass()
        assert second.exclusions[0].clause is ExclusionClause.CLAIMED_OR_IN_FLIGHT
        assert len(queue.submissions) == 1

    async def test_clause_six_excludes_an_issue_an_open_pr_delivers(self) -> None:
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("K-1")],
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
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue(
                    "K-1",
                    state_kind=WorkflowStateKind.STARTED,
                    state_name="In Review",
                ),
            ],
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
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue(
                    "K-1",
                    state_kind=WorkflowStateKind.STARTED,
                    state_name="In Progress",
                ),
            ],
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
        crashed_tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue(
                    "K-1",
                    state_kind=WorkflowStateKind.STARTED,
                    state_name="In Review",
                ),
            ],
        )
        delivered_tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue(
                    "K-1",
                    state_kind=WorkflowStateKind.STARTED,
                    state_name="In Review",
                ),
            ],
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
        tracker = FakeTrackerPort(
            issues=issues,
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
        tracker = FakeTrackerPort(
            issues=issues,
        )
        fire, _, _ = dispatcher(tracker)
        assert (await fire.run_pass()).claimed_issue_key == "RAW-LOW"

    def test_the_predicate_fires_on_every_natural_shape_of_the_defect(
        self,
    ) -> None:
        """The guard is shown to fail before it is trusted to pass.

        The predicate the repository scan below runs on was a digit test,
        and the natural form of the defect carries no digit at all — so the
        check passed the exact line it names.  Every
        form below is asserted to trip it, and the two legitimate forms are
        asserted not to, because a predicate that flags the domain order
        would be turned off rather than obeyed.
        """
        assert [
            line
            for line in (
                "return sorted(issues, key=lambda i: i.raw_priority)",
                "return sorted(issues, key=lambda x: x.priority)",
                "issues.sort(key=attrgetter('priority'))",
                "return min(issues, key=lambda i: i.priority)",
                "return max(issues, key=lambda i: i.priority)",
                "if left.priority < right.priority:",
                "PRIORITY_URGENT = 1",
            )
            if not raw_priority_ordering(line)
        ] == []
        assert [
            line
            for line in (
                "priority=priority_rank(issue.priority),",
                "sorted(IssuePriority, key=priority_rank),",
                "priority: IssuePriority",
            )
            if raw_priority_ordering(line)
        ] == []

    def test_no_module_outside_the_adapter_orders_by_a_raw_priority(
        self,
    ) -> None:
        """A raw-numeric sort is asserted absent, mechanically.

        The vendor's numeric encoding is allowed to exist in exactly two
        modules — the adapter that maps it and the wire shape that declares
        it.  Anywhere else, priority reaching a sort key, a comparison or a
        selection is the defect, and ``priority_rank`` — the domain order —
        is the one way it may.
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
                if raw_priority_ordering(line):
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
        tracker = FakeTrackerPort(
            issues=issues,
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
        tracker = FakeTrackerPort(
            issues=issues,
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
        tracker = FakeTrackerPort(
            issues=issues,
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
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("K-1")],
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
        tracker = FakeTrackerPort(
            issues=issues,
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
        tracker = FakeTrackerPort(
            issues=issues,
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
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("K-1")],
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
            make_tracker_issue("UNAPPROVED", queue_states=[QueueState.PROPOSED]),
        ]
        tracker = UnfilteredTrackerPort(issues=issues)
        fire, queue, _ = dispatcher(tracker)
        report = await fire.run_pass()
        # ``BLOCKED`` is the only issue no eligibility clause rejects, so it
        # wins and its blocker is decided at the pre-claim read (KOD-278) —
        # the one clause that is never the predicate's, and still a report
        # line naming the blocker.
        assert report.outcome is DispatchOutcome.winner_blocked
        assert queue.submissions == []
        by_key = {item.issue_key: item.clause for item in report.exclusions}
        assert by_key == {
            "BLOCKED": ExclusionClause.LIVE_BLOCKER,
            "LIVE": ExclusionClause.NOT_APPROVED,
            "CLOSED": ExclusionClause.NOT_OPEN,
            "UNAPPROVED": ExclusionClause.NOT_APPROVED,
        }

    async def test_a_groomed_duplicate_is_excluded_as_not_open(self) -> None:
        """The kind the vendor emits for an issue closed as a duplicate.

        A live board holds one, and before KOD-156 the scan that returned
        it raised rather than classifying it.  A duplicate is delivered on
        the issue that absorbed it, so it is never a dispatch candidate.
        """
        tracker = UnfilteredTrackerPort(
            issues=[
                make_tracker_issue(
                    "DUPLICATE",
                    state_kind=WorkflowStateKind.DUPLICATE,
                    state_name="Duplicate",
                ),
            ],
        )
        fire, queue, _ = dispatcher(tracker)
        report = await fire.run_pass()

        assert report.outcome is DispatchOutcome.empty_eligible_set
        assert queue.submissions == []
        assert [(item.issue_key, item.clause) for item in report.exclusions] == [
            ("DUPLICATE", ExclusionClause.NOT_OPEN),
        ]

    async def test_the_report_carries_the_raw_query_snapshot(self) -> None:
        issues = [
            make_tracker_issue("BLOCKED", blocked_by=["LIVE"]),
            make_tracker_issue("LIVE", queue_states=[QueueState.TRIAGE]),
        ]
        tracker = FakeTrackerPort(
            issues=issues,
        )
        fire, _, _ = dispatcher(tracker)
        report = await fire.run_pass()
        assert [row.issue_key for row in report.snapshot] == ["BLOCKED"]
        assert report.snapshot[0].state_name == "Todo"
        assert report.snapshot[0].priority is IssuePriority.NONE

    async def test_the_outcome_uses_the_shared_discriminator(self) -> None:
        tracker = FakeTrackerPort(issues=[])
        fire, _, _ = dispatcher(tracker)
        report = await fire.run_pass()
        assert report.outcome is DispatchOutcome.empty_eligible_set
        assert report.exclusions == ()
        assert report.eligible == ()

    async def test_the_empty_set_is_logged_machine_readably(self) -> None:
        tracker = UnfilteredTrackerPort(
            issues=[
                make_tracker_issue("UNAPPROVED", queue_states=[QueueState.PROPOSED]),
            ],
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
        tracker = FakeTrackerPort(
            issues=issues,
        )
        fire, queue, _ = dispatcher(tracker, draw=lambda keys: keys[0])
        await fire.run_pass()
        assert len(queue.submissions) == 1
        assert len(tracker.claims) == 1

    async def test_the_query_uses_the_configured_page_size(self) -> None:
        tracker = FakeTrackerPort(issues=[])
        fire, _, _ = dispatcher(tracker)
        await fire.run_pass()
        assert tracker.scans[0].page_size == PAGE_SIZE
        assert tracker.scans[0].queue_state is QueueState.APPROVED


def _forbidden_draw(candidates: Sequence[str]) -> str:
    raise AssertionError("the draw ran without an exact timestamp tie")


async def release_run(
    tracker: FakeTrackerPort,
    queue: FakeJobQueue,
    report: DispatchReport,
) -> None:
    """Put the issue back in the eligible set, as a finished run does.

    Clause 4 excludes an issue this pass still holds or whose run is
    live, so without this a "second pass" would observe an empty
    eligible set and every assertion below it would pass vacuously.
    """
    assert report.claimed_issue_key is not None
    assert report.job_id is not None
    await tracker.release_claim(
        issue_key=report.claimed_issue_key,
        holder=HOLDER,
    )
    queue.mark(report.job_id, JobState.TERMINAL)


class TestTheBaseIsReadOffTheGraph:
    """KOD-67, wired: the dispatched base is resolved, never assumed.

    Every assertion here is on the request that reached the QUEUE, because
    that is the value the run is actually built on.  A dispatcher that
    resolved a base and then submitted the default would pass a test that
    only inspected the report.
    """

    async def test_a_lane_with_no_blockers_is_dispatched_on_the_configured_trunk(
        self,
    ) -> None:
        """And specifically not on the literal the request model defaults to."""
        assert WorkflowRequest(prompt="x", repo_url=REPO_URL).base_branch != TRUNK

        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("K-1")],
        )
        fire, queue, _ = dispatcher(tracker)

        report = await fire.run_pass()

        _, request = queue.submissions[0]
        assert request.base_branch == TRUNK
        assert report.base is not None
        assert report.base.base_role is None
        assert report.base.inputs == ()

    async def test_a_lane_with_a_blocker_is_dispatched_on_that_blockers_ref(
        self,
    ) -> None:
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue("K-1", blocked_by=["K-2"]),
                make_tracker_issue(
                    "K-2",
                    queue_states=[QueueState.DONE],
                    state_kind=WorkflowStateKind.COMPLETED,
                ),
            ],
            recorded_work_refs={"K-2": [deliverable_ref("K-2", BLOCKER_BRANCH)]},
        )
        fire, queue, _ = dispatcher(tracker, git=git_with(BLOCKER_BRANCH))

        report = await fire.run_pass()

        _, request = queue.submissions[0]
        assert request.base_branch == BLOCKER_BRANCH
        assert request.base_branch != TRUNK
        assert report.base is not None
        assert report.base.base_role is WorkRefRole.DELIVERABLE
        assert [item.blocker_issue_id for item in report.base.inputs] == ["K-2"]

    async def test_a_terminal_refless_blocker_is_assumed_landed_on_trunk(
        self,
    ) -> None:
        """The founder's board reality (KOD-169): pull requests merge
        outside kodezart's loop, so a Done blocker recording no deliverable
        ref anywhere on its chain finished on the trunk — the lane resolves
        there, with the assumption logged by name, instead of refusing to
        dispatch over work that already landed."""
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue("K-1", blocked_by=["K-2"]),
                make_tracker_issue(
                    "K-2",
                    queue_states=[QueueState.DONE],
                    state_kind=WorkflowStateKind.COMPLETED,
                ),
            ],
        )
        fire, queue, _ = dispatcher(tracker)

        report = await fire.run_pass()

        assert report.outcome is DispatchOutcome.fire_enqueued
        assert report.base is not None
        assert report.base.base_branch == TRUNK
        assert report.base.inputs == ()
        assert len(queue.submissions) == 1

    async def test_an_unresolvable_base_reports_it_and_enqueues_nothing(self) -> None:
        """A missing premise is loud, released, and never trunk.

        The blocker's deliverable ref was never pushed, so there is no
        base to build on.  Substituting trunk here would build the lane
        WITHOUT its premise and call it delivered, which is the failure
        the resolver's typed error exists to make impossible.
        """
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue("K-1", blocked_by=["K-2"]),
                make_tracker_issue(
                    "K-2",
                    queue_states=[QueueState.DONE],
                    state_kind=WorkflowStateKind.COMPLETED,
                ),
            ],
            recorded_work_refs={
                "K-2": [
                    WorkRef(
                        issue_id="K-2",
                        role=WorkRefRole.DELIVERABLE,
                        branch=BLOCKER_BRANCH,
                        pushed_head_sha=None,
                        recorded_at=FIXTURE_EPOCH,
                    ),
                ],
            },
        )
        fire, queue, _ = dispatcher(tracker)

        report = await fire.run_pass()

        assert report.outcome is DispatchOutcome.base_unresolved
        assert report.claimed_issue_key == "K-1"
        assert report.base is None
        assert queue.submissions == []
        assert await tracker.active_claim(issue_key="K-1") is None, (
            "the claim is released so a later pass can retry"
        )

    async def test_a_ref_absent_from_the_remote_is_unresolved_not_substituted(
        self,
    ) -> None:
        """Recorded is not the same as present, and the difference matters."""
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue("K-1", blocked_by=["K-2"]),
                make_tracker_issue(
                    "K-2",
                    queue_states=[QueueState.DONE],
                    state_kind=WorkflowStateKind.COMPLETED,
                ),
            ],
            recorded_work_refs={"K-2": [deliverable_ref("K-2", BLOCKER_BRANCH)]},
        )
        absent = FakeGitService(remote_branch_shas={BLOCKER_BRANCH: None})
        fire, queue, _ = dispatcher(tracker, git=absent)

        report = await fire.run_pass()

        assert report.outcome is DispatchOutcome.base_unresolved
        assert queue.submissions == []


class TestTheDispatchedBaseIsRecordedAndCompared:
    """KOD-67 R3, wired: the spec crosses the port, and staleness reads it.

    ``domain/base_staleness`` had no production caller because nothing
    recorded a spec to compare against — the arithmetic could only ever
    compare a value with itself.  These cases are what make the comparison
    real: the dispatch WRITES the spec through the port, and the next
    dispatch of the same issue reads it back and says whether the graph
    moved underneath it.
    """

    async def test_the_dispatched_base_is_recorded_on_the_issue(self) -> None:
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("K-1")],
        )
        fire, _, _ = dispatcher(tracker)

        report = await fire.run_pass()

        assert await tracker.read_base_spec(issue_key="K-1") == report.base

    async def test_a_first_dispatch_supersedes_nothing(self) -> None:
        """No recorded spec is a first dispatch, never a stale base."""
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("K-1")],
        )
        fire, _, _ = dispatcher(tracker)

        report = await fire.run_pass()

        assert report.superseded_base is None

    async def test_a_re_dispatch_on_an_unchanged_graph_supersedes_nothing(
        self,
    ) -> None:
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("K-1")],
        )
        fire, queue, _ = dispatcher(tracker)
        first = await fire.run_pass()
        await release_run(tracker, queue, first)

        again = await fire.run_pass()

        assert again.outcome is DispatchOutcome.fire_enqueued
        assert again.superseded_base is None

    async def test_a_blocker_added_after_the_first_dispatch_supersedes_the_base(
        self,
    ) -> None:
        """The whole point: add an edge, and the recorded base is stale.

        Nobody has to have noticed the edit — the next pass recomputes and
        the arithmetic says the base moved, carrying the superseded value
        so a lapsed verdict can be computed from the report alone.
        """
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("K-1")],
        )
        fire, queue, _ = dispatcher(tracker, git=git_with(BLOCKER_BRANCH))
        first = await fire.run_pass()
        assert first.base is not None and first.base.inputs == ()
        await release_run(tracker, queue, first)

        tracker.issues["K-1"] = make_tracker_issue("K-1", blocked_by=["K-2"])
        tracker.issues["K-2"] = make_tracker_issue(
            "K-2",
            queue_states=[QueueState.DONE],
            state_kind=WorkflowStateKind.COMPLETED,
        )
        tracker.recorded_work_refs["K-2"] = [
            deliverable_ref("K-2", BLOCKER_BRANCH),
        ]

        second = await fire.run_pass()

        assert second.superseded_base == first.base
        assert second.base is not None
        assert second.base.base_branch == BLOCKER_BRANCH
        assert await tracker.read_base_spec(issue_key="K-1") == second.base


class TestThePreClaimStateIsCaptured:
    """The reading a crashed run has to be put back to (KOD-146).

    The pass is the only reader that sees the issue's workflow state
    before the lifecycle moves it: by the time a run has failed, the
    tracker's copy has been overwritten, so the value has to leave the
    pass on the report or it is gone.
    """

    async def test_the_report_carries_the_state_the_pass_read(self) -> None:
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue(
                    "K-1",
                    state_name="Backlog",
                    state_kind=WorkflowStateKind.BACKLOG,
                ),
            ],
        )
        fire, _, _ = dispatcher(tracker)

        report = await fire.run_pass()

        assert report.outcome is DispatchOutcome.fire_enqueued
        assert report.claimed_state_name == "Backlog"

    async def test_the_captured_state_is_one_no_lifecycle_stage_names(self) -> None:
        """Which is why a stage cannot express it, and a name must."""
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue(
                    "K-1",
                    state_name="Backlog",
                    state_kind=WorkflowStateKind.BACKLOG,
                ),
            ],
        )
        fire, _, _ = dispatcher(tracker)

        report = await fire.run_pass()

        assert report.claimed_state_name not in {
            stage.value for stage in LifecycleStage
        }

    async def test_a_pass_that_claimed_nothing_carries_no_state(self) -> None:
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("K-1", queue_states=[QueueState.PROPOSED])],
        )
        fire, _, _ = dispatcher(tracker)

        report = await fire.run_pass()

        assert report.outcome is DispatchOutcome.empty_eligible_set
        assert report.claimed_state_name is None


class TestTheClaimSurvivesTheProcessThatMadeIt:
    """The property the in-process registry was standing in for (KOD-147).

    Every case here runs a SECOND dispatcher built from scratch over the
    same tracker: a fresh queue, a fresh registry, and a ``FireDispatcher``
    whose jobs-by-issue map has never held anything.  That is a restarted
    service, and it is the shape the old guard could not survive — the
    registry it consulted was empty and the tracker's claim had expired
    under a run that was still going, so the next pass fired the issue a
    second time.

    Nothing below reads process state, because the second dispatcher has
    none to read.  The exclusion has to come off the tracker or not at all.
    """

    async def test_a_restarted_process_cannot_claim_an_issue_being_renewed(
        self,
    ) -> None:
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")], clock=clock)
        first, _, _ = dispatcher(tracker, holder=HOLDER)
        assert (await first.run_pass()).outcome is DispatchOutcome.fire_enqueued

        beat = ClaimHeartbeat(
            tracker=tracker,
            holder=HOLDER,
            lease_seconds=LEASE_SECONDS,
            renewal_fraction=RENEWAL_FRACTION,
            sleep=clock.sleep,
        )
        async with beat.renewing(issue_key="K-1"):
            await run_until(tracker, renewals=RENEWALS_PAST_THE_LEASE)
            second, second_queue, _ = dispatcher(tracker, holder=HOLDER)
            report = await second.run_pass()

        assert clock.now > FIXTURE_EPOCH + timedelta(seconds=LEASE_SECONDS), (
            "the run must have outlived the lease for this to be the case at all"
        )
        assert report.outcome is DispatchOutcome.empty_eligible_set
        assert [(item.issue_key, item.clause) for item in report.exclusions] == [
            ("K-1", ExclusionClause.CLAIMED_OR_IN_FLIGHT),
        ]
        assert second_queue.submissions == []

    async def test_a_second_instance_racing_a_renewed_claim_loses_it(self) -> None:
        """A different holder, not a restart: the log still has one winner."""
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")], clock=clock)
        first, _, _ = dispatcher(tracker, holder="pass-a")
        assert (await first.run_pass()).outcome is DispatchOutcome.fire_enqueued

        beat = ClaimHeartbeat(
            tracker=tracker,
            holder="pass-a",
            lease_seconds=LEASE_SECONDS,
            renewal_fraction=RENEWAL_FRACTION,
            sleep=clock.sleep,
        )
        async with beat.renewing(issue_key="K-1"):
            await run_until(tracker, renewals=RENEWALS_PAST_THE_LEASE)
            rival = await tracker.claim_issue(
                issue_key="K-1",
                holder="pass-b",
                lease_seconds=LEASE_SECONDS,
            )

        assert rival.status is ClaimStatus.LOST

    async def test_the_crash_arm_still_hands_the_issue_back(self) -> None:
        """With nothing renewing, the lease runs out and a later pass fires.

        The recovery the lease was introduced for, pinned unchanged: the
        renewal is what a LIVE process does, so a process that is gone
        renews nothing and the expiry does its job.
        """
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")], clock=clock)
        first, _, _ = dispatcher(tracker, holder=HOLDER)
        assert (await first.run_pass()).outcome is DispatchOutcome.fire_enqueued

        clock.advance(seconds=LEASE_SECONDS)
        second, second_queue, _ = dispatcher(tracker, holder=HOLDER)
        report = await second.run_pass()

        assert report.outcome is DispatchOutcome.fire_enqueued
        assert report.claimed_issue_key == "K-1"
        assert len(second_queue.submissions) == 1

    async def test_the_issue_is_held_only_while_something_is_renewing_it(self) -> None:
        """The two arms as one case: held during the run, free after it."""
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")], clock=clock)
        first, _, _ = dispatcher(tracker, holder=HOLDER)
        await first.run_pass()
        beat = ClaimHeartbeat(
            tracker=tracker,
            holder=HOLDER,
            lease_seconds=LEASE_SECONDS,
            renewal_fraction=RENEWAL_FRACTION,
            sleep=clock.sleep,
        )

        async with beat.renewing(issue_key="K-1"):
            await run_until(tracker, renewals=RENEWALS_PAST_THE_LEASE)
            during, _, _ = dispatcher(tracker, holder=HOLDER)
            held = await during.run_pass()
        clock.advance(seconds=LEASE_SECONDS)
        after, _, _ = dispatcher(tracker, holder=HOLDER)
        freed = await after.run_pass()

        assert held.outcome is DispatchOutcome.empty_eligible_set
        assert freed.outcome is DispatchOutcome.fire_enqueued


OTHER_REPO_URL = "https://example.invalid/owner/other"


def two_repo_operation(
    *,
    duck_scope: tuple[str, ...] = (),
) -> OperationConfig:
    """Two declared repositories, one bound team and one unbound board.

    The founder's live shape (KOD-169): the coordination team fires into
    its own repository, and a second board binds nowhere — each of its
    staged issues routes by the repository judgment recorded on it.
    """
    return operation_config(
        teams={
            "engineering": TeamEntry(
                name="fixture-team",
                key="ENG",
                repository=REPO_URL,
            ),
            "duck": TeamEntry(name="duck-team", key="DUC", scope=duck_scope),
        },
        repos=[
            RepoEntry(
                url=REPO_URL,
                trunk=TRUNK,
                checks=[CheckStep(name="check", command="make check")],
            ),
            RepoEntry(
                url=OTHER_REPO_URL,
                trunk=TRUNK,
                checks=[CheckStep(name="check", command="make check")],
            ),
        ],
    )


class TestTheRecordedRoute:
    """Judgment records the repository, determinism reads it (KOD-169).

    Every case runs the pass for ``REPO_URL`` over an approved issue on
    the UNBOUND board; what varies is the route recorded on the issue.
    """

    async def test_no_recorded_repository_is_a_named_refusal_never_a_claim(
        self,
    ) -> None:
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("DUC-1", team_key="duck")],
        )
        fire, queue, _ = dispatcher(tracker, operation=two_repo_operation())

        report = await fire.run_pass()

        assert report.outcome is DispatchOutcome.empty_eligible_set
        assert [(item.issue_key, item.clause) for item in report.exclusions] == [
            ("DUC-1", ExclusionClause.NO_RECORDED_REPOSITORY),
        ]
        assert queue.submissions == []
        assert await tracker.active_claim(issue_key="DUC-1") is None

    async def test_an_issue_recorded_for_this_repository_is_claimed_here(
        self,
    ) -> None:
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("DUC-1", team_key="duck")],
            recorded_repositories={"DUC-1": REPO_URL},
        )
        fire, queue, _ = dispatcher(tracker, operation=two_repo_operation())

        report = await fire.run_pass()

        assert report.outcome is DispatchOutcome.fire_enqueued
        assert report.claimed_issue_key == "DUC-1"
        assert len(queue.submissions) == 1

    async def test_an_issue_recorded_elsewhere_is_the_other_passes_to_claim(
        self,
    ) -> None:
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("DUC-1", team_key="duck")],
            recorded_repositories={"DUC-1": OTHER_REPO_URL},
        )
        fire, queue, _ = dispatcher(tracker, operation=two_repo_operation())

        report = await fire.run_pass()

        assert report.outcome is DispatchOutcome.empty_eligible_set
        exclusion = report.exclusions[0]
        assert exclusion.clause is ExclusionClause.RECORDED_ELSEWHERE
        assert exclusion.detail == OTHER_REPO_URL
        assert queue.submissions == []

    async def test_a_route_outside_the_declared_roster_is_visible_by_its_url(
        self,
    ) -> None:
        """A marker naming an undeclared repository routes nowhere; the
        url in the detail is what makes that legible against the config."""
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("DUC-1", team_key="duck")],
            recorded_repositories={"DUC-1": "https://example.invalid/x/undeclared"},
        )
        fire, queue, _ = dispatcher(tracker, operation=two_repo_operation())

        report = await fire.run_pass()

        exclusion = report.exclusions[0]
        assert exclusion.clause is ExclusionClause.RECORDED_ELSEWHERE
        assert exclusion.detail == "https://example.invalid/x/undeclared"
        assert queue.submissions == []

    async def test_the_unbound_board_is_scanned_by_this_pass(self) -> None:
        """Scan roster = bound teams plus every unbound one — the unbound
        board's issues must be READ here for the route clause to partition
        them at all."""
        tracker = FakeTrackerPort(issues=[])
        fire, _, _ = dispatcher(tracker, operation=two_repo_operation())

        await fire.run_pass()

        assert [query.team_key for query in tracker.scans] == [
            "engineering",
            "duck",
        ]


class TestTheDeclaredScope:
    """Whole board by default; a declared scope narrows by name (KOD-169)."""

    async def test_an_issue_in_a_named_project_is_in_scope(self) -> None:
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue(
                    "DUC-1",
                    team_key="duck",
                    project="a delivery project",
                    project_id="proj-1",
                ),
            ],
            recorded_repositories={"DUC-1": REPO_URL},
        )
        fire, queue, _ = dispatcher(
            tracker,
            operation=two_repo_operation(duck_scope=("a delivery project",)),
        )

        report = await fire.run_pass()

        assert report.outcome is DispatchOutcome.fire_enqueued
        assert len(queue.submissions) == 1

    async def test_an_issue_in_another_project_is_out_of_scope_by_name(
        self,
    ) -> None:
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue(
                    "DUC-1",
                    team_key="duck",
                    project="somebody else's project",
                    project_id="proj-2",
                ),
            ],
            recorded_repositories={"DUC-1": REPO_URL},
        )
        fire, queue, _ = dispatcher(
            tracker,
            operation=two_repo_operation(duck_scope=("a delivery project",)),
        )

        report = await fire.run_pass()

        exclusion = report.exclusions[0]
        assert exclusion.clause is ExclusionClause.OUT_OF_SCOPE
        assert exclusion.detail == "somebody else's project"
        assert queue.submissions == []

    async def test_a_projectless_issue_is_out_of_any_declared_scope(self) -> None:
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("DUC-1", team_key="duck")],
            recorded_repositories={"DUC-1": REPO_URL},
        )
        fire, queue, _ = dispatcher(
            tracker,
            operation=two_repo_operation(duck_scope=("a delivery project",)),
        )

        report = await fire.run_pass()

        exclusion = report.exclusions[0]
        assert exclusion.clause is ExclusionClause.OUT_OF_SCOPE
        assert exclusion.detail == "the issue belongs to no project"
        assert queue.submissions == []

    async def test_a_scope_naming_an_initiative_admits_its_projects_issues(
        self,
    ) -> None:
        """A scope entry may be an initiative in either spelling; the
        project's memberships are read once and cached per pass."""
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue(
                    "DUC-1",
                    team_key="duck",
                    project="a delivery project",
                    project_id="proj-1",
                ),
            ],
            recorded_repositories={"DUC-1": REPO_URL},
            initiative_identifiers={
                "proj-1": frozenset({"init-9", "the big initiative"}),
            },
        )
        fire, queue, _ = dispatcher(
            tracker,
            operation=two_repo_operation(duck_scope=("the big initiative",)),
        )

        report = await fire.run_pass()

        assert report.outcome is DispatchOutcome.fire_enqueued
        assert len(queue.submissions) == 1


class TestTheChurnExclusion:
    """The measured claim churn, reproduced and asserted dead (KOD-169).

    The live defect: an unresolvable winner was claimed, failed, and
    released every tick — 15 write-delete cycles, each one's comment
    writes feeding the next tick's gate delta.  The remembered failure
    must hold across this pass's OWN post-release timestamp, and lift the
    moment the issue genuinely changes.
    """

    def _tracker(self) -> FakeTrackerPort:
        return FakeTrackerPort(
            issues=[
                make_tracker_issue("K-1", blocked_by=["K-2"]),
                make_tracker_issue(
                    "K-2",
                    queue_states=[QueueState.DONE],
                    state_kind=WorkflowStateKind.COMPLETED,
                ),
            ],
            recorded_work_refs={
                "K-2": [
                    WorkRef(
                        issue_id="K-2",
                        role=WorkRefRole.DELIVERABLE,
                        branch=BLOCKER_BRANCH,
                        pushed_head_sha=None,
                        recorded_at=FIXTURE_EPOCH,
                    ),
                ],
            },
        )

    async def test_an_unresolved_issue_is_excluded_until_it_changes(self) -> None:
        tracker = self._tracker()
        fire, queue, _ = dispatcher(tracker)

        first = await fire.run_pass()
        second = await fire.run_pass()

        assert first.outcome is DispatchOutcome.base_unresolved
        assert second.outcome is DispatchOutcome.empty_eligible_set
        assert second.claimed_issue_key is None, "no re-claim, so no churn"
        exclusion = second.exclusions[0]
        assert exclusion.clause is ExclusionClause.BASE_UNRESOLVED
        assert "never been pushed" in exclusion.detail
        assert queue.submissions == []

    async def test_a_genuine_change_re_admits_the_issue(self) -> None:
        tracker = self._tracker()
        fire, _, _ = dispatcher(tracker)

        first = await fire.run_pass()
        tracker.issues["K-1"] = make_tracker_issue(
            "K-1",
            blocked_by=["K-2"],
            updated_at=FIXTURE_EPOCH + timedelta(hours=1),
        )
        third = await fire.run_pass()

        assert first.outcome is DispatchOutcome.base_unresolved
        assert third.outcome is DispatchOutcome.base_unresolved, (
            "the changed issue is retried rather than remembered forever"
        )


class TestTheWinnerIsReadBeforeItIsClaimed:
    """The vacuous live-blocker clause, reproduced and decided (KOD-173).

    Measured 2026-09-01: three winners (17:48, 17:57, 18:06) were claimed,
    failed base resolution and were released.  The scan entries carried no
    relations, so ``blocker_keys`` was empty on every one of them and the
    live-blocker clause could only pass — the graph edge that made each
    winner unfireable was read for the first time inside base resolution,
    after the claim had already been spent.
    """

    async def test_a_blocked_winner_is_excluded_before_any_claim(self) -> None:
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue("K-1", blocked_by=["K-2"]),
                make_tracker_issue("K-2", queue_states=[QueueState.TRIAGE]),
            ],
        )
        fire, queue, _ = dispatcher(tracker)

        report = await fire.run_pass()

        assert report.outcome is DispatchOutcome.winner_blocked
        assert report.claimed_issue_key is None
        assert [
            (item.issue_key, item.clause, item.detail)
            for item in report.exclusions
            if item.clause is ExclusionClause.LIVE_BLOCKER
        ] == [("K-1", ExclusionClause.LIVE_BLOCKER, "K-2")]
        assert tracker.claim_writes == [], "no claim is spent on a blocked winner"
        assert queue.submissions == []

    async def test_the_scan_alone_cannot_see_the_blocker(self) -> None:
        """The premise the case above rests on, asserted rather than assumed.

        The double answers a listing the way the backend does — each
        issue's own fields, no edges — and answers a read with the edges.
        Without this, a double that silently started carrying relations in
        its listings would make the test above pass through an eligibility
        clause that production never reaches, and the defect would be
        untested with everything green (KOD-278).
        """
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue("K-1", blocked_by=["K-2"]),
                make_tracker_issue("K-2", queue_states=[QueueState.TRIAGE]),
            ],
        )

        scanned = await tracker.scan_issues(
            query=IssueQuery(queue_state=QueueState.APPROVED, page_size=PAGE_SIZE),
        )

        assert [issue.relations for issue in scanned] == [()]
        assert (await tracker.read_issue(issue_key="K-1")).relations != ()

    async def test_an_unblocked_winner_is_claimed_at_the_cost_of_one_read(
        self,
    ) -> None:
        """The paired positive: the claim path is unchanged, and the bill is one.

        Two reads reach the port and both name the winner: the pre-claim
        clause's, and the base resolver's own — which this fix did not add
        and does not remove.  A read of anything but the winner, or a
        second pre-claim read, reds here.
        """
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("K-1")],
        )
        fire, queue, _ = dispatcher(tracker)

        report = await fire.run_pass()

        assert report.outcome is DispatchOutcome.fire_enqueued
        assert report.claimed_issue_key == "K-1"
        assert report.claimed_state_name == "Todo"
        assert tracker.claim_writes == ["K-1"]
        assert len(queue.submissions) == 1
        assert tracker.issue_reads == ["K-1", "K-1"]

    async def test_a_blocked_winner_does_not_starve_the_lane_behind_it(self) -> None:
        """The regression the pre-claim read introduced, reproduced and dead.

        Measured on the fix's own lineage: with a top-ranked issue the
        graph blocks, every tick re-selected it, reported
        ``winner_blocked`` and fired nothing — an unblocked candidate one
        rank below it waited for a blocker it had nothing to do with.  The
        blocked winner is remembered under its clause, so the tick after
        the exclusion ranks the next candidate; the pass still claims one
        winner per snapshot and never falls through inside one.
        """
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue(
                    "K-1", priority=IssuePriority.URGENT, blocked_by=["K-2"]
                ),
                make_tracker_issue("K-2", queue_states=[QueueState.TRIAGE]),
                make_tracker_issue("K-3"),
            ],
        )
        fire, queue, _ = dispatcher(tracker)

        first = await fire.run_pass()
        assert first.outcome is DispatchOutcome.winner_blocked
        assert queue.submissions == [], "no fall-through inside one snapshot"

        second = await fire.run_pass()

        assert second.outcome is DispatchOutcome.fire_enqueued
        assert second.claimed_issue_key == "K-3"
        assert [
            (item.issue_key, item.clause, item.detail)
            for item in second.exclusions
            if item.issue_key == "K-1"
        ] == [("K-1", ExclusionClause.LIVE_BLOCKER, "K-2")]

    async def test_a_changed_blocked_winner_is_re_admitted_and_re_decided(
        self,
    ) -> None:
        """The memory is held until the issue moves, never forever.

        K-3 is in flight by then, so K-1 is the only candidate left: a
        pass that still held the memory would report an empty eligible
        set.  ``winner_blocked`` is therefore the proof that the issue was
        re-admitted and its edges read again.
        """
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue(
                    "K-1", priority=IssuePriority.URGENT, blocked_by=["K-2"]
                ),
                make_tracker_issue("K-2", queue_states=[QueueState.TRIAGE]),
                make_tracker_issue("K-3"),
            ],
        )
        fire, _, _ = dispatcher(tracker)
        await fire.run_pass()
        await fire.run_pass()
        tracker.issues["K-1"] = make_tracker_issue(
            "K-1",
            priority=IssuePriority.URGENT,
            blocked_by=["K-2"],
            updated_at=FIXTURE_EPOCH + timedelta(hours=1),
        )

        third = await fire.run_pass()

        assert third.outcome is DispatchOutcome.winner_blocked

    async def test_a_closed_blocker_does_not_block_the_winner(self) -> None:
        """A finished dependency is an edge, not an obstacle.

        The pre-claim read supplies edges to a clause that reads them, and
        that clause's own rule is unchanged: a blocker that is closed is a
        premise already delivered.
        """
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue("K-1", blocked_by=["K-2"]),
                make_tracker_issue(
                    "K-2",
                    queue_states=[QueueState.DONE],
                    state_kind=WorkflowStateKind.COMPLETED,
                ),
            ],
        )
        fire, queue, _ = dispatcher(tracker)

        report = await fire.run_pass()

        assert report.outcome is DispatchOutcome.fire_enqueued
        assert len(queue.submissions) == 1


class TestTheFailedRunExclusion:
    """The measured fire loop, reproduced and asserted dead (KOD-174).

    2026-09-01: a fire was dispatched at 17:48, died at 17:57 on a
    provider rate-limit rejection, and was put back correctly — and the
    next tick at 18:01 re-selected the same issue and fired the whole run
    again.  The pass had no memory of the run it started, so a standing
    failure was a fresh run every interval.
    """

    RATE_LIMIT_CLASS = RateLimitedSoftFailureError.__name__
    CRASH_CLASS = RuntimeError.__name__

    @staticmethod
    async def _fire_then_fail(
        tracker: FakeTrackerPort,
        failure_class: str,
        *,
        clock: MovingClock | None = None,
    ) -> tuple[FireDispatcher, FakeJobQueue, DispatchReport]:
        """One dispatched fire that died, as the watch reports it.

        The order is the live one: the run ends, the claim is released and
        the job goes terminal, and only then does the outcome reach the
        dispatcher.
        """
        fire, queue, _ = dispatcher(tracker, clock=clock)
        first = await fire.run_pass()
        await release_run(tracker, queue, first)
        await fire.record_run_outcome("K-1", RunOutcome.FAILED, failure_class)
        return fire, queue, first

    async def test_a_rate_limited_run_is_not_fired_again_at_the_next_tick(
        self,
    ) -> None:
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
        fire, queue, first = await self._fire_then_fail(tracker, self.RATE_LIMIT_CLASS)

        second = await fire.run_pass()

        assert first.outcome is DispatchOutcome.fire_enqueued
        assert second.outcome is DispatchOutcome.empty_eligible_set
        assert second.claimed_issue_key is None
        assert [
            (item.issue_key, item.clause, item.detail) for item in second.exclusions
        ] == [("K-1", ExclusionClause.RUN_FAILED, self.RATE_LIMIT_CLASS)]
        assert len(queue.submissions) == 1, "the whole run is not fired again"

    async def test_a_changed_issue_re_admits_the_rate_limited_run(self) -> None:
        """The exclusion is held until the issue moves, never forever.

        The lane's own cooldown is stepped past first: it holds every
        issue after a rate limit, so leaving it standing would prove only
        that SOMETHING still excludes K-1 and say nothing about the
        per-issue memory this test is about.
        """
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
        fire, queue, _ = await self._fire_then_fail(
            tracker,
            self.RATE_LIMIT_CLASS,
            clock=clock,
        )
        tracker.issues["K-1"] = make_tracker_issue(
            "K-1",
            updated_at=FIXTURE_EPOCH + timedelta(hours=1),
        )
        clock.advance(seconds=RATE_LIMIT_COOLDOWN_SECONDS)

        third = await fire.run_pass()

        assert third.outcome is DispatchOutcome.fire_enqueued
        assert len(queue.submissions) == 2

    async def test_a_crashed_run_is_re_selected_after_its_exclusion_lifts(
        self,
    ) -> None:
        """The paired negative: the crashed-run rule keeps its promise.

        A crash is evidence about the run, never a verdict on its issue —
        the issue comes back, and what changed is only that it comes back
        when the issue does rather than at the next tick regardless.
        """
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
        fire, queue, _ = await self._fire_then_fail(tracker, self.CRASH_CLASS)

        second = await fire.run_pass()
        tracker.issues["K-1"] = make_tracker_issue(
            "K-1",
            updated_at=FIXTURE_EPOCH + timedelta(hours=1),
        )
        third = await fire.run_pass()

        assert [
            (item.issue_key, item.clause, item.detail) for item in second.exclusions
        ] == [("K-1", ExclusionClause.RUN_FAILED, self.CRASH_CLASS)]
        assert third.outcome is DispatchOutcome.fire_enqueued
        assert len(queue.submissions) == 2

    async def test_a_run_that_named_no_failure_class_is_still_remembered(self) -> None:
        """The third state: a stream that ended with no error frame at all."""
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
        fire, queue, _ = dispatcher(tracker)
        first = await fire.run_pass()
        await release_run(tracker, queue, first)
        await fire.record_run_outcome("K-1", RunOutcome.NEVER_STARTED, None)

        second = await fire.run_pass()

        assert [
            (item.issue_key, item.clause, item.detail) for item in second.exclusions
        ] == [("K-1", ExclusionClause.RUN_FAILED, RunOutcome.NEVER_STARTED.value)]

    async def test_a_completed_run_is_remembered_nowhere(self) -> None:
        """The paired positive: a finished fire leaves the issue as it found it.

        Its own lifecycle says what became of it — an exclusion here would
        be a second, competing statement about a delivered issue.
        """
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
        fire, queue, _ = dispatcher(tracker)
        first = await fire.run_pass()
        await release_run(tracker, queue, first)
        await fire.record_run_outcome("K-1", RunOutcome.COMPLETED, None)

        second = await fire.run_pass()

        assert second.outcome is DispatchOutcome.fire_enqueued
        assert second.exclusions == ()
        assert len(queue.submissions) == 2

    async def test_a_dispatcher_that_never_fired_the_issue_remembers_nothing(
        self,
    ) -> None:
        """Every dispatcher on the lane hears every fire; one of them owns it.

        The outcome is fanned out because the watch cannot route it, so a
        dispatcher that never enqueued this issue must leave it alone —
        remembering another repository's run would exclude an issue this
        pass may legitimately claim.
        """
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
        fire, queue, _ = dispatcher(tracker)

        await fire.record_run_outcome("K-1", RunOutcome.FAILED, self.CRASH_CLASS)
        report = await fire.run_pass()

        assert report.outcome is DispatchOutcome.fire_enqueued
        assert report.exclusions == ()
        assert len(queue.submissions) == 1


class TestTheLaneBacksOffAfterARateLimit:
    """The half of the measured loop remembering one issue cannot reach.

    2026-09-01: the run that died at 17:57 on a provider rate-limit
    rejection was re-fired four minutes later, and under the same limit
    the creator's retry policy spawned around sixteen empty sessions in
    thirty seconds.  A rate limit is a fact about the ACCOUNT, so
    excluding the issue that met it only moves the next tick onto the
    next-ranked candidate and into the same rejection.  The lane itself
    stops until an operator-configured cooldown lapses (KOD-174).
    """

    RATE_LIMIT_CLASS = RateLimitedSoftFailureError.__name__
    CRASH_CLASS = RuntimeError.__name__

    @staticmethod
    def _board() -> FakeTrackerPort:
        """A top-ranked issue to fire and kill, and a candidate behind it."""
        return FakeTrackerPort(
            issues=[
                make_tracker_issue("K-1", priority=IssuePriority.URGENT),
                make_tracker_issue("K-3"),
            ],
        )

    async def _fire_then_fail(
        self,
        tracker: FakeTrackerPort,
        failure_class: str,
        *,
        clock: MovingClock,
    ) -> tuple[FireDispatcher, FakeJobQueue]:
        fire, queue, _ = dispatcher(tracker, clock=clock)
        first = await fire.run_pass()
        assert first.claimed_issue_key == "K-1"
        await release_run(tracker, queue, first)
        await fire.record_run_outcome("K-1", RunOutcome.FAILED, failure_class)
        return fire, queue

    async def test_a_rate_limited_run_stops_the_next_ranked_issue_too(self) -> None:
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker = self._board()
        fire, queue = await self._fire_then_fail(
            tracker,
            self.RATE_LIMIT_CLASS,
            clock=clock,
        )

        second = await fire.run_pass()

        assert second.outcome is DispatchOutcome.empty_eligible_set
        assert [
            (item.issue_key, item.clause, item.detail) for item in second.exclusions
        ] == [
            ("K-1", ExclusionClause.RUN_FAILED, self.RATE_LIMIT_CLASS),
            ("K-3", ExclusionClause.LANE_BACKOFF, self.RATE_LIMIT_CLASS),
        ]
        assert len(queue.submissions) == 1

    async def test_dispatch_resumes_once_the_cooldown_lapses(self) -> None:
        """Lifted by the clock alone — nothing on the board clears a limit."""
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker = self._board()
        fire, queue = await self._fire_then_fail(
            tracker,
            self.RATE_LIMIT_CLASS,
            clock=clock,
        )
        await fire.run_pass()
        clock.advance(seconds=RATE_LIMIT_COOLDOWN_SECONDS)

        third = await fire.run_pass()

        assert third.outcome is DispatchOutcome.fire_enqueued
        assert third.claimed_issue_key == "K-3"
        assert len(queue.submissions) == 2

    async def test_a_crashed_run_leaves_the_lane_running(self) -> None:
        """The paired negative: only the shared failure holds the lane.

        A crash is one run's, and the next-ranked candidate has nothing to
        do with it — a cooldown here would idle a whole board over a
        traceback.
        """
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker = self._board()
        fire, queue = await self._fire_then_fail(
            tracker,
            self.CRASH_CLASS,
            clock=clock,
        )

        second = await fire.run_pass()

        assert second.outcome is DispatchOutcome.fire_enqueued
        assert second.claimed_issue_key == "K-3"
        assert len(queue.submissions) == 2
