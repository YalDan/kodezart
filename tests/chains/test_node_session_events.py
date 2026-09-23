"""Real SDK mapping and evaluator dispatch retain actual native openings."""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from claude_agent_sdk import ResultMessage, SystemMessage
from pydantic import ValidationError

from kodezart.adapters.claude.client_executor import ClaudeClientExecutor
from kodezart.adapters.claude.sdk_mapping import map_message
from kodezart.core.node_sessions import NodeSessionObserver
from kodezart.types.domain.agent import NodeSessionStartedEvent
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.node_session import (
    NodeInvocation,
    NodeSessionObservationError,
)
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.run_state import LaneBinding
from tests.chains.test_ralph_loop import _make_loop, _run_kwargs
from tests.fakes import (
    DEFAULT_SETTING_SOURCES,
    NO_KNOWLEDGE_GRANT,
    FakeWorkspaceProvider,
)

RUN = RunIdentity(
    kind=RunKind.FIRE, name="subject/42", started_at=datetime(2026, 1, 1, tzinfo=UTC)
)


def invocation(**updates):
    return NodeInvocation(
        **{
            "run": RUN,
            "node_key": "evaluation",
            "invocation_key": "invocation-1",
            "declared_sessions": 1,
            **updates,
        }
    )


def init(session_id):
    return SystemMessage(
        subtype="init", data={"session_id": session_id, "model": "engine"}
    )


def result(session_id, *, passed=True, empty=False):
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id=session_id,
        structured_output={
            "criteriaResults": [
                {
                    "criterionId": "AC-2" if empty else "AC-1",
                    "criterion": "Tests pass",
                    "passed": passed,
                    "reasoning": "Observed fixture result.",
                }
            ]
        },
    )


@pytest.mark.parametrize("count", [1, 2, 4])
def test_openings_use_native_identity_and_deduplicate_repeated_frames(count):
    events = []
    declared = invocation(declared_sessions=count)
    observer = NodeSessionObserver(invocation=declared, emit=events.append)
    for index in range(count):
        for message in [
            init(f"session-{index}"),
            init(f"session-{index}"),
            result(f"session-{index}"),
        ]:
            for event in map_message(message):
                observer.observe(event)
    observer.require_valid()
    assert [event.session_id for event in events] == [
        f"session-{index}" for index in range(count)
    ]
    assert all(
        event.type is RunEventKind.NODE_SESSION_STARTED and event.invocation == declared
        for event in events
    )
    assert (
        NodeSessionStartedEvent.model_validate_json(events[0].model_dump_json())
        == events[0]
    )


@pytest.mark.parametrize("session_id", [None, "", " ", 3, True])
def test_malformed_native_opening_is_never_inferred_from_a_result(session_id):
    events = []
    observer = NodeSessionObserver(invocation=invocation(), emit=events.append)
    for message in [init(session_id), result("result-only")]:
        for event in map_message(message):
            observer.observe(event)
    assert events == []
    with pytest.raises(NodeSessionObservationError):
        observer.require_valid()


@pytest.mark.parametrize("count", [0, -1, True, "1"])
def test_a_declared_shape_is_an_explicit_positive_count(count):
    with pytest.raises(ValidationError):
        invocation(declared_sessions=count)


@pytest.mark.parametrize("kind", [RunKind.FIRE_PREP, RunKind.GROOMING])
def test_an_invocation_cannot_rename_another_kind_as_a_fire(kind):
    with pytest.raises(ValidationError, match="existing fire identity"):
        invocation(run=RUN.model_copy(update={"kind": kind}))


@pytest.mark.parametrize("subtype", ["status", "hook_started", "conversation_reset"])
def test_other_native_frames_are_not_session_openings(subtype):
    events = []
    observer = NodeSessionObserver(invocation=invocation(), emit=events.append)
    for event in map_message(SystemMessage(subtype=subtype, data={"session_id": "s"})):
        observer.observe(event)
    observer.require_valid()
    assert events == []


def test_occurrence_cannot_claim_another_event_kind_or_an_extra_field():
    raw = {"invocation": invocation(), "session_id": "native"}
    for update in ({"type": RunEventKind.LANE_DISPATCHED}, {"persisted": True}):
        with pytest.raises(ValidationError):
            NodeSessionStartedEvent(**raw, **update)


class SDKClient:
    def __init__(self, *, options, state):
        self.options = options
        self.state = state
        self.evaluation = options.output_format is not None
        self.session_id = f"native-{len(state['clients'])}"
        state["clients"].append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.state["closed"].append(self.session_id)

    async def query(self, prompt):
        assert prompt

    async def receive_response(self):
        state = self.state
        mode = state["mode"]
        session_id = self.session_id
        if not self.evaluation:
            yield init(session_id)
            yield result(session_id)
            return
        state["evaluations"] += 1
        if mode != "no-opening":
            yield init(None if mode == "invalid-opening" else session_id)
        if mode == "repeat":
            yield init(session_id)
        if mode == "two-sessions":
            yield init("second-native-session")
        if mode == "opening-then-malformed":
            yield init(None)
        if mode == "retry" and state["evaluations"] == 1:
            raise ConnectionError("native transient after opening")
        if mode == "cancel":
            state["opened"].set()
            await asyncio.Event().wait()
        yield result(
            session_id,
            passed=mode != "iterations" or state["evaluations"] == 2,
            empty=mode == "correction" and state["evaluations"] == 1,
        )


@pytest.mark.parametrize(
    "mode",
    [
        "one",
        "repeat",
        "two-sessions",
        "iterations",
        "retry",
        "correction",
        "no-opening",
        "invalid-opening",
        "untracked",
        "cancel",
    ],
)
async def test_actual_evaluator_emits_only_its_native_sessions(monkeypatch, mode):
    state = {
        "clients": [],
        "closed": [],
        "mode": mode,
        "evaluations": 0,
        "opened": asyncio.Event(),
    }
    monkeypatch.setattr(
        "kodezart.adapters.claude.client_executor.ClaudeSDKClient",
        lambda **kwargs: SDKClient(**kwargs, state=state),
    )
    executor = ClaudeClientExecutor(
        setting_sources=DEFAULT_SETTING_SOURCES, knowledge_grant=NO_KNOWLEDGE_GRANT
    )
    workspace = FakeWorkspaceProvider()
    loop = _make_loop(
        executor=executor, workspace=workspace, retry_initial_interval=0.001
    )
    events = []
    observed_start = asyncio.Event()

    async def run():
        async for event in loop.run(
            **_run_kwargs(), run_identity=None if mode == "untracked" else RUN
        ):
            events.append(event)
            if isinstance(event, NodeSessionStartedEvent):
                observed_start.set()

    if mode in {"no-opening", "invalid-opening"}:
        with pytest.raises(NodeSessionObservationError):
            await run()
    elif mode == "cancel":
        task = asyncio.create_task(run())
        await asyncio.wait_for(state["opened"].wait(), 3)
        await asyncio.wait_for(observed_start.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await run()
    starts = [event for event in events if isinstance(event, NodeSessionStartedEvent)]
    if mode in {"no-opening", "invalid-opening", "untracked"}:
        assert starts == []
    elif mode != "cancel":
        assert len(starts) == (
            2 if mode in {"two-sessions", "iterations", "correction", "retry"} else 1
        )
        assert starts[0].session_id == "native-1"
        assert all(
            event.invocation.run == RUN and event.invocation.declared_sessions == 1
            for event in starts
        )
        addresses = [json.loads(event.invocation.invocation_key) for event in starts]
        if mode == "iterations":
            assert [address[1:3] for address in addresses] == [[1, 1], [2, 1]]
        elif mode == "correction":
            assert [address[1:3] for address in addresses] == [[1, 1], [1, 2]]
        elif mode == "two-sessions":
            assert addresses[0] == addresses[1]
        elif mode == "retry":
            assert (
                starts[0].invocation.invocation_key
                != starts[1].invocation.invocation_key
            )
    assert len(state["closed"]) == len(state["clients"])
    assert sum(row[0] == "release" for row in workspace.calls) == sum(
        row[0] == "acquire" for row in workspace.calls
    )


class RecordingSessions:
    """A session recorder that keeps what each evaluation handed it."""

    def __init__(self):
        self.calls = []

    async def record_node_sessions(self, *, lane, started):
        self.calls.append((lane, tuple(started)))


LANE = LaneBinding(
    lane_key="subject/42",
    body_digest="0" * 64,
    loop_branch="ralph/loop",
    deliverable_branch="feature/deliverable",
    base_ref="main",
    repo_url="https://github.com/acme/repo",
    repo_path=None,
    run_id="job-1",
    visibility=RepoVisibility.PUBLIC,
)


@pytest.mark.parametrize(
    ("mode", "recorded"),
    [
        ("one", [1]),
        ("two-sessions", [2]),
        ("iterations", [1, 1]),
        ("retry", [1, 1]),
        ("untracked", []),
    ],
)
async def test_each_evaluations_openings_are_handed_to_the_lanes_recorder(
    monkeypatch, mode, recorded
):
    """What the observer saw open is put on the lane's stream after the drain.

    Once per observed evaluation, with exactly the openings that evaluation
    streamed and nothing inferred: two openings under one invocation are
    handed over together, two iterations hand over one each, and an
    evaluation nothing observed hands over nothing at all. A drain that
    opened a session and then failed hands its opening over too, before the
    evaluation is retried under another attempt.
    """
    state = {
        "clients": [],
        "closed": [],
        "mode": mode,
        "evaluations": 0,
        "opened": asyncio.Event(),
    }
    monkeypatch.setattr(
        "kodezart.adapters.claude.client_executor.ClaudeSDKClient",
        lambda **kwargs: SDKClient(**kwargs, state=state),
    )
    executor = ClaudeClientExecutor(
        setting_sources=DEFAULT_SETTING_SOURCES, knowledge_grant=NO_KNOWLEDGE_GRANT
    )
    loop = _make_loop(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        retry_initial_interval=0.001,
    )
    sessions = RecordingSessions()
    loop._node_sessions = sessions
    # The lane this loop commits for is named by the scoped run's own
    # context; this bare loop is dispatched without one, so the binding it
    # would compose is stood in for.
    monkeypatch.setattr(loop, "_lane_binding", lambda _ctx: LANE)
    events = []

    async with asyncio.timeout(10):
        async for event in loop.run(
            **_run_kwargs(), run_identity=None if mode == "untracked" else RUN
        ):
            events.append(event)

    starts = [event for event in events if isinstance(event, NodeSessionStartedEvent)]
    assert [len(started) for _, started in sessions.calls] == recorded
    assert [event for _, started in sessions.calls for event in started] == starts
    assert {lane for lane, _ in sessions.calls} == ({LANE} if recorded else set())


async def test_openings_are_recorded_before_malformed_evidence_refuses(monkeypatch):
    """A drain whose native evidence is malformed still hands over what opened.

    The evaluation opens one session and then reports an opening with no
    session id. The recorder receives the one opening the observer saw, and
    only then does the observation error propagate.
    """
    state = {
        "clients": [],
        "closed": [],
        "mode": "opening-then-malformed",
        "evaluations": 0,
        "opened": asyncio.Event(),
    }
    monkeypatch.setattr(
        "kodezart.adapters.claude.client_executor.ClaudeSDKClient",
        lambda **kwargs: SDKClient(**kwargs, state=state),
    )
    executor = ClaudeClientExecutor(
        setting_sources=DEFAULT_SETTING_SOURCES, knowledge_grant=NO_KNOWLEDGE_GRANT
    )
    loop = _make_loop(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        retry_initial_interval=0.001,
    )
    sessions = RecordingSessions()
    loop._node_sessions = sessions
    monkeypatch.setattr(loop, "_lane_binding", lambda _ctx: LANE)
    events = []

    with pytest.raises(NodeSessionObservationError):
        async with asyncio.timeout(10):
            async for event in loop.run(**_run_kwargs(), run_identity=RUN):
                events.append(event)

    starts = [event for event in events if isinstance(event, NodeSessionStartedEvent)]
    assert len(starts) == 1
    assert sessions.calls == [(LANE, tuple(starts))]
