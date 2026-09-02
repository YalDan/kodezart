"""One pass, sent as one session: the render path, the gate, and the send.

The grooming render is asserted against the shipped template and the
shipped example operation config — the same artifacts the fire-prep render
is asserted against — because a render path tested against a fixture body
proves nothing about the prompt the deployment would actually send.

The four render-and-send behaviours here are the ones the deleted
per-pass session class carried, with their subject swapped for the single
run callable.  They are unchanged in what they assert: collapsing two
render paths into one must not quietly relax what either proved.

The last group reads what the pass REPORTED.  A pass that closed an issue
and a pass that came back empty-handed both end when their stream runs
out, so the terminal event has to carry the stream's own shape — its
counts, its error, its duration — or the log cannot tell them apart.
"""

import asyncio
from collections.abc import AsyncGenerator, Mapping
from datetime import timedelta
from pathlib import Path

import pytest
import structlog.testing

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.records import RECORD_KIND_BY_PASS
from kodezart.core.errors import PromptRenderError
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.core.protocols import AgentRunner, PromptSetProvider
from kodezart.services.pass_gate import PassGate
from kodezart.services.prompt_pass import run_prompt_pass
from kodezart.types.domain.agent import (
    AgentEvent,
    AssistantTextEvent,
    ErrorEvent,
    ResultEvent,
    ToolUseEvent,
)
from kodezart.types.domain.dispatch import PassRun, PassSignal
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
MODEL = "claude-opus-5"
TRACKER_TOOL = "mcp__linear__save_issue"
#: Milliseconds, because it is a real wait: what the cancellation test
#: proves is the scheduler's own bound reaching a session mid-stream, and
#: that bound is enforced by the event loop's timer and nothing else.
CANCEL_TIMEOUT = 0.05


class HangingRunner:
    """A runner whose stream yields once and then stops producing.

    Stands in for the session the scheduler's budget exists for: one that
    opened, said something, and never reached a terminal event.
    """

    def __init__(self) -> None:
        self.cancelled: bool = False

    async def stream_in_workspace(
        self,
        **_kwargs: object,
    ) -> AsyncGenerator[AgentEvent, None]:
        yield AssistantTextEvent(text="working", model=MODEL)
        try:
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


def pass_gate(tracker: FakeTrackerPort, *signals: PassSignal) -> PassGate:
    """A gate scoped the way the composition scopes a prompt pass's.

    Every declared board and every declared repository, because a prompt
    pass acts on the whole operation — the narrowing to one repository
    belongs to the dispatch pass and to nothing else.
    """
    operation = example_config()
    return PassGate(
        tracker=tracker,
        ledger=tracker.self_writes,
        signals=list(signals),
        team_keys=operation.team_keys(),
        repo_urls=[repo.url for repo in operation.repos],
        page_size=PAGE_SIZE,
    )


async def run(
    *,
    prompts: PromptSetProvider,
    runner: AgentRunner,
    gate: PassGate | None = None,
    key: PromptKey = PromptKey.GROOMING_PASS,
    skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
) -> PassRun:
    return await run_prompt_pass(
        FIXTURE_EPOCH,
        kind=RECORD_KIND_BY_PASS[key],
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

    assert await run(prompts=bound_registry(), runner=runner, gate=None) is PassRun.RAN

    assert len(runner.calls) == 1
    assert tracker.scans == []
    assert tracker.review_scans == []


async def test_a_quiet_gate_opens_no_session_and_renders_nothing() -> None:
    """The token claim, asserted: a quiet board pays for neither.

    The pass SAYS it skipped, rather than ending indistinguishably from
    one that ran: its driver has a record obligation that turns on the
    difference, and answering "completed" here backfilled a phantom run
    record row for every quiet tick of the measured boot (KOD-176).
    """
    tracker = FakeTrackerPort()
    runner = FakeAgentRunner(events=[])
    signals = (PassSignal.issues_changed, PassSignal.triage_backlog)
    gate = pass_gate(tracker, *signals)

    # An unbound registry would raise on render. It does not, which is how
    # this asserts the render never happened rather than merely that no
    # session opened after one.
    assert await run(prompts=load_registry(), runner=runner, gate=gate) is (
        PassRun.SKIPPED
    )

    assert runner.calls == []
    # One query per signal per declared board: the questions are asked
    # WITHIN a container, never once over the whole workspace.
    assert len(tracker.scans) == len(signals) * len(example_config().team_keys())


async def test_one_signal_reporting_work_is_enough_to_run_the_pass() -> None:
    """A review with no issue activity still wakes the pass.

    The paired positive of the skip: a woken pass reports that it RAN, so
    its driver keeps the record obligation the skip does not carry.
    """
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

    assert await run(prompts=bound_registry(), runner=runner, gate=gate) is PassRun.RAN

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

    The failure a pass beneath a gate can meet after the gate has already
    advanced its marks: the session could not be started at all, so the
    window the gate opened was never worked.
    """

    def __init__(self) -> None:
        self.calls: int = 0

    async def stream_in_workspace(
        self,
        **_kwargs: object,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls += 1
        msg = "the session could not be started"
        raise RuntimeError(msg)
        yield  # pragma: no cover - unreachable, and what makes this a generator


class TestAFailedPassGivesTheWakeUpBack:
    """A window the gate opened and the session never worked is re-read.

    Asking advances the mark so the next tick does not re-report the same
    window; the SESSION is what reads it, and a pass that raised opened
    none. Leaving the mark forward spends the wake-up on nothing (KOD-164).
    """

    async def test_a_session_that_raises_leaves_the_mark_where_it_was(self) -> None:
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue(
                    "FIX-1",
                    team_key=example_config().team_keys()[0],
                    created_at=LATER,
                ),
            ],
        )
        gate = pass_gate(tracker, PassSignal.issues_changed)
        runner = RaisingRunner()

        with pytest.raises(RuntimeError):
            await run(prompts=bound_registry(), runner=runner, gate=gate)

        assert (
            gate.mark(
                PassSignal.issues_changed,
                container=example_config().team_keys()[0],
            )
            is None
        )

    async def test_the_next_tick_asks_the_same_question_again(self) -> None:
        """Re-armed means re-asked, not merely un-stamped."""
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue(
                    "FIX-1",
                    team_key=example_config().team_keys()[0],
                    created_at=LATER,
                ),
            ],
        )
        gate = pass_gate(tracker, PassSignal.issues_changed)
        runner = RaisingRunner()

        for _ in range(2):
            with pytest.raises(RuntimeError):
                await run(prompts=bound_registry(), runner=runner, gate=gate)

        assert runner.calls == 2, "the second tick reached the session again"
        assert [scan.updated_since for scan in tracker.scans] == [None] * len(
            tracker.scans
        )

    async def test_a_session_that_ran_still_advances_its_mark(self) -> None:
        """The paired positive: only a failure gives the window back."""
        team_key = example_config().team_keys()[0]
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("FIX-1", team_key=team_key, created_at=LATER)],
        )
        gate = pass_gate(tracker, PassSignal.issues_changed)

        await run(
            prompts=bound_registry(),
            runner=FakeAgentRunner(events=[]),
            gate=gate,
        )

        assert gate.mark(PassSignal.issues_changed, container=team_key) == LATER

    async def test_a_pass_cancelled_on_its_budget_keeps_its_window_too(self) -> None:
        """A timed-out pass may not eat the wake-up either.

        Driven as the scheduler's own bound reaching a live session, so
        what survives the unwind is the re-arm and not an exception type
        this test chose.
        """
        team_key = example_config().team_keys()[0]
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue("FIX-1", team_key=team_key, created_at=LATER)],
        )
        gate = pass_gate(tracker, PassSignal.issues_changed)
        runner = HangingRunner()

        with pytest.raises(TimeoutError):
            async with asyncio.timeout(CANCEL_TIMEOUT):
                await run(prompts=bound_registry(), runner=runner, gate=gate)

        assert runner.cancelled
        assert gate.mark(PassSignal.issues_changed, container=team_key) is None
