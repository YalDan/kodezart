"""Actual local workspace, service and production SDK adapter reach one fresh judge."""

import subprocess
from pathlib import Path

from claude_agent_sdk import ResultMessage

from kodezart.adapters.claude.client_executor import ClaudeClientExecutor
from kodezart.adapters.git.bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.git.service import SubprocessGitService
from kodezart.adapters.git.worktree_provider import GitWorktreeProvider
from kodezart.chains.write_back_verifier import FreshWriteBackJudge
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.audit import AuditVerdict, TrackerArtifact
from kodezart.types.domain.session import SessionType
from tests.adapters.test_git_worktree_provider import git_repo as git_repo
from tests.chains.test_write_back_verifier import SURFACE
from tests.fakes import DEFAULT_SETTING_SOURCES, NO_KNOWLEDGE_GRANT, SUPPRESS_ALL_SKILLS
from tests.prompts.test_prompt_wiring import load_registry


def command(repository, *arguments):
    return subprocess.run(
        ["git", *arguments], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()


async def test_real_local_source_and_sdk_policy_are_owned_fresh_and_released(
    git_repo, tmp_path, monkeypatch
):
    (git_repo / "criterion.txt").write_text("native proof\n")
    command(git_repo, "add", "criterion.txt")
    command(git_repo, "commit", "-m", "actual local evidence")
    head = command(git_repo, "rev-parse", "HEAD")
    seen = []

    class ExternalClient:
        def __init__(self, *, options):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def query(self, prompt):
            seen.append(self.options)
            working = Path(self.options.cwd)
            assert working != git_repo and working.is_dir()
            assert command(working, "rev-parse", "HEAD") == head
            assert command(working, "status", "--porcelain") == ""
            branch = subprocess.run(
                ["git", "symbolic-ref", "-q", "HEAD"], cwd=working, capture_output=True
            )
            assert branch.returncode == 1
            assert self.options.resume is None
            assert self.options.permission_mode == "plan"
            assert self.options.allowed_tools == ["Read", "Glob", "Grep", "Bash"]
            assert self.options.output_format["schema"]["title"] == "WriteBackFinding"
            assert f"<base_ref>{head}</base_ref>" in prompt
            assert "criterion.txt" in prompt

        async def receive_response(self):
            present = (
                Path(self.options.cwd) / "criterion.txt"
            ).read_text() == "native proof\n"
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="external-session",
                structured_output={
                    "verdict": "holds" if present else "refuted",
                    "evidence": "Read the actual pinned criterion.txt file.",
                    "cited_refs": ["criterion.txt"],
                },
            )

    monkeypatch.setattr(
        "kodezart.adapters.claude.client_executor.ClaudeSDKClient", ExternalClient
    )
    git = SubprocessGitService(remote="origin")
    cache_path = tmp_path / "must-not-create-a-remote-cache"
    cache = LocalBareRepoCache(git=git, base_dir=str(cache_path))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
    )
    executor = ClaudeClientExecutor(
        setting_sources=DEFAULT_SETTING_SOURCES, knowledge_grant=NO_KNOWLEDGE_GRANT
    )
    judge = FreshWriteBackJudge(
        runner=AgentService(
            executor=executor,
            workspace=workspace,
            git_base_url="https://unused.invalid",
        ),
        workspace=workspace,
        git=git,
        prompts=load_registry(),
        skills=SUPPRESS_ALL_SKILLS,
        repo_path=str(git_repo),
        session_type=SessionType.SCHEDULED_PASS,
    )
    artifact = TrackerArtifact(
        surface=SURFACE,
        native_ref="actual-comment",
        content="criterion.txt contains native proof.",
    )
    for count in (1, 2):
        finding = await judge.judge(artifact=artifact, ref=head)
        assert finding.verdict is AuditVerdict.HOLDS
        assert len(seen) == count
        assert not workspace._workspaces
        assert all(not Path(options.cwd).exists() for options in seen)
    assert len({options.cwd for options in seen}) == 2
    assert not cache_path.exists()
    assert command(git_repo, "rev-parse", "HEAD") == head
    assert command(git_repo, "status", "--porcelain") == ""
