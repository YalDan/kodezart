"""Construction of the outbound content gate's scanner list.

Local credentials and typed references precede authored text judgment.
"""

from pathlib import Path

from kodezart.adapters.agent_content_scanner import AgentContentScanner
from kodezart.adapters.pattern_outbound_gate import PatternOutboundContentGate
from kodezart.adapters.reference_content_scanner import ReferenceContentScanner
from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.core.backoff import RetryPolicy
from kodezart.core.config import AppConfig
from kodezart.core.errors import ContentScannerBootError
from kodezart.core.logging import BoundLogger
from kodezart.core.protocols import (
    AgentExecutor,
    ContentScanner,
    PromptSetProvider,
)
from kodezart.types.domain.gating import content_digest
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.privacy import PrivateSurface
from kodezart.types.domain.skills import SkillsSelection


def outbound_scanners(
    *,
    config: AppConfig,
    operation: OperationConfig | None,
    executor: AgentExecutor,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
) -> tuple[list[ContentScanner], str]:
    """The gate's ORDERED scanner list, and the fragment digest keying its memo.

    Deterministic first, always, and that ordering is the whole reason a
    credential is still caught when the judgment path is degraded.

    Authored aggregate judgment is always registered. Organization-privacy
    judgment uses the existing opt-in and requires a private-surface
    description when enabled. PRIVATE targets retain the gate fast path.
    """
    private_surface = None if operation is None else operation.private_surface
    scanners: list[ContentScanner] = [
        RegexContentScanner(patterns=config.deny_patterns),
        ReferenceContentScanner(private_surface=private_surface or PrivateSurface()),
    ]
    if config.agentic_content_scanner_enabled and (
        private_surface is None or not private_surface.description.strip()
    ):
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
            retry=RetryPolicy(
                attempts=config.content_scan_retry_max_attempts,
                initial_delay=config.content_scan_retry_initial_interval,
            ),
            timeout_seconds=config.content_scan_timeout_seconds,
            inspect_privacy=config.agentic_content_scanner_enabled,
        ),
    )
    return scanners, content_digest(
        "" if private_surface is None else private_surface.model_dump_json()
    )


async def build_outbound_gate(
    *,
    config: AppConfig,
    operation: OperationConfig | None,
    executor: AgentExecutor,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
    log: BoundLogger,
) -> PatternOutboundContentGate:
    """The gate every outbound path runs through, and the record of its shape.

    Which scanners answered is logged at boot rather than inferred from a
    verdict later: a gate running one scanner and a gate running two are
    indistinguishable from the outside until the one that would have caught
    something is the one that is missing.
    """
    scanners, fragment_digest = outbound_scanners(
        config=config,
        operation=operation,
        executor=executor,
        prompts=prompts,
        skills=skills,
    )
    await log.ainfo(
        "outbound_content_scanners_resolved",
        scanners=[type(scanner).__name__ for scanner in scanners],
        privacy_judgment_enabled=config.agentic_content_scanner_enabled,
    )
    return PatternOutboundContentGate(
        scanners=scanners,
        verdicts=config.deny_pattern_verdicts,
        fragment_digest=fragment_digest,
    )
