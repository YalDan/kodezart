"""What the entry does with an organize report, over the real entry.

The composed cases substitute their own entry to pin the engine's reaction
to a halt; these drive ``ScopeEntry`` itself, so the reaction they pin is
the entry's own.
"""

import pytest

from kodezart.domain.errors import OrganizeHaltError
from kodezart.services.scope_entry import ScopeEntry
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.organize import MandateKind
from kodezart.types.domain.organize_owner import (
    OrganizeReport,
    StageHaltCause,
    StageHaltReport,
)
from tests.integration.test_scope_runtime import ORIGIN, SCOPE, board

REPOSITORY = RepoEntry(url=ORIGIN, trunk="trunk")

HALTED = OrganizeReport(
    halt=StageHaltReport.model_validate(
        {
            "cause": "stage_incomplete",
            "phase": "criteria",
            "unlabelledIssueIds": ["B"],
        }
    )
)


class StubOrganizer:
    """Answers one report and records that it was asked."""

    def __init__(self, report):
        self.report = report
        self.runs = []

    async def run(self, *, scope, repository, job_id):
        self.runs.append((scope, repository, job_id))
        return self.report


def entry(organizer):
    return ScopeEntry(approvals=board(lanes=("A",)), stages_for=lambda _url: organizer)


async def admit(organizer):
    await entry(organizer).admit(scope=SCOPE, repository=REPOSITORY, job_id="entry-job")


async def test_a_halted_report_refuses_the_run_and_carries_the_halt():
    """A halt is the stages' answer, so the run does not begin on it.

    Returning here would start a walk over members the stages said are not
    ready, with nothing carrying why.
    """
    organizer = StubOrganizer(HALTED)

    with pytest.raises(OrganizeHaltError) as caught:
        await admit(organizer)

    assert caught.value.report.halt.cause is StageHaltCause.STAGE_INCOMPLETE
    assert caught.value.report.halt.unlabelled_issue_ids == ("B",)
    assert len(organizer.runs) == 1


async def test_a_completed_report_admits_the_run():
    organizer = StubOrganizer(
        OrganizeReport(completed_phases=(MandateKind.TICKET, MandateKind.CRITERIA))
    )

    await admit(organizer)

    assert len(organizer.runs) == 1


async def test_an_operation_with_no_table_admits_an_approved_scope_unorganized():
    """No table means no stage to run, and no organizer to ask."""
    asked = []

    def stages_for(url):
        asked.append(url)
        return None

    await ScopeEntry(approvals=board(lanes=("A",)), stages_for=stages_for).admit(
        scope=SCOPE, repository=REPOSITORY, job_id="entry-job"
    )

    assert asked == [ORIGIN]
