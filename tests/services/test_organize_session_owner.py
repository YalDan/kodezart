"""The session owner: one session per open phase, one read afterwards.

Over the port double and the executor double every consumer is tested on.
The session double stands in for the tracker tools a live session carries:
it reads the marker and the owed member keys off the prompt it is handed and
labels those members through the port, the way the live session labels them
through the tracker server. What the owner then reads off the board is the
whole of what it reports.
"""

import re
from collections.abc import AsyncGenerator, Sequence

from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.services.agent_service import AgentService
from kodezart.services.organize_session_owner import (
    OrganizeSessionOwner,
    tracker_tool_selector,
)
from kodezart.types.domain.agent import AgentEvent, ResultEvent
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig, ScopeLabel
from kodezart.types.domain.organize import MandateKind
from kodezart.types.domain.organize_owner import OrganizeReport, StageHaltCause
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.session import (
    AllowedTools,
    PermissionMode,
    SessionType,
)
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import (
    NO_SUBAGENTS,
    UNCONFIGURED_SESSION_POLICY,
    AgentDefinition,
    SessionPolicy,
)
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeTrackerPort,
    FakeWorkspaceProvider,
    make_tracker_issue,
)
from tests.prompts.test_prompt_wiring import load_registry

SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="scratch-project")
LANES = ("A", "B")
GROOM_MARKER = "graph complete"
TICKET_MARKER = "body complete"
CRITERIA_MARKER = "criteria complete"
WORKING_DIR = "/tmp/kodezart-organize-session-fixture"


def operation() -> OperationConfig:
    """The three-row table over one repository, each marker its own label."""
    return OperationConfig.model_validate(
        {
            "operation_name": "fixture",
            "workspace": "fixture-workspace",
            "scope_labels": {
                "triage": "candidate scope",
                "proposed": "proposed scope",
                "approved": "approved scope",
            },
            "issue_labels": {
                "criterion": "criterion",
                "decision": "decision",
                "tracker": "tracker",
                "groomed": GROOM_MARKER,
                "body": TICKET_MARKER,
                "criteria": CRITERIA_MARKER,
            },
            "repos": [{"url": "https://example.invalid/repository", "trunk": "main"}],
            "organize_mandates": [
                {
                    "kind": "groom",
                    "gate_label_key": "scope_labels.triage",
                    "rubric_prompt_key": "organize_groom_rubric",
                    "admission_prompt_key": "organize_assess",
                    "terminal_marker_key": "issue_labels.groomed",
                },
                {
                    "kind": "ticket",
                    "gate_label_key": "scope_labels.approved",
                    "rubric_prompt_key": "organize_spec_rubric",
                    "admission_prompt_key": "organize_assess",
                    "terminal_marker_key": "issue_labels.body",
                },
                {
                    "kind": "criteria",
                    "gate_label_key": "issue_labels.body",
                    "rubric_prompt_key": "organize_spec_rubric",
                    "admission_prompt_key": "organize_assess",
                    "terminal_marker_key": "issue_labels.criteria",
                },
            ],
        }
    )


def board(
    *, scope_labels: frozenset[ScopeLabel], labels: dict[str, frozenset[str]]
) -> FakeTrackerPort:
    """Two lanes under one project, each with one criterion child."""
    issues = []
    for key in LANES:
        issues.append(
            make_tracker_issue(
                key, project_id=SCOPE.key, issue_labels=labels.get(key, frozenset())
            )
        )
        issues.append(
            make_tracker_issue(
                f"{key}/check",
                parent_key=key,
                project_id=SCOPE.key,
                issue_labels=frozenset({"criterion"}),
            )
        )
    return FakeTrackerPort(
        issues=issues,
        scope_containers=[
            ScopeContainer(
                ref=SCOPE,
                name="scratch",
                description="",
                url="https://tracker.invalid/project/scratch",
            )
        ],
        scope_memberships={SCOPE: [issue.issue_key for issue in issues]},
        scope_label_members={SCOPE: scope_labels},
    )


class LabellingSession:
    """The session double: labels the owed members the prompt names.

    The same call record the executor double keeps, so a case reads the
    request the way every executor case does. *labels* narrows which of
    the owed members it labels, so a case can leave one out the way a live
    session leaves out a member it escalates.
    """

    def __init__(self, port: FakeTrackerPort, *, labels: Sequence[str] | None) -> None:
        self._port = port
        self._labels = None if labels is None else tuple(labels)
        # The prompt names the tracker's label; the board reads it back as
        # the configured key, which is what the port double stores.
        self._keys = {label: key for key, label in operation().issue_labels.items()}
        self.calls: list[dict[str, object]] = []

    async def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: PermissionMode,
        allowed_tools: AllowedTools,
        skills: SkillsSelection,
        session_type: SessionType,
        run_identity: RunIdentity | None = None,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append(
            {
                "prompt": prompt,
                "cwd": cwd,
                "output_format": output_format,
                "allowed_tools": allowed_tools,
                "session_id": session_id,
                "permission_mode": permission_mode,
                "skills": skills,
                "session_type": session_type,
                "run_identity": run_identity,
            }
        )
        marker = re.search(r"Marker to add: `(.*?)`", prompt)
        assert marker is not None
        owed = re.findall(r"^- (\S+)$", prompt, re.M)
        for key in owed:
            if self._labels is None or key in self._labels:
                await self._port.set_issue_classification(
                    issue_key=key, classification=self._keys[marker[1]]
                )
        yield ResultEvent(
            subtype="result",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="organize-session",
            result="Groomed what the board owed.",
        )


def owner(
    port: FakeTrackerPort,
    executor: LabellingSession,
    *,
    under_approval: bool,
    workspace: FakeWorkspaceProvider | None = None,
) -> OrganizeSessionOwner:
    config = operation()
    rows = [
        row
        for row in config.resolve_organize_mandates()
        if row.role.runs_under_approval is under_approval
    ]
    return OrganizeSessionOwner(
        members=port,
        approvals=port,
        runner=AgentService(
            executor=executor,
            workspace=FakeWorkspaceProvider() if workspace is None else workspace,
            git_base_url="https://example.invalid",
        ),
        prompts=load_registry(
            default_set="claude-opus", bindings=operation_bindings(config)
        ),
        skills=SUPPRESS_ALL_SKILLS,
        phases=rows,
        tracker_server_name="linear",
        working_dir=WORKING_DIR,
    )


async def run(unit: OrganizeSessionOwner) -> OrganizeReport:
    return await unit.run(
        scope=SCOPE,
        repo_url="https://example.invalid/repository",
        base_ref="a" * 40,
        job_id="organize-job",
        visibility=RepoVisibility.UNKNOWN,
    )


async def test_a_closed_gate_opens_no_session_and_reports_nothing() -> None:
    """No triage on the scope: the pre-approval row has nobody to act on."""
    port = board(scope_labels=frozenset(), labels={})
    session = LabellingSession(port, labels=None)

    report = await run(owner(port, session, under_approval=False))

    assert report == OrganizeReport()
    assert session.calls == []
    assert port.classification_writes == []


async def test_an_open_gate_runs_one_session_and_the_labelled_board_completes() -> None:
    """One session for the phase, and the board read afterwards completes it."""
    port = board(scope_labels=frozenset({ScopeLabel.TRIAGE}), labels={})
    session = LabellingSession(port, labels=None)

    report = await run(owner(port, session, under_approval=False))

    assert report.halt is None
    assert report.completed_phases == (MandateKind.GROOM,)
    assert len(session.calls) == 1
    assert port.classification_writes == [(key, "groomed") for key in LANES]
    for key in LANES:
        assert "groomed" in port.issues[key].issue_labels
        assert "groomed" not in port.issues[f"{key}/check"].issue_labels


async def test_a_member_the_session_left_unlabelled_halts_naming_it() -> None:
    """The board is the record: a member without the marker is named, once."""
    port = board(scope_labels=frozenset({ScopeLabel.TRIAGE}), labels={})
    session = LabellingSession(port, labels=("A",))

    report = await run(owner(port, session, under_approval=False))

    assert report.completed_phases == ()
    assert report.halt is not None
    assert report.halt.cause is StageHaltCause.STAGE_INCOMPLETE
    assert report.halt.phase is MandateKind.GROOM
    assert report.halt.unlabelled_issue_ids == ("B",)
    assert len(session.calls) == 1


async def test_a_board_that_owes_nothing_completes_without_a_session() -> None:
    """Every member already carries the marker: the phase costs no session."""
    port = board(
        scope_labels=frozenset({ScopeLabel.TRIAGE}),
        labels=dict.fromkeys(LANES, frozenset({"groomed"})),
    )
    session = LabellingSession(port, labels=None)

    report = await run(owner(port, session, under_approval=False))

    assert report == OrganizeReport(completed_phases=(MandateKind.GROOM,))
    assert session.calls == []


async def test_an_approved_scope_closes_the_pre_approval_row() -> None:
    """Approval ends the groom phase: triage still there, nobody admitted."""
    port = board(
        scope_labels=frozenset({ScopeLabel.TRIAGE, ScopeLabel.APPROVED}), labels={}
    )
    session = LabellingSession(port, labels=None)

    report = await run(owner(port, session, under_approval=False))

    assert report == OrganizeReport()
    assert session.calls == []


async def test_the_run_stages_run_in_order_each_gated_on_the_last() -> None:
    """Ticket on approval, then criteria on the ticket marker: two sessions."""
    port = board(scope_labels=frozenset({ScopeLabel.APPROVED}), labels={})
    session = LabellingSession(port, labels=None)

    report = await run(owner(port, session, under_approval=True))

    assert report.halt is None
    assert report.completed_phases == (MandateKind.TICKET, MandateKind.CRITERIA)
    assert len(session.calls) == 2
    assert port.classification_writes == [
        *((key, "body") for key in LANES),
        *((key, "criteria") for key in LANES),
    ]
    first, second = (str(call["prompt"]) for call in session.calls)
    assert f"Marker to add: `{TICKET_MARKER}`" in first
    assert f"Marker to add: `{CRITERIA_MARKER}`" in second


async def test_the_prompt_carries_the_marker_the_scope_and_the_fire_rule() -> None:
    """What the session is told, and the one label it is never offered."""
    port = board(scope_labels=frozenset({ScopeLabel.TRIAGE}), labels={})
    session = LabellingSession(port, labels=None)

    await run(owner(port, session, under_approval=False))

    (call,) = session.calls
    prompt = str(call["prompt"])
    assert f"Marker to add: `{GROOM_MARKER}`" in prompt
    assert f"Add the marker `{GROOM_MARKER}`" in prompt
    assert "project `scratch-project`" in prompt
    assert "- A\n- B\n" in prompt
    assert "Open question for the fire to rule on before it starts" in prompt
    assert "never an open human choice" in prompt
    assert "add the label `decision`" in prompt
    assert "Never touch a member labelled `tracker`" in prompt
    assert "{{" not in prompt
    # The approval label appears only where the session is told never to set it.
    offering = [
        line
        for line in prompt.splitlines()
        if "approved scope" in line and "Never add or remove" not in line
    ]
    assert offering == []


async def test_the_session_request_is_the_organize_pass_with_the_tracker_tools() -> (
    None
):
    """Unattended, the tracker family only, the neutral directory, no repo."""
    port = board(scope_labels=frozenset({ScopeLabel.TRIAGE}), labels={})
    session = LabellingSession(port, labels=None)
    workspace = FakeWorkspaceProvider()

    await run(owner(port, session, under_approval=False, workspace=workspace))

    (call,) = session.calls
    assert call["session_type"] is SessionType.ORGANIZE_PASS
    assert call["permission_mode"] is PermissionMode.UNATTENDED
    assert call["allowed_tools"] == [tracker_tool_selector("linear")]
    assert call["allowed_tools"] == ["mcp__linear__*"]
    assert call["cwd"] == WORKING_DIR
    assert call["output_format"] is None
    # No repository workspace was acquired for the session.
    assert workspace.calls == []
