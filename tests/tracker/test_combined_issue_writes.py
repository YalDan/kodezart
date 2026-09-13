"""No single vendor save carries both a body and a workflow state.

This backend's one issue save takes a description and a state together and
applies them as one act.  That act cannot be ordered and cannot be
half-undone: a body that did not land the way its caller asserted would
have moved the state anyway, and the issue would then read as reviewed
while carrying text nobody reviewed.

Named here rather than in the port-level suite because it is about what
one vendor is actually SENT, which is the adapter's business.  The
port-level halves — an edit moves no state, a transition rewrites no body,
a refused edit writes nothing — are in ``test_tracker_conformance.py``.
"""

from collections.abc import Mapping

import pytest

from kodezart.adapters.linear_mcp_tracker import (
    LinearMcpTracker,
    refuse_combined_issue_write,
)
from kodezart.core.backoff import RetryPolicy
from kodezart.core.errors import TrackerProtocolError
from kodezart.domain.errors import StaleWriteError
from kodezart.types.domain.dispatch import SelfWriteLedger
from kodezart.types.domain.operation import LifecycleStage
from tests.fakes import FakeLinearMcpServer
from tests.tracker.conftest import (
    APPROVED_ISSUE,
    QUEUE_STATE_LABELS,
    WORKFLOW_STATE_NAMES,
    fixture_server,
)
from tests.tracker.marker_config import MARKER_PREFIXES

SAVE_ISSUE = "save_issue"
REPLACEMENT = "a body written by its owner"


@pytest.fixture
def server() -> FakeLinearMcpServer:
    return fixture_server()


@pytest.fixture
def adapter(server: FakeLinearMcpServer) -> LinearMcpTracker:
    """The shipped adapter, typed concretely: this module names the vendor."""
    return LinearMcpTracker(
        marker_prefixes=MARKER_PREFIXES,
        issue_labels={},
        criteria_stage_label_key=None,
        scope_labels={},
        caller=server,
        queue_state_labels=QUEUE_STATE_LABELS,
        workflow_state_names=WORKFLOW_STATE_NAMES,
        team_identifiers={"engineering": "fixture-team"},
        retry=RetryPolicy(attempts=1, initial_delay=1.0),
        ledger=SelfWriteLedger(),
    )


def _saves(server: FakeLinearMcpServer) -> list[Mapping[str, object]]:
    return [arguments for tool, arguments in server.calls if tool == SAVE_ISSUE]


class TestTheTwoWritesAsSent:
    """Two saves, in one order, neither of them carrying the other's field."""

    async def test_an_edit_then_a_transition_are_two_saves_in_that_order(
        self,
        server: FakeLinearMcpServer,
        adapter: LinearMcpTracker,
    ) -> None:
        current = await adapter.read_issue(issue_key=APPROVED_ISSUE)

        await adapter.edit_description(
            target=APPROVED_ISSUE, expected=current.body, replacement=REPLACEMENT
        )
        await adapter.set_workflow_state(
            issue_key=APPROVED_ISSUE, stage=LifecycleStage.IN_REVIEW
        )

        saves = _saves(server)
        assert len(saves) == 2
        assert saves[0]["description"] == REPLACEMENT and "state" not in saves[0]
        assert saves[1]["state"] == WORKFLOW_STATE_NAMES[LifecycleStage.IN_REVIEW]
        assert "description" not in saves[1] and "patch" not in saves[1]

    async def test_a_refused_edit_sends_no_save_at_all(
        self,
        server: FakeLinearMcpServer,
        adapter: LinearMcpTracker,
    ) -> None:
        """The edit is first so its refusal stops the pair before the move."""
        with pytest.raises(StaleWriteError):
            await adapter.edit_description(
                target=APPROVED_ISSUE,
                expected="a body nobody ever wrote",
                replacement=REPLACEMENT,
            )

        assert _saves(server) == []


class TestACombinedSaveIsRefused:
    """The refusal is at the one place every issue write funnels through."""

    @pytest.mark.parametrize("body_argument", ["description", "patch"])
    async def test_a_save_carrying_a_body_and_a_state_never_reaches_the_backend(
        self,
        server: FakeLinearMcpServer,
        adapter: LinearMcpTracker,
        body_argument: str,
    ) -> None:
        with pytest.raises(TrackerProtocolError, match="separate writes"):
            await adapter._call(
                SAVE_ISSUE,
                {
                    "id": APPROVED_ISSUE,
                    body_argument: REPLACEMENT,
                    "state": "In Review",
                },
            )

        assert server.calls == []

    @pytest.mark.parametrize("body_argument", ["description", "patch"])
    def test_the_rule_refuses_both_body_arguments(self, body_argument: str) -> None:
        with pytest.raises(TrackerProtocolError, match="separate writes"):
            refuse_combined_issue_write(
                {"id": APPROVED_ISSUE, body_argument: REPLACEMENT, "state": "Done"},
            )

    @pytest.mark.parametrize(
        "arguments",
        [
            {"id": APPROVED_ISSUE, "description": REPLACEMENT},
            {"id": APPROVED_ISSUE, "patch": [{"op": "append", "text": "x"}]},
            {"id": APPROVED_ISSUE, "state": "Done"},
            {"id": APPROVED_ISSUE, "title": "renamed", "state": "Done"},
            {"id": APPROVED_ISSUE, "addLabels": ["queue:done"], "state": "Done"},
        ],
    )
    def test_a_save_touching_one_surface_is_allowed(
        self, arguments: Mapping[str, object]
    ) -> None:
        """The pair is what is refused, never a state write on its own."""
        refuse_combined_issue_write(arguments)
