"""Independent native departure judgments before harness persistence."""

from collections.abc import Sequence
from typing import Final

from kodezart.chains.native_amendment import NativeAmendmentGraph
from kodezart.core.errors import (
    TrackerAccessDeniedError,
    TrackerProtocolError,
    TrackerUnavailableError,
)
from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import (
    AgentRunner,
    FireCriteriaReader,
    GitService,
    GitSourceReader,
    NativeWriteGuard,
    OutboundContentGate,
    PromptSetProvider,
    TrackerPort,
    WorkspaceProvider,
)
from kodezart.domain.amendment import (
    NativeWriteRefusalError,
    upheld_reason,
)
from kodezart.domain.comment_markers import configured_marker_prefix
from kodezart.domain.criterion_amendment import require_criterion_source
from kodezart.domain.errors import (
    CriterionReadError,
    DuplicateCommentMarkerError,
    RulingRecordReadError,
    ScopeReadError,
    WriteBackReadError,
)
from kodezart.domain.fire_spec import criterion_check
from kodezart.domain.rulings import pinned_registry, repeated_designations
from kodezart.services.amendment_writeback import (
    AmendmentSource,
    AmendmentWriteBack,
    CriterionAmendmentSource,
    RulingAmendmentSource,
)
from kodezart.services.audit_sessions import judge_in_workspace
from kodezart.services.owned_workspace import owned_workspace
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.services.scope_membership import read_scope_members
from kodezart.services.tracker_artifacts import read_tracker_artifact
from kodezart.types.domain.agent import AMENDMENT_JUDGMENT_SCHEMA, Ruling
from kodezart.types.domain.amendment import (
    AmendmentClaim,
    AmendmentJudgment,
    AmendmentReport,
    AmendmentVerdict,
    CriterionSubject,
    NativeWriterOutput,
    NativeWriterStart,
)
from kodezart.types.domain.audit import TrackerArtifact
from kodezart.types.domain.criteria import TrackerCriterionSet
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.native_execution import NativeAuthoritySnapshot
from kodezart.types.domain.operation import (
    CheckPrerequisite,
    OperationConfig,
    OperationMemberAbsentError,
    RepoEntry,
)
from kodezart.types.domain.persist import PersistResult
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.tracker import (
    TrackerComment,
    TrackerIssue,
    WorkflowStateKind,
    is_non_counting,
)

# The single branch-writing SDK stage is reused by implementation and both
# remediation entries. Assembly consumes this registration before exposing it.
NATIVE_WRITING_STAGES: Final = frozenset({PromptKey.IMPLEMENTATION})


class NativeAmendments:
    """Bind one current native writer to the existing readers and session owner.

    A reproduced semantic ground reaches AMENDED only through the actual
    canonical writer and its fresh independent verification graph.
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        operation: OperationConfig,
        criteria: FireCriteriaReader,
        git: GitService,
        source: GitSourceReader,
        workspace: WorkspaceProvider,
        runner: AgentRunner,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        repositories: Sequence[RepoEntry],
        gate: OutboundContentGate,
        max_verify_rounds: int,
        lease_seconds: float,
    ) -> None:
        self._tracker = tracker
        self._rulings = RulingRecordReader(tracker=tracker, operation=operation)
        self._criteria = criteria
        self._git = git
        self._source = source
        self._workspace = workspace
        self._runner = runner
        self._prompts = prompts
        self._skills = skills
        self._repositories = tuple(repositories)
        self._operation, self._gate = operation, gate
        self._max_verify_rounds, self._lease_seconds = max_verify_rounds, lease_seconds

    def for_writer(
        self,
        *,
        spec: TrackerSpec,
        criteria: TrackerCriterionSet,
        base_ref: str,
        repo_url: str | None,
        holder: str | None,
        visibility: RepoVisibility,
        stage: PromptKey,
    ) -> NativeWriteGuard:
        """Create a runtime guard; no service or tracker enters graph state."""
        if stage not in NATIVE_WRITING_STAGES:
            raise NativeWriteRefusalError(
                "The native writing stage has no amendment gate registration"
            )
        if holder is None or not holder.strip():
            raise NativeWriteRefusalError(
                "Native writing requires the actual parent job holder"
            )
        for purpose in ("amendment", "escalation"):
            configured_marker_prefix(self._operation.marker_prefixes, purpose=purpose)
        if "decision" not in self._operation.issue_labels:
            raise OperationMemberAbsentError(
                missing="issue_labels['decision']",
                stops="native amendment escalation is unavailable",
            )
        matches = [repo for repo in self._repositories if repo.url == repo_url]
        if len(matches) > 1:
            raise NativeWriteRefusalError("The repository declaration is ambiguous")
        environment = None if not matches else matches[0].runner_environment
        return _NativeWriterGuard(
            owner=self,
            spec=spec,
            criteria=criteria,
            base_ref=base_ref,
            environment=environment,
            holder=holder,
            visibility=visibility,
        )

    async def _read_authority(
        self, spec: TrackerSpec
    ) -> tuple[tuple[TrackerIssue, ...], tuple[tuple[TrackerComment, Ruling], ...]]:
        try:
            members = await read_scope_members(
                tracker=self._tracker,
                scope=ScopeRef(kind=ScopeKind.ISSUE, key=spec.subject),
            )
        except (
            TrackerUnavailableError,
            TrackerAccessDeniedError,
            TrackerProtocolError,
            ConnectionError,
            TimeoutError,
            ScopeReadError,
        ) as exc:
            raise NativeWriteRefusalError(
                "Current ruling membership could not be read"
            ) from exc
        criterion_issues = tuple(
            sorted(
                (i for i in members.values() if "criterion" in i.issue_labels),
                key=lambda issue: issue.issue_key,
            )
        )
        if not {str(ref) for ref in spec.criteria} <= {
            issue.issue_key for issue in criterion_issues
        }:
            raise NativeWriteRefusalError("A named native criterion left the subtree")
        # The membership stays whole for the fact comparison; only a
        # criterion that counts has its Check read, as at the spec read: one
        # the board Canceled or closed as a Duplicate refuses nothing (KOD-794).
        for issue in criterion_issues:
            if not is_non_counting(issue.state_kind):
                criterion_check(criterion=issue, issue_key=spec.subject)
        rulings: list[tuple[TrackerComment, Ruling]] = []
        for key in sorted(members):
            try:
                records = await self._rulings.read_issue(issue_key=key)
            except RulingRecordReadError as exc:
                raise NativeWriteRefusalError(
                    "The current ruling registry is unreadable"
                ) from exc
            rulings.extend(records)
        identities = [ruling.ruling_id for _, ruling in rulings]
        if len(identities) != len(set(identities)):
            raise NativeWriteRefusalError(
                "The pinned ruling roster repeats an identity"
            )
        if repeated_designations([record for _, record in rulings]):
            raise NativeWriteRefusalError(
                "The pinned roster designates one protected test twice"
            )
        return criterion_issues, tuple(
            sorted(rulings, key=lambda row: row[1].ruling_id)
        )


class _NativeWriterGuard:
    """One writer's snapshots; used only for current-read comparison, never fallback."""

    def __init__(
        self,
        *,
        owner: NativeAmendments,
        spec: TrackerSpec,
        criteria: TrackerCriterionSet,
        base_ref: str,
        environment: dict[CheckPrerequisite, bool] | None,
        holder: str,
        visibility: RepoVisibility,
    ) -> None:
        self._owner = owner
        self._spec = spec
        self._criteria = criteria
        self._base_ref = base_ref
        self._environment = environment
        self._rulings: tuple[Ruling, ...] | None = None
        self._criterion_issues: tuple[TrackerIssue, ...] | None = None
        self._ruling_records: tuple[tuple[TrackerComment, Ruling], ...] | None = None
        self._archives: tuple[TrackerArtifact, ...] = ()
        self._base_sha: str | None = None
        self._holder, self._visibility = holder, visibility

    @property
    def holder(self) -> str:
        """The actual native job binding, independent of saved checkpoint data."""
        if self._holder is None or not self._holder.strip():
            raise NativeWriteRefusalError(
                "Native writing requires its actual parent holder"
            )
        return self._holder

    async def begin(self, *, workspace_path: str) -> NativeWriterStart:
        owner = self._owner
        head = await settle(owner._git.current_sha(workspace_path))
        self._base_sha = await owner._source.resolve_commit(
            cwd=workspace_path,
            ref=self._base_ref,
        )
        self._criterion_issues, self._ruling_records = await owner._read_authority(
            self._spec
        )
        self._rulings = tuple(ruling for _, ruling in self._ruling_records)
        instructions = owner._prompts.template_for(
            PromptKey.NATIVE_WRITER_CONTRACT,
        ).render({"pinned_rulings": pinned_registry(self._rulings)})
        start = NativeWriterStart(head_sha=head, instructions=instructions)
        await self.require_current(workspace_path=workspace_path, start=start)
        return start

    def snapshot(self) -> NativeAuthoritySnapshot:
        """Retain actual source observations, never a newly inferred replay prior."""
        if (
            self._criterion_issues is None
            or self._ruling_records is None
            or self._base_sha is None
            or self._holder is None
        ):
            raise NativeWriteRefusalError("The native authority is not initialized")
        return NativeAuthoritySnapshot(
            spec=self._spec,
            criteria=self._criteria,
            criterion_issues=self._criterion_issues,
            ruling_records=self._ruling_records,
            archives=self._archives,
            base_ref=self._base_ref,
            base_sha=self._base_sha,
            holder=self._holder,
        )

    async def restore(
        self,
        *,
        snapshot: NativeAuthoritySnapshot,
        workspace_path: str,
        start: NativeWriterStart,
        receipt: PersistResult | None = None,
    ) -> None:
        """Restore original authority and refuse unrelated current source changes."""
        if (
            snapshot.spec != self._spec
            or snapshot.base_ref != self._base_ref
            or snapshot.holder != self._holder
        ):
            raise NativeWriteRefusalError(
                "The saved native authority belongs to another run"
            )
        self._criteria = snapshot.criteria
        self._criterion_issues = snapshot.criterion_issues
        self._ruling_records = snapshot.ruling_records
        self._rulings = tuple(ruling for _, ruling in snapshot.ruling_records)
        self._archives = snapshot.archives
        self._base_sha = snapshot.base_sha
        if receipt is None:
            await self.require_current(workspace_path=workspace_path, start=start)
        else:
            await self.require_publishable(
                workspace_path=workspace_path,
                start=start,
                authorized_commit_sha=receipt.commit_sha,
            )

    async def require_current(
        self,
        *,
        workspace_path: str,
        start: NativeWriterStart,
    ) -> None:
        await self._require_current_at_head(
            workspace_path=workspace_path, expected_head_sha=start.head_sha
        )

    async def require_publishable(
        self,
        *,
        workspace_path: str,
        start: NativeWriterStart,
        authorized_commit_sha: str,
    ) -> None:
        # The SHA is supplied only after the persister's actual commit returns.
        # It is not a writer-reported replacement for the original HEAD.
        if not await settle(
            self._owner._git.is_ancestor(
                workspace_path, start.head_sha, authorized_commit_sha
            )
        ):
            raise NativeWriteRefusalError(
                "The harness commit does not descend from the writer's starting HEAD"
            )
        await self._require_current_at_head(
            workspace_path=workspace_path, expected_head_sha=authorized_commit_sha
        )

    async def _require_current_at_head(
        self,
        *,
        workspace_path: str,
        expected_head_sha: str,
    ) -> None:
        owner = self._owner
        await self._require_head(workspace_path, expected_head_sha)
        if self._rulings is None or self._base_sha is None:
            raise NativeWriteRefusalError("The native writer was not initialized")
        current_issues, current_records = await owner._read_authority(self._spec)
        if current_records != self._ruling_records:
            raise NativeWriteRefusalError(
                "Pinned rulings changed during native writing"
            )
        if self._criterion_issues is None or len(current_issues) != len(
            self._criterion_issues
        ):
            raise NativeWriteRefusalError(
                "Native criterion facts changed during writing"
            )
        for expected, current_issue in zip(
            self._criterion_issues, current_issues, strict=True
        ):
            try:
                require_criterion_source(expected=expected, current=current_issue)
            except CriterionReadError as exc:
                raise NativeWriteRefusalError(
                    "Current Checks or native criterion facts changed during writing"
                ) from exc
        current = await owner._criteria.read_current(
            spec=self._spec, held=self._criteria
        )
        if current != self._criteria:
            raise NativeWriteRefusalError(
                "Current Checks changed during native writing"
            )
        for expected_archive in self._archives:
            try:
                current_archive = await read_tracker_artifact(
                    tracker=owner._tracker, surface=expected_archive.surface
                )
            except (
                TrackerUnavailableError,
                TrackerAccessDeniedError,
                TrackerProtocolError,
                ConnectionError,
                TimeoutError,
                ScopeReadError,
                WriteBackReadError,
                DuplicateCommentMarkerError,
            ) as exc:
                raise NativeWriteRefusalError(
                    "The verified amendment archive could not be re-read"
                ) from exc
            if current_archive != expected_archive:
                raise NativeWriteRefusalError(
                    "The verified amendment archive changed during native writing"
                )
        if await settle(owner._git.has_replace_refs(workspace_path)):
            raise NativeWriteRefusalError("Native writing cannot use replacement refs")
        if (
            await owner._source.resolve_commit(
                cwd=workspace_path,
                ref=self._base_ref,
            )
            != self._base_sha
        ):
            raise NativeWriteRefusalError("The resolved native base changed")
        await self._require_head(workspace_path, expected_head_sha)

    async def require_unchanged_head(
        self,
        *,
        workspace_path: str,
        start: NativeWriterStart,
    ) -> None:
        await self._require_head(workspace_path, start.head_sha)

    async def _require_head(self, workspace_path: str, expected_head_sha: str) -> None:
        if (
            await settle(self._owner._git.current_sha(workspace_path))
            != expected_head_sha
        ):
            raise NativeWriteRefusalError(
                "Writer HEAD changed from the expected boundary; retained workspace, "
                "no further harness commit, push, backup or replay"
            )

    async def _judge_claim(
        self,
        *,
        workspace_path: str,
        claim: AmendmentClaim,
    ) -> AmendmentJudgment:
        owner = self._owner
        if self._base_sha is None or self._rulings is None:
            raise NativeWriteRefusalError(
                "A judgment requires initialized base authority"
            )
        prompt = owner._prompts.template_for(PromptKey.AMENDMENT_JUDGE).render(
            {
                "claim": claim.model_dump_json(),
                "criteria": "\n".join(
                    issue.model_dump_json() for issue in self._criterion_issues or ()
                ),
                "pinned_rulings": "\n".join(r.model_dump_json() for r in self._rulings),
                "base_sha": self._base_sha,
            }
        )
        async with owned_workspace(
            owner._workspace,
            repo_path=workspace_path,
            ref=self._base_sha,
        ) as judge_workspace:

            async def require_base() -> None:
                if (
                    await settle(owner._git.has_replace_refs(judge_workspace))
                    or await settle(owner._git.current_sha(judge_workspace))
                    != self._base_sha
                    or await settle(owner._git.has_changes(judge_workspace))
                ):
                    raise NativeWriteRefusalError(
                        "The independent judgment requires a clean exact base"
                    )

            await require_base()
            output = await judge_in_workspace(
                runner=owner._runner,
                prompts=owner._prompts,
                skills=owner._skills,
                workspace=judge_workspace,
                key=PromptKey.AMENDMENT_JUDGE,
                prompt=prompt,
                output_schema=AMENDMENT_JUDGMENT_SCHEMA,
                site="amendment_judge",
                session_type=SessionType.TICKET_FIRE,
                failure_message="No independent amendment judgment was returned.",
            )
            judgment = AmendmentJudgment.model_validate(output)
            if (
                judgment.subject != claim.subject
                or judgment.base_sha != self._base_sha
                or judgment.ground is not claim.ground
            ):
                raise NativeWriteRefusalError(
                    "The judgment addresses another claim/base"
                )
            for citation in judgment.citations:
                blob = await owner._source.read_source(
                    cwd=judge_workspace,
                    commit_sha=self._base_sha,
                    path=citation.path,
                )
                if (
                    blob.commit_sha != self._base_sha
                    or blob.path != citation.path
                    or citation.quote.encode() not in blob.content
                ):
                    raise NativeWriteRefusalError("A cited base source is not present")
            await require_base()
        return judgment

    async def judge(
        self,
        *,
        workspace_path: str,
        start: NativeWriterStart,
        output: NativeWriterOutput,
    ) -> AmendmentReport:
        await self.require_current(workspace_path=workspace_path, start=start)
        if self._rulings is None:
            raise NativeWriteRefusalError("The ruling registry has not been read")
        criterion_ids = {item.issue_key for item in self._criterion_issues or ()}
        ruling_ids = {ruling.ruling_id for ruling in self._rulings}
        for claim in output.claims:
            roster = (
                criterion_ids
                if isinstance(claim.subject, CriterionSubject)
                else ruling_ids
            )
            if claim.subject.id not in roster:
                raise NativeWriteRefusalError("A claim names no current native subject")
        return await NativeAmendmentGraph(
            actions=_WriterActions(
                guard=self, workspace_path=workspace_path, start=start
            )
        ).run(output=output)


class _WriterActions:
    """Bind graph behavior to the actual guarded writer without storing services."""

    def __init__(
        self,
        *,
        guard: _NativeWriterGuard,
        workspace_path: str,
        start: NativeWriterStart,
    ) -> None:
        self._guard, self._workspace_path, self._start = guard, workspace_path, start
        owner = guard._owner
        self._write_back = AmendmentWriteBack(
            tracker=owner._tracker,
            runner=owner._runner,
            workspace=owner._workspace,
            git=owner._git,
            prompts=owner._prompts,
            skills=owner._skills,
            operation=owner._operation,
            max_verify_rounds=owner._max_verify_rounds,
            gate=owner._gate,
            lease_seconds=owner._lease_seconds,
            repo_path=workspace_path,
        )

    async def require_current(self) -> None:
        await self._guard.require_current(
            workspace_path=self._workspace_path, start=self._start
        )

    async def observe_archive(self, *, artifact: TrackerArtifact) -> None:
        self._guard._archives = (*self._guard._archives, artifact)
        await self.require_current()

    async def judge_claim(self, claim: AmendmentClaim) -> AmendmentJudgment:
        return await self._guard._judge_claim(
            workspace_path=self._workspace_path, claim=claim
        )

    async def apply_judgment(
        self, claim: AmendmentClaim, judgment: AmendmentJudgment
    ) -> AmendmentVerdict:
        guard = self._guard
        source: AmendmentSource
        if isinstance(claim.subject, CriterionSubject):
            criterion = next(
                (
                    i
                    for i in guard._criterion_issues or ()
                    if i.issue_key == claim.subject.id
                ),
                None,
            )
            if criterion is None:
                raise NativeWriteRefusalError(
                    "The claimed native criterion is no longer current"
                )
            source = CriterionAmendmentSource(issue=criterion)
        else:
            ruling = next(
                (
                    row
                    for row in guard._ruling_records or ()
                    if row[1].ruling_id == claim.subject.id
                ),
                None,
            )
            if ruling is None:
                raise NativeWriteRefusalError(
                    "The claimed native ruling is no longer current"
                )
            source = RulingAmendmentSource(comment=ruling[0], ruling=ruling[1])
        return await self._write_back.apply(
            claim=claim,
            judgment=judgment,
            reason=upheld_reason(claim, judgment, environment=guard._environment),
            lane_key=guard._spec.subject,
            holder=guard._holder,
            visibility=guard._visibility,
            authority=self,
            source=source,
        )

    async def observe_criterion(
        self, *, previous: TrackerIssue, body: str, reset: bool
    ) -> TrackerIssue:
        guard = self._guard
        current = await guard._owner._tracker.read_issue(issue_key=previous.issue_key)
        expected = TrackerIssue.model_validate({**previous.model_dump(), "body": body})
        require_criterion_source(
            expected=expected, current=current, pending_replay=reset
        )
        if reset and current.state_kind is not WorkflowStateKind.UNSTARTED:
            raise NativeWriteRefusalError("The amended criterion did not reset")
        guard._criterion_issues = tuple(
            current if i.issue_key == previous.issue_key else i
            for i in guard._criterion_issues or ()
        )
        guard._criteria = await guard._owner._criteria.read_current(
            spec=guard._spec, held=guard._criteria
        )
        await self.require_current()
        return current

    async def observe_ruling(
        self, *, previous: TrackerComment, ruling: Ruling, body: str
    ) -> TrackerComment:
        guard = self._guard
        records = await guard._owner._rulings.read_issue(issue_key=previous.issue_key)
        current = next((comment for comment, value in records if value == ruling), None)
        if (
            current is None
            or current.model_dump(exclude={"body"})
            != previous.model_dump(exclude={"body"})
            or current.body != body
        ):
            raise NativeWriteRefusalError(
                "The amended ruling changed native identity or text"
            )
        guard._ruling_records = tuple(
            (current, ruling) if c.comment_key == previous.comment_key else (c, r)
            for c, r in guard._ruling_records or ()
        )
        guard._rulings = tuple(r for _, r in guard._ruling_records)
        await self.require_current()
        return current

    async def observe_decision(self, *, previous: TrackerIssue) -> None:
        guard = self._guard
        current = await guard._owner._tracker.read_issue(issue_key=previous.issue_key)
        expected = TrackerIssue.model_validate(
            {
                **previous.model_dump(),
                "issue_labels": previous.issue_labels | {"decision"},
            }
        )
        if expected.model_dump(exclude={"updated_at"}) != current.model_dump(
            exclude={"updated_at"}
        ):
            raise NativeWriteRefusalError(
                "The escalation changed more than its authorized "
                "decision classification"
            )
        guard._criterion_issues = tuple(
            current if i.issue_key == previous.issue_key else i
            for i in guard._criterion_issues or ()
        )
        await self.require_current()
