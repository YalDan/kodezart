"""Adapter-owned behaviour that the port deliberately cannot express.

Everything here is about the vendor side of the seam: the raw priority
encoding, the transient-failure policy, the refusal to guess at a shape it
does not recognise, and the claim mechanism's same-instant tie-break.  The
conformance suite covers what every adapter must do; this module covers
what THIS adapter does to get there.
"""

from collections.abc import Mapping
from datetime import timedelta

import pytest

from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.core.errors import McpTransportError, TrackerProtocolError
from kodezart.core.protocols import McpToolResult
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.tracker import (
    ClaimStatus,
    EnsureAction,
    IssuePriority,
    IssueQuery,
    IssueRelationKind,
    MappingKind,
    MappingRef,
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

    async def test_every_measured_relation_arm_maps_to_a_domain_kind(self) -> None:
        """The vendor's relations object has four arms and the adapter reads all.

        Conformed to the measured shape under KOD-143: relations arrive as
        ONE object keyed by relation kind, not as a list of typed edges, so
        an arm's name is a key rather than a ``type`` string.
        """
        server = FakeLinearMcpServer(
            issues=[
                FakeMcpIssue(
                    id="R-1",
                    relations=[
                        ("blocks", "R-2"),
                        ("blockedBy", "R-3"),
                        ("relatedTo", "R-4"),
                        ("duplicateOf", "R-5"),
                    ],
                ),
            ],
            state_types=STATE_TYPES,
        )
        issue = await tracker_over(server).read_issue(issue_key="R-1")
        assert {
            (relation.kind, relation.issue_key) for relation in issue.relations
        } == {
            (IssueRelationKind.BLOCKS, "R-2"),
            (IssueRelationKind.BLOCKED_BY, "R-3"),
            (IssueRelationKind.RELATED, "R-4"),
            (IssueRelationKind.DUPLICATE, "R-5"),
        }

    async def test_an_arm_the_adapter_does_not_know_is_left_alone(self) -> None:
        """A fifth arm is the vendor's business, not a refusal.

        The old list-of-edges shape made an unrecognised relation a typed
        error.  The measured object shape makes it a key, and this module
        ignores keys it did not declare — the vendor extending its own
        payload is not a protocol violation.
        """

        class ExtraArmServer(FakeLinearMcpServer):
            def _tool_get_issue(
                self,
                arguments: Mapping[str, object],
            ) -> Mapping[str, object]:
                issue = self._issue(arguments, "id")
                relations = issue.relations_wire()
                relations["invented"] = [{"id": "R-9"}]
                return {**issue.wire(), "relations": relations}

        server = ExtraArmServer(
            issues=[FakeMcpIssue(id="R-6", relations=[("blocks", "R-7")])],
            state_types=STATE_TYPES,
        )
        issue = await tracker_over(server).read_issue(issue_key="R-6")
        assert [(r.kind, r.issue_key) for r in issue.relations] == [
            (IssueRelationKind.BLOCKS, "R-7"),
        ]

    async def test_a_malformed_payload_raises_naming_the_tool(self) -> None:
        class TruncatingServer(FakeLinearMcpServer):
            async def call_tool(
                self,
                *,
                name: str,
                arguments: Mapping[str, object],
            ) -> McpToolResult:
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


class TestTransportRetry:
    """Transport failures are retried on the same knobs as transient ones.

    The HTTP transport beneath the adapter raises ``McpTransportError``,
    never ``TransientAPIError`` — a retry loop that only caught the latter
    was live solely against the in-process fake (KOD-130 AC-2).
    """

    async def test_a_transport_failure_within_budget_is_retried(self) -> None:
        server = FakeLinearMcpServer(
            issues=[FakeMcpIssue(id="T-4")],
            state_types=STATE_TYPES,
            transport_failures={"get_issue": 2},
        )
        tracker = tracker_over(server, max_retries=2)
        issue = await tracker.read_issue(issue_key="T-4")
        assert issue.issue_key == "T-4"
        assert len(server.tool_calls("get_issue")) == 3

    async def test_exhausting_the_budget_raises_the_transport_error(self) -> None:
        server = FakeLinearMcpServer(
            issues=[FakeMcpIssue(id="T-5")],
            state_types=STATE_TYPES,
            transport_failures={"get_issue": 5},
        )
        tracker = tracker_over(server, max_retries=1)
        with pytest.raises(McpTransportError):
            await tracker.read_issue(issue_key="T-5")
        assert len(server.tool_calls("get_issue")) == 2

    async def test_each_retry_waits_the_configured_backoff(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Delays follow ``factor * base**attempt`` off the configured factor.

        The fake yields with ``sleep(0)`` at every tool boundary, so the
        backoff sequence is the nonzero delays.
        """
        recorded: list[float] = []

        async def instant_sleep(delay: float) -> None:
            recorded.append(delay)

        monkeypatch.setattr("asyncio.sleep", instant_sleep)
        server = FakeLinearMcpServer(
            issues=[FakeMcpIssue(id="T-6")],
            state_types=STATE_TYPES,
            transport_failures={"get_issue": 2},
        )
        tracker = tracker_over(server, max_retries=2, retry_backoff_factor=0.25)
        await tracker.read_issue(issue_key="T-6")

        assert [delay for delay in recorded if delay > 0] == [0.25, 0.5]


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


class TestLabelScopedReading:
    """Labels are read as a UNION of listings, one of them per declared team.

    ``list_issue_labels`` answers with the workspace-level labels when it
    is sent no team and with a team's own labels when it is sent one, so
    neither answer is "the labels this workspace holds".  Reading only the
    unscoped one is what made a boot invisible to itself: it had created
    ``queue:done`` team-scoped, per its ref's scope, and the next boot
    could not see it, re-created it, and was refused by name (KOD-143, the
    label addendum of 2026-08-25).

    Every row here turns on WHICH listing carried an entry.  None of them
    can be satisfied by reading a container off the entry itself, which is
    the reading the addendum forbids and which no live payload supports.
    """

    TEAM = TEAM_IDENTIFIERS["engineering"]
    LABEL = "queue:scoped"

    def _ref(self, identifier: str, scope: str | None) -> MappingRef:
        return MappingRef(
            kind=MappingKind.QUEUE_STATE,
            name="scoped",
            identifier=identifier,
            scope=scope,
        )

    def _server(self, **overrides: object) -> FakeLinearMcpServer:
        kwargs: dict[str, object] = {
            "teams": [self.TEAM],
            "labels": [],
            "users": [],
            "statuses": {self.TEAM: list(STATE_TYPES)},
            "state_types": STATE_TYPES,
        }
        kwargs.update(overrides)
        return FakeLinearMcpServer(**kwargs)  # type: ignore[arg-type]

    async def test_the_read_is_the_unscoped_listing_and_one_per_declared_team(
        self,
    ) -> None:
        """Both listings, every time: neither one answers for the other."""
        server = self._server()
        await tracker_over(server).resolve_mappings(
            refs=[self._ref(self.LABEL, None)],
        )

        assert server.tool_calls("list_issue_labels") == [{}, {"team": self.TEAM}]

    async def test_a_team_scoped_label_the_unscoped_listing_hides_is_adopted(
        self,
    ) -> None:
        """Boot five, as a fixture: the label a boot created, next boot.

        The workspace holds it on the declared team and the unscoped
        listing does not report it at all.  A reader of that listing alone
        calls it absent and re-creates it, which is the write the vendor
        refuses by name — so the assertion is that nothing was written.
        """
        server = self._server(
            labels=[self.LABEL],
            label_containers={self.LABEL: f"{self.TEAM}-id"},
        )
        tracker = tracker_over(server)
        assert server.tool_calls("list_issue_labels") == []

        (outcome,) = await tracker.ensure_mappings(
            refs=[self._ref(self.LABEL, self.TEAM)],
        )

        assert outcome.action is EnsureAction.ADOPTED
        assert server.tool_calls("create_issue_label") == []
        # And the validation pass that follows an ensure resolves it too:
        # boot four got that far and then could not see its own label.
        assert await tracker.resolve_mappings(refs=[self._ref(self.LABEL, None)]) == ()

    async def test_a_label_no_listing_carries_is_created_once_in_the_refs_scope(
        self,
    ) -> None:
        """Absent from both listings is the one case that writes."""
        server = self._server()
        tracker = tracker_over(server)

        (outcome,) = await tracker.ensure_mappings(
            refs=[self._ref(self.LABEL, self.TEAM)],
        )

        assert outcome.action is EnsureAction.CREATED
        assert server.tool_calls("create_issue_label") == [
            {"name": self.LABEL, "teamId": f"{self.TEAM}-id"},
        ]
        assert server.label_containers[self.LABEL] == f"{self.TEAM}-id"
        # Exactly once: the second boot reads the label its first one made.
        (again,) = await tracker.ensure_mappings(
            refs=[self._ref(self.LABEL, self.TEAM)],
        )
        assert again.action is EnsureAction.ADOPTED
        assert len(server.tool_calls("create_issue_label")) == 1

    async def test_a_workspace_level_label_resolves_and_serves_any_scope(
        self,
    ) -> None:
        """The unscoped listing's own entries still answer, for every ref.

        A workspace-level label is addressable on every board, so a ref
        declaring a team adopts it rather than making a second one.
        """
        server = self._server(labels=[self.LABEL])
        tracker = tracker_over(server)

        assert await tracker.resolve_mappings(refs=[self._ref(self.LABEL, None)]) == ()
        (outcome,) = await tracker.ensure_mappings(
            refs=[self._ref(self.LABEL, self.TEAM)],
        )

        assert outcome.action is EnsureAction.ADOPTED
        assert server.tool_calls("create_issue_label") == []
