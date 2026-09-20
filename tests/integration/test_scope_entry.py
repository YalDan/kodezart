"""What a composed scope run does before its first tick.

Over the same composed engine the rest of the scope suite drives, so the
entry these cases exercise is the one a request reaches and not a second
wiring written here.
"""

import pytest

from kodezart.domain.errors import ScopeNotApprovedError
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import ScopeWalkEvent
from tests.integration.test_scope_runtime import SCOPE, board, drive, runtime

MILESTONE = ScopeRef(kind=ScopeKind.MILESTONE, key="first-milestone")


def under_milestone(port):
    """Address the run at a milestone whose owning project is the board's."""
    port.scope_containers[MILESTONE] = ScopeContainer(
        ref=MILESTONE,
        name="first milestone",
        description="",
        url=None,
        parent=SCOPE,
    )
    port.scope_memberships[MILESTONE] = port.scope_memberships[SCOPE]
    return port


async def test_a_milestone_addressed_run_walks_under_its_projects_approval():
    """A milestone carries no label of its own; its project's admits the run."""
    port = under_milestone(board(lanes=("A",)))
    harness = runtime(port=port)
    events = [event async for event in drive(harness, scope=MILESTONE)]
    walks = [event for event in events if isinstance(event, ScopeWalkEvent)]
    assert walks[-1].observation.dispatched == ("A",)

    port.scope_label_members[SCOPE] = frozenset()
    with pytest.raises(ScopeNotApprovedError) as caught:
        _ = [event async for event in drive(harness, scope=MILESTONE, job="second")]
    assert caught.value.ref == MILESTONE


async def test_the_addressed_scopes_own_approval_admits_the_run():
    """The question is asked of the address, not of any one member."""
    port = board(lanes=("A",))
    port.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key="A")] = frozenset(
        {ScopeLabel.APPROVED}
    )
    port.scope_label_members[SCOPE] = frozenset()
    harness = runtime(port=port)
    with pytest.raises(ScopeNotApprovedError):
        _ = [event async for event in drive(harness)]
