"""The five eligibility clauses and the three-level ranking, clause by clause.

Every case here is a pure function over data the port already returned, so
a clause is falsifiable without standing up a pass.  Nothing in this module
touches a live workspace, a live remote, or a model.
"""

from datetime import timedelta

import pytest

from kodezart.domain.dispatch import (
    DOMAIN_PRIORITY_ORDER,
    blocker_keys,
    clause_approved,
    clause_open,
    clause_unclaimed,
    clause_undelivered,
    live_blocker,
    rank_key,
    ranked_order,
    select_top_ranked,
)
from kodezart.types.domain.operation import QueueState
from kodezart.types.domain.tracker import (
    ClaimResult,
    ClaimStatus,
    IssuePriority,
    StateTransition,
    WorkflowStateKind,
)
from tests.fakes import FIXTURE_EPOCH, make_tracker_issue

APPROVER = "the-approver"
IMPOSTOR = "not-the-approver"


def transition(actor: str) -> StateTransition:
    return StateTransition(
        issue_key="K-1",
        queue_state=QueueState.APPROVED,
        actor_key=actor,
        occurred_at=FIXTURE_EPOCH,
    )


class TestClauseOneApproved:
    """The queue state is APPROVED and the APPROVER set it."""

    def test_approved_by_the_approver_holds(self) -> None:
        assert clause_approved(
            make_tracker_issue("K-1"),
            provenance=transition(APPROVER),
            approver_key=APPROVER,
        )

    def test_a_missing_queue_state_fails(self) -> None:
        assert not clause_approved(
            make_tracker_issue("K-1", queue_states=[QueueState.PROPOSED]),
            provenance=transition(APPROVER),
            approver_key=APPROVER,
        )

    def test_the_state_set_by_someone_else_fails(self) -> None:
        """Authority binds to the approving ACT, not to the resulting state."""
        assert not clause_approved(
            make_tracker_issue("K-1"),
            provenance=transition(IMPOSTOR),
            approver_key=APPROVER,
        )

    def test_the_state_with_no_provenance_at_all_fails(self) -> None:
        assert not clause_approved(
            make_tracker_issue("K-1"),
            provenance=None,
            approver_key=APPROVER,
        )


class TestClauseTwoOpen:
    """Neither completed nor canceled."""

    @pytest.mark.parametrize(
        "kind",
        [
            WorkflowStateKind.TRIAGE,
            WorkflowStateKind.BACKLOG,
            WorkflowStateKind.UNSTARTED,
            WorkflowStateKind.STARTED,
        ],
    )
    def test_open_kinds_hold(self, kind: WorkflowStateKind) -> None:
        assert clause_open(make_tracker_issue("K-1", state_kind=kind))

    @pytest.mark.parametrize(
        "kind",
        [WorkflowStateKind.COMPLETED, WorkflowStateKind.CANCELED],
    )
    def test_closed_kinds_fail(self, kind: WorkflowStateKind) -> None:
        assert not clause_open(make_tracker_issue("K-1", state_kind=kind))


class TestClauseThreeBlockers:
    """Zero blockedBy edges to LIVE issues."""

    def test_no_edges_is_unblocked(self) -> None:
        assert live_blocker(make_tracker_issue("K-1"), blockers={}) is None

    def test_an_edge_to_an_open_issue_blocks(self) -> None:
        issue = make_tracker_issue("K-1", blocked_by=["K-2"])
        blockers = {"K-2": make_tracker_issue("K-2")}
        assert live_blocker(issue, blockers=blockers) == "K-2"

    def test_an_edge_to_a_completed_issue_does_not_block(self) -> None:
        issue = make_tracker_issue("K-1", blocked_by=["K-2"])
        blockers = {
            "K-2": make_tracker_issue("K-2", state_kind=WorkflowStateKind.COMPLETED),
        }
        assert live_blocker(issue, blockers=blockers) is None

    def test_an_edge_to_a_canceled_issue_does_not_block(self) -> None:
        issue = make_tracker_issue("K-1", blocked_by=["K-2"])
        blockers = {
            "K-2": make_tracker_issue("K-2", state_kind=WorkflowStateKind.CANCELED),
        }
        assert live_blocker(issue, blockers=blockers) is None

    def test_only_blocked_by_edges_count(self) -> None:
        issue = make_tracker_issue("K-1", blocked_by=["K-2", "K-3"])
        assert blocker_keys(issue) == ("K-2", "K-3")

    def test_the_first_live_blocker_is_the_one_reported(self) -> None:
        issue = make_tracker_issue("K-1", blocked_by=["K-2", "K-3"])
        blockers = {
            "K-2": make_tracker_issue("K-2", state_kind=WorkflowStateKind.COMPLETED),
            "K-3": make_tracker_issue("K-3"),
        }
        assert live_blocker(issue, blockers=blockers) == "K-3"


class TestClauseFourClaimAndRun:
    """No unexpired claim, no active run or queue entry."""

    def test_unclaimed_and_idle_holds(self) -> None:
        assert clause_unclaimed(claim=None, run_is_live=False)

    def test_a_held_claim_fails(self) -> None:
        held = ClaimResult(
            issue_key="K-1",
            status=ClaimStatus.GRANTED,
            holder="another-pass",
            expires_at=FIXTURE_EPOCH + timedelta(minutes=5),
        )
        assert not clause_unclaimed(claim=held, run_is_live=False)

    def test_a_live_run_fails(self) -> None:
        assert not clause_unclaimed(claim=None, run_is_live=True)


class TestClauseFiveDelivery:
    """The discrimination workflow state alone cannot make."""

    def test_delivered_in_review_is_excluded(self) -> None:
        assert not clause_undelivered(has_open_delivery=True)

    def test_crashed_remains_eligible(self) -> None:
        """No open PR and no live run: the issue stays selectable."""
        assert clause_undelivered(has_open_delivery=False)


class TestRanking:
    """Priority, then oldest-first, then a draw only on an exact tie."""

    def test_the_domain_order_is_urgent_first_and_none_last(self) -> None:
        assert DOMAIN_PRIORITY_ORDER == (
            IssuePriority.URGENT,
            IssuePriority.HIGH,
            IssuePriority.MEDIUM,
            IssuePriority.LOW,
            IssuePriority.NONE,
        )

    def test_priority_dominates_age(self) -> None:
        old_low = make_tracker_issue(
            "OLD",
            priority=IssuePriority.LOW,
            created_at=FIXTURE_EPOCH,
        )
        new_urgent = make_tracker_issue(
            "NEW",
            priority=IssuePriority.URGENT,
            created_at=FIXTURE_EPOCH + timedelta(days=30),
        )
        assert ranked_order([old_low, new_urgent]) == ("NEW", "OLD")

    def test_age_breaks_equal_priority_oldest_first(self) -> None:
        younger = make_tracker_issue(
            "YOUNG",
            priority=IssuePriority.HIGH,
            created_at=FIXTURE_EPOCH + timedelta(days=1),
        )
        older = make_tracker_issue(
            "OLD",
            priority=IssuePriority.HIGH,
            created_at=FIXTURE_EPOCH,
        )
        assert ranked_order([younger, older]) == ("OLD", "YOUNG")

    def test_a_near_tie_does_not_draw(self) -> None:
        """Only FULL-precision equality reaches the draw."""
        first = make_tracker_issue(
            "A",
            priority=IssuePriority.HIGH,
            created_at=FIXTURE_EPOCH,
        )
        second = make_tracker_issue(
            "B",
            priority=IssuePriority.HIGH,
            created_at=FIXTURE_EPOCH + timedelta(microseconds=1),
        )

        def never(candidates: list[str]) -> str:
            raise AssertionError("the draw ran on a non-tie")

        selection = select_top_ranked([second, first], draw=never)
        assert selection is not None
        assert selection.winner_key == "A"
        assert selection.tied == ("A",)

    def test_an_exact_tie_draws_and_reports_the_whole_tied_set(self) -> None:
        tied = [
            make_tracker_issue(key, priority=IssuePriority.HIGH)
            for key in ("A", "B", "C")
        ]
        selection = select_top_ranked(tied, draw=lambda keys: keys[-1])
        assert selection is not None
        assert selection.tied == ("A", "B", "C")
        assert selection.winner_key == "C"

    def test_the_tie_set_excludes_lower_ranked_issues(self) -> None:
        tied = [
            make_tracker_issue("A", priority=IssuePriority.HIGH),
            make_tracker_issue("B", priority=IssuePriority.HIGH),
            make_tracker_issue("C", priority=IssuePriority.LOW),
        ]
        selection = select_top_ranked(tied, draw=lambda keys: keys[0])
        assert selection is not None
        assert selection.tied == ("A", "B")

    def test_an_empty_set_selects_nothing(self) -> None:
        assert select_top_ranked([], draw=lambda keys: keys[0]) is None

    def test_the_rank_key_carries_no_backend_encoding(self) -> None:
        key = rank_key(make_tracker_issue("K-1", priority=IssuePriority.URGENT))
        assert key.priority == 0
        assert (
            rank_key(
                make_tracker_issue("K-2", priority=IssuePriority.NONE),
            ).priority
            == len(DOMAIN_PRIORITY_ORDER) - 1
        )
