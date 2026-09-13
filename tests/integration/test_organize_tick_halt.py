"""Retained independent halted-binding progress regression."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from kodezart.domain.errors import OrganizeHaltError
from kodezart.services.organize_tick import OrganizeTick
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from tests.chains.test_organize import RecordingWorkspace
from tests.chains.test_organize_owner import factory
from tests.fakes import FakeGitService
from tests.tracker.conftest import CLAIMED_ISSUE


@pytest.mark.parametrize("first_halts", [False, True])
async def test_halted_first_binding_does_not_starve_second_binding(first_halts):
    first, _, _ = factory(tick=True, refuse_forever=first_halts, bound=1)
    second, board, executor = factory(tick=True)
    key = "independent-second-scope"
    issue = board.server.issues.pop(CLAIMED_ISSUE)
    issue.id = key
    board.server.issues[key] = issue
    original = second._targets[0]
    target = replace(
        original,
        binding=original.binding.model_copy(
            update={"scope": ScopeRef(kind=ScopeKind.ISSUE, key=key)}
        ),
    )
    tick = OrganizeTick(
        targets=(first._targets[0], target),
        git=FakeGitService(remote_branch_shas={target.repository.trunk: "a" * 40}),
        workspace=RecordingWorkspace(),
        remote="origin",
    )
    halt = None
    try:
        await tick.run(datetime(2026, 9, 12, tzinfo=UTC))
    except OrganizeHaltError as error:
        halt = error
    assert (halt is not None) is first_halts
    if halt is not None:
        assert halt.scope == first._targets[0].binding.scope
        assert halt.report.halt.bound.value == 1
        assert halt.report.halt.bound.rounds_used == 1
        assert halt.report.halt.admission_results
    assert executor.calls, "the second independent binding never reaches its owner"
    assert "criteria complete" in board.server.issues[key].labels
