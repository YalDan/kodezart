"""Git change persister — ensures the canonical ref equals workspace HEAD.

Two paths:
- Dirty working tree: generate commit message, stage, commit, push.
- Clean working tree, but workspace HEAD ahead of (or equal to) the
  remote tip: push HEAD's existing commit (no new commit).

Returns ``None`` only when HEAD already equals the remote tip.  Raises
``RuntimeError`` if HEAD has diverged from the remote tip — silent
divergence is forbidden.
"""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentExecutor, GitService
from kodezart.prompts import commit_message
from kodezart.types.domain.agent import (
    COMMIT_MESSAGE_SCHEMA,
    CommitMessageOutput,
    ResultEvent,
)
from kodezart.types.domain.persist import PersistResult

_REMOTE = "origin"


class GitChangePersister:
    """Ensure the canonical ref equals workspace HEAD.

    Implements the ``ChangePersister`` protocol.
    """

    def __init__(
        self,
        git: GitService,
        committer_name: str,
        committer_email: str,
    ) -> None:
        self._git = git
        self._committer_name = committer_name
        self._committer_email = committer_email
        self._log: BoundLogger = get_logger(__name__)

    async def persist(
        self,
        *,
        workspace_path: str,
        branch: str,
        executor: AgentExecutor,
    ) -> PersistResult | None:
        """Ensure ``origin/<branch>`` equals workspace HEAD.

        Decision tree:
        - dirty working tree → stage+commit+push, return ``PersistResult``;
        - clean tree and HEAD == remote tip → ``None`` (no-op);
        - clean tree and HEAD descends from remote tip (or remote is
          absent) → push, return ``PersistResult``;
        - clean tree and HEAD diverged → raise ``RuntimeError``
          describing the divergence.
        """
        if await self._git.has_changes(workspace_path):
            return await self._persist_dirty(
                workspace_path=workspace_path,
                branch=branch,
                executor=executor,
            )

        head_sha = await self._git.current_sha(workspace_path)
        remote_tip = await self._git.remote_branch_sha(
            workspace_path,
            _REMOTE,
            branch,
        )
        if remote_tip is not None and remote_tip == head_sha:
            await self._log.ainfo("persist_no_changes", path=workspace_path)
            return None

        head_descends_from_remote = remote_tip is None or await self._git.is_ancestor(
            workspace_path,
            remote_tip,
            head_sha,
        )
        if not head_descends_from_remote:
            msg = (
                f"Workspace HEAD ({head_sha}) has diverged from "
                f"origin/{branch} ({remote_tip})"
            )
            raise RuntimeError(msg)

        await self._git.push(workspace_path, branch)
        await self._log.ainfo(
            "agent_direct_commit_pushed",
            commit_sha=head_sha,
            branch=branch,
        )
        return PersistResult(commit_sha=head_sha, branch=branch)

    async def _persist_dirty(
        self,
        *,
        workspace_path: str,
        branch: str,
        executor: AgentExecutor,
    ) -> PersistResult:
        commit_msg = await self._generate_commit_message(executor, workspace_path)
        await self._git.add_all(workspace_path)
        full_message = commit_msg.title
        if commit_msg.body:
            full_message = f"{commit_msg.title}\n\n{commit_msg.body}"
        sha = await self._git.commit(
            cwd=workspace_path,
            message=full_message,
            author_name=self._committer_name,
            author_email=self._committer_email,
        )
        await self._git.push(workspace_path, branch)
        await self._log.ainfo("changes_persisted", commit_sha=sha, branch=branch)
        return PersistResult(commit_sha=sha, branch=branch)

    async def _generate_commit_message(
        self,
        executor: AgentExecutor,
        cwd: str,
    ) -> CommitMessageOutput:
        output_format: dict[str, object] = {
            "type": "json_schema",
            "schema": COMMIT_MESSAGE_SCHEMA,
        }
        result_event: ResultEvent | None = None

        async for event in executor.stream(
            prompt=commit_message.PROMPT,
            cwd=cwd,
            permission_mode="plan",
            allowed_tools=["Read", "Glob", "Grep", "Bash"],
            output_format=output_format,
        ):
            if isinstance(event, ResultEvent):
                result_event = event

        if result_event is None or result_event.structured_output is None:
            msg = "Agent did not produce structured output for commit message"
            raise RuntimeError(msg)

        return CommitMessageOutput.model_validate(result_event.structured_output)
