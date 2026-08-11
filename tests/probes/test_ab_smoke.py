"""KOD-93-AC-8 — the A/B smoke: one ticket, both shipped configurations.

The flip moves two defaults together, so the two arms are the two
COHERENT PAIRS (fire-time ruling FR-2 on KOD-93): the configuration a
deployment ran BEFORE this issue (the legacy set with a harness-level
reviewer) and the configuration it runs AFTER it (the v5 set with the
create-only ticket loop).  Holding one axis constant across the arms is
not runnable in one direction — the legacy set declares no draft-critic
lens, so create-only refuses at construction — and not representative in
the other, because nothing ships that pairing.

Both arms run the SAME ticket against their own copy of the SAME fixture
repository, whose origin is a bare repository on this filesystem.  No
forge client is wired: ``github_api`` is absent, so a run reaches its
terminal event through the post-merge review and can neither open a pull
request nor push anywhere but the directory the fixture created.

The four observations are the ones deliverable 5 names — iterations to
acceptance, whether the post-merge review passed, the terminal outcome
discriminator, and any structured-output failure — and each is read off
the emitted events rather than off a transcript.  This is EVIDENCE, not a
gate: a disagreement between the arms is a finding to record with the
events that produced it, never a reason to withhold a row.

Live only (``pytest -m live``): each arm dispatches real engine sessions.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
import structlog

from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.composition.engine import build_workflow_engine
from kodezart.composition.gating import build_outbound_gate
from kodezart.composition.knowledge import boot_knowledge_grant
from kodezart.composition.preflight import boot_skills
from kodezart.composition.prompts import boot_prompts
from kodezart.composition.workspace import build_git_stack
from kodezart.core.config import AppConfig
from kodezart.core.protocols import AgentExecutor, PromptSetProvider
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AgentEvent,
    ErrorEvent,
    WorkflowCompleteEvent,
    WorkflowIterationEvent,
    WorkflowReviewEvent,
    WorkflowTicketEvent,
)
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.ticket_review import TicketReviewMode
from kodezart.types.requests.agent import WorkflowRequest
from tests.probes.recording import record

pytestmark = pytest.mark.live

log: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

#: The one ticket both arms are asked to deliver.  Small and unambiguous
#: on purpose: the smoke measures two CONFIGURATIONS, so a task whose
#: difficulty dominated the difference would be measuring the task.
TICKET = (
    "Add a `subtract(a, b)` function to src/calc.py that returns a - b, "
    "and cover it with a unit test in tests/test_calc.py beside the "
    "existing addition test."
)

CALC_MODULE = '''"""Arithmetic helpers."""


def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b
'''

CALC_TEST = '''"""Tests for the arithmetic helpers."""

from src.calc import add


def test_add() -> None:
    assert add(2, 3) == 5
'''

README = """# calc

A small arithmetic module, used as the fixture repository of a workflow
smoke run.
"""

#: The review verdict has a third state no boolean carries: the node may
#: never be reached, which is a different fact from a failed review.
REVIEW_NOT_REACHED = "not reached"

#: Builds the executor a deployment on this arm runs with.  A parameter
#: rather than a constant because the harness is exercised end to end
#: with a scripted executor before it is trusted with a live engine.
ExecutorFactory = Callable[
    [AppConfig, PromptSetProvider],
    Awaitable[AgentExecutor],
]


@dataclass(frozen=True)
class Arm:
    """One coherent configuration pair, named by BOTH of its axes."""

    label: str
    prompt_set: str
    review_mode: TicketReviewMode


@dataclass(frozen=True)
class ArmObservation:
    """What one arm's run emitted, read off its events."""

    arm: Arm
    resolution_table_sets: tuple[str, ...]
    iterations: int
    iteration_verdicts: tuple[str, ...]
    accepted: bool
    merged: bool
    ticket_review_rounds: int | None
    review_passed: str
    outcome: str
    structured_output_failures: tuple[str, ...]
    error_kinds: tuple[str, ...]
    seconds: float


ARMS: tuple[Arm, ...] = (
    Arm(
        label="before the flip",
        prompt_set="claude-opus",
        review_mode=TicketReviewMode.REVIEWED,
    ),
    Arm(
        label="after the flip",
        prompt_set="anthropic_v5",
        review_mode=TicketReviewMode.CREATE_ONLY,
    ),
)


async def _git(cmd: Sequence[str], cwd: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = f"{' '.join(cmd)} failed: {stderr.decode()}"
        raise RuntimeError(msg)


async def fixture_repo(root: Path) -> Path:
    """A working repository whose origin is a bare repository beside it.

    The remote is a filesystem path, and that is what keeps the smoke off
    every forge: a run that pushes reaches this directory and nothing
    else, and a run that tried to reach a hosted origin would fail here
    rather than succeed somewhere public.
    """
    repo = root / "repo"
    bare = root / "origin.git"
    repo.mkdir(parents=True)
    bare.mkdir(parents=True)
    (root / "cache").mkdir(parents=True)

    await _git(["git", "init", "-b", "main"], cwd=repo)
    await _git(["git", "config", "commit.gpgsign", "false"], cwd=repo)
    await _git(["git", "init", "--bare", "-b", "main"], cwd=bare)
    await _git(["git", "remote", "add", "origin", str(bare)], cwd=repo)

    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "README.md").write_text(README)
    (repo / "src" / "calc.py").write_text(CALC_MODULE)
    (repo / "tests" / "test_calc.py").write_text(CALC_TEST)

    await _git(["git", "add", "-A"], cwd=repo)
    await _git(["git", "commit", "-m", "the fixture repository"], cwd=repo)
    await _git(["git", "push", "-u", "origin", "main"], cwd=repo)
    return repo


async def production_executor(
    config: AppConfig,
    prompts: PromptSetProvider,
) -> AgentExecutor:
    """The executor the composition root wires, built from the arm's config."""
    return ClaudeClientExecutor(
        model=config.model,
        setting_sources=config.setting_sources,
        knowledge_grant=await boot_knowledge_grant(
            config=config,
            prompts=prompts,
            log=log,
        ),
    )


def arm_environment(arm: Arm, root: Path) -> dict[str, str]:
    """The environment a deployment on this arm actually runs under.

    Every axis is a configuration variable rather than a constructor
    argument, so the arm measures a CONFIGURED deployment: an axis that
    stopped reaching the graph would change the observation.
    """
    return {
        "KODEZART_PROMPT_SET": arm.prompt_set,
        "KODEZART_TICKET_REVIEW_MODE": arm.review_mode.value,
        "KODEZART_CLONE_CACHE_DIR": str(root / "cache"),
        "KODEZART_INTEGRATION_WORKSPACE_DIR": str(root / "integration"),
    }


def _classify_review(events: Sequence[AgentEvent]) -> str:
    reviews = [event for event in events if isinstance(event, WorkflowReviewEvent)]
    if not reviews:
        return REVIEW_NOT_REACHED
    return "passed" if reviews[-1].passed else "failed"


def observe(
    *,
    arm: Arm,
    events: Sequence[AgentEvent],
    resolution_sets: tuple[str, ...],
    seconds: float,
) -> ArmObservation:
    """One arm's observations, every one of them read off the events."""
    completes = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    if len(completes) != 1:
        msg = f"{arm.label}: expected one terminal event, saw {len(completes)}"
        raise AssertionError(msg)
    complete = completes[0]
    iterations = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    tickets = [e for e in events if isinstance(e, WorkflowTicketEvent)]
    errors = [e for e in events if isinstance(e, ErrorEvent)]
    return ArmObservation(
        arm=arm,
        resolution_table_sets=resolution_sets,
        iterations=complete.total_iterations,
        iteration_verdicts=tuple(str(event.verdict) for event in iterations),
        accepted=complete.accepted,
        merged=complete.merged,
        ticket_review_rounds=tickets[-1].review_rounds if tickets else None,
        review_passed=_classify_review(events),
        outcome=str(complete.outcome),
        structured_output_failures=tuple(
            event.error for event in errors if event.error_kind is not None
        ),
        error_kinds=tuple(event.error_kind or "unclassified" for event in errors),
        seconds=seconds,
    )


async def run_arm(
    *,
    arm: Arm,
    root: Path,
    executor_for: ExecutorFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> ArmObservation:
    """Compose the deployment this arm describes, run the ticket through it.

    Composition goes through the same helpers the application boots with,
    so the arm is a deployment rather than a hand-assembled graph.  Two
    collaborators are deliberately absent: no forge client, so the run
    terminates after the post-merge review and nothing reaches a hosted
    origin, and no checkpointer, because a smoke measures one pass.
    """
    for name, value in arm_environment(arm, root).items():
        monkeypatch.setenv(name, value)

    config = AppConfig()
    if config.prompt_set != arm.prompt_set:
        msg = f"{arm.label}: the prompt set did not reach the configuration"
        raise AssertionError(msg)
    if config.ticket_review_mode is not arm.review_mode:
        msg = f"{arm.label}: the review mode did not reach the configuration"
        raise AssertionError(msg)

    prompts = await boot_prompts(config=config, operation=None, log=log)
    skills = await boot_skills(config=config, prompts=prompts, log=log)
    resolution_sets = tuple(sorted(set(prompts.resolution_table().values())))
    executor = await executor_for(config, prompts)

    gate = await build_outbound_gate(
        config=config,
        operation=None,
        executor=executor,
        prompts=prompts,
        skills=skills,
        log=log,
    )
    stack = build_git_stack(config=config, prompts=prompts, gate=gate)
    engine = build_workflow_engine(
        config=config,
        agent_service=AgentService(
            executor=executor,
            workspace=stack.workspace,
            persister=stack.persister,
            git_base_url=config.git_base_url,
        ),
        git=stack.git,
        cache=stack.cache,
        workspace=stack.workspace,
        merger=stack.merger,
        artifact_persister=stack.artifact_persister,
        ref_publisher=stack.ref_publisher,
        prompts=prompts,
        skills=skills,
        gate=gate,
        github_api=None,
        checkpointer=None,
    )

    repo = await fixture_repo(root)
    request = WorkflowRequest(prompt=TICKET, repo_path=str(repo))
    events: list[AgentEvent] = []
    started = time.monotonic()
    async for event in engine.run(
        prompt=request.prompt,
        repo_path=request.repo_path,
        repo_url=request.repo_url,
        base_spec=trunk_base(request.base_branch),
        permission_mode=request.permission_mode,
        allowed_tools=request.allowed_tools,
        cache_key=uuid.uuid4().hex,
    ):
        events.append(event)
    seconds = time.monotonic() - started
    await log.ainfo(
        "ab_smoke_arm_finished",
        arm=arm.label,
        prompt_set=arm.prompt_set,
        review_mode=arm.review_mode.value,
        events=len(events),
        seconds=round(seconds, 1),
    )
    return observe(
        arm=arm,
        events=events,
        resolution_sets=resolution_sets,
        seconds=seconds,
    )


def render_pair(observations: Sequence[ArmObservation]) -> str:
    """The paired summary: one column per arm, both axes named on it."""
    columns = [
        ("prompt set", [o.arm.prompt_set for o in observations]),
        ("ticket review mode", [o.arm.review_mode.value for o in observations]),
        (
            "boot resolution table",
            [", ".join(o.resolution_table_sets) for o in observations],
        ),
        ("iterations to acceptance", [str(o.iterations) for o in observations]),
        (
            "iteration verdicts",
            [", ".join(o.iteration_verdicts) or "none" for o in observations],
        ),
        ("accepted", [str(o.accepted) for o in observations]),
        ("merged", [str(o.merged) for o in observations]),
        (
            "ticket review rounds",
            [str(o.ticket_review_rounds) for o in observations],
        ),
        ("post-merge review", [o.review_passed for o in observations]),
        ("terminal outcome", [o.outcome for o in observations]),
        (
            "structured-output failures",
            [", ".join(o.structured_output_failures) or "none" for o in observations],
        ),
        (
            "error events",
            [", ".join(o.error_kinds) or "none" for o in observations],
        ),
        ("wall clock (s)", [str(round(o.seconds)) for o in observations]),
    ]
    rows = [
        "| Observation | " + " | ".join(o.arm.label for o in observations) + " |",
        "| --- | " + " | ".join("---" for _ in observations) + " |",
    ]
    rows.extend(f"| {name} | " + " | ".join(values) + " |" for name, values in columns)
    return "\n".join(rows)


async def test_ab_smoke_runs_one_ticket_through_both_shipped_pairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """One ticket, both coherent pairs, four observations each.

    The assertions are deliberately thin — that each arm reached a single
    terminal event (``observe`` refuses otherwise) and ran under the
    corpus its arm names.  Everything else is RECORDED: the issue body
    makes this evidence rather than a gate, so an arm that ends
    unaccepted produces a row to carry to the tracker rather than a
    failure to hide behind.
    """
    observations = [
        await run_arm(
            arm=arm,
            root=tmp_path / arm.prompt_set,
            executor_for=production_executor,
            monkeypatch=monkeypatch,
        )
        for arm in ARMS
    ]

    for observation in observations:
        assert observation.resolution_table_sets == (observation.arm.prompt_set,)

    for observation in observations:
        record(
            probe=f"KOD-93-AC-8 A/B smoke — {observation.arm.label}",
            question="Same ticket, both shipped pairs: what does each deliver?",
            configuration=(
                f"{observation.arm.prompt_set} + {observation.arm.review_mode.value}"
            ),
            observed=(
                f"iterations={observation.iterations}; "
                f"accepted={observation.accepted}; merged={observation.merged}; "
                f"post-merge review={observation.review_passed}; "
                f"outcome={observation.outcome}; "
                f"structured-output failures="
                f"{len(observation.structured_output_failures)}"
            ),
            verdict="recorded",
        )
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line("\n" + render_pair(observations))
