"""Construction and boot reconciliation of the tracker port.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from dataclasses import dataclass

from kodezart.adapters.http_mcp_tool_caller import HttpMcpToolCaller
from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.core.config import AppConfig
from kodezart.core.logging import BoundLogger
from kodezart.core.protocols import (
    ManagedMcpToolCaller,
    McpToolCaller,
    TrackerPort,
)
from kodezart.services.tracker_boot import reconcile_tracker_mappings
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.tracker import EnsureAction, TrackerBackend


def make_mcp_tool_caller(*, config: AppConfig, token: str) -> ManagedMcpToolCaller:
    """The vendor MCP transport this deployment dials.

    One server definition, two consumers (KOD-57's mechanism ruling): the
    programmatic client on the deterministic path, and the same server
    attached to judgment-pass sessions.
    """
    return HttpMcpToolCaller(
        url=config.tracker_mcp_server_url,
        server_name=config.tracker_mcp_server_name,
        token=token,
        timeout_seconds=config.tracker_timeout_seconds,
        auth_header_name=config.tracker_mcp_auth_header,
        auth_scheme=config.tracker_mcp_auth_scheme,
    )


def build_tracker(
    *,
    config: AppConfig,
    operation: OperationConfig,
    caller: McpToolCaller,
) -> TrackerPort:
    """The ``TrackerPort`` implementation ``config.tracker`` selects.

    Adding a backend is a new adapter plus a member on ``TrackerBackend``.
    Consumers hold the protocol and change by nothing at all.
    """
    match config.tracker:
        case TrackerBackend.LINEAR:
            return LinearMcpTracker(
                caller=caller,
                queue_state_labels=operation.queue_states,
                workflow_state_names=operation.workflow_states,
                team_identifiers={
                    team_key: entry.name
                    for team_key, entry in operation.teams.items()
                },
                max_retries=config.tracker_max_retries,
                retry_backoff_factor=config.tracker_retry_backoff_factor,
            )


@dataclass(frozen=True)
class DialledTracker:
    """A reconciled tracker: the port, its session, and the config it left.

    ``operation`` is the RECONCILED config and is the only copy anything
    downstream may read.  A document this boot created carries an id no
    operator could have declared, and a prompt bound to the pre-boot copy
    would render a placeholder in its place (KOD-57 R9).
    """

    tracker: TrackerPort
    caller: ManagedMcpToolCaller
    operation: OperationConfig


async def boot_tracker(
    *,
    config: AppConfig,
    operation: OperationConfig | None,
    log: BoundLogger,
) -> DialledTracker | None:
    """Dial the tracker and reconcile its configured mappings, or say why not.

    Three states, none silent.  Both an operation config and a credential
    present dials the backend and reconciles every declared mapping before
    the process serves anything; either one absent logs exactly which is
    absent and leaves the tracker unwired; an unreconcilable mapping aborts
    boot with a typed error naming it.
    """
    if operation is None or config.tracker_token is None:
        await log.ainfo(
            "tracker_not_configured",
            operation_config_present=operation is not None,
            tracker_token_present=config.tracker_token is not None,
        )
        return None
    caller = make_mcp_tool_caller(
        config=config,
        token=config.tracker_token.get_secret_value(),
    )
    await caller.open()
    try:
        tracker = build_tracker(config=config, operation=operation, caller=caller)
        reconciliation = await reconcile_tracker_mappings(
            tracker=tracker,
            config=operation,
        )
    except BaseException:
        await caller.close()
        raise
    await log.ainfo(
        "tracker_mappings_reconciled",
        backend=config.tracker.value,
        adopted=[
            item.ref.describe()
            for item in reconciliation.outcomes
            if item.action is EnsureAction.ADOPTED
        ],
        created=[
            item.ref.describe()
            for item in reconciliation.outcomes
            if item.action is EnsureAction.CREATED
        ],
    )
    return DialledTracker(
        tracker=tracker,
        caller=caller,
        operation=reconciliation.config,
    )
