"""What one heartbeat tick did to each standing scope it was given.

A report rather than a log line: "this scope is not approved" is the
answer an operator asks the heartbeat for most often, and an answer that
exists only in a log entry cannot be read off the tick that produced it.
Every standing scope the tick was given appears in it, so a scope missing
from the report is a scope the tick never reached.
"""

from enum import StrEnum

from pydantic import ConfigDict

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.scope import ScopeRef


class HeartbeatOutcome(StrEnum):
    """What became of one standing scope on one tick.

    Four members, and they partition: the scope's run was started here, a
    run of it is already live, approval is not on it yet, or asking cost an
    exception. ``UNAPPROVED`` and ``FAILED`` are never one member — a scope
    nobody has approved is the ordinary resting state of a declared row,
    and a read that raised is a fault.
    """

    SUBMITTED = "submitted"
    LIVE = "live"
    UNAPPROVED = "unapproved"
    FAILED = "failed"


class HeartbeatEntry(CamelCaseModel):
    """One standing scope's line of the report.

    ``job_id`` is the job this process holds for the scope: the one just
    submitted, or the live one that stopped a submission. ``detail`` is the
    failure's own rendering and nothing else, so a FAILED line says which
    read refused rather than that something did.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: ScopeRef
    outcome: HeartbeatOutcome
    job_id: str | None = None
    detail: str | None = None


class HeartbeatReport(CamelCaseModel):
    """Every standing scope the tick was given, in the declared order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: tuple[HeartbeatEntry, ...]

    @property
    def ran(self) -> bool:
        """Whether this tick started anything at all.

        A tick that submitted nothing opened no session and produced no
        run, so it has nothing for the scheduler to record — the same
        distinction a gated pass draws, drawn from the report instead of
        from a gate.
        """
        return any(
            entry.outcome is HeartbeatOutcome.SUBMITTED for entry in self.entries
        )
