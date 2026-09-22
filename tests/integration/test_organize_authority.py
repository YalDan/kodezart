"""What the pre-approval pass may do, and what its terminal act stands on.

Over the production composition — ``build_scope_organizer`` on the shipped
side of approval — so the authority these cases exercise is the one a
scheduled tick reaches and not a second wiring written here.
"""

from kodezart.composition.organize import build_scope_organizer
from kodezart.config.app import AppConfig
from kodezart.config.organize import OrganizeSettings
from kodezart.config.write_back import WriteBackSettings
from kodezart.core.prompt_namespaces import operation_bindings
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
