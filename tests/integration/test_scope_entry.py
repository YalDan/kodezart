"""What a composed scope run does before its first tick.

Over the same composed engine the rest of the scope suite drives, so the
entry these cases exercise is the one a request reaches and not a second
wiring written here.
"""

import pytest

from kodezart.domain.errors import OrganizeHaltError, ScopeNotApprovedError
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.organize_owner import (
    OrganizeReport,
    StageHaltCause,
    StageHaltReport,
)
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


async def test_the_stages_precede_the_first_ready_read_and_a_halt_prevents_it():
    """Whatever the entry does, it is finished before a member is read.

    The engine's own collaborator is wrapped here rather than replaced, so
    the order asserted is the composed engine's and not a second wiring's.
    A halt raised by the entry reaches the caller and the walk never starts.
    """
    port = board(lanes=("A",))
    harness = runtime(port=port)
    entry = harness.engine._scoped_arm._entry
    order = []

    async def recorded_admit(**kwargs):
        order.append("admit")
        return await entry.admit(**kwargs)

    async def recorded_scope_issues(*, ref):
        order.append("member read")
        return await original_scope_issues(ref=ref)

    original_scope_issues = port.scope_issues
    harness.engine._scoped_arm._entry = type(
        "RecordingEntry", (), {"admit": staticmethod(recorded_admit)}
    )()
    port.scope_issues = recorded_scope_issues
    events = [event async for event in drive(harness)]
    assert [event for event in events if isinstance(event, ScopeWalkEvent)]
    assert order[0] == "admit"
    assert order.count("admit") == 1

    halting = board(lanes=("A",))
    second = runtime(port=halting)
    report = OrganizeReport(
        halt=StageHaltReport.model_validate(
            {
                "cause": "human_decision",
                "questions": [
                    {
                        "kind": "unresolved",
                        "issueId": "A",
                        "question": "Which reading of the subject governs?",
                        "evidence": "The body admits two readings.",
                    }
                ],
            }
        )
    )

    async def halting_admit(*, scope, repository, job_id):
        raise OrganizeHaltError(scope=scope, report=report)

    second.engine._scoped_arm._entry = type(
        "HaltingEntry", (), {"admit": staticmethod(halting_admit)}
    )()
    with pytest.raises(OrganizeHaltError) as caught:
        _ = [event async for event in drive(second, job="halted")]
    assert caught.value.report.halt.cause is StageHaltCause.HUMAN_DECISION
    assert second.executor.schema_calls == []
