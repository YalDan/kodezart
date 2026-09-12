"""Actual native runner/persister boundaries with real independent Git worktrees."""

import asyncio
from pathlib import Path

import pytest

from kodezart.adapters.git_change_persister import GitChangePersister
from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.adapters.subprocess_git_source_reader import SubprocessGitSourceReader
from kodezart.chains.criteria import TrackerCriteria
from kodezart.domain.amendment import (
    AmendmentRequiresWriteError,
    NativeWriteRefusalError,
)
from kodezart.domain.errors import FireSpecEntryError
from kodezart.domain.rulings import render_ruling
from kodezart.services.agent_service import AgentService
from kodezart.services.native_amendments import NativeAmendments
from kodezart.types.domain.agent import NativeAmendmentEvent, ResultEvent, Ruling
from kodezart.types.domain.amendment import AmendmentGround, UpheldReason
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig, RepoEntry
from kodezart.types.domain.session import PermissionMode, SessionType, ToolPreset
from tests.chains.test_native_fire import DIRECT_OWED, SUBJECT, tracker
from tests.chains.test_organize import result
from tests.domain.test_rulings import ruling_data
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeRepoCache, PassThroughGate
from tests.prompts.test_prompt_wiring import load_registry

REPO_URL = "https://example.invalid/owner/repo"
QUOTE = "def answer(): return 42"


async def git(cwd, *args):
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await process.communicate()
    assert process.returncode == 0, err.decode()
    return out.decode().strip()


@pytest.fixture
async def repository(tmp_path):
    repo, remote = tmp_path / "repo", tmp_path / "remote.git"
    repo.mkdir()
    await git(repo, "init", "-b", "main")
    await git(repo, "config", "user.name", "Boundary test")
    await git(repo, "config", "user.email", "boundary@example.invalid")
    (repo / "policy.py").write_text(QUOTE + "\n")
    await git(repo, "add", ".")
    await git(repo, "commit", "-m", "base evidence")
    base = await git(repo, "rev-parse", "HEAD")
    (repo / "newer.py").write_text("Only present after the judged base.\n")
    await git(repo, "add", ".")
    await git(repo, "commit", "-m", "newer writer starting point")
    await git(tmp_path, "init", "--bare", str(remote))
    await git(repo, "remote", "add", "origin", str(remote))
    await git(repo, "push", "origin", "main")
    return repo, base


class Executor:
    def __init__(
        self,
        *,
        claim=True,
        reproduced=False,
        direct_commit=False,
        ground=AmendmentGround.UNSATISFIABLE_AT_BASE,
        mutate=None,
        subject=None,
    ):
        self.calls = []
        self.claim = claim
        self.reproduced = reproduced
        self.direct_commit = direct_commit
        self.ground = ground
        self.mutate = mutate
        self.subject = subject or {"kind": "criterion", "id": DIRECT_OWED}

    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        schema = (kwargs.get("output_format") or {}).get("schema", {})
        title = schema.get("title")
        if title == "AmendmentJudgment":
            assert not Path(kwargs["cwd"], "newer.py").exists()
            payload = {
                "subject": self.subject,
                "base_sha": await git(kwargs["cwd"], "rev-parse", "HEAD"),
                "ground": self.ground,
                "reproduced": self.reproduced,
                "finding": {
                    "verdict": "infeasible" if self.reproduced else "feasible",
                    "smallest_repair": "criterion_text" if self.reproduced else "none",
                    "refutation": "A reproduced semantic contradiction."
                    if self.reproduced
                    else None,
                },
                "citations": [{"path": "policy.py", "quote": QUOTE}],
                "measured_by": None,
            }
        elif title == "AcceptanceCriteriaOutput":
            payload = {}
        elif title == "CommitMessageOutput":
            payload = {"title": "fix: implementation", "body": "Reviewed change."}
        else:
            Path(kwargs["cwd"], "change.py").write_text("proposed implementation\n")
            if self.direct_commit:
                await git(kwargs["cwd"], "add", ".")
                await git(kwargs["cwd"], "commit", "-m", "writer bypass")
            payload = {
                "claims": []
                if not self.claim
                else [
                    {
                        "subject": self.subject,
                        "stage": "implementation",
                        "ground": self.ground,
                        "departure": "Use the proposed alternative behavior.",
                        "claimed_capability": None,
                    }
                ]
            }
        if self.mutate is not None:
            await self.mutate(title, payload, kwargs)
        yield result(structured_output=payload)


class Workspaces(GitWorktreeProvider):
    def __init__(self, git_service, cache):
        super().__init__(git_service, cache)
        self.acquired = []
        self.released = []

    async def acquire(self, **kwargs):
        path = await super().acquire(**kwargs)
        self.acquired.append((path, kwargs))
        return path

    async def release(self, path):
        self.released.append(path)
        await super().release(path)


async def build(
    repository, executor, *, prompt_set="claude-opus", port=None, gate=None
):
    repo, base = repository
    git_service = SubprocessGitService(remote="origin")
    workspace = Workspaces(git_service, FakeRepoCache(str(repo)))
    prompts = load_registry(default_set=prompt_set)
    persister = GitChangePersister(
        git_service,
        "Boundary test",
        "boundary@example.invalid",
        remote="origin",
        prompts=prompts,
        gate=gate or PassThroughGate(),
    )
    service = AgentService(
        executor=executor,
        workspace=workspace,
        persister=persister,
        git_base_url="https://example.invalid",
    )
    port = port or tracker()
    criteria = TrackerCriteria(tracker=port)
    spec = await criteria.read_spec(issue_key=SUBJECT)
    owner = NativeAmendments(
        tracker=port,
        operation=OperationConfig(
            operation_name="fixture",
            workspace="fixture",
            marker_prefixes={"ruling": "fixture-pinned"},
        ),
        criteria=criteria,
        git=git_service,
        source=SubprocessGitSourceReader(),
        workspace=workspace,
        runner=service,
        prompts=prompts,
        skills=SUPPRESS_ALL_SKILLS,
        repositories=(RepoEntry(url=REPO_URL, trunk="main"),),
    )
    guard = owner.for_writer(
        spec=spec,
        criteria=await criteria.read_current(spec=spec),
        base_ref=base,
        repo_url=REPO_URL,
    )
    return service, guard, workspace, port


async def drive(service, guard, repository):
    return [
        event
        async for event in service.stream_workflow(
            prompt="Implement the exact current Checks.",
            repo_path=str(repository[0]),
            base_branch="main",
            branch_name="native-test",
            ralph_branch="native-test",
            permission_mode=PermissionMode.UNATTENDED,
            allowed_tools=ToolPreset.IMPLEMENTATION,
            skills=SUPPRESS_ALL_SKILLS,
            session_type=SessionType.TICKET_FIRE,
            visibility=RepoVisibility.PUBLIC,
            native_guard=guard,
        )
    ]


async def cleanup(workspace):
    for path, _ in workspace.acquired:
        if path not in workspace.released:
            await workspace.release(path)


async def test_direct_commit_with_malformed_output_still_retains_workspace(repository):
    async def malformed(title, payload, kwargs):
        if title == "NativeWriterOutput":
            payload.clear()
            payload["unknown"] = "malformed writer result"

    executor = Executor(claim=False, direct_commit=True, mutate=malformed)
    service, guard, workspace, _ = await build(repository, executor)
    try:
        with pytest.raises(NativeWriteRefusalError, match="Writer HEAD changed"):
            await drive(service, guard, repository)
        path = workspace.acquired[0][0]
        assert Path(path).exists() and path not in workspace.released
        assert await git(path, "log", "-1", "--format=%s") == "writer bypass"
        assert (
            await git(repository[0], "ls-remote", "origin", "refs/heads/native-test")
            == ""
        )
    finally:
        await cleanup(workspace)


async def test_writer_exception_after_direct_commit_retains_workspace(repository):
    async def crash(title, payload, kwargs):
        if title == "NativeWriterOutput":
            raise RuntimeError("writer transport failed after its local commit")

    executor = Executor(claim=False, direct_commit=True, mutate=crash)
    service, guard, workspace, _ = await build(repository, executor)
    try:
        with pytest.raises(
            RuntimeError, match="writer transport failed after its local commit"
        ):
            await drive(service, guard, repository)
        path = workspace.acquired[0][0]
        assert Path(path).exists() and path not in workspace.released
        assert await git(path, "log", "-1", "--format=%s") == "writer bypass"
    finally:
        await cleanup(workspace)


@pytest.mark.parametrize("prompt_set", ["claude-opus", "anthropic_v5"])
@pytest.mark.parametrize("ground", list(AmendmentGround))
async def test_quote_exists_but_semantic_ground_is_false_upholds_before_commit(
    repository,
    prompt_set,
    ground,
):
    executor = Executor(ground=ground)
    service, guard, workspace, _ = await build(
        repository, executor, prompt_set=prompt_set
    )
    events = await drive(service, guard, repository)
    report = next(
        event.report for event in events if isinstance(event, NativeAmendmentEvent)
    )
    assert report.upheld[0].reason is UpheldReason.GROUND_NOT_REPRODUCED
    assert not any(isinstance(event, ResultEvent) for event in events)
    assert len(executor.calls) == 2
    writer, judge = executor.calls
    assert "Pinned rulings registry" in writer["prompt"]
    assert "Confirmed empty" in writer["prompt"]
    assert judge["cwd"] != writer["cwd"]
    assert judge["session_id"] is None
    assert judge["agents"] == ()
    assert judge["allowed_tools"] is ToolPreset.EVALUATION
    assert "Author rationale must never be forwarded" not in judge["prompt"]
    assert (
        await git(repository[0], "ls-remote", "origin", "refs/heads/native-test") == ""
    )
    assert len(workspace.released) == 2


async def test_reproduced_ground_is_not_an_applied_amendment(repository):
    service, guard, workspace, _ = await build(repository, Executor(reproduced=True))
    try:
        with pytest.raises(AmendmentRequiresWriteError) as failure:
            await drive(service, guard, repository)
        assert failure.value.judgment.reproduced
        assert "AMENDED" not in failure.value.judgment.model_dump_json()
        assert Path(workspace.acquired[0][0], "change.py").exists()
        assert (
            await git(repository[0], "ls-remote", "origin", "refs/heads/native-test")
            == ""
        )
    finally:
        await cleanup(workspace)


@pytest.mark.parametrize("guarded", [True, False])
async def test_direct_commit_native_refusal_and_authored_compatibility(
    repository,
    guarded,
):
    executor = Executor(claim=False, direct_commit=True)
    service, guard, workspace, _ = await build(repository, executor)
    try:
        if guarded:
            with pytest.raises(NativeWriteRefusalError, match="Writer HEAD changed"):
                await drive(service, guard, repository)
            path = workspace.acquired[0][0]
            assert Path(path).exists() and path not in workspace.released
            assert await git(path, "log", "-1", "--format=%s") == "writer bypass"
            assert (
                await git(
                    repository[0], "ls-remote", "origin", "refs/heads/native-test"
                )
                == ""
            )
            assert len(executor.calls) == 1
        else:
            events = await drive(service, None, repository)
            assert any(
                isinstance(event, ResultEvent) and event.commit_sha for event in events
            )
            assert await git(
                repository[0], "ls-remote", "origin", "refs/heads/native-test"
            )
    finally:
        await cleanup(workspace)


@pytest.mark.parametrize("boundary", ["AmendmentJudgment", "CommitMessageOutput"])
@pytest.mark.parametrize("change", ["check", "outage", "unchanged"])
async def test_current_authority_after_awaited_judge_or_commit_message(
    repository,
    boundary,
    change,
):
    port = tracker()

    async def mutate(title, payload, kwargs):
        if title != boundary or change == "unchanged":
            return
        if change == "check":
            port.issues[DIRECT_OWED] = port.issues[DIRECT_OWED].model_copy(
                update={"body": "**Check:** amended during await\n**Do:** verify"},
            )
        else:

            async def unavailable(**kwargs):
                raise ConnectionError("external tracker outage")

            port.scope_issues = unavailable

    executor = Executor(claim=boundary == "AmendmentJudgment", mutate=mutate)
    service, guard, workspace, _ = await build(repository, executor, port=port)
    try:
        if change == "unchanged":
            events = await drive(service, guard, repository)
            assert any(isinstance(event, NativeAmendmentEvent) for event in events)
        else:
            with pytest.raises((NativeWriteRefusalError, FireSpecEntryError)):
                await drive(service, guard, repository)
            assert (
                await git(
                    repository[0], "ls-remote", "origin", "refs/heads/native-test"
                )
                == ""
            )
            assert (
                await git(repository[0], "log", "native-test", "--format=%s", "-1")
                == "newer writer starting point"
            )
    finally:
        await cleanup(workspace)


@pytest.mark.parametrize("marker_lane", [SUBJECT, "historical-scope-lane"])
async def test_ruling_departure_uses_exact_readback_and_stays_not_actioned(
    repository, marker_lane
):
    port = tracker()
    ruling = Ruling.model_validate(ruling_data(issue_ref=SUBJECT))
    await port.post_comment(
        issue_key=SUBJECT,
        body=render_ruling(
            ruling=ruling,
            lane_key=marker_lane,
            marker_prefixes={"ruling": "fixture-pinned"},
        ),
    )
    executor = Executor(subject={"kind": "ruling", "id": ruling.ruling_id})
    service, guard, _, _ = await build(repository, executor, port=port)
    events = await drive(service, guard, repository)
    report = next(
        event.report for event in events if isinstance(event, NativeAmendmentEvent)
    )
    assert report.upheld[0].subject.kind == "ruling"
    assert report.upheld[0].subject.id == ruling.ruling_id
    assert ruling.resolution in executor.calls[0]["prompt"]
    assert ruling.rejected_alternative in executor.calls[0]["prompt"]
    assert not any(isinstance(event, ResultEvent) for event in events)


async def test_ruling_change_during_commit_content_gate_refuses_before_commit(
    repository,
):
    port = tracker()
    ruling = Ruling.model_validate(ruling_data(issue_ref=SUBJECT))
    await port.post_comment(
        issue_key=SUBJECT,
        body=render_ruling(
            ruling=ruling,
            lane_key=SUBJECT,
            marker_prefixes={"ruling": "fixture-pinned"},
        ),
    )

    class ChangingGate(PassThroughGate):
        async def gate(self, **kwargs):
            decision = await super().gate(**kwargs)
            changed = ruling.model_copy(
                update={"resolution": "A changed current answer"}
            )
            port.comments[0] = port.comments[0].model_copy(
                update={
                    "body": render_ruling(
                        ruling=changed,
                        lane_key=SUBJECT,
                        marker_prefixes={"ruling": "fixture-pinned"},
                    )
                }
            )
            return decision

    service, guard, workspace, _ = await build(
        repository,
        Executor(claim=False),
        port=port,
        gate=ChangingGate(),
    )
    try:
        with pytest.raises(NativeWriteRefusalError, match="Pinned rulings changed"):
            await drive(service, guard, repository)
        assert (
            await git(repository[0], "ls-remote", "origin", "refs/heads/native-test")
            == ""
        )
        assert (
            await git(repository[0], "log", "native-test", "--format=%s", "-1")
            == "newer writer starting point"
        )
    finally:
        await cleanup(workspace)


@pytest.mark.parametrize("fault", ["outage", "ruling", "head", "unchanged"])
async def test_actual_commit_receipt_requires_current_authority_before_publication(
    repository, monkeypatch, fault
):
    port = tracker()
    ruling = Ruling.model_validate(ruling_data(issue_ref=SUBJECT))
    await port.post_comment(
        issue_key=SUBJECT,
        body=render_ruling(
            ruling=ruling,
            lane_key="historical-lane",
            marker_prefixes={"ruling": "fixture-pinned"},
        ),
    )
    executor = Executor(claim=False)
    service, guard, workspace, _ = await build(repository, executor, port=port)
    original_commit = workspace._git.commit
    receipts = []

    async def commit(**kwargs):
        sha = await original_commit(**kwargs)
        receipts.append(sha)
        if fault == "outage":

            async def unavailable(**kwargs):
                raise ConnectionError("tracker outage after local commit")

            port.scope_issues = unavailable
        elif fault == "ruling":
            changed = ruling.model_copy(
                update={"resolution": "Changed before publication"}
            )
            port.comments[0] = port.comments[0].model_copy(
                update={
                    "body": render_ruling(
                        ruling=changed,
                        lane_key="historical-lane",
                        marker_prefixes={"ruling": "fixture-pinned"},
                    ),
                }
            )
        elif fault == "head":
            await git(
                kwargs["cwd"],
                "commit",
                "--allow-empty",
                "-m",
                "unowned post-commit change",
            )
        return sha

    monkeypatch.setattr(workspace._git, "commit", commit)
    try:
        if fault == "unchanged":
            events = await drive(service, guard, repository)
            assert any(
                isinstance(e, ResultEvent) and e.commit_sha == receipts[0]
                for e in events
            )
        else:
            with pytest.raises(NativeWriteRefusalError):
                await drive(service, guard, repository)
            path = workspace.acquired[0][0]
            assert Path(path).exists() and path not in workspace.released
        assert len(receipts) == 1
        remote = await git(
            repository[0], "ls-remote", "origin", "refs/heads/native-test"
        )
        assert (remote.split()[0] if remote else None) == (
            receipts[0] if fault == "unchanged" else None
        )
        assert [
            call["output_format"]["schema"]["title"] for call in executor.calls
        ] == [
            "NativeWriterOutput",
            "CommitMessageOutput",
        ]
    finally:
        await cleanup(workspace)
