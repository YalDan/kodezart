"""Stacked lanes, against real git: inherited work survives the run.

The defect this suite exists to fail: a lane built on another lane's
branch, graded against trunk, reads everything it inherited as its own
out-of-scope change — and reverts it. Every fixture below builds a REAL
stack in a REAL repository and runs the REAL engine over it, because the
claim is about files on a branch after a loop and a review, and no fake
git service can make that claim true.
"""

import asyncio
import uuid
from pathlib import Path

import pytest

from kodezart.adapters.git_branch_merger import GitBranchMerger
from kodezart.adapters.git_change_persister import GitChangePersister
from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.chains.ralph_loop import RalphLoop
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.chains.ticket_generation import TicketGenerationLoop
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AgentEvent,
    WorkflowCompleteEvent,
    WorkflowScopeBaseEvent,
)
from kodezart.types.domain.branch import (
    BaseInput,
    BaseSpec,
    WorkRefRole,
    trunk_base,
)
from kodezart.types.domain.ticket_review import TicketReviewMode
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    PassThroughGate,
    ScriptedFakeExecutor,
    make_prompt_provider,
    no_delay_floor,
)

BLOCKER_A_BRANCH = "kodezart/blocker-a-11111111"
BLOCKER_B_BRANCH = "kodezart/blocker-b-22222222"
INTEGRATION_BRANCH = "kodezart/integration-33333333"

A_FILE = "inherited_from_a.py"
A_LINES = "def landed_in_a() -> int:\n    return 1\n"
B_FILE = "inherited_from_b.py"
B_LINES = "def landed_in_b() -> int:\n    return 2\n"
# What the scripted agent writes: this lane's own, in-scope change.
LANE_OWN_FILE = "scripted_change.txt"


async def _git(cmd: list[str], cwd: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = f"{' '.join(cmd)} failed: {stderr.decode()}"
        raise RuntimeError(msg)


async def _git_output(cmd: list[str], cwd: Path) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = f"{' '.join(cmd)} failed: {stderr.decode()}"
        raise RuntimeError(msg)
    return stdout.decode().strip()


async def _commit_file(repo: Path, name: str, body: str, message: str) -> None:
    (repo / name).write_text(body, encoding="utf-8")
    await _git(["git", "add", name], cwd=repo)
    await _git(["git", "commit", "-m", message], cwd=repo)


@pytest.fixture
async def trunk_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A repository with a bare remote and one commit on ``main``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    bare = tmp_path / "remote.git"
    bare.mkdir()
    (tmp_path / "cache").mkdir()

    await _git(["git", "init", "-b", "main"], cwd=repo)
    await _git(["git", "config", "user.email", "t@t.dev"], cwd=repo)
    await _git(["git", "config", "user.name", "test"], cwd=repo)
    await _commit_file(repo, "trunk.py", "TRUNK = True\n", "chore: trunk")
    await _git(["git", "init", "--bare"], cwd=bare)
    await _git(["git", "remote", "add", "origin", str(bare)], cwd=repo)
    await _git(["git", "push", "-u", "origin", "HEAD:refs/heads/main"], cwd=repo)
    return repo, bare


async def _branch_from(
    repo: Path,
    *,
    name: str,
    start: str,
    file_name: str,
    body: str,
) -> str:
    """Create *name* off *start*, land one file on it, push it. Returns its sha."""
    await _git(["git", "checkout", "-b", name, start], cwd=repo)
    await _commit_file(repo, file_name, body, f"feat: work landed on {name}")
    await _git(["git", "push", "origin", name], cwd=repo)
    sha = await _git_output(["git", "rev-parse", "HEAD"], cwd=repo)
    await _git(["git", "checkout", "main"], cwd=repo)
    return sha


def _engine(repo: Path, tmp_path: Path) -> RalphWorkflowEngine:
    """The real engine over real git, with only the model scripted."""
    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="t@t.dev",
    )
    persister = GitChangePersister(
        gate=PassThroughGate(),
        prompts=make_prompt_provider(),
        git=git,
        committer_name="test",
        committer_email="t@t.dev",
        remote="origin",
    )
    passing = {
        "criteriaResults": [
            {
                "criterionId": f"AC-{n}",
                "criterion": f"criterion {n}",
                "passed": True,
                "reasoning": "scripted pass",
            }
            for n in (1, 2, 3)
        ],
    }
    executor = ScriptedFakeExecutor(eval_results=[passing, passing])
    service = AgentService(
        git_base_url="https://github.com",
        executor=executor,
        workspace=workspace,
        persister=persister,
    )
    return RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        quality_gate=RalphLoop(
            skills=SUPPRESS_ALL_SKILLS,
            prompts=make_prompt_provider(),
            service=service,
            max_iterations=1,
            plateau_window=2,
            git=git,
            cache=cache,
            retry_max_attempts=3,
            retry_initial_interval=1.0,
            fan_in_max_attempts=2,
            delay_floor_for=no_delay_floor,
        ),
        ticket_generator=TicketGenerationLoop(
            skills=SUPPRESS_ALL_SKILLS,
            prompts=make_prompt_provider(),
            service=service,
            workspace=workspace,
            max_reviews=1,
            review_mode=TicketReviewMode.REVIEWED,
            retry_max_attempts=3,
            retry_initial_interval=1.0,
            delay_floor_for=no_delay_floor,
        ),
        merger=GitBranchMerger(git=git, workspace=workspace, remote="origin"),
        git_base_url="https://github.com",
        git_remote="origin",
        git=git,
        cache=cache,
        artifact_persister=None,
        retry_max_attempts=3,
        retry_initial_interval=1.0,
        remediation_max_rounds=1,
        criteria_max_regeneration_rounds=1,
        fan_in_max_attempts=2,
        delay_floor_for=no_delay_floor,
    )


async def _run(
    engine: RalphWorkflowEngine,
    repo: Path,
    base_spec: BaseSpec,
) -> list[AgentEvent]:
    return [
        event
        async for event in engine.run(
            prompt="do the lane's own work",
            repo_path=str(repo),
            repo_url=None,
            base_spec=base_spec,
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]


async def _file_on(bare: Path, branch: str, path: str) -> str:
    return await _git_output(["git", "show", f"{branch}:{path}"], cwd=bare)


async def _changed_against(bare: Path, base: str, head: str) -> list[str]:
    listing = await _git_output(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        cwd=bare,
    )
    return [line for line in listing.splitlines() if line]


# ---------------------------------------------------------------------------
# KOD-53/AC-21 — a 3-level stack, run end to end
# ---------------------------------------------------------------------------


async def test_a_three_level_stack_keeps_the_inherited_lines(
    trunk_repo: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """trunk → A → B, fired on B: A's lines are still on B's branch after.

    The run's own commits land on a feature branch cut from the recorded
    base, so everything A landed is present in the result. A run graded
    against trunk is what removes them, and that is the case the sibling
    test below pins.
    """
    repo, bare = trunk_repo
    a_sha = await _branch_from(
        repo,
        name=BLOCKER_A_BRANCH,
        start="main",
        file_name=A_FILE,
        body=A_LINES,
    )
    b_sha = await _branch_from(
        repo,
        name=BLOCKER_B_BRANCH,
        start=BLOCKER_A_BRANCH,
        file_name=B_FILE,
        body=B_LINES,
    )
    recorded = BaseSpec(
        base_branch=BLOCKER_B_BRANCH,
        base_role=WorkRefRole.DELIVERABLE,
        inputs=(
            BaseInput(
                blocker_issue_id="KOD-B",
                branch=BLOCKER_B_BRANCH,
                sha=b_sha,
            ),
        ),
    )

    events = await _run(_engine(repo, tmp_path), repo, recorded)

    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.merged is True
    feature = complete.feature_branch

    # Both ancestors' work is present on the branch the run produced.
    assert await _file_on(bare, feature, A_FILE) == A_LINES.strip()
    assert await _file_on(bare, feature, B_FILE) == B_LINES.strip()

    # And neither ancestor's file reads as this lane's own change, while
    # the lane's OWN change does — without which the assertion above is
    # satisfied by a run that changed nothing at all.
    changed = await _changed_against(bare, BLOCKER_B_BRANCH, feature)
    assert LANE_OWN_FILE in changed
    assert A_FILE not in changed
    assert B_FILE not in changed
    assert a_sha != b_sha


async def test_grading_the_same_stack_against_trunk_convicts_the_inheritance(
    trunk_repo: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """The negative control: trunk as the base flags both inherited files.

    Without this, the test above proves only that nothing happened. This
    is the reading the defect produced — and the reason it deleted work.
    """
    repo, bare = trunk_repo
    await _branch_from(
        repo,
        name=BLOCKER_A_BRANCH,
        start="main",
        file_name=A_FILE,
        body=A_LINES,
    )
    b_sha = await _branch_from(
        repo,
        name=BLOCKER_B_BRANCH,
        start=BLOCKER_A_BRANCH,
        file_name=B_FILE,
        body=B_LINES,
    )
    recorded = BaseSpec(
        base_branch=BLOCKER_B_BRANCH,
        base_role=WorkRefRole.DELIVERABLE,
        inputs=(
            BaseInput(
                blocker_issue_id="KOD-B",
                branch=BLOCKER_B_BRANCH,
                sha=b_sha,
            ),
        ),
    )
    events = await _run(_engine(repo, tmp_path), repo, recorded)
    feature = next(
        e for e in events if isinstance(e, WorkflowCompleteEvent)
    ).feature_branch

    against_recorded = await _changed_against(bare, BLOCKER_B_BRANCH, feature)
    against_trunk = await _changed_against(bare, "main", feature)

    assert LANE_OWN_FILE in against_recorded
    assert A_FILE not in against_recorded
    assert B_FILE not in against_recorded
    assert A_FILE in against_trunk
    assert B_FILE in against_trunk


# ---------------------------------------------------------------------------
# KOD-53/AC-25 — the combined base: every input's files survive, none is flagged
# ---------------------------------------------------------------------------


async def test_a_combined_base_keeps_every_input_intact(
    trunk_repo: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """Two divergent blockers, one constructed base — the acute case.

    A check run against trunk reverts the most here, because the lane
    inherits from BOTH inputs.
    """
    repo, bare = trunk_repo
    a_sha = await _branch_from(
        repo,
        name=BLOCKER_A_BRANCH,
        start="main",
        file_name=A_FILE,
        body=A_LINES,
    )
    b_sha = await _branch_from(
        repo,
        name=BLOCKER_B_BRANCH,
        start="main",
        file_name=B_FILE,
        body=B_LINES,
    )
    # The base the lane was handed: A and B combined into one ref.
    await _git(
        ["git", "checkout", "-b", INTEGRATION_BRANCH, BLOCKER_A_BRANCH], cwd=repo
    )
    await _git(["git", "merge", "--no-edit", BLOCKER_B_BRANCH], cwd=repo)
    await _git(["git", "push", "origin", INTEGRATION_BRANCH], cwd=repo)
    await _git(["git", "checkout", "main"], cwd=repo)

    recorded = BaseSpec(
        base_branch=INTEGRATION_BRANCH,
        base_role=WorkRefRole.INTEGRATION,
        inputs=(
            BaseInput(blocker_issue_id="KOD-A", branch=BLOCKER_A_BRANCH, sha=a_sha),
            BaseInput(blocker_issue_id="KOD-B", branch=BLOCKER_B_BRANCH, sha=b_sha),
        ),
    )

    events = await _run(_engine(repo, tmp_path), repo, recorded)
    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    feature = complete.feature_branch

    # Present AND unmodified — from either input, not merely one.
    assert await _file_on(bare, feature, A_FILE) == A_LINES.strip()
    assert await _file_on(bare, feature, B_FILE) == B_LINES.strip()

    changed = await _changed_against(bare, INTEGRATION_BRANCH, feature)
    assert LANE_OWN_FILE in changed
    assert A_FILE not in changed
    assert B_FILE not in changed

    against_trunk = await _changed_against(bare, "main", feature)
    assert A_FILE in against_trunk
    assert B_FILE in against_trunk


# ---------------------------------------------------------------------------
# KOD-53/AC-24 — the base used is emitted; and KOD-53/AC-22's regression guard
# ---------------------------------------------------------------------------


async def test_the_emitted_event_names_the_ref_that_was_compared(
    trunk_repo: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, _bare = trunk_repo
    b_sha = await _branch_from(
        repo,
        name=BLOCKER_A_BRANCH,
        start="main",
        file_name=A_FILE,
        body=A_LINES,
    )
    recorded = BaseSpec(
        base_branch=BLOCKER_A_BRANCH,
        base_role=WorkRefRole.DELIVERABLE,
        inputs=(
            BaseInput(blocker_issue_id="KOD-A", branch=BLOCKER_A_BRANCH, sha=b_sha),
        ),
    )

    events = await _run(_engine(repo, tmp_path), repo, recorded)

    scope_events = [e for e in events if isinstance(e, WorkflowScopeBaseEvent)]
    assert len(scope_events) == 1
    emitted = scope_events[0]
    assert emitted.base_branch == BLOCKER_A_BRANCH
    assert emitted.base_role is WorkRefRole.DELIVERABLE
    assert [item.blocker_issue_id for item in emitted.inputs] == ["KOD-A"]

    payload = emitted.model_dump(by_alias=True, mode="json")
    assert payload["baseBranch"] == BLOCKER_A_BRANCH
    assert payload["inputs"][0]["blockerIssueId"] == "KOD-A"


async def test_a_trunk_fired_ticket_still_computes_against_trunk(
    trunk_repo: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """KOD-53/AC-22: no regression for a lane with no blockers."""
    repo, _bare = trunk_repo

    events = await _run(_engine(repo, tmp_path), repo, trunk_base("main"))

    emitted = next(e for e in events if isinstance(e, WorkflowScopeBaseEvent))
    assert emitted.base_branch == "main"
    assert emitted.base_role is None
    assert emitted.inputs == []
    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.merged is True
