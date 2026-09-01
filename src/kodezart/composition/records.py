"""Composition of the run recorder — sinks by system, reports by kind.

One recorder for the process, because the record registry is one
configuration surface: the pass scheduler and the lifecycle watcher both
report into it, and which backing system serves which kind is read off
the declared entries rather than decided per producer (KOD-170).

The sinks are built from what the deployment can actually dial: the
tracker sink rides the SAME transport the tracker adapter holds, and the
knowledge sink dials the SAME server definition granted sessions ride —
programmatically, with no model in the loop.  A knowledge-side record
declared by an operation whose deployment configures no knowledge server
is a boot refusal naming both halves, never a row that silently fails at
three in the morning.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from kodezart.adapters.http_mcp_tool_caller import HttpMcpToolCaller
from kodezart.adapters.linear_record_sink import LinearRecordSink
from kodezart.adapters.notion_record_sink import NotionRecordSink
from kodezart.adapters.stdio_mcp_tool_caller import StdioMcpToolCaller
from kodezart.core.config import AppConfig
from kodezart.core.errors import PassKnowledgeCapabilityError
from kodezart.core.logging import BoundLogger
from kodezart.core.protocols import McpToolCaller, RunRecordSink
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.operation import (
    DocumentSystem,
    OperationConfig,
    RunKind,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_records import RunOutcome, RunRecord
from kodezart.types.domain.session import KnowledgeTransport

#: Which record kind each scheduled prompt pass reports as.  The dispatch
#: scans carry no kind: their outcome is the fire they start, and the fire
#: reports itself through the lifecycle watcher.
RECORD_KIND_BY_PASS: dict[PromptKey, RunKind] = {
    PromptKey.FIRE_PREP_PASS: RunKind.FIRE_PREP,
    PromptKey.GROOMING_PASS: RunKind.GROOMING,
}


@dataclass(frozen=True)
class BuiltRecorder:
    """The recorder plus the transport built for it, for lifecycle hands.

    ``knowledge_caller`` is the session this composition OPENED and the
    shutdown must close; ``None`` when no knowledge-side record is
    declared.  The tracker-side transport is the tracker's own and is
    closed where it was opened.
    """

    recorder: RunRecorder
    knowledge_caller: StdioMcpToolCaller | HttpMcpToolCaller | None


def run_report(
    recorder: RunRecorder,
    kind: RunKind,
    name: str,
) -> Callable[[RunOutcome, float, datetime], Awaitable[None]]:
    """The report callback binding one pass to its kind's declared log."""

    async def report(
        outcome: RunOutcome,
        duration_seconds: float,
        started_at: datetime,
    ) -> None:
        await recorder.record(
            RunRecord(
                kind=kind,
                name=name,
                outcome=outcome,
                duration_seconds=duration_seconds,
                started_at=started_at,
                recorded_at=datetime.now(UTC),
            ),
        )

    return report


async def build_run_recorder(
    *,
    config: AppConfig,
    operation: OperationConfig | None,
    tracker_caller: McpToolCaller | None,
    log: BoundLogger,
) -> BuiltRecorder:
    """The recorder over every sink this deployment can serve.

    A declared system with no transport to serve it splits by who is
    responsible: a TRACKER record without a dialled tracker is a legal
    degraded mode this deployment chose (the same one that runs no
    dispatch), so it is NAMED here and every write refuses loudly; a
    KNOWLEDGE record without a knowledge server is a configuration
    contradiction nothing downstream can repair, so it refuses boot.
    """
    records = {} if operation is None else dict(operation.records)
    sinks: dict[DocumentSystem, RunRecordSink] = {}
    knowledge_caller: StdioMcpToolCaller | HttpMcpToolCaller | None = None
    declared = {entry.system for entry in records.values()}
    if DocumentSystem.TRACKER in declared:
        if tracker_caller is None:
            await log.ainfo(
                "run_record_sink_unavailable",
                system=DocumentSystem.TRACKER.value,
                detail=(
                    "tracker-side records are declared and no tracker is "
                    "dialled; every write to them will refuse loudly"
                ),
            )
        else:
            sinks[DocumentSystem.TRACKER] = LinearRecordSink(
                caller=tracker_caller,
                server_name=config.tracker_mcp_server_name,
            )
    if DocumentSystem.KNOWLEDGE in declared:
        knowledge_destinations = [
            f"records.{key} ({entry.name})"
            for key, entry in sorted(records.items())
            if entry.system is DocumentSystem.KNOWLEDGE
        ]
        knowledge_caller = _knowledge_caller(config, knowledge_destinations)
        sinks[DocumentSystem.KNOWLEDGE] = NotionRecordSink(
            caller=knowledge_caller,
            server_name=config.knowledge_mcp_server_name,
        )
    return BuiltRecorder(
        recorder=RunRecorder(records=records, sinks=sinks),
        knowledge_caller=knowledge_caller,
    )


def _knowledge_caller(
    config: AppConfig,
    destinations: list[str],
) -> StdioMcpToolCaller | HttpMcpToolCaller:
    """The programmatic client for the deployment's knowledge server.

    The same server definition granted sessions ride, dialled by this
    process for the deterministic record path.  Each transport names the
    field it cannot proceed without.
    """
    if config.knowledge_mcp_transport is KnowledgeTransport.STDIO:
        command = config.knowledge_mcp_command
        if command is None:
            raise PassKnowledgeCapabilityError(
                "knowledge-side records are declared and the stdio "
                "knowledge transport names no server command; declare "
                "KODEZART_KNOWLEDGE_MCP_COMMAND or move the records",
                destinations=destinations,
            )
        env = dict(config.knowledge_mcp_env)
        credential_env = config.knowledge_mcp_credential_env
        token = config.knowledge_mcp_token
        if credential_env is not None and token is not None:
            env[credential_env] = token.get_secret_value()
        return StdioMcpToolCaller(
            command=command,
            args=tuple(config.knowledge_mcp_args),
            env=env,
            server_name=config.knowledge_mcp_server_name,
            error_detail_limit=config.knowledge_mcp_error_detail_limit,
        )
    url = config.knowledge_mcp_server_url
    if url is None:
        raise PassKnowledgeCapabilityError(
            "knowledge-side records are declared and the http knowledge "
            "transport names no server url; declare "
            "KODEZART_KNOWLEDGE_MCP_SERVER_URL or move the records",
            destinations=destinations,
        )
    gateway = config.knowledge_mcp_gateway_token
    token = config.knowledge_mcp_token if gateway is None else gateway
    if token is None or config.knowledge_mcp_auth_scheme is None:
        raise PassKnowledgeCapabilityError(
            "knowledge-side records are declared and the http knowledge "
            "transport carries no credential and scheme to present; "
            "declare them or move the records",
            destinations=destinations,
        )
    return HttpMcpToolCaller(
        url=url,
        server_name=config.knowledge_mcp_server_name,
        token=token.get_secret_value(),
        timeout_seconds=config.knowledge_mcp_timeout_seconds,
        call_timeout_seconds=config.knowledge_mcp_call_timeout_seconds,
        auth_header_name=config.knowledge_mcp_auth_header,
        auth_scheme=config.knowledge_mcp_auth_scheme,
        error_detail_limit=config.knowledge_mcp_error_detail_limit,
    )
