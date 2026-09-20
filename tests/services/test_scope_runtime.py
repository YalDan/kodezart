"""The walker's own refusals, asked of the shipped engine directly.

The integration walk observes a refusal as a lane failure, and a lane failure
keeps only ``str(exc)``: the typed error itself, with the fields a caller reads
off it, is reachable nowhere on that path.  So the gate on an unrecorded
blocker (KOD-721) is driven here, over the shipped resolver and a delivery
double, and the exception is caught where it is raised.  The refusal of an
origin with no open-delivery reader at all is driven the same way, for the same
reason.

Everything these tests touch is the shipped object: the resolver is a real
``BaseResolver`` over the fake port, so which blockers the gate asks about is
the production answer and not a list written here.  The collaborators a test's
own subject must never reach are functions that refuse, so a subject that
reached one would fail loudly rather than quietly pass.
"""

from typing import NoReturn

import pytest

from kodezart.domain.errors import BaseResolutionError, ScopedExecutionUnavailableError
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.lane_entry import LaneEntryReader
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.scope_runtime import ScopeWorkflowEngine
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.operation import OperationConfig, RepoEntry
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode
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


class RefusingLane:
    """A lane graph nothing may touch, whichever attribute is asked for.

    The walker asks its composition for a lane graph before it asks for the
    origin's delivery reader, so a test about the reader's absence has to hand
    one over; a walk that then went on to USE it has passed the refusal this
    test is about, and says so here rather than failing somewhere downstream.
    """

    def __getattr__(self, name: str) -> NoReturn:
        raise AssertionError(f"the walk reached the lane graph: {name}")


def engine(
    port: FakeTrackerPort,
    *,
    lane_for=None,
    probe_for=None,
    repositories=(),
) -> ScopeWorkflowEngine:
    """The walker, wired as the composition wires it for everything under test.

    *lane_for* and *probe_for* default to functions that refuse: a subject that
    reached a collaborator it has no business reaching fails loudly.
    """

    def no_lane(url: str) -> NoReturn:
        raise AssertionError(f"the walker asked for a lane graph: {url}")

    def no_probe(url: str) -> NoReturn:
        raise AssertionError(f"the walker asked for an origin's probe: {url}")

    git = FakeGitService()
    records = LaneRecordReader(tracker=port, operation=OPERATION)
    return ScopeWorkflowEngine(
        tracker=port,
        lane_for=no_lane if lane_for is None else lane_for,
        probe_for=no_probe if probe_for is None else probe_for,
        resolver=BaseResolver(tracker=port, git=git, remote=REMOTE),
        entries=LaneEntryReader(records=records, git=git, remote=REMOTE),
        cache=FakeRepoCache(),
        repositories=repositories,
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


async def test_an_origin_with_no_open_delivery_reader_refuses_the_scope() -> None:
    """An origin the composition built no delivery reader for is not walked.

    That reader is what the gate above stands on, so an origin without one
    cannot answer the single question a lane's turn asks of a forge. The walk
    refuses the origin with its own typed error on the first event asked of it,
    rather than walking on with the gate quietly skipped: a lane would then be
    resolved against a trunk its blocker's work may not be on. Removing both
    candidate probes left this refusal exactly where it was (KOD-431).
    """
    walk = engine(
        board(),
        lane_for=lambda _: RefusingLane(),
        probe_for=lambda _: None,
        repositories=(RepoEntry(url=URL, trunk="main"),),
    ).run(
        prompt="Request prose is not the native subject",
        repo_path="/fixture/repo",
        repo_url=URL,
        base_spec=trunk_base("unused-request-default"),
        scope=ScopeRef(kind=ScopeKind.PROJECT, key="fixture-scope"),
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=[],
        cache_key="fixture-job",
    )

    # On the first event asked of it, because the body of an async generator
    # does not run until then: a walk that refused only later would already
    # have reported ticks a consumer could act on.
    with pytest.raises(ScopedExecutionUnavailableError) as caught:
        await anext(walk)

    assert "open-delivery reader" in str(caught.value)
    assert caught.value.ref == ScopeRef(kind=ScopeKind.PROJECT, key="fixture-scope")
