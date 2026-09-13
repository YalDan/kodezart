"""Real squash and merge graphs cannot change a recorded landing decision."""

from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.services.base_resolver import BaseResolver
from kodezart.types.domain.branch import WorkRef, WorkRefLanding, WorkRefRole
from tests.services.test_union_composition import git
from tests.tracker.conftest import APPROVED_ISSUE, CLAIMED_ISSUE, FIXTURE_NOW


class ObservedGit(SubprocessGitService):
    def __init__(self):
        super().__init__(remote="upstream")
        self.commands = []

    async def _run_output(self, *args, **kwargs):
        self.commands.append(args)
        return await super()._run_output(*args, **kwargs)

    async def _run(self, *args, **kwargs):
        self.commands.append(args)
        return await super()._run(*args, **kwargs)

    async def _run_with_exit_codes(self, *args, **kwargs):
        self.commands.append(args)
        return await super()._run_with_exit_codes(*args, **kwargs)


async def test_squash_and_merge_commit_read_the_same_observer_fact(tracker, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    await git(repo, "init", "-b", "start")
    (repo / "base.txt").write_text("base\n")
    await git(repo, "add", ".")
    await git(repo, "commit", "-m", "base")
    await git(repo, "checkout", "-b", "work/blocker")
    (repo / "feature.txt").write_text("feature\n")
    await git(repo, "add", ".")
    await git(repo, "commit", "-m", "feature")
    feature = await git(repo, "rev-parse", "HEAD")
    await git(repo, "checkout", "start")
    (repo / "other.txt").write_text("other lane\n")
    await git(repo, "add", ".")
    await git(repo, "commit", "-m", "other lane")
    await git(repo, "checkout", "-b", "trunk-squash")
    await git(repo, "merge", "--squash", "work/blocker")
    await git(repo, "commit", "-m", "landed by squash")
    await git(repo, "checkout", "-b", "trunk-merge", "start")
    await git(repo, "merge", "--no-ff", "work/blocker", "-m", "landed by merge")
    assert await git(repo, "merge-base", "work/blocker", "trunk-squash") != feature
    assert await git(repo, "merge-base", "work/blocker", "trunk-merge") == feature
    assert await git(repo, "rev-parse", "trunk-squash^{tree}") == await git(
        repo, "rev-parse", "trunk-merge^{tree}"
    )
    await tracker.record_work_ref(
        ref=WorkRef(
            issue_id=CLAIMED_ISSUE,
            role=WorkRefRole.DELIVERABLE,
            branch="work/blocker",
            pushed_head_sha=feature,
            landing=WorkRefLanding.LANDED,
            recorded_at=FIXTURE_NOW,
        )
    )
    adapter = ObservedGit()
    resolver = BaseResolver(tracker=tracker, git=adapter, remote="upstream")
    resolved = []
    for strategy in ("trunk-squash", "trunk-merge"):
        await git(repo, "update-ref", "refs/heads/scope-trunk", strategy)
        resolved.append(
            await resolver.resolve(
                issue_key=APPROVED_ISSUE,
                repo_path=str(repo),
                integration_workspace=str(tmp_path / "unused"),
                trunk="scope-trunk",
                now=FIXTURE_NOW,
            )
        )
    assert resolved[0] == resolved[1]
    assert resolved[0].base_branch == "scope-trunk"
    assert resolved[0].inputs == ()
    assert adapter.commands == []
