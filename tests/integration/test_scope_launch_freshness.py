"""Fresh admission is required after every awaited launch preparation."""

import pytest

from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.integration.test_scope_runtime import (
    A_KEYS,
    SCOPE,
    TWO_CHECKS,
    WalkRepos,
    board,
    drive,
    echoes,
    first_fire,
    lane_failures,
    resumable,
)


@pytest.mark.parametrize("change", ["approval", "membership", "blocker", "unchanged"])
async def test_re_entry_rechecks_readiness_after_the_awaited_record_read(
    monkeypatch, change
):
    """Admission has to still hold at the graph's launch, not only before it.

    The awaited preparation a resumed lane makes is the read of its own record,
    so that is where a board is changed under it here: the record listing for
    lane A yields, the board moves, and the launch must not happen. Run one
    leaves A a record and one criterion still owed, which is the state a killed
    process leaves behind (KOD-785, KOD-806: the record read is the awaited
    preparation).

    The unchanged case asserts what the fire did rather than how many times it
    graded: the exact-count clause this test used to carry is replaced by the
    completion assertions below, because where the awaited preparation sits no
    longer decides the number of gradings.
    """
    repos = WalkRepos()
    port = board(lanes=("A",), checks=TWO_CHECKS)
    await first_fire(port, repos)
    second = resumable(port=port, repos=repos, evaluations=echoes(passed=set(A_KEYS)))
    listing = port.list_comments
    reads = 0

    async def change_during_record_read(*, issue_key):
        nonlocal reads
        reads += 1
        if reads == 1:
            if change == "approval":
                port.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key="A")] = (
                    frozenset()
                )
            elif change == "membership":
                port.scope_memberships[SCOPE] = ()
            elif change == "blocker":
                other = board(
                    lanes=("B", "A"), blocked={"A": ("B",)}, checks=TWO_CHECKS
                )
                port.issues["A"] = other.issues["A"]
                port.issues["B"] = other.issues["B"]
                port.issues["B/check"] = other.issues["B/check"]
                port.issues["B/second"] = other.issues["B/second"]
                port.scope_memberships[SCOPE] = ("A", "B")
                port.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key="B")] = (
                    frozenset()
                )
        return await listing(issue_key=issue_key)

    monkeypatch.setattr(port, "list_comments", change_during_record_read)
    events = [event async for event in drive(second, job="second-job")]
    assert reads, "the resumed lane made no record read to change the board under"
    if change == "unchanged":
        # Nothing moved, so the lane launched and its fire ran to the end:
        # it graded, nothing refused it, and it owes nothing afterwards.
        assert second.executor.evaluation_prompts
        assert not lane_failures(events)
        assert all(
            port.issues[key].state_kind is WorkflowStateKind.COMPLETED for key in A_KEYS
        )
    else:
        assert not second.executor.evaluation_prompts
