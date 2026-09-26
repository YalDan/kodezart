"""Prove what a write-back actually put on a surface, and repair it if wrong.

A write that returned without raising has established one thing: the call
went through.  It has not established that the text now on the surface says
anything true — a write-back naming a test that does not exist at the ref it
claims to have run at is exactly as successful, at the call, as a correct
one.  The next consumer reads that text and treats it as evidence.

So the loop is write, RE-READ what landed, judge it, and — where the
judgment refutes it — repair and judge again.  The re-read is not
ceremony: the artifact a consumer will read is the one the backend now
holds, which is not necessarily the string the writing step composed.
"""

import re
from collections.abc import Sequence

from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    PromptSetProvider,
    TrackerArtifactReader,
    WorkspaceProvider,
    WriteBackJudge,
    WriteBackStep,
)
from kodezart.domain.errors import WriteBackReadError
from kodezart.services.audit_sessions import judge_in_workspace
from kodezart.services.git_observations import read_workspace_head
from kodezart.services.owned_workspace import owned_workspace
from kodezart.services.tracker_artifacts import (
    read_tracker_artifact,
    require_artifact_read,
)
from kodezart.types.domain.agent import WRITE_BACK_SCHEMA
from kodezart.types.domain.audit import AuditVerdict, TrackerArtifact
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.write_back import (
    WriteBackFinding as WriteBackFinding,
)
from kodezart.types.domain.write_back import (
    WriteBackResult as WriteBackResult,
)


class WriteBackVerifier:
    """Write, re-read, judge, repair, judge again — within a round budget.

    ``max_rounds`` is the operator's, supplied by the caller: how many
    times a deployment is willing to have a writing step try again is a
    deployment's decision and never this loop's.
    """

    def __init__(
        self,
        *,
        tracker: TrackerArtifactReader,
        judge: WriteBackJudge,
        max_rounds: int,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("a write-back needs at least one round to be judged")
        self._tracker = tracker
        self._judge = judge
        self._max_rounds = max_rounds

    async def write_back(self, *, step: WriteBackStep, ref: str) -> WriteBackResult:
        """Drive *step* until its artifact holds at *ref* or the budget ends.

        Exhaustion is ``unverifiable`` and not ``refuted``: the loop ran
        out of rounds, which says the claim was never settled, not that it
        was disproved — and the last round's refutation stays in
        ``rounds`` for whoever reads why.
        """
        surface = step.surface
        require_artifact_read(surface)
        rounds: list[WriteBackFinding] = []
        finding: WriteBackFinding | None = None
        while True:
            await step.write(finding=finding)
            artifact = await read_tracker_artifact(
                tracker=self._tracker, surface=surface
            )
            finding = await self._judge.judge(artifact=artifact, ref=ref)
            rounds.append(finding)
            if finding.verdict is AuditVerdict.HOLDS:
                return self._result(
                    verdict=AuditVerdict.HOLDS, artifact=artifact, rounds=rounds
                )
            if len(rounds) >= self._max_rounds:
                return self._result(
                    verdict=AuditVerdict.UNVERIFIABLE,
                    artifact=artifact,
                    rounds=rounds,
                )

    @staticmethod
    def _result(
        *,
        verdict: AuditVerdict,
        artifact: TrackerArtifact,
        rounds: Sequence[WriteBackFinding],
    ) -> WriteBackResult:
        return WriteBackResult(verdict=verdict, artifact=artifact, rounds=tuple(rounds))


class FreshWriteBackJudge:
    """Judge landed claims at an exact clean commit in a fresh read-only session."""

    def __init__(
        self,
        *,
        runner: AgentRunner,
        workspace: WorkspaceProvider,
        git: GitService,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        repo_url: str | None = None,
        repo_path: str | None = None,
        session_type: SessionType,
    ) -> None:
        if (repo_url is None) == (repo_path is None) or any(
            value is not None and not value.strip() for value in (repo_url, repo_path)
        ):
            raise ValueError(
                "write-back verification requires exactly one nonblank "
                "repository source"
            )
        self._runner, self._workspace, self._git = runner, workspace, git
        self._prompts, self._skills = prompts, skills
        self._repo_url, self._repo_path, self._session_type = (
            repo_url,
            repo_path,
            session_type,
        )

    async def _require_head(self, workspace: str, ref: str) -> None:
        if await settle(
            self._git.has_replace_refs(workspace)
        ) or await read_workspace_head(git=self._git, workspace=workspace) != (
            ref,
            False,
        ):
            raise WriteBackReadError(
                "write-back verification requires a clean repository "
                "at the exact commit"
            )

    async def judge(self, *, artifact: TrackerArtifact, ref: str) -> WriteBackFinding:
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", ref) is None:
            raise WriteBackReadError(
                "write-back verification requires a complete commit SHA"
            )
        key = PromptKey.WRITE_BACK_VERIFY
        prompt = self._prompts.template_for(key).render(
            {
                "written_artifact": artifact.model_dump_json(by_alias=True),
                "base_ref": ref,
            }
        )
        async with owned_workspace(
            self._workspace, repo_url=self._repo_url, repo_path=self._repo_path, ref=ref
        ) as workspace:
            await self._require_head(workspace, ref)
            structured = await judge_in_workspace(
                runner=self._runner,
                prompts=self._prompts,
                skills=self._skills,
                workspace=workspace,
                key=key,
                prompt=prompt,
                output_schema=WRITE_BACK_SCHEMA,
                site="write_back_verify",
                session_type=self._session_type,
                failure_message="Write-back verifier produced no structured judgment.",
            )
            await self._require_head(workspace, ref)
        return WriteBackFinding.model_validate(structured)
