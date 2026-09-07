"""Real scratch composition, ordered planner inputs, and owned cleanup."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from kodezart.adapters.subprocess_check_chain import SubprocessCheckChainRunner
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.core.config import AppConfig
from kodezart.domain.errors import CheckChainExecutionError
from kodezart.services.union_composition import UnionComposition
from kodezart.types.domain.operation import CheckStep, RepoEntry
from kodezart.types.domain.union import UnionLaneHead
from tests.fakes import FakeGitService


async def git(repo, *args):
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=repo,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        },
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()
    return stdout.decode().strip()


@pytest.fixture
async def repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    await git(repo, "init", "-b", "main")
    (repo / "base.txt").write_text("base\n")
    await git(repo, "add", ".")
    await git(repo, "commit", "-m", "base")
    base = await git(repo, "rev-parse", "HEAD")
    heads = []
    # The planner requests z before a although a's PR was opened first.
    for lane in ("a", "z"):
        await git(repo, "checkout", "-b", f"work/{lane}", base)
        (repo / f"{lane}.txt").write_text(lane)
        await git(repo, "add", ".")
        await git(repo, "commit", "-m", lane)
        heads.append(
            UnionLaneHead(
                lane_key=lane,
                branch=f"work/{lane}",
                head_sha=await git(repo, "rev-parse", "HEAD"),
            )
        )
    await git(repo, "checkout", "main")
    return repo, base, tuple(reversed(heads))


class ObservedGit(SubprocessGitService):
    def __init__(self):
        super().__init__(remote="upstream")
        self.created = []
        self.merged = []
        self.removed = []

    async def create_worktree(
        self, repo_path, base_ref, worktree_path, branch_name=None, create_branch=True
    ):
        assert branch_name is None
        assert create_branch is False
        await super().create_worktree(
            repo_path, base_ref, worktree_path, branch_name, create_branch
        )
        self.created.append(worktree_path)

    async def merge_scratch_head(self, **kwargs):
        self.merged.append(kwargs["head_sha"])
        await super().merge_scratch_head(**kwargs)

    async def remove_worktree(self, repo_path, worktree_path):
        await super().remove_worktree(repo_path, worktree_path)
        self.removed.append(worktree_path)


def service(adapter, runner=None):
    return UnionComposition(
        git=adapter,
        runner=runner or SubprocessCheckChainRunner(config=AppConfig()),
        author_name="Union Fixture",
        author_email="union@example.invalid",
    )


def entry(command=None):
    return RepoEntry(
        url="file:///fixture",
        trunk="main",
        checks=(
            CheckStep(
                name="gate",
                command=command
                or (
                    f'{sys.executable} -c "from pathlib import Path; '
                    "assert Path('z.txt').exists(); assert Path('a.txt').exists()\""
                ),
            ),
        ),
    )


async def verify(repository, adapter, runner=None, repo_entry=None):
    repo, base, heads = repository
    return await service(adapter, runner).verify(
        scope_key="scope",
        repo_path=str(repo),
        repo=repo_entry or entry(),
        base_sha=base,
        lane_heads=heads,
    )


async def test_real_independent_heads_merge_in_planner_order_and_remove(repository):
    adapter = ObservedGit()
    result = await verify(repository, adapter)
    repo, base, heads = repository
    assert adapter.merged == [h.head_sha for h in heads]
    assert result.composition_order == ("z", "a")
    assert not result.checks.failed_step_names
    assert result.base_sha == base
    assert result.lane_heads == heads
    assert result.scratch_sha != base
    assert adapter.removed == adapter.created == [result.scratch_path]
    assert not Path(result.scratch_path).exists()
    assert (await git(repo, "worktree", "list", "--porcelain")).count("worktree ") == 1
    # The actual merge graph, not just the fake's call order.
    last_parents = (
        await git(repo, "show", "-s", "--format=%P", result.scratch_sha)
    ).split()
    assert last_parents[1] == heads[1].head_sha
    prior_parents = (
        await git(repo, "show", "-s", "--format=%P", last_parents[0])
    ).split()
    assert prior_parents == [base, heads[0].head_sha]


class RaisingRunner:
    async def run_chain(self, **kwargs):
        raise RuntimeError("check transport broke")


async def test_runner_raise_still_removes_actual_composed_tree(repository):
    adapter = ObservedGit()
    with pytest.raises(RuntimeError, match="transport broke"):
        await verify(repository, adapter, RaisingRunner())
    assert adapter.removed == adapter.created
    assert adapter.created
    assert not Path(adapter.created[0]).exists()


async def test_merge_raise_still_removes_tree(repository):
    adapter = ObservedGit()
    repo, base, heads = repository
    broken = (heads[0], heads[1].model_copy(update={"head_sha": "f" * 40}))
    with pytest.raises(RuntimeError, match="merge"):
        await service(adapter).verify(
            scope_key="scope",
            repo_path=str(repo),
            repo=entry(),
            base_sha=base,
            lane_heads=broken,
        )
    assert adapter.removed == adapter.created
    assert not Path(adapter.created[0]).exists()


@pytest.mark.parametrize("phase", ["create", "merge", "remove"])
async def test_repeated_cancel_settles_owned_git_before_cleanup(repository, phase):
    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedGit(ObservedGit):
        async def create_worktree(self, *args, **kwargs):
            await super().create_worktree(*args, **kwargs)
            if phase == "create":
                entered.set()
                await release.wait()

        async def merge_scratch_head(self, **kwargs):
            await super().merge_scratch_head(**kwargs)
            if phase == "merge":
                entered.set()
                await release.wait()

        async def remove_worktree(self, *args):
            if phase == "remove":
                entered.set()
                await release.wait()
            await super().remove_worktree(*args)

    adapter = DelayedGit()
    task = asyncio.create_task(verify(repository, adapter))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
    assert adapter.removed == adapter.created
    assert not Path(adapter.created[0]).exists()


async def test_partial_worktree_creation_failure_is_cleaned(repository):
    class FailedCreate(ObservedGit):
        async def create_worktree(self, *args, **kwargs):
            await super().create_worktree(*args, **kwargs)
            raise RuntimeError("created but handshake failed")

    adapter = FailedCreate()
    with pytest.raises(RuntimeError, match="handshake"):
        await verify(repository, adapter)
    assert adapter.removed == adapter.created


async def test_scratch_merge_refuses_attached_branch_and_noncommit_ref(repository):
    repo, base, heads = repository
    adapter = SubprocessGitService(remote="upstream")
    with pytest.raises(ValueError, match="detached"):
        await adapter.merge_scratch_head(
            cwd=str(repo),
            head_sha=heads[0].head_sha,
            author_name="Test",
            author_email="t@example.invalid",
        )
    with pytest.raises(ValueError, match="immutable"):
        await adapter.merge_scratch_head(
            cwd=str(repo),
            head_sha="work/z",
            author_name="Test",
            author_email="t@example.invalid",
        )
    assert await git(repo, "rev-parse", "HEAD") == base


async def test_duplicate_planner_lanes_refuse_before_git():
    adapter = FakeGitService()
    same = UnionLaneHead(lane_key="a", branch="work/a", head_sha="a" * 40)
    with pytest.raises(ValueError, match="unique"):
        await service(adapter).verify(
            scope_key="scope",
            repo_path="unused",
            repo=entry(),
            base_sha="b" * 40,
            lane_heads=(same, same),
        )
    assert adapter.calls == []


async def test_composed_tree_without_declared_chain_uses_typed_refusal(repository):
    adapter = ObservedGit()
    no_chain = RepoEntry(url="file:///fixture", trunk="main")
    with pytest.raises(CheckChainExecutionError, match="no check chain") as caught:
        await verify(repository, adapter, RaisingRunner(), no_chain)
    assert adapter.merged == [head.head_sha for head in repository[2]]
    assert caught.value.step_name is None
    assert caught.value.cwd == adapter.created[0]
    assert adapter.removed == adapter.created
    assert not Path(caught.value.cwd).exists()


@pytest.mark.parametrize("raises", [False, True])
async def test_verification_cannot_publish_or_change_open_prs(repository, raises):
    from kodezart.core.protocols import ForgeQuery
    from tests.fakes import FakeForgeQuery

    class NoPublisher(ObservedGit):
        async def push(self, *args):
            pytest.fail("a scratch verification must not push")

        async def delete_remote_branch(self, *args):
            pytest.fail("a scratch verification must not delete a branch")

    repo, _, heads = repository
    opened = {
        (entry().url, h.branch): (f"https://forge.invalid/pr/{i}", i)
        for i, h in enumerate(reversed(heads), 1)
    }
    forge: ForgeQuery = FakeForgeQuery(open_prs=opened)
    assert not hasattr(forge, "merge")
    assert not hasattr(forge, "merge_pr")
    before = await git(repo, "show-ref")
    adapter = NoPublisher()
    if raises:
        with pytest.raises(RuntimeError, match="transport broke"):
            await verify(repository, adapter, RaisingRunner())
    else:
        await verify(repository, adapter)
    assert await git(repo, "show-ref") == before
    for h in heads:
        assert (
            await forge.open_pr_for_head(repo_url=entry().url, head=h.branch)
            == opened[(entry().url, h.branch)]
        )


async def test_incompatible_constructor_heads_are_each_green_but_union_red(repository):
    from kodezart.types.domain.union import UnionOutcome

    repo, _, _ = repository
    (repo / "api.py").write_text(
        "class Service:\n    def __init__(self):\n        pass\n"
    )
    await git(repo, "add", ".")
    await git(repo, "commit", "-m", "shared constructor")
    base = await git(repo, "rev-parse", "HEAD")
    heads = []
    runner = SubprocessCheckChainRunner(config=AppConfig())
    individual = []
    for lane, parameter in (("left", "timeout"), ("right", "credentials")):
        await git(repo, "checkout", "-b", lane, base)
        (repo / "api.py").write_text(
            f"class Service:\n    def __init__(self, {parameter}):\n"
            f"        self.{parameter} = {parameter}\n"
        )
        (repo / "check.py").write_text(
            f"from api import Service\nService({parameter}=object())\n"
        )
        await git(repo, "add", ".")
        await git(repo, "commit", "-m", lane)
        check_repo = entry(f"{sys.executable} -B check.py")
        individual.append(
            await runner.run_chain(cwd=str(repo), steps=check_repo.checks)
        )
        heads.append(
            UnionLaneHead(
                lane_key=lane,
                branch=lane,
                head_sha=await git(repo, "rev-parse", "HEAD"),
            )
        )
    await git(repo, "checkout", "main")
    assert all(not item.failed_step_names for item in individual)
    adapter = ObservedGit()
    result = await service(adapter).verify(
        scope_key="scope",
        repo_path=str(repo),
        repo=check_repo,
        base_sha=base,
        lane_heads=heads,
    )
    assert result.outcome is UnionOutcome.RED
    assert result.checks is None
    assert result.merge_conflict.lane_key == "right"
    assert "api.py" in result.merge_conflict.paths
    assert result.composed_lane_heads == (heads[0],)
    assert result.lane_heads == tuple(heads)
    assert all(not item.failed_step_names for item in individual)
    assert adapter.removed == adapter.created


async def test_clean_union_is_green_and_check_failure_is_red(repository):
    from kodezart.types.domain.union import UnionOutcome

    green = await verify(repository, ObservedGit())
    red = await verify(
        repository,
        ObservedGit(),
        repo_entry=entry(f'{sys.executable} -c "raise SystemExit(1)"'),
    )
    assert green.outcome is UnionOutcome.GREEN
    assert red.outcome is UnionOutcome.RED
    assert red.checks.failed_step_names == frozenset({"gate"})
    assert red.merge_conflict is None


async def test_real_consumer_calls_single_classifier_and_names_root_once(
    repository, monkeypatch
):
    from kodezart.domain import check_chain

    called = []
    actual = check_chain.classify_check_failures

    def observed(steps, failed):
        called.append((tuple(steps), failed))
        return actual(steps, failed)

    monkeypatch.setattr(check_chain, "classify_check_failures", observed)
    command = f'{sys.executable} -c "raise SystemExit(1)"'
    steps = (
        CheckStep(name="gate", command=command),
        CheckStep(name="middle", command=command, depends_on="gate"),
        CheckStep(name="last", command=command, depends_on="middle"),
    )
    result = await verify(
        repository,
        ObservedGit(),
        repo_entry=RepoEntry(url="file:///fixture", trunk="main", checks=steps),
    )
    assert called == [(steps, frozenset({"gate", "middle", "last"}))]
    assert result.remediation.root_step_names == ("gate",)
    assert result.remediation.cascade_step_names == ("middle", "last")
    assert (
        result.remediation.detail
        == "Repair union check roots: gate. Cascading checks: middle, last."
    )
    assert [output.name for output in result.checks.step_outputs] == [
        "gate",
        "middle",
        "last",
    ]


@pytest.mark.parametrize(
    "steps",
    [
        (
            CheckStep(name="a", command="a", depends_on="b"),
            CheckStep(name="b", command="b", depends_on="a"),
        ),
        (
            CheckStep(name="a", command="a", depends_on="b"),
            CheckStep(name="b", command="b", depends_on="missing"),
        ),
        (CheckStep(name="a", command="a"), CheckStep(name="a", command="a")),
    ],
)
async def test_malformed_chain_refuses_before_git_or_classifier(steps):
    adapter = FakeGitService()
    repo = RepoEntry(url="file:///fixture", trunk="main", checks=steps)
    with pytest.raises(CheckChainExecutionError):
        await service(adapter).verify(
            scope_key="scope",
            repo_path="unused",
            repo=repo,
            base_sha="b" * 40,
            lane_heads=(
                UnionLaneHead(lane_key="a", branch="work/a", head_sha="a" * 40),
            ),
        )
    assert adapter.calls == []
