"""One pass, sent as one session: the render path, the gate, and the send.

The grooming render is asserted against the shipped template and the
shipped example operation config — the same artifacts the fire-prep render
is asserted against — because a render path tested against a fixture body
proves nothing about the prompt the deployment would actually send.

The four render-and-send behaviours here are the ones the deleted
per-pass session class carried, with their subject swapped for the single
pass object.  They are unchanged in what they assert: collapsing two
render paths into one must not quietly relax what either proved.

The gate group drives one pass object over several ticks: the first tick
runs unasked, every later tick asks the gate question over the window
since the last tick that ran, and what the answer does to the pass — skip,
run, or run because the answer could not be read — is asserted through
the calls the runner recorded and the events the pass logged.

The last group reads what the pass REPORTED.  A pass that closed an issue
and a pass that came back empty-handed both end when their stream runs
out, so the terminal event has to carry the stream's own shape — its
counts, its error, its duration — or the log cannot tell them apart.
"""

import asyncio
from collections.abc import AsyncGenerator, Mapping, Sequence
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Final

import pytest
import structlog.testing

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.records import RECORD_KIND_BY_PASS
from kodezart.core.errors import PromptRenderError
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.core.protocols import AgentRunner, PromptSetProvider
from kodezart.services import pass_scheduler as pass_scheduler_module
from kodezart.services.prompt_pass import (
    PromptPass,
    gate_render_bindings,
    pass_render_bindings,
)
from kodezart.types.domain.agent import (
    PASS_GATE_SCHEMA,
    AgentEvent,
    AssistantTextEvent,
    ErrorEvent,
    ResultEvent,
    ToolUseEvent,
)
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.session import (
    AllowedTools,
    PermissionMode,
    SessionType,
)
from kodezart.types.domain.skills import SkillsMode, SkillsSelection
from kodezart.types.domain.subagents import (
    NO_SUBAGENTS,
    UNCONFIGURED_SESSION_POLICY,
    AgentDefinition,
    SessionEffort,
    SessionPolicy,
)
from tests.fakes import (
    FAKE_SESSION_TYPE,
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
)
from tests.prompts.test_prompt_wiring import load_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "docs" / "operation.example.toml"

WORKSPACE = "/tmp/kodezart-scheduled-pass"
PERMISSION_MODE = PermissionMode.UNATTENDED
#: The shipped set that declares a session role covering both pass keys.
POLICIED_SET = "anthropic_v5"
ALL_SKILLS = SkillsSelection(mode=SkillsMode.ALL)
MODEL = "claude-opus-5"
#: The engine a deployment pins the gate key to, spelled so no default
#: could produce it.
GATE_MODEL = "cheapest-engine"
TRACKER_TOOL = "mcp__linear__save_issue"
#: What the gate is sent with: the wire schema of its output model.
GATE_OUTPUT_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "schema": PASS_GATE_SCHEMA,
}
#: Milliseconds, because it is a real wait: what the cancellation test
#: proves is the scheduler's own bound reaching a session mid-stream, and
#: that bound is enforced by the event loop's timer and nothing else.
CANCEL_TIMEOUT = 0.05


#: The instant every tick in this module begins at.  The scheduler stamps
#: ``started_at`` from the wall clock and the pass binds the per-run record
#: title from it (KOD-290), so a case that compares a sent prompt with a
#: rendered one renders the same instant the tick used.
TICK: Final[datetime] = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
#: The ticks after it, one interval apart, for the cases that drive one
#: pass object through several.
NEXT_TICK: Final[datetime] = TICK + timedelta(minutes=30)
THIRD_TICK: Final[datetime] = NEXT_TICK + timedelta(minutes=30)


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


class HangingRunner:
    """A runner whose stream yields once and then stops producing.

    Stands in for the session the scheduler's budget exists for: one that
    opened, said something, and never reached a terminal event.
    """

    def __init__(self) -> None:
        self.cancelled: bool = False
        self.entered = asyncio.Event()

    async def stream_in_workspace(
        self,
        **_kwargs: object,
    ) -> AsyncGenerator[AgentEvent, None]:
        yield AssistantTextEvent(text="working", model=MODEL)
        try:
            self.entered.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def tool_use(index: int) -> ToolUseEvent:
    """One tracker call, as the stream carries it."""
    return ToolUseEvent(
        name=TRACKER_TOOL,
        input={"id": f"KOD-{index}"},
        id=f"toolu_{index}",
        model=MODEL,
    )


def result_event() -> ResultEvent:
    return ResultEvent(
        subtype="success",
        duration_ms=1200,
        duration_api_ms=900,
        is_error=False,
        num_turns=3,
        session_id="session-1",
    )


def terminal_event(
    logs: list[Mapping[str, object]],
    name: str,
) -> Mapping[str, object]:
    """The one emission called *name*; unpacking fails if there is not exactly one."""
    (entry,) = [record for record in logs if record["event"] == name]
    return entry


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


def gate_prompt(
    registry: PromptSetProvider, *, key: PromptKey, window_start: datetime
) -> str:
    """The gate question *key*'s pass sends over a window starting then."""
    return registry.template_for(PromptKey.PASS_GATE).render(
        gate_render_bindings(name=key.value, window_start=window_start)
    )


def prompt_pass(
    *,
    prompts: PromptSetProvider,
    runner: AgentRunner,
    key: PromptKey = PromptKey.GROOMING_PASS,
    skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
) -> PromptPass:
    """One pass object, wired the way the composition wires it."""
    return PromptPass(
        kind=RECORD_KIND_BY_PASS[key],
        key=key,
        prompts=prompts,
        runner=runner,
        workspace_path=WORKSPACE,
        permission_mode=PERMISSION_MODE,
        allowed_tools=["Bash"],
        skills=skills,
        session_type=SessionType.SCHEDULED_PASS,
    )


async def run(
    *,
    prompts: PromptSetProvider,
    runner: AgentRunner,
    key: PromptKey = PromptKey.GROOMING_PASS,
    skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
) -> PassRun:
    """One tick of a fresh pass: the first tick, which asks no gate."""
    return await prompt_pass(
        prompts=prompts, runner=runner, key=key, skills=skills
    ).run(TICK)


class GateAnsweringRunner:
    """A runner that answers the gate question and then plays the pass.

    A call carrying an ``output_format`` is the gate: it ends in one
    result whose structured output is the next scripted answer, ``None``
    standing for a result that carries none.  Every other call is the
    pass's own session and plays *events*.  Records what
    :class:`FakeAgentRunner` records, so a case can assert on the gate
    call and the pass call in one shape.
    """

    def __init__(
        self,
        *,
        answers: Sequence[object | None],
        events: Sequence[AgentEvent] = (),
    ) -> None:
        self._answers: list[object | None] = list(answers)
        self._events: tuple[AgentEvent, ...] = tuple(events)
        self.calls: list[dict[str, object]] = []

    async def stream_in_workspace(
        self,
        *,
        prompt: str,
        workspace_path: str,
        permission_mode: PermissionMode,
        allowed_tools: AllowedTools,
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_type: SessionType = FAKE_SESSION_TYPE,
        run_identity: RunIdentity | None = None,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append(
            {
                "method": "stream_in_workspace",
                "prompt": prompt,
                "workspace_path": workspace_path,
                "permission_mode": permission_mode,
                "allowed_tools": allowed_tools,
                "session_id": session_id,
                "session_type": session_type,
                "run_identity": run_identity,
                "skills": skills,
                "session_policy": session_policy,
                "output_format": output_format,
            }
        )
        if output_format is None:
            for event in self._events:
                yield event
            return
        yield ResultEvent(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="gate-session",
            structured_output=self._answers.pop(0),
        )

    def gate_calls(self) -> list[dict[str, object]]:
        """The calls that carried the gate's schema, in order."""
        return [call for call in self.calls if call["output_format"] is not None]

    def pass_calls(self) -> list[dict[str, object]]:
        """The calls that opened a pass session, in order."""
        return [call for call in self.calls if call["output_format"] is None]


def gate_answer(
    *, run: bool, moved: Sequence[str] = (), reason: str
) -> dict[str, object]:
    """One well-formed answer, as the wire carries it."""
    return {
        "run": run,
        "moved": [{"key": key, "why": f"{key} moved"} for key in moved],
        "reason": reason,
    }


def test_the_grooming_prompt_composes_through_the_registry() -> None:
    """The mirror of the fire-prep render: template plus operation config."""
    config = example_config()
    rendered = (
        bound_registry()
        .template_for(PromptKey.GROOMING_PASS)
        .render(per_run(PromptKey.GROOMING_PASS))
    )

    assert rendered
    assert "{{" not in rendered
    assert config.operation_name in rendered


def test_an_unbound_placeholder_is_a_typed_refusal_not_a_prompt() -> None:
    """No config value, no prompt, and the placeholder is named."""
    with pytest.raises(PromptRenderError) as excinfo:
        load_registry().template_for(PromptKey.GROOMING_PASS).render(
            per_run(PromptKey.GROOMING_PASS)
        )

    assert "operation_name" in excinfo.value.missing


async def test_the_session_receives_the_rendered_prompt_and_its_grant() -> None:
    """What reaches the query path is what the registry rendered."""
    registry = bound_registry()
    rendered = registry.template_for(PromptKey.GROOMING_PASS).render(
        per_run(PromptKey.GROOMING_PASS)
    )
    runner = FakeAgentRunner(events=[])

    await run(prompts=registry, runner=runner)

    assert runner.calls == [
        {
            "method": "stream_in_workspace",
            "prompt": rendered,
            "workspace_path": WORKSPACE,
            "permission_mode": PERMISSION_MODE,
            "allowed_tools": ["Bash"],
            "session_id": None,
            "session_type": SessionType.SCHEDULED_PASS,
            "run_identity": None,
            "skills": SUPPRESS_ALL_SKILLS,
            "session_policy": registry.session_policy(PromptKey.GROOMING_PASS),
            "output_format": None,
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
    assert call["session_policy"].effort is SessionEffort.MAX
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
    assert sent[0] == registry.template_for(PromptKey.FIRE_PREP_PASS).render(
        per_run(PromptKey.FIRE_PREP_PASS)
    )
    assert sent[1] == registry.template_for(PromptKey.GROOMING_PASS).render(
        per_run(PromptKey.GROOMING_PASS)
    )


class TestTheGateQuestion:
    """The gate: one short session before the pass, answered in a shape.

    Driven over one pass object through several ticks, because the window
    the question is asked over is the pass's own state: where the last
    tick that ran began.
    """

    async def test_the_first_tick_asks_nothing_and_runs(self) -> None:
        """No window yet, no question: the boot tick reads the whole board."""
        registry = bound_registry()
        runner = GateAnsweringRunner(answers=[])
        pass_ = prompt_pass(prompts=registry, runner=runner)

        assert pass_.window_start is None
        assert await pass_.run(TICK) is PassRun.RAN

        assert runner.gate_calls() == []
        assert [call["prompt"] for call in runner.pass_calls()] == [
            registry.template_for(PromptKey.GROOMING_PASS).render(
                per_run(PromptKey.GROOMING_PASS)
            )
        ]
        assert pass_.window_start == TICK

    async def test_the_next_tick_asks_the_gate_over_the_window_with_the_schema(
        self,
    ) -> None:
        """The question carries the window, the schema and the gate key's policy.

        The window starts where the last tick that ran began, the answer is
        demanded in the output model's own wire schema, and the session is
        the same kind as the pass with the ``pass_gate`` key's own policy —
        the way every other structured session here is dispatched.
        """
        registry = policied_registry()
        runner = GateAnsweringRunner(
            answers=[gate_answer(run=True, moved=["KOD-1"], reason="one moved")]
        )
        pass_ = prompt_pass(prompts=registry, runner=runner, skills=ALL_SKILLS)
        await pass_.run(TICK)

        with structlog.testing.capture_logs() as logs:
            assert await pass_.run(NEXT_TICK) is PassRun.RAN

        (gate,) = runner.gate_calls()
        assert gate["prompt"] == gate_prompt(
            registry, key=PromptKey.GROOMING_PASS, window_start=TICK
        )
        assert gate["output_format"] == GATE_OUTPUT_FORMAT
        assert gate["session_type"] is SessionType.SCHEDULED_PASS
        assert gate["permission_mode"] is PERMISSION_MODE
        assert gate["allowed_tools"] == []
        assert gate["workspace_path"] == WORKSPACE
        assert gate["session_policy"] == registry.session_policy(PromptKey.PASS_GATE)
        assert gate["skills"] == registry.session_skills(
            PromptKey.PASS_GATE, ALL_SKILLS
        )
        asked = terminal_event(logs, "agent_question_asked")
        assert asked["key"] == PromptKey.PASS_GATE.value
        answered = terminal_event(logs, "pass_gate_answered")
        assert answered["name"] == PromptKey.GROOMING_PASS.value
        assert asked["effort"] == SessionEffort.MAX.value
        assert asked["model"] is None
        # The gate is asked first, and the pass session comes after it.
        assert [call["output_format"] for call in runner.calls[-2:]] == [
            GATE_OUTPUT_FORMAT,
            None,
        ]

    async def test_a_no_answer_skips_the_pass_with_the_reason_logged(self) -> None:
        """``run: false`` opens no session and says why, and the window stays.

        The pass SAYS it skipped, rather than ending indistinguishably from
        one that ran: its driver has a record obligation that turns on the
        difference (KOD-176).  Nothing ran, so the next question is asked
        over the same window again.
        """
        registry = bound_registry()
        runner = GateAnsweringRunner(
            answers=[
                gate_answer(run=False, reason="nothing moved since the last pass"),
                gate_answer(run=False, reason="still nothing"),
            ]
        )
        pass_ = prompt_pass(prompts=registry, runner=runner)
        await pass_.run(TICK)

        with structlog.testing.capture_logs() as logs:
            assert await pass_.run(NEXT_TICK) is PassRun.SKIPPED

        assert len(runner.pass_calls()) == 1, "the skipped tick opened no session"
        answered = terminal_event(logs, "pass_gate_answered")
        assert answered["name"] == PromptKey.GROOMING_PASS.value
        assert answered["run"] is False
        assert answered["moved_count"] == 0
        assert answered["reason"] == "nothing moved since the last pass"
        assert [
            record["event"]
            for record in logs
            if record["event"].startswith("prompt_pass")
        ] == []
        assert pass_.window_start == TICK
        await pass_.run(THIRD_TICK)
        assert runner.gate_calls()[-1]["prompt"] == gate_prompt(
            registry, key=PromptKey.GROOMING_PASS, window_start=TICK
        )

    async def test_a_yes_answer_opens_the_pass_and_advances_the_window(self) -> None:
        """``run: true`` opens the session as today, and the window moves on."""
        registry = bound_registry()
        runner = GateAnsweringRunner(
            answers=[
                gate_answer(run=True, moved=["KOD-1", "KOD-2"], reason="two moved"),
                gate_answer(run=False, reason="quiet"),
            ],
            events=[result_event()],
        )
        pass_ = prompt_pass(prompts=registry, runner=runner)
        await pass_.run(TICK)

        with structlog.testing.capture_logs() as logs:
            assert await pass_.run(NEXT_TICK) is PassRun.RAN

        assert len(runner.pass_calls()) == 2
        answered = terminal_event(logs, "pass_gate_answered")
        assert answered["run"] is True
        assert answered["moved_count"] == 2
        assert answered["reason"] == "two moved"
        assert terminal_event(logs, "prompt_pass_finished")["result_event_observed"]
        assert pass_.window_start == NEXT_TICK
        await pass_.run(THIRD_TICK)
        assert runner.gate_calls()[-1]["prompt"] == gate_prompt(
            registry, key=PromptKey.GROOMING_PASS, window_start=NEXT_TICK
        )

    async def test_a_missing_answer_runs_the_pass(self) -> None:
        """A gate that ended with no structured answer is not a skip."""
        registry = bound_registry()
        runner = GateAnsweringRunner(answers=[None])
        pass_ = prompt_pass(prompts=registry, runner=runner)
        await pass_.run(TICK)

        with structlog.testing.capture_logs() as logs:
            assert await pass_.run(NEXT_TICK) is PassRun.RAN

        assert len(runner.pass_calls()) == 2
        unanswered = terminal_event(logs, "agent_question_unanswered")
        assert unanswered["log_level"] == "warning"
        assert unanswered["key"] == PromptKey.PASS_GATE.value
        assert unanswered["error"] == "the session ended with no structured answer"
        assert [
            record["event"]
            for record in logs
            if record["event"] == "pass_gate_answered"
        ] == []

    async def test_a_malformed_answer_runs_the_pass(self) -> None:
        """An answer the output model refuses is named, and the pass runs."""
        registry = bound_registry()
        runner = GateAnsweringRunner(answers=[{"run": "maybe", "moved": "KOD-1"}])
        pass_ = prompt_pass(prompts=registry, runner=runner)
        await pass_.run(TICK)

        with structlog.testing.capture_logs() as logs:
            assert await pass_.run(NEXT_TICK) is PassRun.RAN

        assert len(runner.pass_calls()) == 2
        unanswered = terminal_event(logs, "agent_question_unanswered")
        assert unanswered["key"] == PromptKey.PASS_GATE.value
        assert "run" in str(unanswered["error"])
        assert "reason" in str(unanswered["error"])

    async def test_the_gate_runs_on_the_engine_its_key_is_pinned_to(self) -> None:
        """``session_models`` pins the gate key alone; the pass keeps its own.

        The same mechanism that pins the branch-name session: the policy the
        registry serves for the key carries the pinned engine and the
        role's effort, and both reach the executor call and the log.
        """
        registry = load_registry(
            default_set=POLICIED_SET,
            bindings=dict(bindings_for(example_config())),
            session_models={PromptKey.PASS_GATE.value: GATE_MODEL},
        )
        runner = GateAnsweringRunner(answers=[gate_answer(run=True, reason="moved")])
        pass_ = prompt_pass(prompts=registry, runner=runner)
        await pass_.run(TICK)

        with structlog.testing.capture_logs() as logs:
            await pass_.run(NEXT_TICK)

        (gate,) = runner.gate_calls()
        assert gate["session_policy"].model == GATE_MODEL
        assert gate["session_policy"].effort is SessionEffort.MAX
        assert all(call["session_policy"].model is None for call in runner.pass_calls())
        asked = terminal_event(logs, "agent_question_asked")
        assert asked["model"] == GATE_MODEL
        assert asked["effort"] == SessionEffort.MAX.value

    async def test_a_gate_that_raises_propagates_and_keeps_the_window(self) -> None:
        """A raise is not an answer: the tick fails loudly, nothing is skipped."""
        runner = RaisingRunner(after_calls=1)
        pass_ = prompt_pass(prompts=bound_registry(), runner=runner)
        await pass_.run(TICK)

        with pytest.raises(RuntimeError):
            await pass_.run(NEXT_TICK)

        assert runner.calls == 2, "the gate was asked and raised"
        assert pass_.window_start == TICK

    async def test_a_pass_that_raised_leaves_no_window_behind(self) -> None:
        """A session that never ran is not a run the next window starts from."""
        pass_ = prompt_pass(prompts=bound_registry(), runner=RaisingRunner())

        with pytest.raises(RuntimeError):
            await pass_.run(TICK)

        assert pass_.window_start is None

    def test_the_gate_prompt_is_stable_up_to_its_per_tick_values(self) -> None:
        """Everything before the pass name and the window is the same text.

        The stable prefix is what the engine caches across ticks, so the
        two values that change per tick are the last lines and nothing
        before them differs between passes or windows.
        """
        registry = bound_registry()
        first = gate_prompt(registry, key=PromptKey.FIRE_PREP_PASS, window_start=TICK)
        second = gate_prompt(
            registry, key=PromptKey.GROOMING_PASS, window_start=NEXT_TICK
        )

        head, marker, tail = first.rpartition("\nPass: ")
        assert marker
        assert second.startswith(head + marker)
        assert (
            tail
            == f"{PromptKey.FIRE_PREP_PASS.value}\nWindow start: {TICK.isoformat()}"
        )
        assert len(head) > len(tail) * 10


async def test_an_error_arriving_mid_stream_ends_the_pass_as_a_failure() -> None:
    """The event that used to be consumed and dropped on the way past.

    Mid-stream deliberately: the session keeps producing after it, the
    stream still ends normally, and a pass that only looked at how the
    stream ENDED would report the same completion as a clean run — which
    is how a pass that achieved nothing came to read as one that had.
    """
    events: list[AgentEvent] = [
        AssistantTextEvent(text="reading the board", model=MODEL),
        ErrorEvent(error="the tracker refused the scan", error_kind="TrackerMCPError"),
        AssistantTextEvent(text="giving up", model=MODEL),
    ]
    runner = FakeAgentRunner(events=events)

    with structlog.testing.capture_logs() as logs:
        await run(prompts=bound_registry(), runner=runner)

    assert [record["event"] for record in logs] == ["prompt_pass_failed"]
    failure = terminal_event(logs, "prompt_pass_failed")
    assert failure["log_level"] == "error"
    assert failure["name"] == PromptKey.GROOMING_PASS.value
    assert failure["error"] == "the tracker refused the scan"
    assert failure["error_kind"] == "TrackerMCPError"
    assert failure["result_event_observed"] is False
    assert failure["events"] == {"assistant_text": 2, "error": 1}
    assert failure["event_count"] == 3


async def test_a_pass_that_touched_the_tracker_is_legible_as_one_that_did() -> None:
    """The counts are what separate work done from a session that idled."""
    events: list[AgentEvent] = [
        AssistantTextEvent(text="closing it", model=MODEL),
        tool_use(1),
        tool_use(2),
        result_event(),
    ]
    runner = FakeAgentRunner(events=events)

    with structlog.testing.capture_logs() as logs:
        await run(prompts=bound_registry(), runner=runner)

    finished = terminal_event(logs, "prompt_pass_finished")
    assert finished["events"] == {"assistant_text": 1, "tool_use": 2, "result": 1}
    assert finished["event_count"] == 4
    assert finished["result_event_observed"] is True


async def test_a_pass_that_did_nothing_at_all_says_that_much() -> None:
    """The other half of the same reading, and the reason it is a count."""
    runner = FakeAgentRunner(events=[result_event()])

    with structlog.testing.capture_logs() as logs:
        await run(prompts=bound_registry(), runner=runner)

    finished = terminal_event(logs, "prompt_pass_finished")
    assert finished["events"] == {"result": 1}
    assert finished["event_count"] == 1
    assert finished["result_event_observed"] is True


async def test_a_stream_that_never_produced_a_terminal_result_says_so() -> None:
    """ "Finished" claims the stream ended, and never more than that."""
    runner = FakeAgentRunner(events=[])

    with structlog.testing.capture_logs() as logs:
        await run(prompts=bound_registry(), runner=runner)

    finished = terminal_event(logs, "prompt_pass_finished")
    assert finished["result_event_observed"] is False
    assert finished["events"] == {}
    assert finished["event_count"] == 0


async def test_every_terminal_pass_event_carries_how_long_the_pass_took() -> None:
    """Both arms, one reading: a pass degrading is visible before it hangs.

    The value is asserted as a non-negative number and never against a
    wall time — what a pass took on the machine running the suite is not
    a property of the pass.
    """
    clean = FakeAgentRunner(events=[result_event()])
    broken = FakeAgentRunner(events=[ErrorEvent(error="refused")])

    with structlog.testing.capture_logs() as logs:
        await run(prompts=bound_registry(), runner=clean)
        await run(prompts=bound_registry(), runner=broken)

    for name in ("prompt_pass_finished", "prompt_pass_failed"):
        duration = terminal_event(logs, name)["duration_seconds"]
        assert isinstance(duration, float)
        assert duration >= 0.0


async def test_a_pass_cancelled_mid_stream_reports_no_terminal_outcome() -> None:
    """The scheduler's budget, meeting this body: nothing claims it ended.

    The bookkeeping around the stream read has no handler of its own, so
    ``CancelledError`` unwinds straight through it — a pass abandoned by
    the driver leaves ``scheduled_pass_timed_out`` and no pass event, not
    a completion for a session that never completed.
    """
    runner = HangingRunner()

    with structlog.testing.capture_logs() as logs:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(CANCEL_TIMEOUT):
                await run(prompts=bound_registry(), runner=runner)

    assert runner.cancelled
    assert logs == []


class RaisingRunner:
    """A runner whose stream raises before the session says anything.

    The session could not be started at all — the gate's or the pass's —
    so the tick fails where the scheduler names it, and no window is
    advanced for work that never happened.
    """

    def __init__(self, *, after_calls: int = 0) -> None:
        #: How many calls play out empty before the stream starts raising:
        #: zero raises the first session, one lets a first tick run and
        #: raises the gate the next tick asks.
        self._after_calls: int = after_calls
        self.calls: int = 0

    async def stream_in_workspace(
        self,
        **_kwargs: object,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls += 1
        if self.calls <= self._after_calls:
            return
        msg = "the session could not be started"
        raise RuntimeError(msg)
        yield  # pragma: no cover - unreachable, and what makes this a generator


async def test_a_pass_cancelled_on_its_budget_advances_no_window() -> None:
    """A timed-out pass is not a run the next window starts from.

    Driven as the scheduler's own bound reaching a live session, so what
    survives the unwind is the untouched window and not an exception type
    this test chose.
    """
    runner = HangingRunner()
    pass_ = prompt_pass(prompts=bound_registry(), runner=runner)

    running = asyncio.create_task(pass_.run(TICK))
    try:
        await asyncio.wait_for(runner.entered.wait(), timeout=5.0)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(running, timeout=CANCEL_TIMEOUT)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    assert runner.cancelled
    assert pass_.window_start is None
