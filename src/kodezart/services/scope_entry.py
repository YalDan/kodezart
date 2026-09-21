"""What a scope run does before its first tick."""

from collections.abc import Callable

from kodezart.core.protocols import JobRegistry, TrackerScopeApprovalReader
from kodezart.domain.errors import (
    OrganizeHaltError,
    ScopeNotApprovedError,
    ScopeRunLiveError,
)
from kodezart.domain.scope_submission import prior_live_job
from kodezart.services.scope_approval import scope_approved
from kodezart.services.scope_organizer import ScopeOrganizer
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.scope import ScopeRef


class ScopeEntry:
    """What a scope run does before its first tick.

    Refuses a scope another job in this process is already walking, and a
    scope that is not approved, both before any member is read. Then, where
    the operation declares an organize table, runs the two organize stages
    over the approved scope and returns only when every member carries each
    stage's label. Writes nothing itself: the stage labels are the owner's
    writes, under its verified write-back, and no lease, claim or in-progress
    mark is taken for either refusal (KOD-788).
    """

    def __init__(
        self,
        *,
        approvals: TrackerScopeApprovalReader,
        stages_for: Callable[[str], ScopeOrganizer | None],
        registry: JobRegistry,
    ) -> None:
        self._approvals = approvals
        self._stages_for = stages_for
        self._registry = registry

    async def admit(
        self, *, scope: ScopeRef, repository: RepoEntry, job_id: str
    ) -> None:
        """Raise unless the run may begin; return on success.

        Raises ``ScopeRunLiveError``, ``ScopeNotApprovedError``,
        ``OrganizeHaltError`` and the write refusals the organize stages
        themselves raise.

        Liveness is asked FIRST, and of the record store rather than of the
        tracker: a scope is a queue item and two walks of one scope contend
        over every lane of it, so the run that yields should cost the board
        nothing at all — not even the approval read.
        """
        ahead = prior_live_job(
            live=await self._registry.live_for_scope(scope=scope), job_id=job_id
        )
        if ahead is not None:
            raise ScopeRunLiveError(ref=scope, job_id=ahead.job_id, lane=ahead.lane)
        if not await scope_approved(ref=scope, tracker=self._approvals):
            raise ScopeNotApprovedError(ref=scope)
        organizer = self._stages_for(repository.url)
        if organizer is None:
            return
        report = await organizer.run(scope=scope, repository=repository, job_id=job_id)
        if report.halt is not None:
            raise OrganizeHaltError(scope=scope, report=report)
