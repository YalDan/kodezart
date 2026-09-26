"""Which declared repositories a scope run's branch holds commits in.

A scope run checks out every repository the operation declares and lets
its agent choose where the work goes, so what the run later merges,
reviews, opens pull requests for and watches is read off the clones:
a repository gained commits when its branch holds commits beyond its trunk.
"""

from collections.abc import Sequence
from typing import NamedTuple

from kodezart.core.prompt_namespaces import repo_display
from kodezart.core.protocols import GitService, RepoCache
from kodezart.types.domain.consolidation import ChangesetDigest
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.scope import ScopeRef

#: ``ls-remote`` over the clone itself answers from the clone's own branches.
_THE_CLONE = "."


class Gained(NamedTuple):
    """A repository whose branch holds commits beyond its trunk."""

    repository: RepoEntry
    head_sha: str
    changeset: ChangesetDigest


def scope_repositories(
    scope: ScopeRef | None, declared: Sequence[RepoEntry]
) -> Sequence[RepoEntry]:
    """Every declared repository on a scope run; none on any other run.

    Any other run works in the one repository its request names, exactly
    as before, which is what an empty answer tells each caller.
    """
    return declared if scope is not None else ()


async def gained_commits(
    *,
    git: GitService,
    cache: RepoCache,
    repositories: Sequence[RepoEntry],
    branch: str,
    cache_key: str | None,
) -> list[Gained]:
    """The repositories whose clone holds commits on *branch* beyond its trunk.

    A clone with no *branch* at all gained nothing on it.
    """
    gained: list[Gained] = []
    for repository in repositories:
        clone = await cache.ensure_available(repository.url, cache_key)
        head_sha = await git.remote_branch_sha(clone, _THE_CLONE, branch)
        if head_sha is None:
            continue
        changeset = await git.diff_summary(clone, repository.trunk, branch)
        if changeset.commit_count > 0:
            gained.append(Gained(repository, head_sha, changeset))
    return gained


def folded(gained: Sequence[Gained]) -> ChangesetDigest:
    """One digest of every repository's commits, each path under its directory."""
    return ChangesetDigest(
        file_paths=[
            f"{repo_display(each.repository.url)[0]}/{path}"
            for each in gained
            for path in each.changeset.file_paths
        ],
        commit_subjects=[
            subject for each in gained for subject in each.changeset.commit_subjects
        ],
        commit_count=sum(each.changeset.commit_count for each in gained),
    )
