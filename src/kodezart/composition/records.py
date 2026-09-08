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
from kodezart.core.errors import PassKnowledgeCapabilityError
from kodezart.core.knowledge_settings import KnowledgeSettings
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
from kodezart.types.domain.session import HttpKnowledge, StdioKnowledge

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
    knowledge: KnowledgeSettings,
    tracker_server_name: str,
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
                server_name=tracker_server_name,
            )
    if DocumentSystem.KNOWLEDGE in declared:
        knowledge_destinations = [
            f"records.{key} ({entry.name})"
            for key, entry in sorted(records.items())
            if entry.system is DocumentSystem.KNOWLEDGE
        ]
        knowledge_caller = _knowledge_caller(knowledge, knowledge_destinations)
        sinks[DocumentSystem.KNOWLEDGE] = NotionRecordSink(
            caller=knowledge_caller,
            server_name=knowledge.server_name,
        )
    return BuiltRecorder(
        recorder=RunRecorder(records=records, sinks=sinks),
        knowledge_caller=knowledge_caller,
    )


def _knowledge_caller(
    knowledge: KnowledgeSettings,
    destinations: list[str],
) -> StdioMcpToolCaller | HttpMcpToolCaller:
    """Build the recorder from the same typed connection SDK sessions receive."""
    connection = knowledge.connection
    if isinstance(connection, StdioKnowledge):
        return StdioMcpToolCaller(
            command=connection.command,
            args=connection.args,
            env=connection.environment(),
            server_name=knowledge.server_name,
            call_timeout_seconds=knowledge.call_timeout_seconds,
            error_detail_limit=knowledge.error_detail_limit,
            stderr_tail_limit=connection.stderr_tail_limit,
        )
    if not isinstance(connection, HttpKnowledge) or not connection.authenticated:
        raise PassKnowledgeCapabilityError(
            "knowledge-side records require knowledge.connection with an "
            "authenticated HTTP endpoint or installed stdio server",
            destinations=destinations,
        )
    return HttpMcpToolCaller(
        url=connection.server_url,
        server_name=knowledge.server_name,
        headers=connection.headers(),
        timeout_seconds=connection.timeout_seconds,
        call_timeout_seconds=knowledge.call_timeout_seconds,
        sse_read_timeout_seconds=connection.sse_read_timeout_seconds,
        error_detail_limit=knowledge.error_detail_limit,
    )
