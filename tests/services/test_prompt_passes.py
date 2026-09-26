"""The registrations: each pass, on its own cadence, with its own prompt.

Driven through the real ``PassScheduler`` over a substituted clock, so what
is asserted is what a tick actually sends — not what a builder returned.
The prompts are compared against the registry's own renders rather than
against any string written here: a literal in the assertion would pass
against a literal in the code, which is the one thing this has to catch.
"""

import ast
import asyncio
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Final

import pytest
import structlog.testing
from pydantic import ValidationError

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.passes import (
    _DISPATCH_NAME,
    MARKER_PURPOSES_BY_FAMILY,
    DispatchRuntime,
    build_dispatch_runtime,
    build_prompt_passes,
    verify_pass_preflight,
)
from kodezart.composition.records import RECORD_KIND_BY_PASS
from kodezart.composition.tracker import DialledTracker
from kodezart.config.app import AppConfig
from kodezart.core.constants import DEFAULT_LANE
from kodezart.core.errors import (
    PassGateCapabilityError,
    PassKnowledgeCapabilityError,
    PromptRenderError,
)
from kodezart.core.logging import get_logger
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.services import pass_scheduler as pass_scheduler_module
from kodezart.services.pass_scheduler import PassScheduler, ScheduledPass
from kodezart.services.prompt_pass import gate_render_bindings, pass_render_bindings
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.agent import (
    PASS_GATE_SCHEMA,
    ResultEvent,
    ScopeScanNode,
    ScopeScanOutput,
)
from kodezart.types.domain.dispatch import PassRun, PassSignal
from kodezart.types.domain.operation import (
    DocumentSystem,
    OperationConfig,
    OperationMemberAbsentError,
    QueueState,
    RecordDestination,
    RunKind,
    ScopeLabel,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_records import (
    RunIdentity,
    RunOutcome,
    RunRecord,
    RunRecordResult,
)
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.session import SessionType
from tests.fakes import (
    FIXTURE_EPOCH,
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
    FakeDeliveryProbe,
    FakeGitService,
    FakeJobQueue,
    FakeRepoCache,
    FakeScopeStatusWriter,
    FakeTrackerPort,
    FakeWorkspaceProvider,
    ManagedFakeLinearMcpServer,
    PassThroughGate,
    make_tracker_issue,
)
from tests.prompts.sets import V5_SET
from tests.prompts.test_minimal_floor import minimal_fixture
from tests.prompts.test_operation_config import raw_example, write_toml
from tests.prompts.test_organize_mandate_bindings import declared_operation
from tests.prompts.test_prompt_wiring import DEFAULT_SET, load_registry
from tests.services.test_pass_scheduler import Metronome
from tests.services.test_prompt_pass import example_config

COMPOSITION_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "kodezart"
    / "composition"
    / "passes.py"
)

#: Cadences no default would produce, so what is observed is the knob's
#: consumer and not a coincidence.
FIRE_PREP_INTERVAL = 613.0
GROOMING_INTERVAL = 907.0

#: Budgets on the same principle, and distinct from the cadences above: a
#: row wired to its own interval where its timeout belongs would still
#: pass against shared values, and fails against these.
FIRE_PREP_TIMEOUT = 401.0
GROOMING_TIMEOUT = 809.0

#: The other cadences this module's deployment sets, on the same principle.
#: None of them has a default: a pass is scheduled only when its interval and
#: timeout are both set, so a deployment that means a pass to run says so.
DISPATCH_INTERVAL = 293.0
DISPATCH_TIMEOUT = 211.0
SUPERVISOR_INTERVAL = 317.0
SUPERVISOR_TIMEOUT = 113.0

#: Every cadence setting left unset, for the rows that ask what a deployment
#: that sets none of them schedules.
NO_CADENCE: dict[str, object] = {
    "dispatch_pass_interval_seconds": None,
    "dispatch_pass_timeout_seconds": None,
    "fire_prep_pass_interval_seconds": None,
    "fire_prep_pass_timeout_seconds": None,
    "grooming_pass_interval_seconds": None,
    "grooming_pass_timeout_seconds": None,
    "supervisor_pass_interval_seconds": None,
    "supervisor_pass_timeout_seconds": None,
}


#: The shipped example declares documents and records in the knowledge
#: system, so a deployment wiring its passes must grant them that store —
#: and a grant carries a credential.  Both are overridable, because the
#: mismatch between the two halves is itself one of the cases below.
KNOWLEDGE_TOKEN = "knowledge-credential"


#: The instant every tick in this module begins at.  The scheduler stamps
#: ``started_at`` from the wall clock and the pass binds the per-run record
#: title from it (KOD-290), so a case that compares a sent prompt with a
#: rendered one has to render the same instant the tick used.
TICK: Final[datetime] = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class _FrozenDatetime(datetime):
    """``datetime`` whose ``now`` is the tick this module fires at."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        del tz
        return TICK


@pytest.fixture(autouse=True)
def _ticks_at_a_known_instant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pass_scheduler_module, "datetime", _FrozenDatetime)


def per_run(key: PromptKey) -> dict[str, object]:
    """What the service binds for *key*'s pass at the frozen tick."""
    identity = RunIdentity(
        kind=RECORD_KIND_BY_PASS[key], name=key.value, started_at=TICK
    )
    return pass_render_bindings(identity)


#: The deployment's own tracker credential, set on every configuration this
#: module builds: the organize stage sessions of a scope run are described the
#: tracker server built from it, so a scope deployment's boot requires it, and
#: a per-issue deployment reads nothing else off it. A case about its absence
#: overrides it with an empty tracker table.
TRACKER_CREDENTIAL: dict[str, object] = {"token": "lin_api_" + "k" * 40}


def _config(tmp_path: Path, **overrides: object) -> AppConfig:
    settings: dict[str, object] = {
        "tracker": TRACKER_CREDENTIAL,
        "fire_prep_pass_interval_seconds": FIRE_PREP_INTERVAL,
        "fire_prep_pass_timeout_seconds": FIRE_PREP_TIMEOUT,
        "grooming_pass_interval_seconds": GROOMING_INTERVAL,
        "grooming_pass_timeout_seconds": GROOMING_TIMEOUT,
        "dispatch_pass_interval_seconds": DISPATCH_INTERVAL,
        "dispatch_pass_timeout_seconds": DISPATCH_TIMEOUT,
        "supervisor_pass_interval_seconds": SUPERVISOR_INTERVAL,
        "supervisor_pass_timeout_seconds": SUPERVISOR_TIMEOUT,
        "scheduled_pass_working_dir": str(tmp_path / "pass"),
        "knowledge": {
            "session_grants": [SessionType.SCHEDULED_PASS],
            "connection": {
                "transport": "http",
                "server_url": "https://knowledge.invalid/mcp",
                "credential": KNOWLEDGE_TOKEN,
            },
        },
    }
    settings.update(overrides)
    return AppConfig(**settings)  # type: ignore[arg-type]


def dialled_over(
    tracker: FakeTrackerPort | None,
    operation: OperationConfig,
) -> DialledTracker | None:
    """A boot's tracker: the port, its session and its own write ledger.

    One value, because the builders take one.  A port handed over without
    the ledger of this process's writes is a pass gate that wakes on the
    operation's own churn, and there is no shape here that can express it
    (KOD-289).
    """
    if tracker is None:
        return None
    return DialledTracker(
        tracker=tracker,
        caller=ManagedFakeLinearMcpServer(),
        operation=operation,
        ledger=tracker.self_writes,
        status=FakeScopeStatusWriter(),
    )


async def _registrations(
    tmp_path: Path,
    *,
    operation: OperationConfig | None = None,
    **overrides: object,
) -> tuple[list[ScheduledPass], FakeAgentRunner]:
    """The passes exactly as the composition registers them.

    No tracker: the prompt passes take none, because the session reaches
    the tracker itself and so does the gate question asked before it.
    """
    declared = example_config() if operation is None else operation
    prompts = load_registry(bindings=dict(bindings_for(declared)))
    runner = FakeAgentRunner(events=[])
    return (
        await build_prompt_passes(
            recorder=RunRecorder(records={}, sinks={}),
            config=_config(tmp_path, **overrides),
            operation=declared,
            prompts=prompts,
            runner=runner,
            skills=SUPPRESS_ALL_SKILLS,
        ),
        runner,
    )


#: The vendor's own words when a credential holds no scope for a scan.
DIAGNOSIS = "auth_insufficient_scope: this credential cannot read those"

#: Every boot refusal below is asserted in both HTTP modes.  Debug is the
#: switch a handler would read to keep serving past a refusal, and a boot
#: that refuses only outside it degrades in the mode it was not tested in
#: (KOD-706: no code path degrades, skips a write, or logs and continues).
EITHER_MODE = pytest.mark.parametrize(
    "debug", [False, True], ids=["debug-off", "debug-on"]
)


#: What the standing-scope pass is registered under, spelled here rather
#: than imported: the name is what an operator reads in a log and what a
#: later pass-set assertion enumerates, so a rename must redden this too.
HEARTBEAT_PASS = "scope_heartbeat"

#: The organize owner's two bounds.
ORGANIZE_BOUNDS: dict[str, object] = {
    "max_admission_rounds": 2,
    "max_convergence_rounds": 2,
}

#: The host-MCP opt-in, switched on. It is the organize session's only way to
#: the tracker's tools, so boot refuses a deployment that declares organize
#: scopes over a dialled tracker without it; every such deployment here sets it.
#: The deployment half of a standing-scope operation: the owner bounds the
#: run's stages require and the dispatch cadence driving the heartbeat, so
#: what the schedule holds is decided by the declared rows alone.
STANDING_SCOPE_SETTINGS: dict[str, object] = {
    "organize": ORGANIZE_BOUNDS,
    "write_back": {"max_verify_rounds": 2},
}


#: The one standing scope this module declares, and the board the pass
#: reads it off. Stated once as a ref so the row the operation declares and
#: the container the label sits on cannot drift apart: a pass submitting
#: some other scope's run would then be submitting a scope no board here
#: has.
STANDING_SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="standing-project")


def standing_scope_operation(*, scopes: bool = True) -> OperationConfig:
    """The declared organize table, with or without one standing-scope row."""
    fields = declared_operation().model_dump()
    fields["organize_scopes"] = (
        [
            {
                "scope": STANDING_SCOPE.model_dump(mode="json"),
                "repo_url": fields["repos"][0]["url"],
            }
        ]
        if scopes
        else []
    )
    return OperationConfig.model_validate(fields)


def approving_board() -> FakeTrackerPort:
    """The standing project, carrying the label that admits its run.

    The container as well as the label, because the approval question reads
    a node's labels AND its parent edge: a board holding the label and no
    container answers a question no workspace answers.
    """
    return FakeTrackerPort(
        scope_containers=[
            ScopeContainer(
                ref=STANDING_SCOPE,
                name="the standing project",
                description="",
                url=f"https://tracker.invalid/project/{STANDING_SCOPE.key}",
            )
        ],
        scope_label_members={STANDING_SCOPE: frozenset({ScopeLabel.APPROVED})},
    )


async def _runtime(
    tmp_path: Path,
    *,
    tracker: FakeTrackerPort | None,
    runner: FakeAgentRunner,
    operation: OperationConfig | None = None,
    reconciled: OperationConfig | None = None,
    github_api: FakeDeliveryProbe | None = None,
    prompt_set: str = DEFAULT_SET,
    queue: FakeJobQueue | None = None,
    **overrides: object,
) -> DispatchRuntime:
    """Boot the scheduled-pass runtime exactly as the composition root does.

    Preflight FIRST and then the wiring, in that order and as two calls,
    because that is what the root does: every refusal the passes can raise
    is settled before anything stateful is built, and the builder below
    re-checks none of it.

    *reconciled* is the operation the dialled tracker carries, where a caller
    needs it to differ from the one handed in raw. Preflight still runs on the
    raw copy, as the root's does. Left out, the two are one object, so nothing
    downstream can tell which copy it was handed.

    *github_api* is the delivery probe the dispatch passes are gated on, so a
    caller that needs a schedule with something registered BEFORE the arms this
    module asks about can have one. Left out, no dispatch pass is built, which
    is what every caller here but the registration rows wants.

    *queue* is the caller's when it means to read what a registered pass
    submitted: the queue this boot wires is the one the pass holds, and a
    case that could not see it could only assert the registration rather
    than the pass.
    """
    declared = example_config() if operation is None else operation
    config = _config(tmp_path, **overrides)
    prompts = load_registry(
        default_set=prompt_set,
        bindings=dict(bindings_for(declared)),
    )
    queue = FakeJobQueue() if queue is None else queue
    await verify_pass_preflight(
        config=config,
        operation=declared,
        tracker=tracker,
        github_api=github_api,
        prompts=prompts,
    )
    return await build_dispatch_runtime(
        workspace=FakeWorkspaceProvider(),
        recorder=RunRecorder(records={}, sinks={}),
        config=config,
        operation=declared,
        dialled=dialled_over(tracker, declared if reconciled is None else reconciled),
        github_api=github_api,
        queue=queue,
        registry=queue,
        # The dispatch passes' own collaborators, which nothing builds without a
        # delivery probe: with none handed over these stay the absent values
        # every other caller here boots with, so a row that asks for no dispatch
        # pass boots exactly as it did.
        gate=PassThroughGate() if github_api is not None else None,
        git=FakeGitService() if github_api is not None else None,  # type: ignore[arg-type]
        cache=FakeRepoCache() if github_api is not None else None,  # type: ignore[arg-type]
        prompts=prompts,
        runner=runner,
        skills=SUPPRESS_ALL_SKILLS,
        log=get_logger(__name__),
    )


async def _settle_requests(metronome: Metronome, count: int) -> None:
    """Wait until every driver has run its boot tick and asked for its sleep.

    With a limit of zero no sleep completes, so what the scheduler ran is
    exactly the boot ticks; each driver then asks for its interval once.
    """
    for _ in range(count * 500):
        if len(metronome.requested) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"only {metronome.requested} requested, expected {count}")


async def test_each_pass_sends_its_own_rendered_prompt_on_its_own_cadence(
    tmp_path: Path,
) -> None:
    """One tick each: two sessions, two prompts, two configured intervals."""
    registered, runner = await _registrations(tmp_path)
    prompts = load_registry(bindings=dict(bindings_for(example_config())))
    metronome = Metronome(limit=0)
    scheduler = PassScheduler(passes=registered, sleep=metronome.sleep)

    await scheduler.start()
    await _settle_requests(metronome, len(registered))
    await scheduler.stop()

    assert set(metronome.requested) == {FIRE_PREP_INTERVAL, GROOMING_INTERVAL}
    assert {call["prompt"] for call in runner.calls} == {
        prompts.template_for(PromptKey.FIRE_PREP_PASS).render(
            per_run(PromptKey.FIRE_PREP_PASS)
        ),
        prompts.template_for(PromptKey.GROOMING_PASS).render(
            per_run(PromptKey.GROOMING_PASS)
        ),
    }


async def test_the_registrations_take_every_cadence_from_configuration(
    tmp_path: Path,
) -> None:
    """Each pass carries the interval its own knob holds, never a shared one."""
    registered, _ = await _registrations(tmp_path)

    assert [(entry.name, entry.interval_seconds) for entry in registered] == [
        (PromptKey.FIRE_PREP_PASS.value, FIRE_PREP_INTERVAL),
        (PromptKey.GROOMING_PASS.value, GROOMING_INTERVAL),
    ]


async def test_the_registrations_take_every_budget_from_configuration(
    tmp_path: Path,
) -> None:
    """Each pass carries the timeout its own knob holds, never its cadence."""
    registered, _ = await _registrations(tmp_path)

    assert [(entry.name, entry.timeout_seconds) for entry in registered] == [
        (PromptKey.FIRE_PREP_PASS.value, FIRE_PREP_TIMEOUT),
        (PromptKey.GROOMING_PASS.value, GROOMING_TIMEOUT),
    ]


def test_the_pass_composition_holds_no_numeric_literal() -> None:
    """A cadence written into the wiring fails here with nothing to negotiate."""
    tree = ast.parse(COMPOSITION_SOURCE.read_text(encoding="utf-8"))
    numbers = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    ]

    assert numbers == []


async def test_the_boot_tick_asks_no_gate_and_the_next_tick_does(
    tmp_path: Path,
) -> None:
    """The registered pass keeps its window: unasked at boot, asked after.

    Asserted through ticks rather than by reading the wiring: the boot tick
    opens the session over the whole board, and the tick after asks the
    gate question over the window since it, in the schema of the gate's
    output model, before opening anything.
    """
    registered, runner = await _registrations(tmp_path)
    fire_prep = next(
        entry for entry in registered if entry.name == PromptKey.FIRE_PREP_PASS.value
    )

    await fire_prep.run(FIXTURE_EPOCH)
    boot_calls = list(runner.calls)
    await fire_prep.run(TICK)

    prompts = load_registry(bindings=dict(bindings_for(example_config())))
    assert [call["output_format"] for call in boot_calls] == [None]
    assert [call["output_format"] for call in runner.calls[1:]] == [
        {"type": "json_schema", "schema": PASS_GATE_SCHEMA},
        None,
    ], "the runner fake answers nothing, and no answer runs the pass"
    assert runner.calls[1]["prompt"] == prompts.template_for(
        PromptKey.PASS_GATE
    ).render(
        gate_render_bindings(
            name=PromptKey.FIRE_PREP_PASS.value, window_start=FIXTURE_EPOCH
        )
    )
    assert runner.calls[1]["session_type"] is SessionType.SCHEDULED_PASS


async def test_the_boot_seam_registers_the_prompt_passes(tmp_path: Path) -> None:
    """Deleting the schedule-extending call must redden something.

    The gap KOD-60's own grading named: every assertion over the built
    schedule filtered to dispatch entries, so the two prompt passes could
    have stopped being registered with the suite still green.
    """
    runtime = await _runtime(
        tmp_path,
        tracker=None,
        runner=FakeAgentRunner(events=[]),
    )

    assert {entry.name for entry in runtime.scheduler.passes} == {
        PromptKey.FIRE_PREP_PASS.value,
        PromptKey.GROOMING_PASS.value,
    }
    assert runtime.lifecycle is None


async def test_declared_standing_scopes_register_the_heartbeat_on_the_dispatch_cadence(
    tmp_path: Path,
) -> None:
    """The scope heartbeat, beside the grooming pass.

    One registration for the whole operation, on the cadence the dispatch
    scans already run on, ticking at boot, and with no report: it writes no
    record, so a tick of it is not a run anything could record. The grooming
    pass is still there once — it works the whole board before approval, the
    heartbeat submits what approval admits — and the cadence is read off the
    configuration rather than spelled here.
    """
    config = _config(tmp_path, **STANDING_SCOPE_SETTINGS)
    operation = standing_scope_operation()
    repo_url = operation.repos[0].url
    scan = ScopeScanOutput(
        scopes=[
            ScopeScanNode(
                kind=STANDING_SCOPE.kind,
                key=STANDING_SCOPE.key,
                repository=repo_url,
                why="approved, with an open issue below it",
            )
        ],
        reason="one approved project is not finished",
    )
    answer = ResultEvent(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="scan-session",
        structured_output=scan.model_dump(mode="json", by_alias=True),
    )
    queue = FakeJobQueue()
    runtime = await _runtime(
        tmp_path,
        tracker=approving_board(),
        runner=FakeAgentRunner(events=[answer]),
        operation=operation,
        queue=queue,
        **STANDING_SCOPE_SETTINGS,
    )

    registered = [entry.name for entry in runtime.scheduler.passes]
    (heartbeat,) = [
        entry for entry in runtime.scheduler.passes if entry.name == HEARTBEAT_PASS
    ]
    assert heartbeat.interval_seconds == config.dispatch_pass_interval_seconds
    assert heartbeat.timeout_seconds == config.dispatch_pass_timeout_seconds
    assert heartbeat.report is None
    assert heartbeat.tick_at_boot is True
    assert registered.count(HEARTBEAT_PASS) == 1
    assert registered.count(PromptKey.GROOMING_PASS.value) == 1

    # The REGISTERED callable, ticked: what the scheduler would reach is the
    # heartbeat's own scheduled run, answering in the vocabulary a scheduled
    # pass answers in and submitting the node the scan lists onto the queue
    # this boot wired, where POST /fire submits too. A registration carrying
    # some other callable passes every assertion above and fails here.
    assert await heartbeat.run(FIXTURE_EPOCH) is PassRun.RAN
    ((lane, request),) = queue.submissions
    assert lane == DEFAULT_LANE
    assert request.scope == STANDING_SCOPE
    assert request.repo_url == repo_url
    # The scan lists the same node again, and the registry holds its run as
    # live: the second tick submits nothing beside it.
    assert await heartbeat.run(FIXTURE_EPOCH) is PassRun.SKIPPED
    assert len(queue.submissions) == 1


async def test_an_operation_with_no_scope_labels_registers_no_heartbeat(
    tmp_path: Path,
) -> None:
    """Non-vacuity for the registration above: the approval labels wire it.

    The same deployment over an operation that declares no scope labels
    schedules no heartbeat: its scan would have no approval to look for.
    """
    fields = example_config().model_dump()
    fields["scope_labels"] = {}
    runtime = await _runtime(
        tmp_path,
        tracker=FakeTrackerPort(),
        runner=FakeAgentRunner(events=[]),
        operation=OperationConfig.model_validate(fields),
    )

    assert {entry.name for entry in runtime.scheduler.passes} == {
        PromptKey.FIRE_PREP_PASS.value,
        PromptKey.GROOMING_PASS.value,
    }


@EITHER_MODE
async def test_a_signal_the_credential_cannot_scan_for_aborts_boot(
    tmp_path: Path, debug: bool
) -> None:
    """KOD-151: the silent failure, made the loudest thing a deployment has.

    A gate whose scan the credential is not scoped for answers "nothing
    moved" every tick, which is exactly what a quiet board answers. The
    dispatch pass it guards never runs again and nothing says so — so boot
    asks first, and dies naming the signal, the pass and the vendor's reason.
    """
    tracker = FakeTrackerPort(scan_refusals={PassSignal.reviews_changed: DIAGNOSIS})
    runner = FakeAgentRunner(events=[])

    with pytest.raises(PassGateCapabilityError) as caught:
        await _runtime(
            tmp_path,
            tracker=tracker,
            runner=runner,
            http={"debug": debug},
            github_api=FakeDeliveryProbe(),
            dispatch_pass_gate_signals=[
                PassSignal.approved_changed,
                PassSignal.reviews_changed,
            ],
        )

    named = str(caught.value)
    assert PassSignal.reviews_changed.value in named
    assert _DISPATCH_NAME in named
    assert DIAGNOSIS in named
    assert PassSignal.approved_changed.value not in named, (
        "a signal the credential can answer is not part of the refusal"
    )
    # The refusal ends the boot: one probe, and no session after it.
    assert tracker.capability_probes == [
        (PassSignal.approved_changed, PassSignal.reviews_changed)
    ]
    assert runner.calls == []


@EITHER_MODE
async def test_two_refused_signals_are_named_in_one_abort(
    tmp_path: Path, debug: bool
) -> None:
    """Every refused signal is named at once, with its pass and its reason.

    An operator fixing one scope at a time pays a boot cycle per signal, so
    the probe asks for every signal the schedule declares and the abort names
    each refusal it came back with, the pass that refusal leaves ungated, and
    the diagnosis the backend gave for it.
    """
    second = "auth_insufficient_scope: nor those"
    tracker = FakeTrackerPort(
        scan_refusals={
            PassSignal.approved_changed: DIAGNOSIS,
            PassSignal.reviews_changed: second,
        }
    )
    runner = FakeAgentRunner(events=[])

    with pytest.raises(PassGateCapabilityError) as caught:
        await _runtime(
            tmp_path,
            tracker=tracker,
            runner=runner,
            http={"debug": debug},
            github_api=FakeDeliveryProbe(),
            dispatch_pass_gate_signals=[
                PassSignal.approved_changed,
                PassSignal.reviews_changed,
            ],
        )

    named = str(caught.value)
    assert PassSignal.approved_changed.value in named
    assert PassSignal.reviews_changed.value in named
    assert DIAGNOSIS in named and second in named
    assert named.count(_DISPATCH_NAME) == 2
    # One abort carries both: the probe asked for both signals in one call.
    assert tracker.capability_probes == [
        (PassSignal.approved_changed, PassSignal.reviews_changed)
    ]
    assert runner.calls == []


@EITHER_MODE
async def test_two_refused_signals_sharing_one_diagnosis_are_each_named(
    tmp_path: Path, debug: bool
) -> None:
    """Refusals are one per refused signal, however many share a diagnosis.

    A backend gives every signal one tool serves the same diagnosis, so two
    refused signals with one reason are the ordinary multi-refusal case: the
    abort still carries a refusal for each signal, each naming its own.
    """
    tracker = FakeTrackerPort(
        scan_refusals={
            PassSignal.approved_changed: DIAGNOSIS,
            PassSignal.triage_backlog: DIAGNOSIS,
        }
    )
    runner = FakeAgentRunner(events=[])

    with pytest.raises(PassGateCapabilityError) as caught:
        await _runtime(
            tmp_path,
            tracker=tracker,
            runner=runner,
            http={"debug": debug},
            github_api=FakeDeliveryProbe(),
            dispatch_pass_gate_signals=[
                PassSignal.approved_changed,
                PassSignal.triage_backlog,
            ],
        )

    refusals = caught.value.refusals
    assert len(refusals) == 2, refusals
    for signal in (PassSignal.approved_changed, PassSignal.triage_backlog):
        starting = [item for item in refusals if item.startswith(f"{signal.value} ")]
        assert len(starting) == 1, (signal, refusals)
    assert all(DIAGNOSIS in item for item in refusals)
    assert tracker.capability_probes == [
        (PassSignal.approved_changed, PassSignal.triage_backlog)
    ]
    assert runner.calls == []


@EITHER_MODE
async def test_the_prompt_passes_put_no_signal_in_the_probe(
    tmp_path: Path, debug: bool
) -> None:
    """A credential that can scan for nothing still boots the two prompt passes.

    Their gate is a session over the tracker server the pass itself is
    described, so no scan is asked through the dialled port on their
    behalf: a deployment scheduling only them probes nothing and boots.
    """
    tracker = FakeTrackerPort(scan_refusals=dict.fromkeys(PassSignal, DIAGNOSIS))
    runner = FakeAgentRunner(events=[])

    runtime = await _runtime(
        tmp_path,
        tracker=tracker,
        runner=runner,
        http={"debug": debug},
        # Unset, so the heartbeat that pair would schedule is not here either.
        dispatch_pass_interval_seconds=None,
        dispatch_pass_timeout_seconds=None,
    )

    assert tracker.capability_probes == []
    assert {entry.name for entry in runtime.scheduler.passes} == {
        PromptKey.FIRE_PREP_PASS.value,
        PromptKey.GROOMING_PASS.value,
    }


@EITHER_MODE
async def test_a_refused_dispatch_signal_aborts_naming_the_dispatch_pass(
    tmp_path: Path, debug: bool
) -> None:
    """The per-issue dispatch pass's signal is probed and refused like any other.

    A deployment with a delivery probe and repositories whose teams the
    dispatch pass scans schedules the dispatch pass; its gate signal is asked
    for at boot, and a credential that cannot scan for it aborts startup naming
    the dispatch pass and the diagnosis. The two session passes declare no
    signal here, so the dispatch pass is the only thing that can have put the
    signal in the probe.
    """
    operation = example_config()
    assert any(operation.teams_scanned_by(repo.url) for repo in operation.repos)
    tracker = FakeTrackerPort(scan_refusals={PassSignal.approved_changed: DIAGNOSIS})
    runner = FakeAgentRunner(events=[])

    with pytest.raises(PassGateCapabilityError) as caught:
        await _runtime(
            tmp_path,
            tracker=tracker,
            runner=runner,
            http={"debug": debug},
            operation=operation,
            github_api=FakeDeliveryProbe(),
            dispatch_pass_gate_signals=[PassSignal.approved_changed],
        )

    assert caught.value.refusals == (
        f"{PassSignal.approved_changed.value} gates {_DISPATCH_NAME}: {DIAGNOSIS}",
    )
    assert tracker.capability_probes == [(PassSignal.approved_changed,)]
    assert runner.calls == []


@EITHER_MODE
def test_the_refusal_cases_boot_in_the_mode_they_name(
    tmp_path: Path, debug: bool
) -> None:
    """Non-vacuity for the mode rows: the override reaches the boot's config."""
    assert _config(tmp_path, http={"debug": debug}).http.debug is debug


async def test_the_shipped_defaults_boot_and_then_run(tmp_path: Path) -> None:
    """The other arm, end to end: what ships boots, and the pass it wired works.

    Nothing here restates the defaults. Boot probes nothing for the prompt
    passes, and the pass the composition built runs its first tick over the
    board without asking and asks the gate on the tick after.
    """
    tracker = FakeTrackerPort()
    runner = FakeAgentRunner(events=[])

    runtime = await _runtime(tmp_path, tracker=tracker, runner=runner)

    assert tracker.capability_probes == []
    fire_prep = next(
        entry
        for entry in runtime.scheduler.passes
        if entry.name == PromptKey.FIRE_PREP_PASS.value
    )
    await fire_prep.run(FIXTURE_EPOCH)

    assert [call["output_format"] for call in runner.calls] == [None]
    await fire_prep.run(TICK)
    assert [call["output_format"] for call in runner.calls[1:]] == [
        {"type": "json_schema", "schema": PASS_GATE_SCHEMA},
        None,
    ]


@EITHER_MODE
async def test_a_pass_whose_prompt_has_a_hole_refuses_at_preflight(
    tmp_path: Path, debug: bool
) -> None:
    """KOD-150: the hole is a boot refusal naming the pass and the placeholders.

    The operation configuration is boot-static, so the hole a tick would
    find is exactly the hole this finds — and a pass that fails on the tick
    that found it fails silently, every interval, on a board nobody is
    watching. The refusal carries the same type and the same ``missing``
    list the tick would have raised.
    """
    raw = raw_example()
    del raw["endpoints"]
    operation = load_operation_config(write_toml(tmp_path, raw))

    with pytest.raises(PromptRenderError) as caught:
        await verify_pass_preflight(
            config=_config(tmp_path, http={"debug": debug}),
            operation=operation,
            tracker=None,
            github_api=None,
            prompts=load_registry(bindings=dict(bindings_for(operation))),
        )

    assert PromptKey.FIRE_PREP_PASS.value in str(caught.value)
    assert "endpoints.host_runner" in caught.value.missing
    assert "endpoints.host_runner" in str(caught.value)


async def test_the_shipped_example_wires_without_a_render_refusal(
    tmp_path: Path,
) -> None:
    """Non-vacuity: the refusal above is the config's, not the check's."""
    assert len((await _registrations(tmp_path))[0]) == len(
        (PromptKey.FIRE_PREP_PASS, PromptKey.GROOMING_PASS)
    )


# ---------------------------------------------------------------------------
# KOD-1235: boot verifies the marker prefixes a wired pass can ask for
# ---------------------------------------------------------------------------


def without_prefixes(operation: OperationConfig, *purposes: str) -> OperationConfig:
    """*operation* with the named marker purposes undeclared."""
    return operation.model_copy(
        update={
            "marker_prefixes": {
                purpose: prefix
                for purpose, prefix in operation.marker_prefixes.items()
                if purpose not in purposes
            }
        }
    )


async def test_a_scope_deployment_lacking_prefixes_its_passes_ask_for_is_refused(
    tmp_path: Path,
) -> None:
    """Every missing key at once, before any pass is built.

    Measured 2026-09-24 on the live scope deployment: a file declaring ten
    of the template's fifteen prefixes booted four times, and the first
    stage with work refused 30 s in over ``claim`` when its surface lease
    released. Boot now names that key, and every other one a pass it
    schedules can ask for, in the spelling the point-of-use refusal uses.
    """
    operation = without_prefixes(standing_scope_operation(), "claim", "run_alarm")

    with pytest.raises(OperationMemberAbsentError) as caught:
        await verify_pass_preflight(
            config=_config(tmp_path, **STANDING_SCOPE_SETTINGS),
            operation=operation,
            tracker=approving_board(),
            github_api=None,
            prompts=load_registry(bindings=dict(bindings_for(operation))),
        )

    assert caught.value.missing == (
        "marker_prefixes['claim'], marker_prefixes['run_alarm']"
    )


async def test_a_prefix_only_an_unwired_pass_asks_for_does_not_refuse_the_boot(
    tmp_path: Path,
) -> None:
    """The check reads exactly the passes that will wire.

    A scope deployment schedules no per-issue dispatch pass, so the base
    spec and run outcome only that pass records are not its operator's
    problem; the boot goes through and the grooming pass is scheduled.
    """
    dispatch_only = MARKER_PURPOSES_BY_FAMILY["dispatch"] - (
        MARKER_PURPOSES_BY_FAMILY["scope"] | MARKER_PURPOSES_BY_FAMILY["audit"]
    )
    assert dispatch_only == {"base_spec", "run_outcome"}
    operation = without_prefixes(standing_scope_operation(), *dispatch_only)

    runtime = await _runtime(
        tmp_path,
        tracker=approving_board(),
        runner=FakeAgentRunner(events=[]),
        operation=operation,
        organize=STANDING_SCOPE_SETTINGS["organize"],
        write_back=STANDING_SCOPE_SETTINGS["write_back"],
    )

    assert PromptKey.GROOMING_PASS.value in [
        entry.name for entry in runtime.scheduler.passes
    ]


async def test_the_shipped_example_declares_every_prefix_the_wired_passes_ask_for(
    tmp_path: Path,
) -> None:
    """Non-vacuity for the refusal above: the per-issue example boots whole.

    The example wires the dispatch passes — a tracker and a delivery probe
    over a roster with no scopes — and its table declares everything they
    can ask for, so the check has nothing to name.
    """
    runtime = await _runtime(
        tmp_path,
        tracker=FakeTrackerPort(),
        runner=FakeAgentRunner(events=[]),
        github_api=FakeDeliveryProbe(),
    )

    assert [
        entry.name for entry in runtime.scheduler.passes if _DISPATCH_NAME in entry.name
    ] != []


# ---------------------------------------------------------------------------
# KOD-1238: every cadence is set explicitly; unset, the pass is not scheduled
# ---------------------------------------------------------------------------


def _not_configured(logs: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Each pass boot named as not configured, and the settings it named."""
    return {
        str(entry["name"]): entry["settings"]
        for entry in logs
        if entry["event"] == "scheduled_pass_not_configured"
    }


async def test_with_no_cadence_set_a_per_issue_deployment_schedules_nothing(
    tmp_path: Path,
) -> None:
    """Every pass that would wire is named, with the two settings to set."""
    with structlog.testing.capture_logs() as logs:
        runtime = await _runtime(
            tmp_path,
            tracker=FakeTrackerPort(),
            runner=FakeAgentRunner(events=[]),
            github_api=FakeDeliveryProbe(),
            dispatch_pass_interval_seconds=None,
            dispatch_pass_timeout_seconds=None,
            fire_prep_pass_interval_seconds=None,
            fire_prep_pass_timeout_seconds=None,
            grooming_pass_interval_seconds=None,
            grooming_pass_timeout_seconds=None,
            supervisor_pass_interval_seconds=None,
            supervisor_pass_timeout_seconds=None,
        )

    assert runtime.scheduler.passes == ()
    assert runtime.lifecycle is None
    assert _not_configured(logs) == {
        "dispatch": [
            "KODEZART_DISPATCH_PASS_INTERVAL_SECONDS",
            "KODEZART_DISPATCH_PASS_TIMEOUT_SECONDS",
        ],
        HEARTBEAT_PASS: [
            "KODEZART_DISPATCH_PASS_INTERVAL_SECONDS",
            "KODEZART_DISPATCH_PASS_TIMEOUT_SECONDS",
        ],
        "audit": [
            "KODEZART_AUDIT_SWEEP_INTERVAL_SECONDS",
            "KODEZART_AUDIT__TIMEOUT_SECONDS",
        ],
        PromptKey.FIRE_PREP_PASS.value: [
            "KODEZART_FIRE_PREP_PASS_INTERVAL_SECONDS",
            "KODEZART_FIRE_PREP_PASS_TIMEOUT_SECONDS",
        ],
        PromptKey.GROOMING_PASS.value: [
            "KODEZART_GROOMING_PASS_INTERVAL_SECONDS",
            "KODEZART_GROOMING_PASS_TIMEOUT_SECONDS",
        ],
    }


async def test_with_no_cadence_set_a_scope_deployment_schedules_nothing(
    tmp_path: Path,
) -> None:
    """Every pass is named the same way: the scope passes and the session passes.

    A declared scope switches nothing off (2026-09-24): the two session passes
    would run here on their cadence pairs, so unset they are named too, and
    so is the heartbeat, which runs on the dispatch pair.
    """
    with structlog.testing.capture_logs() as logs:
        runtime = await _runtime(
            tmp_path,
            tracker=approving_board(),
            runner=FakeAgentRunner(events=[]),
            operation=standing_scope_operation(),
            organize=ORGANIZE_BOUNDS,
            write_back=STANDING_SCOPE_SETTINGS["write_back"],
            dispatch_pass_interval_seconds=None,
            dispatch_pass_timeout_seconds=None,
            fire_prep_pass_interval_seconds=None,
            fire_prep_pass_timeout_seconds=None,
            grooming_pass_interval_seconds=None,
            grooming_pass_timeout_seconds=None,
            supervisor_pass_interval_seconds=None,
            supervisor_pass_timeout_seconds=None,
        )

    assert runtime.scheduler.passes == ()
    assert set(_not_configured(logs)) == {
        HEARTBEAT_PASS,
        "supervisor",
        "audit",
        PromptKey.FIRE_PREP_PASS.value,
        PromptKey.GROOMING_PASS.value,
    }
    assert _not_configured(logs)[HEARTBEAT_PASS] == [
        "KODEZART_DISPATCH_PASS_INTERVAL_SECONDS",
        "KODEZART_DISPATCH_PASS_TIMEOUT_SECONDS",
    ]


@pytest.mark.parametrize(
    ("interval", "timeout"),
    [
        (
            "KODEZART_DISPATCH_PASS_INTERVAL_SECONDS",
            "KODEZART_DISPATCH_PASS_TIMEOUT_SECONDS",
        ),
        (
            "KODEZART_FIRE_PREP_PASS_INTERVAL_SECONDS",
            "KODEZART_FIRE_PREP_PASS_TIMEOUT_SECONDS",
        ),
        (
            "KODEZART_GROOMING_PASS_INTERVAL_SECONDS",
            "KODEZART_GROOMING_PASS_TIMEOUT_SECONDS",
        ),
        (
            "KODEZART_SUPERVISOR_PASS_INTERVAL_SECONDS",
            "KODEZART_SUPERVISOR_PASS_TIMEOUT_SECONDS",
        ),
        ("KODEZART_AUDIT_SWEEP_INTERVAL_SECONDS", "KODEZART_AUDIT__TIMEOUT_SECONDS"),
    ],
)
@pytest.mark.parametrize("half", ["interval", "timeout"])
def test_one_half_of_a_cadence_refuses_at_load_naming_both(
    monkeypatch: pytest.MonkeyPatch, interval: str, timeout: str, half: str
) -> None:
    """An interval with no timeout, or a timeout with no interval, never boots."""
    monkeypatch.setenv("KODEZART_ORGANIZE__MAX_ADMISSION_ROUNDS", "1")
    monkeypatch.setenv("KODEZART_ORGANIZE__MAX_CONVERGENCE_ROUNDS", "1")
    monkeypatch.setenv(interval if half == "interval" else timeout, "600")
    with pytest.raises(ValidationError) as refused:
        AppConfig(_env_file=None)
    assert f"{interval} and {timeout}" in str(refused.value)


# ---------------------------------------------------------------------------
# KOD-112 R5: the minimal floor boots, and says what it cannot schedule
# ---------------------------------------------------------------------------


async def test_the_minimal_floor_boots_and_names_the_roster_it_lacks(
    tmp_path: Path,
) -> None:
    """The floor is bootable again, and its silence is a log line.

    Every shipped pass template enumerates the declared teams and the
    declared repositories, so a config that declares neither cannot be a
    pass's prompt. That does not make the config wrong — an empty board
    boots, and loading one is a decision this model calls legitimate — it
    makes the passes unwireable, which is a fact the operator reads rather
    than deduces from a schedule that quietly came back empty.
    """
    with structlog.testing.capture_logs() as logs:
        runtime = await _runtime(
            tmp_path,
            tracker=None,
            runner=FakeAgentRunner(events=[]),
            operation=minimal_fixture(),
        )

    assert [entry.name for entry in runtime.scheduler.passes] == []
    (unwired,) = [
        entry for entry in logs if entry["event"] == "prompt_passes_not_wired"
    ]
    assert unwired["operation_config_present"] is True
    assert unwired["absent"] == ["teams", "repos"]


@EITHER_MODE
async def test_the_floor_boots_where_the_same_hole_over_a_roster_refuses(
    tmp_path: Path, debug: bool
) -> None:
    """The boot render still guards every pass that WIRES, and only those.

    Both configs here have the same hole — no ``endpoints`` — and they go
    opposite ways. The floor boots because it wires nothing, so there is no
    prompt for the hole to be in. The rostered one refuses because it wires
    two, which is KOD-150 unweakened.
    """
    holed = raw_example()
    del holed["endpoints"]
    rostered = load_operation_config(write_toml(tmp_path, holed))

    floor = await _runtime(
        tmp_path,
        tracker=None,
        runner=FakeAgentRunner(events=[]),
        operation=minimal_fixture(),
    )

    assert [entry.name for entry in floor.scheduler.passes] == []
    runner = FakeAgentRunner(events=[])
    with pytest.raises(PromptRenderError):
        await _runtime(
            tmp_path,
            tracker=None,
            runner=runner,
            operation=rostered,
            http={"debug": debug},
        )
    assert runner.calls == []


# ---------------------------------------------------------------------------
# KOD-160: a knowledge destination the grant cannot serve
# ---------------------------------------------------------------------------

#: The shipped grant list: no session type is named, so no session reaches
#: the knowledge store.  Written out rather than left to the default,
#: because what these cases turn on is the grant and it must be visible.
UNGRANTED: dict[str, object] = {
    "knowledge": {
        "session_grants": [],
        "connection": {
            "transport": "http",
            "server_url": "https://knowledge.invalid/mcp",
            "credential": None,
        },
    },
}

#: Every surface of the shipped example that lives in the knowledge system,
#: spelled as the refusal spells it.  THREE registries, because a pass reads
#: three: a read-side document, a write-side record, and the map that says
#: what lives where — which the prelude only carries for a GRANTED session
#: type, so a declared map under an ungranted pass is an instruction to
#: consult a map the session never received.  Literal rather than derived
#: from the config: a list derived from the same predicate the production
#: check reads would agree with it however either one drifted.
KNOWLEDGE_ENTRIES = (
    "documents.house_rules",
    "documents.constitution",
    "records.fire_prep",
    "records.grooming",
    "knowledge.house_rules",
    "knowledge.constitution",
    "knowledge.run_logs",
    "knowledge.memories",
    "knowledge.personas",
    "knowledge.notes",
)

#: The one entry of the same registries that lives tracker-side.
TRACKER_ENTRY = "documents.checkpoint"


def _tracker_side(raw: dict[str, object]) -> None:
    """Move every declared destination into the tracker's own system.

    The ``knowledge`` map goes rather than moves: it has no system field to
    change, because being the map of what lives in the knowledge store is
    the whole of what it is.  An operation that keeps everything tracker-
    side declares none.
    """
    for field in ("documents", "records"):
        registry = raw[field]
        assert isinstance(registry, dict)
        for entry in registry.values():
            entry["system"] = DocumentSystem.TRACKER.value
    raw["knowledge"] = {}


def _without_stores(raw: dict[str, object]) -> None:
    """The M1 deployment: a tracker, and no store or record beside it."""
    for field in ("documents", "records", "knowledge"):
        del raw[field]


def _mutated(tmp_path: Path, mutate: Callable[[dict[str, object]], None]) -> Path:
    """The annotated example, mutated, written back as TOML."""
    raw = raw_example()
    mutate(raw)
    return write_toml(tmp_path, raw)


@EITHER_MODE
async def test_a_knowledge_destination_no_pass_can_reach_aborts_boot(
    tmp_path: Path, debug: bool
) -> None:
    """Two halves, legal apart, an instruction to nowhere together.

    The operation names surfaces in the knowledge system and the deployment
    grants that store to no session type, so every tick would tell a pass to
    read and write where its session holds no capability — and the only
    place that can fail is inside the session, where it looks like a pass
    that ran and recorded nothing.  Every affected entry is named at once,
    the ``knowledge`` map's keys among them, and the tracker-side one is not
    among them.
    """
    runner = FakeAgentRunner(events=[])
    with pytest.raises(PassKnowledgeCapabilityError) as caught:
        await _runtime(
            tmp_path,
            tracker=None,
            runner=runner,
            http={"debug": debug},
            **UNGRANTED,
        )
    assert runner.calls == []

    named = str(caught.value)
    for entry in KNOWLEDGE_ENTRIES:
        assert entry in named, entry
    assert len(caught.value.destinations) == len(KNOWLEDGE_ENTRIES)
    assert TRACKER_ENTRY not in named
    assert SessionType.SCHEDULED_PASS.value in named


async def test_the_same_config_boots_with_its_destinations_tracker_side(
    tmp_path: Path,
) -> None:
    """The registries stay populated; only the system they name changes.

    Non-vacuity for the refusal above, and the first-class M1 shape: an
    operation that keeps its checkpoint and its run log on the tracker, and
    declares no map beside it, needs no knowledge grant to wire a pass.

    Rendered from the v5 set, because absence is a three-state render there
    and the frozen ``claude-opus`` prose names its constitution page in
    running text: that set carries the routines byte for byte, so a
    map-less operation is a shape it cannot express, not one it refuses.
    """
    operation = load_operation_config(_mutated(tmp_path, _tracker_side))

    runtime = await _runtime(
        tmp_path,
        tracker=None,
        runner=FakeAgentRunner(events=[]),
        operation=operation,
        prompt_set=V5_SET,
        **UNGRANTED,
    )

    assert operation.documents
    assert operation.records
    assert operation.knowledge == {}
    assert {entry.name for entry in runtime.scheduler.passes} == {
        PromptKey.FIRE_PREP_PASS.value,
        PromptKey.GROOMING_PASS.value,
    }


async def test_the_declared_map_boots_once_the_scheduled_pass_is_granted(
    tmp_path: Path,
) -> None:
    """The third arm: nothing about the map is wrong on its own.

    The same example, unmutated, under a deployment that grants the store
    to the scheduled passes — which is what makes the map the session is
    preluded with a real one. Both passes wire and the refusal above is
    demonstrably about the GRANT rather than about declaring a map.
    """
    operation = example_config()

    runtime = await _runtime(
        tmp_path,
        tracker=None,
        runner=FakeAgentRunner(events=[]),
        operation=operation,
    )

    assert operation.knowledge
    assert {entry.name for entry in runtime.scheduler.passes} == {
        PromptKey.FIRE_PREP_PASS.value,
        PromptKey.GROOMING_PASS.value,
    }


#: The fire's own log, in the knowledge store: the shape the operation
#: took when fires gained a record destination of their own (KOD-170).  A
#: fire's session — not a scheduled pass — is what reaches it.
FIRE_LOG_NAME = "Example Fire Log"


def _fire_record_knowledge_side(raw: dict[str, object]) -> None:
    """Declare the fire's record destination in the knowledge system."""
    registry = raw["records"]
    assert isinstance(registry, dict)
    registry[RunKind.FIRE.value] = {
        "system": DocumentSystem.KNOWLEDGE.value,
        "name": FIRE_LOG_NAME,
        "id": "example-fire-log-destination-id",
        "append_only": True,
    }


@EITHER_MODE
async def test_a_fire_log_the_fires_own_session_cannot_reach_aborts_boot(
    tmp_path: Path, debug: bool
) -> None:
    """KOD-265: the capability question is asked of every session, not one.

    The deployment grants the knowledge store to the scheduled passes and
    the operation declares the FIRE's log in it, so a granted scheduled
    pass answered for a surface it never reads and the fire's own missing
    capability went unchecked — the arm that carries the session's prose
    contribution to the Fire Log.  The refusal names the session type that
    is missing, and names only the surface that session was owed.
    """
    operation = load_operation_config(_mutated(tmp_path, _fire_record_knowledge_side))
    runner = FakeAgentRunner(events=[])

    with pytest.raises(PassKnowledgeCapabilityError) as caught:
        await _runtime(
            tmp_path,
            tracker=None,
            runner=runner,
            operation=operation,
            http={"debug": debug},
        )
    assert runner.calls == []

    named = str(caught.value)
    assert SessionType.TICKET_FIRE.value in named
    assert f"records.{RunKind.FIRE.value} ({FIRE_LOG_NAME})" in named
    # The scheduled pass holds its capability, so not one of ITS surfaces
    # is named: the refusal is about the session that is missing one.
    assert caught.value.destinations == (
        f"records.{RunKind.FIRE.value} ({FIRE_LOG_NAME}) "
        f"→ {SessionType.TICKET_FIRE.value}",
    )


async def test_the_same_operation_boots_once_the_fire_is_granted_too(
    tmp_path: Path,
) -> None:
    """The paired positive: the grant list names the session, and it boots.

    Non-vacuity for the refusal above, and the shipped deployment's own
    shape — ``ticket_fire`` was added to the grants for exactly this row.
    """
    operation = load_operation_config(_mutated(tmp_path, _fire_record_knowledge_side))

    runtime = await _runtime(
        tmp_path,
        tracker=None,
        runner=FakeAgentRunner(events=[]),
        operation=operation,
        knowledge={
            "session_grants": [
                SessionType.SCHEDULED_PASS,
                SessionType.TICKET_FIRE,
            ],
            "connection": {
                "transport": "http",
                "server_url": "https://knowledge.invalid/mcp",
                "credential": KNOWLEDGE_TOKEN,
            },
        },
    )

    assert operation.records[RunKind.FIRE.value].system is DocumentSystem.KNOWLEDGE
    assert {entry.name for entry in runtime.scheduler.passes} == {
        PromptKey.FIRE_PREP_PASS.value,
        PromptKey.GROOMING_PASS.value,
    }


async def test_a_deployment_with_no_store_wires_both_passes_and_records_nothing(
    tmp_path: Path,
) -> None:
    """M1 at the wiring seam: a tracker, an operation, and no store at all.

    The other arm of the refusal — destinations removed rather than moved —
    and the running half of what the three-state render promises.  Both
    passes wire, their boot render succeeds, and the text a tick actually
    sends carries the record-nothing-outside-the-tracker instruction rather
    than a hole where a destination would be.
    """
    operation = load_operation_config(_mutated(tmp_path, _without_stores))
    tracker = FakeTrackerPort(
        issues=[
            make_tracker_issue(
                "FIX-1",
                team_key=operation.team_keys()[0],
                queue_states=[QueueState.TRIAGE],
            ),
        ],
    )
    runner = FakeAgentRunner(events=[])

    runtime = await _runtime(
        tmp_path,
        tracker=tracker,
        runner=runner,
        operation=operation,
        prompt_set=V5_SET,
        **UNGRANTED,
        # Unset: the heartbeat that pair would schedule records nothing.
        dispatch_pass_interval_seconds=None,
        dispatch_pass_timeout_seconds=None,
    )

    assert {entry.name for entry in runtime.scheduler.passes} == {
        PromptKey.FIRE_PREP_PASS.value,
        PromptKey.GROOMING_PASS.value,
    }
    for entry in runtime.scheduler.passes:
        await entry.run(FIXTURE_EPOCH)
    assert len(runner.calls) == len(runtime.scheduler.passes)
    for call in runner.calls:
        prompt = str(call["prompt"])
        assert "{{" not in prompt
        assert "No record destination\nis declared for this pass's kind" in (prompt)
        assert "No store is configured beside the tracker" in prompt
        # No separate checkpoint surface exists (founder ruling 2026-09-01):
        # the window rides the record log, so its absence arm is the window's.
        assert "so no window\ncarries between passes" in prompt


async def test_adding_a_pass_is_a_table_row(tmp_path: Path) -> None:
    """The open-closed claim, executable.

    A third pass needs a prompt key and a cadence pair — and nothing
    structural. The two registered rows prove the shape carries its own key
    and interval rather than either being wired per pass.
    """
    registered, runner = await _registrations(tmp_path)
    metronome = Metronome(limit=0)
    scheduler = PassScheduler(passes=registered, sleep=metronome.sleep)

    await scheduler.start()
    await _settle_requests(metronome, len(registered))
    await scheduler.stop()

    assert len(runner.calls) == len(registered)
    assert [entry.interval_seconds for entry in registered] == [
        FIRE_PREP_INTERVAL,
        GROOMING_INTERVAL,
    ]


def prescribed_title(prompt: str) -> str:
    """The row title the rendered Record clause told the session to use."""
    _, marker, tail = prompt.partition("titled EXACTLY")
    assert marker, "the Record clause prescribes no title"
    return tail.strip().splitlines()[0]


class SessionWrittenLog:
    """The declared destination as it stands after a SESSION wrote its row.

    Rows are held by TITLE alone, because a title is the whole of what a
    destination can be asked about a row somebody else wrote: the runner
    looks its run up by the one string that spells the run's identity, and
    whatever prose the session put under that title is not this
    obligation's business (KOD-290).

    Answered the way both shipped sinks answer — by the record's title at
    the HEAD of a row (``LinearRecordSink`` reads a document line that
    opens with it, ``NotionRecordSink`` asks the title property for a
    ``starts_with``).  The row the clause instructs is one line on the
    document arm, the title and then the prose, and the runner's own line
    opens with the title too; a fake matching the title whole would reject
    the very row the session was told to write.
    """

    def __init__(self, titles: list[str]) -> None:
        self.titles: list[str] = titles

    async def holds_record(
        self,
        *,
        destination: RecordDestination,
        record: RunRecord,
    ) -> bool:
        return any(title.startswith(record.title()) for title in self.titles)

    async def write_record(
        self,
        *,
        destination: RecordDestination,
        record: RunRecord,
    ) -> None:
        self.titles.append(record.title())


class TestTheClauseAndTheRunnerNameOneRow:
    """One declaration, two readers — the session's and the runner's.

    Measured at ``00416e1``: the Record clause prescribed no title at all,
    so once verification became per-run and exact (KOD-288) every
    session-written row was invisible to the runner, which backfilled a
    second row beside it on every pass.  The prompt now carries the exact
    string the runner will look for, rendered from the same run identity.
    """

    KIND = RunKind.FIRE_PREP
    KEY = PromptKey.FIRE_PREP_PASS

    @staticmethod
    async def _prompt_of_one_run(tmp_path: Path) -> str:
        """Run the shipped fire-prep pass once and take the prompt it sent."""
        operation = example_config()
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue(
                    "FIX-1",
                    team_key=operation.team_keys()[0],
                    queue_states=[QueueState.TRIAGE],
                ),
            ],
        )
        runner = FakeAgentRunner(events=[])
        runtime = await _runtime(
            tmp_path,
            tracker=tracker,
            runner=runner,
            prompt_set=V5_SET,
        )
        entry = next(
            item
            for item in runtime.scheduler.passes
            if item.name == PromptKey.FIRE_PREP_PASS.value
        )
        await entry.run(FIXTURE_EPOCH)
        return str(runner.calls[0]["prompt"])

    @staticmethod
    def _recorder(sink: SessionWrittenLog) -> RunRecorder:
        records = example_config().records
        return RunRecorder(
            records=records,
            sinks={records["fire_prep"].system: sink},
        )

    def _record(self) -> RunRecord:
        return RunRecord(
            kind=self.KIND,
            name=self.KEY.value,
            outcome=RunOutcome.COMPLETED,
            duration_seconds=1.0,
            started_at=FIXTURE_EPOCH,
            recorded_at=FIXTURE_EPOCH,
        )

    async def test_the_clause_prescribes_the_title_the_runner_verifies_by(
        self,
        tmp_path: Path,
    ) -> None:
        prompt = await self._prompt_of_one_run(tmp_path)

        assert prescribed_title(prompt) == self._record().title()

    async def test_a_row_titled_as_prescribed_is_verified_and_not_backfilled(
        self,
        tmp_path: Path,
    ) -> None:
        prompt = await self._prompt_of_one_run(tmp_path)
        log = SessionWrittenLog([prescribed_title(prompt)])

        result = await self._recorder(log).record(self._record())

        assert result is RunRecordResult.VERIFIED
        assert len(log.titles) == 1, "the runner wrote no second row"

    async def test_the_row_the_clause_instructs_is_verified_with_its_prose(
        self,
        tmp_path: Path,
    ) -> None:
        """The row as the clause has the session write it: the title, then
        what it examined and changed, on the one line the document arm
        holds a row as — verified, because the title heads it."""
        prompt = await self._prompt_of_one_run(tmp_path)
        row = f"{prescribed_title(prompt)} — examined 3 issues, staged 1"
        log = SessionWrittenLog([row])

        result = await self._recorder(log).record(self._record())

        assert result is RunRecordResult.VERIFIED
        assert log.titles == [row], "the runner wrote no second row"

    async def test_a_row_titled_any_other_way_leaves_the_run_unrecorded(
        self,
        tmp_path: Path,
    ) -> None:
        """The paired negative, and the shape the measured defect produced.

        Before the clause carried a title the session spelled the run's
        identity its own way — the same kind, name and instant, in words
        the runner does not read — so it had written about this run and
        said so nowhere the runner looks, and the runner backfilled: two
        rows for one run, on every live pass.
        """
        prompt = await self._prompt_of_one_run(tmp_path)
        # The same instant spelled the session's own way: a space for the T.
        other = prescribed_title(prompt).replace("T", " ", 1)
        assert other != prescribed_title(prompt)
        log = SessionWrittenLog([other])

        result = await self._recorder(log).record(self._record())

        assert result is RunRecordResult.WRITTEN
        assert log.titles == [other, self._record().title()]


async def test_the_intake_passes_tick_at_boot_and_the_dispatcher_does_not(
    tmp_path: Path,
) -> None:
    """Fire prep and grooming run when the process comes up (owner, 2026-09-24)."""
    runtime = await _runtime(
        tmp_path,
        tracker=FakeTrackerPort(),
        runner=FakeAgentRunner(events=[]),
        github_api=FakeDeliveryProbe(),
    )
    at_boot = {entry.name: entry.tick_at_boot for entry in runtime.scheduler.passes}
    assert at_boot[PromptKey.FIRE_PREP_PASS.value] is True
    assert at_boot[PromptKey.GROOMING_PASS.value] is True
    assert all(
        flag is False
        for name, flag in at_boot.items()
        if name.startswith(f"{_DISPATCH_NAME}:")
    )
    assert any(name.startswith(f"{_DISPATCH_NAME}:") for name in at_boot)
