"""Fresh admission is required after every awaited launch preparation."""

import pytest

from kodezart.domain.errors import FireSpecEntryError, ScopeReadError
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from tests.integration.test_scope_runtime import (
    ORIGIN,
    SCOPE,
    board,
    drive,
    lane_of,
    runtime,
)


@pytest.mark.parametrize("change", ["approval", "membership", "blocker", "unchanged"])
async def test_paused_resume_rechecks_readiness_after_awaited_delivery_probe(
    monkeypatch, change
):
    harness = runtime()
    lane_of(harness).fire.native_graph.interrupt_before_nodes = [
        "review_against_ticket"
    ]
    with pytest.raises(ScopeReadError, match="no final delivery phase"):
        _ = [event async for event in drive(harness)]
    port = harness.port
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
    try:
        _ = [event async for event in drive(fresh)]
    except (ScopeReadError, FireSpecEntryError):
        if change == "unchanged":
            raise
    if change == "unchanged":
        assert len(fresh.executor.evaluation_prompts) == 1
    else:
        assert not fresh.executor.evaluation_prompts
