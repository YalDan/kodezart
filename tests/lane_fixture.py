"""One lane's branch, as the doubles a lane-state test drives it through.

The repository is the shared fact: the git double reads it, the persister
advances it, and a test asserts against it.  Nothing here scripts a record
or a comment — every recorded fact has to come from a real observation of
this repository through the production reader it is written by.
"""

from collections.abc import Awaitable, Callable

from kodezart.types.domain.consolidation import ChangesetDigest
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.persist import PersistResult, PersistSource
from kodezart.types.domain.subagents import NO_SUBAGENTS, UNCONFIGURED_SESSION_POLICY
from tests.chains.test_native_fire import NativeSourceReader
from tests.fakes import (
    FAKE_SESSION_TYPE,
    SUPPRESS_ALL_SKILLS,
    FakeChangePersister,
    FakeGitService,
)

#: The shas a trunk-shaped ref resolves to, as ``RemoteGit`` already spells it.
TRUNK_SHA = "b" * 40
TRUNK_BRANCHES = ("trunk", "main")


class LaneRepo:
    """The commits made on one lane branch and what the remote holds of them."""

    def __init__(self) -> None:
        self.shas: list[str] = []
        self.head: str = TRUNK_SHA
        self.pushed: str | None = None

    def commit(self) -> str:
        """Advance the branch by one commit and return its complete sha."""
        self.head = f"{len(self.shas) + 1:040x}"
        self.shas.append(self.head)
        return self.head

    def publish(self) -> None:
        """Record that the remote now holds this branch at its current head."""
        self.pushed = self.head


class LaneGit(FakeGitService):
    """The git reads of one lane, answered from the repository itself."""

    def __init__(self, repo: LaneRepo, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.repo = repo

    async def current_sha(self, cwd: str) -> str:
        self.calls.append(("current_sha", cwd))
        return self.repo.head

    async def remote_branch_sha(self, cwd: str, remote: str, branch: str) -> str | None:
        self.calls.append(("remote_branch_sha", cwd, remote, branch))
        if branch in TRUNK_BRANCHES:
            return TRUNK_SHA
        return self.repo.pushed

    async def diff_summary(
        self, cwd: str, base_ref: str, head_ref: str
    ) -> ChangesetDigest:
        self.calls.append(("diff_summary", cwd, base_ref, head_ref))
        made = self.repo.shas.index(head_ref) + 1 if head_ref in self.repo.shas else 0
        return ChangesetDigest(
            file_paths=[f"lane-{index}.py" for index in range(made)],
            commit_subjects=[f"feat: commit {index + 1}" for index in range(made)],
            commit_count=made,
        )

    async def is_ancestor(
        self, cwd: str, ancestor_ref: str, descendant_ref: str
    ) -> bool:
        self.calls.append(("is_ancestor", cwd, ancestor_ref, descendant_ref))
        shas = [TRUNK_SHA, *self.repo.shas]
        return (
            ancestor_ref in shas
            and descendant_ref in shas
            and shas.index(ancestor_ref) <= shas.index(descendant_ref)
        )


def lane_operation() -> OperationConfig:
    """An operation configuring exactly the marker purposes a lane writes."""
    return OperationConfig(
        operation_name="lane-fixture",
        workspace="fixture",
        marker_prefixes={
            "run_state": "lane-fixture-record",
            "run_event": "lane-fixture-event",
        },
        issue_labels={"decision": "decision"},
    )


class RecordingAfterPublish:
    """The record write's place in a test whose subject is the commit path."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, PersistResult]] = []

    async def __call__(self, workspace_path: str, receipt: PersistResult) -> None:
        self.calls.append((workspace_path, receipt))


class LaneSource(NativeSourceReader):
    """Resolves this lane's refs, HEAD included, against the repository."""

    def __init__(self, repo: LaneRepo) -> None:
        self.repo = repo

    async def resolve_commit(self, *, cwd: str, ref: str) -> str:
        if ref in TRUNK_BRANCHES:
            return TRUNK_SHA
        return ref if ref in self.repo.shas else self.repo.head


class LanePersister(FakeChangePersister):
    """Commits and pushes this lane's branch and returns the real receipt shape."""

    def __init__(self, repo: LaneRepo) -> None:
        super().__init__()
        self.repo = repo

    async def persist(
        self,
        *,
        workspace_path: str,
        branch: str,
        executor: object,
        backup_ref_id_prefix: str,
        skills: object = SUPPRESS_ALL_SKILLS,
        session_type: object = FAKE_SESSION_TYPE,
        run_identity: object = None,
        agents: object = NO_SUBAGENTS,
        session_policy: object = UNCONFIGURED_SESSION_POLICY,
        visibility: RepoVisibility = RepoVisibility.UNKNOWN,
        before_commit: Callable[[], Awaitable[None]] | None = None,
        before_publish: Callable[[str], Awaitable[None]] | None = None,
    ) -> PersistResult | None:
        if before_commit is not None:
            await before_commit()
        self.calls.append({"workspace_path": workspace_path, "branch": branch})
        sha = self.repo.commit()
        if before_publish is not None:
            await before_publish(sha)
        self.repo.publish()
        return PersistResult(
            commit_sha=sha,
            branch=branch,
            message=f"feat: commit {len(self.repo.shas)}\n\nthe body of that commit",
            source=PersistSource.WORKING_TREE_COMMIT,
        )
