"""The per-issue flow keeps running beside the scope walk (KOD-846).

A team bound to a repository a scope row names is walked scope by scope and
gets no per-issue pass over its board; every other team of the same deployment
keeps the dispatch pass, both session passes and the lifecycle watcher exactly
as before. And a per-issue run never reaches the scope walk: the dispatcher
submits no scope, the router sends an unscoped run to its origin's arm even with
the scoped arm wired, and the fire streams the authored graph for it.
"""

import asyncio
import re
from collections.abc import AsyncIterator

import pytest
import structlog.testing

from kodezart.chains.authored_delivery import AuthoredDeliveryCoordinator
from kodezart.chains.criteria import TrackerCriteria
from kodezart.composition.engine import OriginRoutedWorkflowEngine
from kodezart.composition.organize import scope_queue_lane
from kodezart.composition.passes import build_dispatch_runtime, verify_pass_preflight
from kodezart.composition.tracker import DialledTracker
from kodezart.core.logging import get_logger
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.agent import AgentEvent, WorkflowCompleteEvent
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import PermissionMode
from tests.chains.test_native_fire import drive, engine, tracker
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
from tests.integration.test_organize_scheduler import dependencies, scope_only
from tests.prompts.test_per_issue_roster_bindings import SCOPE_WALK_BOUND
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_prompt_passes import HEARTBEAT_PASS, ORGANIZE_PASS, _config
from tests.test_forge_origin_selection import ForbiddenWorkflowEngine, _arm

#: Bounded because a tick or a run that hangs is a failure, not a wait.
TICK_BOUND_SECONDS = 30

#: One approved open issue on each team's board: the walked team's and the
#: per-issue team's. The board keys are the teams' declared tracker keys.
WALKED_ISSUE = "EXA-1"
PER_ISSUE_ISSUE = "EXG-1"


#: The words opening each sweep of a session template that reaches past the
#: team roster: fire-prep's triage backlog and issue mention sweep, grooming's
#: tree and its mention scan. Each is one line of its template.
SWEEPS = {
    PromptKey.FIRE_PREP_PASS: ("(a) Triage backlog", "(b) Mention sweep (issues)"),
    PromptKey.GROOMING_PASS: (
        "**2. Groom the whole tree**",
        "**Mentions & principal comments.**",
    ),
}


def _board() -> FakeTrackerPort:
    return FakeTrackerPort(
        issues=[
            make_tracker_issue(WALKED_ISSUE, team_key="primary"),
            make_tracker_issue(PER_ISSUE_ISSUE, team_key="agent"),
        ]
    )


def _mixed(tmp_path):
    """The shared scope fixture, with every gate open.

    Primary (EXA) is bound to the repository the one scope row names, and agent
    (EXG) to a repository no row names, so this one operation declares a walked
    team and a per-issue team.
    """
    config, operation, _, _, prompts, _ = dependencies(tmp_path)
    config = config.model_copy(update={"dispatch_pass_gate_signals": []})
    return config, operation, prompts


def _v02_only(tmp_path, operation):
    """The same operation declaring no scope row, as a per-issue deployment."""
    per_issue = OperationConfig.model_validate(
        {**operation.model_dump(), "organize_scopes": []}
    )
    config = _config(
        tmp_path,
        fire_prep_pass_gate_signals=[],
        grooming_pass_gate_signals=[],
        dispatch_pass_gate_signals=[],
        ticket_review_mode="reviewed",
        write_back={"max_verify_rounds": 2},
    )
    prompts = load_registry(
        default_set="claude-opus", bindings=operation_bindings(per_issue)
    )
    return config, per_issue, prompts


async def _boot(config, operation, prompts, *, board, runner, queue):
    """Preflight, then the wiring: the two calls the composition root makes."""
    forge = FakeDeliveryProbe()
    with structlog.testing.capture_logs() as logs:
        await verify_pass_preflight(
            config=config,
            operation=operation,
            tracker=board,
            github_api=forge,
            prompts=prompts,
        )
        runtime = await build_dispatch_runtime(
            config=config,
            operation=operation,
            dialled=DialledTracker(
                tracker=board,
                caller=ManagedFakeLinearMcpServer(),
                operation=operation,
                ledger=board.self_writes,
                status=FakeScopeStatusWriter(),
            ),
            github_api=forge,
            queue=queue,
            registry=queue,
            gate=PassThroughGate(),
            git=FakeGitService(),
            cache=FakeRepoCache(),
            workspace=FakeWorkspaceProvider(),
            prompts=prompts,
            runner=runner,
            skills=SUPPRESS_ALL_SKILLS,
            recorder=RunRecorder(records={}, sinks={}),
            log=get_logger(__name__),
        )
    return runtime, logs


def _logged(logs, name):
    return [entry for entry in logs if entry.get("event") == name]


def _named(runtime, name):
    (entry,) = [entry for entry in runtime.scheduler.passes if entry.name == name]
    return entry


async def _tick(entry):
    async with asyncio.timeout(TICK_BOUND_SECONDS):
        return await entry.run(FIXTURE_EPOCH)


# ---------------------------------------------------------------------------
# One deployment, two flows: the walked team gets none of the per-issue
# passes, and the other team keeps all three.
# ---------------------------------------------------------------------------


async def test_a_scope_team_gets_no_per_issue_pass_while_the_other_team_keeps_all_three(
    tmp_path,
):
    config, operation, prompts = _mixed(tmp_path)
    example, second = (repo.url for repo in operation.repos)
    assert operation.scope_walked_teams() == ("primary",)
    assert operation.per_issue_teams() == ("agent",)
    board = _board()
    runner = FakeAgentRunner(events=[])
    queue = FakeJobQueue()

    runtime, logs = await _boot(
        config, operation, prompts, board=board, runner=runner, queue=queue
    )

    names = [entry.name for entry in runtime.scheduler.passes]
    assert names == [
        f"dispatch:{second}",
        "supervisor",
        ORGANIZE_PASS,
        PromptKey.FIRE_PREP_PASS.value,
        PromptKey.GROOMING_PASS.value,
        HEARTBEAT_PASS,
    ]
    assert len(set(names)) == len(names)
    assert f"dispatch:{example}" not in names
    assert runtime.lifecycle is not None
    assert _logged(logs, "scheduled_passes_not_wired") == []
    assert _logged(logs, "prompt_passes_not_wired") == []
    (unbound,) = _logged(logs, "dispatch_pass_unbound_repository")
    assert unbound["repo_url"] == example
    assert unbound["scope_walked_teams"] == ["primary"]

    # The per-issue team's dispatch tick scans its own board only, and submits
    # its issue with no scope on the dispatch lane. The walked team's issue is
    # never claimed.
    await _tick(_named(runtime, f"dispatch:{second}"))
    assert board.scans
    assert {query.team_key for query in board.scans} == {"agent"}
    ((lane, submission),) = queue.submissions
    assert lane == config.dispatch_lane
    assert submission.issue_key == PER_ISSUE_ISSUE
    assert submission.scope is None
    assert WALKED_ISSUE not in board.claims

    # Both session passes are told about the per-issue team's board alone,
    # and each sweep that reaches past that roster is bounded to it.
    for key in (PromptKey.FIRE_PREP_PASS, PromptKey.GROOMING_PASS):
        runner.calls.clear()
        await _tick(_named(runtime, key.value))
        (call,) = runner.calls
        prompt = str(call["prompt"])
        # The team keys as words: the templates' own prose says "EXACTLY".
        assert "example-agent-team" in prompt, key
        assert re.search(r"\bEXG\b", prompt), key
        assert "Example Team" not in prompt, key
        assert not re.search(r"\bEXA\b", prompt), key
        for sweep in SWEEPS[key]:
            assert re.search(
                re.escape(sweep) + r"[^\n]*" + re.escape(SCOPE_WALK_BOUND), prompt
            ), (key, sweep)

    # The heartbeat's lane is never the lane a per-issue fire waits on.
    assert scope_queue_lane(config) != config.dispatch_lane

    # The per-issue team's dispatch pass scans as it does with no scope row,
    # over the same doubles. Cadence, budget, record identity and gates are
    # compared over gated boots in the next test.
    v02_config, v02_operation, v02_prompts = _v02_only(tmp_path, operation)
    v02_board = _board()
    v02, _logs = await _boot(
        v02_config,
        v02_operation,
        v02_prompts,
        board=v02_board,
        runner=FakeAgentRunner(events=[]),
        queue=FakeJobQueue(),
    )
    assert operation.teams_scanned_by(second) == ("agent",)
    assert v02_operation.teams_scanned_by(second) == ("agent",)
    await _tick(_named(v02, f"dispatch:{second}"))
    assert {query.team_key for query in v02_board.scans} == {"agent"}
    # The one intended difference: with no scope row, primary is a per-issue
    # team too, and its repository keeps its own dispatch pass.
    assert v02_operation.per_issue_teams() == ("primary", "agent")
    assert f"dispatch:{example}" in [entry.name for entry in v02.scheduler.passes]


#: The two session passes, in the order the schedule registers them.
SESSION_PASSES = (PromptKey.FIRE_PREP_PASS, PromptKey.GROOMING_PASS)


def _gated(config):
    """*config* with both session passes gated on issue churn, so each gate scans."""
    return config.model_copy(
        update={
            "fire_prep_pass_gate_signals": [PassSignal.issues_changed],
            "grooming_pass_gate_signals": [PassSignal.issues_changed],
        }
    )


async def _session_scans(runtime, board, key):
    """The boards one tick of *key*'s session pass asks its gate about."""
    board.scans.clear()
    await _tick(_named(runtime, key.value))
    assert board.scans, key
    return {query.team_key for query in board.scans}


async def test_a_gated_session_pass_scans_only_the_per_issue_boards(tmp_path):
    config, operation, prompts = _mixed(tmp_path)
    second = operation.repos[1].url
    board = _board()
    runtime, _logs = await _boot(
        _gated(config),
        operation,
        prompts,
        board=board,
        runner=FakeAgentRunner(events=[]),
        queue=FakeJobQueue(),
    )
    v02_config, v02_operation, v02_prompts = _v02_only(tmp_path, operation)
    v02_board = _board()
    v02, _v02_logs = await _boot(
        _gated(v02_config),
        v02_operation,
        v02_prompts,
        board=v02_board,
        runner=FakeAgentRunner(events=[]),
        queue=FakeJobQueue(),
    )

    # The one intended difference: a session gate of the mixed deployment
    # never asks about the walked team's board, while with no scope row
    # primary is a per-issue team and its board is scanned too.
    for key in SESSION_PASSES:
        assert await _session_scans(runtime, board, key) == {"agent"}, key
        assert await _session_scans(v02, v02_board, key) == {"primary", "agent"}, key

    # Everything else is exactly as the deployment with no scope row schedules
    # it: cadence and budget of all three passes, and each session pass's
    # record identity and gate signals.
    for name in (
        f"dispatch:{second}",
        PromptKey.FIRE_PREP_PASS.value,
        PromptKey.GROOMING_PASS.value,
    ):
        mixed_entry, v02_entry = _named(runtime, name), _named(v02, name)
        assert mixed_entry.interval_seconds == v02_entry.interval_seconds, name
        assert mixed_entry.timeout_seconds == v02_entry.timeout_seconds, name
    for key in SESSION_PASSES:
        mixed_run = _named(runtime, key.value).run
        v02_run = _named(v02, key.value).run
        for keyword in ("kind", "key"):
            assert mixed_run.keywords[keyword] == v02_run.keywords[keyword], key
        assert mixed_run.keywords["key"] is key
        assert (
            mixed_run.keywords["gate"].signals
            == v02_run.keywords["gate"].signals
            == (PassSignal.issues_changed,)
        ), key


#: A third repository, bound to a team of its own that no scope row names.
THIRD_REPO = "https://example.invalid/example-org/third-repo"


async def test_the_unbound_repository_event_names_only_that_repositorys_walked_teams(
    tmp_path,
):
    """Two walked repositories, each with its own team, beside a per-issue one."""
    config, operation, _prompts = _mixed(tmp_path)
    fields = scope_only(operation).model_dump()
    fields["repos"].append({**fields["repos"][1], "url": THIRD_REPO})
    fields["teams"]["third"] = {
        **fields["teams"]["agent"],
        "name": "third-team",
        "key": "EXT",
        "repository": THIRD_REPO,
    }
    three = OperationConfig.model_validate(fields)
    first, second, third = (repo.url for repo in three.repos)
    assert three.scope_walked_teams() == ("primary", "agent")
    assert three.per_issue_teams() == ("third",)

    runtime, logs = await _boot(
        config,
        three,
        load_registry(default_set="claude-opus", bindings=operation_bindings(three)),
        board=_board(),
        runner=FakeAgentRunner(events=[]),
        queue=FakeJobQueue(),
    )

    unbound = _logged(logs, "dispatch_pass_unbound_repository")
    assert len(unbound) == 2
    assert {entry["repo_url"]: entry["scope_walked_teams"] for entry in unbound} == {
        first: ["primary"],
        second: ["agent"],
    }
    names = [entry.name for entry in runtime.scheduler.passes]
    assert f"dispatch:{third}" in names
    assert f"dispatch:{first}" not in names
    assert f"dispatch:{second}" not in names


class _RecordingPrompts:
    """The boot registry, recording every template preflight asks for."""

    def __init__(self, inner):
        self._inner = inner
        self.rendered: list[PromptKey] = []

    def template_for(self, key, *args, **kwargs):
        self.rendered.append(key)
        return self._inner.template_for(key, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


async def test_preflight_probes_and_renders_the_per_issue_passes_of_a_mixed_operation(
    tmp_path,
):
    """Preflight asks exactly what the wiring builds: the per-issue team's passes."""
    _, operation, prompts = _mixed(tmp_path)
    config = _config(
        tmp_path,
        organize={"max_admission_rounds": 2, "max_convergence_rounds": 2},
        write_back={"max_verify_rounds": 2},
        fire_prep_pass_gate_signals=[PassSignal.issues_changed],
        grooming_pass_gate_signals=[],
        dispatch_pass_gate_signals=[PassSignal.approved_changed],
        ticket_review_mode="reviewed",
    )
    board = FakeTrackerPort()
    recording = _RecordingPrompts(prompts)

    await verify_pass_preflight(
        config=config,
        operation=operation,
        tracker=board,
        github_api=FakeDeliveryProbe(),
        prompts=recording,
    )

    (probe,) = board.capability_probes
    assert set(probe) == {PassSignal.issues_changed, PassSignal.approved_changed}
    assert recording.rendered == [PromptKey.FIRE_PREP_PASS, PromptKey.GROOMING_PASS]


async def test_a_deployment_whose_every_team_is_walked_schedules_no_per_issue_pass(
    tmp_path,
):
    config, operation, prompts = _mixed(tmp_path)
    walked = scope_only(operation)
    assert walked.per_issue_teams() == ()

    runtime, logs = await _boot(
        config,
        walked,
        prompts,
        board=_board(),
        runner=FakeAgentRunner(events=[]),
        queue=FakeJobQueue(),
    )

    assert [entry.name for entry in runtime.scheduler.passes] == [
        "supervisor",
        ORGANIZE_PASS,
        HEARTBEAT_PASS,
    ]
    assert runtime.lifecycle is None
    for name in ("scheduled_passes_not_wired", "prompt_passes_not_wired"):
        (withheld,) = _logged(logs, name)
        assert withheld["organize_scopes_declared"] is True, name


# ---------------------------------------------------------------------------
# A per-issue run never reaches the scope walk.
# ---------------------------------------------------------------------------


class _RecordingArm:
    """An engine arm that records each run it is handed and emits nothing."""

    def __init__(self) -> None:
        self.runs: list[dict[str, object]] = []

    async def run(self, **kwargs: object) -> AsyncIterator[AgentEvent]:
        self.runs.append(kwargs)
        return
        yield


@pytest.mark.parametrize(
    ("origin", "forge_less"),
    [("https://github.com/o/r", False), ("file:///tmp/r", True)],
)
async def test_an_unscoped_run_reaches_its_origin_arm_while_the_scoped_arm_is_wired(
    origin, forge_less
):
    forge_arm, forge_less_arm = _RecordingArm(), _RecordingArm()
    router = OriginRoutedWorkflowEngine(
        forge_arm=forge_arm,
        forge_less_arm=forge_less_arm,
        scoped_arm=ForbiddenWorkflowEngine(),
    )

    async with asyncio.timeout(TICK_BOUND_SECONDS):
        events = [
            event
            async for event in router.run(
                prompt="fix it",
                issue_key="EXG-1",
                repo_path=None,
                repo_url=origin,
                base_spec=trunk_base("main"),
                scope=None,
                permission_mode=PermissionMode.UNATTENDED,
                allowed_tools=["Bash"],
                cache_key="unscoped",
            )
        ]

    assert events == []
    reached, idle = (
        (forge_less_arm, forge_arm) if forge_less else (forge_arm, forge_less_arm)
    )
    (run,) = reached.runs
    assert run["scope"] is None
    assert run["issue_key"] == "EXG-1"
    assert idle.runs == []


class _Graph:
    """A compiled-graph stand-in: the terminal it streams, or a refusal."""

    def __init__(self, events: list[AgentEvent] | None) -> None:
        self._events = events

    async def astream(self, *_args: object, **_kwargs: object):
        if self._events is None:
            raise AssertionError("an unscoped fire streamed the native graph")
        for event in self._events:
            yield event


async def test_an_unscoped_fire_streams_the_authored_graph_beside_a_native_graph(
    monkeypatch,
):
    fire = engine(criteria=TrackerCriteria(tracker=tracker()))
    assert fire.native_graph is not None
    terminal = WorkflowCompleteEvent(
        feature_branch="feature",
        ralph_branch="ralph",
        total_iterations=1,
        accepted=True,
        outcome=WorkflowOutcome.ci_passed,
    )
    monkeypatch.setattr(fire, "native_graph", _Graph(None))
    monkeypatch.setattr(fire, "graph", _Graph([terminal]))

    async with asyncio.timeout(TICK_BOUND_SECONDS):
        events = await drive(fire, scope=None)

    assert events == [terminal]


class _Untouchable:
    """A native graph the authored arm must never look at."""

    def __getattribute__(self, name: str) -> object:
        raise AssertionError("the authored arm reached the native graph")

    def __bool__(self) -> bool:
        raise AssertionError("the authored arm reached the native graph")


def test_the_authored_arm_never_reaches_the_native_graph():
    """Construction is the assertion.

    The coordinator compiles its graph when it is built, and the sentinel
    raises on any attribute read and on truth testing, so an arm that so much
    as looks at the native graph while wiring its fire node fails right here.
    """
    fire = engine(criteria=TrackerCriteria(tracker=tracker()))
    assert fire.native_graph is not None
    wired = _arm(forge=None)
    # Assigned rather than patched: the fire is this test's own, and the patch
    # helper itself inspects the value it installs.
    fire.native_graph = _Untouchable()  # the sentinel stands in for a graph

    AuthoredDeliveryCoordinator(
        fire=fire, publication=wired.publication, checks=wired.checks
    )
