"""The registrations: each pass, on its own cadence, with its own prompt.

Driven through the real ``PassScheduler`` over a substituted clock, so what
is asserted is what a tick actually sends — not what a builder returned.
The prompts are compared against the registry's own renders rather than
against any string written here: a literal in the assertion would pass
against a literal in the code, which is the one thing this has to catch.
"""

import ast
from collections.abc import Callable
from pathlib import Path

import pytest
import structlog.testing

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.passes import (
    DispatchRuntime,
    build_dispatch_runtime,
    build_prompt_passes,
    verify_pass_preflight,
)
from kodezart.core.config import AppConfig
from kodezart.core.errors import (
    PassGateCapabilityError,
    PassKnowledgeCapabilityError,
    PromptRenderError,
)
from kodezart.core.logging import get_logger
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.services.pass_scheduler import PassScheduler, ScheduledPass
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.operation import (
    DocumentSystem,
    OperationConfig,
    QueueState,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
    FakeJobQueue,
    FakeTrackerPort,
    make_tracker_issue,
)
from tests.prompts.test_claude_opus_goldens import V5_SET
from tests.prompts.test_minimal_floor import minimal_fixture
from tests.prompts.test_operation_config import raw_example, write_toml
from tests.prompts.test_prompt_wiring import DEFAULT_SET, load_registry
from tests.services.test_pass_scheduler import Metronome, _settle
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


#: The shipped example declares documents and records in the knowledge
#: system, so a deployment wiring its passes must grant them that store —
#: and a grant carries a credential.  Both are overridable, because the
#: mismatch between the two halves is itself one of the cases below.
KNOWLEDGE_TOKEN = "knowledge-credential"


def _config(tmp_path: Path, **overrides: object) -> AppConfig:
    settings: dict[str, object] = {
        "fire_prep_pass_interval_seconds": FIRE_PREP_INTERVAL,
        "fire_prep_pass_timeout_seconds": FIRE_PREP_TIMEOUT,
        "grooming_pass_interval_seconds": GROOMING_INTERVAL,
        "grooming_pass_timeout_seconds": GROOMING_TIMEOUT,
        "scheduled_pass_working_dir": str(tmp_path / "pass"),
        "knowledge_session_grants": [SessionType.SCHEDULED_PASS],
        "knowledge_mcp_token": KNOWLEDGE_TOKEN,
    }
    settings.update(overrides)
    return AppConfig(**settings)  # type: ignore[arg-type]


async def _registrations(
    tmp_path: Path,
    *,
    tracker: FakeTrackerPort | None = None,
    operation: OperationConfig | None = None,
    **overrides: object,
) -> tuple[list[ScheduledPass], FakeAgentRunner]:
    """The passes exactly as the composition registers them."""
    declared = example_config() if operation is None else operation
    prompts = load_registry(bindings=dict(bindings_for(declared)))
    runner = FakeAgentRunner(events=[])
    return (
        await build_prompt_passes(
            recorder=RunRecorder(records={}, sinks={}),
            config=_config(tmp_path, **overrides),
            operation=declared,
            prompts=prompts,
            tracker=tracker,
            runner=runner,
            skills=SUPPRESS_ALL_SKILLS,
        ),
        runner,
    )


#: The vendor's own words when a credential holds no scope for a scan.
DIAGNOSIS = "auth_insufficient_scope: this credential cannot read those"


async def _runtime(
    tmp_path: Path,
    *,
    tracker: FakeTrackerPort | None,
    runner: FakeAgentRunner,
    operation: OperationConfig | None = None,
    prompt_set: str = DEFAULT_SET,
    **overrides: object,
) -> DispatchRuntime:
    """Boot the scheduled-pass runtime exactly as the composition root does.

    Preflight FIRST and then the wiring, in that order and as two calls,
    because that is what the root does: every refusal the passes can raise
    is settled before anything stateful is built, and the builder below
    re-checks none of it.
    """
    declared = example_config() if operation is None else operation
    config = _config(tmp_path, **overrides)
    prompts = load_registry(
        default_set=prompt_set,
        bindings=dict(bindings_for(declared)),
    )
    queue = FakeJobQueue()
    await verify_pass_preflight(
        config=config,
        operation=declared,
        tracker=tracker,
        github_api=None,
        prompts=prompts,
    )
    return await build_dispatch_runtime(
        recorder=RunRecorder(records={}, sinks={}),
        config=config,
        operation=declared,
        tracker=tracker,
        github_api=None,
        queue=queue,
        registry=queue,
        gate=None,
        git=None,  # type: ignore[arg-type]
        cache=None,  # type: ignore[arg-type]
        prompts=prompts,
        runner=runner,
        skills=SUPPRESS_ALL_SKILLS,
        log=get_logger(__name__),
    )


async def test_each_pass_sends_its_own_rendered_prompt_on_its_own_cadence(
    tmp_path: Path,
) -> None:
    """One tick each: two sessions, two prompts, two configured intervals."""
    registered, runner = await _registrations(tmp_path)
    prompts = load_registry(bindings=dict(bindings_for(example_config())))
    metronome = Metronome(limit=len(registered))
    scheduler = PassScheduler(passes=registered, sleep=metronome.sleep)

    await scheduler.start()
    await _settle(metronome.parked)
    await scheduler.stop()

    assert set(metronome.requested) == {FIRE_PREP_INTERVAL, GROOMING_INTERVAL}
    assert {call["prompt"] for call in runner.calls} == {
        prompts.template_for(PromptKey.FIRE_PREP_PASS).render({}),
        prompts.template_for(PromptKey.GROOMING_PASS).render({}),
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


async def test_gating_is_per_pass_configuration_and_the_defaults_differ(
    tmp_path: Path,
) -> None:
    """Fire-prep ships gated on two of its streams; grooming ships ungated.

    Asserted through a tick rather than by reading the wiring: what
    matters is that a quiet board skips one pass and still runs the other.
    """
    tracker = FakeTrackerPort()
    registered, runner = await _registrations(tmp_path, tracker=tracker)
    metronome = Metronome(limit=len(registered))
    scheduler = PassScheduler(passes=registered, sleep=metronome.sleep)

    await scheduler.start()
    await _settle(metronome.parked)
    await scheduler.stop()

    prompts = load_registry(bindings=dict(bindings_for(example_config())))
    assert [call["prompt"] for call in runner.calls] == [
        prompts.template_for(PromptKey.GROOMING_PASS).render({}),
    ], "grooming verifies the tree, which is work even when nothing changed"
    # Port calls and no session: fire-prep asked its questions, got
    # nothing, and never opened one. Every one of them named a board, and
    # none of them was a review scan — the shipped set carries neither an
    # unscoped question nor the review signal.
    assert tracker.scans, "fire-prep consulted its gate rather than skipping it"
    assert tracker.review_scans == []
    assert [query for query in tracker.scans if query.team_key is None] == []


async def test_an_operator_can_gate_or_ungate_any_pass(tmp_path: Path) -> None:
    """The knob is real in both directions, over the same quiet board."""
    quiet = FakeTrackerPort()
    gated, gated_runner = await _registrations(
        tmp_path,
        tracker=quiet,
        grooming_pass_gate_signals=[PassSignal.issues_changed],
        fire_prep_pass_gate_signals=[],
    )
    metronome = Metronome(limit=len(gated))
    scheduler = PassScheduler(passes=gated, sleep=metronome.sleep)

    await scheduler.start()
    await _settle(metronome.parked)
    await scheduler.stop()

    prompts = load_registry(bindings=dict(bindings_for(example_config())))
    assert [call["prompt"] for call in gated_runner.calls] == [
        prompts.template_for(PromptKey.FIRE_PREP_PASS).render({}),
    ], "the defaults are a default, not the behaviour"


async def test_a_declared_signal_with_no_tracker_runs_the_pass_ungated(
    tmp_path: Path,
) -> None:
    """Absent gate and quiet gate are different states, never conflated."""
    registered, runner = await _registrations(tmp_path, tracker=None)
    metronome = Metronome(limit=len(registered))
    scheduler = PassScheduler(passes=registered, sleep=metronome.sleep)

    await scheduler.start()
    await _settle(metronome.parked)
    await scheduler.stop()

    assert len(runner.calls) == len(registered), (
        "no port to ask means ungated, never silently switched off"
    )


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


async def test_a_signal_the_credential_cannot_scan_for_aborts_boot(
    tmp_path: Path,
) -> None:
    """KOD-151: the silent failure, made the loudest thing a deployment has.

    A gate whose scan the credential is not scoped for answers "nothing
    moved" every tick, which is exactly what a quiet board answers. The
    pass it guards never runs again and nothing says so — so boot asks
    first, and dies naming the signal, the pass and the vendor's reason.
    """
    tracker = FakeTrackerPort(scan_refusals={PassSignal.reviews_changed: DIAGNOSIS})

    with pytest.raises(PassGateCapabilityError) as caught:
        await _runtime(
            tmp_path,
            tracker=tracker,
            runner=FakeAgentRunner(events=[]),
            fire_prep_pass_gate_signals=[
                PassSignal.issues_changed,
                PassSignal.reviews_changed,
            ],
        )

    named = str(caught.value)
    assert PassSignal.reviews_changed.value in named
    assert PromptKey.FIRE_PREP_PASS.value in named
    assert DIAGNOSIS in named
    assert PassSignal.issues_changed.value not in named, (
        "a signal the credential can answer is not part of the refusal"
    )


async def test_the_shipped_defaults_boot_and_then_run(tmp_path: Path) -> None:
    """The other arm, end to end: what ships boots, and the pass it wired works.

    Nothing here restates the defaults. Boot probes exactly the signals the
    shipped configuration carries, the credential answers, and the gate the
    composition built — containers and all — wakes fire-prep on a board
    with a standing triage backlog.
    """
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

    runtime = await _runtime(tmp_path, tracker=tracker, runner=runner)

    assert tracker.capability_probes == [
        tuple(AppConfig().fire_prep_pass_gate_signals),
    ]
    fire_prep = next(
        entry
        for entry in runtime.scheduler.passes
        if entry.name == PromptKey.FIRE_PREP_PASS.value
    )
    await fire_prep.run()

    assert len(runner.calls) == 1


async def test_a_pass_whose_prompt_has_a_hole_refuses_at_preflight(
    tmp_path: Path,
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
            config=_config(tmp_path),
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


async def test_the_floor_boots_where_the_same_hole_over_a_roster_refuses(
    tmp_path: Path,
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
    with pytest.raises(PromptRenderError):
        await _runtime(
            tmp_path,
            tracker=None,
            runner=FakeAgentRunner(events=[]),
            operation=rostered,
        )


# ---------------------------------------------------------------------------
# KOD-160: a knowledge destination the grant cannot serve
# ---------------------------------------------------------------------------

#: The shipped grant list: no session type is named, so no session reaches
#: the knowledge store.  Written out rather than left to the default,
#: because what these cases turn on is the grant and it must be visible.
UNGRANTED: dict[str, object] = {
    "knowledge_session_grants": [],
    "knowledge_mcp_token": None,
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


async def test_a_knowledge_destination_no_pass_can_reach_aborts_boot(
    tmp_path: Path,
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
    with pytest.raises(PassKnowledgeCapabilityError) as caught:
        await _runtime(
            tmp_path,
            tracker=None,
            runner=FakeAgentRunner(events=[]),
            **UNGRANTED,
        )

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
    )

    assert {entry.name for entry in runtime.scheduler.passes} == {
        PromptKey.FIRE_PREP_PASS.value,
        PromptKey.GROOMING_PASS.value,
    }
    for entry in runtime.scheduler.passes:
        await entry.run()
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

    A third pass needs a prompt key, an interval and a signal list — and
    nothing structural. Standing in for the third row with a re-pointed
    existing one proves the shape carries its own key, interval and gate
    rather than any of the three being wired per pass.
    """
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue("FIX-1", team_key=example_config().team_keys()[0])],
    )
    registered, runner = await _registrations(
        tmp_path,
        tracker=tracker,
        fire_prep_pass_gate_signals=[PassSignal.approved_changed],
        grooming_pass_gate_signals=[PassSignal.approved_changed],
    )
    metronome = Metronome(limit=len(registered))
    scheduler = PassScheduler(passes=registered, sleep=metronome.sleep)

    await scheduler.start()
    await _settle(metronome.parked)
    await scheduler.stop()

    assert len(runner.calls) == len(registered)
    assert [entry.interval_seconds for entry in registered] == [
        FIRE_PREP_INTERVAL,
        GROOMING_INTERVAL,
    ]
