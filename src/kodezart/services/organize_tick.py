"""The configured Organize owner, on the scheduler under its own run identity."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from kodezart.domain.errors import OrganizeHaltError
from kodezart.services.scope_organizer import ScopeOrganizer
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.operation import OrganizeScopeBinding, RepoEntry, RunKind
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.session import SessionType

#: The record kind the organize tick reports its runs under.  Its own, never
#: the grooming kind: the grooming session reads the newest row of its log as
#: the start of its window, so a row this tick left there would move it.
ORGANIZE_RUN_KIND: Final = RunKind.ORGANIZE

#: The name the tick is scheduled and titled under, the organize session
#: type's own spelling.  One constant for both, so the title a run carries and
#: the title its record is verified by are one string.
ORGANIZE_PASS_NAME: Final = SessionType.ORGANIZE_PASS.value


@dataclass(frozen=True)
class OrganizeTarget:
    binding: OrganizeScopeBinding
    repository: RepoEntry
    organizer: ScopeOrganizer


class OrganizeTick:
    def __init__(self, *, targets: Sequence[OrganizeTarget]) -> None:
        self._targets = tuple(targets)

    async def run(self, started_at: datetime) -> PassRun:
        identity = RunIdentity(
            kind=ORGANIZE_RUN_KIND,
            name=ORGANIZE_PASS_NAME,
            started_at=started_at,
        )
        first_halt: OrganizeHaltError | None = None
        for target in self._targets:
            report = await target.organizer.run(
                scope=target.binding.scope,
                repository=target.repository,
                job_id=identity.title(),
            )
            if report.halt is not None and first_halt is None:
                first_halt = OrganizeHaltError(
                    scope=target.binding.scope,
                    report=report,
                )
        if first_halt is not None:
            raise first_halt
        return PassRun.RAN if self._targets else PassRun.SKIPPED
