"""A scope is either being organized or being run, and the gate is approval.

Both sides are the composed ones — the scheduled pass's tick from the
composition root and the scope run's own entry — over one board, so what
these cases read is the boundary a deployment has rather than a second
wiring written here.
"""

from datetime import UTC, datetime

import pytest

from kodezart.composition import organize as organize_composition
from kodezart.domain.errors import OrganizeWriteRefusalError, ScopeNotApprovedError
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.scope_runtime import ScopeWalkEvent
from tests.fakes import SUPPRESS_ALL_SKILLS, PassThroughGate, make_prompt_provider
from tests.integration.test_scope_entry import (
    GROOM_MARKER,
    HEARTBEAT_CONFIG,
    STAGED,
    TICKET_MARKER,
    approve,
    errors,
    staging_runtime,
    standing_board,
    standing_operation,
)
from tests.integration.test_scope_runtime import SCOPE, drive

LANES = ("A", "B")
NOW = datetime(2026, 9, 22, tzinfo=UTC)


def triaged(port):
    """The board before anybody approved it, carrying the pre-approval gate."""
    port.scope_label_members[SCOPE] = frozenset({ScopeLabel.TRIAGE})
    return port


def grooming(harness, operation):
    """The scheduled pass's tick, built by the composition root."""
    return organize_composition.build_organize_tick(
        config=HEARTBEAT_CONFIG,
        operation=operation,
        tracker=harness.port,
        runner=harness.service,
        workspace=harness.workspace,
        git=harness.git,
        prompts=make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
    )


def markers(port):
    """Every stage marker each member carries, keyed by member."""
    return {
        key: port.issues[key].issue_labels
        & frozenset({GROOM_MARKER, TICKET_MARKER, STAGED})
        for key in LANES
    }


async def test_an_unapproved_scope_is_groomed_and_admits_no_run(monkeypatch):
    """The pre-approval row works the board; the run is refused before it reads.

    The tick leaves its own marker on every member and no lease behind. The
    entry of a run over the same board refuses on approval, so no stage
    marker joins them and no session is opened for one.
    """
    port = triaged(standing_board(LANES))
    operation = standing_operation()
    harness = staging_runtime(
        port, LANES, monkeypatch=monkeypatch, builds=[], operation=operation
    )
    assert await grooming(harness, operation).run(NOW) is PassRun.RAN
    assert markers(port) == dict.fromkeys(LANES, frozenset({GROOM_MARKER}))
    assert port.leases == {}
    spent = len(harness.executor.organize_calls)

    with pytest.raises(ScopeNotApprovedError) as refused:
        _ = [event async for event in drive(harness, job="unapproved-run")]
    assert refused.value.ref == SCOPE
    assert len(harness.executor.organize_calls) == spent
    assert markers(port) == dict.fromkeys(LANES, frozenset({GROOM_MARKER}))
    assert port.leases == {}


async def test_approval_during_a_grooming_session_refuses_its_write_and_frees_the_run(
    monkeypatch,
):
    """Approval inside a grooming session refuses that row's next write.

    The scope is approved while the pass reads back the first member's
    marker. The pre-approval row is refused on the reading it makes before
    every write, so the second member keeps no marker and the row holds
    nothing; the run the same approval admits stages every member and walks.
    """
    port = triaged(standing_board(LANES))
    operation = standing_operation()
    harness = staging_runtime(
        port, LANES, monkeypatch=monkeypatch, builds=[], operation=operation
    )
    original = harness.executor.stream

    async def approving(**kwargs):
        async for event in original(**kwargs):
            if (
                kwargs["output_format"]["schema"].get("title") == "WriteBackFinding"
                and '"kind":"issue_label_set"' in kwargs["prompt"]
            ):
                approve(port)
            yield event

    monkeypatch.setattr(harness.executor, "stream", approving)
    with pytest.raises(OrganizeWriteRefusalError, match="groom is not admitted"):
        await grooming(harness, operation).run(NOW)
    groomed = {key for key, carried in markers(port).items() if GROOM_MARKER in carried}
    assert len(groomed) == 1

    assert port.leases == {}

    events = [event async for event in drive(harness, job="approved-run")]
    assert errors(events) == []
    assert markers(port) == {
        key: frozenset({TICKET_MARKER, STAGED})
        | (frozenset({GROOM_MARKER}) if key in groomed else frozenset())
        for key in LANES
    }
    assert [event for event in events if isinstance(event, ScopeWalkEvent)]
    assert port.leases == {}


async def test_an_approved_scope_runs_its_stages_and_the_grooming_tick_takes_no_lease(
    monkeypatch,
):
    """After approval the pre-approval row has nobody to act on at all.

    It opens no session, writes nothing and takes no lease, so the only
    markers on the board are the two the run's stages wrote.
    """
    port = triaged(standing_board(LANES))
    operation = standing_operation()
    harness = staging_runtime(
        port, LANES, monkeypatch=monkeypatch, builds=[], operation=operation
    )
    approve(port)
    events = [event async for event in drive(harness, job="staged-run")]
    assert errors(events) == []
    assert markers(port) == dict.fromkeys(LANES, frozenset({TICKET_MARKER, STAGED}))
    spent = len(harness.executor.organize_calls)
    writes = len(port.classification_writes)

    assert await grooming(harness, operation).run(NOW) is PassRun.RAN
    assert len(harness.executor.organize_calls) == spent
    assert len(port.classification_writes) == writes
    assert markers(port) == dict.fromkeys(LANES, frozenset({TICKET_MARKER, STAGED}))
    assert port.leases == {}
