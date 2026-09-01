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
import structlog

from kodezart.adapters.linear_mcp_tracker import _CLAIM_MARKER, LinearMcpTracker
from kodezart.core.errors import (
    McpCredentialRefusedError,
    McpTransportError,
    TrackerEnsureConflictError,
    TrackerProtocolError,
)
from kodezart.core.protocols import McpToolResult
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.tracker import (
    ClaimStatus,
    EnsureAction,
    IssuePriority,
    IssueQuery,
    IssueRelationKind,
    MappingKind,
    MappingRef,
    WorkflowStateKind,
    is_open,
    priority_rank,
)
from tests.fakes import FakeLinearMcpServer, FakeMcpComment, FakeMcpIssue
from tests.tracker.conftest import (
    APPROVER,
    CLAIMED_ISSUE,
    DOCUMENT_KEY,
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


#: Renewals a measured fire makes: the ninety-one-minute run of KOD-147
#: against the configured fifteen-minute lease, renewed on a quarter of
#: it.  Every one of them used to leave a comment on the issue.
RENEWALS_OF_A_MEASURED_RUN = 24


def claim_markers(server: FakeLinearMcpServer) -> list[FakeMcpComment]:
    """Every claim marker on the fake workspace's comment log.

    Matched with the adapter's OWN pattern rather than a second spelling
    of it here: a test that recognised markers by a shape the writer had
    moved off would count nothing and pass.
    """
    return [
        comment
        for comment in server.comments
        if _CLAIM_MARKER.search(comment.body) is not None
    ]


def holder_of(comment: FakeMcpComment) -> str:
    """The holder a claim marker names."""
    match = _CLAIM_MARKER.search(comment.body)
    assert match is not None
    return match.group("holder")


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

    async def test_a_duplicate_kind_state_reads_as_the_domain_member(self) -> None:
        """The vendor emits it and the board holds one, so the enum carries it.

        A groomed duplicate used to be an unmapped kind, which turned every
        scan that returned it into a refusal — one issue crash-looping the
        pass that had to read the whole board (KOD-156).
        """
        server = FakeLinearMcpServer(
            issues=[
                FakeMcpIssue(id="S-2", status="Duplicate", status_type="duplicate"),
            ],
            state_types=STATE_TYPES,
        )
        issue = await tracker_over(server).read_issue(issue_key="S-2")
        assert issue.state_kind is WorkflowStateKind.DUPLICATE
        assert issue.state_name == "Duplicate"
        assert not is_open(issue.state_kind)

    async def test_the_fixture_vocabulary_is_covered_by_the_domain_enum(self) -> None:
        """Every kind the fixture workspace can serve has a domain member.

        The vendor's vocabulary is the input this adapter has no say over,
        and the fixture's is the measured stand-in for it: a kind the
        workspace offers and the enum does not name is exactly the shape
        KOD-156 was — found on a live board rather than here.
        """
        unmapped = sorted(
            {
                raw
                for raw in STATE_TYPES.values()
                if raw not in {kind.value for kind in WorkflowStateKind}
            },
        )

        assert unmapped == [], (
            f"the fixture vocabulary carries {unmapped}, which WorkflowStateKind "
            "does not name; every issue in such a state is unreadable"
        )

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


class TestScanContainment:
    """One unreadable issue costs that issue, never the board it sits on.

    The measured shape (KOD-156): a single groomed duplicate on the board
    turned every fire-prep and dispatch scan into a ``TrackerProtocolError``
    and crash-looped the pass.  The kind is mapped now, but the NEXT kind
    the vendor invents must cost the same one issue — the containment is
    the durable half of that fix, and the enum is the perishable half.
    """

    def board_with_one_unmappable_issue(self) -> FakeLinearMcpServer:
        """Four approved issues on one team; the second names an unknown kind."""
        return FakeLinearMcpServer(
            issues=[
                FakeMcpIssue(id="B-1", labels=["queue:approved"]),
                FakeMcpIssue(
                    id="B-2",
                    status="Invented",
                    status_type="invented",
                    labels=["queue:approved"],
                ),
                FakeMcpIssue(id="B-3", labels=["queue:approved"]),
                FakeMcpIssue(id="B-4", labels=["queue:approved"]),
            ],
            state_types=STATE_TYPES,
        )

    async def test_the_scan_excludes_that_issue_and_returns_the_rest(self) -> None:
        server = self.board_with_one_unmappable_issue()

        found = await tracker_over(server).scan_issues(
            query=IssueQuery(queue_state=QueueState.APPROVED, page_size=10),
        )

        assert [issue.issue_key for issue in found] == ["B-1", "B-3", "B-4"]

    async def test_the_exclusion_names_the_issue_the_tool_and_the_raw_value(
        self,
    ) -> None:
        """An issue dropped without a name is a board hole nobody can find."""
        server = self.board_with_one_unmappable_issue()

        with structlog.testing.capture_logs() as logs:
            await tracker_over(server).scan_issues(
                query=IssueQuery(queue_state=QueueState.APPROVED, page_size=10),
            )

        excluded = [
            entry for entry in logs if entry["event"] == "tracker_scan_issue_excluded"
        ]
        assert len(excluded) == 1
        assert excluded[0]["issue_key"] == "B-2"
        assert excluded[0]["tool"] == "list_issues"
        assert excluded[0]["status_type"] == "invented"

    async def test_the_single_issue_read_of_that_issue_still_raises(self) -> None:
        """The fail-loud arm is unchanged where the issue IS the answer."""
        server = self.board_with_one_unmappable_issue()

        with pytest.raises(TrackerProtocolError) as caught:
            await tracker_over(server).read_issue(issue_key="B-2")

        assert caught.value.tool == "get_issue"
        assert "invented" in str(caught.value)


class TestCapabilityProbe:
    """How this adapter answers "can this credential scan for that signal?".

    The port promises the ANSWER; the two facts here are this adapter's own
    and the conformance suite cannot state either, because both are about
    the vendor's tools.  A signal maps to a scan, several signals map to the
    same one, and the probe is a call — so probing three issue signals costs
    one call, not three.
    """

    ISSUE_SIGNALS: tuple[PassSignal, ...] = (
        PassSignal.issues_changed,
        PassSignal.triage_backlog,
        PassSignal.approved_changed,
    )

    async def test_signals_served_by_one_scan_cost_one_call(self) -> None:
        server = FakeLinearMcpServer(issues=[], state_types=STATE_TYPES)

        refusals = await tracker_over(server).verify_scan_capability(
            signals=list(self.ISSUE_SIGNALS),
        )

        assert refusals == {}
        assert len(server.tool_calls("list_issues")) == 1

    async def test_the_probe_asks_for_the_smallest_page_the_tool_takes(self) -> None:
        """A probe is about reachability; a second row would be read by nobody."""
        server = FakeLinearMcpServer(issues=[], state_types=STATE_TYPES)

        await tracker_over(server).verify_scan_capability(
            signals=[PassSignal.issues_changed],
        )

        assert server.tool_calls("list_issues") == [{"limit": 1}]

    async def test_the_review_signal_probes_a_different_scan(self) -> None:
        """Reviews are a separate object class, so they are a separate probe."""
        server = FakeLinearMcpServer(issues=[], state_types=STATE_TYPES)

        await tracker_over(server).verify_scan_capability(signals=list(PassSignal))

        assert len(server.tool_calls("list_issues")) == 1
        assert len(server.tool_calls("list_diffs")) == 1

    async def test_a_transport_failure_that_says_nothing_about_scope_propagates(
        self,
    ) -> None:
        """An outage is not a refusal: reporting it as one silences a pass."""
        server = FakeLinearMcpServer(
            issues=[],
            state_types=STATE_TYPES,
            transport_failures={"list_issues": 1},
        )

        with pytest.raises(McpTransportError):
            await tracker_over(server).verify_scan_capability(
                signals=[PassSignal.issues_changed],
            )

    async def test_a_refusal_the_vendor_did_not_diagnose_as_scope_propagates(
        self,
    ) -> None:
        """A status code is not a diagnosis, so it is not read as one.

        The tool answered with an error and said nothing about a scope.
        What is known is that the call failed, so boot fails on it:
        classifying it as a scope refusal would tell an operator to widen a
        credential that was never the problem, and would do it on the
        strength of three characters.
        """
        server = FakeLinearMcpServer(
            issues=[],
            state_types=STATE_TYPES,
            tool_errors={"list_issues": "the request failed with status 403"},
        )

        with pytest.raises(McpTransportError, match="403"):
            await tracker_over(server).verify_scan_capability(
                signals=[PassSignal.issues_changed],
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


class TestARefusedCredentialIsNeverRetried:
    """The one failure a retry budget cannot buy anything against (KOD-171).

    Measured 2026-09-01: fifty-one minutes into a boot the server began
    answering 401, and every renewal, scan and tick then spent its whole
    budget of sleeps re-asking a question whose answer does not change.
    """

    async def test_the_refusal_stops_the_loop_on_the_attempt_that_met_it(
        self,
    ) -> None:
        server = FakeLinearMcpServer(
            issues=[FakeMcpIssue(id="T-6")],
            state_types=STATE_TYPES,
            credential_refused_after={"get_issue": 1},
        )
        tracker = tracker_over(server, max_retries=3)

        served = await tracker.read_issue(issue_key="T-6")
        with pytest.raises(McpCredentialRefusedError):
            await tracker.read_issue(issue_key="T-6")

        assert served.issue_key == "T-6"
        # One call served, one refused: no back-off attempt was spent, where
        # a retried refusal would have made four more.
        assert len(server.tool_calls("get_issue")) == 2

    async def test_the_refusal_names_the_credential_once(self) -> None:
        server = FakeLinearMcpServer(
            issues=[FakeMcpIssue(id="T-7")],
            state_types=STATE_TYPES,
            credential_refused_after={"get_issue": 0},
        )
        tracker = tracker_over(server, max_retries=3)

        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(McpCredentialRefusedError),
        ):
            await tracker.read_issue(issue_key="T-7")

        named = [
            entry for entry in logs if entry["event"] == "tracker_credential_refused"
        ]
        assert len(named) == 1
        assert named[0]["server_name"] == "fake-linear"
        assert named[0]["tool"] == "get_issue"
        assert not [entry for entry in logs if entry["event"] == "tracker_mcp_retry"]

    async def test_a_transport_failure_is_still_retried_beside_it(self) -> None:
        """The paired positive: the retried class did not narrow."""
        server = FakeLinearMcpServer(
            issues=[FakeMcpIssue(id="T-8")],
            state_types=STATE_TYPES,
            transport_failures={"get_issue": 2},
        )
        tracker = tracker_over(server, max_retries=2)

        issue = await tracker.read_issue(issue_key="T-8")

        assert issue.issue_key == "T-8"
        assert len(server.tool_calls("get_issue")) == 3

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


class TestClaimMarkerVolume:
    """What a claim COSTS the issue's comment log, over a whole run.

    The log is a surface a person reads and a board that mirrors publicly,
    and every marker on it is a machine comment.  The measured shape
    (KOD-152): a renewal appended, so a long fire wrote dozens of them, and
    a claimant that lost the race left its marker there for the whole lease
    — a claim nobody held, outranking every later claimant and surviving
    the winner's own release.

    Counted on the fake server's log rather than through the port, because
    the port cannot express "how many comments did this cost" and that is
    exactly the question.
    """

    async def test_a_claim_renewed_through_a_long_run_leaves_one_marker(self) -> None:
        server = fixture_server()
        tracker = linear_over_fake_mcp(server)
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=60.0,
        )

        for _ in range(RENEWALS_OF_A_MEASURED_RUN):
            renewed = await tracker.renew_claim(
                issue_key=CLAIMED_ISSUE,
                holder="pass-a",
                lease_seconds=60.0,
            )
            assert renewed is not None, "the claim lapsed under a run still going"

        assert len(claim_markers(server)) == 1

    async def test_a_renewal_across_a_competitors_claim_keeps_the_order(self) -> None:
        """The renewal edits in place, so the holder keeps where it stood.

        The competitor arrives BETWEEN renewals, which is the ordering the
        edit exists for: an appended renewal would carry a later timestamp
        than the competitor's marker and could lose the log to it.
        """
        server = fixture_server()
        tracker = linear_over_fake_mcp(server)
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=60.0,
        )
        (first,) = claim_markers(server)
        loser = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-b",
            lease_seconds=60.0,
        )
        await tracker.renew_claim(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=120.0,
        )

        held = await tracker.active_claim(issue_key=CLAIMED_ISSUE)

        assert loser.status is ClaimStatus.LOST
        assert held is not None
        assert held.holder == "pass-a"
        assert held.expires_at == FIXTURE_NOW + timedelta(seconds=120.0)
        # The place in the order is the marker's creation instant, and the
        # renewal did not move it: an appended renewal would carry a later
        # one than the competitor's arrival.
        (carried,) = claim_markers(server)
        assert carried.created_at == first.created_at

    async def test_a_losing_claimant_leaves_no_marker_behind(self) -> None:
        """The loser deletes its own append; the winner's is untouched."""
        server = fixture_server()
        tracker = linear_over_fake_mcp(server)
        won = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=60.0,
        )
        lost = await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-b",
            lease_seconds=60.0,
        )

        assert lost.status is ClaimStatus.LOST
        assert [holder_of(marker) for marker in claim_markers(server)] == ["pass-a"]
        held = await tracker.active_claim(issue_key=CLAIMED_ISSUE)
        assert held is not None
        assert held.expires_at == won.expires_at

    async def test_the_loser_leaves_nothing_that_outlives_the_winner(self) -> None:
        """The measured consequence: the winner's release frees the issue.

        An orphaned marker made the release a half-measure — the issue went
        on being unclaimable, by a marker nobody was renewing, until the
        loser's own lease ran out.
        """
        server = fixture_server()
        tracker = linear_over_fake_mcp(server)
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-a",
            lease_seconds=60.0,
        )
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="pass-b",
            lease_seconds=60.0,
        )

        await tracker.release_claim(issue_key=CLAIMED_ISSUE, holder="pass-a")

        assert claim_markers(server) == []
        assert await tracker.active_claim(issue_key=CLAIMED_ISSUE) is None


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


async def _label_entries(
    server: FakeLinearMcpServer,
    team: str,
) -> list[Mapping[str, object]]:
    """One team's label listing, asked the way the adapter asks for it."""
    payload = await server.call_tool(
        name="list_issue_labels",
        arguments={"team": team},
    )
    assert isinstance(payload, Mapping)
    entries = payload["labels"]
    assert isinstance(entries, list)
    return entries


async def _label_names(server: FakeLinearMcpServer, team: str) -> list[str]:
    """The names one team's listing answers with."""
    return [str(entry["name"]) for entry in await _label_entries(server, team)]


async def _label_ids(server: FakeLinearMcpServer, team: str) -> set[str]:
    """The distinct label ids one team's listing answers with."""
    return {str(entry["id"]) for entry in await _label_entries(server, team)}


class TestLabelScopedReading:
    """Labels are read from both listings and classified by id.

    ``list_issue_labels`` answers with the workspace-level labels when it
    is sent no team, so it is not "the labels this workspace holds":
    reading only that one made a boot invisible to itself, having created
    ``queue:done`` team-scoped per its ref's scope (KOD-143, the label
    addendum of 2026-08-25).

    Sent a team it answers with that team's own labels AND the
    workspace-level ones, so neither is the team's definitions either.
    Which listing carried an entry is therefore not enough on its own —
    a name in a team's answer may be one workspace label reaching that
    board — and the ID is what separates the two.  Reading the team's
    answer whole made every workspace label look contested and refused a
    healthy board on its approval label (KOD-167).

    Still no row here reads a container off the entry's own ``teamId``,
    which is the reading the addendum forbids and which no live payload
    supports: the id says WHICH label, never where it lives.
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
            labels=[],
            team_labels={f"{self.TEAM}-id": [self.LABEL]},
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
        assert server.team_labels[f"{self.TEAM}-id"] == [self.LABEL]
        assert self.LABEL not in server.labels
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
        declaring a team adopts it rather than making a second one — and
        the board's own listing ECHOES it, which is the shape that made
        the adapter read it as the board's and refuse.  The echo is
        asserted here rather than assumed: without it this case cannot
        tell a correct classification from the one that broke boot.
        """
        server = self._server(labels=[self.LABEL])
        tracker = tracker_over(server)

        assert await _label_names(server, self.TEAM) == [self.LABEL]

        assert await tracker.resolve_mappings(refs=[self._ref(self.LABEL, None)]) == ()
        (outcome,) = await tracker.ensure_mappings(
            refs=[self._ref(self.LABEL, self.TEAM)],
        )

        assert outcome.action is EnsureAction.ADOPTED
        assert server.tool_calls("create_issue_label") == []

    async def test_a_team_copy_beside_a_workspace_label_refuses_naming_both(
        self,
    ) -> None:
        """Same name, two ids: two definitions, and no way to pick one.

        The counterpart of the case above and the reason it is decided by
        id.  Both listings carry the name, but the board's entry is its
        OWN label rather than the workspace's reaching it — so which one a
        write on that board resolves to is undecidable and the ensure
        names every container it found.
        """
        server = self._server(
            labels=[self.LABEL],
            team_labels={f"{self.TEAM}-id": [self.LABEL]},
        )
        tracker = tracker_over(server)

        assert len(await _label_ids(server, self.TEAM)) == 2

        with pytest.raises(TrackerEnsureConflictError) as caught:
            await tracker.ensure_mappings(refs=[self._ref(self.LABEL, self.TEAM)])

        assert "workspace" in str(caught.value)
        assert self.TEAM in str(caught.value)
        assert server.tool_calls("create_issue_label") == []


class TestIdentifierListingPerKind:
    """Each kind is answered by the listing that can answer for it, and only it.

    The resolution used to be an if-chain whose trailing return handed
    every unlisted kind the TEAM names — an implicit wildcard, so a sixth
    ``MappingKind`` would have booted resolving against a listing nobody
    chose for it, and passed.  The arms are now explicit and total: the
    four kinds with a workspace-wide listing each name theirs, and the
    fifth says it has none.
    """

    def _ref(self, kind: MappingKind, identifier: str) -> MappingRef:
        return MappingRef(kind=kind, name="fixture", identifier=identifier)

    @pytest.mark.parametrize(
        ("kind", "identifier"),
        [
            (MappingKind.USER, APPROVER),
            (MappingKind.TEAM, TEAM_IDENTIFIERS["engineering"]),
            (MappingKind.QUEUE_STATE, QUEUE_STATE_LABELS["approved"]),
            (MappingKind.DOCUMENT, DOCUMENT_KEY),
        ],
    )
    async def test_a_kind_resolves_against_its_own_listing(
        self,
        kind: MappingKind,
        identifier: str,
    ) -> None:
        """Every current member keeps the answer it had before the match."""
        server = fixture_server()

        unresolved = await linear_over_fake_mcp(server).resolve_mappings(
            refs=[self._ref(kind, identifier)],
        )

        assert unresolved == ()

    @pytest.mark.parametrize(
        "kind",
        [MappingKind.USER, MappingKind.QUEUE_STATE, MappingKind.DOCUMENT],
    )
    async def test_a_team_name_resolves_no_other_kind(
        self,
        kind: MappingKind,
    ) -> None:
        """The wildcard's fingerprint: a team name answering for something else."""
        server = fixture_server()
        ref = self._ref(kind, TEAM_IDENTIFIERS["engineering"])

        assert await linear_over_fake_mcp(server).resolve_mappings(refs=[ref]) == (ref,)

    async def test_a_workflow_state_has_no_workspace_wide_listing(self) -> None:
        """The kind with no answer raises rather than borrowing the team names.

        Its caller routes it to the per-team read before this call, so
        reaching here means that guard is gone — and a silent team-name
        answer would resolve every declared state against a listing that
        holds none of them.
        """
        tracker = linear_over_fake_mcp(fixture_server())

        with pytest.raises(RuntimeError, match="workflow state resolves per team"):
            await tracker._identifiers_of(MappingKind.WORKFLOW_STATE)

    async def test_the_caller_never_sends_a_workflow_state_to_that_listing(
        self,
    ) -> None:
        """The guard is intact: the per-team read answers, and nothing raises."""
        server = fixture_server()
        ref = self._ref(
            MappingKind.WORKFLOW_STATE,
            WORKFLOW_STATE_NAMES[LifecycleStage.IN_PROGRESS],
        )

        assert await linear_over_fake_mcp(server).resolve_mappings(refs=[ref]) == ()


class TestUserIdentityResolution:
    """A user answers to TWO names, and a config may spell either of them.

    The listing reports an account ``name`` and a ``displayName`` — the
    handle a mention addresses — and no measured entry has them equal.
    The operation config's identity convention puts the mention handle
    first, so a resolution knowing only the account name leaves exactly
    that entry unresolvable and no real config can pass boot.  And because
    the pass templates are byte-identical to the literals the routine
    texts substitute, the configured spelling may carry the mention's own
    leading ``@`` (KOD-143 addendum 3).

    The ``@`` is syntax and comes off the CONFIG side before matching.
    Nothing else is normalised: no case-folding is added here.
    """

    def _ref(self, identifier: str) -> MappingRef:
        return MappingRef(
            kind=MappingKind.USER,
            name="agent",
            identifier=identifier,
        )

    async def test_an_identity_that_is_only_a_display_name_resolves(self) -> None:
        """Boot six's one leftover: the agent's mention handle."""
        server = fixture_server()
        handle = server.display_name(APPROVER)
        assert handle != APPROVER

        unresolved = await linear_over_fake_mcp(server).resolve_mappings(
            refs=[self._ref(handle)],
        )

        assert unresolved == ()

    async def test_a_display_name_spelled_as_a_mention_resolves(self) -> None:
        """One leading ``@`` is the vendor's syntax, not part of the name."""
        server = fixture_server()
        ref = self._ref(f"@{server.display_name(APPROVER)}")

        assert await linear_over_fake_mcp(server).resolve_mappings(refs=[ref]) == ()

    async def test_an_account_name_spelled_as_a_mention_resolves(self) -> None:
        """Either identity may carry the syntax; neither one is privileged."""
        server = fixture_server()

        unresolved = await linear_over_fake_mcp(server).resolve_mappings(
            refs=[self._ref(f"@{APPROVER}")],
        )

        assert unresolved == ()

    async def test_a_second_at_sign_belongs_to_the_name_and_is_not_stripped(
        self,
    ) -> None:
        """EXACTLY one comes off, so ``@@x`` asks for a user named ``@x``."""
        server = fixture_server()
        ref = self._ref(f"@@{server.display_name(APPROVER)}")

        assert await linear_over_fake_mcp(server).resolve_mappings(refs=[ref]) == (ref,)

    async def test_an_identity_matching_neither_is_reported_as_configured(
        self,
    ) -> None:
        """The refusal quotes the operator's own spelling, ``@`` and all.

        Stripping is a step in the MATCH, never a rewrite of the ref: what
        comes back unresolved is the ref as configured, so the boot failure
        names a string the operator can find in their own config file
        rather than an internal form nothing there contains.
        """
        server = fixture_server()
        ref = self._ref("@nobody.at.all")

        (unresolved,) = await linear_over_fake_mcp(server).resolve_mappings(refs=[ref])

        assert unresolved is ref
        assert "'@nobody.at.all'" in ref.describe()

    async def test_no_other_kind_gains_a_second_name_or_the_mention_syntax(
        self,
    ) -> None:
        """Only USER changed: every other kind is addressed by its name alone."""
        server = fixture_server()
        ref = MappingRef(
            kind=MappingKind.TEAM,
            name="board",
            identifier=f"@{TEAM_IDENTIFIERS['engineering']}",
        )

        assert await linear_over_fake_mcp(server).resolve_mappings(refs=[ref]) == (ref,)


class TestTheRecordedRepositoryMarker:
    """The kodezart-repo marker — judgment writes it, this read routes by it.

    Parsed regardless of author (KOD-169): the fire-prep pass writes it
    through the rendered mechanism and a principal can write one by hand,
    so authorship is deliberately not part of the read.
    """

    async def test_the_latest_marker_wins_whoever_wrote_it(self) -> None:
        server = fixture_server()
        server.comments.extend(
            [
                FakeMcpComment(
                    id="route-1",
                    issue_id=CLAIMED_ISSUE,
                    author="Kodezart",
                    body='<!-- kodezart-repo url="https://example.invalid/a/one" -->',
                    created_at=FIXTURE_NOW,
                ),
                FakeMcpComment(
                    id="route-2",
                    issue_id=CLAIMED_ISSUE,
                    author=APPROVER,
                    body=(
                        "rerouted by hand:\n"
                        '<!-- kodezart-repo url="https://example.invalid/a/two" -->'
                    ),
                    created_at=FIXTURE_NOW,
                ),
            ],
        )
        tracker = linear_over_fake_mcp(server)

        recorded = await tracker.recorded_repository(issue_key=CLAIMED_ISSUE)

        assert recorded == "https://example.invalid/a/two"

    async def test_no_marker_reads_as_none(self) -> None:
        tracker = linear_over_fake_mcp(fixture_server())

        assert await tracker.recorded_repository(issue_key=CLAIMED_ISSUE) is None


class TestInitiativeIdentifiers:
    async def test_every_spelling_of_every_initiative_is_answered(self) -> None:
        """One ``get_project`` read; both spellings, because a scope may be
        declared in whichever one the operator reads on the tracker."""
        server = fixture_server()
        server.projects["proj-1"] = {
            "id": "proj-1",
            "name": "a delivery project",
            "initiatives": [
                {"id": "init-9", "name": "the big initiative"},
                {"id": "init-10", "name": "the other initiative"},
            ],
        }
        tracker = linear_over_fake_mcp(server)

        identifiers = await tracker.initiative_identifiers(project_id="proj-1")

        assert identifiers == frozenset(
            {"init-9", "the big initiative", "init-10", "the other initiative"},
        )
        assert server.tool_calls("get_project") == [{"query": "proj-1"}]
