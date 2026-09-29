"""Infrastructure adapter implementing the RepoCache port.

Clones and fetches remote repos into a local bare cache.
"""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import GitService
from kodezart.domain.git_url import cache_dir_for_repo, parse_repo_url


class LocalBareRepoCache:
    """Ensures a remote repo is locally available as a bare clone."""

    def __init__(
        self,
        git: GitService,
        base_dir: str,
    ) -> None:
        self._git = git
        self._base_dir = base_dir
        self._log: BoundLogger = get_logger(__name__)

    async def ensure_available(
        self,
        url: str,
        cache_key: str | None = None,
    ) -> str:
        """Returns local path to bare repo."""
        clone_url = parse_repo_url(url)
        repo_dir = cache_dir_for_repo(self._base_dir, clone_url)
        if cache_key is not None:
            repo_dir = f"{repo_dir}--{cache_key}"
        if self._git.is_repo(repo_dir):
            await self._git.fetch(repo_dir)
            await self._fast_forward_heads(repo_dir)
        else:
            await self._git.clone_bare(clone_url, repo_dir)
        return repo_dir

    async def _fast_forward_heads(self, repo_dir: str) -> None:
        """Move each local head forward to the remote's tip the fetch just read.

        A bare clone copies the remote's heads once, when it is made, and a
        fetch refreshes only the remote-tracking refs, so a branch read here by
        name (the trunk a loop branch is cut from, the base a changeset is read
        against) would otherwise stay where the clone found it. A head only
        ever moves forward, and never while a worktree has it checked out.
        """
        for head in await self._git.tracked_heads(repo_dir):
            if head.checked_out or head.sha == head.remote_sha:
                continue
            if not await self._git.is_ancestor(repo_dir, head.sha, head.remote_sha):
                continue
            await self._git.update_ref(repo_dir, head.ref, head.remote_sha, head.sha)
            await self._log.ainfo(
                "clone_head_fast_forwarded",
                repo_path=repo_dir,
                ref=head.ref,
                old_sha=head.sha,
                new_sha=head.remote_sha,
            )
