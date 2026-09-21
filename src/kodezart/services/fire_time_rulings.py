"""Answer a fire's open questions onto the tracker before its loop runs.

Three things happen here and nowhere else on this path. Which questions the
subject text leaves open is judgement, so it is asked of one read-only
session. Which of the answers the tracker still owes a record is arithmetic,
so it is a plain function over minted identities and the records already
there. Putting each owed record on the issue whose text raised the question
is a write, so it goes through the verified, leased window every other
authored tracker write goes through.

The step never repairs: one judged write per record is the whole window, and
a record whose landed text is not upheld ends the fire rather than being
rewritten until it passes.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from kodezart.chains.write_back_verifier import (
    FreshWriteBackJudge,
    WriteBackFinding,
    WriteBackVerifier,
)
from kodezart.core.errors import (
    TrackerAccessDeniedError,
    TrackerProtocolError,
    TrackerUnavailableError,
)
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.outbound_write import gated_exact
from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    OutboundContentGate,
    PromptSetProvider,
    TrackerPort,
    WorkspaceProvider,
)
from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.domain.comment_markers import configured_marker_prefix
from kodezart.domain.errors import (
    DuplicateCommentMarkerError,
    RulingRecordReadError,
    RulingUnrecordedError,
    StaleCommentWriteError,
    SurfaceLeaseError,
    SurfaceLeaseLostError,
    TransientAPIError,
    WriteBackReadError,
)
from kodezart.domain.prompt_variables import tracker_checks_section
from kodezart.domain.rulings import (
    owed_rulings,
    pinned_registry,
    render_ruling,
    ruling_marker,
)
from kodezart.domain.ticket import format_fire_spec
from kodezart.services.audit_sessions import judge_in_workspace
from kodezart.services.owned_workspace import owned_workspace
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.agent import (
    RULING_SCHEMA,
    Ruling,
    RulingAnswer,
    RulingOutput,
)
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.criteria import TrackerCriterionSet
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    RepoVisibility,
)
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.surface import SurfaceKind, WritableSurface

#: Every failure that means "no confirmed record is on the tracker". A
#: session that produced nothing is deliberately not here: that is the
#: graph's retry, not a statement about the tracker.
_UNRECORDED: tuple[type[Exception], ...] = (
    TrackerUnavailableError,
    TrackerAccessDeniedError,
    TrackerProtocolError,
    TransientAPIError,
    SurfaceLeaseError,
    SurfaceLeaseLostError,
    StaleCommentWriteError,
    DuplicateCommentMarkerError,
    WriteBackReadError,
)


@dataclass(frozen=True)
class _Answers:
    """What the read-only pass said, and the commit it stood at."""

    rulings: tuple[RulingAnswer, ...]
    base_sha: str


@dataclass(frozen=True)
class _PinStep:
    """One pinned answer's write, as the write-back verifier drives it."""

    surface: WritableSurface
    ruling: Ruling
    lane_key: str
    tracker: TrackerPort
    gate: OutboundContentGate
    log: BoundLogger
    lease: RunSurfaceLease
    holder: str
    visibility: RepoVisibility
    marker_prefixes: dict[str, str]

    async def write(self, *, finding: WriteBackFinding | None) -> None:
        """Put this record's exact text on its own marker comment.

        There is no repair arm: the window is one round, so a finding here
        would be a round this step has no honest answer for.
        """
        if finding is not None:
            raise RulingUnrecordedError(
                issue_key=self.lane_key,
                reason=(
                    f"{self.ruling.issue_ref!r}: a pinned answer is written once "
                    "and never rewritten"
                ),
            )
        text = render_ruling(
            ruling=self.ruling,
            lane_key=self.lane_key,
            marker_prefixes=self.marker_prefixes,
        )
        marker, content = text.split("\n", 1)
        await gated_exact(
            gate=self.gate,
            log=self.log,
            content=content,
            visibility=self.visibility,
            destination=OutboundDestination.TRACKER_COMMENT,
            content_class=ContentClass.AUTHORED,
            aggregates=(),
            refusal=lambda: RulingUnrecordedError(
                issue_key=self.lane_key,
                reason=(
                    f"{self.ruling.issue_ref!r}: the outbound gate changed the "
                    "exact pinned text"
                ),
            ),
        )
        await self.lease.renew()
        await settle(
            self.tracker.upsert_comment(
                target=self.ruling.issue_ref,
                marker=marker,
                body=content,
                holder=self.holder,
                expected=None,
            )
        )


class FireTimeRulings:
    """Find the open questions in a fire's tracker text and pin each answer.

    Held by the fire engine and shared by every fire it compiles: the
    component carries no run state, so the subject, the tree and the holder
    all arrive per call.
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
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
        self._records = RulingRecordReader(tracker=tracker, operation=operation)
        self._runner = runner
        self._workspace = workspace
        self._git = git
        self._prompts = prompts
        self._skills = skills
        self._gate = gate
        self._lease_seconds = lease_seconds
        self._log = get_logger(__name__)

    async def rule(
        self,
        *,
        spec: TrackerSpec,
        criteria: TrackerCriterionSet,
        repo_path: str | None,
        repo_url: str | None,
        ref: str,
        cache_key: str,
        holder: str | None,
        visibility: RepoVisibility,
    ) -> None:
        """Answer every open question this fire still owes the tracker.

        Returns having written nothing when the text leaves nothing open, or
        when every answer is already a record — which is what makes a second
        pass over one fire, a remediation round, and a lane resumed in a new
        process all write nothing.
        """
        if holder is None or not holder.strip():
            raise NativeWriteRefusalError(
                "Native writing requires the actual parent job holder"
            )
        prefixes = dict(self._operation.marker_prefixes)
        configured_marker_prefix(prefixes, purpose="ruling")
        addressable = frozenset(
            {spec.subject, *(str(ref_item) for ref_item in spec.criteria)}
        )
        recorded = await self._recorded(addressable)
        answers = await self._answers(
            spec=spec,
            criteria=criteria,
            repo_path=repo_path,
            repo_url=repo_url,
            ref=ref,
            cache_key=cache_key,
            recorded=recorded,
        )
        owed = owed_rulings(
            subject=spec.subject,
            answers=answers.rulings,
            addressable=addressable,
            recorded=tuple(record.ruling_id for record in recorded),
        )
        if not owed:
            return
        try:
            await self._pin(
                spec=spec,
                owed=owed,
                base_sha=answers.base_sha,
                repo_path=repo_path,
                repo_url=repo_url,
                holder=holder,
                visibility=visibility,
                prefixes=prefixes,
            )
        except RulingUnrecordedError:
            raise
        except _UNRECORDED as exc:
            raise RulingUnrecordedError(
                issue_key=spec.subject, reason=str(exc)
            ) from exc

    async def _recorded(self, addressable: frozenset[str]) -> tuple[Ruling, ...]:
        """Every record already on the issues this fire can address.

        A read failure propagates: nothing has been written yet, so an
        unreadable registry is a fact about the tracker and not an answer
        this pass failed to record.
        """
        records: list[Ruling] = []
        for key in sorted(addressable):
            records.extend(
                record for _, record in await self._records.read_issue(issue_key=key)
            )
        return tuple(sorted(records, key=lambda record: record.ruling_id))

    async def _answers(
        self,
        *,
        spec: TrackerSpec,
        criteria: TrackerCriterionSet,
        repo_path: str | None,
        repo_url: str | None,
        ref: str,
        cache_key: str,
        recorded: Sequence[Ruling],
    ) -> _Answers:
        """Ask one read-only tree what the subject text leaves open."""
        prompt = self._prompts.template_for(PromptKey.FIRE_TIME_RULING).render(
            {
                "issue_key": spec.subject,
                "task_md": format_fire_spec(spec)
                + "\n\n"
                + tracker_checks_section(criteria),
                "pinned_rulings": pinned_registry(recorded),
            }
        )
        async with owned_workspace(
            self._workspace,
            repo_path=repo_path,
            repo_url=repo_url,
            ref=ref,
            cache_key=cache_key,
        ) as tree:
            structured = await judge_in_workspace(
                runner=self._runner,
                prompts=self._prompts,
                skills=self._skills,
                workspace=tree,
                key=PromptKey.FIRE_TIME_RULING,
                prompt=prompt,
                output_schema=RULING_SCHEMA,
                site="fire_time_ruling",
                session_type=SessionType.TICKET_FIRE,
                failure_message="Open-question pass produced no structured answer.",
            )
            base_sha = await settle(self._git.current_sha(tree))
        return _Answers(
            rulings=RulingOutput.model_validate(structured).rulings, base_sha=base_sha
        )

    async def _pin(
        self,
        *,
        spec: TrackerSpec,
        owed: tuple[Ruling, ...],
        base_sha: str,
        repo_path: str | None,
        repo_url: str | None,
        holder: str,
        visibility: RepoVisibility,
        prefixes: dict[str, str],
    ) -> None:
        verifier = WriteBackVerifier(
            tracker=self._tracker,
            judge=FreshWriteBackJudge(
                runner=self._runner,
                workspace=self._workspace,
                git=self._git,
                prompts=self._prompts,
                skills=self._skills,
                repo_path=repo_path,
                repo_url=None if repo_path is not None else repo_url,
                session_type=SessionType.TICKET_FIRE,
            ),
            max_rounds=1,
        )
        surfaces = {
            record.ruling_id: WritableSurface(
                kind=SurfaceKind.MARKER_COMMENT,
                ref=ScopeRef(kind=ScopeKind.ISSUE, key=record.issue_ref),
                marker=ruling_marker(
                    ruling_id=record.ruling_id,
                    lane_key=spec.subject,
                    marker_prefixes=prefixes,
                ),
            )
            for record in owed
        }
        async with RunSurfaceLease(
            tracker=self._tracker,
            job_id=holder,
            surfaces=frozenset(surfaces.values()),
            lease_seconds=self._lease_seconds,
        ) as lease:
            for record in owed:
                result = await verifier.write_back(
                    step=_PinStep(
                        surface=surfaces[record.ruling_id],
                        ruling=record,
                        lane_key=spec.subject,
                        tracker=self._tracker,
                        gate=self._gate,
                        log=self._log,
                        lease=lease,
                        holder=holder,
                        visibility=visibility,
                        marker_prefixes=prefixes,
                    ),
                    ref=base_sha,
                )
                if result.verdict is not AuditVerdict.HOLDS:
                    raise RulingUnrecordedError(
                        issue_key=spec.subject,
                        reason=(
                            f"{record.issue_ref!r}: the landed pinned text was not "
                            "independently upheld"
                        ),
                    )
        await self._require_read_back(spec=spec, owed=owed)

    async def _require_read_back(
        self, *, spec: TrackerSpec, owed: tuple[Ruling, ...]
    ) -> None:
        """A cold read of each touched issue must carry exactly what was written."""
        touched = sorted({record.issue_ref for record in owed})
        landed: dict[str, Ruling] = {}
        for key in touched:
            try:
                records = await self._records.read_issue(issue_key=key)
            except RulingRecordReadError as exc:
                raise RulingUnrecordedError(
                    issue_key=spec.subject,
                    reason=f"the records on {key!r} could not be read back",
                ) from exc
            landed.update({record.ruling_id: record for _, record in records})
        for record in owed:
            if landed.get(record.ruling_id) != record:
                await self._log.ainfo(
                    "fire_open_question_unconfirmed",
                    issue_key=record.issue_ref,
                    subject=spec.subject,
                )
                raise RulingUnrecordedError(
                    issue_key=spec.subject,
                    reason=f"{record.issue_ref!r} does not carry the answer written",
                )
