"""Observe a scope's retained lane heads independently of dispatch readiness.

Membership comes from the coherent scope facts. The existing topology policy
orders those participants; approval, criterion gaps and live blockers decide
future dispatch, not whether a retained branch belongs in this measurement.
The consumer holds the scope's facts, the narrow ref-reading role that answers
which branch a lane delivers, Git and the check-chain runner, without forge or
lane-delivery authority. Composition changes only a disposable scratch tree.
"""

from kodezart.config.app import AppConfig
from kodezart.core.protocols import (
    CheckChainRunner,
    GitService,
    ScopePlanReader,
    WorkRefReader,
)
from kodezart.domain.errors import UnionHeadReadError
from kodezart.domain.issue_tree import RECORD_KINDS
from kodezart.domain.topology import plan_topology
from kodezart.services.scope_planning import read_scope_facts
from kodezart.services.union_composition import UnionComposition
from kodezart.services.union_tick import UnionTick
from kodezart.types.domain.branch import WorkRefRole
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.union import UnionCompositionResult
from kodezart.types.domain.union_tick import UnionLaneBranch, UnionTickContext


class ScopeUnionCoordinator:
    """One scope's union observations, separate from individual lane delivery.

    The scope is named once, by the union context this consumer was
    configured with; the kind of container that key addresses is the only
    thing this constructor adds to it.
    """

    def __init__(
        self,
        *,
        scope_kind: ScopeKind,
        tracker: ScopePlanReader,
        refs: WorkRefReader,
        git: GitService,
        runner: CheckChainRunner,
        context: UnionTickContext,
        config: AppConfig,
        committer_name: str,
        committer_email: str,
    ) -> None:
        self._tracker = tracker
        # Required and undefaulted: the port satisfies the role structurally,
        # so a default would hand a caller the carrier that answers nothing
        # about a scope lane without anybody choosing it.
        self._refs: WorkRefReader = refs
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
        """Return an observation only while its participant roster remains current."""
        roster = await self._roster()

        async def validate_roster() -> None:
            if await self._roster() != roster:
                raise UnionHeadReadError(
                    scope_key=self._scope.key,
                    branch=None,
                    reason="the scope union roster changed during verification",
                )

        return await self._tick.verify(
            lane_branches=roster, validate_roster=validate_roster
        )

    async def _roster(self) -> tuple[UnionLaneBranch, ...]:
        """Rank complete ordinary membership without dispatch eligibility.

        A retained lane still participates when it has no current work or
        is blocked from another dispatch. Structural criterion and record
        issues are facts for planning, not independent delivery branches.
        """
        plan = await read_scope_facts(ref=self._scope, tracker=self._tracker)
        participants = frozenset(
            issue.issue_key
            for issue in plan.scope.issues
            if "criterion" not in issue.issue_labels
            and not issue.issue_labels & RECORD_KINDS
        )
        if not participants:
            raise UnionHeadReadError(
                scope_key=self._scope.key,
                branch=None,
                reason="the scope contains no participating lane to compose",
            )
        ranking = plan_topology(
            issues=(*plan.scope.issues, *plan.dependencies),
            candidate_keys=participants,
            blocking_issue_keys=frozenset(),
        )
        roster = [
            await self._lane_branch(issue_key=lane.issue.issue_key)
            for lane in ranking.ready
        ]
        return tuple(roster)

    async def _lane_branch(self, *, issue_key: str) -> UnionLaneBranch:
        """The one deliverable ref the lane recorded, or a typed refusal.

        Asked of the narrow ref-reading role and not of the wider carrier: on
        the scope path the branch a lane delivers is written on that lane's own
        run-state record, and the role is what reads it there (KOD-842).
        """
        deliverables = [
            ref
            for ref in await self._refs.work_refs(issue_key=issue_key)
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
