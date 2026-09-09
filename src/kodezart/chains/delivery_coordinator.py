"""Compose a scope's ready lanes, in the order the planner ranked them.

This is the union step's production constructor: it builds the scratch
composition and its tick from configuration, reads the planner's ranked
ready set, and hands that ranking to the composition unchanged.

It holds the tracker, the git port and the check-chain runner, and no
forge port of any kind. Composing a scope is a measurement: nothing here
can push, open, merge or read the state of a pull request, so the order a
scope composes in can only be the planner's, never the order the lanes'
pull requests happen to have been opened in.
"""

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.core.config import AppConfig
from kodezart.core.protocols import CheckChainRunner, GitService, TrackerPort
from kodezart.domain.errors import UnionHeadReadError
from kodezart.services.union_composition import UnionComposition
from kodezart.services.union_tick import UnionTick
from kodezart.types.domain.branch import WorkRefRole
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.union import UnionCompositionResult
from kodezart.types.domain.union_tick import UnionLaneBranch, UnionTickContext


class DeliveryCoordinator:
    """One scope's union observations, taken over the planner's own ranking.

    The scope is named once, by the union context this consumer was
    configured with; the kind of container that key addresses is the only
    thing this constructor adds to it.
    """

    def __init__(
        self,
        *,
        scope_kind: ScopeKind,
        tracker: TrackerPort,
        git: GitService,
        runner: CheckChainRunner,
        context: UnionTickContext,
        config: AppConfig,
        committer_name: str,
        committer_email: str,
    ) -> None:
        self._tracker = tracker
        self._scope = ScopeRef(kind=scope_kind, key=context.scope_key)
        self._tick = UnionTick(
            composition=UnionComposition(
                git=git,
                runner=runner,
                author_name=committer_name,
                author_email=committer_email,
            ),
            git=git,
            context=context,
            config=config,
        )

    async def verify(self) -> UnionCompositionResult:
        """Observe whether the planner's current ready lanes compose.

        The roster is the ready set in the planner's own order. Nothing
        here sorts, filters or reverses it, so a lane's position in the
        composition is the position the planner's ranking gave it.
        """
        selection = await read_scope_ready(ref=self._scope, tracker=self._tracker)
        if not selection.ready:
            raise UnionHeadReadError(
                scope_key=self._scope.key,
                branch=None,
                reason="the planner ranked no ready lane to compose",
            )
        roster = [
            await self._lane_branch(issue_key=lane.issue.issue_key)
            for lane in selection.ready
        ]
        return await self._tick.verify(lane_branches=roster)

    async def _lane_branch(self, *, issue_key: str) -> UnionLaneBranch:
        """The one deliverable ref the lane recorded, or a typed refusal."""
        deliverables = [
            ref
            for ref in await self._tracker.work_refs(issue_key=issue_key)
            if ref.role is WorkRefRole.DELIVERABLE
        ]
        match deliverables:
            case [ref]:
                return UnionLaneBranch(lane_key=issue_key, branch=ref.branch)
            case []:
                reason = "a ranked lane records no deliverable ref"
            case _:
                reason = "a ranked lane records more than one deliverable ref"
        raise UnionHeadReadError(
            scope_key=self._scope.key,
            branch=None,
            reason=f"{reason}: {issue_key}",
        )
