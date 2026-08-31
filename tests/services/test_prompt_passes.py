"""The registrations: each pass, on its own cadence, with its own prompt.

Driven through the real ``PassScheduler`` over a substituted clock, so what
is asserted is what a tick actually sends — not what a builder returned.
The prompts are compared against the registry's own renders rather than
against any string written here: a literal in the assertion would pass
against a literal in the code, which is the one thing this has to catch.
"""

import ast
from pathlib import Path

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.passes import (
    DispatchRuntime,
    build_dispatch_runtime,
    build_prompt_passes,
)
from kodezart.core.config import AppConfig
from kodezart.core.errors import PassGateCapabilityError, PromptRenderError
from kodezart.core.logging import get_logger
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.services.pass_scheduler import PassScheduler, ScheduledPass
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.operation import QueueState
from kodezart.types.domain.prompts import PromptKey
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
    FakeJobQueue,
    FakeTrackerPort,
    make_tracker_issue,
)
from tests.prompts.test_operation_config import raw_example, write_toml
from tests.prompts.test_prompt_wiring import load_registry
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


def _config(tmp_path: Path, **overrides: object) -> AppConfig:
    return AppConfig(
        fire_prep_pass_interval_seconds=FIRE_PREP_INTERVAL,
        fire_prep_pass_timeout_seconds=FIRE_PREP_TIMEOUT,
        grooming_pass_interval_seconds=GROOMING_INTERVAL,
        grooming_pass_timeout_seconds=GROOMING_TIMEOUT,
        scheduled_pass_working_dir=str(tmp_path / "pass"),
        **overrides,  # type: ignore[arg-type]
    )


def _registrations(
    tmp_path: Path,
    *,
    tracker: FakeTrackerPort | None = None,
    **overrides: object,
) -> tuple[list[ScheduledPass], FakeAgentRunner]:
    """The passes exactly as the composition registers them."""
    operation = example_config()
    prompts = load_registry(bindings=dict(bindings_for(operation)))
    runner = FakeAgentRunner(events=[])
    return (
        build_prompt_passes(
            config=_config(tmp_path, **overrides),
            operation=operation,
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
    **overrides: object,
) -> DispatchRuntime:
    """Boot the scheduled-pass runtime exactly as the composition root does."""
    operation = example_config()
    queue = FakeJobQueue()
    return await build_dispatch_runtime(
        config=_config(tmp_path, **overrides),
        operation=operation,
        tracker=tracker,
        github_api=None,
        queue=queue,
        registry=queue,
        gate=None,
        git=None,  # type: ignore[arg-type]
        cache=None,  # type: ignore[arg-type]
        prompts=load_registry(bindings=dict(bindings_for(operation))),
        runner=runner,
        skills=SUPPRESS_ALL_SKILLS,
        log=get_logger(__name__),
    )


async def test_each_pass_sends_its_own_rendered_prompt_on_its_own_cadence(
    tmp_path: Path,
) -> None:
    """One tick each: two sessions, two prompts, two configured intervals."""
    registered, runner = _registrations(tmp_path)
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


def test_the_registrations_take_every_cadence_from_configuration(
    tmp_path: Path,
) -> None:
    """Each pass carries the interval its own knob holds, never a shared one."""
    registered, _ = _registrations(tmp_path)

    assert [(entry.name, entry.interval_seconds) for entry in registered] == [
        (PromptKey.FIRE_PREP_PASS.value, FIRE_PREP_INTERVAL),
        (PromptKey.GROOMING_PASS.value, GROOMING_INTERVAL),
    ]


def test_the_registrations_take_every_budget_from_configuration(
    tmp_path: Path,
) -> None:
    """Each pass carries the timeout its own knob holds, never its cadence."""
    registered, _ = _registrations(tmp_path)

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
    registered, runner = _registrations(tmp_path, tracker=tracker)
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
    gated, gated_runner = _registrations(
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
    registered, runner = _registrations(tmp_path, tracker=None)
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


def test_a_pass_whose_prompt_has_a_hole_refuses_at_wiring(tmp_path: Path) -> None:
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
        build_prompt_passes(
            config=_config(tmp_path),
            operation=operation,
            prompts=load_registry(bindings=dict(bindings_for(operation))),
            tracker=None,
            runner=FakeAgentRunner(events=[]),
            skills=SUPPRESS_ALL_SKILLS,
        )

    assert PromptKey.FIRE_PREP_PASS.value in str(caught.value)
    assert "endpoints.host_runner" in caught.value.missing
    assert "endpoints.host_runner" in str(caught.value)


def test_the_shipped_example_wires_without_a_render_refusal(tmp_path: Path) -> None:
    """Non-vacuity: the refusal above is the config's, not the check's."""
    assert len(_registrations(tmp_path)[0]) == len(
        (PromptKey.FIRE_PREP_PASS, PromptKey.GROOMING_PASS)
    )


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
    registered, runner = _registrations(
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
