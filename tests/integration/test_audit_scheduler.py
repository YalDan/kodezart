"""The real composition root registers or explicitly declines the audit pass."""

import structlog.testing

from kodezart.composition.passes import build_dispatch_runtime
from kodezart.core.config import AppConfig
from kodezart.core.logging import get_logger
from kodezart.services.run_recorder import RunRecorder
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
    FakeGitService,
    FakeJobQueue,
    FakeRepoCache,
    FakeWorkspaceProvider,
    PassThroughGate,
)
from tests.prompts.test_prompt_wiring import load_registry


async def test_absent_audit_configuration_has_a_named_runtime_event():
    queue = FakeJobQueue()
    with structlog.testing.capture_logs() as events:
        runtime = await build_dispatch_runtime(
            config=AppConfig(audit_sweep_interval_seconds=3777.0),
            operation=None,
            dialled=None,
            github_api=None,
            queue=queue,
            registry=queue,
            gate=PassThroughGate(),
            git=FakeGitService(),
            cache=FakeRepoCache(),
            workspace=FakeWorkspaceProvider(),
            prompts=load_registry(),
            runner=FakeAgentRunner([]),
            skills=SUPPRESS_ALL_SKILLS,
            recorder=RunRecorder(records={}, sinks={}),
            log=get_logger(__name__),
        )
    assert not [entry for entry in runtime.scheduler.passes if entry.name == "audit"]
    absent = [event for event in events if event["event"] == "audit_pass_not_wired"]
    assert len(absent) == 1
    assert absent[0]["reason"] == "audit_unconfigured"
