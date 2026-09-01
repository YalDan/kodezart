"""Workflow-level gating: visibility resolution and writer coverage (KOD-47)."""

import uuid

import pytest

from kodezart.adapters.pattern_outbound_gate import PatternOutboundContentGate
from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.core.config import AppConfig
from kodezart.domain.errors import OutboundContentBlockedError
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AgentEvent,
    WorkflowPREvent,
    WorkflowVisibilityEvent,
)
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.gating import (
    RedactionCategory,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.skills import SkillsMode, SkillsSelection
from tests.fakes import (
    FakeAgentExecutor,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeChangePersister,
    FakeCIMonitor,
    FakeGitService,
    FakePRCreator,
    FakeQualityGate,
    FakeRepoCache,
    FakeTicketGenerator,
    FakeVisibilityResolver,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_passing_evaluation,
    make_prompt_provider,
)


def make_engine(
    *,
    gate: object,
    visibility_resolver: object | None = None,
    pr_creator: FakePRCreator | None = None,
    ci_monitor: FakeCIMonitor | None = None,
    artifact_persister: FakeArtifactPersister | None = None,
    executor: FakeAgentExecutor | None = None,
) -> RalphWorkflowEngine:
    """Build a workflow engine wired to fakes, with a real gate."""
    service = AgentService(
        git_base_url="https://github.com",
        executor=executor or FakeAgentExecutor(events=[]),
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    return RalphWorkflowEngine(
        service=service,
        quality_gate=FakeQualityGate(
            events=[],
            evaluation=make_passing_evaluation(),
            last_commit_sha="a" * 40,
        ),
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        prompts=make_prompt_provider(),
        skills=SkillsSelection(mode=SkillsMode.NONE),
        gate=gate,
        visibility_resolver=visibility_resolver,
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
        artifact_persister=artifact_persister,
        retry_max_attempts=3,
        retry_initial_interval=1.0,
        remediation_max_rounds=1,
        criteria_max_regeneration_rounds=1,
        fan_in_max_attempts=2,
    )


async def run_engine(
    engine: RalphWorkflowEngine,
    *,
    repo_url: str | None = "https://github.com/owner/repo",
    repo_path: str | None = None,
) -> list[AgentEvent]:
    """Drive a full workflow run and collect its events."""
    return [
        event
        async for event in engine.run(
            prompt="do the thing",
            repo_path=repo_path,
            repo_url=repo_url,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]


# ---------------------------------------------------------------------------
# AC-1 / R-5 — visibility resolved once, in the first node
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("visibility", list(RepoVisibility))
async def test_visibility_matrix_is_resolved_and_observable(
    visibility: RepoVisibility,
) -> None:
    """Every visibility is carried in state and emitted as an event."""
    resolver = FakeVisibilityResolver(visibility)
    engine = make_engine(gate=PassThroughGate(), visibility_resolver=resolver)
    events = await run_engine(engine)
    emitted = [e for e in events if isinstance(e, WorkflowVisibilityEvent)]
    assert len(emitted) == 1
    assert emitted[0].visibility is visibility


async def test_visibility_resolves_exactly_once_per_run() -> None:
    """R-5: one resolution per run, in a dedicated first node."""
    resolver = FakeVisibilityResolver(RepoVisibility.PUBLIC)
    engine = make_engine(gate=PassThroughGate(), visibility_resolver=resolver)
    await run_engine(engine)
    assert len(resolver.calls) == 1


async def test_visibility_resolves_strictly_before_branch_name_generation() -> None:
    """The gate must know the visibility before the first gated writer runs."""
    resolver = FakeVisibilityResolver(RepoVisibility.PUBLIC)
    recording_gate = PassThroughGate()
    engine = make_engine(gate=recording_gate, visibility_resolver=resolver)
    events = await run_engine(engine)

    visibility_index = next(
        i for i, e in enumerate(events) if isinstance(e, WorkflowVisibilityEvent)
    )
    assert visibility_index == 0
    assert recording_gate.calls
    assert recording_gate.calls[0][2] is WriterShape.IDENTIFIER
    assert recording_gate.calls[0][1] is RepoVisibility.PUBLIC


# ---------------------------------------------------------------------------
# AC-8 — fail-closed, with no exemption
# ---------------------------------------------------------------------------


async def test_resolution_failure_yields_unknown_and_the_run_continues() -> None:
    """A resolver that could not decide yields UNKNOWN — the run is not skipped."""
    resolver = FakeVisibilityResolver(RepoVisibility.UNKNOWN)
    recording_gate = PassThroughGate()
    engine = make_engine(
        gate=recording_gate,
        visibility_resolver=resolver,
        pr_creator=FakePRCreator(),
    )
    events = await run_engine(engine)
    emitted = [e for e in events if isinstance(e, WorkflowVisibilityEvent)]
    assert emitted[0].visibility is RepoVisibility.UNKNOWN
    assert any(isinstance(e, WorkflowPREvent) for e in events)
    assert all(call[1] is RepoVisibility.UNKNOWN for call in recording_gate.calls)


async def test_tokenless_deployment_is_unknown_and_keeps_the_gate_engaged() -> None:
    """No forge client is UNKNOWN, not an exemption."""
    recording_gate = PassThroughGate()
    engine = make_engine(gate=recording_gate, visibility_resolver=None)
    events = await run_engine(engine)
    emitted = [e for e in events if isinstance(e, WorkflowVisibilityEvent)]
    assert emitted[0].visibility is RepoVisibility.UNKNOWN
    assert all(call[1] is RepoVisibility.UNKNOWN for call in recording_gate.calls)


async def test_local_only_run_is_unknown_and_keeps_the_gate_engaged() -> None:
    """A repo_path-only run has no remote to ask, so it is UNKNOWN."""
    resolver = FakeVisibilityResolver(RepoVisibility.PRIVATE)
    recording_gate = PassThroughGate()
    engine = make_engine(gate=recording_gate, visibility_resolver=resolver)
    events = await run_engine(engine, repo_url=None, repo_path="/tmp/fake")
    emitted = [e for e in events if isinstance(e, WorkflowVisibilityEvent)]
    assert emitted[0].visibility is RepoVisibility.UNKNOWN
    assert resolver.calls == []
    assert recording_gate.calls


# ---------------------------------------------------------------------------
# AC-2 / AC-6 — every writer routes through the gate
# ---------------------------------------------------------------------------


async def test_every_workflow_writer_routes_through_the_gate() -> None:
    """The corrected inventory: branch name, both artifacts, PR title/body, comment."""
    seen: list[str] = []

    class RecordingGate(PassThroughGate):
        async def gate(self, *, content, visibility, shape, destination, content_class):
            seen.append(content)
            return await super().gate(
                content=content,
                visibility=visibility,
                shape=shape,
                destination=destination,
                content_class=content_class,
            )

    engine = make_engine(
        gate=RecordingGate(),
        visibility_resolver=FakeVisibilityResolver(RepoVisibility.PUBLIC),
        pr_creator=FakePRCreator(),
        ci_monitor=FakeCIMonitor(passed=False, summary="ci failed"),
        artifact_persister=FakeArtifactPersister(),
    )
    await run_engine(engine)

    joined = "\n".join(seen)
    assert "test-branch" in joined or "scripted-branch" in joined
    assert any('"title"' in payload for payload in seen)
    assert any('"conjunction"' in payload for payload in seen)
    assert any("kodezart: remediation budget exhausted" in p for p in seen)


@pytest.mark.parametrize("visibility", list(RepoVisibility))
async def test_writer_matrix_over_every_visibility(
    visibility: RepoVisibility,
) -> None:
    """AC-6: the full visibility x writer matrix runs clean unconfigured."""
    config = AppConfig()
    gate = PatternOutboundContentGate(
        scanners=[RegexContentScanner(patterns=config.deny_patterns)],
        verdicts=config.deny_pattern_verdicts,
    )
    engine = make_engine(
        gate=gate,
        visibility_resolver=FakeVisibilityResolver(visibility),
        pr_creator=FakePRCreator(),
        artifact_persister=FakeArtifactPersister(),
    )
    events = await run_engine(engine)
    assert any(isinstance(e, WorkflowPREvent) for e in events)


async def test_blocked_write_fails_loudly_and_posts_nothing() -> None:
    """BLOCKED raises the typed error; the forge client is never called."""
    gate = PatternOutboundContentGate(
        scanners=[
            RegexContentScanner(
                patterns={RedactionCategory.INFRA_ENDPOINTS: [r"test-branch"]},
            )
        ],
        verdicts=AppConfig().deny_pattern_verdicts,
    )
    pr_creator = FakePRCreator()
    engine = make_engine(
        gate=gate,
        visibility_resolver=FakeVisibilityResolver(RepoVisibility.PUBLIC),
        pr_creator=pr_creator,
    )
    with pytest.raises(OutboundContentBlockedError) as excinfo:
        await run_engine(engine)
    assert excinfo.value.writer == "branch_name"
    assert pr_creator.calls == []


async def test_private_target_sees_no_behavioral_change_beyond_the_events() -> None:
    """AC-4: a private target's payloads are untouched."""
    recording_gate = PassThroughGate()
    engine = make_engine(
        gate=recording_gate,
        visibility_resolver=FakeVisibilityResolver(RepoVisibility.PRIVATE),
        pr_creator=FakePRCreator(),
    )
    events = await run_engine(engine)
    assert all(call[1] is RepoVisibility.PRIVATE for call in recording_gate.calls)
    pr_events = [e for e in events if isinstance(e, WorkflowPREvent)]
    assert pr_events
