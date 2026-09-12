"""Refutations need a source-grounded mandate verdict, never a silent edit."""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from kodezart.chains.audit_pass import AuditMandateHunt
from kodezart.core.constants import EVAL_PERMISSION_MODE
from kodezart.domain.errors import AuditClaimReadError
from kodezart.types.domain.agent import AUDIT_MANDATE_SCHEMA
from kodezart.types.domain.audit import (
    AuditClaimObservation,
    AuditClaimReport,
    AuditMandateJudgment,
    AuditMandateRequest,
    AuditVerdict,
    MandateFinding,
)
from kodezart.types.domain.organize import DefectRole, SpecFinding
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import SessionType, ToolPreset
from kodezart.types.domain.subagents import NO_SUBAGENTS
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeGitService,
    FakeMcpIssue,
    FakeWorkspaceProvider,
)
from tests.prompts.sets import OPUS_SET, V5_SET
from tests.prompts.test_prompt_wiring import load_registry
from tests.tracker.conftest import fixture_server
from tests.tracker.test_audit_claim import Runner, result_event

ISSUE = "mandate/source"
OTHER = "mandate/other"
QUOTE = "Each writer must copy the global gate into its own container."
DEFECT = "duplicate central gate"
HEAD = "a" * 40
SURFACES = tuple(
    WritableSurface(
        kind=SurfaceKind.ISSUE_DESCRIPTION, ref=ScopeRef(kind=ScopeKind.ISSUE, key=key)
    )
    for key in [OTHER, ISSUE]
)
CLAIM = AuditClaimObservation(
    judgment={
        "criterion_key": "claim/check",
        "verdict": "refuted",
        "evidence": "The implementation duplicates the configured central gate.",
    },
    head_sha=HEAD,
    record_ref="record/reference",
    check="Only the central gate governs output.",
)
REQUEST = AuditMandateRequest(
    claim=CLAIM,
    defect_class=DEFECT,
    surfaces=SURFACES,
    repo_url="https://forge.invalid/team/project",
)


def output(**changes):
    return {
        "verdict": "holds",
        "finding": {
            "issue_id": ISSUE,
            "defect_class": DEFECT,
            "evidence": "The source explicitly instructs duplicate gates.",
            "role": "mandate",
            "mandate_text": QUOTE,
        },
        "source_index": 1,
        "evidence": "The instruction explains the observed duplication.",
        **changes,
    }


@pytest.fixture
def server():
    server = fixture_server()
    server.issues[ISSUE] = FakeMcpIssue(
        id=ISSUE, description=f"Context\n{QUOTE}\nOther instructions"
    )
    server.issues[OTHER] = FakeMcpIssue(
        id=OTHER, description="An unrelated instruction."
    )
    return server


@pytest.fixture
async def setup(tracker):
    runner = Runner([result_event(subtype="success", structured_output=output())])
    workspace = FakeWorkspaceProvider()
    git = FakeGitService()

    def build(set_name=V5_SET):
        return AuditMandateHunt(
            tracker=tracker,
            runner=runner,
            workspace=workspace,
            git=git,
            prompts=load_registry(default_set=set_name),
            skills=SUPPRESS_ALL_SKILLS,
        )

    return build, runner, git, workspace


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
async def test_actual_source_instruction_is_quoted_without_writes(
    setup, tracker_writes, set_name
):
    build, runner, _, workspace = setup
    before = tracker_writes()
    report = await build(set_name).complete(REQUEST)
    assert report.claim == CLAIM
    assert report.mandate.verdict is AuditVerdict.HOLDS
    assert type(report.mandate.finding) is MandateFinding
    assert isinstance(report.mandate.finding, SpecFinding)
    assert report.mandate.finding.role is DefectRole.MANDATE
    assert report.mandate.finding.mandate_text == QUOTE
    assert report.mandate.finding_surface == SURFACES[1]
    assert tuple(item.surface for item in report.mandate.covered) == SURFACES
    assert not report.mandate.unreadable
    assert tracker_writes() == before
    args = runner.arguments
    assert QUOTE in args["prompt"] and CLAIM.judgment.evidence in args["prompt"]
    assert args["session_id"] is None
    assert args["session_type"] is SessionType.SCHEDULED_PASS
    assert args["permission_mode"] == EVAL_PERMISSION_MODE
    assert args["allowed_tools"] == ToolPreset.EVALUATION
    assert args["agents"] == NO_SUBAGENTS
    assert args["output_format"]["schema"] == AUDIT_MANDATE_SCHEMA
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


async def test_absence_requires_entire_addressed_set(setup, tracker_writes):
    build, runner, *_ = setup
    runner._events[0] = result_event(
        subtype="success",
        structured_output=output(verdict="refuted", finding=None, source_index=None),
    )
    before = tracker_writes()
    result = await build().complete(REQUEST)
    assert result.mandate.verdict is AuditVerdict.REFUTED
    assert tuple(item.surface for item in result.mandate.covered) == SURFACES
    assert result.mandate.finding is None and not result.mandate.unreadable
    assert tracker_writes() == before


@pytest.mark.parametrize("damage", ["missing", "unsupported"])
async def test_unreadable_surface_names_coverage_failure_before_session(setup, damage):
    build, runner, _, workspace = setup
    ref = ScopeRef(kind=ScopeKind.ISSUE, key="unreadable")
    surface = WritableSurface(
        kind=SurfaceKind.ISSUE_DESCRIPTION
        if damage == "missing"
        else SurfaceKind.ISSUE_LABEL_SET,
        ref=ref,
    )
    request = REQUEST.model_copy(update={"surfaces": (*SURFACES, surface)})
    report = await build().complete(request)
    assert report.mandate.verdict is AuditVerdict.UNVERIFIABLE
    assert report.mandate.unreadable[0].surface == surface
    assert report.mandate.unreadable[0].reason
    assert len(report.mandate.covered) == len(SURFACES)
    assert not runner.calls and not workspace.calls


async def test_session_cannot_invent_unreadability_of_a_covered_source(
    setup, tracker_writes
):
    build, runner, *_ = setup
    runner._events[0] = result_event(
        subtype="success",
        structured_output=output(
            verdict="unverifiable",
            finding=None,
            source_index=0,
            evidence="The instruction references an inaccessible ruling.",
        ),
    )
    before = tracker_writes()
    with pytest.raises(AuditClaimReadError, match="successfully read"):
        await build().complete(REQUEST)
    assert tracker_writes() == before


@pytest.mark.parametrize("damage", ["quote", "identity", "class", "index", "role"])
async def test_session_cannot_invent_or_redirect_mandate(setup, damage):
    build, runner, _, workspace = setup
    data = output()
    if damage == "quote":
        data["finding"]["mandate_text"] = QUOTE.lower()
    elif damage == "identity":
        data["finding"]["issue_id"] = OTHER
    elif damage == "class":
        data["finding"]["defect_class"] = "another defect"
    elif damage == "index":
        data["source_index"] = len(SURFACES)
    else:
        data["finding"]["role"] = "instance"
        data["finding"]["mandate_text"] = None
    runner._events[0] = result_event(subtype="success", structured_output=data)
    with pytest.raises((AuditClaimReadError, ValidationError)):
        await build().complete(REQUEST)
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


@pytest.mark.parametrize("damage", ["source", "head", "dirty"])
async def test_inflight_changes_refuse_complete_report(
    setup, tracker, monkeypatch, damage
):
    build, runner, git, workspace = setup

    async def mutate():
        if damage == "source":
            await tracker.update_issue(issue_key=ISSUE, body="Changed instruction")
        elif damage == "head":
            monkeypatch.setattr(git, "current_sha", AsyncMock(return_value="b" * 40))
        else:
            git.has_changes_result = True

    runner.during = mutate
    with pytest.raises(AuditClaimReadError):
        await build().complete(REQUEST)
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


@pytest.mark.parametrize("verdict", [AuditVerdict.HOLDS, AuditVerdict.UNVERIFIABLE])
async def test_non_refutation_needs_no_hunt_or_mandate(setup, verdict):
    build, runner, git, workspace = setup
    claim = CLAIM.model_copy(
        update={"judgment": CLAIM.judgment.model_copy(update={"verdict": verdict})}
    )
    result = await build().complete(REQUEST.model_copy(update={"claim": claim}))
    assert result.claim == claim and result.mandate is None
    assert not runner.calls and not git.calls and not workspace.calls


def test_refutation_cannot_cross_completeness_boundary_without_mandate():
    with pytest.raises(ValidationError, match="every refutation"):
        AuditClaimReport(claim=CLAIM, mandate=None)


@pytest.mark.parametrize("surfaces", [(), (SURFACES[0], SURFACES[0])])
def test_missing_or_duplicate_set_refuses(surfaces):
    with pytest.raises(ValidationError):
        AuditMandateRequest(**{**REQUEST.model_dump(), "surfaces": surfaces})


@pytest.mark.parametrize(
    "data",
    [
        output(finding=None),
        output(verdict="unverifiable", finding=None, source_index=None),
        output(verdict=True),
    ],
)
def test_mandate_judgment_is_three_state_and_complete(data):
    with pytest.raises(ValidationError):
        AuditMandateJudgment.model_validate(data)


@pytest.mark.parametrize("phase", ["current_sha", "has_changes"])
@pytest.mark.parametrize("read_number", [1, 2])
async def test_native_git_read_cancellation_settles_before_workspace_release(
    setup, monkeypatch, tmp_path, phase, read_number
):
    from tests.git_read_cancellation import assert_git_read_settles_before_release

    build, _, git, workspace = setup
    await assert_git_read_settles_before_release(
        invoke=lambda: build().complete(REQUEST),
        git=git,
        workspace=workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        phase=phase,
        read_number=read_number,
    )


@pytest.mark.parametrize("phase", ["acquire", "release"])
async def test_repeated_cancellation_settles_owned_workspace(setup, monkeypatch, phase):
    import asyncio

    build, runner, _, workspace = setup
    entered = asyncio.Event()
    settled = asyncio.Event()
    released = []

    async def acquire(**kwargs):
        if phase == "acquire":
            entered.set()
            await settled.wait()
        return "/tmp/mandate-workspace"

    async def release(path):
        if phase == "release":
            entered.set()
            await settled.wait()
        released.append(path)

    monkeypatch.setattr(workspace, "acquire", acquire)
    monkeypatch.setattr(workspace, "release", release)
    task = asyncio.create_task(build().complete(REQUEST))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    settled.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert released == ["/tmp/mandate-workspace"]
    if phase == "acquire":
        assert not runner.calls


async def test_actual_agent_service_gets_only_fresh_claim_and_native_sources(
    setup, tracker, monkeypatch
):
    from kodezart.services.agent_service import AgentService
    from tests.fakes import FakeAgentExecutor

    _, runner, git, workspace = setup
    acquire = AsyncMock(wraps=workspace.acquire)
    monkeypatch.setattr(workspace, "acquire", acquire)
    executor = FakeAgentExecutor(runner._events)
    service = AgentService(
        executor=executor, workspace=workspace, git_base_url="https://forge.invalid"
    )
    hunt = AuditMandateHunt(
        tracker=tracker,
        runner=service,
        workspace=workspace,
        git=git,
        prompts=load_registry(default_set=V5_SET),
        skills=SUPPRESS_ALL_SKILLS,
    )
    report = await hunt.complete(REQUEST)
    assert report.mandate.verdict is AuditVerdict.HOLDS
    acquire.assert_awaited_once_with(
        repo_path=None,
        repo_url=REQUEST.repo_url,
        ref=HEAD,
        create_branch=False,
        cache_key=None,
    )
    (call,) = executor.calls
    assert call["session_id"] is None
    assert call["cwd"] == "/tmp/fake-workspace"
    assert call["permission_mode"] == EVAL_PERMISSION_MODE
    assert call["allowed_tools"] == ToolPreset.EVALUATION
    assert call["output_format"]["schema"] == AUDIT_MANDATE_SCHEMA
    assert QUOTE in call["prompt"]


@pytest.mark.parametrize("damage", ["error", "empty", "invalid"])
async def test_failed_session_never_completes_refutation(setup, damage):
    from kodezart.core.errors import NoStructuredOutputError

    build, runner, _, workspace = setup
    if damage == "error":
        runner._events[0] = result_event(
            subtype="success", is_error=True, structured_output=output()
        )
    elif damage == "empty":
        runner._events[0] = result_event(subtype="success", structured_output=None)
    else:
        runner._events[0] = result_event(
            subtype="success", structured_output={"verdict": "holds"}
        )
    with pytest.raises((NoStructuredOutputError, ValidationError)):
        await build().complete(REQUEST)
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


async def test_complete_report_cannot_claim_same_surface_read_and_unreadable(setup):
    from kodezart.types.domain.audit import AuditMandateObservation

    build, *_ = setup
    actual = (await build().complete(REQUEST)).mandate
    with pytest.raises(ValidationError, match="both covered and unreadable"):
        AuditMandateObservation(
            verdict=AuditVerdict.UNVERIFIABLE,
            covered=actual.covered,
            unreadable=[
                {"surface": actual.covered[0].surface, "reason": "Invented failure"}
            ],
            finding=None,
            finding_surface=None,
            evidence="Contradictory provenance",
        )
