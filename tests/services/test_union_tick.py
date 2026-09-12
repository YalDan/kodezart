"""Current scope verification consumes real remote heads and real scratch checks."""

import asyncio
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import pytest
from pydantic import ValidationError

from kodezart.adapters.subprocess_check_chain import SubprocessCheckChainRunner
from kodezart.core.config import AppConfig
from kodezart.domain.errors import UnionHeadReadError, UnionUnstableError
from kodezart.services.union_composition import UnionComposition
from kodezart.services.union_tick import UnionTick
from kodezart.types.domain.operation import CheckStep
from kodezart.types.domain.union import UnionOutcome
from kodezart.types.domain.union_tick import UnionLaneBranch, UnionTickContext
from tests.fakes import FakeWorkspaceProvider
from tests.git_read_cancellation import assert_git_read_settles_before_release
from tests.services import test_union_composition as pinned

repository = pinned.repository


class CountingRunner:
    def __init__(self):
        self.actual = SubprocessCheckChainRunner(
            timeout=AppConfig().union_check_step_timeout_seconds
        )
        self.calls = []
        self.after = None

    async def run_chain(self, **kwargs):
        self.calls.append(kwargs["cwd"])
        result = await self.actual.run_chain(**kwargs)
        if self.after is not None:
            await self.after(len(self.calls))
        return result


@dataclass
class Fixture:
    author: Path
    remote: Path
    observer: Path
    heads: tuple
    git: pinned.ObservedGit
    runner: CountingRunner
    context: UnionTickContext

    @property
    def branches(self):
        return tuple(
            UnionLaneBranch(lane_key=h.lane_key, branch=h.branch) for h in self.heads
        )

    def consumer(self, attempts=3, context=None):
        return UnionTick(
            composition=UnionComposition(
                git=self.git,
                runner=self.runner,
                author_name="Fixture",
                author_email="fixture@example.invalid",
            ),
            git=self.git,
            context=context or self.context,
            config=AppConfig(union_stale_max_attempts=attempts),
        )

    async def advance(self, text, *, branch="work/a", path="a.txt"):
        await pinned.git(self.author, "checkout", branch)
        (self.author / path).write_text(text)
        await pinned.git(self.author, "add", ".")
        await pinned.git(self.author, "commit", "-m", "new lane work")
        sha = await pinned.git(self.author, "rev-parse", "HEAD")
        await pinned.git(self.author, "push", str(self.remote), branch)
        await pinned.git(self.author, "checkout", "main")
        return sha


@pytest.fixture
async def current(repository, tmp_path):
    author, base, heads = repository
    remote, observer = tmp_path / "remote.git", tmp_path / "observer.git"
    await pinned.git(tmp_path, "clone", "--bare", str(author), str(remote))
    await pinned.git(
        tmp_path, "clone", "--bare", "--origin", "upstream", str(remote), str(observer)
    )
    return Fixture(
        author,
        remote,
        observer,
        heads,
        pinned.ObservedGit(),
        CountingRunner(),
        UnionTickContext(
            scope_key="scope/one",
            repo_path=str(observer),
            base_sha=base,
            git_remote="upstream",
            repo=pinned.entry().model_copy(update={"url": remote.as_uri()}),
        ),
    )


async def test_native_unchanged_heads_reuse_the_whole_scope_result(current):
    tick = current.consumer()
    first = await tick.verify(lane_branches=current.branches)
    assert await tick.verify(lane_branches=current.branches) is first
    assert first.lane_heads == current.heads
    assert first.scope_key == "scope/one" and first.composition_order == ("z", "a")
    assert first.outcome is UnionOutcome.GREEN
    assert len(current.runner.calls) == 1
    assert current.git.removed == current.git.created == [first.scratch_path]
    assert not Path(first.scratch_path).exists()
    assert (
        await pinned.git(current.observer, "worktree", "list", "--porcelain")
    ).count("worktree ") == 1


async def test_native_changed_head_invalidates_the_previous_result(current):
    tick = current.consumer()
    first = await tick.verify(lane_branches=current.branches)
    sha = await current.advance("Changed native lane\n")
    second = await tick.verify(lane_branches=current.branches)
    assert second is not first and second.lane_heads[-1].head_sha == sha
    assert len(current.runner.calls) == 2
    assert current.git.removed == current.git.created


async def test_native_move_after_fetch_retries_before_composing_missing_objects(
    current, monkeypatch
):
    fetch = current.git.fetch
    fetched = []
    moved = []

    async def move_after_first_fetch(path):
        await fetch(path)
        fetched.append(path)
        if len(fetched) == 1:
            moved.append(await current.advance("new object after fetch\n"))

    monkeypatch.setattr(current.git, "fetch", move_after_first_fetch)
    result = await current.consumer().verify(lane_branches=current.branches)
    assert result.lane_heads[-1].head_sha == moved[0]
    assert result.outcome is UnionOutcome.GREEN
    assert len(fetched) == 2 and len(current.runner.calls) == 1
    assert len(current.git.created) == 1
    assert current.git.removed == current.git.created


async def test_native_continuous_fetch_movement_refuses_before_composition(
    current, monkeypatch
):
    fetch = current.git.fetch
    fetched = []

    async def move_after_every_fetch(path):
        await fetch(path)
        fetched.append(path)
        await current.advance(f"move after fetch {len(fetched)}\n")

    monkeypatch.setattr(current.git, "fetch", move_after_every_fetch)
    with pytest.raises(UnionUnstableError) as raised:
        await current.consumer(attempts=2).verify(lane_branches=current.branches)
    assert raised.value.attempts == 2
    assert raised.value.measured_shas != raised.value.current_shas
    assert len(fetched) == 2 and not current.git.created
    assert not current.runner.calls


@pytest.mark.parametrize(
    "initial,current_text,expected",
    [("a", "red", UnionOutcome.RED), ("red", "a", UnionOutcome.GREEN)],
)
async def test_native_move_during_check_reruns_before_reporting(
    current, initial, current_text, expected
):
    if initial != "a":
        await current.advance(initial)
    repo = current.context.repo.model_copy(
        update={"checks": (CheckStep(name="gate", command="test $(cat a.txt) = a"),)}
    )
    tick = current.consumer(context=current.context.model_copy(update={"repo": repo}))
    moved = []

    async def change_first(call):
        if call == 1:
            moved.append(await current.advance(current_text))

    current.runner.after = change_first
    result = await tick.verify(lane_branches=current.branches)
    assert result.outcome is expected
    assert result.lane_heads[-1].head_sha == moved[0]
    assert len(current.runner.calls) == 2
    assert current.git.removed == current.git.created
    assert not any(Path(path).exists() for path in current.git.created)
    assert await tick.verify(lane_branches=current.branches) is result


async def test_native_continuous_movement_refuses_and_never_caches_stale_green(current):
    tick = current.consumer(attempts=2)

    async def move_every_time(call):
        await current.advance(f"move {call}\n")

    current.runner.after = move_every_time
    with pytest.raises(UnionUnstableError) as raised:
        await tick.verify(lane_branches=current.branches)
    assert raised.value.scope_key == "scope/one"
    assert raised.value.attempts == 2 and raised.value.lane_keys == ("z", "a")
    assert raised.value.current_shas != raised.value.measured_shas
    assert len(current.runner.calls) == 2
    assert current.git.removed == current.git.created
    current.runner.after = None
    result = await tick.verify(lane_branches=current.branches)
    assert result.outcome is UnionOutcome.GREEN and len(current.runner.calls) == 3


async def test_reordered_planner_lanes_do_not_reuse_another_composition(current):
    tick = current.consumer()
    first = await tick.verify(lane_branches=current.branches)
    second = await tick.verify(lane_branches=tuple(reversed(current.branches)))
    assert first.composition_order == ("z", "a")
    assert second.composition_order == ("a", "z")
    assert len(current.runner.calls) == 2


async def test_concurrent_identical_scope_calls_share_one_composition(current):
    tick = current.consumer()
    started, finish = asyncio.Event(), asyncio.Event()

    async def hold(_call):
        started.set()
        await finish.wait()

    current.runner.after = hold
    first = asyncio.create_task(tick.verify(lane_branches=current.branches))
    second = None
    try:
        await asyncio.wait_for(started.wait(), 5)
        second = asyncio.create_task(tick.verify(lane_branches=current.branches))
        await asyncio.sleep(0.02)
        assert not second.done() and len(current.runner.calls) == 1
        finish.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), 5)
        assert results[0] is results[1] and len(current.runner.calls) == 1
    finally:
        finish.set()
        await asyncio.gather(
            first, *([second] if second is not None else []), return_exceptions=True
        )


@pytest.mark.parametrize(
    "phase,read_number",
    [("fetch", 1), ("remote_branch_sha", 1), ("remote_branch_sha", 5)],
)
async def test_native_read_cancellation_settles_before_union_returns(
    current, monkeypatch, tmp_path, phase, read_number
):
    tick = current.consumer()
    await assert_git_read_settles_before_release(
        invoke=partial(tick.verify, lane_branches=current.branches),
        git=current.git,
        workspace=FakeWorkspaceProvider(),
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        phase=phase,
        read_number=read_number,
        expect_release=False,
    )
    if read_number == 1:
        assert current.runner.calls == [] and current.git.created == []
    else:
        assert len(current.runner.calls) == 1
        assert current.git.removed == current.git.created
        await tick.verify(lane_branches=current.branches)
        assert len(current.runner.calls) == 2


async def test_native_stale_merge_conflict_is_rerun_before_reporting(
    current, monkeypatch
):
    await current.advance("z", branch="work/z", path="clash.txt")
    await current.advance("a", path="clash.txt")
    original = current.git.current_sha
    moved = []

    async def repair_after_observation(worktree):
        sha = await original(worktree)
        if not moved:
            moved.append(await current.advance("z", path="clash.txt"))
        return sha

    monkeypatch.setattr(current.git, "current_sha", repair_after_observation)
    result = await current.consumer().verify(lane_branches=current.branches)
    assert result.outcome is UnionOutcome.GREEN and result.merge_conflict is None
    assert result.lane_heads[-1].head_sha == moved[0]
    assert len(current.runner.calls) == 1 and len(current.git.created) == 2
    assert current.git.removed == current.git.created


async def test_new_configuration_instance_cannot_reuse_another_check_chain(current):
    first = await current.consumer().verify(lane_branches=current.branches)
    repo = current.context.repo.model_copy(
        update={"checks": (CheckStep(name="gate", command="exit 1"),)}
    )
    other = current.consumer(context=current.context.model_copy(update={"repo": repo}))
    second = await other.verify(lane_branches=current.branches)
    assert first.outcome is UnionOutcome.GREEN and second.outcome is UnionOutcome.RED
    assert first.lane_heads == second.lane_heads and len(current.runner.calls) == 2


async def test_unreadable_current_head_does_not_return_cached_green(
    current, monkeypatch
):
    tick = current.consumer()
    await tick.verify(lane_branches=current.branches)

    async def unreadable(*_args, **_kwargs):
        raise OSError("unreadable current head")

    monkeypatch.setattr(current.git, "remote_branch_sha", unreadable)
    with pytest.raises(UnionHeadReadError) as raised:
        await tick.verify(lane_branches=current.branches)
    assert isinstance(raised.value.__cause__, OSError)
    assert len(current.runner.calls) == 1


async def test_unreadable_fetch_refuses_with_cause_before_composition(
    current, monkeypatch
):
    failure = RuntimeError("fetch failed")

    async def unreadable(_path):
        raise failure

    monkeypatch.setattr(current.git, "fetch", unreadable)
    with pytest.raises(UnionHeadReadError) as raised:
        await current.consumer().verify(lane_branches=current.branches)
    assert raised.value.__cause__ is failure and raised.value.branch is None
    assert not current.git.created and not current.runner.calls


@pytest.mark.parametrize("outcome", [None, "not-a-sha", OSError("unreadable")])
async def test_missing_malformed_and_unreadable_heads_refuse_before_composition(
    current, monkeypatch, outcome
):
    async def read(*_args, **_kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(current.git, "remote_branch_sha", read)
    with pytest.raises(UnionHeadReadError) as raised:
        await current.consumer().verify(lane_branches=current.branches)
    assert raised.value.scope_key == "scope/one" and raised.value.branch == "work/z"
    assert not current.git.created and not current.runner.calls
    if isinstance(outcome, Exception):
        assert raised.value.__cause__ is outcome


@pytest.mark.parametrize(
    "invalid",
    [
        (),
        (
            UnionLaneBranch(lane_key="x", branch="one"),
            UnionLaneBranch(lane_key="x", branch="two"),
        ),
    ],
)
async def test_invalid_roster_refuses_before_any_native_read(
    current, monkeypatch, invalid
):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("invalid plan reached native read")

    monkeypatch.setattr(current.git, "remote_branch_sha", forbidden)
    with pytest.raises(ValidationError):
        await current.consumer().verify(lane_branches=invalid)
    assert not current.git.created


@pytest.mark.parametrize("value", [0, -1])
def test_stale_attempt_bound_is_positive(value):
    with pytest.raises(ValidationError):
        AppConfig(union_stale_max_attempts=value)


def test_stale_attempt_bound_has_its_own_environment_name(monkeypatch):
    assert AppConfig().union_stale_max_attempts == 3
    monkeypatch.setenv("KODEZART_UNION_STALE_MAX_ATTEMPTS", "2")
    assert AppConfig().union_stale_max_attempts == 2
