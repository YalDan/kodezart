"""PR114 real-Git exit and ref-invariance scenarios, without source scanners.

The named scenarios exercise cleanup and publication invariance directly;
no AST inventory or object-holdings heuristic is treated as runtime proof.
"""

import asyncio
from pathlib import Path

import pytest

from kodezart.adapters.git.check_chain import SubprocessCheckChainRunner
from kodezart.chains.delivery_coordinator import ScopeUnionCoordinator
from kodezart.config.app import AppConfig
from kodezart.domain.errors import CheckChainExecutionError, MergeConflictError
from kodezart.types.domain.operation import CheckStep
from kodezart.types.domain.union import UnionLaneHead, UnionOutcome
from kodezart.types.domain.union_tick import UnionTickContext
from tests.chains.test_delivery_coordinator import (
    OPENED_ORDER,
    PROJECT,
    RaisingRunner,
    Scope,
)
from tests.services import test_union_composition as pinned

INDEPENDENT_EDITS = {lane: (f"{lane}.txt", lane) for lane in OPENED_ORDER}
CONFLICTING_EDITS = {
    "z": ("api.py", "def build(timeout):\n    return timeout\n"),
    "a": ("api.py", "def build(credentials):\n    return credentials\n"),
}
COMPOSED_CHECK = pinned.entry().checks[0].command


class Fixture:
    """A whole delivery world: author repository, remote, and the observer."""

    def __init__(
        self,
        *,
        scope: Scope,
        git: pinned.ObservedGit,
        context: UnionTickContext,
        author: Path,
        remote: Path,
        observer: Path,
    ) -> None:
        self.scope = scope
        self.git = git
        self.context = context
        self.author = author
        self.remote = remote
        self.observer = observer
        self.tracker = scope.tracker()

    def coordinator(self, runner: object = None) -> ScopeUnionCoordinator:
        return ScopeUnionCoordinator(
            scope_kind=PROJECT.kind,
            tracker=self.tracker,
            git=self.git,
            runner=runner
            or SubprocessCheckChainRunner(
                timeout=AppConfig().union_check_step_timeout_seconds
            ),
            context=self.context,
            config=AppConfig(),
            committer_name="Union Fixture",
            committer_email="union@example.invalid",
        )

    def sha(self, lane: str) -> str:
        return self.scope.by_lane[lane].head_sha

    def with_checks(self, checks: object) -> None:
        """Replace the declared chain on the repository this scope composes."""
        self.context = self.context.model_copy(
            update={"repo": self.context.repo.model_copy(update={"checks": checks})}
        )

    async def refs(self) -> tuple[str, ...]:
        """Every ref in every repository — what any publication would move."""
        return tuple(
            [
                await pinned.git(path, "show-ref")
                for path in (self.author, self.remote, self.observer)
            ]
        )

    async def merge_graph_order(self, scratch_sha: str) -> tuple[str, ...]:
        """The lanes the composed commit actually merged, oldest merge first.

        Read out of the merge graph the scratch tree left behind rather than
        out of the port double's call log, so an order recorded correctly and
        composed differently is still caught.
        """
        by_sha = {head.head_sha: lane for lane, head in self.scope.by_lane.items()}
        merged: list[str] = []
        cursor = scratch_sha
        while True:
            parents = (
                await pinned.git(self.observer, "show", "-s", "--format=%P", cursor)
            ).split()
            if len(parents) != 2:
                return tuple(reversed(merged))
            merged.append(by_sha[parents[1]])
            cursor = parents[0]


async def make_repository(
    root: Path, *, edits: dict[str, tuple[str, str]]
) -> tuple[Path, str, tuple[UnionLaneHead, ...]]:
    """One branch per lane off a shared base, in the opened order."""
    repo = root / "repo"
    repo.mkdir(parents=True)
    await pinned.git(repo, "init", "-b", "main")
    (repo / "base.txt").write_text("base\n")
    await pinned.git(repo, "add", ".")
    await pinned.git(repo, "commit", "-m", "base")
    base = await pinned.git(repo, "rev-parse", "HEAD")
    heads: list[UnionLaneHead] = []
    for lane in OPENED_ORDER:
        path, text = edits[lane]
        await pinned.git(repo, "checkout", "-b", f"work/{lane}", base)
        (repo / path).write_text(text)
        await pinned.git(repo, "add", ".")
        await pinned.git(repo, "commit", "-m", lane)
        heads.append(
            UnionLaneHead(
                lane_key=lane,
                branch=f"work/{lane}",
                head_sha=await pinned.git(repo, "rev-parse", "HEAD"),
            )
        )
    await pinned.git(repo, "checkout", "main")
    return repo, base, tuple(heads)


async def build_delivery(
    root: Path,
    *,
    edits: dict[str, tuple[str, str]] | None = None,
    git: pinned.ObservedGit | None = None,
) -> Fixture:
    """The production wiring over a real repository, remote and observer."""
    root.mkdir(parents=True, exist_ok=True)
    author, base, heads = await make_repository(root, edits=edits or INDEPENDENT_EDITS)
    remote, observer = root / "remote.git", root / "observer.git"
    await pinned.git(root, "clone", "--bare", str(author), str(remote))
    await pinned.git(
        root, "clone", "--bare", "--origin", "upstream", str(remote), str(observer)
    )
    # Settle the observer's remote-tracking refs through the same port the
    # union step fetches with. Fetching is a read the step is entitled to
    # make; leaving its first one until then would put a legitimate read
    # inside any before/after comparison of the world's refs.
    await pinned.SubprocessGitService(remote="upstream").fetch(str(observer))
    return Fixture(
        scope=Scope(heads),
        git=git or pinned.ObservedGit(),
        context=UnionTickContext(
            scope_key=PROJECT.key,
            repo_path=str(observer),
            base_sha=base,
            git_remote="upstream",
            repo=pinned.entry(COMPOSED_CHECK).model_copy(
                update={"url": remote.as_uri()}
            ),
        ),
        author=author,
        remote=remote,
        observer=observer,
    )


class ForbiddenPublisher(pinned.ObservedGit):
    """The git port the union step actually holds, with publication fatal.

    Every one of these is reachable on the object the composition is given,
    so a step that grew a publish makes the case fail rather than pass
    quietly against a double that could not have been asked.
    """

    async def push(self, cwd: str, branch: str) -> None:
        raise AssertionError("the union step pushed a branch")

    async def merge_branch(self, cwd: str, source_branch: str) -> None:
        raise AssertionError("the union step merged a branch")

    async def delete_remote_branch(self, repo_path: str, branch: str) -> None:
        raise AssertionError("the union step deleted a remote branch")


class PathlessConflict(ForbiddenPublisher):
    """A refused merge git named no conflicting path for."""

    async def merge_scratch_head(self, **kwargs: object) -> None:
        raise MergeConflictError(
            "the scratch head could not be merged",
            source_branch=str(kwargs["head_sha"]),
            paths=(),
        )


class BlockedCreate(ForbiddenPublisher):
    """A git port that parks inside worktree creation until released."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def create_worktree(self, *args: object, **kwargs: object) -> None:
        await super().create_worktree(*args, **kwargs)
        self.entered.set()
        await self.release.wait()


async def drive_green(fixture) -> None:
    result = await fixture.coordinator().verify()
    assert result.outcome is UnionOutcome.GREEN


async def drive_merge_conflict(fixture) -> None:
    result = await fixture.coordinator().verify()
    assert result.outcome is UnionOutcome.RED
    assert result.merge_conflict is not None


async def drive_pathless_conflict(fixture) -> None:
    with pytest.raises(MergeConflictError):
        await fixture.coordinator().verify()


async def drive_unclassifiable_chain(fixture) -> None:
    repeated = CheckStep(name="gate", command="true")
    fixture.with_checks((repeated, repeated))
    with pytest.raises(CheckChainExecutionError):
        await fixture.coordinator().verify()


async def drive_undeclared_chain(fixture) -> None:
    fixture.with_checks(())
    with pytest.raises(CheckChainExecutionError):
        await fixture.coordinator().verify()


async def drive_unobservable_chain(fixture) -> None:
    with pytest.raises(CheckChainExecutionError):
        await fixture.coordinator(RaisingRunner()).verify()


async def drive_cancellation(fixture) -> None:
    task = asyncio.create_task(fixture.coordinator().verify())
    try:
        await asyncio.wait_for(fixture.git.entered.wait(), 10)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        fixture.git.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)


EXIT_SCENARIOS = (
    ("a green union", None, ForbiddenPublisher, drive_green),
    ("a merge conflict", CONFLICTING_EDITS, ForbiddenPublisher, drive_merge_conflict),
    ("a conflict naming no path", None, PathlessConflict, drive_pathless_conflict),
    (
        "a chain that cannot be classified",
        None,
        ForbiddenPublisher,
        drive_unclassifiable_chain,
    ),
    (
        "a repository declaring no chain",
        None,
        ForbiddenPublisher,
        drive_undeclared_chain,
    ),
    (
        "a chain that cannot be observed",
        None,
        ForbiddenPublisher,
        drive_unobservable_chain,
    ),
    ("cancellation while composing", None, BlockedCreate, drive_cancellation),
)


@pytest.mark.parametrize(
    "name, edits, publisher, drive",
    EXIT_SCENARIOS,
    ids=[row[0] for row in EXIT_SCENARIOS],
)
async def test_named_union_exit_preserves_real_refs_and_removes_scratch(
    tmp_path,
    name,
    edits,
    publisher,
    drive,
):
    fixture = await build_delivery(tmp_path / "world", edits=edits, git=publisher())
    before = await fixture.refs()

    await drive(fixture)

    assert await fixture.refs() == before, name
    assert fixture.git.created == fixture.git.removed, name
    assert all(not Path(path).exists() for path in fixture.git.created), name
    assert (
        await pinned.git(fixture.observer, "worktree", "list", "--porcelain")
    ).count("worktree ") == 1
