"""Construct the fixed local and semantic outbound admission boundary."""

from pathlib import Path

from kodezart.adapters.agent_content_scanner import AgentContentScanner
from kodezart.adapters.outbound_admission import OutboundAdmission
from kodezart.adapters.reference_content_scanner import ReferenceContentScanner
from kodezart.core.backoff import RetryPolicy
from kodezart.core.config import AppConfig
from kodezart.core.errors import ContentScannerBootError
from kodezart.core.logging import BoundLogger
from kodezart.core.protocols import AgentExecutor, PromptSetProvider
from kodezart.types.domain.gating import content_digest
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.privacy import PrivateSurface
from kodezart.types.domain.skills import SkillsSelection


async def build_outbound_gate(
    *,
    config: AppConfig,
    operation: OperationConfig | None,
    executor: AgentExecutor,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
    log: BoundLogger,
) -> OutboundAdmission:
    """Credentials and native facts always precede authored text judgment."""
    private_surface = None if operation is None else operation.private_surface
    if config.agentic_content_scanner_enabled and (
        private_surface is None or not private_surface.description.strip()
    ):
        msg = "The judgment content scanner is enabled with nothing to judge against"
        raise ContentScannerBootError(msg, missing="OperationConfig.private_surface")
    references = ReferenceContentScanner(
        private_surface=private_surface or PrivateSurface()
    )
    working_dir = Path(config.content_audit_working_dir).expanduser()
    working_dir.mkdir(parents=True, exist_ok=True)
    judgment = AgentContentScanner(
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
    )
    await log.ainfo(
        "outbound_content_admission_resolved",
        privacy_judgment_enabled=config.agentic_content_scanner_enabled,
    )
    return OutboundAdmission(
        references=references,
        judgment=judgment,
        fragment_digest=content_digest(
            "" if private_surface is None else private_surface.model_dump_json()
        ),
    )
