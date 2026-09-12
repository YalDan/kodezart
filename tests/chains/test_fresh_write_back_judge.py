"""The registered write-back role judges freshly at the actual ref."""

import pytest
from pydantic import ValidationError

from kodezart.chains.write_back_verifier import FreshWriteBackJudge
from kodezart.domain.errors import WriteBackReadError
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.audit import AuditVerdict, TrackerArtifact
from kodezart.types.domain.session import SessionType, ToolPreset
from tests.chains.test_organize import RecordingExecutor, RecordingWorkspace, result
from tests.chains.test_write_back_verifier import SURFACE
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeGitService
from tests.prompts.test_prompt_wiring import load_registry

HEAD = "a" * 40
ARTIFACT = TrackerArtifact(
    surface=SURFACE,
    native_ref="comment-native-id",
    content="tests/real.py passed at abc123",
)


def build(payload, *, git=None, prompt_set="claude-opus"):
    executor = RecordingExecutor([result(structured_output=payload)])
    workspace = RecordingWorkspace()
    judge = FreshWriteBackJudge(
        runner=AgentService(
            executor=executor,
            workspace=workspace,
            git_base_url="https://example.invalid",
        ),
        workspace=workspace,
        git=git or FakeGitService(),
        prompts=load_registry(default_set=prompt_set),
        skills=SUPPRESS_ALL_SKILLS,
        repo_url="https://example.invalid/repo",
        session_type=SessionType.ORGANIZE_PASS,
    )
    return judge, executor, workspace


@pytest.mark.parametrize("prompt_set", ["claude-opus", "anthropic_v5"])
async def test_actual_role_preserves_artifact_ref_and_fresh_session_boundaries(
    prompt_set,
):
    judge, executor, workspace = build(
        {
            "verdict": "holds",
            "evidence": "Read actual tests/real.py at abc123",
            "cited_refs": ["tests/real.py"],
        },
        prompt_set=prompt_set,
    )
    for _ in range(2):
        finding = await judge.judge(artifact=ARTIFACT, ref=HEAD)
        assert finding.verdict is AuditVerdict.HOLDS
    assert len(executor.calls) == 2
    for call in executor.calls:
        assert call["session_id"] is None
        assert call["allowed_tools"] is ToolPreset.EVALUATION
        assert ARTIFACT.content in call["prompt"]
        assert f"<base_ref>{HEAD}</base_ref>" in call["prompt"]
        assert "citedRefs" in str(call["output_format"])
    assert all(row["ref"] == HEAD for row in workspace.arguments)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "verdict": "buildable",
            "issue_id": "parent",
            "evidence": "wrong semantic type",
        },
        {
            "verdict": "refuted",
            "evidence": "No actual checked references",
            "cited_refs": [],
        },
    ],
)
async def test_buildability_or_uncited_refutation_is_not_a_writeback_judgment(payload):
    judge, _, _ = build(payload)
    with pytest.raises(ValidationError):
        await judge.judge(artifact=ARTIFACT, ref=HEAD)


@pytest.mark.parametrize(
    "git,ref",
    [
        (FakeGitService(has_changes_result=True), HEAD),
        (FakeGitService(has_replace_refs_result=True), HEAD),
        (FakeGitService(), "different"),
    ],
)
async def test_wrong_dirty_or_replaced_repository_refuses_before_dispatch(git, ref):
    judge, executor, _ = build({"verdict": "holds", "evidence": "unchecked"}, git=git)
    with pytest.raises(WriteBackReadError):
        await judge.judge(artifact=ARTIFACT, ref=ref)
    assert executor.calls == []


@pytest.mark.parametrize("reference", ["", "   "])
async def test_refutation_cannot_fill_the_citation_requirement_with_blank_refs(
    reference,
):
    judge, _, _ = build(
        {
            "verdict": "refuted",
            "evidence": "No source was actually identified.",
            "cited_refs": [reference],
        }
    )
    with pytest.raises(ValidationError):
        await judge.judge(artifact=ARTIFACT, ref=HEAD)
