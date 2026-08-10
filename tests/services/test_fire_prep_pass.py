"""The preparation pass, driven over the shipped scan, gate and session.

Nothing between the tick and the tracker is a stand-in for the thing under
test.  ``PassSession`` is the shipped one over a scripted executor,
``HygieneScan`` is the shipped one over the shipped ``RegexContentScanner``
and the shipped pattern set, and the writes are observed on the tracker the
port wrote to.  Only two things are doubles and both are named: the tracker
(no live workspace in CI, ever) and the model's answer, which is stated by
each case because the pass is graded on what it does with an answer it did
not choose.

AC-18's production half lives here: the scan is reached through the pass,
on a body the pass froze, at the destination its own writer writes to.
"""

from collections.abc import AsyncGenerator, Callable, Sequence
from datetime import timedelta

from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.core.config import AppConfig
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.core.protocols import OutboundContentGate, PromptProvider
from kodezart.services.fire_prep_pass import FirePrepPass
from kodezart.services.hygiene_scan import HygieneScan
from kodezart.services.pass_gate import PassGate
from kodezart.services.pass_session import PassSession
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.gating import (
    GateDecision,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.operation import QueueState
from tests.fakes import (
    FIXTURE_EPOCH,
    SUPPRESS_ALL_SKILLS,
    FakeContentScanner,
    FakePassExecutor,
    FakeTrackerPort,
    PassThroughGate,
    make_tracker_issue,
)
from tests.services.test_dispatch_pass import operation_config

ISSUE = "K-9"
OTHER_ISSUE = "K-404"
WORKING_DIR = "/tmp/fixture-pass-session"
PAGE_SIZE = 50
TIMEOUT_SECONDS = 30.0
LATER = FIXTURE_EPOCH + timedelta(hours=1)

SHAPED_BODY = (
    "The importer accepts a trailing comma in a list literal. It should "
    "reject it and name the offending line, so a malformed manifest fails "
    "at load rather than half-importing."
)
#: Trips the shipped evaluator-material set. A body handing the executing
#: node its own answer sheet is the class the scan exists to stop.
UNSHAPED_BODY = "Acceptance criteria: the parser rejects a trailing comma."


class ExecutorAnotherActorRunsUnder(FakePassExecutor):
    """A session during which somebody else changes the board.

    The move happens when the session starts, which is the only place a
    test can put it: the pass froze its window before that and writes
    after, so this is the whole interval the re-read guard covers.
    """

    def __init__(
        self,
        *,
        answers: Sequence[dict[str, object] | None],
        move: Callable[[], None],
    ) -> None:
        super().__init__(answers=answers)
        self._move: Callable[[], None] = move

    async def stream(self, **kwargs: object) -> AsyncGenerator[AgentEvent, None]:
        self._move()
        async for event in super().stream(**kwargs):  # type: ignore[arg-type]
            yield event


class BlockingGate:
    """An ``OutboundContentGate`` that blocks everything it is handed."""

    def __init__(self) -> None:
        self.calls: list[OutboundDestination] = []

    async def gate(
        self,
        *,
        content: str,
        visibility: RepoVisibility,
        shape: WriterShape,
        destination: OutboundDestination,
    ) -> GateDecision:
        self.calls.append(destination)
        return GateDecision(verdict=GateVerdict.BLOCKED, content="")


def pass_prompts() -> PromptProvider:
    """The real registry with the operation namespace bound, as boot binds it.

    A registry loaded with empty bindings cannot render either pass
    template at all, and every case below would then pass by rendering
    nothing — which is why the binding source is the shipped
    ``bindings_for`` rather than a literal here.
    """
    return InRepoPromptRegistry.load(
        sets_root=default_sets_root(),
        default_set="claude-opus",
        set_overrides={},
        template_overrides={},
        bindings=dict(bindings_for(operation_config())),
    )


def shipped_scan() -> HygieneScan:
    """The scan as the composition root builds it: shipped engine and set."""
    return HygieneScan(
        scanner=RegexContentScanner(patterns=AppConfig().hygiene_patterns),
    )


def make_pass(
    *,
    tracker: FakeTrackerPort,
    answers: Sequence[dict[str, object] | None],
    gate: OutboundContentGate | None = None,
    scan: HygieneScan | None = None,
    executor: FakePassExecutor | None = None,
) -> FirePrepPass:
    """The pass over the shipped session, scan, pre-query and gate.

    The pre-query is the shipped ``PassGate`` over the same tracker, because
    it is the pass's window read as well as its cost gate: a double here
    would decide what the session sees.
    """
    return FirePrepPass(
        pre_query=PassGate(
            tracker=tracker,
            queue_state=QueueState.TRIAGE,
            page_size=PAGE_SIZE,
        ),
        tracker=tracker,
        session=PassSession(
            executor=executor or FakePassExecutor(answers=answers),
            prompts=pass_prompts(),
            skills=SUPPRESS_ALL_SKILLS,
            timeout_seconds=TIMEOUT_SECONDS,
        ),
        scan=scan or shipped_scan(),
        gate=gate or PassThroughGate(),
        working_dir=WORKING_DIR,
    )


def triage_tracker(*keys: str) -> FakeTrackerPort:
    return FakeTrackerPort(
        issues=[
            make_tracker_issue(key, queue_states=[QueueState.TRIAGE]) for key in keys
        ],
    )


async def test_a_shaped_body_lands_on_the_issue_and_leaves_the_entry_queue() -> None:
    """The whole promotion: scanned, gated, written, then the state moves."""
    tracker = triage_tracker(ISSUE)
    gate = PassThroughGate()
    await make_pass(
        tracker=tracker,
        answers=[{"preparations": [{"issueKey": ISSUE, "body": SHAPED_BODY}]}],
        gate=gate,
    ).run()

    assert tracker.issues[ISSUE].body == SHAPED_BODY
    assert tracker.queue_writes == [(ISSUE, QueueState.PROPOSED)]
    assert gate.destinations == [OutboundDestination.PREPARED_FIRE_BODY]


async def test_the_scan_receives_the_frozen_body_at_its_own_destination() -> None:
    """AC-18 on the production path: the entry point, the body, the surface.

    The recording scanner is the port's own implementation, so this
    observes the call the pass actually makes rather than a call count on
    the scan.
    """
    scanner = FakeContentScanner()
    tracker = triage_tracker(ISSUE)
    await make_pass(
        tracker=tracker,
        answers=[{"preparations": [{"issueKey": ISSUE, "body": SHAPED_BODY}]}],
        scan=HygieneScan(scanner=scanner),
    ).run()

    assert scanner.calls == [SHAPED_BODY]
    assert scanner.destinations == [OutboundDestination.PREPARED_FIRE_BODY]


async def test_a_body_the_hygiene_set_trips_is_never_written() -> None:
    """The scan is a gate on promotion, not a note attached to one."""
    tracker = triage_tracker(ISSUE)
    gate = PassThroughGate()
    await make_pass(
        tracker=tracker,
        answers=[{"preparations": [{"issueKey": ISSUE, "body": UNSHAPED_BODY}]}],
        gate=gate,
    ).run()

    assert tracker.issues[ISSUE].body == "fixture body"
    assert tracker.queue_writes == []
    assert gate.calls == []


async def test_a_blocked_body_leaves_its_issue_exactly_where_it_was() -> None:
    """The outbound gate's refusal is not a partial write and not a crash."""
    tracker = triage_tracker(ISSUE)
    gate = BlockingGate()
    await make_pass(
        tracker=tracker,
        answers=[{"preparations": [{"issueKey": ISSUE, "body": SHAPED_BODY}]}],
        gate=gate,
    ).run()

    assert gate.calls == [OutboundDestination.PREPARED_FIRE_BODY]
    assert tracker.issues[ISSUE].body == "fixture body"
    assert tracker.queue_writes == []


async def test_a_body_naming_an_item_outside_the_frozen_window_is_discarded() -> None:
    """A pass never acts on a state it did not read in this window."""
    tracker = triage_tracker(ISSUE)
    await make_pass(
        tracker=tracker,
        answers=[
            {
                "preparations": [
                    {"issueKey": OTHER_ISSUE, "body": SHAPED_BODY},
                    {"issueKey": ISSUE, "body": SHAPED_BODY},
                ],
            },
        ],
    ).run()

    assert OTHER_ISSUE not in tracker.issues
    assert tracker.queue_writes == [(ISSUE, QueueState.PROPOSED)]


async def test_an_empty_entry_queue_costs_no_session_at_all() -> None:
    """Nothing to prepare is not a reason to spend a session discovering it."""
    executor = FakePassExecutor(answers=[])
    tracker = FakeTrackerPort(issues=[])
    await make_pass(tracker=tracker, answers=[], executor=executor).run()

    assert executor.calls == []
    assert tracker.queue_writes == []


async def test_a_session_that_did_not_answer_writes_nothing() -> None:
    """Three states, none silent: no answer is not an empty answer."""
    tracker = triage_tracker(ISSUE)
    await make_pass(tracker=tracker, answers=[None]).run()

    assert tracker.queue_writes == []
    assert tracker.issues[ISSUE].body == "fixture body"


async def test_an_answer_the_model_shaped_differently_is_rejected_whole() -> None:
    """A malformed answer is a failed pass, never a partially applied one."""
    tracker = triage_tracker(ISSUE)
    await make_pass(
        tracker=tracker,
        answers=[{"preparations": [{"issueKey": ISSUE, "verdict": "ship it"}]}],
    ).run()

    assert tracker.queue_writes == []
    assert tracker.issues[ISSUE].body == "fixture body"


async def test_the_pass_never_sets_the_approved_state() -> None:
    """Approval is the only human act, asserted from the pass's own side."""
    tracker = triage_tracker(ISSUE)
    await make_pass(
        tracker=tracker,
        answers=[{"preparations": [{"issueKey": ISSUE, "body": SHAPED_BODY}]}],
    ).run()

    assert QueueState.APPROVED not in {state for _, state in tracker.queue_writes}


async def test_a_second_tick_over_an_unmoved_entry_queue_costs_no_session() -> None:
    """AC-19: the pre-query bounds the token cost, not the interval.

    Two ticks, one moved item: the second tick asks the tracker from the
    mark the first advanced, gets nothing, and never reaches a session. A
    pass wired without the gate composes over the same stalled window every
    interval, forever, which is the unbounded cost the deliverable names.
    """
    executor = FakePassExecutor(
        answers=[{"preparations": []}, {"preparations": []}],
    )
    tracker = triage_tracker(ISSUE)
    subject = make_pass(tracker=tracker, answers=[], executor=executor)

    await subject.run()
    await subject.run()

    assert len(executor.calls) == 1
    assert tracker.scans[-1].updated_since == FIXTURE_EPOCH


async def test_a_pass_whose_session_did_not_answer_re_reads_its_window() -> None:
    """The mark goes back: a window an aborted pass consumed is not skipped.

    Checkpointing without this is strictly worse than no checkpoint at all
    — before the gate the whole entry queue was re-read every tick, so a
    failed session was retried; a mark advanced on observation alone drops
    the window silently.
    """
    executor = FakePassExecutor(
        answers=[
            None,
            {"preparations": [{"issueKey": ISSUE, "body": SHAPED_BODY}]},
        ],
    )
    tracker = triage_tracker(ISSUE)
    subject = make_pass(tracker=tracker, answers=[], executor=executor)

    await subject.run()
    await subject.run()

    assert len(executor.calls) == 2
    assert tracker.scans[-1].updated_since is None
    assert tracker.issues[ISSUE].body == SHAPED_BODY


async def test_an_item_this_pass_refused_is_not_offered_again_until_it_moves() -> None:
    """KOD-60 R14: the window moves past a refusal, and the docstrings say so.

    Three ticks over one item. The first composes a body the hygiene set
    trips, so nothing is written and the item stays in the entry queue —
    but the mark has already advanced over it, so the second tick asks
    from that mark, gets nothing back and reaches no session. Only an
    edit that moves the item's stamp brings it into a window again.

    This is the behaviour the module docstrings previously denied. It
    reddens in both directions: rewinding the mark for a refused item
    makes the second tick spend a session, and a mark that never came
    back for an edited item makes the third spend none.
    """
    executor = FakePassExecutor(
        answers=[
            {"preparations": [{"issueKey": ISSUE, "body": UNSHAPED_BODY}]},
            {"preparations": [{"issueKey": ISSUE, "body": SHAPED_BODY}]},
        ],
    )
    tracker = triage_tracker(ISSUE)
    subject = make_pass(tracker=tracker, answers=[], executor=executor)

    await subject.run()
    await subject.run()

    assert len(executor.calls) == 1
    assert tracker.issues[ISSUE].body == "fixture body"
    assert QueueState.TRIAGE in tracker.issues[ISSUE].queue_states

    tracker.issues[ISSUE] = make_tracker_issue(
        ISSUE,
        queue_states=[QueueState.TRIAGE],
        created_at=LATER,
    )
    await subject.run()

    assert len(executor.calls) == 2
    assert tracker.issues[ISSUE].body == SHAPED_BODY


async def test_a_body_for_an_item_that_moved_under_the_session_is_dropped() -> None:
    """The re-read guard: another actor's change is not overwritten by a stale body.

    The item leaves the entry queue while the session composes — the exact
    window the guard exists for, since a session runs for minutes and the
    body it returns answers the state the pass froze.
    """
    tracker = triage_tracker(ISSUE)
    executor = ExecutorAnotherActorRunsUnder(
        answers=[{"preparations": [{"issueKey": ISSUE, "body": SHAPED_BODY}]}],
        move=lambda: tracker.issues.__setitem__(
            ISSUE,
            make_tracker_issue(ISSUE, queue_states=[QueueState.PROPOSED]),
        ),
    )
    await make_pass(tracker=tracker, answers=[], executor=executor).run()

    assert tracker.issues[ISSUE].body == "fixture body"
    assert tracker.queue_writes == []


async def test_a_body_for_an_item_edited_under_the_session_is_dropped() -> None:
    """Still in the entry queue, but no longer the text the pass read."""
    tracker = triage_tracker(ISSUE)
    executor = ExecutorAnotherActorRunsUnder(
        answers=[{"preparations": [{"issueKey": ISSUE, "body": SHAPED_BODY}]}],
        move=lambda: tracker.issues.__setitem__(
            ISSUE,
            make_tracker_issue(
                ISSUE,
                queue_states=[QueueState.TRIAGE],
                created_at=LATER,
                body="the founder rewrote this while the session was composing",
            ),
        ),
    )
    await make_pass(tracker=tracker, answers=[], executor=executor).run()

    assert tracker.issues[ISSUE].body.startswith("the founder rewrote")
    assert tracker.queue_writes == []


async def test_an_item_nobody_touched_still_gets_its_body() -> None:
    """Guards the two above: a guard that dropped everything would pass them."""
    tracker = triage_tracker(ISSUE)
    executor = ExecutorAnotherActorRunsUnder(
        answers=[{"preparations": [{"issueKey": ISSUE, "body": SHAPED_BODY}]}],
        move=lambda: None,
    )
    await make_pass(tracker=tracker, answers=[], executor=executor).run()

    assert tracker.issues[ISSUE].body == SHAPED_BODY
    assert tracker.queue_writes == [(ISSUE, QueueState.PROPOSED)]


async def test_the_session_reads_the_window_and_holds_no_tool() -> None:
    """The work set is rendered INTO the prompt; the session reaches nothing."""
    executor = FakePassExecutor(answers=[{"preparations": []}])
    tracker = triage_tracker(ISSUE)
    await make_pass(tracker=tracker, answers=[], executor=executor).run()

    (call,) = executor.calls
    assert call["allowed_tools"] == []
    assert call["cwd"] == WORKING_DIR
    prompt = call["prompt"]
    assert isinstance(prompt, str)
    assert ISSUE in prompt
    assert "fixture body" in prompt
