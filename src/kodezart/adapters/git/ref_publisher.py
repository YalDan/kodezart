"""Ref publisher — makes an existing commit visible on the remote."""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import GitService, WorkspaceProvider


class GitRefPublisher:
    """Publishes a commit under a named ref.  Implements ``RefPublisher``.

    The workspace is acquired DETACHED at the commit and the ref is
    written by the push refspec alone.  Naming a local branch instead
    would leave it behind in the shared repository, and a later publish
    of the same ref would then check that stale branch out rather than
    the commit it was asked for.
    """

    def __init__(
        self,
        git: GitService,
        workspace: WorkspaceProvider,
    ) -> None:
        self._git: GitService = git
        self._workspace: WorkspaceProvider = workspace
        self._log: BoundLogger = get_logger(__name__)

    async def publish(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        commit_sha: str,
        ref: str,
        cache_key: str | None = None,
    ) -> None:
        """Point *ref* at *commit_sha* on the remote."""
        workspace_path = await self._workspace.acquire(
            repo_path=repo_path,
            repo_url=repo_url,
            ref=commit_sha,
            cache_key=cache_key,
        )
        try:
            await self._git.push(workspace_path, ref)
            await self._log.ainfo(
                "ref_published",
                ref=ref,
                commit_sha=commit_sha,
            )
        finally:
            await self._workspace.release(workspace_path)
