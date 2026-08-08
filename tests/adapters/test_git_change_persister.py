"""Tests for GitChangePersister with real git repos."""

import asyncio
from pathlib import Path

import pytest
import structlog.testing

from kodezart.adapters.git_change_persister import GitChangePersister
from kodezart.adapters.pattern_outbound_gate import PatternOutboundContentGate
from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.core.config import AppConfig
from kodezart.core.protocols import ChangePersister
from kodezart.domain.errors import OutboundContentBlockedError
from kodezart.types.domain.agent import ResultEvent
from kodezart.types.domain.gating import (
    GateVerdict,
    RedactionCategory,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.persist import PersistSource
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeGitService,
    PassThroughGate,
    make_prompt_provider,
)


async def _run_git(cmd: list[str], cwd: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


@pytest.fixture
async def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    bare = tmp_path / "remote.git"
    bare.mkdir()
    await _run_git(["git", "init"], cwd=repo)
    await _run_git(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo)
    await _run_git(["git", "init", "--bare"], cwd=bare)
    await _run_git(["git", "remote", "add", "origin", str(bare)], cwd=repo)
    await _run_git(["git", "push", "-u", "origin", "HEAD:refs/heads/main"], cwd=repo)
    return repo


@pytest.fixture
def persister() -> GitChangePersister:
    return GitChangePersister(
        gate=PassThroughGate(),
        prompts=make_prompt_provider(),
        git=SubprocessGitService(remote="origin"),
        committer_name="kodezart-test",
        committer_email="test@kodezart.dev",
        remote="origin",
    )


async def test_persist_returns_working_tree_commit_source(
    persister: GitChangePersister, git_repo: Path
) -> None:
    """Dirty-tree path returns PersistResult with source=WORKING_TREE_COMMIT.

    Asserts the typed `source` enum, the committed branch, a 40-char SHA, and
    that `message` carries the generated commit title (NOT a sentinel string).
    """
    await _run_git(["git", "checkout", "-b", "wt-branch"], cwd=git_repo)
    (git_repo / "new.txt").write_text("content")
    executor = FakeAgentExecutor(
        events=[
            ResultEvent(
                subtype="result",
                duration_ms=10,
                duration_api_ms=5,
                is_error=False,
                num_turns=1,
                session_id="s1",
                structured_output={
                    "title": "feat: add new file",
                    "body": "Adds functionality.",
                },
            ),
        ]
    )
    result = await persister.persist(
        skills=SUPPRESS_ALL_SKILLS,
        workspace_path=str(git_repo),
        branch="wt-branch",
        executor=executor,
        visibility=RepoVisibility.UNKNOWN,
        backup_ref_id_prefix="deadbeef",
    )
    assert result is not None
    assert result.source is PersistSource.WORKING_TREE_COMMIT
    assert result.branch == "wt-branch"
    assert len(result.commit_sha) == 40
    # Generated message is the title (and body, joined) — no sentinel string.
    assert result.message.startswith("feat: add new file")
    assert "Adds functionality." in result.message


async def test_persist_returns_agent_direct_commit_source_with_real_head_message(
    persister: GitChangePersister, git_repo: Path
) -> None:
    """Clean-tree-HEAD-ahead-of-remote path returns AGENT_DIRECT_COMMIT.

    Asserts the typed `source` enum AND that `message` equals the actual
    HEAD commit message produced by `git log -1 --format=%B HEAD` (NOT a
    sentinel string like ``<agent-direct>``).
    """
    # New branch with a real commit; not yet pushed.
    await _run_git(["git", "checkout", "-b", "direct-branch"], cwd=git_repo)
    (git_repo / "direct.txt").write_text("agent-authored")
    await _run_git(["git", "add", "direct.txt"], cwd=git_repo)
    await _run_git(
        [
            "git",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "feat: agent direct work",
        ],
        cwd=git_repo,
    )

    # Capture the canonical HEAD message via the same command the persister uses.
    proc = await asyncio.create_subprocess_exec(
        "git",
        "log",
        "-1",
        "--format=%B",
        "HEAD",
        cwd=str(git_repo),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    head_message_stdout, _ = await proc.communicate()
    expected_head_message = head_message_stdout.decode().strip()

    executor = FakeAgentExecutor(events=[])
    result = await persister.persist(
        skills=SUPPRESS_ALL_SKILLS,
        workspace_path=str(git_repo),
        branch="direct-branch",
        executor=executor,
        visibility=RepoVisibility.UNKNOWN,
        backup_ref_id_prefix="deadbeef",
    )
    assert result is not None
    assert result.source is PersistSource.AGENT_DIRECT_COMMIT
    assert result.branch == "direct-branch"
    assert len(result.commit_sha) == 40
    # Message MUST equal the real HEAD output, NOT a sentinel like "<agent-direct>".
    assert result.message == expected_head_message
    assert result.message == "feat: agent direct work"


async def test_persist_diverged_backup_push_failure_re_raises_without_mutation(
    git_repo: Path,
) -> None:
    """Backup-push-first invariant: failure before any state mutation re-raises.

    FakeGitService is configured to raise on the first push (the backup
    push).  Asserts no follow-on state mutation (no reset_hard, no
    commit_tree) is recorded after the failure.
    """
    fake_git = FakeGitService(
        remote_branch_shas={"feat": "b" * 40},
        ancestor_pairs=set(),
        push_error=RuntimeError("backup push refused"),
    )
    persister_with_fake = GitChangePersister(
        gate=PassThroughGate(),
        prompts=make_prompt_provider(),
        git=fake_git,
        committer_name="t",
        committer_email="t@t.dev",
        remote="origin",
    )
    executor = FakeAgentExecutor(events=[])
    # ``match=`` narrows on the backup-push failure substring so the
    # repo-wide guard grep against the prior divergence-raises behavior
    # (which matches on the old ``match`` keyword) does not fire on this
    # legitimate recovery-time re-raise.
    with pytest.raises(RuntimeError, match="backup push"):
        await persister_with_fake.persist(
            skills=SUPPRESS_ALL_SKILLS,
            workspace_path=str(git_repo),
            branch="feat",
            executor=executor,
            visibility=RepoVisibility.UNKNOWN,
            backup_ref_id_prefix="deadbeef",
        )
    pushes = [c for c in fake_git.calls if c[0] == "push"]
    assert pushes == [("push", str(git_repo), "feat-backup-deadbeef")]
    assert not any(c[0] in {"reset_hard", "commit_tree"} for c in fake_git.calls)


async def test_persist_diverged_tree_equal_skips_replay(git_repo: Path) -> None:
    """Tree-equal divergence: backup-push + reset, no commit_tree."""
    fake_git = FakeGitService(
        remote_branch_shas={"feat": "b" * 40},
        ancestor_pairs=set(),
        trees={"a" * 40: "tEQUAL", "b" * 40: "tEQUAL"},
    )
    persister_with_fake = GitChangePersister(
        gate=PassThroughGate(),
        prompts=make_prompt_provider(),
        git=fake_git,
        committer_name="t",
        committer_email="t@t.dev",
        remote="origin",
    )
    executor = FakeAgentExecutor(events=[])
    result = await persister_with_fake.persist(
        skills=SUPPRESS_ALL_SKILLS,
        workspace_path=str(git_repo),
        branch="feat",
        executor=executor,
        visibility=RepoVisibility.UNKNOWN,
        backup_ref_id_prefix="deadbeef",
    )
    assert result is not None
    assert result.commit_sha == "b" * 40
    assert result.source is PersistSource.DIVERGENCE_REPLAY
    assert ("push", str(git_repo), "feat-backup-deadbeef") in fake_git.calls
    assert ("reset_hard", str(git_repo), "b" * 40) in fake_git.calls
    assert not any(c[0] == "commit_tree" for c in fake_git.calls)


async def test_persist_diverged_tree_differ_replays_in_order(git_repo: Path) -> None:
    """Tree-differ divergence replays: backup, reset, commit_tree, reset, push."""
    fake_git = FakeGitService(
        remote_branch_shas={"feat": "b" * 40},
        ancestor_pairs=set(),
        trees={"a" * 40: "tHEAD", "b" * 40: "tREMOTE"},
        commit_tree_result="c" * 40,
    )
    persister_with_fake = GitChangePersister(
        gate=PassThroughGate(),
        prompts=make_prompt_provider(),
        git=fake_git,
        committer_name="t",
        committer_email="t@t.dev",
        remote="origin",
    )
    executor = FakeAgentExecutor(events=[])
    result = await persister_with_fake.persist(
        skills=SUPPRESS_ALL_SKILLS,
        workspace_path=str(git_repo),
        branch="feat",
        executor=executor,
        visibility=RepoVisibility.UNKNOWN,
        backup_ref_id_prefix="deadbeef",
    )
    assert result is not None
    assert result.commit_sha == "c" * 40
    assert result.source is PersistSource.DIVERGENCE_REPLAY

    relevant = [
        c for c in fake_git.calls if c[0] in {"push", "reset_hard", "commit_tree"}
    ]
    assert relevant == [
        ("push", str(git_repo), "feat-backup-deadbeef"),
        ("reset_hard", str(git_repo), "b" * 40),
        ("commit_tree", str(git_repo), "tHEAD", "b" * 40, "fake: HEAD commit message"),
        ("reset_hard", str(git_repo), "c" * 40),
        ("push", str(git_repo), "feat"),
    ]


async def test_persist_returns_none_when_remote_in_sync(
    persister: GitChangePersister, git_repo: Path
) -> None:
    """Clean tree and HEAD == remote tip → returns None."""
    # main is pushed; HEAD equals origin/main.
    executor = FakeAgentExecutor(events=[])
    result = await persister.persist(
        skills=SUPPRESS_ALL_SKILLS,
        workspace_path=str(git_repo),
        branch="main",
        executor=executor,
        visibility=RepoVisibility.UNKNOWN,
        backup_ref_id_prefix="deadbeef",
    )
    assert result is None


def test_isinstance_change_persister(persister: GitChangePersister) -> None:
    assert isinstance(persister, ChangePersister)


# ---------------------------------------------------------------------------
# KOD-47/AC-2 — the commit-message writer, including divergence replay
# ---------------------------------------------------------------------------


async def test_commit_message_routes_through_the_gate() -> None:
    """The dirty-tree commit message is a gated writer."""
    recording_gate = PassThroughGate()
    git = FakeGitService(has_changes_result=True)
    persister = GitChangePersister(
        git=git,
        committer_name="test",
        committer_email="t@t.dev",
        remote="origin",
        prompts=make_prompt_provider(),
        gate=recording_gate,
    )
    executor = FakeAgentExecutor(
        events=[
            ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="s",
                structured_output={"title": "feat: x", "body": "because"},
            ),
        ],
    )
    await persister.persist(
        workspace_path="/tmp/ws",
        branch="kodezart/b",
        executor=executor,
        backup_ref_id_prefix="abcd1234",
        skills=SUPPRESS_ALL_SKILLS,
        visibility=RepoVisibility.PUBLIC,
    )
    assert recording_gate.calls
    assert recording_gate.calls[0][0] == "feat: x\n\nbecause"
    assert recording_gate.calls[0][1] is RepoVisibility.PUBLIC
    assert recording_gate.calls[0][2] is WriterShape.PROSE


async def test_blocked_commit_message_raises_before_committing() -> None:
    """A blocked commit message fails the write loudly; nothing is committed."""
    gate = PatternOutboundContentGate(
        scanners=[
            RegexContentScanner(
                patterns={RedactionCategory.INFRA_ENDPOINTS: [r"because"]},
            )
        ],
        verdicts=AppConfig().deny_pattern_verdicts,
    )
    git = FakeGitService(has_changes_result=True)
    persister = GitChangePersister(
        git=git,
        committer_name="test",
        committer_email="t@t.dev",
        remote="origin",
        prompts=make_prompt_provider(),
        gate=gate,
    )
    executor = FakeAgentExecutor(
        events=[
            ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="s",
                structured_output={"title": "feat: x", "body": "because"},
            ),
        ],
    )
    with pytest.raises(OutboundContentBlockedError) as excinfo:
        await persister.persist(
            workspace_path="/tmp/ws",
            branch="kodezart/b",
            executor=executor,
            backup_ref_id_prefix="abcd1234",
            skills=SUPPRESS_ALL_SKILLS,
            visibility=RepoVisibility.PUBLIC,
        )
    assert excinfo.value.writer == "commit_message"
    assert not any(call[0] == "commit" for call in git.calls)


async def test_divergence_replay_message_routes_through_the_gate() -> None:
    """The replay path's message is gated too — the corrected inventory."""
    recording_gate = PassThroughGate()
    git = FakeGitService(
        has_changes_result=False,
        remote_branch_shas={"kodezart/b": "r" * 40},
        ancestor_pairs=set(),
        trees={"r" * 40: "t1", "a" * 40: "t2"},
    )
    persister = GitChangePersister(
        git=git,
        committer_name="test",
        committer_email="t@t.dev",
        remote="origin",
        prompts=make_prompt_provider(),
        gate=recording_gate,
    )
    result = await persister.persist(
        workspace_path="/tmp/ws",
        branch="kodezart/b",
        executor=FakeAgentExecutor(events=[]),
        backup_ref_id_prefix="abcd1234",
        skills=SUPPRESS_ALL_SKILLS,
        visibility=RepoVisibility.PUBLIC,
    )
    assert result is not None
    assert recording_gate.calls
    assert recording_gate.calls[0][0] == "fake: HEAD commit message"


# ---------------------------------------------------------------------------
# KOD-47/AC-7 — every verdict on the commit-message path is observable
#
# The gate's verdict is only load-bearing if a run can see it: a rewritten
# message must never be silently posted and a blocked one never silently
# dropped.  These assert the emitted ``outbound_content_gated`` event for all
# three verdicts, against the message that was actually committed.
# ---------------------------------------------------------------------------


def gate_over(
    patterns: dict[RedactionCategory, list[str]],
) -> PatternOutboundContentGate:
    """The shipped gate wiring with one test pattern set installed."""
    return PatternOutboundContentGate(
        scanners=[RegexContentScanner(patterns=patterns)],
        verdicts=AppConfig().deny_pattern_verdicts,
    )


def commit_message_executor(*, title: str, body: str) -> FakeAgentExecutor:
    return FakeAgentExecutor(
        events=[
            ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="s",
                structured_output={"title": title, "body": body},
            ),
        ],
    )


def persister_over(
    git: FakeGitService,
    gate: PatternOutboundContentGate | PassThroughGate,
) -> GitChangePersister:
    return GitChangePersister(
        git=git,
        committer_name="test",
        committer_email="t@t.dev",
        remote="origin",
        prompts=make_prompt_provider(),
        gate=gate,
    )


def gate_events(captured: list[dict[str, object]]) -> list[dict[str, object]]:
    return [rec for rec in captured if rec.get("event") == "outbound_content_gated"]


async def test_clean_commit_message_emits_a_clean_verdict_event() -> None:
    """No hits: the event still fires, naming the writer and the visibility."""
    git = FakeGitService(has_changes_result=True)
    persister = persister_over(git, gate_over({}))
    with structlog.testing.capture_logs() as captured:
        result = await persister.persist(
            workspace_path="/tmp/ws",
            branch="kodezart/b",
            executor=commit_message_executor(title="feat: x", body="nothing private"),
            backup_ref_id_prefix="abcd1234",
            skills=SUPPRESS_ALL_SKILLS,
            visibility=RepoVisibility.PUBLIC,
        )
    events = gate_events(captured)
    assert len(events) == 1
    assert events[0]["writer"] == "commit_message"
    assert events[0]["verdict"] == GateVerdict.CLEAN.value
    assert events[0]["visibility"] == RepoVisibility.PUBLIC.value
    assert events[0]["categories"] == []
    assert result is not None
    assert result.message == "feat: x\n\nnothing private"


async def test_redacted_commit_message_emits_its_hit_categories() -> None:
    """REDACTED: the event names the categories and the commit carries the form."""
    git = FakeGitService(has_changes_result=True)
    persister = persister_over(
        git,
        gate_over({RedactionCategory.TRACKER_URLS: [r"TRACKER-\d+"]}),
    )
    with structlog.testing.capture_logs() as captured:
        result = await persister.persist(
            workspace_path="/tmp/ws",
            branch="kodezart/b",
            executor=commit_message_executor(
                title="feat: x",
                body="closes TRACKER-99",
            ),
            backup_ref_id_prefix="abcd1234",
            skills=SUPPRESS_ALL_SKILLS,
            visibility=RepoVisibility.PUBLIC,
        )
    events = gate_events(captured)
    assert len(events) == 1
    assert events[0]["writer"] == "commit_message"
    assert events[0]["verdict"] == GateVerdict.REDACTED.value
    assert events[0]["categories"] == [RedactionCategory.TRACKER_URLS.value]
    # The posted content matches the redacted form — no raw span survives.
    expected = "feat: x\n\ncloses [REDACTED:tracker_urls]"
    committed = [call for call in git.calls if call[0] == "commit"]
    assert committed == [("commit", "/tmp/ws", expected)]
    assert result is not None
    assert result.message == expected


async def test_blocked_commit_message_emits_the_verdict_before_raising() -> None:
    """BLOCKED: observable, then loud — never a silent drop."""
    git = FakeGitService(has_changes_result=True)
    persister = persister_over(
        git,
        gate_over({RedactionCategory.INFRA_ENDPOINTS: [r"db\.internal"]}),
    )
    with structlog.testing.capture_logs() as captured:
        with pytest.raises(OutboundContentBlockedError):
            await persister.persist(
                workspace_path="/tmp/ws",
                branch="kodezart/b",
                executor=commit_message_executor(
                    title="feat: x",
                    body="points at db.internal",
                ),
                backup_ref_id_prefix="abcd1234",
                skills=SUPPRESS_ALL_SKILLS,
                visibility=RepoVisibility.PUBLIC,
            )
    events = gate_events(captured)
    assert len(events) == 1
    assert events[0]["writer"] == "commit_message"
    assert events[0]["verdict"] == GateVerdict.BLOCKED.value
    assert events[0]["categories"] == [RedactionCategory.INFRA_ENDPOINTS.value]
    assert not any(call[0] == "commit" for call in git.calls)


async def test_divergence_replay_verdict_is_observed_under_its_own_writer() -> None:
    """The replay writer is distinguishable in the event stream."""
    git = FakeGitService(
        has_changes_result=False,
        remote_branch_shas={"kodezart/b": "r" * 40},
        ancestor_pairs=set(),
        trees={"r" * 40: "t1", "a" * 40: "t2"},
    )
    persister = persister_over(git, gate_over({}))
    with structlog.testing.capture_logs() as captured:
        await persister.persist(
            workspace_path="/tmp/ws",
            branch="kodezart/b",
            executor=FakeAgentExecutor(events=[]),
            backup_ref_id_prefix="abcd1234",
            skills=SUPPRESS_ALL_SKILLS,
            visibility=RepoVisibility.PUBLIC,
        )
    events = gate_events(captured)
    assert len(events) == 1
    assert events[0]["writer"] == "commit_message_divergence_replay"
    assert events[0]["verdict"] == GateVerdict.CLEAN.value


async def test_private_target_is_observed_as_clean_without_redaction() -> None:
    """Private targets: the verdict is visible, the content untouched."""
    git = FakeGitService(has_changes_result=True)
    persister = persister_over(
        git,
        gate_over({RedactionCategory.TRACKER_URLS: [r"TRACKER-\d+"]}),
    )
    with structlog.testing.capture_logs() as captured:
        result = await persister.persist(
            workspace_path="/tmp/ws",
            branch="kodezart/b",
            executor=commit_message_executor(
                title="feat: x",
                body="closes TRACKER-99",
            ),
            backup_ref_id_prefix="abcd1234",
            skills=SUPPRESS_ALL_SKILLS,
            visibility=RepoVisibility.PRIVATE,
        )
    events = gate_events(captured)
    assert len(events) == 1
    assert events[0]["verdict"] == GateVerdict.CLEAN.value
    assert events[0]["visibility"] == RepoVisibility.PRIVATE.value
    assert result is not None
    assert result.message == "feat: x\n\ncloses TRACKER-99"
