"""One lane's branch, as the doubles a lane-state test drives it through.

The repository is the shared fact: the git double reads it, the persister
advances it, and a test asserts against it.  Nothing here scripts a record
or a comment — every recorded fact has to come from a real observation of
this repository through the production reader it is written by.
"""

from kodezart.types.domain.consolidation import ChangesetDigest
from kodezart.types.domain.operation import OperationConfig
from tests.fakes import FakeGitService

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
