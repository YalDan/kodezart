"""One lane's branch, as the doubles a lane-state test drives it through.

The repository is the shared fact: the git double reads it, the persister
advances it, and a test asserts against it.  Nothing here scripts a record
or a comment — every recorded fact has to come from a real observation of
this repository through the production reader it is written by.
"""

from collections.abc import Awaitable, Callable

import httpx

from kodezart.adapters.github.api import GitHubAPIClient
from kodezart.core.backoff import RetryPolicy
from kodezart.types.domain.consolidation import ChangesetDigest
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.persist import PersistResult, PersistSource
from kodezart.types.domain.subagents import NO_SUBAGENTS, UNCONFIGURED_SESSION_POLICY
from tests.chains.test_native_fire import (
    TRUNK_BRANCHES,
    TRUNK_SHA,
    NativeSourceReader,
)
from tests.fakes import (
    FAKE_SESSION_TYPE,
    SUPPRESS_ALL_SKILLS,
    FakeChangePersister,
    FakeGitService,
)

#: The API host the forge double is configured against and never asked at.
FORGE_API = "https://api.github.com"
#: The credential that host would need, for a client that never reaches it.
FORGE_CREDENTIAL = "lane-fixture-credential"


class LaneRepo:
    """The commits made on one lane branch and what the remote holds of them.

    The branch and the remote are the repository's own facts, so a read of
    any other pair is a read of something this repository cannot answer for:
    a double that answered every branch alike would report a lane's push
    status from a branch nobody pushed.
    """

    def __init__(self, *, branch: str, remote: str = "origin") -> None:
        self.branch = branch
        self.remote = remote
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
    """The git reads of one lane, answered from the repository itself.

    Per tree, not per run: a tree somebody left changes in, or moved to
    another commit, is that tree and no other, so both facts are keyed by
    the path they are asked about. A double answering every path alike
    could not tell a read of the graded workspace from a read of the cache
    the branch was resolved in.
    """

    def __init__(self, repo: LaneRepo) -> None:
        super().__init__()
        self.repo = repo
        #: Trees holding uncommitted changes, by the path each one is at.
        self.dirtied: set[str] = set()
        #: Trees standing at a commit other than the branch head.
        self.heads: dict[str, str] = {}

    async def current_sha(self, cwd: str) -> str:
        self.calls.append(("current_sha", cwd))
        return self.heads.get(cwd, self.repo.head)

    async def has_changes(self, cwd: str) -> bool:
        self.calls.append(("has_changes", cwd))
        return cwd in self.dirtied or self.has_changes_result

    async def remote_branch_sha(self, cwd: str, remote: str, branch: str) -> str | None:
        self.calls.append(("remote_branch_sha", cwd, remote, branch))
        if branch in TRUNK_BRANCHES:
            return TRUNK_SHA
        if (remote, branch) != (self.repo.remote, self.repo.branch):
            # The lane's branch on the lane's remote is the only ref this
            # repository holds; nothing else has been pushed anywhere.
            return None
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


def lane_forge() -> GitHubAPIClient:
    """The forge a lane records its branch page from, answering no request.

    Composing a branch address asks the forge nothing, so every request
    this client's transport receives is a failure: a record carrying the
    branch page proves the address was composed and not fetched.
    """

    def unasked(request: httpx.Request) -> httpx.Response:
        raise AssertionError("composing a branch address asks the forge nothing")

    return GitHubAPIClient(
        token=FORGE_CREDENTIAL,
        base_url=FORGE_API,
        ci_poll_interval_seconds=0.0,
        ci_poll_max_attempts=1,
        ci_no_checks_grace_polls=1,
        ci_no_workflows_grace_polls=1,
        ci_grace_poll_interval_seconds=0.0,
        ci_ref_not_found_grace_polls=1,
        ci_check_runs_max_pages=1,
        timeout_seconds=5.0,
        retry=RetryPolicy(attempts=1, initial_delay=0.0, jitter=0.0),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(unasked), base_url=FORGE_API
        ),
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
    """Commits and pushes this lane's branch and returns the real receipt shape.

    *publishes* answers, for the count of commits made so far, whether this
    one reaches the remote: a lane whose push is withheld holds a head the
    remote does not, which is the state a push status has to survive.
    """

    def __init__(
        self, repo: LaneRepo, *, publishes: Callable[[int], bool] | None = None
    ) -> None:
        super().__init__()
        self.repo = repo
        self.publishes = publishes

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
        if self.publishes is None or self.publishes(len(self.repo.shas)):
            self.repo.publish()
        return PersistResult(
            commit_sha=sha,
            branch=branch,
            message=f"feat: commit {len(self.repo.shas)}\n\nthe body of that commit",
            source=PersistSource.WORKING_TREE_COMMIT,
        )
