"""Boot validation for the tracker's configured mappings.

Every identity, team and state mapping the operation config declares is
resolved against the workspace before the process serves anything.  One
unresolvable entry aborts startup naming exactly that entry — there is no
partial operation and no silent default, because a mapping that does not
resolve surfaces otherwise as a mis-targeted write hours later, with
nothing to falsify.

Tracker-agnostic: the refs are built from the operation config in domain
vocabulary and handed to ``TrackerPort.resolve_mappings``.  Which backend
resolves them is the adapter's business.
"""

from collections.abc import Sequence

from kodezart.core.errors import TrackerBootValidationError
from kodezart.core.protocols import TrackerPort
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.tracker import MappingKind, MappingRef


def configured_mappings(config: OperationConfig) -> tuple[MappingRef, ...]:
    """Every mapping entry boot validation must resolve, in a stable order."""
    refs: list[MappingRef] = [
        MappingRef(
            kind=MappingKind.USER,
            name=principal.role.value,
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
