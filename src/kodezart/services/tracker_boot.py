"""Boot reconciliation for the tracker's configured mappings.

Split by OWNERSHIP, because "does this exist?" and "whose job is it to
make it exist?" are different questions and the config declares both
classes of thing.  A value the operation owns is INSTATED — created if
absent, adopted unchanged if present — and a value another system is
authoritative for is RESOLVED, with one unresolvable entry aborting
startup naming exactly that entry.  A principal cannot be conjured; a
queue label is nothing but this operation saying it exists.

Treating both as "resolve or abort" made every owned value a manual
prerequisite for booting, which is the manual step the cutover exists to
remove: a real config could not boot until its checkpoint document and
terminal queue label had been hand-made.

Tracker-agnostic: the refs are built from the operation config in domain
vocabulary and handed to the port.  Which backend instates or resolves
them is the adapter's business.
"""

from collections.abc import Callable, Sequence

from kodezart.core.errors import TrackerBootValidationError, TrackerEnsureConflictError
from kodezart.core.protocols import TrackerPort
from kodezart.types.domain.operation import (
    FIELD_OWNERSHIP,
    ConfigOwnership,
    DocumentSystem,
    OperationConfig,
)
from kodezart.types.domain.tracker import (
    MappingKind,
    MappingOutcome,
    MappingReconciliation,
    MappingRef,
)


def configured_mappings(config: OperationConfig) -> tuple[MappingRef, ...]:
    """Every mapping entry boot validation must resolve, in a stable order.

    ``documents`` are absent from this pass and that is deliberate: the
    ensure is what establishes them and it reports the identifier the
    workspace holds, so a resolution pass over the same values would re-ask
    the tool that just answered.  What this pass exists to catch is the
    class no boot can instate — a principal, a team, a lifecycle state.

    ``records`` are EXTERNAL and so belong here, tracker-side ones at
    least: a record destination declares an id it did not assign itself,
    which is exactly the class this pass resolves, and a typo in one used
    to survive boot and fail inside an unattended pass session instead.  A
    tracker-side record IS a document to this backend, so it resolves as
    one.  A record in the KNOWLEDGE system is left alone: no client in this
    process can open that store, so nothing here could ask — their boot
    guard is the grant-coverage check at the composition root.
    """
    refs: list[MappingRef] = [
        MappingRef(
            kind=MappingKind.USER,
            name="+".join(sorted(role.value for role in principal.roles)),
            identifier=principal.tracker_user,
        )
        for principal in config.principals
    ]
    refs.extend(
        MappingRef(
            kind=MappingKind.USER,
            name=identity,
            identifier=identity,
        )
        for identity in config.agent_identities
    )
    refs.extend(
        MappingRef(kind=MappingKind.TEAM, name=team_key, identifier=entry.name)
        for team_key, entry in sorted(config.teams.items())
    )
    refs.extend(
        MappingRef(kind=MappingKind.QUEUE_STATE, name=name, identifier=identifier)
        for name, identifier in sorted(config.queue_states.items())
    )
    refs.extend(
        MappingRef(
            kind=MappingKind.WORKFLOW_STATE,
            name=stage.value,
            identifier=identifier,
        )
        for stage, identifier in sorted(
            config.workflow_states.items(),
            key=lambda item: item[0].value,
        )
    )
    refs.extend(
        MappingRef(
            kind=MappingKind.DOCUMENT,
            name=entry.name,
            identifier=entry.id,
        )
        for _, entry in sorted(config.records.items())
        if entry.system is DocumentSystem.TRACKER
    )
    return tuple(refs)


def _queue_state_refs(
    config: OperationConfig,
    containers: tuple[str | None, ...],
) -> tuple[MappingRef, ...]:
    """The queue vocabulary, once per container it has to exist in.

    One ref per (container, member), because a queue label resolves WITHIN
    a container and an operation dispatching from several teams needs the
    member to exist on each of them.  A single declared team therefore
    yields exactly the refs it always did; a second one adds its own copies
    rather than moving the first team's (KOD-167).
    """
    return tuple(
        MappingRef(
            kind=MappingKind.QUEUE_STATE,
            name=name,
            identifier=identifier,
            scope=container,
        )
        for container in containers
        for name, identifier in sorted(config.queue_states.items())
    )


def _document_refs(
    config: OperationConfig,
    _containers: tuple[str | None, ...],
) -> tuple[MappingRef, ...]:
    """The documents this operation instates, keyed by their declared name.

    Only the ones in the TRACKER system: a document in the knowledge store
    is not this port's to create, declares its id at load, and therefore
    has nothing for an ensure to do.  No container scope is carried — a
    document belongs to the workspace, not to a team, and passing the
    operation's team here would ask the backend to file it somewhere the
    config never said.
    """
    return tuple(
        MappingRef(
            kind=MappingKind.DOCUMENT,
            name=entry.name,
            identifier=entry.id,
        )
        for _, entry in sorted(config.documents.items())
        if entry.system is DocumentSystem.TRACKER
    )


#: How each OWNED field's declared values become refs boot can instate.
#: Keyed by field name so the partition in the MODEL decides what is
#: instated, rather than this module holding a second opinion about it.
OWNED_REF_BUILDERS: dict[
    str,
    Callable[[OperationConfig, tuple[str | None, ...]], tuple[MappingRef, ...]],
] = {
    "documents": _document_refs,
    "queue_states": _queue_state_refs,
}


def owned_mappings(config: OperationConfig) -> tuple[MappingRef, ...]:
    """Every mapping entry boot INSTATES, in a stable order.

    Derived from ``FIELD_OWNERSHIP`` rather than from a list here.  A
    partition declared in the model and never read is decoration: it can be
    neither honoured nor violated, and a field promoted to OWNED would
    change nothing at boot.  Reading it means the promotion is what turns
    the ensure on.

    A field the model calls OWNED that this module cannot instate is a
    typed boot failure, never a skip — the alternative is a config
    declaring a value the operation claims to own, and a boot that quietly
    owns nothing.

    Each ref carries the container its value is created in, and a builder
    is handed EVERY declared team rather than one chosen container: a queue
    label lives inside a team on the measured backend, so an operation
    dispatching from several teams needs its vocabulary on each of them and
    a builder emits one ref per team.  Workspace scope is what an operation
    declaring NO team gets, which is the only shape with no container to
    name (KOD-167).
    """
    names = sorted({entry.name for entry in config.teams.values()})
    containers: tuple[str | None, ...] = tuple(names) if names else (None,)
    owned = sorted(
        field
        for field, ownership in FIELD_OWNERSHIP.items()
        if ownership is ConfigOwnership.OWNED
    )
    uninstatable = [field for field in owned if field not in OWNED_REF_BUILDERS]
    if uninstatable:
        raise TrackerBootValidationError(
            "operation config declares OWNED fields boot cannot instate",
            unresolved=uninstatable,
        )
    refs: list[MappingRef] = []
    for field in owned:
        refs.extend(OWNED_REF_BUILDERS[field](config, containers))
    return tuple(refs)


async def validate_tracker_mappings(
    *,
    tracker: TrackerPort,
    config: OperationConfig,
) -> None:
    """Resolve every configured mapping; raise naming EVERY failure at once."""
    refs: Sequence[MappingRef] = configured_mappings(config)
    unresolved = await tracker.resolve_mappings(refs=refs)
    if unresolved:
        raise TrackerBootValidationError(
            "operation config names tracker entities the workspace does not resolve",
            unresolved=[ref.describe() for ref in unresolved],
        )


def adopt_mappings(
    config: OperationConfig,
    outcomes: Sequence[MappingOutcome],
) -> OperationConfig:
    """*config* with every identifier the workspace assigned written back.

    Only the server-assigned kinds move anything: a queue state's outcome
    reports the identifier the config already declared.  A document's
    reports the id it was created with or adopted under, and the config it
    came from is not true until that id is in it — every later reader,
    including the prompt renderer, reads the config rather than the
    outcomes.
    """
    adopted = {
        outcome.ref.name: outcome.identifier
        for outcome in outcomes
        if outcome.ref.kind is MappingKind.DOCUMENT
    }
    if not adopted:
        return config
    documents = {
        key: (
            entry
            if entry.name not in adopted
            else entry.model_copy(update={"id": adopted[entry.name]})
        )
        for key, entry in config.documents.items()
    }
    return config.model_copy(update={"documents": documents})


async def reconcile_tracker_mappings(
    *,
    tracker: TrackerPort,
    config: OperationConfig,
) -> MappingReconciliation:
    """Instate what the operation owns, then resolve everything.

    Ordered, and the order is the point: the owned values are created
    first, so the resolution pass that follows sees the workspace the
    operation declared rather than the one it started from.  What that pass
    can still fail on is exactly the external class — a principal, a team, a
    workflow state — which is the half no boot can instate.

    The config that comes back is the one that is TRUE after the ensure.
    It is the reconciled copy every later consumer must read: a document
    the operation named and boot created carries an id nobody could have
    declared, and a prompt rendered from the pre-boot copy would name a
    placeholder no session can open.
    """
    refs = owned_mappings(config)
    # Keyed by CONTAINER as well as kind and identifier, because a container
    # is half of what identifies a backend value: two declared entries
    # claiming one label on one team still contradict each other, while one
    # member instated on each of two teams is two definitions and the whole
    # point of declaring both boards (KOD-167).
    claimed: list[tuple[MappingKind, str | None, str]] = [
        (ref.kind, ref.scope, ref.identifier)
        for ref in refs
        if ref.identifier is not None
    ]
    collisions = sorted(
        {
            ref.describe()
            for ref in refs
            if ref.identifier is not None
            and claimed.count((ref.kind, ref.scope, ref.identifier)) > 1
        },
    )
    if collisions:
        raise TrackerEnsureConflictError(
            "two declared entries of one kind claim one backend value",
            entry="; ".join(collisions),
        )
    outcomes = await tracker.ensure_mappings(refs=refs)
    reconciled = adopt_mappings(config, outcomes)
    await validate_tracker_mappings(tracker=tracker, config=reconciled)
    return MappingReconciliation(config=reconciled, outcomes=tuple(outcomes))
