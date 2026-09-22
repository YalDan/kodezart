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
from kodezart.types.domain.dispatch import ExclusionClause
from kodezart.types.domain.operation import RepoEntry, ScopeLabel
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import GapMeasurement, ScopeWalkEvent
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import FakeDeliveryProbe, FakeTrackerPort, make_tracker_issue
from tests.services.test_scope_runtime import (
    LANE_STATE,
    SCOPE,
    STAGED,
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

    assert observation.gaps == (
        GapMeasurement(lane_key="A", criterion_keys=("A/check",)),
    )

    ready = await read_scope_ready(ref=SCOPE, tracker=port)
    assert [issue.issue_key for issue in ready.closed] == ["B"]
    assert ready.excluded == ("A/dropped", "B/twin")


def out_of_reach_board() -> FakeTrackerPort:
    """One lane, a deliverable child under it, and a criterion under the child.

    The child is the shape a container filter misses: it is nobody's direct
    criterion child, so no member read resolves it, while the criterion it
    carries is squarely inside the lane's subtree and squarely the lane's work.
    """
    return scope_board(
        criterion_row("A/check"),
        criterion_row("A1/check", parent="A1"),
        children=(
            make_tracker_issue(
                "A1",
                parent_key="A",
                issue_labels=frozenset({STAGED}),
                state_name=LANE_STATE,
                state_kind=WorkflowStateKind.STARTED,
            ),
        ),
    )


def out_of_scope(observation) -> list[tuple[str, ExclusionClause, str]]:
    """The observation's out-of-scope exclusions, as key, clause and reason."""
    return [
        (item.issue_key, item.clause, item.detail)
        for item in observation.exclusions
        if item.clause is ExclusionClause.OUT_OF_SCOPE
    ]


async def test_an_open_criterion_the_filter_cannot_reach_is_named_with_its_reason() -> (
    None
):
    """Unreachable is stated, with the reason the filter itself is stated in.

    A reader that saw neither the criterion nor a statement about it could not
    tell an obligation the scope cannot address from none at all, so the walk
    says which key and why. The lane is still fired for it: the key is on the
    lane's own gap measurement beside the criterion the filter does reach.
    """
    observation = await first_observation(out_of_reach_board())

    assert out_of_scope(observation) == [
        ("A1/check", ExclusionClause.OUT_OF_SCOPE, "the issue belongs to no project")
    ]
    assert "A/check" not in [key for key, _, _ in out_of_scope(observation)]
    assert observation.gaps == (
        GapMeasurement(lane_key="A", criterion_keys=("A/check", "A1/check")),
    )


async def test_the_same_shape_inside_the_filter_names_no_out_of_scope_entry() -> None:
    """The control arm: the filter resolves the child, so nothing is out of reach.

    Same tree, same criteria, one difference — the child is a scope member, so
    the member read resolves its criterion in its own right. An exclusion that
    appeared here would be naming reachable work unreachable.
    """
    port = out_of_reach_board()
    port.scope_memberships[SCOPE] = ("A", "A1")
    port.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key="A1")] = frozenset(
        {ScopeLabel.APPROVED}
    )

    observation = await first_observation(port)

    assert out_of_scope(observation) == []
    assert observation.gaps == (
        GapMeasurement(lane_key="A", criterion_keys=("A1/check", "A/check")),
        GapMeasurement(lane_key="A1", criterion_keys=("A1/check",)),
    )
