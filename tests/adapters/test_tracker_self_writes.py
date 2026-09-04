"""The tracker adapter tells the ledger what its own writes left (KOD-175).

The pass gates decide "is this movement ours?" by comparing an issue's
newest stamp against what this process's last write left on it, and the
comparison is only as good as the recording.  Both recording paths are
here, because the backend answers a write in two shapes and only one of
them carries the stamp: an issue write comes back AS the issue, and a
comment write comes back as a comment while moving the issue underneath
it — which is the shape every claim, marker and base spec takes.
"""

import inspect
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Final

from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.core.errors import McpSessionClosedError
from kodezart.core.protocols import McpToolResult
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.dispatch import SelfWriteLedger
from kodezart.types.domain.operation import LifecycleStage, QueueState
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue

ISSUE: Final[str] = "FIX-1"
TEAM: Final[str] = "fixture-team"
TEAM_KEY: Final[str] = "engineering"
APPROVED_LABEL: Final[str] = "queue:approved"
PROPOSED_LABEL: Final[str] = "queue:proposed"
DONE_STATE: Final[str] = "Done"
STAMP: Final[datetime] = datetime(2026, 9, 1, 17, 55, tzinfo=UTC)


def _server() -> FakeLinearMcpServer:
    return FakeLinearMcpServer(
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


def _tracker(server: FakeLinearMcpServer, ledger: SelfWriteLedger) -> LinearMcpTracker:
    return LinearMcpTracker(
        caller=server,
        queue_state_labels={
            QueueState.APPROVED.value: APPROVED_LABEL,
            QueueState.PROPOSED.value: PROPOSED_LABEL,
        },
        workflow_state_names={LifecycleStage.DONE: DONE_STATE},
        team_identifiers={TEAM_KEY: TEAM},
        max_retries=0,
        retry_backoff_factor=1.0,
        ledger=ledger,
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


async def test_a_marker_write_records_the_stamp_a_read_back_finds() -> None:
    """The comment log: the answer is a comment, so the issue is read back.

    Every claim, renewal, release, work ref and base spec rides this shape,
    and it is the one the measured boot woke itself on 30 ticks out of 31.
    What the ledger holds is the stamp the write LEFT — strictly past the
    one the issue carried before it — so a read placed ahead of the write
    would record the wrong one and fail here.
    """
    ledger = SelfWriteLedger()
    server = _server()
    tracker = _tracker(server, ledger)

    await tracker.record_base_spec(issue_key=ISSUE, spec=trunk_base("main"))

    stored = await tracker.read_issue(issue_key=ISSUE)
    assert stored.updated_at > STAMP
    assert ledger.wrote(issue_key=ISSUE, updated_at=stored.updated_at)


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
    """The ledger entry is bookkeeping; the write already landed (KOD-172).

    Every comment-shaped write reads the issue back to learn the stamp it
    left.  That read used to be inside the write: a session that died in
    between raised out of ``post_comment``, and a caller told its write
    failed writes again — a second marker on a log that already carries
    the first, from a call whose comment the caller never saw.

    What the failure costs instead is one ledger entry, which is one extra
    wake-up on this operation's own churn.
    """
    server = _server()
    ledger = SelfWriteLedger()
    tracker = LinearMcpTracker(
        caller=_ReadBackGone(server),
        queue_state_labels={
            QueueState.APPROVED.value: APPROVED_LABEL,
            QueueState.PROPOSED.value: PROPOSED_LABEL,
        },
        workflow_state_names={LifecycleStage.DONE: DONE_STATE},
        team_identifiers={TEAM_KEY: TEAM},
        max_retries=0,
        retry_backoff_factor=1.0,
        ledger=ledger,
    )

    comment = await tracker.post_comment(issue_key=ISSUE, body="a marker")

    assert comment.body == "a marker"
    assert len(server.comments) == 1
    assert ledger.wrote(
        issue_key=ISSUE, updated_at=server.issues[ISSUE].updated_at
    ) is (False)
