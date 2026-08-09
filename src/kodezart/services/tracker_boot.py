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

from collections.abc import Sequence

from kodezart.core.errors import TrackerBootValidationError, TrackerEnsureConflictError
from kodezart.core.protocols import TrackerPort
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.tracker import MappingKind, MappingOutcome, MappingRef


def configured_mappings(config: OperationConfig) -> tuple[MappingRef, ...]:
    """Every mapping entry boot validation must resolve, in a stable order."""
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
        MappingRef(kind=MappingKind.TEAM, name=name, identifier=identifier)
        for name, identifier in sorted(config.teams.items())
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
    return tuple(refs)


def owned_mappings(config: OperationConfig) -> tuple[MappingRef, ...]:
    """Every mapping entry boot INSTATES, in a stable order.

    The queue vocabulary, and nothing else: it exists only because this
    operation says it does, and no other system defines it.  Each ref
    carries the container its value is created in — the operation's own
    team when it declares exactly one, and workspace scope when it declares
    several, because one queue state addressed on several teams' issues
    cannot be a label private to one of them.
    """
    identifiers = sorted(set(config.teams.values()))
    scope = identifiers[0] if len(identifiers) == 1 else None
    return tuple(
        MappingRef(
            kind=MappingKind.QUEUE_STATE,
            name=name,
            identifier=identifier,
            scope=scope,
        )
        for name, identifier in sorted(config.queue_states.items())
    )


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


async def reconcile_tracker_mappings(
    *,
    tracker: TrackerPort,
    config: OperationConfig,
) -> Sequence[MappingOutcome]:
    """Instate what the operation owns, then resolve everything.

    Ordered, and the order is the point: the owned values are created
    first, so the resolution pass that follows sees the workspace the
    operation declared rather than the one it started from.  What that pass
    can still fail on is exactly the external class — a principal, a team, a
    workflow state — which is the half no boot can instate.
    """
    refs = owned_mappings(config)
    identifiers = [ref.identifier for ref in refs]
    collisions = sorted(
        {ref.describe() for ref in refs if identifiers.count(ref.identifier) > 1},
    )
    if collisions:
        raise TrackerEnsureConflictError(
            "two declared queue states claim one backend value",
            entry="; ".join(collisions),
        )
    outcomes = await tracker.ensure_mappings(refs=refs)
    await validate_tracker_mappings(tracker=tracker, config=config)
    return outcomes
