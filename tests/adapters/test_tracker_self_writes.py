"""Native writes leave atomic issue stamps or explicit comment receipts.

The behavioral own-churn proof survives the source-authorized protocol
change: comment responses have no issue stamp and no later read may claim
one. Actual gate tests replace the obsolete read-back-stamp assertion.
"""

import inspect
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest

from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.core.backoff import RetryPolicy
from kodezart.core.errors import McpSessionClosedError
from kodezart.core.protocols import McpToolCaller, McpToolResult
from kodezart.services.pass_gate import PassGate
from kodezart.types.domain.branch import WorkRef, WorkRefRole, trunk_base
from kodezart.types.domain.dispatch import PassSignal, SelfWriteLedger
from kodezart.types.domain.operation import LifecycleStage, QueueState
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue
from tests.tracker.marker_config import MARKER_PREFIXES

ISSUE: Final[str] = "FIX-1"
TEAM: Final[str] = "fixture-team"
TEAM_KEY: Final[str] = "engineering"
APPROVED_LABEL: Final[str] = "queue:approved"
PROPOSED_LABEL: Final[str] = "queue:proposed"
DONE_STATE: Final[str] = "Done"
STAMP: Final[datetime] = datetime(2026, 9, 1, 17, 55, tzinfo=UTC)


def _server() -> FakeLinearMcpServer:
    return FakeLinearMcpServer(
        comment_clock=lambda: STAMP,
        issues=[
            FakeMcpIssue(
                id=ISSUE,
                title="an issue this operation works",
                description="fixture body",
                priority_raw=1,
                status="Todo",
                status_type="unstarted",
                team=TEAM,
                labels=[APPROVED_LABEL],
                created_at=STAMP - timedelta(days=1),
                updated_at=STAMP,
            ),
        ],
        teams=[TEAM],
        labels=[APPROVED_LABEL, PROPOSED_LABEL],
        statuses={TEAM: ["Todo", "In Progress", DONE_STATE]},
        state_types={
            "Todo": "unstarted",
            "In Progress": "started",
            DONE_STATE: "completed",
        },
    )


def _tracker(server: McpToolCaller, ledger: SelfWriteLedger) -> LinearMcpTracker:
    return LinearMcpTracker(
        marker_prefixes=MARKER_PREFIXES,
        issue_labels={"criterion": "acceptance-condition"},
        scope_labels={},
        criteria_stage_label_key=None,
        caller=server,
        queue_state_labels={
            QueueState.APPROVED.value: APPROVED_LABEL,
            QueueState.PROPOSED.value: PROPOSED_LABEL,
        },
        workflow_state_names={LifecycleStage.DONE: DONE_STATE},
        team_identifiers={TEAM_KEY: TEAM},
        retry=RetryPolicy(attempts=1, initial_delay=1.0),
        ledger=ledger,
        clock=lambda: STAMP,
    )


async def test_a_write_answered_with_the_issue_records_that_answers_stamp() -> None:
    """The lifecycle transition: the response IS the stamp, so no extra read."""
    ledger = SelfWriteLedger()
    tracker = _tracker(_server(), ledger)

    issue = await tracker.set_workflow_state(
        issue_key=ISSUE,
        stage=LifecycleStage.DONE,
    )

    assert issue.updated_at > STAMP
    assert ledger.wrote(issue_key=ISSUE, updated_at=issue.updated_at)


async def test_a_marker_records_a_mutation_without_an_issue_stamp() -> None:
    ledger = SelfWriteLedger()
    server = _server()
    tracker = _tracker(server, ledger)
    gate = _gate(tracker, ledger)
    assert (await gate.delta()).changed == (ISSUE,)

    await tracker.record_base_spec(issue_key=ISSUE, spec=trunk_base("main"))

    stored = await tracker.read_issue(issue_key=ISSUE)
    assert stored.updated_at > STAMP
    assert not ledger.wrote(issue_key=ISSUE, updated_at=stored.updated_at)
    assert len(ledger.receipts(issue_key=ISSUE)[1]) == 1
    assert (await gate.delta()).changed == ()
    assert (
        gate.mark(PassSignal.approved_changed, container=TEAM_KEY) == stored.updated_at
    )


async def test_an_issue_this_adapter_never_wrote_to_is_not_in_the_ledger() -> None:
    """The paired negative: reading is not writing, and neither is silence."""
    ledger = SelfWriteLedger()
    server = _server()
    tracker = _tracker(server, ledger)

    stored = await tracker.read_issue(issue_key=ISSUE)

    assert not ledger.wrote(issue_key=ISSUE, updated_at=stored.updated_at)


def test_the_adapter_cannot_be_built_without_the_ledger_that_will_be_read() -> None:
    """A defaulted ledger is a gate that never wakes (KOD-175).

    The adapter used to make its own when none was handed in.  Nothing
    failed and nothing warned: the writes were recorded faithfully into a
    record no gate held a reference to, so every gated pass compared a
    principal's edit against an empty ledger and slept through the
    movement it had made itself.  A ledger nobody can read is not a
    weaker ledger, it is the absence of one, and the constructor says so.
    """
    ledger = inspect.signature(LinearMcpTracker.__init__).parameters["ledger"]

    assert ledger.default is inspect.Parameter.empty
    assert ledger.kind is inspect.Parameter.KEYWORD_ONLY


class _ReadBackGone:
    """A caller that serves every write and refuses every issue read.

    The shape a session takes when the server goes away between the write
    and the bookkeeping read that follows it — the write landed, and
    nothing that happens afterwards can un-land it.
    """

    def __init__(self, server: FakeLinearMcpServer) -> None:
        self.server = server

    async def call_tool(
        self,
        *,
        name: str,
        arguments: Mapping[str, object],
    ) -> McpToolResult:
        if name == "get_issue":
            msg = "the session went away after the write"
            raise McpSessionClosedError(msg, server_name="linear", tool_name=name)
        return await self.server.call_tool(name=name, arguments=arguments)


async def test_a_read_back_that_fails_does_not_fail_the_write_it_recorded() -> None:
    """A landed comment needs no follow-up issue read to return successfully.

    The previous bookkeeping-read failure remains a paired native control:
    this caller refuses all get_issue calls, but the exact write response
    and explicit receipt are sufficient. No issue stamp is claimed.
    """
    server = _server()
    ledger = SelfWriteLedger()
    tracker = LinearMcpTracker(
        marker_prefixes=MARKER_PREFIXES,
        issue_labels={"criterion": "acceptance-condition"},
        scope_labels={},
        criteria_stage_label_key=None,
        caller=_ReadBackGone(server),
        queue_state_labels={
            QueueState.APPROVED.value: APPROVED_LABEL,
            QueueState.PROPOSED.value: PROPOSED_LABEL,
        },
        workflow_state_names={LifecycleStage.DONE: DONE_STATE},
        team_identifiers={TEAM_KEY: TEAM},
        retry=RetryPolicy(attempts=1, initial_delay=1.0),
        ledger=ledger,
    )

    comment = await tracker.post_comment(issue_key=ISSUE, body="a marker")

    assert comment.body == "a marker"
    assert len(server.comments) == 1
    assert ledger.wrote(
        issue_key=ISSUE, updated_at=server.issues[ISSUE].updated_at
    ) is (False)


# ---------------------------------------------------------------------------
# KOD-197: every comment-shaped write path, over the SHIPPED adapter
# ---------------------------------------------------------------------------


#: One holder for the writes below, and a second for the claim the first
#: one loses.  Both are pass identities of the shape the dispatcher mints.
HOLDER: Final[str] = "dispatch-pass-a"
RIVAL: Final[str] = "dispatch-pass-b"
LEASE_SECONDS: Final[float] = 60.0

Write = Callable[[LinearMcpTracker], Awaitable[None]]


async def _legacy_claim(tracker: LinearMcpTracker) -> None:
    await tracker.post_comment(
        issue_key=ISSUE,
        body=(
            f'<!-- {MARKER_PREFIXES["claim"]} holder="{HOLDER}" '
            'expires-at="2099-01-01T00:00:00+00:00" -->'
        ),
    )


async def _release(tracker: LinearMcpTracker) -> None:
    await _legacy_claim(tracker)
    await tracker.release_claim(issue_key=ISSUE, holder=HOLDER)


async def _plain_comment(tracker: LinearMcpTracker) -> None:
    await tracker.post_comment(issue_key=ISSUE, body="a note this operation left")


async def _work_ref(tracker: LinearMcpTracker) -> None:
    await tracker.record_work_ref(
        ref=WorkRef(
            issue_id=ISSUE,
            role=WorkRefRole.DELIVERABLE,
            branch="kodezart/fixture-deliverable",
            pushed_head_sha="0" * 40,
            recorded_at=STAMP,
        ),
    )


@pytest.mark.parametrize(
    "write",
    [
        _legacy_claim,
        _release,
        _plain_comment,
        _work_ref,
    ],
    ids=["legacy-marker", "release", "comment", "work-ref"],
)
async def test_every_comment_shaped_write_records_only_its_own_mutations(
    write: Write,
) -> None:
    """The paths the measured incident actually rode (KOD-175).

    These supported native writes retain their own receipt attribution.
    Unfenced acquisition and renewal now refuse before mutation; their
    former successful-write fixtures cannot establish supported behavior.
    Legacy marker cleanup remains a real native delete path.
    """
    ledger = SelfWriteLedger()
    server = _server()
    tracker = _tracker(server, ledger)

    gate = _gate(tracker, ledger)
    assert (await gate.delta()).changed == (ISSUE,)

    await write(tracker)

    stored = await tracker.read_issue(issue_key=ISSUE)
    assert stored.updated_at > STAMP, "the write moved the issue"
    assert not ledger.wrote(issue_key=ISSUE, updated_at=stored.updated_at)
    assert ledger.receipts(issue_key=ISSUE)[1]
    assert (await gate.delta()).changed == ()
    assert (
        gate.mark(PassSignal.approved_changed, container=TEAM_KEY) == stored.updated_at
    )


@pytest.mark.parametrize("foreign", [None, "body", "state", "comment"])
async def test_comment_readback_cannot_claim_a_concurrent_principal_movement(
    foreign: str | None,
) -> None:

    server = _server()
    ledger = SelfWriteLedger()

    class Interleaved:
        async def call_tool(
            self, *, name: str, arguments: Mapping[str, object]
        ) -> McpToolResult:
            result = await server.call_tool(name=name, arguments=arguments)
            if name == "save_comment" and arguments.get("body") == "our own note":
                if foreign == "body":
                    await server.call_tool(
                        name="save_issue",
                        arguments={"id": ISSUE, "description": "principal's new body"},
                    )
                elif foreign == "state":
                    await server.call_tool(
                        name="save_issue",
                        arguments={"id": ISSUE, "state": DONE_STATE},
                    )
                elif foreign == "comment":
                    await server.call_tool(
                        name="save_comment",
                        arguments={"issueId": ISSUE, "body": "principal's new note"},
                    )
            return result

    tracker = _tracker(Interleaved(), ledger)
    gate = PassGate(
        tracker=tracker,
        ledger=ledger,
        signals=[PassSignal.approved_changed],
        team_keys=[TEAM_KEY],
        repo_urls=[],
        page_size=50,
    )
    assert (await gate.delta()).changed == (ISSUE,)
    await tracker.post_comment(issue_key=ISSUE, body="our own note")
    changed = (await gate.delta()).changed
    assert changed == (() if foreign is None else (ISSUE,))


def _gate(tracker: LinearMcpTracker, ledger: SelfWriteLedger) -> PassGate:
    return PassGate(
        tracker=tracker,
        ledger=ledger,
        signals=[PassSignal.approved_changed],
        team_keys=[TEAM_KEY],
        repo_urls=[],
        page_size=50,
    )
