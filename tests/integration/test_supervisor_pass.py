"""The supervisor tick as the composition root registers and runs it."""

import asyncio
import subprocess
from pathlib import Path

import pytest
import structlog.testing

from kodezart.composition.supervisor import build_supervisor_pass
from kodezart.config.app import AppConfig
from kodezart.core.errors import PassGateCapabilityError
from kodezart.domain.errors import LaneRecordReadError
from kodezart.domain.lane_alarms import stored_alarm
from kodezart.domain.run_alarm_record import MARKER_PURPOSE, run_alarm_marker
from kodezart.domain.run_alarm_table import alarm_raised
from kodezart.domain.run_shape import GROOM_MARKER_SOURCE, TICKET_MARKER_SOURCE
from kodezart.services.supervisor_pass import (
    SUPERVISOR_TICK_NAME,
    supervisor_holder,
)
from kodezart.types.domain.dispatch import PassRun, PassSignal
from kodezart.types.domain.operation import (
    OperationMemberAbsentError,
    OrganizeScopeBinding,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_alarm import LaneSubject, ScopeSubject
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
from tests.prompts.test_minimal_floor import minimal_fixture
from tests.prompts.test_operation_config import EXAMPLE
from tests.prompts.test_organize_mandate_bindings import declared_operation
from tests.services.lane_tally_fixtures import (
    BOUND,
    PREFIXES,
    SIGNAL,
    board,
    checks,
    declared_set_fixture,
    subject,
)
from tests.services.test_prompt_pass import example_config
from tests.services.test_prompt_passes import (
    DIAGNOSIS,
    HEARTBEAT_PASS,
    STANDING_SCOPE_SETTINGS,
    _runtime,
)

#: Bounded because an integration tick that hangs is a failure, not a wait.
TICK_BOUND_SECONDS = 60
LANES = ("LANE-B", "LANE-C")

#: Cadences no default would produce, so what is observed is the knob's
#: consumer and not a coincidence.
INTERVAL = 611.0
TIMEOUT = 97.0
SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="scoped-project")

every_write_of_a_tick_is_inside_the_declared_set = declared_set_fixture()


#: The one repository the dispatch wiring below binds, named once: the pass name
#: asserted and the roster the deployment is built with cannot then disagree.
REPO = example_config().repos[0].url


def roster(*scopes, repo_url=REPO):
    """The one scope table over *scopes*, as a model update.

    One helper because every fixture here declares the same table: the tick
    reads each row's scope, and the rest of the row is what the passes beside
    it read, so a fixture cannot declare a roster for one pass only.
    """
    return {
        "organize_scopes": tuple(
            OrganizeScopeBinding(scope=scope, repo_url=repo_url) for scope in scopes
        )
    }


def declared(*, scopes):
    """This deployment declaring *scopes*, with the alarm prefixes in place.

    Built on the operation that carries the mandate table, because declaring a
    scope requires the organize owner: the rows the tick observes are the rows
    the organize stages groom, and one without the other is a partial
    configuration refused before anything is scheduled.
    """
    base = declared_operation()
    return base.model_copy(
        update={
            **roster(*scopes),
            "marker_prefixes": {**base.marker_prefixes, **PREFIXES},
        }
    )


#: Each wiring: the roster handed in raw, the roster the dialled tracker's
#: reconciled copy carries (``None`` when no tracker is dialled at all), what
#: the absent arm must state — ``None`` where a tick registers, the two log
#: facts where the boot completes, or the name of the member a refusal carries —
#: and whether a delivery probe is dialled. Two of the cases tell the copies
#: apart, and the fact they now pin is that every arm reads the copy handed in:
#: a roster only the reconciled copy carries registers nothing, and a roster only
#: the raw copy carries registers the tick. That is the one copy the organize
#: tick and the heartbeat are built from, so a tick over the other copy would be
#: this deployment observing rows nothing else here works.
#: A declared roster without a tracker is now a partial organize configuration
#: rather than a quiet absence, so that case refuses at preflight and names the
#: member; it never reaches the arm at all.
#: The dispatch case keeps its id and its probe, and what it demonstrates has
#: changed: a declared roster withholds the dispatch pass, so what stands in the
#: schedule before the observation arm is the organize tick and the heartbeat
#: rather than a board scan.
WIRINGS = {
    "declared_with_tracker": ((SCOPE,), (SCOPE,), None, False),
    "declared_with_tracker_and_dispatch": ((SCOPE,), (SCOPE,), None, True),
    "declared_without_tracker": ((SCOPE,), None, "tracker", False),
    "undeclared": ((), (), (True, False), False),
    "reconciled_declares": ((), (SCOPE,), (True, False), False),
    "only_raw_declares": ((SCOPE,), (), None, False),
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
    tick; no tracker is a partial organize configuration and refuses at
    preflight naming the member, and no roster registers none and says so in the
    boot log, so an operator reads the reason rather than deducing it from a
    schedule with no supervisor in it.

    The roster read is the copy handed in, which is the copy the organize tick
    and the heartbeat are built from. Two of the cases below make the two copies
    disagree, so the gate and the absent-arm log are each shown to read that one
    and not the dialled tracker's reconciled copy: a tick over rows the rest of
    this deployment never works is one factory holding two opinions.
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
            else operation.model_copy(update=roster(*reconciled_roster)),
            github_api=FakeDeliveryProbe() if dispatching else None,
            # A declared roster is a whole organize configuration: the owner's
            # two bounds and the verification budget belong beside the rows, and
            # a deployment declaring no row configures no owner — a configured
            # owner with no row refuses by name.
            **(STANDING_SCOPE_SETTINGS if raw else {}),
            supervisor_pass_interval_seconds=INTERVAL,
            supervisor_pass_timeout_seconds=TIMEOUT,
        )

    if absent == "tracker":
        # The roster is declared and nothing is dialled, so this deployment
        # never reaches the arm: preflight refuses the partial organize
        # configuration and names the member that is missing.
        with pytest.raises(OperationMemberAbsentError) as refused:
            await boot(tmp_path, raw=raw_scopes, reconciled_roster=reconciled_scopes)
        assert refused.value.missing == "tracker"
        return

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
    # other. The two sets are named, because a declared roster and an undeclared
    # one no longer schedule the same passes: the roster withholds the per-issue
    # machine and puts the organize tick and the heartbeat there instead, and
    # both of those are registered before the observation arm runs.
    per_issue = {PromptKey.FIRE_PREP_PASS.value, PromptKey.GROOMING_PASS.value}
    if dispatching:
        per_issue |= {f"dispatch:{REPO}"}
    scope_passes = {PromptKey.GROOMING_PASS.value, HEARTBEAT_PASS}
    expected = scope_passes if raw_scopes else per_issue
    assert {entry.name for entry in registered} - {"supervisor"} == expected

    # "As before" is the same deployment declaring no roster at all, held to the
    # other of the two sets above: the arm added its own registration and removed
    # none, and what a roster does to the rest of the schedule is stated here
    # rather than inferred from the boot being compared against itself.
    with structlog.testing.capture_logs():
        as_before = await boot(
            tmp_path / "as-before",
            raw=(),
            reconciled_roster=None if reconciled_scopes is None else (),
        )

    assert {entry.name for entry in as_before.scheduler.passes} == per_issue


#: The per-issue gates with issue activity left out, so any probe of
#: ``issues_changed`` a boot makes is the supervisor's and nobody else's.
GATES_WITHOUT_ISSUE_ACTIVITY: dict[str, object] = {
    "fire_prep_pass_gate_signals": [PassSignal.triage_backlog],
    "grooming_pass_gate_signals": [],
    "dispatch_pass_gate_signals": [PassSignal.approved_changed],
}


async def _boot_gated(directory, *, raw, reconciled_roster, dispatching, tracker):
    """This deployment over *tracker*, with no per-issue gate on issue activity."""
    operation = declared(scopes=raw)
    if dispatching:
        operation = bound_to_one_repository(operation)
    return await _runtime(
        directory,
        tracker=tracker,
        runner=FakeAgentRunner(events=[]),
        operation=operation,
        reconciled=None
        if reconciled_roster is None
        else operation.model_copy(update=roster(*reconciled_roster)),
        github_api=FakeDeliveryProbe() if dispatching else None,
        **{
            **(STANDING_SCOPE_SETTINGS if raw else {}),
            **GATES_WITHOUT_ISSUE_ACTIVITY,
        },
        supervisor_pass_interval_seconds=INTERVAL,
        supervisor_pass_timeout_seconds=TIMEOUT,
    )


@pytest.mark.parametrize("wiring", list(WIRINGS))
async def test_the_supervisors_scans_are_probed_exactly_when_its_tick_registers(
    tmp_path: Path, wiring: str
) -> None:
    """The probe and the registration are one predicate, read on every wiring.

    Each wiring boots twice: once over a credential that refuses issue
    activity, the one scan the supervisor's alarms declare, and once over a
    credential that answers everything. A refusal naming a ``supervisor/``
    alarm happens exactly when the answering boot registers the tick; a
    deployment that registers none boots over the refusing credential and
    never asks it about issue activity at all.
    """
    raw_scopes, reconciled_scopes, absent, dispatching = WIRINGS[wiring]
    if reconciled_scopes is None:
        # Nothing is dialled, so nothing can be probed and no tick registers:
        # preflight refuses the partial organize configuration first.
        with pytest.raises(OperationMemberAbsentError):
            await _boot_gated(
                tmp_path,
                raw=raw_scopes,
                reconciled_roster=None,
                dispatching=dispatching,
                tracker=None,
            )
        return

    def board_for(scan_refusals):
        return FakeTrackerPort(
            issues=[],
            marker_prefixes=declared(scopes=raw_scopes).marker_prefixes,
            scan_refusals=scan_refusals,
        )

    refusing = board_for({PassSignal.issues_changed: DIAGNOSIS})
    try:
        await _boot_gated(
            tmp_path / "refusing",
            raw=raw_scopes,
            reconciled_roster=reconciled_scopes,
            dispatching=dispatching,
            tracker=refusing,
        )
    except PassGateCapabilityError as caught:
        refused = [line for line in caught.refusals if "supervisor/" in line]
        assert refused, caught.refusals
    else:
        refused = []
        assert not any(
            PassSignal.issues_changed in probe for probe in refusing.capability_probes
        )

    with structlog.testing.capture_logs():
        answered = await _boot_gated(
            tmp_path / "answering",
            raw=raw_scopes,
            reconciled_roster=reconciled_scopes,
            dispatching=dispatching,
            tracker=board_for({}),
        )
    ticks = [entry for entry in answered.scheduler.passes if entry.name == "supervisor"]

    assert bool(refused) is bool(ticks)
    assert bool(ticks) is (absent is None)


async def test_a_per_issue_deployment_probes_its_own_gates_and_no_supervisor_scan(
    tmp_path: Path,
) -> None:
    """No roster, a delivery probe dialled: the probe is the per-issue gates alone.

    Issue activity is left out of every per-issue gate, so the probe holds
    exactly what the fire-preparation and dispatch passes are gated on and
    nothing the supervisor's alarms declare.
    """
    tracker = FakeTrackerPort(
        issues=[], marker_prefixes=declared(scopes=()).marker_prefixes
    )

    with structlog.testing.capture_logs():
        runtime = await _boot_gated(
            tmp_path,
            raw=(),
            reconciled_roster=(),
            dispatching=True,
            tracker=tracker,
        )

    assert [
        entry for entry in runtime.scheduler.passes if entry.name == "supervisor"
    ] == []
    assert tracker.capability_probes == [
        (PassSignal.triage_backlog, PassSignal.approved_changed)
    ]


async def test_the_floor_over_a_refusing_credential_boots_and_probes_nothing(
    tmp_path: Path,
) -> None:
    """No roster, a tracker dialled that refuses issue activity, no delivery probe.

    The floor schedules no session pass, no dispatch pass and no supervisor
    tick, so it needs no scan at all: it boots over a credential that would
    refuse the supervisor's one scan, and that credential is never asked.
    """
    tracker = FakeTrackerPort(
        issues=[], scan_refusals={PassSignal.issues_changed: DIAGNOSIS}
    )

    with structlog.testing.capture_logs():
        runtime = await _runtime(
            tmp_path,
            tracker=tracker,
            runner=FakeAgentRunner(events=[]),
            operation=minimal_fixture(),
            github_api=None,
        )

    assert [entry.name for entry in runtime.scheduler.passes] == []
    assert tracker.capability_probes == []


def test_the_example_operation_declares_the_roster_the_tick_reads() -> None:
    """The shipped example names the one table, and no second spelling of it."""
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "organize_scopes" in text
    assert "supervisor_scopes" not in text


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
        stored = stored_alarm(
            await port.read_run_alarms(issue_key=lane),
            subject=subject(lane),
            signal=SIGNAL,
        )
        assert stored is not None
        assert alarm_raised(stored)


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
        stored = stored_alarm(
            await port.read_run_alarms(issue_key=lane),
            subject=LaneSubject(scope_key=ref.key, lane_key=lane),
            signal=SIGNAL,
        )
        assert stored is not None, ref.key
        assert alarm_raised(stored), ref.key
        raised = [
            event
            for event in await port.lane_run_events(issue_key=lane, lane_key=lane)
            if event.kind is RunEventKind.RUN_ALARM_RAISED
        ]
        assert len(raised) == 1, ref.key


#: A stalled lane whose landing row records its best commit again (KOD-681):
#: three rows, two distinct shas. The two counts differ, so a clock that read
#: the rows' length would report a different value from one that reads shas.
LANDING_LANE = "LANE-R"
LANDING_COMMITS = ("sha-one", "sha-two", "sha-one")


async def test_a_stalled_lane_whose_landing_repeats_a_sha_raises_on_distinct_shas():
    """A returning sha is a recorded act, not new work, and not a malformed read.

    The tick completes over a record whose commit shas repeat, and the alarm
    it raises carries the bound's field and value and the count of distinct
    shas the lane recorded: two, where the rows number three.
    """
    operation = declared(scopes=(SCOPE,))
    port = await board(
        lanes=(LANDING_LANE,),
        commits=LANDING_COMMITS,
        scope=SCOPE,
        holder=supervisor_holder(operation_name=operation.operation_name),
    )
    # The premise: a sha repeats, and the distinct shas still pass the bound,
    # so the lane is measured and the repeat is what the reading has to take.
    assert len(set(LANDING_COMMITS)) == 2
    assert len(LANDING_COMMITS) == 3
    assert len(set(LANDING_COMMITS)) > BOUND

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

    stored = stored_alarm(
        await port.read_run_alarms(issue_key=LANDING_LANE),
        subject=subject(LANDING_LANE),
        signal=SIGNAL,
    )
    assert stored is not None
    assert alarm_raised(stored)
    assert stored.bound is not None
    assert stored.bound.config_field == "run_alarm_max_commits_without_closure"
    assert stored.bound.configured_value == BOUND
    assert stored.bound.observed_value == 2
    raised = [
        event
        for event in await port.lane_run_events(
            issue_key=LANDING_LANE, lane_key=LANDING_LANE
        )
        if event.kind is RunEventKind.RUN_ALARM_RAISED
    ]
    assert len(raised) == 1


async def write_ledger(port):
    """Every write a tick left on the board, per issue, as one comparable value.

    Each comment write with the issue it landed on and its body, each issue's
    whole stream, every alarm record on each issue, and each lease grant's
    holder and surfaces. Nothing here is keyed by a comment's own identity, so
    two boards built the same way compare equal exactly when the same writes
    landed on them.
    """
    rows = {row.comment_key: row for row in port.comments}
    issues = sorted(port.issues)
    return (
        sorted((rows[key].issue_key, body) for key, body in port.comment_writes),
        {
            key: list(await port.lane_run_events(issue_key=key, lane_key=key))
            for key in issues
        },
        {key: list(await port.read_run_alarms(issue_key=key)) for key in issues},
        [(lease.holder, lease.surfaces) for lease in port.lease_writes],
    )


async def test_the_composed_tick_observes_a_scope_stalled_at_a_stage_barrier():
    """KOD-503: the scope arm runs in the composed tick, beside the lane arm.

    Every member of the scope has entered the criteria stage while none carries
    the body stage's marker, so the barrier between the two is open and the
    tick says so at warning, naming the scope and the rung's marker. It writes
    nothing about the scope anywhere — no record and no event is keyed to it —
    and the lanes are still observed exactly as without it: the whole write
    ledger of the tick equals the one the same tick leaves on the same board
    with no stage marker on any member, where the scope arm observes nothing.
    """
    operation = declared(scopes=(SCOPE,))

    async def tick(*, staged):
        port = await board(
            lanes=LANES,
            scope=SCOPE,
            holder=supervisor_holder(operation_name=operation.operation_name),
        )
        if staged:
            for lane in LANES:
                issue = port.issues[lane]
                port.issues[lane] = issue.model_copy(
                    update={"issue_labels": issue.issue_labels | {"criteria"}}
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
        with structlog.testing.capture_logs() as logs:
            async with asyncio.timeout(TICK_BOUND_SECONDS):
                assert await scheduled.run(FIXTURE_EPOCH) is PassRun.RAN
        return port, logs

    port, logs = await tick(staged=True)
    unstaged, quiet = await tick(staged=False)

    assert [
        (entry["log_level"], entry["scope"], entry["marker"])
        for entry in logs
        if entry["event"] == "supervisor_scope_alarm_raised"
    ] == [("warning", SCOPE.key, TICKET_MARKER_SOURCE)]
    assert [entry for entry in quiet if "scope" in entry["event"]] == []
    written = {row.comment_key: row.issue_key for row in port.comments}
    assert {written[key] for key, _ in port.comment_writes} == set(LANES)
    assert await write_ledger(port) == await write_ledger(unstaged)
    for lane in LANES:
        assert all(
            event.subject_key != SCOPE.key
            for event in await port.lane_run_events(issue_key=lane, lane_key=lane)
        )
        assert all(
            not isinstance(record.subject, ScopeSubject)
            for record in await port.read_run_alarms(issue_key=lane)
        )
    for lane in LANES:
        stored = stored_alarm(
            await port.read_run_alarms(issue_key=lane),
            subject=subject(lane),
            signal=SIGNAL,
        )
        assert stored is not None
        assert alarm_raised(stored)


async def test_the_composed_tick_observes_a_scope_stalled_at_the_groom_barrier():
    """The first rung is observed too: graph complete, then body complete.

    One member carries the groom marker and has entered the body stage; the
    other carries neither. So the barrier between grooming and the body stage
    is open, and the warning names the groom marker's address. No member has
    entered the criteria stage, so the next barrier is quiet.
    """
    operation = declared(scopes=(SCOPE,))
    port = await board(
        lanes=LANES,
        scope=SCOPE,
        holder=supervisor_holder(operation_name=operation.operation_name),
    )
    entered, behind = LANES
    issue = port.issues[entered]
    port.issues[entered] = issue.model_copy(
        update={"issue_labels": issue.issue_labels | {"groomed", "body"}}
    )
    assert not {"groomed", "body"} & port.issues[behind].issue_labels
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

    with structlog.testing.capture_logs() as logs:
        async with asyncio.timeout(TICK_BOUND_SECONDS):
            assert await scheduled.run(FIXTURE_EPOCH) is PassRun.RAN

    assert [
        (entry["log_level"], entry["scope"], entry["marker"])
        for entry in logs
        if entry["event"] == "supervisor_scope_alarm_raised"
    ] == [("warning", SCOPE.key, GROOM_MARKER_SOURCE)]


def _enter_criteria_stage(port, lane, *, entered):
    """Give *lane* the criteria stage's label, or take it away."""
    issue = port.issues[lane]
    labels = (
        issue.issue_labels | {"criteria"}
        if entered
        else issue.issue_labels - {"criteria"}
    )
    port.issues[lane] = issue.model_copy(update={"issue_labels": labels})


def _scope_raises(logs):
    return [
        (entry["scope"], entry["marker"])
        for entry in logs
        if entry["event"] == "supervisor_scope_alarm_raised"
    ]


async def test_every_declared_scope_is_observed_on_every_tick():
    """Each declared scope's barrier is read from its own roster, every tick.

    Two scopes with different rosters, one lane each. Only the second scope's
    lane has entered the criteria stage without the body stage's marker, so
    only its barrier is open, and the tick says so for that scope and no
    other, and says it again on the next tick. Then the two swap, and the
    next tick names the first scope alone.
    """
    operation = declared(scopes=tuple(PAIRED_LANES))
    port = await board(
        lanes=tuple(PAIRED_LANES.values()),
        scopes={ref: (lane,) for ref, lane in PAIRED_LANES.items()},
        holder=supervisor_holder(operation_name=operation.operation_name),
    )
    _enter_criteria_stage(port, PAIRED_LANES[SECOND_SCOPE], entered=True)
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

    for _ in range(2):
        with structlog.testing.capture_logs() as logs:
            async with asyncio.timeout(TICK_BOUND_SECONDS):
                assert await scheduled.run(FIXTURE_EPOCH) is PassRun.RAN
        assert _scope_raises(logs) == [(SECOND_SCOPE.key, TICKET_MARKER_SOURCE)]

    _enter_criteria_stage(port, PAIRED_LANES[SECOND_SCOPE], entered=False)
    _enter_criteria_stage(port, PAIRED_LANES[SCOPE], entered=True)
    with structlog.testing.capture_logs() as logs:
        async with asyncio.timeout(TICK_BOUND_SECONDS):
            assert await scheduled.run(FIXTURE_EPOCH) is PassRun.RAN

    assert _scope_raises(logs) == [(SCOPE.key, TICKET_MARKER_SOURCE)]


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
        stored = stored_alarm(
            await port.read_run_alarms(issue_key=lane),
            subject=subject(lane),
            signal=SIGNAL,
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
    """The walk's own operation, as a deployment declaring its roster boots it.

    A declared roster is refused at boot without the organize table beside it,
    so the walk's marker prefixes are laid over the operation that carries the
    table: the tick reads each scope's stage markers through it.
    """
    declared = native_operation()
    return declared_operation().model_copy(
        update={
            "operation_name": declared.operation_name,
            "marker_prefixes": {
                **declared_operation().marker_prefixes,
                **declared.marker_prefixes,
                MARKER_PURPOSE: ALARM_PREFIX,
            },
            **roster(WALK_SCOPE, repo_url=ORIGIN),
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
        stored = stored_alarm(
            await port.read_run_alarms(issue_key=lane),
            subject=LaneSubject(scope_key=WALK_SCOPE.key, lane_key=lane),
            signal=SIGNAL,
        )
        if stored is not None and alarm_raised(stored):
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
    # The clock counts distinct shas: the landing row records the best commit
    # again (KOD-681), so the record may carry a sha twice.
    recorded = {row.sha for row in stalled.commits}
    # The precondition, from the board: a lane whose recorded commits do not
    # pass the bound could satisfy what follows by never being measured.
    assert len(recorded) > STALL_BOUND
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

    stored = stored_alarm(
        await port.read_run_alarms(issue_key="S"),
        subject=LaneSubject(scope_key=WALK_SCOPE.key, lane_key="S"),
        signal=SIGNAL,
    )
    assert stored is not None
    assert alarm_raised(stored)
    assert stored.bound is not None
    assert stored.bound.config_field == "run_alarm_max_commits_without_closure"
    assert stored.bound.configured_value == STALL_BOUND
    assert stored.bound.observed_value == len(recorded)
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
