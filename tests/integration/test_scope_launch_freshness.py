"""Fresh admission is required after every awaited launch preparation."""

import pytest

from kodezart.types.domain.scope import ScopeKind, ScopeRef
from tests.integration.test_scope_runtime import (
    ORIGIN,
    SCOPE,
    board,
    drive,
    lane_failures,
    lane_of,
    owed_again,
    runtime,
    walk_reporting,
)


@pytest.mark.parametrize("change", ["approval", "membership", "blocker", "unchanged"])
async def test_paused_resume_rechecks_readiness_after_awaited_delivery_probe(
    monkeypatch, change
):
    harness = runtime()
    lane_of(harness).fire.native_graph.interrupt_before_nodes = [
        "review_against_ticket"
    ]
    await walk_reporting(
        harness, kind="ScopeReadError", match="no final delivery phase"
    )
    port = harness.port
    # The loop ran before this pause and crossed its criterion off, so the
    # lane is closed and no walk offers it again; what the resume is about is
    # readiness, so the lane is made owed again the way the product does it.
    owed_again(port)
    fresh = runtime(port=port, saver=harness.saver)
    probe = fresh.engine._scoped_arm._probe_for(ORIGIN)
    count = 0

    async def change_during_probe(*, repo_url, issue_key):
        nonlocal count
        count += 1
        if count == 2:
            if change == "approval":
                port.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key="A")] = (
                    frozenset()
                )
            elif change == "membership":
                port.scope_memberships[SCOPE] = ()
            elif change == "blocker":
                other = board(lanes=("B", "A"), blocked={"A": ("B",)})
                port.issues["A"] = other.issues["A"]
                port.issues["B"] = other.issues["B"]
                port.issues["B/check"] = other.issues["B/check"]
                port.scope_memberships[SCOPE] = ("A", "B")
                port.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key="B")] = (
                    frozenset()
                )
        return False

    monkeypatch.setattr(probe, "open_delivery_exists", change_during_probe)
    events = [event async for event in drive(fresh)]
    if change == "unchanged":
        assert len(fresh.executor.evaluation_prompts) == 1
        # Nothing changed, so nothing about this lane was refused either.
        assert not lane_failures(events)
    else:
        assert not fresh.executor.evaluation_prompts
