"""Retained branch composition is independent of future FIRE admission."""

import pytest

from kodezart.types.domain.tracker import WorkflowStateKind
from kodezart.types.domain.union import UnionOutcome
from tests.chains.test_delivery_coordinator import PROJECT, issue
from tests.chains.test_union_exit_invariance import build_delivery


@pytest.mark.parametrize("barrier", ["none", "open-decision", "backlog-criterion"])
async def test_retained_union_is_observable_under_future_stage_barrier(
    tmp_path, barrier
):
    fixture = await build_delivery(tmp_path / "world")
    if barrier == "open-decision":
        decision = issue("decision-record", issue_labels=frozenset({"decision"}))
        fixture.tracker.issues[decision.issue_key] = decision
        fixture.tracker.scope_memberships[PROJECT] = ("a", "z", decision.issue_key)
    elif barrier == "backlog-criterion":
        fixture.tracker.issues["a-check"] = fixture.tracker.issues[
            "a-check"
        ].model_copy(
            update={"state_kind": WorkflowStateKind.BACKLOG, "state_name": "Backlog"}
        )

    result = await fixture.coordinator().verify()

    assert result.composition_order == ("z", "a")
    assert result.outcome is UnionOutcome.GREEN
    assert fixture.git.created == fixture.git.removed == [result.scratch_path]
