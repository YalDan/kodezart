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
from kodezart.domain.errors import OrganizeWriteRefusalError
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.organize import MandateKind
from tests.chains.test_native_fire import TRUNK_BRANCHES, WORK_SHA, native_evaluation
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeGitService,
    FakeWorkspaceProvider,
    PassThroughGate,
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
        gate=PassThroughGate(),
        repo_url=operation.repos[0].url,
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

    Nothing is written onto any member's scope-member set: the gate is a
    property of the addressed scope and of the containers above it, and the
    pass reads it rather than materializing it per member.
    """
    lanes = ("A", "B")
    port = under_milestone(board(lanes=lanes, approved=False))
    port.scope_label_members[SCOPE] = frozenset({ScopeLabel.TRIAGE})
    seeded = dict(port.scope_label_members)

    report, executor = await groom(port, lanes=lanes)

    assert report.halt is None
    assert report.completed_phases == (MandateKind.GROOM,)
    for key in lanes:
        assert GROOM_MARKER in port.issues[key].issue_labels
    assert [
        issue_key
        for issue_key, classification in port.classification_writes
        if classification == GROOM_MARKER
    ] == list(lanes)
    # One admission session per lane, and no member gained a scope member.
    assert sorted({key for key, _, _ in executor.admissions}) == list(lanes)
    assert port.scope_label_members == seeded


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


async def test_a_port_that_swallows_the_marker_write_ends_grooming_before_the_judge():
    """The marker the board does not report is no marker at all.

    The pass's own read-back is what refuses: the write answered with the
    marker, so a comparison over that answer would pass, and the refusal comes
    before the write-back judge is asked about the member at all.
    """
    lanes = ("A",)
    port = under_milestone(board(lanes=lanes, approved=False))
    port.scope_label_members[SCOPE] = frozenset({ScopeLabel.TRIAGE})
    organizer, operation, executor = groomer(port, lanes=lanes)
    journal = swallow_marker_writes(port, executor)

    with pytest.raises(OrganizeWriteRefusalError, match="did not read back"):
        await organizer.run(
            scope=MILESTONE, repository=operation.repos[0], job_id="groom-job"
        )

    assert port.classification_writes == [("A", GROOM_MARKER)]
    assert GROOM_MARKER not in port.issues["A"].issue_labels
    # Observed, then written: the lane's assessment and its independent
    # verification both ran before the write; nothing was opened after it.
    assert [key for key, _, _ in executor.admissions] == ["A", "A"]
    assert executor.organize_calls[journal[0] :] == []
