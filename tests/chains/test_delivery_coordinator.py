"""The planner's ranking, not the branch order, is what a scope composes in.

The repository fixture opens lane ``a``'s branch before lane ``z``'s and the
tracker records their deliverable refs in that same order, while the planner
ranks ``z`` first on priority.  Every case below drives the shipped
production constructor, so an implementation that sorted the roster, or took
the order the refs were recorded in, composes in the wrong order and fails.
"""

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
    TrackerIssue,
    WorkflowStateKind,
)
from kodezart.types.domain.union import UnionOutcome
from kodezart.types.domain.union_tick import UnionTickContext
from tests.fakes import FakeTrackerPort
from tests.services import test_union_composition as pinned

PROJECT = ScopeRef(kind=ScopeKind.PROJECT, key="project-one")

#: The lanes, in the order their branches and their recorded refs were made.
OPENED_ORDER: tuple[str, ...] = ("a", "z")

#: Priorities that make the planner rank the SECOND-opened lane first.
RANKING: dict[str, IssuePriority] = {
    "a": IssuePriority.LOW,
    "z": IssuePriority.URGENT,
}

repository = pinned.repository


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
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
            "url": f"https://tracker.invalid/{key}",
            **changes,
        }
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
    """One approved project whose lanes each still owe one criterion."""

    def __init__(self, heads: tuple[object, ...]) -> None:
        self.by_lane = {head.lane_key: head for head in heads}
        self.refs: dict[str, list[WorkRef]] = {
            lane: [
                work_ref(lane, self.by_lane[lane].branch, self.by_lane[lane].head_sha)
            ]
            for lane in OPENED_ORDER
        }
        self.rows = [
            row
            for lane in OPENED_ORDER
            for row in (
                issue(lane, priority=RANKING[lane]),
                issue(
                    f"{lane}-check",
                    parent_key=lane,
                    issue_labels=frozenset({"criterion"}),
                ),
            )
        ]

    def close_every_criterion(self) -> None:
        for lane in OPENED_ORDER:
            key = f"{lane}-check"
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
            scope_memberships={PROJECT: list(OPENED_ORDER)},
            scope_label_members={PROJECT: frozenset({ScopeLabel.APPROVED})},
            recorded_work_refs=self.refs,
        )


class Fixture:
    def __init__(
        self,
        *,
        scope: Scope,
        git: pinned.ObservedGit,
        context: UnionTickContext,
    ) -> None:
        self.scope = scope
        self.git = git
        self.context = context
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
async def delivery(repository, tmp_path):
    author, base, heads = repository
    remote, observer = tmp_path / "remote.git", tmp_path / "observer.git"
    await pinned.git(tmp_path, "clone", "--bare", str(author), str(remote))
    await pinned.git(
        tmp_path, "clone", "--bare", "--origin", "upstream", str(remote), str(observer)
    )
    return Fixture(
        scope=Scope(heads),
        git=pinned.ObservedGit(),
        context=UnionTickContext(
            scope_key=PROJECT.key,
            repo_path=str(observer),
            base_sha=base,
            git_remote="upstream",
            repo=pinned.entry().model_copy(update={"url": remote.as_uri()}),
        ),
    )


async def test_the_planner_ranking_is_the_order_the_scope_composes_in(delivery):
    """The ranked order reaches the git port unchanged, opened order does not."""
    ranked = await read_scope_ready(ref=PROJECT, tracker=delivery.tracker)
    ranking = tuple(lane.issue.issue_key for lane in ranked.ready)
    assert ranking == ("z", "a")
    assert ranking != OPENED_ORDER
    assert tuple(delivery.scope.refs) == OPENED_ORDER

    result = await delivery.coordinator().verify()

    assert result.composition_order == ranking
    assert delivery.git.merged == [delivery.sha("z"), delivery.sha("a")]
    assert result.outcome is UnionOutcome.GREEN


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
    delivery.scope.refs["z"] = []
    delivery.tracker = delivery.scope.tracker()

    with pytest.raises(UnionHeadReadError) as raised:
        await delivery.coordinator().verify()

    assert raised.value.reason.endswith("z")
    assert delivery.git.created == []


async def test_a_lane_with_two_recorded_deliverable_refs_refuses(delivery):
    head = delivery.scope.by_lane["z"]
    delivery.scope.refs["z"].append(work_ref("z", "work/other", head.head_sha))
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
