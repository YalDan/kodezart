"""The planner's ranking, not the branch order, is what a scope composes in.

Four lanes open in alphabetical order and record their deliverable refs in
that same order.  The planner ranks them ``c, a, d, b``: ``c`` first on the
urgency it INHERITS from the urgent member it blocks, then the two equally
prioritised lanes oldest-first, then the rest.

That shape is deliberate.  Nothing the union step can reach reproduces it:
not the opened order, not the recorded-ref order, not either alphabetical
direction, not age, not the roster reversed, and not a second reading of
the planner's own rule over each lane's OWN priority — which is exactly
where the inherited urgency is absent.  A ranking of two lanes cannot say
that, because at two positions most of those rivals coincide with the right
answer; the guard case below states each rival and its disagreement, so the
ordering cases mean what they claim.

Every case drives the shipped production constructor.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kodezart.adapters.subprocess_check_chain import SubprocessCheckChainRunner
from kodezart.chains.delivery_coordinator import DeliveryCoordinator
from kodezart.chains.scope_walker import read_scope_ready
from kodezart.core.config import AppConfig
from kodezart.domain.errors import CheckChainExecutionError, UnionHeadReadError
from kodezart.types.domain.branch import WorkRef, WorkRefRole
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.tracker import (
    IssuePriority,
    IssueRelation,
    IssueRelationKind,
    TrackerIssue,
    WorkflowStateKind,
    priority_rank,
)
from kodezart.types.domain.union import UnionLaneHead, UnionOutcome
from kodezart.types.domain.union_tick import UnionTickContext
from tests.fakes import FakeTrackerPort
from tests.services import test_union_composition as pinned

PROJECT = ScopeRef(kind=ScopeKind.PROJECT, key="project-one")

#: The lanes, in the order their branches and their recorded refs were made.
OPENED_ORDER: tuple[str, ...] = ("a", "b", "c", "d")

#: The scope member that is not a lane: urgent, and blocked by lane ``c``.
#: It never becomes ready itself, and it is what makes ``c`` compose first.
BLOCKED_KEY = "e"

#: The lane that member is blocked by — the one whose EFFECTIVE priority is
#: therefore urgent while its own recorded priority is the lowest of four.
URGENCY_SOURCE = "c"

#: Each lane's OWN recorded priority.
OWN_PRIORITY: dict[str, IssuePriority] = {
    "a": IssuePriority.HIGH,
    "b": IssuePriority.MEDIUM,
    "c": IssuePriority.LOW,
    "d": IssuePriority.HIGH,
}

#: Creation instants ascending in the opened order, so age alone orders the
#: roster the way the branches were opened and never the way it composes.
CREATED: dict[str, datetime] = {
    key: datetime(2026, 1, position + 1, tzinfo=UTC)
    for position, key in enumerate((*OPENED_ORDER, BLOCKED_KEY))
}

#: What the planner ranks, and therefore the order the scope composes in.
PLANNER_ORDER: tuple[str, ...] = ("c", "a", "d", "b")

#: One independent file per lane: four changes that compose cleanly.
INDEPENDENT_EDITS: dict[str, tuple[str, str]] = {
    lane: (f"{lane}.txt", f"{lane}\n") for lane in OPENED_ORDER
}

#: The same four lanes, where the first TWO the planner ranks add the same
#: file with different contents.  Under the opened order the conflict would
#: fall on ``c``; under the planner's it falls on ``a``.
CONFLICTING_EDITS: dict[str, tuple[str, str]] = {
    **INDEPENDENT_EDITS,
    "c": ("api.py", "def build(timeout):\n    return timeout\n"),
    "a": ("api.py", "def build(credentials):\n    return credentials\n"),
}

#: A chain the composed tree passes only when every lane is in it.
COMPOSED_CHECK: str = (
    f'{sys.executable} -c "from pathlib import Path; '
    + "".join(f"assert Path('{lane}.txt').is_file(); " for lane in OPENED_ORDER)
    + '"'
)


def issue(key: str, **changes: object) -> TrackerIssue:
    return TrackerIssue.model_validate(
        {
            "issue_key": key,
            "title": f"Title for {key}",
            "body": f"Body for {key}",
            "priority": IssuePriority.NONE,
            "state_name": "Todo",
            "state_kind": WorkflowStateKind.UNSTARTED,
            "queue_states": [],
            "team_key": "engineering",
            "project": PROJECT.key,
            "project_id": PROJECT.key,
            "created_at": CREATED.get(key, datetime(2026, 1, 1, tzinfo=UTC)),
            "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
            "url": f"https://tracker.invalid/{key}",
            **changes,
        }
    )


def criterion(parent: str) -> TrackerIssue:
    return issue(
        f"{parent}-check",
        parent_key=parent,
        issue_labels=frozenset({"criterion"}),
    )


def work_ref(lane: str, branch: str, sha: str) -> WorkRef:
    return WorkRef(
        issue_id=lane,
        role=WorkRefRole.DELIVERABLE,
        branch=branch,
        pushed_head_sha=sha,
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class Scope:
    """One approved project: four lanes plus the urgent member one blocks."""

    def __init__(self, heads: tuple[UnionLaneHead, ...]) -> None:
        self.by_lane = {head.lane_key: head for head in heads}
        self.refs: dict[str, list[WorkRef]] = {
            lane: [
                work_ref(lane, self.by_lane[lane].branch, self.by_lane[lane].head_sha)
            ]
            for lane in OPENED_ORDER
        }
        self.rows = [
            *(
                row
                for lane in OPENED_ORDER
                for row in (issue(lane, priority=OWN_PRIORITY[lane]), criterion(lane))
            ),
            issue(
                BLOCKED_KEY,
                priority=IssuePriority.URGENT,
                relations=(
                    IssueRelation(
                        kind=IssueRelationKind.BLOCKED_BY, issue_key=URGENCY_SOURCE
                    ),
                ),
            ),
            criterion(BLOCKED_KEY),
        ]

    def close_every_criterion(self) -> None:
        for parent in (*OPENED_ORDER, BLOCKED_KEY):
            key = f"{parent}-check"
            index = next(i for i, row in enumerate(self.rows) if row.issue_key == key)
            self.rows[index] = self.rows[index].model_copy(
                update={
                    "state_name": "Done",
                    "state_kind": WorkflowStateKind.COMPLETED,
                }
            )

    def tracker(self) -> FakeTrackerPort:
        return FakeTrackerPort(
            issues=self.rows,
            scope_containers=[
                ScopeContainer(
                    ref=PROJECT,
                    name=PROJECT.key,
                    description=f"Complete description of {PROJECT.key}",
                    url=f"https://tracker.invalid/project/{PROJECT.key}",
                    parent=None,
                )
            ],
            scope_memberships={PROJECT: [*OPENED_ORDER, BLOCKED_KEY]},
            scope_label_members={PROJECT: frozenset({ScopeLabel.APPROVED})},
            recorded_work_refs=self.refs,
        )


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

    def coordinator(self, runner: object = None) -> DeliveryCoordinator:
        return DeliveryCoordinator(
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


class RaisingRunner:
    """A chain that cannot be observed at all, on the composed tree."""

    def __init__(self) -> None:
        self.trees: list[str] = []

    async def run_chain(self, *, cwd: str, steps: object) -> object:
        self.trees.append(cwd)
        raise CheckChainExecutionError(
            cwd=cwd, step_name=None, reason="the runner could not start"
        )


@pytest.fixture
async def delivery(tmp_path):
    return await build_delivery(tmp_path / "green")


@pytest.fixture
async def conflicting_delivery(tmp_path):
    return await build_delivery(tmp_path / "conflicting", edits=CONFLICTING_EDITS)


async def test_no_order_the_union_step_could_re_derive_is_the_planner_order(delivery):
    """Guards every ordering case: each rival ordering disagrees with it.

    A fixture whose planner order coincides with a rival cannot tell a step
    that CONSUMES the ranking from one that recomputes it, and a ranking of
    two lanes coincides with most of them.
    """
    ranked = await read_scope_ready(ref=PROJECT, tracker=delivery.tracker)
    lanes = ranked.ready
    keys = tuple(lane.issue.issue_key for lane in lanes)
    assert keys == PLANNER_ORDER

    rivals = {
        "the opened order": OPENED_ORDER,
        "the recorded-ref order": tuple(delivery.scope.refs),
        "the roster reversed": tuple(reversed(keys)),
        "sorted by lane key": tuple(sorted(keys)),
        "sorted by lane key, descending": tuple(sorted(keys, reverse=True)),
        "sorted by age": tuple(
            lane.issue.issue_key
            for lane in sorted(lanes, key=lambda entry: entry.issue.created_at)
        ),
        "the planner's own rule over each lane's OWN priority": tuple(
            lane.issue.issue_key
            for lane in sorted(
                lanes,
                key=lambda entry: (
                    priority_rank(entry.issue.priority),
                    entry.issue.created_at,
                ),
            )
        ),
    }

    for description, order in rivals.items():
        assert order != PLANNER_ORDER, description


async def test_the_planner_ranking_is_the_order_the_scope_composes_in(delivery):
    """The ranked order reaches the git port unchanged, opened order does not."""
    ranked = await read_scope_ready(ref=PROJECT, tracker=delivery.tracker)
    ranking = tuple(lane.issue.issue_key for lane in ranked.ready)
    assert ranking == PLANNER_ORDER
    assert tuple(delivery.scope.refs) == OPENED_ORDER

    result = await delivery.coordinator().verify()

    assert result.composition_order == PLANNER_ORDER
    assert delivery.git.merged == [delivery.sha(lane) for lane in PLANNER_ORDER]
    assert await delivery.merge_graph_order(result.scratch_sha) == PLANNER_ORDER
    assert result.outcome is UnionOutcome.GREEN


async def test_the_lane_that_conflicts_is_the_one_the_ranking_reaches_second(
    conflicting_delivery,
):
    """Two lanes touch one file; which of them conflicts states the order."""
    first, second = PLANNER_ORDER[0], PLANNER_ORDER[1]

    result = await conflicting_delivery.coordinator().verify()

    assert result.outcome is UnionOutcome.RED
    assert result.merge_conflict.lane_key == second
    assert "api.py" in result.merge_conflict.paths
    assert result.composed_lane_heads == (conflicting_delivery.scope.by_lane[first],)
    assert conflicting_delivery.git.merged == [
        conflicting_delivery.sha(first),
        conflicting_delivery.sha(second),
    ]


async def test_the_scratch_tree_is_obtained_through_the_git_port_and_removed(delivery):
    result = await delivery.coordinator().verify()

    assert delivery.git.created == [result.scratch_path]
    assert delivery.git.removed == delivery.git.created
    assert not Path(result.scratch_path).exists()


async def test_a_raising_check_chain_still_removes_the_composed_tree(delivery):
    runner = RaisingRunner()

    with pytest.raises(CheckChainExecutionError):
        await delivery.coordinator(runner).verify()

    assert runner.trees == delivery.git.created
    assert delivery.git.removed == delivery.git.created
    assert not Path(delivery.git.created[0]).exists()


async def test_a_lane_with_no_recorded_deliverable_ref_refuses(delivery):
    delivery.scope.refs[URGENCY_SOURCE] = []
    delivery.tracker = delivery.scope.tracker()

    with pytest.raises(UnionHeadReadError) as raised:
        await delivery.coordinator().verify()

    assert raised.value.reason.endswith(URGENCY_SOURCE)
    assert delivery.git.created == []


async def test_a_lane_with_two_recorded_deliverable_refs_refuses(delivery):
    head = delivery.scope.by_lane[URGENCY_SOURCE]
    delivery.scope.refs[URGENCY_SOURCE].append(
        work_ref(URGENCY_SOURCE, "work/other", head.head_sha)
    )
    delivery.tracker = delivery.scope.tracker()

    with pytest.raises(UnionHeadReadError) as raised:
        await delivery.coordinator().verify()

    assert "more than one" in raised.value.reason
    assert delivery.git.created == []


async def test_a_scope_the_planner_ranks_nothing_in_refuses_to_compose(delivery):
    delivery.scope.close_every_criterion()
    delivery.tracker = delivery.scope.tracker()
    assert (await read_scope_ready(ref=PROJECT, tracker=delivery.tracker)).ready == ()

    with pytest.raises(UnionHeadReadError) as raised:
        await delivery.coordinator().verify()

    assert "no ready lane" in raised.value.reason
    assert delivery.git.created == []
