"""What a scope run does before its first tick."""

from collections.abc import Callable

from kodezart.core.protocols import TrackerScopeApprovalReader
from kodezart.domain.errors import OrganizeHaltError, ScopeNotApprovedError
from kodezart.services.scope_approval import scope_approved
from kodezart.services.scope_organizer import ScopeOrganizer
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.scope import ScopeRef


class ScopeEntry:
    """What a scope run does before its first tick.

    Refuses a scope that is not approved, before any member is read. Then,
    where the operation declares an organize table, runs the two organize
    stages over the approved scope and returns only when every member
    carries each stage's label. Writes nothing itself: the stage labels are
    the owner's writes, under its verified write-back.
    """

    def __init__(
        self,
        *,
        approvals: TrackerScopeApprovalReader,
        stages_for: Callable[[str], ScopeOrganizer | None],
    ) -> None:
        self._approvals = approvals
        self._stages_for = stages_for

    async def admit(
        self, *, scope: ScopeRef, repository: RepoEntry, job_id: str
    ) -> None:
        """Raise unless the run may begin; return on success.

        Raises ``ScopeNotApprovedError``, ``OrganizeHaltError`` and the
        write refusals the organize stages themselves raise.
        """
        if not await scope_approved(ref=scope, tracker=self._approvals):
            raise ScopeNotApprovedError(ref=scope)
        organizer = self._stages_for(repository.url)
        if organizer is None:
            return
        report = await organizer.run(scope=scope, repository=repository, job_id=job_id)
        if report.halt is not None:
            raise OrganizeHaltError(scope=scope, report=report)
