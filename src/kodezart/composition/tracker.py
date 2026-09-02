"""Construction and boot reconciliation of the tracker port.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from dataclasses import dataclass
from typing import Final, assert_never

from kodezart.adapters.http_mcp_tool_caller import HttpMcpToolCaller
from kodezart.adapters.linear_mcp_tracker import (
    ACCEPTED_CREDENTIAL_SHAPE,
    LinearMcpTracker,
    is_long_lived_credential,
)
from kodezart.core.config import AppConfig
from kodezart.core.errors import TrackerCredentialShapeError
from kodezart.core.logging import BoundLogger
from kodezart.core.protocols import (
    ManagedMcpToolCaller,
    McpToolCaller,
    TrackerPort,
)
from kodezart.services.tracker_boot import reconcile_tracker_mappings
from kodezart.types.domain.dispatch import SelfWriteLedger
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.tracker import EnsureAction, TrackerBackend

#: Where the tracker credential is read from, named in the refusal because
#: it is half of what an operator has to act on.
CREDENTIAL_FIELD: Final[str] = "KODEZART_TRACKER_TOKEN"


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
        call_timeout_seconds=config.tracker_mcp_call_timeout_seconds,
        auth_header_name=config.tracker_mcp_auth_header,
        auth_scheme=config.tracker_mcp_auth_scheme,
        error_detail_limit=config.tracker_mcp_error_detail_limit,
    )


def refuse_foreign_credential(*, backend: TrackerBackend, token: str) -> None:
    """Refuse any credential that is not the backend's long-lived key shape.

    The SHAPE knowledge is the adapter's — which credentials its backend
    mints and which of them outlive a run — and the refusal is boot's,
    because this is the last moment at which a deployment can be told
    anything.  Nothing in this process refreshes a credential: a boot that
    accepted one with a lifetime would serve until it ended and then answer
    every tracker call with a refusal, hours later, unattended (KOD-171).

    The match is TOTAL.  A second backend added without its own shape rule
    stops the type check here rather than reaching this function's tail
    with nothing decided about the credential it was handed.
    """
    match backend:
        case TrackerBackend.LINEAR:
            accepted = is_long_lived_credential(token)
            shape = ACCEPTED_CREDENTIAL_SHAPE
        case _:
            assert_never(backend)
    if not accepted:
        raise TrackerCredentialShapeError(
            "the tracker credential is not the vendor's long-lived key shape "
            "and nothing here refreshes a credential that expires",
            field=CREDENTIAL_FIELD,
            accepted_shape=shape,
        )


def build_tracker(
    *,
    config: AppConfig,
    operation: OperationConfig,
    caller: McpToolCaller,
) -> tuple[TrackerPort, SelfWriteLedger]:
    """The ``TrackerPort`` implementation ``config.tracker`` selects.

    Adding a backend is a new adapter plus a member on ``TrackerBackend``.
    Consumers hold the protocol and change by nothing at all.

    The write ledger is built here and comes back BESIDE the port: the
    pass gates need the record of this process's own writes, and a port
    method for it would put a gate's concern into every tracker
    implementation — and an adapter's public surface is exactly the port's,
    which a reader on it would break (KOD-175).
    """
    ledger = SelfWriteLedger()
    match config.tracker:
        case TrackerBackend.LINEAR:
            adapter = LinearMcpTracker(
                caller=caller,
                queue_state_labels=operation.queue_states,
                workflow_state_names=operation.workflow_states,
                team_identifiers={
                    team_key: entry.name for team_key, entry in operation.teams.items()
                },
                max_retries=config.tracker_max_retries,
                retry_backoff_factor=config.tracker_retry_backoff_factor,
                ledger=ledger,
            )
            return adapter, ledger


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
    ledger: SelfWriteLedger
    """Where this tracker's own writes leave their stamp, for the pass gates
    that must not wake on them.  It travels WITH the tracker because the two
    are one fact: the writer and the reader of the same issues (KOD-175)."""


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

    The credential is judged twice before the session exists, and both
    judgements are cheap.  Its SHAPE is read first, off the bytes alone, so
    a credential this process could not renew is refused without a request
    being made at all.  Then it is PRESENTED once, over plain HTTP, so a
    key of the right shape that the server does not accept is named as the
    refusal it is — a 401 met while the session opens says only that the
    session broke (KOD-268).
    """
    if operation is None or config.tracker_token is None:
        await log.ainfo(
            "tracker_not_configured",
            operation_config_present=operation is not None,
            tracker_token_present=config.tracker_token is not None,
        )
        return None
    token = config.tracker_token.get_secret_value()
    refuse_foreign_credential(backend=config.tracker, token=token)
    caller = make_mcp_tool_caller(config=config, token=token)
    await caller.probe()
    await caller.open()
    try:
        tracker, ledger = build_tracker(
            config=config,
            operation=operation,
            caller=caller,
        )
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
        ledger=ledger,
    )
