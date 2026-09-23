"""Construction and boot reconciliation of the tracker port.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

import asyncio
from dataclasses import dataclass
from typing import Final, assert_never

from pydantic import SecretStr

from kodezart.adapters.linear.status_update import LinearScopeStatusUpdates
from kodezart.adapters.linear.tracker import (
    ACCEPTED_CREDENTIAL_SHAPE,
    LinearMcpTracker,
    is_long_lived_credential,
)
from kodezart.adapters.mcp.http_tool_caller import HttpMcpToolCaller
from kodezart.adapters.mcp.mapping import TRACKER_SESSION
from kodezart.config.tracker import TrackerSettings
from kodezart.core.backoff import RetryPolicy
from kodezart.core.errors import (
    McpServerNameClashError,
    TrackerCredentialShapeError,
    TrackerWriterAttributionError,
)
from kodezart.core.logging import BoundLogger
from kodezart.core.owned_tasks import finish_owned
from kodezart.core.protocols import (
    ManagedMcpToolCaller,
    McpToolCaller,
    ScopeStatusUpdates,
    TrackerPort,
)
from kodezart.services.tracker_boot import reconcile_tracker_mappings
from kodezart.types.domain.dispatch import SelfWriteLedger
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.organize import split_label_key
from kodezart.types.domain.session import HttpMcpServer, KnowledgeGrant
from kodezart.types.domain.tracker import EnsureAction, TrackerBackend

#: Where the tracker credential is read from, named in the refusal because
#: it is half of what an operator has to act on.
CREDENTIAL_FIELD: Final[str] = "KODEZART_TRACKER__TOKEN"

#: The operation field that declares who the non-human writer is, named in
#: the refusal when the operation declares none at all.
AGENT_IDENTITY_FIELD: Final[str] = "agent_identities"

#: The capability every refusal below names, so an operator reading one
#: knows which boot check spoke.
ATTRIBUTABLE_WRITER: Final[str] = "attributable writer"

#: Where each server's name is read from, named in the name-clash refusal
#: because renaming either one is the fix.
KNOWLEDGE_SERVER_NAME_FIELD: Final[str] = "KODEZART_KNOWLEDGE__SERVER_NAME"
TRACKER_SERVER_NAME_FIELD: Final[str] = "KODEZART_TRACKER__SERVER_NAME"


def tracker_mcp_server(*, settings: TrackerSettings, token: str) -> HttpMcpServer:
    """The tracker's MCP server definition: its name, its url, its header.

    The one place the credential header is rendered. Both consumers read
    this value: the programmatic client on the deterministic path
    (:func:`make_mcp_tool_caller`) and the session adapter, which attaches
    the same server to the scheduled-pass sessions.
    """
    return HttpMcpServer(
        name=settings.server_name,
        url=settings.server_url,
        headers={settings.auth_header: f"{settings.auth_scheme} {token}"},
    )


def session_tracker_server(
    *, settings: TrackerSettings, token: SecretStr | None
) -> HttpMcpServer | None:
    """The tracker server the composition root hands the session adapter.

    ``None`` without a credential, so no session is given the tracker; with
    one, the definition :func:`tracker_mcp_server` renders, looked up at call
    time so the sessions and the programmatic client can only ever read the
    one rendering.
    """
    if token is None:
        return None
    return tracker_mcp_server(settings=settings, token=token.get_secret_value())


def refuse_server_name_clash(
    *, grant: KnowledgeGrant, tracker_server: HttpMcpServer | None
) -> None:
    """Refuse a deployment whose sessions would get two servers of one name.

    The tracker server is attached to the scheduled passes whenever a tracker
    credential is configured, and the knowledge server to every session kind
    the grant names. Where both reach the scheduled passes under one name,
    one of them would replace the other, so boot refuses and names both.
    The session mapping refuses the same clash again when a session starts,
    as the last line of defence for a caller that skipped boot.
    """
    if tracker_server is None or not grant.grants(TRACKER_SESSION):
        return
    if grant.server_name != tracker_server.name:
        return
    raise McpServerNameClashError(
        "the knowledge server and the tracker server share one name",
        knowledge_server=grant.server_name,
        knowledge_field=KNOWLEDGE_SERVER_NAME_FIELD,
        tracker_server=tracker_server.name,
        tracker_field=TRACKER_SERVER_NAME_FIELD,
        session_type=TRACKER_SESSION.value,
    )


def make_mcp_tool_caller(
    *, settings: TrackerSettings, token: str
) -> ManagedMcpToolCaller:
    """The vendor MCP transport this deployment dials.

    Over the server definition :func:`tracker_mcp_server` renders, so the
    client and the sessions present the same header to the same url.
    """
    server = tracker_mcp_server(settings=settings, token=token)
    return HttpMcpToolCaller(
        url=server.url,
        server_name=server.name,
        headers=dict(server.headers),
        timeout_seconds=settings.timeout_seconds,
        call_timeout_seconds=settings.call_timeout_seconds,
        sse_read_timeout_seconds=settings.sse_read_timeout_seconds,
        error_detail_limit=settings.error_detail_limit,
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


async def refuse_unattributable_writer(
    *, tracker: TrackerPort, operation: OperationConfig
) -> None:
    """Refuse a deployment whose writes no declared agent identity owns.

    Read at boot and never again: every write this process makes is signed
    by the credential's account, and an operation that cannot recognise
    that account as its own agent reads its own writes as a principal's.
    The comparison is over both spellings, since a declared identity may be
    written as a mention or as the account name.
    """
    found = await tracker.writer_identity()
    declared = {identity.lstrip("@") for identity in operation.agent_identities}
    if not declared:
        raise TrackerWriterAttributionError(
            "the operation declares no non-human writer for this deployment",
            capability=ATTRIBUTABLE_WRITER,
            writer=sorted(found),
            declared=(),
            field=AGENT_IDENTITY_FIELD,
        )
    if {spelling.lstrip("@") for spelling in found}.isdisjoint(declared):
        raise TrackerWriterAttributionError(
            "the tracker credential is attributed to no declared agent identity",
            capability=ATTRIBUTABLE_WRITER,
            writer=sorted(found),
            declared=sorted(declared),
            field=CREDENTIAL_FIELD,
        )


def criteria_stage_label_key(operation: OperationConfig) -> str | None:
    """The issue-label key that marks a lane's criteria stage complete.

    The terminal marker of whichever resolved mandate row marks the execution
    stage, reduced to the key half of its qualified reference — which is what
    the adapter then looks up in ``issue_labels`` and nowhere else. ``None``
    where no declared row marks that stage, and a lane that carries no such
    marker cannot fire.

    A function beside the builder rather than an expression inside it, because
    a test about a shipped operation file needs the same answer the adapter is
    built with, and a second copy of this expression in a test would be a
    second opinion about which label that is.
    """
    return next(
        (
            split_label_key(row.spec.terminal_marker_key)[1]
            for row in operation.resolve_organize_mandates()
            if row.role.marks_execution_stage
        ),
        None,
    )


def build_tracker(
    *,
    backend: TrackerBackend,
    retry: RetryPolicy,
    operation: OperationConfig,
    caller: McpToolCaller,
) -> tuple[TrackerPort, SelfWriteLedger]:
    """The ``TrackerPort`` implementation ``backend`` selects.

    Adding a backend is a new adapter plus a member on ``TrackerBackend``.
    Consumers hold the protocol and change by nothing at all.

    The write ledger is built here and comes back BESIDE the port: the
    pass gates need the record of this process's own writes, and a port
    method for it would put a gate's concern into every tracker
    implementation — and an adapter's public surface is exactly the port's,
    which a reader on it would break (KOD-175).
    """
    ledger = SelfWriteLedger()
    match backend:
        case TrackerBackend.LINEAR:
            adapter = LinearMcpTracker(
                caller=caller,
                queue_state_labels=operation.queue_states,
                scope_labels=operation.scope_labels,
                workflow_state_names=operation.workflow_states,
                marker_prefixes=operation.marker_prefixes,
                issue_labels=operation.issue_labels,
                criteria_stage_label_key=criteria_stage_label_key(operation),
                team_identifiers={
                    team_key: entry.name for team_key, entry in operation.teams.items()
                },
                retry=retry,
                ledger=ledger,
            )
            return adapter, ledger


def build_scope_status_writer(
    *, backend: TrackerBackend, caller: McpToolCaller
) -> ScopeStatusUpdates:
    """The ``ScopeStatusUpdates`` implementation *backend* selects.

    One class for one role over the same session the port dials, beside the
    port rather than on it: the scope terminal states the single write it
    makes and the one read that keeps it single, and every other tracker
    consumer is unchanged by its existence (KOD-829).  The match is TOTAL, so
    a second backend added without this role stops the type check here.
    """
    match backend:
        case TrackerBackend.LINEAR:
            return LinearScopeStatusUpdates(caller=caller)


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
    status: ScopeStatusUpdates
    """The scope terminal's one write and the read that keeps it one, over
    the same session and BESIDE the port the way the ledger is: they belong
    to one consumer's role, and members for them on the port would put that
    role into every tracker implementation (KOD-829)."""
    ledger: SelfWriteLedger
    """Where this tracker's own writes leave their stamp, for the pass gates
    that must not wake on them.  It travels WITH the tracker because the two
    are one fact: the writer and the reader of the same issues (KOD-175)."""


async def boot_tracker(
    *,
    settings: TrackerSettings,
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
    if operation is None or settings.token is None:
        await log.ainfo(
            "tracker_not_configured",
            operation_config_present=operation is not None,
            tracker_token_present=settings.token is not None,
        )
        return None
    token = settings.token.get_secret_value()
    refuse_foreign_credential(backend=settings.backend, token=token)
    caller = make_mcp_tool_caller(settings=settings, token=token)
    await caller.probe()
    try:
        await caller.open()
        tracker, ledger = build_tracker(
            backend=settings.backend,
            retry=RetryPolicy(
                attempts=settings.max_retries + 1,
                initial_delay=settings.retry_backoff_factor,
            ),
            operation=operation,
            caller=caller,
        )
        await refuse_unattributable_writer(tracker=tracker, operation=operation)
        reconciliation = await reconcile_tracker_mappings(
            tracker=tracker,
            config=operation,
        )
        await log.ainfo(
            "tracker_mappings_reconciled",
            backend=settings.backend.value,
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
            status=build_scope_status_writer(backend=settings.backend, caller=caller),
            ledger=ledger,
        )
    except BaseException as failure:

        async def close_caller() -> BaseException | None:
            try:
                await caller.close()
            except BaseException as exc:
                await log.aerror(
                    "tracker_boot_cleanup_failed",
                    error_kind=type(exc).__name__,
                    error=str(exc),
                    exc_info=True,
                )
                return exc
            return None

        cleanup_error, cancelled = await finish_owned(
            asyncio.create_task(close_caller())
        )
        if cancelled or isinstance(failure, asyncio.CancelledError):
            raise asyncio.CancelledError from cleanup_error
        if cleanup_error is not None:
            raise cleanup_error from failure
        raise
