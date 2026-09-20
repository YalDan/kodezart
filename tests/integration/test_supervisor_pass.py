"""The supervisor tick as the composition root registers and runs it."""

import asyncio
from pathlib import Path

import pytest
import structlog.testing

from kodezart.composition.supervisor import build_supervisor_pass
from kodezart.config.app import AppConfig
from kodezart.domain.tally_record import is_raised
from kodezart.services.supervisor_pass import SUPERVISOR_TICK_NAME
from kodezart.services.tally_supervisor import SIGNAL
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from tests.fakes import (
    FIXTURE_EPOCH,
    FakeAgentRunner,
    FakeGitService,
    FakeTrackerPort,
    FakeWorkspaceProvider,
)
from tests.prompts.test_operation_config import EXAMPLE
from tests.services.lane_tally_fixtures import BOUND, PREFIXES, board, subject
from tests.services.test_prompt_pass import example_config
from tests.services.test_prompt_passes import _runtime

#: Bounded because an integration tick that hangs is a failure, not a wait.
TICK_BOUND_SECONDS = 60
LANES = ("LANE-B", "LANE-C")

#: Cadences no default would produce, so what is observed is the knob's
#: consumer and not a coincidence.
INTERVAL = 611.0
TIMEOUT = 97.0
SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="scoped-project")


def declared(*, scopes):
    return example_config().model_copy(
        update={
            "supervisor_scopes": scopes,
            "marker_prefixes": {**example_config().marker_prefixes, **PREFIXES},
        }
    )


@pytest.mark.parametrize(
    "wiring", ["declared_with_tracker", "declared_without_tracker", "undeclared"]
)
async def test_the_pass_registers_only_with_declared_scopes_and_a_dialled_tracker(
    tmp_path: Path, wiring: str
) -> None:
    """The roster and the dialled tracker are the whole gate, and both are named.

    A deployment that declares scopes and dials a tracker registers exactly one
    tick; either one absent registers none and says which was missing in the
    boot log, so an operator reads the reason rather than deducing it from a
    schedule with no supervisor in it.
    """
    operation = declared(scopes=() if wiring == "undeclared" else (SCOPE,))
    tracker = (
        None
        if wiring == "declared_without_tracker"
        else FakeTrackerPort(issues=[], marker_prefixes=operation.marker_prefixes)
    )

    with structlog.testing.capture_logs() as logs:
        runtime = await _runtime(
            tmp_path,
            tracker=tracker,
            runner=FakeAgentRunner(events=[]),
            operation=operation,
            supervisor_pass_interval_seconds=INTERVAL,
            supervisor_pass_timeout_seconds=TIMEOUT,
        )

    registered = list(runtime.scheduler.passes)
    ticks = [entry for entry in registered if entry.name == SUPERVISOR_TICK_NAME]
    unwired = [entry for entry in logs if entry["event"] == "supervisor_pass_not_wired"]
    if wiring == "declared_with_tracker":
        assert len(ticks) == 1
        assert ticks[0].interval_seconds == INTERVAL
        assert ticks[0].timeout_seconds == TIMEOUT
        assert ticks[0].report is None
        assert unwired == []
    else:
        assert ticks == []
        assert len(unwired) == 1
        assert unwired[0]["tracker_present"] is (tracker is not None)
        assert unwired[0]["scopes_declared"] is (wiring == "declared_without_tracker")

    # Every other pass is as it was: the arm adds one registration and edits
    # no other, so the rest of the schedule is the same set either way.
    assert [entry.name for entry in registered if entry.name != SUPERVISOR_TICK_NAME]


def test_the_example_operation_declares_the_roster_the_tick_reads() -> None:
    """The shipped example names the member, so an operator has one to edit."""
    assert "supervisor_scopes" in EXAMPLE.read_text(encoding="utf-8")


async def test_a_whole_tick_dispatches_no_agent_and_touches_no_repository():
    """The real factory over a real board: nothing outside the tracker is asked.

    The doubles are handed to nothing, because the factory takes none of them.
    They are here so the claim is the observed one — zero dispatches, zero
    version-control calls, zero prepared trees across the whole tick — rather
    than an argument from the factory's signature, which is asserted elsewhere.
    """
    port = await board(lanes=LANES, scope=SCOPE)
    runner = FakeAgentRunner(events=[])
    git = FakeGitService()
    workspace = FakeWorkspaceProvider(git=git)
    operation = declared(scopes=(SCOPE,))

    scheduled = build_supervisor_pass(
        config=AppConfig(
            _env_file=None,
            run_alarm_max_commits_without_closure=BOUND,
            supervisor_pass_interval_seconds=INTERVAL,
            supervisor_pass_timeout_seconds=TIMEOUT,
        ),
        operation=operation,
        tracker=port,
    )

    async with asyncio.timeout(TICK_BOUND_SECONDS):
        outcome = await scheduled.run(FIXTURE_EPOCH)

    assert outcome is PassRun.RAN
    assert runner.calls == []
    assert git.calls == []
    assert workspace.calls == []
    assert workspace.acquisitions == []
    # The tick did observe: a board whose lanes are all past the bound raises
    # on each, so "no agent and no repository" is not "nothing happened".
    for lane in LANES:
        stored = await port.read_run_alarm(
            issue_key=lane, subject=subject(lane), signal=SIGNAL
        )
        assert stored is not None
        assert is_raised(stored)
