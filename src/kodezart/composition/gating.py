"""Construction of the outbound content gate's scanner list.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from pathlib import Path

from kodezart.adapters.agent_content_scanner import AgentContentScanner
from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.core.config import AppConfig
from kodezart.core.errors import ContentScannerBootError
from kodezart.core.protocols import (
    AgentExecutor,
    ContentScanner,
    PromptProvider,
)
from kodezart.types.domain.gating import content_digest
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.skills import SkillsSelection


def outbound_scanners(
    *,
    config: AppConfig,
    operation: OperationConfig | None,
    executor: AgentExecutor,
    prompts: PromptProvider,
    skills: SkillsSelection,
) -> tuple[list[ContentScanner], str]:
    """The gate's ORDERED scanner list, and the fragment digest keying its memo.

    Deterministic first, always, and that ordering is the whole reason a
    credential is still caught when the judgment path is degraded.

    Three states, none silent.  Enabled with a private-surface description
    registers the judgment scanner; enabled without one aborts boot rather
    than registering a scanner whose every answer would be
    ``NOT_CONFIGURED``; disabled runs the deterministic scanners alone.
    """
    scanners: list[ContentScanner] = [
        RegexContentScanner(patterns=config.deny_patterns),
    ]
    if not config.agentic_content_scanner_enabled:
        return scanners, ""

    private_surface = None if operation is None else operation.private_surface
    if private_surface is None or not private_surface.strip():
        msg = "The judgment content scanner is enabled with nothing to judge against"
        raise ContentScannerBootError(msg, missing="OperationConfig.private_surface")

    working_dir = Path(config.content_audit_working_dir).expanduser()
    working_dir.mkdir(parents=True, exist_ok=True)
    scanners.append(
        AgentContentScanner(
            executor=executor,
            prompts=prompts,
            neutral_cwd=str(working_dir),
            skills=skills,
            retry_max_attempts=config.content_scan_retry_max_attempts,
            retry_initial_interval=config.content_scan_retry_initial_interval,
            timeout_seconds=config.content_scan_timeout_seconds,
        ),
    )
    return scanners, content_digest(private_surface)
