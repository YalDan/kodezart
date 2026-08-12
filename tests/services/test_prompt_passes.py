"""The two registrations: each pass, on its own cadence, with its own prompt.

Driven through the real ``PassScheduler`` over a substituted clock, so what
is asserted is what a tick actually sends — not what a builder returned.
The prompts are compared against the registry's own renders rather than
against any string written here: a literal in the assertion would pass
against a literal in the code, which is the one thing this has to catch.
"""

import ast
from pathlib import Path

from kodezart.composition.passes import build_fire_prep_pass, build_prompt_passes
from kodezart.core.config import AppConfig
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.services.pass_scheduler import PassScheduler, ScheduledPass
from kodezart.types.domain.prompts import PromptKey
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeAgentRunner
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_pass_scheduler import Metronome, _settle
from tests.services.test_pass_session import example_config

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


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        fire_prep_pass_interval_seconds=FIRE_PREP_INTERVAL,
        grooming_pass_interval_seconds=GROOMING_INTERVAL,
        scheduled_pass_working_dir=str(tmp_path / "pass"),
    )


def _registrations(tmp_path: Path) -> tuple[list[ScheduledPass], FakeAgentRunner]:
    """The passes exactly as the composition registers them."""
    operation = example_config()
    prompts = load_registry(bindings=dict(bindings_for(operation)))
    config = _config(tmp_path)
    runner = FakeAgentRunner(events=[])
    return (
        build_prompt_passes(
            config=config,
            prompts=prompts,
            fire_prep=build_fire_prep_pass(
                config=config,
                operation=operation,
                prompts=prompts,
            ),
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
        ("fire_prep", FIRE_PREP_INTERVAL),
        ("grooming", GROOMING_INTERVAL),
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
