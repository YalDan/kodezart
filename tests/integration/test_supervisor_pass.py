"""The supervisor tick as the composition root registers and runs it."""

import asyncio
import subprocess
from pathlib import Path

import pytest
import structlog.testing

from kodezart.composition.supervisor import build_supervisor_pass
from kodezart.config.app import AppConfig
from kodezart.domain.errors import LaneRecordReadError
from kodezart.domain.run_alarm_record import MARKER_PURPOSE, run_alarm_marker
from kodezart.domain.tally_record import is_raised
from kodezart.services.supervisor_pass import (
    SUPERVISOR_TICK_NAME,
    supervisor_holder,
)
from kodezart.services.tally_supervisor import SIGNAL
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_alarm import LaneSubject
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import ScopeWalkEvent
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.chains.test_native_fire import native_operation
from tests.fakes import (
    FIXTURE_EPOCH,
    FakeAgentRunner,
    FakeDeliveryProbe,
    FakeTrackerPort,
)
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
    checks,
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


#: The one repository the dispatch wiring below binds, named once: the pass name
#: asserted and the roster the deployment is built with cannot then disagree.
REPO = example_config().repos[0].url

#: Each wiring: the roster handed in raw, the roster the dialled tracker's
#: reconciled copy carries (``None`` when no tracker is dialled at all), the two
#: facts the absent-arm log must state — or ``None`` where a tick registers —
#: and whether a delivery probe is dialled. Two of the cases tell the copies
#: apart: the gate and the log both read the reconciled one, so a roster
#: reconciliation added registers a tick and a roster it removed registers none.
#: The dispatch case is what puts a pass of another kind in the schedule BEFORE
#: the observation arm reaches it, which is the only way the clause about the
#: other passes is a claim about something the arm could have dropped.
WIRINGS = {
    "declared_with_tracker": ((SCOPE,), (SCOPE,), None, False),
    "declared_with_tracker_and_dispatch": ((SCOPE,), (SCOPE,), None, True),
    "declared_without_tracker": ((SCOPE,), None, (False, True), False),
    "undeclared": ((), (), (True, False), False),
    "reconciled_declares": ((), (SCOPE,), None, False),
    "only_raw_declares": ((SCOPE,), (), (True, False), False),
}


def bound_to_one_repository(operation):
    """*operation* narrowed to its first repository, and the teams bound to it.

    One repository is one dispatch pass, which is enough for the schedule to
    hold a registration the observation arm runs after and few enough to name.
    """
    return operation.model_copy(
        update={
            "repos": (operation.repos[0],),
            "teams": {
                key: entry
                for key, entry in operation.teams.items()
                if entry.repository == REPO
            },
        }
    )


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
    raw_scopes, reconciled_scopes, absent, dispatching = WIRINGS[wiring]

    async def boot(directory, *, raw, reconciled_roster):
        """This deployment with *raw* declared, and *reconciled_roster* dialled."""
        operation = declared(scopes=raw)
        if dispatching:
            operation = bound_to_one_repository(operation)
        return await _runtime(
            directory,
            tracker=None
            if reconciled_roster is None
            else FakeTrackerPort(issues=[], marker_prefixes=operation.marker_prefixes),
            runner=FakeAgentRunner(events=[]),
            operation=operation,
            reconciled=None
            if reconciled_roster is None
            else operation.model_copy(update={"supervisor_scopes": reconciled_roster}),
            github_api=FakeDeliveryProbe() if dispatching else None,
            supervisor_pass_interval_seconds=INTERVAL,
            supervisor_pass_timeout_seconds=TIMEOUT,
        )

    with structlog.testing.capture_logs() as logs:
        runtime = await boot(
            tmp_path, raw=raw_scopes, reconciled_roster=reconciled_scopes
        )

    registered = list(runtime.scheduler.passes)
    # The name is written out here rather than read off the production constant:
    # the claim is that the tick answers to this name, and a selection that
    # imported the name would follow it wherever it was moved to.
    ticks = [entry for entry in registered if entry.name == "supervisor"]
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

    # Every other pass is as it was: the arm adds one registration and edits no
    # other, so the rest of the schedule is the same set either way. Named, so
    # what the arm is being compared against is readable; a dispatch pass is
    # among them wherever one was dialled, and that one is registered before the
    # arm runs.
    expected = {PromptKey.FIRE_PREP_PASS.value, PromptKey.GROOMING_PASS.value}
    if dispatching:
        expected |= {f"dispatch:{REPO}"}
    assert {entry.name for entry in registered} - {"supervisor"} == expected

    # "As before" is the same deployment declaring no roster at all, so the
    # comparison is against the schedule this boot would have had rather than
    # against a set written out above: whatever the fixture wires, the arm added
    # its own registration and removed none.
    with structlog.testing.capture_logs():
        as_before = await boot(
            tmp_path / "as-before",
            raw=(),
            reconciled_roster=None if reconciled_scopes is None else (),
        )

    assert {entry.name for entry in registered} - {"supervisor"} == {
        entry.name for entry in as_before.scheduler.passes
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

    The other half of the claim — zero dispatches and zero version-control calls
    — is pinned by this process seam together with the static scan of what the
    tick's modules can reach at all. The acceptance test's per-double call counts
    are a different and weaker claim: those doubles are wired to the walk, not to
    the observation, which is built with the tracker alone, so what the counts
    say is that the tick does not set the walk's collaborators going through the
    tracker state the two share.
    """
    operation = declared(scopes=(SCOPE,))
    port = await board(
        lanes=LANES,
        scope=SCOPE,
        holder=supervisor_holder(operation_name=operation.operation_name),
    )

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


#: A second declared scope beside :data:`SCOPE`, and the one stalled lane each
#: of the two holds. One board carries both, so the only thing telling the two
#: lanes apart is which scope declares it.
SECOND_SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="second-scoped-project")
PAIRED_LANES = {SCOPE: "LANE-D", SECOND_SCOPE: "LANE-E"}
#: Written out rather than left to the board's default, so the crossing of the
#: bound is a comparison the test makes and not one it assumes.
STALL_COMMITS = ("sha-one", "sha-two")


async def test_a_tick_observes_a_stalled_lane_under_every_declared_scope() -> None:
    """KOD-103: the factory hands the tick the whole roster, not its head.

    Both lanes are stalled the same way — more recorded commits than the bound
    the deployment is built with, and every criterion still open — so a lane
    left unobserved is a scope the roster never reached rather than a lane that
    read as healthy. A tick over the first scope alone therefore leaves the
    second scope's lane with no record and no event.
    """
    operation = declared(scopes=tuple(PAIRED_LANES))
    port = await board(
        lanes=tuple(PAIRED_LANES.values()),
        commits=STALL_COMMITS,
        scopes={ref: (lane,) for ref, lane in PAIRED_LANES.items()},
        holder=supervisor_holder(operation_name=operation.operation_name),
    )

    # The stall, from the board and the configured bound: a lane whose recorded
    # commits do not pass the bound, or whose roster is already closed, would
    # satisfy what follows by never being measured.
    assert len(STALL_COMMITS) > BOUND
    for lane in PAIRED_LANES.values():
        for key in checks(lane):
            assert port.issues[key].state_kind is not WorkflowStateKind.COMPLETED

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
        assert await scheduled.run(FIXTURE_EPOCH) is PassRun.RAN

    for ref, lane in PAIRED_LANES.items():
        stored = await port.read_run_alarm(
            issue_key=lane,
            subject=LaneSubject(scope_key=ref.key, lane_key=lane),
            signal=SIGNAL,
        )
        assert stored is not None, ref.key
        assert is_raised(stored), ref.key
        raised = [
            event
            for event in await port.lane_run_events(issue_key=lane, lane_key=lane)
            if event.kind is RunEventKind.RUN_ALARM_RAISED
        ]
        assert len(raised) == 1, ref.key


#: A dispatch holder no default would produce, so a lease holder composed from
#: it would be visible wherever it appeared.
FOREIGN_PROCESS = "separate-deployment"


async def test_the_composed_tick_leases_under_its_pass_identity_not_dispatch_holder():
    """KOD-388: the pass's lease holder is its own, never the claim identity.

    The deployment is built with a dispatch holder nothing else on this board
    uses, so a holder derived from it would show up in every lease the tick
    takes and in the alarm record it writes. What is there instead is the pass
    identity, on both, and the lease is released when the tick is done.
    """
    operation = declared(scopes=(SCOPE,))
    expected = supervisor_holder(operation_name=operation.operation_name)
    # Stated rather than derived: every assertion below compares to *expected*,
    # so an identity composed from something else entirely would move both
    # sides together and read as agreement.
    assert expected == f"{operation.operation_name}/{SUPERVISOR_TICK_NAME}"
    assert FOREIGN_PROCESS not in expected
    port = await board(lanes=LANES, scope=SCOPE, holder=expected)

    scheduled = build_supervisor_pass(
        config=AppConfig(
            _env_file=None,
            dispatch_holder=FOREIGN_PROCESS,
            run_alarm_max_commits_without_closure=BOUND,
            supervisor_pass_interval_seconds=INTERVAL,
            supervisor_pass_timeout_seconds=TIMEOUT,
        ),
        operation=operation,
        tracker=port,
    )
    async with asyncio.timeout(TICK_BOUND_SECONDS):
        assert await scheduled.run(FIXTURE_EPOCH) is PassRun.RAN

    assert port.lease_writes
    assert {lease.holder for lease in port.lease_writes} == {expected}
    assert [body for _, body in port.comment_writes if FOREIGN_PROCESS in body] == []
    for lane in LANES:
        stored = await port.read_run_alarm(
            issue_key=lane, subject=subject(lane), signal=SIGNAL
        )
        assert stored is not None
        assert stored.raised_by == expected
    assert port.leases == {}


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
#: The configured bound, named once: it is the value the deployment is built
#: with, the value the precondition compares the recorded commits to, and the
#: value the stored record must carry. Three literals could disagree, and a
#: bound above the recorded commits would satisfy the rest of the test by never
#: being crossed.
STALL_BOUND = 1
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
            run_alarm_max_commits_without_closure=STALL_BOUND,
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


async def streams_of(port):
    """Every lane's whole stream, of whatever kind, as one comparable value.

    Read over every kind rather than the raises alone: a tick that posted a
    clear for a lane nobody had raised leaves the raises exactly as they were,
    so a filtered read reports a healthy walk either way. The walk's own
    events are on these streams too, which is why the claim is that the tick
    added nothing to them and not that they are empty.
    """
    return {
        lane: list(await port.lane_run_events(issue_key=lane, lane_key=lane))
        for lane in WALK_LANES
    }


def alarm_address(lane):
    """The marker the tick's own record for *lane* is written under."""
    return run_alarm_marker(
        subject=LaneSubject(scope_key=WALK_SCOPE.key, lane_key=lane),
        signal=SIGNAL,
        marker_prefixes=walk_operation().marker_prefixes,
    )


def written_since(port, mark):
    """Each comment written after *mark*, as the address it landed at.

    A write is read as issue key and marker together: a body under the record's
    own marker landing on another issue is a write this tick did not owe, and a
    per-lane read of the two lane issues sees neither the issue nor the marker.
    """
    rows = {row.comment_key: row for row in port.comments}
    return [
        (rows[key].issue_key, body.split("\n", 1)[0])
        for key, body in port.comment_writes[mark:]
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
    """Call counts of every double outside the tracker, as one comparable value.

    Each double's own log, not a proxy: a read-only version-control call and a
    consolidate of an already-integrated branch both move no branch head, so
    branch heads alone would miss them. The heads stay too, because they are the
    fact a delivery would read.
    """
    return (
        len(harness.executor.schema_calls),
        len(harness.git.calls),
        len(harness.workspace.calls),
        len(harness.persister.calls),
        len(harness.merger.calls),
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
    async with asyncio.timeout(WALK_BOUND_SECONDS):
        async for event in stream:
            if isinstance(event, ScopeWalkEvent):
                observed += 1
                if observed == 2:
                    break
        await stream.aclose()
    assert observed == 2

    before = board_state(port)
    doubles = doubles_of(first, repos)
    streams = await streams_of(port)
    quiet_writes = len(port.comment_writes)
    async with asyncio.timeout(TICK_BOUND_SECONDS):
        assert await scheduled.run(FIXTURE_EPOCH) is PassRun.RAN

    assert await observed_alarms(port) == []
    assert await raised_events(port, "A") == []
    assert await raised_events(port, "S") == []
    # Every kind, not the raises alone: the tick added nothing at all to either
    # lane's stream, so a clear posted for a lane nobody had raised reddens here.
    assert await streams_of(port) == streams
    # The whole tick's writes, not only the two lane issues': the one write a
    # healthy walk earns is the quiet reading on A, and a write coupled to it
    # that landed on a criterion issue is read by nothing else above.
    assert written_since(port, quiet_writes) == [("A", alarm_address("A"))]
    assert board_state(port) == before
    assert doubles_of(first, repos) == doubles
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
    assert len(stalled.commits) > STALL_BOUND
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
    assert stored.bound.configured_value == STALL_BOUND
    assert stored.bound.observed_value == len(stalled.commits)
    assert await observed_alarms(port) == ["S"]
    assert len(await raised_events(port, "S")) == 1
    assert await raised_events(port, "A") == []
    assert len([row for row in alarm_records(port) if row.issue_key == "S"]) == 1
    # Every alarm address on the whole board, not only the two lane addresses:
    # a record written somewhere else would be read by nothing above.
    assert sorted(row.issue_key for row in alarm_records(port)) == ["A", "S"]

    # Nothing moved, across the raising tick and the two replays.
    assert board_state(port) == before
    assert doubles_of(second, repos) == doubles

    # The scoped arm still holds no checkpointer, on either origin shape.
    for url in (ORIGIN, FORGE_ORIGIN):
        lane = second.engine._scoped_arm._lane_for(url)
        assert lane.graph.checkpointer is None
        assert lane.fire.native_graph.checkpointer is None
        assert lane.fire.checkpointer is None
