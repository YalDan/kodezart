"""Actual application ownership across startup, service and teardown failures."""

import asyncio
import traceback
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from kodezart import main
from kodezart.composition.jobs import build_job_queue
from kodezart.core.config import AppConfig
from kodezart.services.pass_scheduler import PassScheduler, ScheduledPass
from kodezart.types.domain.session import PermissionMode


class LifecycleError(Exception):
    pass


@pytest.fixture
def resources(monkeypatch):
    events = []
    cleanup_errors = []
    failures = set()
    transports = []
    queues = []
    schedulers = []

    async def step(name):
        events.append(name)
        if name in failures:
            raise LifecycleError(name)

    class Transport:
        def __init__(self, name):
            self.name = name
            self.closed = False
            transports.append(self)

        async def open(self):
            await step(self.name + ".open")

        async def close(self):
            self.closed = True
            await step(self.name + ".close")

    forge, tracker, knowledge = [
        Transport(name) for name in ("forge", "tracker", "knowledge")
    ]

    async def tracker_boot(**kwargs):
        await tracker.open()
        return SimpleNamespace(tracker=object(), caller=tracker, operation=None)

    async def recorder(**kwargs):
        await step("recorder")
        return SimpleNamespace(knowledge_caller=knowledge, recorder=object())

    def boot(name, value=None):
        async def invoke(**kwargs):
            await step(name)
            return value

        return invoke

    @asynccontextmanager
    async def checkpoint(url):
        await step("checkpoint.open")
        try:
            yield None
        finally:
            await step("checkpoint.close")

    def queue(**kwargs):
        result = build_job_queue(**kwargs)
        queues.append(result)
        original_start, original_stop = result.start, result.stop

        async def start():
            await original_start()
            await step("queue.start")

        async def stop():
            await original_stop()
            await step("queue.stop")

        result.start, result.stop = start, stop
        return result

    class Lifecycle:
        async def drain(self):
            assert not queues[-1]._accepting
            await step("lifecycle.drain")

        async def record_unfinished(self):
            assert not queues[-1]._accepting
            await step("lifecycle.records")

    async def dispatch(**kwargs):
        await step("dispatch")

        async def tick(now):
            raise AssertionError("the long-cadence fixture must not tick")

        scheduler = PassScheduler(
            passes=(
                ScheduledPass(
                    name="fixture", interval_seconds=3600, timeout_seconds=1, run=tick
                ),
            )
        )
        schedulers.append(scheduler)
        original_start, original_stop = scheduler.start, scheduler.stop

        async def start():
            await original_start()
            await step("scheduler.start")

        async def stop():
            await original_stop()
            await step("scheduler.stop")

        scheduler.start, scheduler.stop = start, stop
        return SimpleNamespace(scheduler=scheduler, lifecycle=Lifecycle())

    class Log:
        async def aerror(self, event, **kwargs):
            cleanup_errors.append((event, kwargs))

        async def ainfo(self, event, **kwargs):
            await step(event)

    monkeypatch.setattr(main, "configure_logging", lambda **_kwargs: None)
    monkeypatch.setattr(main, "get_logger", lambda _name: Log())
    monkeypatch.setattr(main, "build_forge_client", lambda **_kwargs: forge)
    monkeypatch.setattr(main, "boot_tracker", tracker_boot)
    monkeypatch.setattr(main, "boot_prompts", boot("prompts"))
    monkeypatch.setattr(main, "verify_pass_preflight", boot("preflight"))
    monkeypatch.setattr(main, "build_run_recorder", recorder)
    monkeypatch.setattr(main, "boot_skills", boot("skills"))
    monkeypatch.setattr(main, "boot_knowledge_grant", boot("knowledge_grant"))
    monkeypatch.setattr(main, "fire_record_template", lambda **_kwargs: None)
    monkeypatch.setattr(main, "ClaudeClientExecutor", lambda **_kwargs: None)
    monkeypatch.setattr(main, "build_outbound_gate", boot("gate"))
    monkeypatch.setattr(
        main,
        "build_git_stack",
        lambda **_kwargs: SimpleNamespace(
            **dict.fromkeys(
                (
                    "git",
                    "cache",
                    "workspace",
                    "persister",
                    "merger",
                    "artifact_persister",
                    "ref_publisher",
                )
            )
        ),
    )
    monkeypatch.setattr(main, "make_checkpointer", checkpoint)
    monkeypatch.setattr(main, "build_workflow_engine", lambda **_kwargs: object())
    monkeypatch.setattr(main, "build_job_queue", queue)

    def service(**kwargs):
        if "job_service" in failures:
            raise LifecycleError("job_service")
        return None

    monkeypatch.setattr(main, "build_job_service", service)
    monkeypatch.setattr(main, "build_dispatch_runtime", dispatch)
    app = FastAPI(lifespan=main.lifespan)
    app.state.config = AppConfig()
    return SimpleNamespace(
        app=app,
        events=events,
        cleanup_errors=cleanup_errors,
        failures=failures,
        transports=transports,
        queues=queues,
        schedulers=schedulers,
    )


RELEASES = (
    "scheduler.stop",
    "queue.stop",
    "lifecycle.drain",
    "lifecycle.records",
    "knowledge.close",
    "tracker.close",
    "forge.close",
)


def released(resources):
    return [event for event in resources.events if event in RELEASES]


async def settle_fixture(resources):
    # Before-controls must not strand the real background scheduler they expose.
    for scheduler in resources.schedulers:
        if scheduler.running:
            await scheduler.stop()
    for queue in resources.queues:
        if queue._accepting:
            await queue.stop()


@pytest.mark.parametrize(
    "failure",
    [
        "prompts",
        "preflight",
        "recorder",
        "knowledge.open",
        "skills",
        "knowledge_grant",
        "gate",
        "checkpoint.open",
        "queue.start",
        "job_service",
        "dispatch",
        "scheduler.start",
        "application_starting",
    ],
)
async def test_partial_startup_releases_every_resource_it_acquired(resources, failure):
    resources.failures.add(failure)
    try:
        with pytest.raises(LifecycleError, match=failure):
            async with resources.app.router.lifespan_context(resources.app):
                raise AssertionError("startup was expected to fail")
        assert resources.transports[0].closed and resources.transports[1].closed
        if "knowledge.open" in resources.events:
            assert resources.transports[2].closed
        assert all(not queue._accepting for queue in resources.queues)
        assert all(not scheduler.running for scheduler in resources.schedulers)
        assert len(released(resources)) == len(set(released(resources)))
    finally:
        resources.failures.clear()
        await settle_fixture(resources)


@pytest.mark.parametrize("exceptional", [False, True])
async def test_lifespan_exit_releases_once_in_dependency_order(resources, exceptional):
    observed = False
    try:
        try:
            async with resources.app.router.lifespan_context(resources.app):
                assert resources.queues[0]._accepting
                assert resources.schedulers[0].running
                if exceptional:
                    raise LifecycleError("serving")
        except LifecycleError as exc:
            observed = True
            assert exceptional and str(exc) == "serving"
        assert observed is exceptional
        assert released(resources) == list(RELEASES)
        assert all(transport.closed for transport in resources.transports)
        assert not resources.queues[0]._accepting
        assert not resources.schedulers[0].running
        assert resources.events.count("checkpoint.close") == 1
        assert resources.events.index("checkpoint.close") > resources.events.index(
            "queue.stop"
        )
    finally:
        await settle_fixture(resources)


@pytest.mark.parametrize("failure", (*RELEASES, "checkpoint.close"))
async def test_one_teardown_failure_still_attempts_every_required_release(
    resources, failure
):
    resources.failures.add(failure)
    try:
        with pytest.raises(LifecycleError, match=failure):
            async with resources.app.router.lifespan_context(resources.app):
                pass
        assert released(resources) == list(RELEASES)
        assert all(transport.closed for transport in resources.transports)
    finally:
        resources.failures.clear()
        await settle_fixture(resources)


@pytest.mark.parametrize("exceptional", [False, True])
async def test_multiple_cleanup_errors_remain_observable(resources, exceptional):
    failures = {
        "scheduler.stop",
        "lifecycle.drain",
        "checkpoint.close",
        "tracker.close",
    }
    resources.failures.update(failures)
    try:
        with pytest.raises(LifecycleError) as caught:
            async with resources.app.router.lifespan_context(resources.app):
                if exceptional:
                    raise LifecycleError("serving")
        if exceptional:
            rendered = "".join(traceback.format_exception(caught.value))
            assert "LifecycleError: serving" in rendered
        assert {details["error"] for _, details in resources.cleanup_errors} == failures
        assert len(resources.cleanup_errors) == len(failures)
        assert all(
            event == "application_cleanup_failed"
            and details["error_kind"] == "LifecycleError"
            and details["exc_info"] is True
            for event, details in resources.cleanup_errors
        )
        assert released(resources) == list(RELEASES)
        assert resources.events.count("checkpoint.close") == 1
    finally:
        resources.failures.clear()
        await settle_fixture(resources)


@pytest.mark.parametrize("exceptional", [False, True])
@pytest.mark.parametrize("failed_watch", [False, True])
async def test_actual_queue_watchers_finish_fire_records_before_transports_close(
    resources, monkeypatch, exceptional, failed_watch
):
    from kodezart.services.claim_heartbeat import ClaimHeartbeat
    from kodezart.services.lifecycle_watcher import LifecycleWatcher
    from kodezart.services.run_recorder import RunRecorder
    from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
    from kodezart.types.domain.branch import trunk_base
    from kodezart.types.domain.operation import DocumentSystem, LifecycleStage, RunKind
    from kodezart.types.domain.run_records import RunOutcome
    from kodezart.types.domain.workflow import WorkflowSubmission
    from tests.fakes import (
        FakeFireReport,
        FakeTrackerPort,
        PassThroughGate,
        RecordingLogSink,
        make_tracker_issue,
    )
    from tests.test_composition_root import (
        FINISHED,
        FIRE_LOG,
        HOLDER,
        KILLED,
        NEVER_RAN,
        PRE_CLAIM_STATE,
        REPO_URL,
        _ScriptedEngine,
        _TrackerGoneAtShutdown,
        _until,
    )

    keys = (FINISHED, KILLED, NEVER_RAN)
    issues = [make_tracker_issue(key) for key in keys]
    tracker = (
        _TrackerGoneAtShutdown(issues=issues, gone_for=KILLED)
        if failed_watch
        else FakeTrackerPort(issues=issues)
    )

    class Sink(RecordingLogSink):
        async def write_record(self, **kwargs):
            assert not any(resource.closed for resource in resources.transports)
            await super().write_record(**kwargs)

    sink = Sink()
    recorder = RunRecorder(
        records={RunKind.FIRE.value: FIRE_LOG}, sinks={DocumentSystem.KNOWLEDGE: sink}
    )

    async def build_recorder(**kwargs):
        return SimpleNamespace(
            recorder=recorder, knowledge_caller=resources.transports[2]
        )

    monkeypatch.setattr(main, "build_run_recorder", build_recorder)
    monkeypatch.setattr(
        main,
        "build_workflow_engine",
        lambda **_kwargs: _ScriptedEngine(finishing=FINISHED),
    )
    original_dispatch = main.build_dispatch_runtime
    watches = []

    async def dispatch(**kwargs):
        runtime = await original_dispatch(**kwargs)
        watch = LifecycleWatcher(
            queue=kwargs["queue"],
            registry=kwargs["registry"],
            writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
            heartbeat=ClaimHeartbeat(
                tracker=tracker, holder=HOLDER, lease_seconds=600, renewal_fraction=0.25
            ),
            recorder=kwargs["recorder"],
            report=FakeFireReport(),
        )
        watches.append(watch)
        runtime.lifecycle = watch
        return runtime

    monkeypatch.setattr(main, "build_dispatch_runtime", dispatch)
    resources.app.state.config = AppConfig(queue={"max_concurrent_runs_per_lane": 1})
    observed = False
    try:
        try:
            async with resources.app.router.lifespan_context(resources.app):
                queue, watch = resources.queues[0], watches[0]
                for key in keys:
                    job = await queue.submit(
                        lane="lane",
                        request=WorkflowSubmission(
                            prompt=key,
                            repo_path=None,
                            repo_url=REPO_URL,
                            base_spec=trunk_base("main"),
                            implied_base=None,
                            scope=None,
                            permission_mode=PermissionMode.UNATTENDED,
                            allowed_tools=["Read"],
                        ),
                    )
                    watch.follow(
                        issue_key=key,
                        job_id=job.job_id,
                        pre_claim_state=PRE_CLAIM_STATE,
                    )
                await _until(lambda: len(sink.writes) == 1)
                await _until(
                    lambda: (
                        (KILLED, LifecycleStage.IN_PROGRESS) in tracker.workflow_writes
                    )
                )
                if exceptional:
                    raise LifecycleError("serving")
        except LifecycleError as exc:
            observed = True
            assert exceptional and str(exc) == "serving"
        assert observed is exceptional
        assert {row.name: row.outcome for row in sink.writes} == {
            FINISHED: RunOutcome.COMPLETED,
            KILLED: RunOutcome.FAILED,
            NEVER_RAN: RunOutcome.NEVER_STARTED,
        }
        assert len(sink.writes) == 3
        assert not watches[0].following
        assert all(resource.closed for resource in resources.transports)
    finally:
        await settle_fixture(resources)
        for watch in watches:
            await watch.drain()


@pytest.mark.parametrize("cleanup_error", [False, True])
async def test_repeated_lifespan_cancellation_settles_the_actual_queue_worker(
    resources, monkeypatch, cleanup_error
):
    from kodezart.types.domain.agent import AssistantTextEvent
    from kodezart.types.domain.branch import trunk_base
    from kodezart.types.domain.workflow import WorkflowSubmission

    serving, worker_closing, release_worker, worker_closed = [
        asyncio.Event() for _ in range(4)
    ]

    class Engine:
        async def run(self, **kwargs):
            try:
                yield AssistantTextEvent(text="working", model="fixture")
                serving.set()
                await asyncio.Event().wait()
            finally:
                worker_closing.set()
                await release_worker.wait()
                worker_closed.set()

    monkeypatch.setattr(main, "build_workflow_engine", lambda **_kwargs: Engine())

    async def run():
        async with resources.app.router.lifespan_context(resources.app):
            await resources.queues[0].submit(
                lane="lane",
                request=WorkflowSubmission(
                    prompt="work",
                    repo_path=None,
                    repo_url="https://forge.invalid/repo",
                    base_spec=trunk_base("main"),
                    implied_base=None,
                    scope=None,
                    permission_mode=PermissionMode.UNATTENDED,
                    allowed_tools=["Read"],
                ),
            )
            await asyncio.Event().wait()

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(serving.wait(), 5)
        if cleanup_error:
            resources.failures.add("tracker.close")
        task.cancel()
        await asyncio.wait_for(worker_closing.wait(), 5)
        task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done()
        assert not any(resource.closed for resource in resources.transports)
        release_worker.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(task, 5)
        if cleanup_error:
            assert str(caught.value.__cause__) == "tracker.close"
        assert worker_closed.is_set()
        assert released(resources) == list(RELEASES)
    finally:
        resources.failures.clear()
        release_worker.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await settle_fixture(resources)


async def test_actual_lifespan_passes_queue_section_to_composed_workers(
    resources, monkeypatch
):
    from tests.api.v1.test_jobs import GatedWorkflowEngine, _request, _until

    engine = GatedWorkflowEngine()
    monkeypatch.setattr(main, "build_workflow_engine", lambda **_kwargs: engine)
    resources.app.state.config = AppConfig(queue={"max_concurrent_runs_per_lane": 2})
    async with resources.app.router.lifespan_context(resources.app):
        queue = resources.queues[0]
        for prompt in ["first", "second"]:
            await queue.submit(lane="same-lane", request=_request(prompt))
        await _until(lambda: engine.started == ["first", "second"])
    assert engine.finished == []


@pytest.mark.parametrize(
    "level,pretty", [("INFO", False), ("error", False), ("WARNING", True)]
)
async def test_actual_lifespan_forwards_logging_choices(
    resources, monkeypatch, level, pretty
):
    from kodezart.core.logging import configure_logging, get_logger
    from tests.core.test_logging_chain import configured_chain

    resources.app.state.config = AppConfig(logging={"level": level, "pretty": pretty})
    monkeypatch.setattr(main, "configure_logging", configure_logging)
    with configured_chain() as output:
        async with resources.app.router.lifespan_context(resources.app):
            logger = get_logger("fixture.logging_settings")
            logger.info("logging_info_probe")
            logger.warning("logging_warning_probe")
            logger.error("logging_error_probe")
        text = output.getvalue()
        assert ("logging_info_probe" in text) is (level.upper() == "INFO")
        assert ("logging_warning_probe" in text) is (level.upper() != "ERROR")
        assert "logging_error_probe" in text
        assert ("\x1b[" in text) is pretty
        assert ('"event": "logging_error_probe"' in text) is (not pretty)


async def test_actual_lifespan_forwards_git_section_and_resolves_shorthand(
    resources, monkeypatch
):
    from dataclasses import replace

    from kodezart.composition.workspace import build_git_stack as actual_stack
    from kodezart.types.domain.session import SessionType
    from tests.fakes import (
        SUPPRESS_ALL_SKILLS,
        FakeAgentExecutor,
        FakeWorkspaceProvider,
        PassThroughGate,
        make_prompt_provider,
    )

    monkeypatch.setenv(
        "KODEZART_GIT__BASE_URL", "https://configured.example.invalid/group"
    )
    monkeypatch.setenv("KODEZART_GIT__REMOTE", "configured")
    monkeypatch.setenv("KODEZART_GITHUB_TOKEN", "fixture-token")
    seen = []
    workspace = FakeWorkspaceProvider()

    def stack(*, settings, github_token, prompts, gate):
        seen.append((settings.model_dump(), github_token))
        actual = actual_stack(
            settings=settings,
            github_token=github_token,
            prompts=make_prompt_provider(),
            gate=PassThroughGate(),
        )
        return replace(actual, workspace=workspace)

    monkeypatch.setattr(main, "build_git_stack", stack)
    monkeypatch.setattr(
        main, "ClaudeClientExecutor", lambda **_kwargs: FakeAgentExecutor(events=[])
    )
    app = main.create_app()
    async with app.router.lifespan_context(app):
        async for _ in app.state.agent_service.stream(
            prompt="probe",
            repo_url="owner/repo",
            permission_mode=PermissionMode.INTERACTIVE,
            allowed_tools=[],
            skills=SUPPRESS_ALL_SKILLS,
            session_type=SessionType.API_QUERY,
        ):
            pass
    assert seen[0][0]["remote"] == "configured"
    assert seen[0][1] == "fixture-token"
    assert any(
        "https://configured.example.invalid/group/owner/repo.git" in call
        for call in workspace.calls
    )
