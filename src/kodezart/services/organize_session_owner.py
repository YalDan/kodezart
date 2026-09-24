"""The Organize owner that runs one session per phase and verifies with one read.

Each phase row it is given is settled the same way: read the gate the cheap
way, and if the gate is open and a member owes the phase's marker, render
the phase's prompt, run ONE session with the tracker tools the host attaches
(session type ORGANIZE_PASS, unattended, no repository workspace), then read
the scope once more through the port and report. The session does the
board work with its own tools; kodezart keeps no lease, writes no marker,
takes no proof and runs no verifier. What the board reports afterwards is
the whole record: a member that owes the marker and lacks it is named in a
stage-incomplete halt. An escalated member is one of those and is never a
work subject: it is counted and named, and no session is spent on it until
a person removes the escalation label.

Measured 2026-09-24 (KOD-1239): the cascade-reading owner cost about 5,900
tracker calls per settling round; one session with the tracker tools took
8 tool calls and 45 seconds over the same scope.
"""

from collections.abc import Sequence
from typing import NamedTuple

from kodezart.core.constants import UNATTENDED_PERMISSION_MODE
from kodezart.core.logging import get_logger
from kodezart.core.protocols import (
    AgentRunner,
    PromptSetProvider,
    ScopeFamilyReader,
    TrackerScopeApprovalReader,
)
from kodezart.domain.organize import is_organize_subject, stage_unlabelled
from kodezart.services.scope_approval import scope_approved, scope_carries
from kodezart.types.domain.agent import ResultEvent
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationMemberAbsentError, ScopeLabel
from kodezart.types.domain.organize import (
    MandateKind,
    OrganizeLabelNamespace,
    ResolvedMandateSpec,
    split_label_key,
)
from kodezart.types.domain.organize_owner import (
    OrganizeReport,
    StageHaltCause,
    StageHaltReport,
    StageIncompleteHalt,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS


class _RunFields(NamedTuple):
    """The run's identity as every phase's log line carries it."""

    run_identity: str
    repository: str
    base_ref: str
    visibility: str
    scope: dict[str, object]


def tracker_tool_selector(server_name: str) -> str:
    """The allowlist entry that names every tool of the tracker's MCP server.

    The spelling the CLI accepts for a server family, and the one the
    measured headless runs used: ``mcp__<server>__*``.
    """
    return f"mcp__{server_name}__*"


class OrganizeSessionOwner:
    """Settle the phase rows it is given, one session per open phase."""

    def __init__(
        self,
        *,
        members: ScopeFamilyReader,
        approvals: TrackerScopeApprovalReader,
        runner: AgentRunner,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        phases: Sequence[ResolvedMandateSpec],
        tracker_server_name: str,
        working_dir: str,
    ) -> None:
        self._members, self._approvals, self._runner = members, approvals, runner
        self._prompts, self._skills = prompts, skills
        self._phases = tuple(phases)
        if not self._phases:
            raise OperationMemberAbsentError(
                missing="organize_mandates", stops="Organize construction"
            )
        self._allowed_tools = [tracker_tool_selector(tracker_server_name)]
        self._working_dir = working_dir
        self._log = get_logger(__name__)

    async def run(
        self,
        *,
        scope: ScopeRef,
        repo_url: str,
        base_ref: str,
        job_id: str,
        visibility: RepoVisibility,
    ) -> OrganizeReport:
        """The contract the organizer, the entry and the tick rely on.

        *repo_url*, *base_ref* and *visibility* are the organizer's reading
        of the repository; this owner opens no workspace and hands the
        session no repository, so they reach the phase's log line and
        nothing else.
        """
        approved = await scope_approved(ref=scope, tracker=self._approvals)
        run = _RunFields(
            run_identity=job_id,
            repository=repo_url,
            base_ref=base_ref,
            visibility=visibility.value,
            scope=scope.model_dump(),
        )
        completed: list[MandateKind] = []
        for phase in self._phases:
            marker = split_label_key(phase.spec.terminal_marker_key)[1]
            if approved is not phase.role.runs_under_approval:
                await self._settled(phase, run, gate_open=False, owed=(), left=())
                continue
            namespace, gate = split_label_key(phase.spec.gate_label_key)
            if namespace is OrganizeLabelNamespace.SCOPE:
                gate_open = approved
                if gate != ScopeLabel.APPROVED.value:
                    gate_open = await scope_carries(
                        ref=scope, member=ScopeLabel(gate), tracker=self._approvals
                    )
                if not gate_open:
                    await self._settled(phase, run, gate_open=False, owed=(), left=())
                    continue
                issues = await self._members.scope_issues(ref=scope)
                admitted = [issue for issue in issues if is_organize_subject(issue)]
            else:
                issues = await self._members.scope_issues(ref=scope)
                admitted = [
                    issue
                    for issue in issues
                    if is_organize_subject(issue) and gate in issue.issue_labels
                ]
            owed = tuple(
                issue.issue_key
                for issue in admitted
                if marker not in issue.issue_labels
            )
            report: str | None = None
            if owed:
                report = await self._session(phase=phase, scope=scope, owed=owed)
                issues = await self._members.scope_issues(ref=scope)
            left = stage_unlabelled(issues=issues, marker=marker)
            await self._settled(
                phase, run, gate_open=True, owed=owed, left=left, report=report
            )
            if left:
                return OrganizeReport(
                    completed_phases=tuple(completed),
                    halt=StageHaltReport(
                        StageIncompleteHalt(
                            cause=StageHaltCause.STAGE_INCOMPLETE,
                            phase=phase.spec.kind,
                            unlabelled_issue_ids=left,
                        )
                    ),
                )
            completed.append(phase.spec.kind)
        return OrganizeReport(completed_phases=tuple(completed))

    def render(
        self, *, phase: ResolvedMandateSpec, scope: ScopeRef, owed: Sequence[str]
    ) -> str:
        """The one prompt a phase's session is given.

        The scope is named by the kind that decides what its key is to the
        tracker: a project's, an initiative's or a milestone's id, or an
        issue's key.
        """
        kind = phase.spec.kind
        return self._prompts.template_for(PromptKey.ORGANIZE_SESSION).render(
            {
                "scope_key": scope.key,
                "scope_project": True if scope.kind is ScopeKind.PROJECT else None,
                "scope_issue": True if scope.kind is ScopeKind.ISSUE else None,
                "scope_initiative": (
                    True if scope.kind is ScopeKind.INITIATIVE else None
                ),
                "scope_milestone": True if scope.kind is ScopeKind.MILESTONE else None,
                "phase_marker": phase.terminal_marker,
                "owed_members": tuple(owed),
                "phase_groom": True if kind is MandateKind.GROOM else None,
                "phase_ticket": True if kind is MandateKind.TICKET else None,
                "phase_criteria": True if kind is MandateKind.CRITERIA else None,
            }
        )

    async def _session(
        self, *, phase: ResolvedMandateSpec, scope: ScopeRef, owed: tuple[str, ...]
    ) -> str | None:
        """Run the phase's one session; return the report it ended with."""
        prompt = self.render(phase=phase, scope=scope, owed=owed)
        result: ResultEvent | None = None
        async for event in self._runner.stream_in_workspace(
            prompt=prompt,
            workspace_path=self._working_dir,
            permission_mode=UNATTENDED_PERMISSION_MODE,
            allowed_tools=list(self._allowed_tools),
            skills=self._prompts.session_skills(
                PromptKey.ORGANIZE_SESSION, self._skills
            ),
            session_type=SessionType.ORGANIZE_PASS,
            agents=NO_SUBAGENTS,
            session_policy=self._prompts.session_policy(PromptKey.ORGANIZE_SESSION),
        ):
            if isinstance(event, ResultEvent):
                result = event
        return None if result is None else result.result

    async def _settled(
        self,
        phase: ResolvedMandateSpec,
        run: _RunFields,
        *,
        gate_open: bool,
        owed: tuple[str, ...],
        left: tuple[str, ...],
        report: str | None = None,
    ) -> None:
        """The one log event a phase writes, whatever it found.

        Every field is named here: an event handed a field set at runtime
        has no one shape a reader can pin, and the log scan refuses it.
        """
        await self._log.ainfo(
            "organize_phase_settled",
            run_identity=run.run_identity,
            repository=run.repository,
            base_ref=run.base_ref,
            visibility=run.visibility,
            scope=run.scope,
            phase=phase.spec.kind.value,
            gate=phase.spec.gate_label_key,
            gate_open=gate_open,
            marker=phase.terminal_marker,
            owed=list(owed),
            session_opened=bool(owed),
            unlabelled=list(left),
            session_report=report,
        )
