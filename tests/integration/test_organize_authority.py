"""What the run's stages act on, and what their terminal act stands on.

Over the production composition — ``build_scope_organizer``, the organizer a
scope run's entry builds — so the authority these cases exercise is the one a
run reaches and not a second wiring written here.
"""

from kodezart.composition.organize import build_scope_organizer
from kodezart.config.app import AppConfig
from kodezart.config.organize import OrganizeSettings
from kodezart.config.write_back import WriteBackSettings
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.organize import MandateKind
from kodezart.types.domain.organize_owner import StageHaltCause
from tests.chains.test_native_fire import TRUNK_BRANCHES, WORK_SHA, native_evaluation
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeGitService,
    FakeWorkspaceProvider,
)
from tests.integration.test_scope_entry import (
    MILESTONE,
    TICKET_MARKER,
    OrganizingExecutor,
    organize_operation,
    under_milestone,
)
from tests.integration.test_scope_runtime import SCOPE, STAGED, board
from tests.prompts.test_prompt_wiring import load_registry


def organizer_over(port, *, lanes, executor=None):
    """The shipped organizer over *port*, and the double it reads.

    The rows this organizer runs are the two run stages, the whole table:
    every row runs under approval, and the scope's approval is what admits a
    member to the first.
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
    )
    return organizer, operation, executor


async def stage(port, *, lanes, executor=None, job="stage-job"):
    organizer, operation, executor = organizer_over(
        port, lanes=lanes, executor=executor
    )
    report = await organizer.run(
        scope=MILESTONE, repository=operation.repos[0], job_id=job
    )
    return report, executor


async def test_a_milestone_addressed_scope_runs_its_stages_on_its_projects_approval():
    """A milestone has no label level; the approval above it admits the run.

    The stage markers are the only things written: one classification per
    lane per stage, in lane order, and nothing else — approval is a property
    of the containers above the addressed scope, and the stages read it
    rather than materializing it on any member. One session per stage
    labels both lanes.
    """
    lanes = ("A", "B")
    port = under_milestone(board(lanes=lanes, approved=False, staged=False))
    port.scope_label_members[SCOPE] = frozenset({ScopeLabel.APPROVED})

    report, executor = await stage(port, lanes=lanes)

    assert report.halt is None
    assert report.completed_phases == (MandateKind.TICKET, MandateKind.CRITERIA)
    for key in lanes:
        assert {TICKET_MARKER, STAGED} <= port.issues[key].issue_labels
    assert port.classification_writes == [
        *((key, TICKET_MARKER) for key in lanes),
        *((key, STAGED) for key in lanes),
    ]
    # One session per stage, and each labelled both lanes.
    assert len(executor.organize_calls) == 2
    assert sorted({key for key, _, _ in executor.admissions}) == list(lanes)


async def test_a_milestone_whose_project_lacks_approval_stays_idle():
    """The same composition, the same milestone, and no approval above it.

    Triage on the project admits nothing: what a triaged scope needs is the
    intake passes' work, and the stages read approval alone. No session
    opens and nothing is written.
    """
    lanes = ("A", "B")
    port = under_milestone(board(lanes=lanes, approved=False, staged=False))
    port.scope_label_members[SCOPE] = frozenset({ScopeLabel.TRIAGE})

    report, executor = await stage(port, lanes=lanes)

    assert report.halt is None
    assert report.completed_phases == ()
    assert executor.organize_calls == []
    assert executor.admissions == []
    assert port.classification_writes == []
    for key in lanes:
        assert not {TICKET_MARKER, STAGED} & port.issues[key].issue_labels


def swallow_marker_writes(port, executor):
    """The board accepts the marker write, answers with it, and does not keep it.

    The write lands in the ordered journal with the number of sessions opened
    before it, and its answer carries the marker, while the stored member
    keeps the labels it had. A stage that trusted the write's own answer could
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

    The stage's own cold read after the session is what decides: the write
    answered with the marker, so a stage trusting that answer would complete
    the phase, and the re-read finds the member still owing it. No second
    session is spent finding that out, and the second stage never opens.
    """
    lanes = ("A",)
    # The staged board: the lane already carries the criteria stage's label,
    # which is not the ticket marker, so the halt below is about the marker's
    # absence and not an empty label set.
    port = under_milestone(board(lanes=lanes, approved=False))
    port.scope_label_members[SCOPE] = frozenset({ScopeLabel.APPROVED})
    organizer, operation, executor = organizer_over(port, lanes=lanes)
    journal = swallow_marker_writes(port, executor)
    assert port.issues["A"].issue_labels - {TICKET_MARKER}

    report = await organizer.run(
        scope=MILESTONE, repository=operation.repos[0], job_id="stage-job"
    )

    assert report.completed_phases == ()
    assert report.halt is not None
    assert report.halt.cause is StageHaltCause.STAGE_INCOMPLETE
    assert report.halt.phase is MandateKind.TICKET
    assert report.halt.unlabelled_issue_ids == ("A",)
    assert port.classification_writes == [("A", TICKET_MARKER)]
    assert TICKET_MARKER not in port.issues["A"].issue_labels
    # One session, and the write was made inside it.
    assert journal == [1]
    assert len(executor.organize_calls) == 1
