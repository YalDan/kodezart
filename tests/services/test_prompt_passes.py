"""The registrations: each pass, on its own cadence, with its own prompt.

Driven through the real ``PassScheduler`` over a substituted clock, so what
is asserted is what a tick actually sends — not what a builder returned.
The prompts are compared against the registry's own renders rather than
against any string written here: a literal in the assertion would pass
against a literal in the code, which is the one thing this has to catch.
"""

import ast
from pathlib import Path

from kodezart.composition.passes import (
    build_dispatch_runtime,
    build_prompt_passes,
)
from kodezart.core.config import AppConfig
from kodezart.core.logging import get_logger
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.services.pass_scheduler import PassScheduler, ScheduledPass
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.prompts import PromptKey
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
    FakeJobQueue,
    FakeTrackerPort,
    make_tracker_issue,
)
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


def _config(tmp_path: Path, **overrides: object) -> AppConfig:
    return AppConfig(
        fire_prep_pass_interval_seconds=FIRE_PREP_INTERVAL,
        grooming_pass_interval_seconds=GROOMING_INTERVAL,
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
            prompts=prompts,
            tracker=tracker,
            runner=runner,
            skills=SUPPRESS_ALL_SKILLS,
        ),
        runner,
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
    await _settle(metronome)
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
    """Fire-prep ships gated on its three streams; grooming ships ungated.

    Asserted through a tick rather than by reading the wiring: what
    matters is that a quiet board skips one pass and still runs the other.
    """
    tracker = FakeTrackerPort()
    registered, runner = _registrations(tmp_path, tracker=tracker)
    metronome = Metronome(limit=len(registered))
    scheduler = PassScheduler(passes=registered, sleep=metronome.sleep)

    await scheduler.start()
    await _settle(metronome)
    await scheduler.stop()

    prompts = load_registry(bindings=dict(bindings_for(example_config())))
    assert [call["prompt"] for call in runner.calls] == [
        prompts.template_for(PromptKey.GROOMING_PASS).render({}),
    ], "grooming verifies the tree, which is work even when nothing changed"
    # Three port calls and no session: fire-prep asked its three questions,
    # got nothing, and never opened one.
    assert len(tracker.scans) + len(tracker.review_scans) == len(
        AppConfig().fire_prep_pass_gate_signals,
    )


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
    await _settle(metronome)
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
    await _settle(metronome)
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
    operation = example_config()
    prompts = load_registry(bindings=dict(bindings_for(operation)))
    queue = FakeJobQueue()
    runtime = await build_dispatch_runtime(
        config=_config(tmp_path),
        operation=operation,
        tracker=None,
        github_api=None,
        queue=queue,
        registry=queue,
        gate=None,
        git=None,  # type: ignore[arg-type]
        cache=None,  # type: ignore[arg-type]
        prompts=prompts,
        runner=FakeAgentRunner(events=[]),
        skills=SUPPRESS_ALL_SKILLS,
        log=get_logger(__name__),
    )

    assert {entry.name for entry in runtime.scheduler.passes} == {
        PromptKey.FIRE_PREP_PASS.value,
        PromptKey.GROOMING_PASS.value,
    }
    assert runtime.lifecycle is None


async def test_adding_a_pass_is_a_table_row(tmp_path: Path) -> None:
    """The open-closed claim, executable.

    A third pass needs a prompt key, an interval and a signal list — and
    nothing structural. Standing in for the third row with a re-pointed
    existing one proves the shape carries its own key, interval and gate
    rather than any of the three being wired per pass.
    """
    tracker = FakeTrackerPort(issues=[make_tracker_issue("FIX-1")])
    registered, runner = _registrations(
        tmp_path,
        tracker=tracker,
        fire_prep_pass_gate_signals=[PassSignal.approved_changed],
        grooming_pass_gate_signals=[PassSignal.approved_changed],
    )
    metronome = Metronome(limit=len(registered))
    scheduler = PassScheduler(passes=registered, sleep=metronome.sleep)

    await scheduler.start()
    await _settle(metronome)
    await scheduler.stop()

    assert len(runner.calls) == len(registered)
    assert [entry.interval_seconds for entry in registered] == [
        FIRE_PREP_INTERVAL,
        GROOMING_INTERVAL,
    ]
