"""Construct the scope heartbeat."""

from pathlib import Path

from kodezart.config.app import AppConfig
from kodezart.core.protocols import (
    AgentRunner,
    JobQueue,
    JobRegistry,
    PromptSetProvider,
)
from kodezart.services.agent_question import ask
from kodezart.services.scope_heartbeat import ScopeHeartbeat
from kodezart.types.domain.agent import ScopeScanOutput
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.skills import SkillsSelection


def scope_heartbeat_wires(operation: OperationConfig, *, tracker_present: bool) -> bool:
    """Whether the heartbeat wires: a dialled tracker and approval labels to scan for.

    Named once: the wiring builds on it, and so does the preflight that
    renders the scan and the done question at boot.
    """
    return tracker_present and bool(operation.scope_labels)


def build_scope_heartbeat(
    *,
    config: AppConfig,
    operation: OperationConfig,
    tracker_present: bool,
    queue: JobQueue,
    registry: JobRegistry,
    runner: AgentRunner,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
) -> ScopeHeartbeat | None:
    """The heartbeat over *operation*'s board, or ``None`` where it does not wire.

    Its scan is the shared question on the scope_scan key, asked in the
    scheduled passes' working directory, which is no cloned repository; the
    session reads the board through the tracker server its kind is given. What
    the heartbeat holds besides is the two ports it submits through and each
    declared repository's trunk.
    """
    if not scope_heartbeat_wires(operation, tracker_present=tracker_present):
        return None
    working_dir = Path(config.scheduled_pass_working_dir).expanduser()
    working_dir.mkdir(parents=True, exist_ok=True)

    async def scan() -> ScopeScanOutput | None:
        return await ask(
            runner=runner,
            prompts=prompts,
            skills=skills,
            workspace_path=str(working_dir),
            key=PromptKey.SCOPE_SCAN,
            bindings={},
            answer=ScopeScanOutput,
        )

    return ScopeHeartbeat(
        ask=scan,
        registry=registry,
        queue=queue,
        trunks={repo.url: repo.trunk for repo in operation.repos},
    )
