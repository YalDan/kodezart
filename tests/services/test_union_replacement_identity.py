"""A union's named commit identities must describe the actual checked tree."""

from pathlib import Path

import pytest

from kodezart.domain.errors import MergeConflictError, UnionHeadReadError
from tests.fakes import FakeWorkspaceProvider
from tests.git_read_cancellation import assert_git_read_settles_before_release
from tests.services.test_union_composition import (
    ObservedGit,
    entry,
    git,
    verify,
)
from tests.services.test_union_composition import (
    repository as repository,
)
from tests.services.test_union_tick import CountingRunner
from tests.services.test_union_tick import current as current


async def replacement(repository):
    repo, base, heads = repository
    original = heads[-1].head_sha
    await git(repo, "checkout", "-b", "replacement", base)
    (repo / "a.txt").write_text("replacement")
    await git(repo, "add", ".")
    await git(repo, "commit", "-m", "different tree under original identity")
    alternate = await git(repo, "rev-parse", "HEAD")
    await git(repo, "checkout", "main")
    return original, alternate


@pytest.mark.parametrize("replaced", [False, True])
async def test_replacement_ref_cannot_substitute_the_named_lane_tree(
    repository, replaced
):
    repo, _, heads = repository
    original, alternate = await replacement(repository)
    if replaced:
        await git(repo, "replace", original, alternate)
    assert await git(repo, "--no-replace-objects", "show", f"{original}:a.txt") == "a"
    assert await git(repo, "show", f"{original}:a.txt") == (
        "replacement" if replaced else "a"
    )
    adapter = ObservedGit()
    if replaced:
        with pytest.raises(UnionHeadReadError, match="substitutes Git objects"):
            await verify(repository, adapter, repo_entry=entry("test $(cat a.txt) = a"))
        assert adapter.created == []
        return
    result = await verify(
        repository, adapter, repo_entry=entry("test $(cat a.txt) = a")
    )
    assert result.lane_heads == heads
    assert result.checks.failed_step_names == frozenset()


@pytest.mark.parametrize("phase", ["create", "merge", "check", "head"])
async def test_replacement_appearing_during_composition_refuses_and_releases(
    repository, monkeypatch, phase
):
    repo, _, heads = repository
    original, alternate = await replacement(repository)
    adapter, runner = ObservedGit(), CountingRunner()

    async def replace():
        await git(repo, "replace", original, alternate)

    if phase == "check":

        async def after_check(_call):
            await replace()

        runner.after = after_check
    else:
        name = {
            "create": "create_worktree",
            "merge": "merge_scratch_head",
            "head": "current_sha",
        }[phase]
        native = getattr(adapter, name)

        async def after_native(*args, **kwargs):
            result = await native(*args, **kwargs)
            if phase != "merge" or kwargs["head_sha"] == heads[-1].head_sha:
                await replace()
            return result

        monkeypatch.setattr(adapter, name, after_native)
    with pytest.raises(UnionHeadReadError, match="substitutes Git objects"):
        await verify(repository, adapter, runner)
    assert len(runner.calls) == (1 if phase in {"check", "head"} else 0)
    assert adapter.created == adapter.removed
    assert len(adapter.created) == 1
    assert not Path(adapter.created[0]).exists()


async def test_replacement_during_native_conflict_cannot_be_reported_as_remediation(
    current, monkeypatch
):
    first = await current.advance("z", branch="work/z", path="clash.txt")
    second = await current.advance("a", path="clash.txt")
    native = current.git.merge_scratch_head

    async def replace_after_conflict(**kwargs):
        try:
            await native(**kwargs)
        except MergeConflictError:
            await git(current.observer, "replace", second, first)
            raise

    monkeypatch.setattr(current.git, "merge_scratch_head", replace_after_conflict)
    await current.git.fetch(str(current.observer))
    heads = (
        current.heads[0].model_copy(update={"head_sha": first}),
        current.heads[1].model_copy(update={"head_sha": second}),
    )
    with pytest.raises(UnionHeadReadError, match="substitutes Git objects"):
        # Exercise the pinned public consumer directly: the tick's separate
        # final-head guard must not mask a missing conflict-result guard.
        await verify(
            (current.observer, current.context.base_sha, heads),
            current.git,
            current.runner,
        )
    assert len(current.git.created) == 1
    assert current.git.removed == current.git.created
    assert not Path(current.git.created[0]).exists()
    assert current.runner.calls == []


async def test_cached_union_refuses_new_local_replacements_then_reuses_after_removal(
    current,
):
    tick = current.consumer()
    first = await tick.verify(lane_branches=current.branches)
    before = tuple(current.git.created)
    await git(
        current.observer,
        "replace",
        current.heads[-1].head_sha,
        current.heads[0].head_sha,
    )
    with pytest.raises(UnionHeadReadError, match="substitutes Git objects"):
        await tick.verify(lane_branches=current.branches)
    assert tuple(current.git.created) == before
    assert len(current.runner.calls) == 1
    await git(current.observer, "replace", "-d", current.heads[-1].head_sha)
    assert await tick.verify(lane_branches=current.branches) is first
    assert tuple(current.git.created) == before


@pytest.mark.parametrize("kind", [OSError, RuntimeError, ValueError])
async def test_unreadable_replacement_namespace_is_a_typed_refusal(
    repository, monkeypatch, kind
):
    adapter = ObservedGit()
    error = kind("native replacement read unavailable")

    async def unreadable(_path):
        raise error

    monkeypatch.setattr(adapter, "has_replace_refs", unreadable)
    with pytest.raises(UnionHeadReadError, match="namespace is unreadable") as caught:
        await verify(repository, adapter)
    assert caught.value.__cause__ is error
    assert caught.value.scope_key == "scope" and caught.value.branch is None
    assert adapter.created == []


@pytest.mark.parametrize("read_number", [1, 2, 3])
async def test_composition_replacement_read_settles_repeated_cancellation(
    repository, monkeypatch, tmp_path, read_number
):
    adapter = ObservedGit()
    workspace = FakeWorkspaceProvider()
    native_remove = adapter.remove_worktree

    async def remove(repo_path, worktree):
        await workspace.release(worktree)
        await native_remove(repo_path, worktree)

    monkeypatch.setattr(adapter, "remove_worktree", remove)
    await assert_git_read_settles_before_release(
        invoke=lambda: verify(repository, adapter),
        git=adapter,
        workspace=workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        phase="has_replace_refs",
        read_number=read_number,
        expect_release=read_number != 1,
    )
    assert adapter.removed == adapter.created


async def test_cached_replacement_read_settles_repeated_cancellation(
    current, monkeypatch, tmp_path
):
    tick = current.consumer()
    await tick.verify(lane_branches=current.branches)
    created = tuple(current.git.created)
    heads = {head.branch: head.head_sha for head in current.heads}

    async def observed_head(repository, remote, branch):
        # The native first tick established these unchanged heads. Keep this
        # ownership control's child-ready deadline local to its target read;
        # the complete native cached-refusal path is exercised above.
        assert repository == str(current.observer) and remote == "upstream"
        return heads[branch]

    monkeypatch.setattr(current.git, "remote_branch_sha", observed_head)
    await assert_git_read_settles_before_release(
        invoke=lambda: tick.verify(lane_branches=current.branches),
        git=current.git,
        workspace=FakeWorkspaceProvider(),
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        phase="has_replace_refs",
        read_number=1,
        expect_release=False,
    )
    assert tuple(current.git.created) == created
