"""Infrastructure adapter implementing the RepoCache port.

Clones and fetches remote repos into a local bare cache.
"""

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
        else:
            await self._git.clone_bare(clone_url, repo_dir)
        return repo_dir
