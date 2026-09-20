"""The supervisor tick as the composition root registers and runs it."""

import asyncio
import subprocess
from pathlib import Path

import pytest
import structlog.testing

from kodezart.composition.supervisor import build_supervisor_pass
from kodezart.config.app import AppConfig
from kodezart.domain.errors import LaneRecordReadError
from kodezart.domain.run_alarm_record import MARKER_PURPOSE
from kodezart.domain.tally_record import is_raised
from kodezart.services.supervisor_pass import SUPERVISOR_TICK_NAME
from kodezart.services.tally_supervisor import SIGNAL
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_alarm import LaneSubject
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import ScopeWalkEvent
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.chains.test_native_fire import native_operation
from tests.fakes import FIXTURE_EPOCH, FakeAgentRunner, FakeTrackerPort
from tests.integration.test_scope_runtime import (
    FORGE_ORIGIN,
    ORIGIN,
    WALK_BOUND_SECONDS,
    WalkRepos,
    drive,
    resumable,
)
from tests.integration.test_scope_runtime import SCOPE as WALK_SCOPE
from tests.integration.test_scope_runtime import board as walk_fixture
from tests.integration.test_scope_runtime import lane_record as walk_record
from tests.lane_fixture import criteria_echo
from tests.prompts.test_operation_config import EXAMPLE
from tests.services.lane_tally_fixtures import (
    BOUND,
    PREFIXES,
    board,
    declared_set_fixture,
    subject,
)
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

every_write_of_a_tick_is_inside_the_declared_set = declared_set_fixture()


def declared(*, scopes):
    return example_config().model_copy(
        update={
            "supervisor_scopes": scopes,
            "marker_prefixes": {**example_config().marker_prefixes, **PREFIXES},
        }
    )


#: Each wiring: the roster handed in raw, the roster the dialled tracker's
#: reconciled copy carries (``None`` when no tracker is dialled at all), and the
#: two facts the absent-arm log must state — or ``None`` where a tick registers.
#: The last two cases are the ones that tell the copies apart: the gate and the
#: log both read the reconciled one, so a roster reconciliation added registers
#: a tick and a roster it removed registers none.
WIRINGS = {
    "declared_with_tracker": ((SCOPE,), (SCOPE,), None),
    "declared_without_tracker": ((SCOPE,), None, (False, True)),
    "undeclared": ((), (), (True, False)),
    "reconciled_declares": ((), (SCOPE,), None),
    "only_raw_declares": ((SCOPE,), (), (True, False)),
}


@pytest.mark.parametrize("wiring", list(WIRINGS))
async def test_the_pass_registers_only_with_declared_scopes_and_a_dialled_tracker(
    tmp_path: Path, wiring: str
) -> None:
    """The roster and the dialled tracker are the whole gate, and both are named.

    A deployment that declares scopes and dials a tracker registers exactly one
    tick; either one absent registers none and says which was missing in the
    boot log, so an operator reads the reason rather than deducing it from a
    schedule with no supervisor in it.

    The roster read is the reconciled copy's, which is the only copy anything
    downstream may read. Two of the cases below make the two copies disagree,
    so the gate and the absent-arm log are each shown to read that one and not
    the copy handed in raw.
    """
    raw_scopes, reconciled_scopes, absent = WIRINGS[wiring]
    operation = declared(scopes=raw_scopes)
    reconciled = (
        None if reconciled_scopes is None else declared(scopes=reconciled_scopes)
    )
    tracker = (
        None
        if reconciled_scopes is None
        else FakeTrackerPort(issues=[], marker_prefixes=operation.marker_prefixes)
    )

    with structlog.testing.capture_logs() as logs:
        runtime = await _runtime(
            tmp_path,
            tracker=tracker,
            runner=FakeAgentRunner(events=[]),
            operation=operation,
            reconciled=reconciled,
            supervisor_pass_interval_seconds=INTERVAL,
            supervisor_pass_timeout_seconds=TIMEOUT,
        )

    registered = list(runtime.scheduler.passes)
    ticks = [entry for entry in registered if entry.name == SUPERVISOR_TICK_NAME]
    unwired = [entry for entry in logs if entry["event"] == "supervisor_pass_not_wired"]
    if absent is None:
        assert len(ticks) == 1
        assert ticks[0].interval_seconds == INTERVAL
        assert ticks[0].timeout_seconds == TIMEOUT
        assert ticks[0].report is None
        assert unwired == []
    else:
        tracker_present, scopes_declared = absent
        assert ticks == []
        assert len(unwired) == 1
        assert unwired[0]["tracker_present"] is tracker_present
        assert unwired[0]["scopes_declared"] is scopes_declared

    # Every other pass is as it was: the arm adds one registration and edits
    # no other, so the rest of the schedule is the same set either way. Asserted
    # as that set rather than as "not empty", because an arm that cleared the
    # schedule before appending its own would leave a non-empty list of one.
    assert {entry.name for entry in registered} - {SUPERVISOR_TICK_NAME} == {
        PromptKey.FIRE_PREP_PASS.value,
        PromptKey.GROOMING_PASS.value,
    }


def test_the_example_operation_declares_the_roster_the_tick_reads() -> None:
    """The shipped example names the member, so an operator has one to edit."""
    assert "supervisor_scopes" in EXAMPLE.read_text(encoding="utf-8")


def refuse_every_process(monkeypatch):
    """Make any process the tick starts, by any route, fail the test.

    Patched on the modules rather than asserted over a double, because a double
    the factory never receives records nothing however the tick behaves. This
    catches a process reached through a collaborator the factory was never
    given, typed or not, and ``subprocess.run`` and ``check_output`` both reach
    ``Popen`` by module-global lookup, so the three names below are every route
    out of this process.
    """

    def reached(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the supervisor tick reached a process")

    for module, name in (
        (asyncio, "create_subprocess_exec"),
        (asyncio, "create_subprocess_shell"),
        (subprocess, "Popen"),
    ):
        monkeypatch.setattr(module, name, reached)


async def test_a_whole_tick_dispatches_no_agent_and_touches_no_repository(monkeypatch):
    """The real factory over a real board: nothing outside the tracker is asked.

    "No repository" is observed rather than argued: every route out of this
    process is made to fail for the duration of the tick, so a repository or a
    session reached through any collaborator reddens even though the factory
    takes none. Doubles handed to nothing would have recorded nothing whatever
    the tick did.

    The other half of the claim — that the runner records zero dispatches and
    the version-control service zero calls — is pinned over doubles that ARE
    wired, by the acceptance test's per-double call counts around each tick.
    """
    port = await board(lanes=LANES, scope=SCOPE)
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

    refuse_every_process(monkeypatch)
    async with asyncio.timeout(TICK_BOUND_SECONDS):
        outcome = await scheduled.run(FIXTURE_EPOCH)

    assert outcome is PassRun.RAN
    # The tick did observe: a board whose lanes are all past the bound raises
    # on each, so "no agent and no repository" is not "nothing happened".
    for lane in LANES:
        stored = await port.read_run_alarm(
            issue_key=lane, subject=subject(lane), signal=SIGNAL
        )
        assert stored is not None
        assert is_raised(stored)


# ---------------------------------------------------------------------------
# KOD-832 clause 9 — no alarm on the healthy walk, one keyed alarm on a
# planted stalled tally, and no state moved either way.
# ---------------------------------------------------------------------------

#: The alarm marker prefix this deployment configures, which nothing else on
#: the walk's board writes under.
ALARM_PREFIX = "run-alarm"
WALK_LANES = ("A", "S")
WALK_CHECKS = {"A": ("check", "second"), "S": ("check",)}
WALK_KEYS = ("A/check", "A/second", "S/check")
#: Lane A's roster passes; lane S's criterion never does, which is the stall.
PASSING = ("A/check", "A/second")
#: The healthy phase closes part of lane A's roster and leaves the rest owed,
#: which is what a lane in the middle of its work looks like.
PARTLY_PASSING = ("A/check",)


class KeyedEchoes(list):
    """Grades each roster from the criterion keys the prompt just recorded names.

    A flat list of echoes would have to be written in the order the walk
    happens to dispatch its lanes in, and a roster answered in another lane's
    keys is refused by the fan-in contract rather than read as a stall. The
    executor records the prompt before it asks for the next echo, so the echo
    can be the answer to that prompt.
    """

    def __init__(self, executor, *, passing=PASSING):
        super().__init__()
        self._executor = executor
        self._passing = frozenset(passing)

    def __bool__(self):
        return True

    def pop(self, index=-1):
        prompt = self._executor.evaluation_prompts[-1]
        return criteria_echo(
            keys=[key for key in WALK_KEYS if key in prompt], passed=self._passing
        )


def walk_board():
    port = walk_fixture(lanes=WALK_LANES, checks=WALK_CHECKS)
    # The deployment that observes this board configures the alarm prefix; the
    # walk itself writes under none of it.
    port.marker_prefixes[MARKER_PURPOSE] = ALARM_PREFIX
    return port


def walk_operation():
    declared = native_operation()
    return declared.model_copy(
        update={
            "marker_prefixes": {
                **declared.marker_prefixes,
                MARKER_PURPOSE: ALARM_PREFIX,
            },
            "supervisor_scopes": (WALK_SCOPE,),
        }
    )


def walk_supervisor(port):
    return build_supervisor_pass(
        config=AppConfig(
            _env_file=None,
            max_iterations=2,
            run_alarm_max_commits_without_closure=1,
            supervisor_pass_interval_seconds=INTERVAL,
            supervisor_pass_timeout_seconds=TIMEOUT,
        ),
        operation=walk_operation(),
        tracker=port,
    )


def alarm_records(port):
    """Every comment on the whole board written at an alarm address."""
    return [row for row in port.comments if row.body.startswith(f"[{ALARM_PREFIX}:")]


async def raised_events(port, lane):
    return [
        event
        for event in await port.lane_run_events(issue_key=lane, lane_key=lane)
        if event.kind is RunEventKind.RUN_ALARM_RAISED
    ]


def board_state(port):
    """Everything a tick must leave exactly as it found it."""
    return (
        list(port.workflow_writes),
        list(port.restored_states),
        list(port.issue_writes),
        list(port.queue_writes),
        list(port.classification_writes),
        list(port.claim_writes),
        dict(port.issue_state_changes),
        {key: (row.state_name, row.state_kind) for key, row in port.issues.items()},
    )


def doubles_of(harness, repos):
    """Call counts of every double outside the tracker, as one comparable value."""
    return (
        len(harness.executor.schema_calls),
        len(harness.service._workspace.calls),
        len(harness.service._persister.calls),
        len(harness.workspace.calls),
        {branch: repo.head for branch, repo in repos.branches.items()},
    )


async def observed_alarms(port):
    """Every alarm address on the board whose readings replay to an alarm."""
    raised = []
    for lane in WALK_LANES:
        stored = await port.read_run_alarm(
            issue_key=lane,
            subject=LaneSubject(scope_key=WALK_SCOPE.key, lane_key=lane),
            signal=SIGNAL,
        )
        if stored is not None and is_raised(stored):
            raised.append(lane)
    return raised


async def test_no_alarm_on_a_healthy_walk_and_one_keyed_alarm_on_a_stalled_lane():
    """Clause 9, in process, over the walk's own board and the real factory.

    Phase one is a healthy walk: lane A closed a criterion and still owes one,
    lane S has not been fired. Phase two plants the stall by running the walk
    to completion — S's fire records its commits, closes nothing and is rested
    — which is a state the tracker alone carries, so a later run plants it the
    same way.
    """
    repos = WalkRepos()
    port = walk_board()
    scheduled = walk_supervisor(port)

    # --- Phase one: one fire, on a lane that closed what it owed.
    first = resumable(port=port, repos=repos, lanes=WALK_LANES, max_iterations=2)
    first.executor.evaluations = KeyedEchoes(first.executor, passing=PARTLY_PASSING)
    stream = drive(first, job="healthy-job")
    observed = 0
    async for event in stream:
        if isinstance(event, ScopeWalkEvent):
            observed += 1
            if observed == 2:
                break
    await stream.aclose()
    assert observed == 2

    before = board_state(port)
    async with asyncio.timeout(TICK_BOUND_SECONDS):
        assert await scheduled.run(FIXTURE_EPOCH) is PassRun.RAN

    assert await observed_alarms(port) == []
    assert await raised_events(port, "A") == []
    assert await raised_events(port, "S") == []
    assert board_state(port) == before
    # Not vacuous: the tick did observe lane A, and what it left there is a
    # tally reading rather than an alarm. Lane S was never fired, so there is
    # no record of it at all and no clock to measure it by.
    assert [row.issue_key for row in alarm_records(port)] == ["A"]
    with pytest.raises(LaneRecordReadError):
        await walk_record(port, "S")

    # --- Phase two: the same board, walked to completion. S closes nothing.
    second = resumable(port=port, repos=repos, lanes=WALK_LANES, max_iterations=2)
    second.executor.evaluations = KeyedEchoes(second.executor)
    async with asyncio.timeout(WALK_BOUND_SECONDS):
        [event async for event in drive(second, job="stalled-job")]

    stalled = await walk_record(port, "S")
    # The precondition, from the board: a lane whose recorded commits do not
    # pass the bound could satisfy what follows by never being measured.
    assert len(stalled.commits) > 1
    assert port.issues["S/check"].state_kind is not WorkflowStateKind.COMPLETED

    before = board_state(port)
    doubles = doubles_of(second, repos)
    async with asyncio.timeout(TICK_BOUND_SECONDS):
        assert await scheduled.run(FIXTURE_EPOCH) is PassRun.RAN
    quiet = (list(port.comments), list(port.comment_writes), list(port.lease_writes))
    for _ in range(2):
        async with asyncio.timeout(TICK_BOUND_SECONDS):
            assert await scheduled.run(FIXTURE_EPOCH) is PassRun.RAN
        assert (
            list(port.comments),
            list(port.comment_writes),
            list(port.lease_writes),
        ) == quiet

    stored = await port.read_run_alarm(
        issue_key="S",
        subject=LaneSubject(scope_key=WALK_SCOPE.key, lane_key="S"),
        signal=SIGNAL,
    )
    assert stored is not None
    assert is_raised(stored)
    assert stored.bound is not None
    assert stored.bound.config_field == "run_alarm_max_commits_without_closure"
    assert stored.bound.configured_value == 1
    assert stored.bound.observed_value == len(stalled.commits)
    assert await observed_alarms(port) == ["S"]
    assert len(await raised_events(port, "S")) == 1
    assert await raised_events(port, "A") == []
    assert len([row for row in alarm_records(port) if row.issue_key == "S"]) == 1

    # Nothing moved, across the raising tick and the two replays.
    assert board_state(port) == before
    assert doubles_of(second, repos) == doubles

    # The scoped arm still holds no checkpointer, on either origin shape.
    for url in (ORIGIN, FORGE_ORIGIN):
        lane = second.engine._scoped_arm._lane_for(url)
        assert lane.graph.checkpointer is None
        assert lane.fire.native_graph.checkpointer is None
        assert lane.fire.checkpointer is None
