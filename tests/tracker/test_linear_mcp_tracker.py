"""Adapter-owned behaviour that the port deliberately cannot express.

Everything here is about the vendor side of the seam: the raw priority
encoding, the transient-failure policy, the refusal to guess at a shape it
does not recognise, and the claim mechanism's same-instant tie-break.  The
conformance suite covers what every adapter must do; this module covers
what THIS adapter does to get there.
"""

from datetime import timedelta

import pytest

from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.core.errors import TrackerProtocolError
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.tracker import (
    ClaimStatus,
    IssuePriority,
    IssueQuery,
    priority_rank,
)
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue
from tests.tracker.conftest import (
    CLAIMED_ISSUE,
    FIXTURE_NOW,
    QUEUE_STATE_LABELS,
    STATE_TYPES,
    TEAM_IDENTIFIERS,
    WORKFLOW_STATE_NAMES,
    fixture_server,
    linear_over_fake_mcp,
)

RAW_PRIORITY_BY_DOMAIN_MEMBER: dict[int, IssuePriority] = {
    0: IssuePriority.NONE,
    1: IssuePriority.URGENT,
    2: IssuePriority.HIGH,
    3: IssuePriority.MEDIUM,
    4: IssuePriority.LOW,
}


def tracker_over(server: FakeLinearMcpServer, **overrides: object) -> LinearMcpTracker:
    """The adapter over *server*, with per-test constructor overrides."""
    kwargs: dict[str, object] = {
        "caller": server,
        "queue_state_labels": QUEUE_STATE_LABELS,
        "workflow_state_names": WORKFLOW_STATE_NAMES,
        "team_identifiers": TEAM_IDENTIFIERS,
        "max_retries": 0,
        "retry_backoff_factor": 0.0,
        "clock": lambda: FIXTURE_NOW,
    }
    kwargs.update(overrides)
    return LinearMcpTracker(**kwargs)  # type: ignore[arg-type]


class TestPriorityEncoding:
    """The raw numeric field is NOT an order, and the adapter never sorts it."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        sorted(RAW_PRIORITY_BY_DOMAIN_MEMBER.items()),
    )
    async def test_every_raw_value_maps_explicitly(
        self,
        raw: int,
        expected: IssuePriority,
    ) -> None:
        server = FakeLinearMcpServer(
            issues=[FakeMcpIssue(id="P-1", priority_raw=raw)],
            state_types=STATE_TYPES,
        )
        issue = await tracker_over(server).read_issue(issue_key="P-1")
        assert issue.priority is expected

    def test_no_priority_ranks_last_although_its_raw_value_is_lowest(self) -> None:
        """The failure the explicit mapping exists to prevent."""
        raw_ascending = [
            RAW_PRIORITY_BY_DOMAIN_MEMBER[raw]
            for raw in sorted(RAW_PRIORITY_BY_DOMAIN_MEMBER)
        ]
        assert raw_ascending[0] is IssuePriority.NONE
        domain_order = sorted(raw_ascending, key=priority_rank)
        assert domain_order[0] is IssuePriority.URGENT
        assert domain_order[-1] is IssuePriority.NONE

    async def test_an_unmapped_raw_value_raises_rather_than_defaulting(self) -> None:
        server = FakeLinearMcpServer(
            issues=[FakeMcpIssue(id="P-9", priority_raw=99)],
            state_types=STATE_TYPES,
        )
        with pytest.raises(TrackerProtocolError) as caught:
            await tracker_over(server).read_issue(issue_key="P-9")
        assert "priority" in str(caught.value)


class TestShapeRefusal:
    """A response the adapter cannot read is an error, never a guess."""

    async def test_an_unknown_state_kind_raises(self) -> None:
        server = FakeLinearMcpServer(
            issues=[FakeMcpIssue(id="S-1", status_type="invented")],
            state_types=STATE_TYPES,
        )
        with pytest.raises(TrackerProtocolError):
            await tracker_over(server).read_issue(issue_key="S-1")

    async def test_an_unknown_relation_kind_raises(self) -> None:
        server = FakeLinearMcpServer(
            issues=[FakeMcpIssue(id="R-1", relations=[("invented", "R-2")])],
            state_types=STATE_TYPES,
        )
        with pytest.raises(TrackerProtocolError):
            await tracker_over(server).read_issue(issue_key="R-1")

    async def test_a_malformed_payload_raises_naming_the_tool(self) -> None:
        class TruncatingServer(FakeLinearMcpServer):
            async def call_tool(
                self,
                *,
                name: str,
                arguments: object,
            ) -> dict[str, object]:
                return {"id": "T-1"}

        server = TruncatingServer(state_types=STATE_TYPES)
        with pytest.raises(TrackerProtocolError) as caught:
            await tracker_over(server).read_issue(issue_key="T-1")
        assert caught.value.tool == "get_issue"

    async def test_an_unconfigured_queue_state_raises(self) -> None:
        server = fixture_server()
        tracker = tracker_over(server, queue_state_labels={})
        with pytest.raises(TrackerProtocolError):
            await tracker.scan_issues(
                query=IssueQuery(queue_state=QueueState.APPROVED, page_size=1),
            )

    async def test_an_unconfigured_lifecycle_stage_raises(self) -> None:
        server = fixture_server()
        tracker = tracker_over(server, workflow_state_names={})
        with pytest.raises(TrackerProtocolError):
            await tracker.set_workflow_state(
                issue_key=CLAIMED_ISSUE,
                stage=LifecycleStage.DONE,
            )

    async def test_an_unconfigured_team_raises(self) -> None:
        server = fixture_server()
        with pytest.raises(TrackerProtocolError):
            await tracker_over(server).create_issue(
                title="t",
                body="b",
                team_key="not-configured",
                priority=IssuePriority.LOW,
            )


class TestTransientRetry:
    """Transient failures are retried up to the configured bound, then raise."""

    async def test_a_transient_failure_within_budget_is_retried(self) -> None:
        server = FakeLinearMcpServer(
            issues=[FakeMcpIssue(id="T-2")],
            state_types=STATE_TYPES,
            transient_failures={"get_issue": 2},
        )
        tracker = tracker_over(server, max_retries=2)
        issue = await tracker.read_issue(issue_key="T-2")
        assert issue.issue_key == "T-2"
        assert len(server.tool_calls("get_issue")) == 3

    async def test_exhausting_the_budget_raises_the_transient_error(self) -> None:
        from kodezart.domain.errors import TransientAPIError

        server = FakeLinearMcpServer(
            issues=[FakeMcpIssue(id="T-3")],
            state_types=STATE_TYPES,
            transient_failures={"get_issue": 5},
        )
        tracker = tracker_over(server, max_retries=1)
        with pytest.raises(TransientAPIError):
            await tracker.read_issue(issue_key="T-3")
        assert len(server.tool_calls("get_issue")) == 2


class TestClaimMechanism:
    """The comment-log claim, including the same-instant tie-break."""

    async def test_same_instant_claims_still_produce_one_winner(self) -> None:
        """Server timestamps can collide; the comment key breaks the tie."""
        server = fixture_server()
        server.comment_instants = [FIXTURE_NOW] * 4
        tracker = linear_over_fake_mcp(server)
        first = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=60.0,
        )
        second = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-b",
            lease_seconds=60.0,
        )
        assert first.status is ClaimStatus.GRANTED
        assert second.status is ClaimStatus.LOST

    async def test_an_expired_lease_frees_the_issue(self) -> None:
        server = fixture_server()
        early = linear_over_fake_mcp(server)
        await early.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=60.0,
        )
        later = tracker_over(
            server,
            clock=lambda: FIXTURE_NOW + timedelta(seconds=120),
        )
        assert await later.active_claim(issue_key=CLAIMED_ISSUE) is None
        won = await later.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-b",
            lease_seconds=60.0,
        )
        assert won.status is ClaimStatus.GRANTED


class TestDeterministicPath:
    """No model is in this loop — every call is a named tool invocation."""

    async def test_every_read_is_a_named_tool_call(self) -> None:
        server = fixture_server()
        tracker = linear_over_fake_mcp(server)
        await tracker.scan_issues(
            query=IssueQuery(queue_state=QueueState.APPROVED, page_size=5),
        )
        await tracker.read_issue(issue_key=CLAIMED_ISSUE)
        assert [name for name, _ in server.calls] == ["list_issues", "get_issue"]

    async def test_the_scan_forwards_the_configured_page_size(self) -> None:
        server = fixture_server()
        await linear_over_fake_mcp(server).scan_issues(
            query=IssueQuery(page_size=7),
        )
        assert server.tool_calls("list_issues")[0]["limit"] == 7
