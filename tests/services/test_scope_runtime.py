"""The walker's gate on an unrecorded blocker, asked of it directly (KOD-721).

The integration walk observes the refusal as a lane failure, and a lane
failure keeps only ``str(exc)``: the typed error itself, with the fields a
caller reads off it, is reachable nowhere on that path.  So the gate is driven
here, over the shipped resolver and a delivery double, and the exception is
caught where it is raised.

Everything the gate touches is the shipped object: the resolver is a real
``BaseResolver`` over the fake port, so which blockers the gate asks about is
the production answer and not a list written here.  The two collaborators the
gate must never reach — the lane graph and the delivery probe for an origin —
are functions that refuse, so a gate that reached one would fail loudly rather
than quietly pass.
"""

from typing import NoReturn

import pytest

from kodezart.domain.errors import BaseResolutionError
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.lane_entry import LaneEntryReader
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.scope_entry import ScopeEntry
from kodezart.services.scope_runtime import ScopeWorkflowEngine
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import (
    FakeDeliveryProbe,
    FakeGitService,
    FakeRepoCache,
    FakeTrackerPort,
    make_tracker_issue,
)

LANE = "B"
BLOCKER = "A"
URL = "https://forge.invalid/fixture/repo"
REMOTE = "fixture-remote"
OPERATION = OperationConfig(
    operation_name="fixture",
    workspace="fixture",
    marker_prefixes={"run_state": "fixture-record"},
)


def board() -> FakeTrackerPort:
    """A lane blocked by a closed issue that recorded no branch at all.

    Exactly the set the resolver names as assumed landed, which is what makes
    the gate ask about it: the blocker is closed, so nothing is coming, and it
    recorded nothing, so there is no ref to stand on.
    """
    return FakeTrackerPort(
        issues=[
            make_tracker_issue(LANE, blocked_by=[BLOCKER]),
            make_tracker_issue(
                BLOCKER, state_name="Done", state_kind=WorkflowStateKind.COMPLETED
            ),
        ]
    )


def engine(port: FakeTrackerPort) -> ScopeWorkflowEngine:
    """The walker, wired as the composition wires it for everything the gate uses."""

    def no_lane(url: str) -> NoReturn:
        raise AssertionError(f"the gate asked for a lane graph: {url}")

    def no_probe(url: str) -> NoReturn:
        raise AssertionError(f"the gate asked for an origin's probe: {url}")

    git = FakeGitService()
    records = LaneRecordReader(tracker=port, operation=OPERATION)
    return ScopeWorkflowEngine(
        tracker=port,
        lane_for=no_lane,
        probe_for=no_probe,
        resolver=BaseResolver(tracker=port, git=git, remote=REMOTE),
        entries=LaneEntryReader(records=records, git=git, remote=REMOTE),
        # Neither case here reaches the entry: the gate refuses first.
        entry=ScopeEntry(approvals=port, stages_for=lambda _url: None),
        cache=FakeRepoCache(),
        repositories=(),
        git_base_url="https://forge.invalid",
        integration_workspace_dir="/fixture/integration",
    )


async def test_an_open_delivery_refusal_names_the_blocker() -> None:
    """The refusal says which blocker refused the lane, in both its statements.

    ``blocker_issue_ids`` is the field; the message text carries the key too,
    because the lane failure a consumer of the walk reads is built from
    ``str(exc)`` and nothing else.
    """
    port = board()
    probe = FakeDeliveryProbe(delivered=(BLOCKER,))

    with pytest.raises(BaseResolutionError) as caught:
        await engine(port)._gate_unrecorded_blockers(LANE, url=URL, probe=probe)

    assert caught.value.issue_id == LANE
    assert caught.value.blocker_issue_ids == (BLOCKER,)
    # The rendered tail, not the key anywhere in the text: a key of one
    # character is in half the sentences a rewording could produce, and the
    # assertion is about the message naming the blocker it refused for.
    assert str(caught.value).endswith(f"for the blocker {BLOCKER}")
    # The one read the carve-out allows, about the blocker and not the lane.
    assert probe.calls == [BLOCKER]
    assert probe.merge_state.calls == []


async def test_no_open_delivery_lets_the_lane_through() -> None:
    """The other answer raises nothing: resolution takes its unchanged arm."""
    probe = FakeDeliveryProbe()

    await engine(board())._gate_unrecorded_blockers(LANE, url=URL, probe=probe)

    assert probe.calls == [BLOCKER]
    assert probe.merge_state.calls == []
