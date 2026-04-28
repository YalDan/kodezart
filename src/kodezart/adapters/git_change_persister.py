"""Git change persister — detects, commits, and pushes workspace changes."""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentExecutor, GitService
from kodezart.prompts import commit_message
from kodezart.types.domain.agent import (
    COMMIT_MESSAGE_SCHEMA,
    CommitMessageOutput,
    ResultEvent,
)
from kodezart.types.domain.persist import PersistResult


class GitChangePersister:
    """Detect changes, generate commit message, commit, and push.

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
        """Stage, commit, and push if changes exist.

        Returns ``PersistResult`` or ``None`` if no changes.
        """
        if not await self._git.has_changes(workspace_path):
            await self._log.ainfo("persist_no_changes", path=workspace_path)
            return None

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
        return PersistResult(commit_sha=sha, branch=branch, message=commit_msg.title)

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
