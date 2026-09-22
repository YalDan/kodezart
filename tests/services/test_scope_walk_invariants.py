"""What one walk observation states about the criteria the gap does not carry.

Both readings are made at the walker itself, over a board the shipped
``read_scope_ready`` reads, because both are about what an OBSERVATION says:
the keys a criterion's own state set aside, and the keys the scope's filter
cannot address in their own right. A test written against the ready set alone
could not tell either of them from a field nobody fills.

The walk is driven exactly one observation deep. Nothing here needs a fire, a
clone or an origin, so the lane graph is inert and every other collaborator the
walker could reach refuses: a subject that went further would fail loudly.
"""

from typing import NoReturn

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.scope_runtime import ScopeWalkEvent
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import FakeDeliveryProbe, FakeTrackerPort
from tests.services.test_scope_runtime import (
    SCOPE,
    URL,
    criterion_row,
    engine,
    scope_board,
)


class InertLane:
    """A lane graph that answers ``delivers`` and refuses everything else.

    Selection asks a lane graph one question before it offers anything, so a
    walk driven to its first observation has to be handed one. ``delivers`` is
    False because a delivery-only turn is a different reading; every other
    attribute raises, so a walk that fired would say so here.
    """

    delivers = False

    def __getattr__(self, name: str) -> NoReturn:
        raise AssertionError(f"the walk reached the lane graph: {name}")


async def first_observation(port: FakeTrackerPort):
    """The walk's first ``scope_walk`` observation over *port*, and nothing more."""
    walk = engine(
        port,
        lane_for=lambda _: InertLane(),
        probe_for=lambda _: FakeDeliveryProbe(),
        repositories=(RepoEntry(url=URL, trunk="main"),),
    ).run(
        prompt="Request prose is not the native subject",
        repo_path="/fixture/repo",
        repo_url=URL,
        base_spec=trunk_base("unused-request-default"),
        scope=SCOPE,
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=[],
        cache_key="fixture-job",
    )
    try:
        event = await anext(walk)
    finally:
        await walk.aclose()
    assert isinstance(event, ScopeWalkEvent)
    return event.observation


async def test_the_walk_lists_the_excluded_criteria_beside_each_gap() -> None:
    """A criterion set aside on its own state is named, never dropped in silence.

    Two lanes, so the claim is scope-wide and not one lane's: A owes one check
    and had another canceled under it, and B's only criterion is a duplicate,
    which leaves B owing nothing at all. A reader of this observation can tell
    B-owes-nothing from B-was-never-read, which is the whole point of naming
    the keys beside the gap rather than shortening the gap silently.
    """
    port = scope_board(
        criterion_row("A/check"),
        criterion_row(
            "A/dropped",
            state_name="Canceled",
            state_kind=WorkflowStateKind.CANCELED,
        ),
        criterion_row(
            "B/twin",
            parent="B",
            state_name="Duplicate",
            state_kind=WorkflowStateKind.DUPLICATE,
        ),
        lanes=("A", "B"),
    )

    observation = await first_observation(port)

    assert observation.excluded_criteria == ("A/dropped", "B/twin")
    assert observation.unresolved_criteria == ("A/check",)
    assert observation.ready == ("A",)

    ready = await read_scope_ready(ref=SCOPE, tracker=port)
    assert [issue.issue_key for issue in ready.closed] == ["B"]
    assert ready.excluded == ("A/dropped", "B/twin")
