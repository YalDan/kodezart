"""The configured Organize owner behind the existing grooming scheduler identity."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from kodezart.domain.errors import OrganizeHaltError
from kodezart.services.scope_organizer import ScopeOrganizer
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.operation import OrganizeScopeBinding, RepoEntry, RunKind
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_records import RunIdentity


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
            kind=RunKind.GROOMING,
            name=PromptKey.GROOMING_PASS.value,
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
