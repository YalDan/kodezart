"""One pass, sent as one session: the render path, the gate, and the send.

The grooming render is asserted against the shipped template and the
shipped example operation config — the same artifacts the fire-prep render
is asserted against — because a render path tested against a fixture body
proves nothing about the prompt the deployment would actually send.

The four render-and-send behaviours here are the ones the deleted
per-pass session class carried, with their subject swapped for the single
run callable.  They are unchanged in what they assert: collapsing two
render paths into one must not quietly relax what either proved.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.errors import PromptRenderError
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.core.protocols import PromptSetProvider
from kodezart.services.pass_gate import PassGate
from kodezart.services.prompt_pass import run_prompt_pass
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.operation import OperationConfig, QueueState
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsMode, SkillsSelection
from tests.fakes import (
    FIXTURE_EPOCH,
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
    FakeTrackerPort,
    make_tracker_issue,
    make_tracker_review,
)
from tests.prompts.test_prompt_wiring import load_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "docs" / "operation.example.toml"

WORKSPACE = "/tmp/kodezart-scheduled-pass"
PERMISSION_MODE = "bypassPermissions"
PAGE_SIZE = 50
LATER = FIXTURE_EPOCH + timedelta(hours=1)
#: The shipped set that declares a session role covering both pass keys.
POLICIED_SET = "anthropic_v5"
ALL_SKILLS = SkillsSelection(mode=SkillsMode.ALL)


def example_config() -> OperationConfig:
    return load_operation_config(EXAMPLE)


def bound_registry() -> PromptSetProvider:
    return load_registry(bindings=dict(bindings_for(example_config())))


def policied_registry() -> PromptSetProvider:
    """A bound registry over the set that declares roles for the pass keys."""
    return load_registry(
        default_set=POLICIED_SET,
        bindings=dict(bindings_for(example_config())),
    )


def pass_gate(tracker: FakeTrackerPort, *signals: PassSignal) -> PassGate:
    """A gate scoped the way the composition scopes a prompt pass's.

    Every declared board and every declared repository, because a prompt
    pass acts on the whole operation — the narrowing to one repository
    belongs to the dispatch pass and to nothing else.
    """
    operation = example_config()
    return PassGate(
        tracker=tracker,
        signals=list(signals),
        team_keys=operation.team_keys(),
        repo_urls=[repo.url for repo in operation.repos],
        page_size=PAGE_SIZE,
    )


async def run(
    *,
    prompts: PromptSetProvider,
    runner: FakeAgentRunner,
    gate: PassGate | None = None,
    key: PromptKey = PromptKey.GROOMING_PASS,
    skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
) -> None:
    await run_prompt_pass(
        key=key,
        prompts=prompts,
        runner=runner,
        gate=gate,
        workspace_path=WORKSPACE,
        permission_mode=PERMISSION_MODE,
        allowed_tools=["Bash"],
        skills=skills,
        session_type=SessionType.SCHEDULED_PASS,
    )


def test_the_grooming_prompt_composes_through_the_registry() -> None:
    """The mirror of the fire-prep render: template plus operation config."""
    config = example_config()
    rendered = bound_registry().template_for(PromptKey.GROOMING_PASS).render({})

    assert rendered
    assert "{{" not in rendered
    assert config.operation_name in rendered


def test_an_unbound_placeholder_is_a_typed_refusal_not_a_prompt() -> None:
    """No config value, no prompt, and the placeholder is named."""
    with pytest.raises(PromptRenderError) as excinfo:
        load_registry().template_for(PromptKey.GROOMING_PASS).render({})

    assert "operation_name" in excinfo.value.missing


async def test_the_session_receives_the_rendered_prompt_and_its_grant() -> None:
    """What reaches the query path is what the registry rendered."""
    registry = bound_registry()
    rendered = registry.template_for(PromptKey.GROOMING_PASS).render({})
    runner = FakeAgentRunner(events=[])

    await run(prompts=registry, runner=runner)

    assert runner.calls == [
        {
            "method": "stream_in_workspace",
            "prompt": rendered,
            "workspace_path": WORKSPACE,
            "session_id": None,
            "session_type": SessionType.SCHEDULED_PASS,
            "skills": SUPPRESS_ALL_SKILLS,
            "session_policy": registry.session_policy(PromptKey.GROOMING_PASS),
        },
    ]


@pytest.mark.parametrize(
    "key",
    [PromptKey.FIRE_PREP_PASS, PromptKey.GROOMING_PASS],
)
async def test_a_pass_runs_under_the_policy_its_own_set_declares(
    key: PromptKey,
) -> None:
    """The set states what a pass costs, and the pass is sent at that.

    A scheduled pass is a dispatch like any other: its set declares the
    role its key runs under, and the effort and the skill loadout of
    that role are what reach the session.  The pass used to send the
    deployment-wide selection with no policy at all, so the two most
    expensive judgment sessions the deployment runs unattended were the
    only ones the set could not dial.
    """
    registry = policied_registry()
    runner = FakeAgentRunner(events=[])

    await run(prompts=registry, runner=runner, key=key, skills=ALL_SKILLS)

    (call,) = runner.calls
    assert call["session_policy"] == registry.session_policy(key)
    assert call["session_policy"].effort == "xhigh"
    assert call["skills"] == registry.session_skills(key, ALL_SKILLS)
    assert call["skills"] != ALL_SKILLS


async def test_a_deployment_suppression_is_not_reopened_by_the_set() -> None:
    """Narrowing intersects two bounds; it never widens the operator's one."""
    registry = policied_registry()
    runner = FakeAgentRunner(events=[])

    await run(prompts=registry, runner=runner, skills=SUPPRESS_ALL_SKILLS)

    (call,) = runner.calls
    assert call["skills"] == SUPPRESS_ALL_SKILLS


async def test_a_prompt_that_cannot_render_starts_no_session() -> None:
    """Fail loudly rather than send a hole: the failure precedes the send."""
    runner = FakeAgentRunner(events=[])

    with pytest.raises(PromptRenderError):
        await run(prompts=load_registry(), runner=runner)

    assert runner.calls == []


async def test_each_pass_sends_its_own_prompt_and_never_the_other_one() -> None:
    """The late-binding trap, asserted rather than reviewed for.

    Binding the key through a closure over a loop variable would hand
    every pass the LAST key, so one prompt would silently never be sent.
    """
    registry = bound_registry()
    runner = FakeAgentRunner(events=[])

    await run(prompts=registry, runner=runner, key=PromptKey.FIRE_PREP_PASS)
    await run(prompts=registry, runner=runner, key=PromptKey.GROOMING_PASS)

    sent = [call["prompt"] for call in runner.calls]
    assert len(sent) == 2
    assert sent[0] != sent[1]
    assert sent[0] == registry.template_for(PromptKey.FIRE_PREP_PASS).render({})
    assert sent[1] == registry.template_for(PromptKey.GROOMING_PASS).render({})


async def test_an_ungated_pass_asks_nothing_and_always_runs() -> None:
    """Ungated is the cheapest path, not a degraded one: zero queries."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue("FIX-1")])
    runner = FakeAgentRunner(events=[])

    await run(prompts=bound_registry(), runner=runner, gate=None)

    assert len(runner.calls) == 1
    assert tracker.scans == []
    assert tracker.review_scans == []


async def test_a_quiet_gate_opens_no_session_and_renders_nothing() -> None:
    """The token claim, asserted: a quiet board pays for neither."""
    tracker = FakeTrackerPort()
    runner = FakeAgentRunner(events=[])
    signals = (PassSignal.issues_changed, PassSignal.triage_backlog)
    gate = pass_gate(tracker, *signals)

    # An unbound registry would raise on render. It does not, which is how
    # this asserts the render never happened rather than merely that no
    # session opened after one.
    await run(prompts=load_registry(), runner=runner, gate=gate)

    assert runner.calls == []
    # One query per signal per declared board: the questions are asked
    # WITHIN a container, never once over the whole workspace.
    assert len(tracker.scans) == len(signals) * len(example_config().team_keys())


async def test_one_signal_reporting_work_is_enough_to_run_the_pass() -> None:
    """A review with no issue activity still wakes the pass."""
    tracker = FakeTrackerPort()
    repo_url = example_config().repos[0].url
    tracker.reviews[repo_url] = [make_tracker_review("acme/repo#7", updated_at=LATER)]
    runner = FakeAgentRunner(events=[])
    gate = pass_gate(
        tracker,
        PassSignal.issues_changed,
        PassSignal.triage_backlog,
        PassSignal.reviews_changed,
    )

    await run(prompts=bound_registry(), runner=runner, gate=gate)

    assert len(runner.calls) == 1


async def test_a_standing_backlog_wakes_the_pass_on_an_otherwise_quiet_board() -> None:
    """Nothing moved, and there is still a whole backlog to sweep."""
    tracker = FakeTrackerPort(
        issues=[
            make_tracker_issue(
                "FIX-1",
                team_key=example_config().team_keys()[0],
                queue_states=[QueueState.TRIAGE],
            ),
        ],
    )
    runner = FakeAgentRunner(events=[])
    gate = pass_gate(tracker, PassSignal.triage_backlog)

    await run(prompts=bound_registry(), runner=runner, gate=gate)
    await run(prompts=bound_registry(), runner=runner, gate=gate)

    assert len(runner.calls) == 2, "a backlog does not drain by being swept once"
