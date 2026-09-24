"""One question per lapsed observation, raised inside a verified window (KOD-699).

A grading resting on a performed observation is one the loop cannot take
again: when the paths it exercised move, the criterion is owed to somebody
outside the loop, so the board being moved back is only half the act. The
other half is the question, addressed under the criterion whose grading
lapsed so a later iteration and a later run rewrite the same one rather than
adding a second.

Composing the question is arithmetic over values the loop already holds, so
it is a plain function. Putting it on the tracker is a write, so it goes
through the verified, leased window every other authored tracker write goes
through — one round, no repair arm: there is nothing to author differently
the second time a question is asked.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from kodezart.chains.write_back_verifier import (
    FreshWriteBackJudge,
    WriteBackFinding,
    WriteBackVerifier,
)
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    LaneLapseEscalationTracker,
    OutboundContentGate,
    PromptSetProvider,
    WorkspaceProvider,
)
from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.services.lane_escalation import LaneEscalationWriter
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.criterion_lifecycle import CriterionCrossOff
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig, RunKind
from kodezart.types.domain.run_state import LaneBinding, LaneEscalation
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.surface import SurfaceKind, WritableSurface


def lapse_question(*, lane_key: str, cross_off: CriterionCrossOff) -> LaneEscalation:
    """The occurrence a lapsed observation owes, composed from what it holds.

    The address carries the criterion identity and nothing else that moves:
    no sha and no iteration, so a question asked again — by a later iteration
    or by a re-entered run — composes the same occurrence and rewrites the
    same body rather than adding a second question about one criterion. The
    raiser is the kind of run and never the holder, for the same reason.

    The interim basis is the prefixes that grading exercised, which is what a
    reader needs in order to see what moved; the sha it was taken at is
    already the occurrence's own provenance field.
    """
    return LaneEscalation(
        issue_id=lane_key,
        escalation_key=f"{cross_off.criterion}:lapse",
        raised_by=RunKind.FIRE.value,
        raised_at_sha=cross_off.evidence.graded_sha,
        question=(
            f"Re-derive or re-state the observation {cross_off.criterion} was "
            "graded on: the paths it exercised have moved since, and this loop "
            "cannot perform that observation again."
        ),
        interim_reading=(
            "The criterion is returned to unstarted, carrying the sha it was "
            "graded at; nothing counts it until the observation is taken again."
        ),
        interim_basis=", ".join(cross_off.exercised_paths),
    )


@dataclass(frozen=True)
class _LapseStep:
    """One lapse question's write, as the write-back verifier drives it."""

    surface: WritableSurface
    escalation: LaneEscalation
    lane_key: str
    holder: str
    visibility: RepoVisibility
    writer: LaneEscalationWriter

    async def write(self, *, finding: WriteBackFinding | None) -> None:
        """Put this question on its own marker comment, through the writer.

        There is no repair arm: the window is one round, so a finding here
        would be a round this step has no honest answer for — the question
        is the same question however it was read.

        Which makes the refusal below unreachable as this caller drives it:
        ``max_rounds=1`` means *finding* is never anything but ``None``, and
        no test can reach the raise. It is kept, and said out loud here
        rather than left for a reader to discover, because it records what
        this step would refuse if the budget ever allowed a second round —
        the protocol requires the parameter either way.
        """
        if finding is not None:
            raise NativeWriteRefusalError(
                f"{self.escalation.escalation_key!r}: a lapse question is "
                "written once and never rewritten"
            )
        await self.writer.raise_escalation(
            lane_key=self.lane_key,
            job_id=self.holder,
            escalation=self.escalation,
            visibility=self.visibility,
        )


class LaneLapseEscalations:
    """Raise the question a lane owes for each grading it cannot re-derive.

    Held by the engine and shared by every loop it compiles: the component
    carries no run state, so the lane, the tree and the head all arrive per
    call.
    """

    def __init__(
        self,
        *,
        tracker: LaneLapseEscalationTracker,
        operation: OperationConfig,
        runner: AgentRunner,
        workspace: WorkspaceProvider,
        git: GitService,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        gate: OutboundContentGate,
        lease_seconds: float,
    ) -> None:
        self._tracker = tracker
        self._operation = operation
        self._runner = runner
        self._workspace = workspace
        self._git = git
        self._prompts = prompts
        self._skills = skills
        self._writer = LaneEscalationWriter(
            tracker=tracker,
            gate=gate,
            operation=operation,
            surface_lease_seconds=lease_seconds,
        )

    async def raise_lapses(
        self,
        *,
        lane: LaneBinding,
        lapsed: Sequence[CriterionCrossOff],
        head_sha: str,
    ) -> None:
        """Raise one question per cross-off of *lapsed*, in the order given.

        The tree the judge reads is *head_sha* and not the sha the grading was
        taken at: what the question claims is about what moved SINCE that
        grading, so the commit the lapse was read at is the commit that
        claim can be read in.
        """
        if not lapsed:
            return
        verifier = WriteBackVerifier(
            tracker=self._tracker,
            judge=FreshWriteBackJudge(
                runner=self._runner,
                workspace=self._workspace,
                git=self._git,
                prompts=self._prompts,
                skills=self._skills,
                repo_path=lane.repo_path,
                repo_url=None if lane.repo_path is not None else lane.repo_url,
                session_type=SessionType.TICKET_FIRE,
            ),
            max_rounds=1,
        )
        for cross_off in lapsed:
            escalation = lapse_question(lane_key=lane.lane_key, cross_off=cross_off)
            marker = compose_comment_marker(
                prefixes=self._operation.marker_prefixes,
                purpose="escalation",
                lane=lane.lane_key,
                occurrence_key=escalation.escalation_key,
            )
            result = await verifier.write_back(
                step=_LapseStep(
                    surface=WritableSurface(
                        kind=SurfaceKind.MARKER_COMMENT,
                        ref=ScopeRef(kind=ScopeKind.ISSUE, key=lane.lane_key),
                        marker=marker,
                    ),
                    escalation=escalation,
                    lane_key=lane.lane_key,
                    holder=lane.run_id,
                    visibility=lane.visibility,
                    writer=self._writer,
                ),
                ref=head_sha,
            )
            if result.verdict is not AuditVerdict.HOLDS:
                raise NativeWriteRefusalError(
                    f"{escalation.escalation_key!r}: the lapse question on the "
                    "board is not upheld at the head it was raised at"
                )
