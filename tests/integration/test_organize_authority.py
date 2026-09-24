"""What the pre-approval pass may do, and what its terminal act stands on.

Over the production composition — ``build_scope_organizer`` on the shipped
side of approval — so the authority these cases exercise is the one a
scheduled tick reaches and not a second wiring written here.
"""

import pytest

from kodezart.composition.organize import build_scope_organizer
from kodezart.config.app import AppConfig
from kodezart.config.organize import OrganizeSettings
from kodezart.config.write_back import WriteBackSettings
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.organize import MandateKind
from kodezart.types.domain.organize_owner import StageHaltCause
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from tests.chains.test_native_fire import TRUNK_BRANCHES, WORK_SHA, native_evaluation
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeGitService,
    FakeWorkspaceProvider,
)
from tests.integration.test_scope_entry import (
    GROOM_MARKER,
    MILESTONE,
    OrganizingExecutor,
    organize_operation,
    under_milestone,
)
from tests.integration.test_scope_runtime import SCOPE, board
from tests.prompts.test_prompt_wiring import load_registry


def groomer(port, *, lanes, executor=None):
    """The shipped pre-approval organizer over *port*, and the double it reads.

    ``under_approval=False`` is the tick's own side of the table, so the rows
    this organizer runs are the rows the scheduled pass runs.
    """
    operation = organize_operation()
    executor = (
        OrganizingExecutor(
            [
                native_evaluation(checks={f"{key}/check": f"{key} live Check  bytes"})
                for key in lanes
                for _ in range(2)
            ],
            port=port,
        )
        if executor is None
        else executor
    )
    git = FakeGitService(remote_branch_shas=dict.fromkeys(TRUNK_BRANCHES, WORK_SHA))
    workspace = FakeWorkspaceProvider(git=git)
    organizer = build_scope_organizer(
        config=AppConfig(
            organize=OrganizeSettings(max_admission_rounds=2, max_convergence_rounds=2),
            write_back=WriteBackSettings(max_verify_rounds=2),
        ),
        operation=operation,
        tracker=port,
        runner=AgentService(
            executor=executor,
            workspace=workspace,
            git_base_url="https://example.invalid",
        ),
        workspace=workspace,
        git=git,
        prompts=load_registry(
            default_set="claude-opus", bindings=operation_bindings(operation)
        ),
        skills=SUPPRESS_ALL_SKILLS,
        under_approval=False,
    )
    return organizer, operation, executor


async def groom(port, *, lanes, executor=None, job="groom-job"):
    organizer, operation, executor = groomer(port, lanes=lanes, executor=executor)
    report = await organizer.run(
        scope=MILESTONE, repository=operation.repos[0], job_id=job
    )
    return report, executor


async def test_a_milestone_addressed_scope_grooms_on_its_projects_triage():
    """A milestone has no label level; the triage member above it opens the gate.

    The phase marker is the only thing written: one classification per lane,
    in lane order, and nothing else — the gate is a property of the addressed
    scope and of the containers above it, and the pass reads it rather than
    materializing it on any member. One session labels both lanes.
    """
    lanes = ("A", "B")
    port = under_milestone(board(lanes=lanes, approved=False))
    port.scope_label_members[SCOPE] = frozenset({ScopeLabel.TRIAGE})

    report, executor = await groom(port, lanes=lanes)

    assert report.halt is None
    assert report.completed_phases == (MandateKind.GROOM,)
    for key in lanes:
        assert GROOM_MARKER in port.issues[key].issue_labels
    assert port.classification_writes == [(key, GROOM_MARKER) for key in lanes]
    # One session for the phase, and it labelled each lane once.
    assert len(executor.organize_calls) == 1
    assert sorted({key for key, _, _ in executor.admissions}) == list(lanes)


async def test_a_milestone_whose_project_lacks_triage_stays_idle():
    """The same composition, the same milestone, and no triage above it.

    Nothing opens the gate, so grooming has nobody to act on: no session
    opens and nothing is written.
    """
    lanes = ("A", "B")
    port = under_milestone(board(lanes=lanes, approved=False))
    port.scope_label_members[SCOPE] = frozenset()

    report, executor = await groom(port, lanes=lanes)

    assert report.halt is None
    assert report.completed_phases == ()
    assert executor.organize_calls == []
    assert executor.admissions == []
    assert port.classification_writes == []
    for key in lanes:
        assert GROOM_MARKER not in port.issues[key].issue_labels


@pytest.mark.parametrize(
    ("issue_carries", "grooms"),
    [(True, True), (False, False)],
    ids=["issue-carries-triage", "project-alone-carries-triage"],
)
async def test_an_issue_addressed_scope_reads_triage_off_the_addressed_issue(
    issue_carries, grooms
):
    """The bound of the walk on an issue-addressed scope, made visible.

    The lane belongs to the addressed project, and that project carries
    triage in both arms. Only the addressed issue's own members open the
    pre-approval gate on an issue-addressed scope: with triage on the issue
    the lane is groomed, and with triage on the project alone nothing opens,
    no session runs and nothing is written. The parity control at the end
    shows the project is above the lane for the approval cascade on this
    same board, so the idle arm is the bound and not a lane outside the
    project.
    """
    lanes = ("A",)
    port = board(lanes=lanes, approved=False)
    port.issues["A"] = port.issues["A"].model_copy(update={"project_id": SCOPE.key})
    port.scope_label_members[SCOPE] = frozenset({ScopeLabel.TRIAGE})
    scope = ScopeRef(kind=ScopeKind.ISSUE, key="A")
    if issue_carries:
        port.scope_label_members[scope] = frozenset({ScopeLabel.TRIAGE})
    organizer, operation, executor = groomer(port, lanes=lanes)

    report = await organizer.run(
        scope=scope, repository=operation.repos[0], job_id="groom-job"
    )

    assert report.halt is None
    if grooms:
        assert report.completed_phases == (MandateKind.GROOM,)
        assert port.classification_writes == [("A", GROOM_MARKER)]
    else:
        assert report.completed_phases == ()
        assert executor.organize_calls == []
        assert port.classification_writes == []
        assert GROOM_MARKER not in port.issues["A"].issue_labels
    # Parity: approval on the project alone reaches the lane on this board.
    assert await port.execution_approved(issue_key="A") is False
    port.scope_label_members[SCOPE] = frozenset({ScopeLabel.APPROVED})
    assert await port.execution_approved(issue_key="A") is True


def swallow_marker_writes(port, executor):
    """The board accepts the marker write, answers with it, and does not keep it.

    The write lands in the ordered journal with the number of sessions opened
    before it, and its answer carries the marker, while the stored member
    keeps the labels it had. A pass that trusted the write's own answer could
    not tell this board from one that kept the label; only a cold read can.
    """
    journal = []

    async def swallowing(*, issue_key, classification, holder=None):
        port.classification_writes.append((issue_key, classification))
        journal.append(len(executor.organize_calls))
        issue = await port.read_issue(issue_key=issue_key)
        return issue.model_copy(
            update={"issue_labels": issue.issue_labels | {classification}}
        )

    port.set_issue_classification = swallowing
    return journal


async def test_a_board_that_drops_the_marker_write_halts_the_phase_naming_the_member():
    """The marker the board does not report is no marker at all.

    The pass's own cold read after the session is what decides: the write
    answered with the marker, so a pass trusting that answer would complete
    the phase, and the re-read finds the member still owing it. No second
    session is spent finding that out.
    """
    lanes = ("A",)
    port = under_milestone(board(lanes=lanes, approved=False))
    port.scope_label_members[SCOPE] = frozenset({ScopeLabel.TRIAGE})
    organizer, operation, executor = groomer(port, lanes=lanes)
    journal = swallow_marker_writes(port, executor)
    # The lane carries a label that is not the marker before the run, so the
    # halt below is about the marker's absence and not an empty label set.
    assert port.issues["A"].issue_labels - {GROOM_MARKER}

    report = await organizer.run(
        scope=MILESTONE, repository=operation.repos[0], job_id="groom-job"
    )

    assert report.completed_phases == ()
    assert report.halt is not None
    assert report.halt.cause is StageHaltCause.STAGE_INCOMPLETE
    assert report.halt.phase is MandateKind.GROOM
    assert report.halt.unlabelled_issue_ids == ("A",)
    assert port.classification_writes == [("A", GROOM_MARKER)]
    assert GROOM_MARKER not in port.issues["A"].issue_labels
    # One session, and the write was made inside it.
    assert journal == [1]
    assert len(executor.organize_calls) == 1
