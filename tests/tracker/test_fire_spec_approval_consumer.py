"""Missing entry facts stop the actual pre-loop consumer before its session."""

import pytest

from kodezart.chains.organize import OrganizeAdmission
from kodezart.domain.errors import FireSpecEntryError
from tests.tracker.test_fire_spec_approval import approval, marker
from tests.tracker.test_tracker_feasibility import REQUEST, SUBJECT
from tests.tracker.test_tracker_feasibility import server as server
from tests.tracker.test_tracker_feasibility import setup as setup


@pytest.mark.parametrize("missing", ["approval", "completion"])
async def test_actual_feasibility_refuses_before_any_session_or_workspace(
    setup, tracker, server, tracker_writes, missing, monkeypatch
):
    build, runner, git, cache, workspace = setup

    async def forbidden(*args, **kwargs):
        raise AssertionError("fire entry must not rerun organize admission")

    monkeypatch.setattr(OrganizeAdmission, "assess", forbidden)
    monkeypatch.setattr(OrganizeAdmission, "verify", forbidden)
    change = approval if missing == "approval" else marker
    change(tracker, server, SUBJECT, False)
    before = tracker_writes()
    with pytest.raises(FireSpecEntryError):
        await build().validate(REQUEST)
    assert runner.arguments == []
    assert cache.calls == []
    assert workspace.calls == []
    assert git.calls == []
    assert tracker_writes() == before


async def test_approved_feasibility_does_not_rerun_organize_admission(
    setup, tracker_writes, monkeypatch
):
    build, runner, _git, _cache, _workspace = setup

    async def forbidden(*args, **kwargs):
        raise AssertionError("fire entry must not rerun organize admission")

    monkeypatch.setattr(OrganizeAdmission, "assess", forbidden)
    monkeypatch.setattr(OrganizeAdmission, "verify", forbidden)
    before = tracker_writes()
    await build().validate(REQUEST)
    assert len(runner.arguments) == 1
    assert runner.arguments[0]["session_id"] is None
    assert tracker_writes() == before
