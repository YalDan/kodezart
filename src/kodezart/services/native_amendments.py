"""Independent native departure judgments before harness persistence."""

from collections.abc import Sequence

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
    PromptSetProvider,
    TrackerPort,
    WorkspaceProvider,
)
from kodezart.domain.amendment import (
    AmendmentRequiresWriteError,
    NativeWriteRefusalError,
    upheld_reason,
)
from kodezart.domain.errors import RulingRecordReadError, ScopeReadError
from kodezart.services.audit_sessions import judge_in_workspace
from kodezart.services.owned_workspace import owned_workspace
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.services.scope_membership import read_scope_members
from kodezart.types.domain.agent import AMENDMENT_JUDGMENT_SCHEMA, Ruling
from kodezart.types.domain.amendment import (
    AmendmentClaim,
    AmendmentJudgment,
    AmendmentReport,
    CriterionSubject,
    NativeWriterOutput,
    NativeWriterStart,
    UpheldAmendment,
)
from kodezart.types.domain.criteria import TrackerCriterionSet
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.operation import (
    CheckPrerequisite,
    OperationConfig,
    RepoEntry,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection


class NativeAmendments:
    """Bind one current native writer to the existing readers and session owner.

    A reproduced semantic ground is not permission to claim AMENDED. Until its
    canonical write/readback owner is supplied, that arm explicitly refuses.
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

    def for_writer(
        self,
        *,
        spec: TrackerSpec,
        criteria: TrackerCriterionSet,
        base_ref: str,
        repo_url: str | None,
    ) -> NativeWriteGuard:
        """Create a runtime guard; no service or tracker enters graph state."""
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
        )

    async def _read_rulings(self, spec: TrackerSpec) -> tuple[Ruling, ...]:
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
        rulings: list[Ruling] = []
        for key in sorted(members):
            try:
                records = await self._rulings.read_issue(issue_key=key)
            except RulingRecordReadError as exc:
                raise NativeWriteRefusalError(
                    "The current ruling registry is unreadable"
                ) from exc
            rulings.extend(ruling for _, ruling in records)
        identities = [ruling.ruling_id for ruling in rulings]
        if len(identities) != len(set(identities)):
            raise NativeWriteRefusalError(
                "The pinned ruling roster repeats an identity"
            )
        return tuple(sorted(rulings, key=lambda ruling: ruling.ruling_id))


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
    ) -> None:
        self._owner = owner
        self._spec = spec
        self._criteria = criteria
        self._base_ref = base_ref
        self._environment = environment
        self._rulings: tuple[Ruling, ...] | None = None
        self._base_sha: str | None = None

    async def begin(self, *, workspace_path: str) -> NativeWriterStart:
        owner = self._owner
        head = await settle(owner._git.current_sha(workspace_path))
        self._base_sha = await owner._source.resolve_commit(
            cwd=workspace_path,
            ref=self._base_ref,
        )
        self._rulings = await owner._read_rulings(self._spec)
        registry = "\n".join(ruling.model_dump_json() for ruling in self._rulings)
        instructions = owner._prompts.template_for(
            PromptKey.NATIVE_WRITER_CONTRACT,
        ).render({"pinned_rulings": registry or "Confirmed empty ruling registry."})
        start = NativeWriterStart(head_sha=head, instructions=instructions)
        await self.require_current(workspace_path=workspace_path, start=start)
        return start

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
        if await owner._read_rulings(self._spec) != self._rulings:
            raise NativeWriteRefusalError(
                "Pinned rulings changed during native writing"
            )
        current = await owner._criteria.read_current(spec=self._spec)
        if current != self._criteria:
            raise NativeWriteRefusalError(
                "Current Checks changed during native writing"
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
                "criteria": self._criteria.model_dump_json(),
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
        criterion_ids = {item.id for item in self._criteria.criteria}
        ruling_ids = {ruling.ruling_id for ruling in self._rulings}
        upheld = []
        for claim in output.claims:
            roster = (
                criterion_ids
                if isinstance(claim.subject, CriterionSubject)
                else ruling_ids
            )
            if claim.subject.id not in roster:
                raise NativeWriteRefusalError("A claim names no current native subject")
            judgment = await self._judge_claim(
                workspace_path=workspace_path, claim=claim
            )
            await self.require_current(workspace_path=workspace_path, start=start)
            reason = upheld_reason(claim, judgment, environment=self._environment)
            if reason is None:
                raise AmendmentRequiresWriteError(judgment)
            upheld.append(
                UpheldAmendment(
                    claim=claim,
                    reason=reason,
                    judgment=judgment,
                )
            )
        return AmendmentReport(upheld=tuple(upheld))
