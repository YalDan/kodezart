"""Git change persister — ensures the canonical ref equals workspace HEAD.

Three persistence paths:
- Dirty working tree: generate commit message, stage, commit, push.
- Clean working tree, HEAD ahead of (or equal to) the remote tip:
  push HEAD's existing commit (no new commit).
- Clean working tree, HEAD diverged from the remote tip: push the
  divergent line to a backup ref on origin, reset the worktree to
  ``<remote>/<branch>``, and either skip the replay (when divergent
  tree == remote-tip tree) or synthesize one replay commit via
  ``git commit-tree`` whose parent IS the remote tip and push
  fast-forward.

Returns ``None`` only when HEAD already equals the remote tip.
Raises ``RuntimeError`` only when the pre-mutation backup push in the
divergence-recovery path fails — in that case no state has been
mutated (no reset, no commit-tree, no follow-up push).
"""

from kodezart.core.errors import NoStructuredOutputError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentExecutor, GitService
from kodezart.core.stream_drain import drain
from kodezart.prompts import commit_message
from kodezart.types.domain.agent import (
    COMMIT_MESSAGE_SCHEMA,
    CommitMessageOutput,
)
from kodezart.types.domain.branch import BackupBranchName
from kodezart.types.domain.persist import PersistResult, PersistSource


class GitChangePersister:
    """Ensure the canonical ref equals workspace HEAD.

    Implements the ``ChangePersister`` protocol.
    """

    def __init__(
        self,
        git: GitService,
        committer_name: str,
        committer_email: str,
        *,
        remote: str,
    ) -> None:
        self._git = git
        self._committer_name = committer_name
        self._committer_email = committer_email
        self._remote = remote
        self._log: BoundLogger = get_logger(__name__)

    async def persist(
        self,
        *,
        workspace_path: str,
        branch: str,
        executor: AgentExecutor,
        backup_ref_id_prefix: str,
    ) -> PersistResult | None:
        """Ensure ``<remote>/<branch>`` equals workspace HEAD.

        Decision tree:
        - dirty working tree → stage+commit+push, return ``PersistResult``;
        - clean tree and HEAD == remote tip → ``None`` (no-op);
        - clean tree and HEAD descends from remote tip (or remote is
          absent) → push, return ``PersistResult``;
        - clean tree and HEAD diverged → recover (backup-push the divergent
          line, reset to the remote tip, skip-or-replay) and return a
          ``PersistResult`` with ``source = DIVERGENCE_REPLAY``.  Raises
          ``RuntimeError`` only if the pre-mutation backup push fails;
          no state is mutated in that case.
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
            self._remote,
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
            if remote_tip is None:
                msg = (
                    f"Internal invariant violated: divergence branch entered "
                    f"with no remote tip for {self._remote}/{branch}"
                )
                raise RuntimeError(msg)
            return await self._recover_from_divergence(
                workspace_path=workspace_path,
                branch=branch,
                head_sha=head_sha,
                remote_tip=remote_tip,
                backup_ref_id_prefix=backup_ref_id_prefix,
            )

        head_message = await self._git.head_commit_message(workspace_path)
        await self._git.push(workspace_path, branch)
        await self._log.ainfo(
            "agent_direct_commit_pushed",
            commit_sha=head_sha,
            branch=branch,
        )
        return PersistResult(
            commit_sha=head_sha,
            branch=branch,
            message=head_message,
            source=PersistSource.AGENT_DIRECT_COMMIT,
        )

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
        return PersistResult(
            commit_sha=sha,
            branch=branch,
            message=full_message,
            source=PersistSource.WORKING_TREE_COMMIT,
        )

    async def _recover_from_divergence(
        self,
        *,
        workspace_path: str,
        branch: str,
        head_sha: str,
        remote_tip: str,
        backup_ref_id_prefix: str,
    ) -> PersistResult:
        backup_name = str(
            BackupBranchName(
                source_branch=branch,
                workspace_id_prefix=backup_ref_id_prefix,
            )
        )
        # Step 1: backup BEFORE any state mutation. Preserves the divergent
        # commit (and its tree) on the remote, independent of local GC.
        try:
            await self._git.push(workspace_path, backup_name)
        except Exception as exc:
            await self._log.aerror(
                "divergence_backup_push_failed",
                backup=backup_name,
                head_sha=head_sha,
                remote_tip=remote_tip,
                branch=branch,
                error=str(exc),
            )
            msg = (
                f"Workspace HEAD ({head_sha}) has diverged from "
                f"{self._remote}/{branch} ({remote_tip}); backup push to "
                f"{backup_name} failed — no state mutated"
            )
            raise RuntimeError(msg) from exc

        # Capture divergent-HEAD message + tree BEFORE reset (defensive: keeps
        # recovery correct even if a worktree pruned unreachable objects).
        head_message_divergent = await self._git.head_commit_message(workspace_path)
        head_tree = await self._git.tree_of(workspace_path, head_sha)

        # Step 2: single reset moves the shared refs/heads/<branch>.
        await self._git.reset_hard(workspace_path, remote_tip)

        # Step 3: tree-equality guard.
        remote_tip_tree = await self._git.tree_of(workspace_path, remote_tip)
        if head_tree == remote_tip_tree:
            # HEAD is now at remote_tip; head_commit_message reads remote_tip's
            # message — consistent with PersistResult.commit_sha = remote_tip.
            remote_tip_message = await self._git.head_commit_message(workspace_path)
            await self._log.ainfo(
                "divergence_recovered_tree_equal",
                backup=backup_name,
                commit_sha=remote_tip,
                branch=branch,
            )
            return PersistResult(
                commit_sha=remote_tip,
                branch=branch,
                message=remote_tip_message,
                source=PersistSource.DIVERGENCE_REPLAY,
            )

        # Step 4: replay as a single commit whose parent IS remote_tip.
        replay_sha = await self._git.commit_tree(
            cwd=workspace_path,
            tree=head_tree,
            parent=remote_tip,
            message=head_message_divergent,
            author_name=self._committer_name,
            author_email=self._committer_email,
        )
        await self._git.reset_hard(workspace_path, replay_sha)
        await self._git.push(workspace_path, branch)  # fast-forward by construction
        await self._log.ainfo(
            "divergence_recovered_replay",
            backup=backup_name,
            commit_sha=replay_sha,
            branch=branch,
        )
        return PersistResult(
            commit_sha=replay_sha,
            branch=branch,
            message=head_message_divergent,
            source=PersistSource.DIVERGENCE_REPLAY,
        )

    async def _generate_commit_message(
        self,
        executor: AgentExecutor,
        cwd: str,
    ) -> CommitMessageOutput:
        output_format: dict[str, object] = {
            "type": "json_schema",
            "schema": COMMIT_MESSAGE_SCHEMA,
        }

        result_event, rate_limit_rejected = await drain(
            executor.stream(
                prompt=commit_message.PROMPT,
                cwd=cwd,
                permission_mode="plan",
                allowed_tools=["Read", "Glob", "Grep", "Bash"],
                output_format=output_format,
            )
        )

        if result_event is None or result_event.structured_output is None:
            msg = "Agent did not produce structured output for commit message"
            raise NoStructuredOutputError(
                msg,
                raise_site="commit_message",
                result_event=result_event,
                rate_limit_rejected=rate_limit_rejected,
            )

        return CommitMessageOutput.model_validate(result_event.structured_output)
