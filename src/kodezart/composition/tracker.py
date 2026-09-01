"""Construction and boot reconciliation of the tracker port.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from dataclasses import dataclass

from kodezart.adapters.http_mcp_tool_caller import HttpMcpToolCaller
from kodezart.adapters.linear_mcp_tracker import (
    LinearMcpTracker,
    credential_expiry_field,
)
from kodezart.core.config import AppConfig
from kodezart.core.errors import TrackerCredentialExpiryError
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

    One server definition, one consumer: this factory, which builds the
    programmatic client on the deterministic path.  No session attaches
    the tracker server.
    """
    return HttpMcpToolCaller(
        url=config.tracker_mcp_server_url,
        server_name=config.tracker_mcp_server_name,
        token=token,
        timeout_seconds=config.tracker_timeout_seconds,
        auth_header_name=config.tracker_mcp_auth_header,
        auth_scheme=config.tracker_mcp_auth_scheme,
        error_detail_limit=config.tracker_mcp_error_detail_limit,
    )


def refuse_expiring_credential(*, backend: TrackerBackend, token: str) -> None:
    """Refuse a credential that will expire under the boot it is starting.

    The SHAPE knowledge is the adapter's — which credentials its backend
    takes and which of them declare a lifetime — and the refusal is boot's,
    because this is the last moment at which a deployment can be told
    anything.  Nothing in this process refreshes a credential: a boot that
    accepted an expiring one would serve until the expiry and then answer
    every tracker call with a refusal, hours later, unattended (KOD-171).
    """
    match backend:
        case TrackerBackend.LINEAR:
            field = credential_expiry_field(token)
    if field is not None:
        raise TrackerCredentialExpiryError(
            "the tracker credential declares its own expiry and nothing here "
            "refreshes it; configure a long-lived key instead",
            field=field,
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
                    team_key: entry.name for team_key, entry in operation.teams.items()
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

    The credential is judged BEFORE the dial: one that declares its own
    expiry is refused here, so a deployment learns it at the second the
    process starts rather than at the minute the token dies.
    """
    if operation is None or config.tracker_token is None:
        await log.ainfo(
            "tracker_not_configured",
            operation_config_present=operation is not None,
            tracker_token_present=config.tracker_token is not None,
        )
        return None
    token = config.tracker_token.get_secret_value()
    refuse_expiring_credential(backend=config.tracker, token=token)
    caller = make_mcp_tool_caller(config=config, token=token)
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
