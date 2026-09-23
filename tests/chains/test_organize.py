"""Actual tracker-to-executor admission calls preserve the fresh-source boundary."""

import ast
import functools
import inspect
from collections import Counter
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from itertools import pairwise

import pytest
import structlog.testing
from pydantic import ValidationError

from kodezart.chains.organize import OrganizeAdmission
from kodezart.core.errors import NoStructuredOutputError, RateLimitedSoftFailureError
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.domain.errors import (
    OrganizeAdmissionIdentityError,
    OrganizeWriteRefusalError,
)
from kodezart.domain.gap import compute_gap
from kodezart.domain.organize import organize_at_rest, organize_gap
from kodezart.services import organize_owner
from kodezart.services.agent_service import AgentService
from kodezart.services.audit_sessions import judge_in_workspace
from kodezart.services.organize_context import OrganizeContextReader
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.agent import AgentEvent, RateLimitWarningEvent, ResultEvent
from kodezart.types.domain.operation import CheckPrerequisite
from kodezart.types.domain.organize import (
    AdmissionJudgment,
    AdmissionResult,
    AdmissionRoute,
    AdmissionVerdict,
    DefectRole,
    MandateKind,
    OrganizeAdmissionRequest,
    RefusalKind,
    SpecFinding,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope_address import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode, SessionType, ToolPreset
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import (
    NO_SUBAGENTS,
    UNCONFIGURED_SESSION_POLICY,
    AgentDefinition,
    SessionPolicy,
)
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import (
    IssuePriority,
    IssueQuery,
    IssueRelation,
    IssueRelationKind,
    ReviewQuery,
    TrackerIssue,
    TrackerIssueRevision,
    WorkflowStateKind,
)
from kodezart.types.domain.tracker_writes import DescriptionEditResult
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeLinearMcpServer,
    FakeMcpIssue,
    FakeTrackerPort,
    FakeWorkspaceProvider,
)
from tests.name_resolution import (
    call_sites,
    defining_module,
    modules_reaching,
    parsed,
    source_tree,
)
from tests.prompts.sets import OPUS_SET, V5_SET
from tests.prompts.test_organize_mandate_bindings import declared_operation
from tests.prompts.test_prompt_wiring import load_registry
from tests.tracker.conftest import APPROVED_ISSUE, ASSET_ISSUE, CLAIMED_ISSUE
from tests.tracker.test_linear_mcp_tracker import tracker_over

SUBJECT = "subject/42"
REPO = "https://example.invalid/owner/repository"
BASE = "refs/heads/selected-base"


def issue(key: str, body: str, **changes: object) -> TrackerIssue:
    return TrackerIssue.model_validate(
        {
            "issue_key": key,
            "title": f"Title for {key}",
            "body": body,
            "priority": IssuePriority.NONE,
            "state_name": "Todo",
            "state_kind": WorkflowStateKind.UNSTARTED,
            "queue_states": [],
            "team_key": "engineering",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
            "url": f"https://tracker.invalid/{key}",
            **changes,
        }
    )


def tracker() -> FakeTrackerPort:
    return FakeTrackerPort(
        issues=[
            issue(
                SUBJECT,
                "Current issue body\nverbatim second line.",
                relations=[
                    IssueRelation(
                        kind=IssueRelationKind.BLOCKED_BY, issue_key="linked/a"
                    ),
                    IssueRelation(kind=IssueRelationKind.RELATED, issue_key="linked/b"),
                    IssueRelation(kind=IssueRelationKind.RELATED, issue_key="linked/a"),
                    IssueRelation(kind=IssueRelationKind.RELATED, issue_key=SUBJECT),
                ],
            ),
            issue("linked/a", "First linked body."),
            issue("linked/b", "Second linked body."),
            issue(
                "criterion/a",
                "First criterion body.",
                parent_key=SUBJECT,
                issue_labels=["criterion"],
            ),
            issue(
                "criterion/b",
                "Second criterion body.",
                parent_key=SUBJECT,
                issue_labels=["criterion"],
            ),
            issue(
                "ordinary/child",
                "Unlabelled child is not a criterion.",
                parent_key=SUBJECT,
            ),
            issue("outside/a", "Unrelated issue stays outside the prompt."),
        ]
    )


def request() -> OrganizeAdmissionRequest:
    return OrganizeAdmissionRequest(
        scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT),
        issue_key=SUBJECT,
        mandate_rubric="Apply the selected rubric.",
        repo_url=REPO,
        base_ref=BASE,
        cache_key="selected-cache",
        defect_classes=("unsupported-claim",),
    )


def result(**changes: object) -> ResultEvent:
    return ResultEvent.model_validate(
        {
            "result": "Author rationale must never be forwarded.",
            "session_id": "previous-agent-session",
            "subtype": "result",
            "duration_ms": 1,
            "duration_api_ms": 1,
            "is_error": False,
            "num_turns": 1,
            "structured_output": {
                "issue_id": SUBJECT,
                "verdict": "buildable",
                "evidence": "Concrete repository evidence.",
            },
            **changes,
        }
    )


class RecordingExecutor:
    """The real service reaches this strict executor with all permissions visible."""

    def __init__(self, events: Sequence[AgentEvent]) -> None:
        self.events = events
        self.calls: list[dict[str, object]] = []

    async def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: PermissionMode,
        allowed_tools: list[str],
        skills: SkillsSelection,
        session_type: SessionType,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
        run_identity: RunIdentity | None = None,
    ) -> AsyncIterator[AgentEvent]:
        self.calls.append(
            {
                "prompt": prompt,
                "cwd": cwd,
                "permission_mode": permission_mode,
                "allowed_tools": allowed_tools,
                "skills": skills,
                "session_type": session_type,
                "agents": tuple(agents),
                "session_policy": session_policy,
                "session_id": session_id,
                "output_format": output_format,
                "run_identity": run_identity,
            }
        )
        for event in self.events:
            yield event


class RecordingWorkspace(FakeWorkspaceProvider):
    def __init__(self) -> None:
        super().__init__()
        self.arguments: list[dict[str, object]] = []

    async def acquire(self, **kwargs):
        self.arguments.append(kwargs)
        return await super().acquire(**kwargs)


def consumer(source, executor, workspace, set_name=V5_SET, bindings=None):
    runner = AgentService(
        executor=executor, workspace=workspace, git_base_url="https://example.invalid"
    )
    return OrganizeAdmission(
        tracker=source,
        context=OrganizeContextReader(tracker=source, operation=declared_operation()),
        runner=runner,
        workspace=workspace,
        prompts=load_registry(default_set=set_name, bindings=bindings),
        skills=SUPPRESS_ALL_SKILLS,
    )


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
@pytest.mark.parametrize(
    "method,key",
    [
        ("assess", PromptKey.ORGANIZE_ASSESS),
        ("verify", PromptKey.ORGANIZE_VERIFY),
    ],
)
async def test_every_call_reads_full_tracker_sources_and_dispatches_fresh_at_base(
    set_name, method, key
):
    source = tracker()
    executor = RecordingExecutor([result()])
    workspace = RecordingWorkspace()
    admission = consumer(source, executor, workspace, set_name)
    before = dict(source.issues)

    with structlog.testing.capture_logs() as logs:
        for _ in range(2):
            verdict = await getattr(admission, method)(request())
            assert verdict.verdict is AdmissionVerdict.BUILDABLE

    assert len(executor.calls) == 2
    for call in executor.calls:
        prompt = call["prompt"]
        for body in (
            source.issues[SUBJECT].body,
            "First linked body.",
            "Second linked body.",
            "First criterion body.",
            "Second criterion body.",
            "Unlabelled child is not a criterion.",
            "Apply the selected rubric.",
            "unsupported-claim",
            SUBJECT,
            BASE,
        ):
            assert body in prompt
        for excluded in (
            "Unrelated issue stays outside the prompt.",
            "Author rationale must never be forwarded.",
            "previous-agent-session",
        ):
            assert excluded not in prompt
        assert call["session_id"] is None
        assert call["session_type"] is SessionType.ORGANIZE_PASS
        assert call["permission_mode"] == "plan"
        assert call["allowed_tools"] is ToolPreset.EVALUATION
        assert call["agents"] == ()
        assert call["run_identity"] is None
        assert call["skills"] == SUPPRESS_ALL_SKILLS
        assert call["session_policy"] == load_registry(
            default_set=set_name
        ).session_policy(key)
        assert call["cwd"] == "/tmp/fake-workspace"
        assert call["output_format"]["schema"] == AdmissionJudgment.model_json_schema()
    attempts = [log for log in logs if log["event"] == "organize_admission_attempt"]
    assert len(attempts) == 2
    for attempt in attempts:
        assert attempt["session_id"] is None
        assert attempt["resumed_session_id"] is None
        assert attempt["prompt_key"] == key.value
        assert attempt["base_ref"] == BASE
    assert (
        workspace.arguments
        == [
            {
                "repo_path": None,
                "repo_url": REPO,
                "ref": BASE,
                "create_branch": False,
                "cache_key": "selected-cache",
            }
        ]
        * 2
    )
    assert workspace.calls.count(("release", "/tmp/fake-workspace")) == 2
    assert source.issue_reads.count("linked/a") == 2
    assert source.issue_reads.count("linked/b") == 2
    assert source.issues == before
    assert source.issue_writes == source.comment_writes == source.workflow_writes == []
    assert source.issue_creations == []


async def test_verify_rereads_mutated_tracker_bodies_instead_of_reusing_assess_input():
    source = tracker()
    executor = RecordingExecutor([result()])
    workspace = RecordingWorkspace()
    admission = consumer(source, executor, workspace)
    await admission.assess(request())
    for key in (SUBJECT, "linked/a", "criterion/a"):
        source.issues[key] = source.issues[key].model_copy(
            update={"body": f"Fresh {key} body."}
        )
    await admission.verify(request())
    first, second = [call["prompt"] for call in executor.calls]
    for key in (SUBJECT, "linked/a", "criterion/a"):
        assert f"Fresh {key} body." not in first
        assert f"Fresh {key} body." in second


async def test_surface_liveness_reads_never_retest_or_restamp():
    source = tracker()
    executor = RecordingExecutor([])
    workspace = RecordingWorkspace()
    admission = consumer(source, executor, workspace)
    keys = (SUBJECT, "criterion/a", "criterion/b")
    results = {}
    for key in keys:
        executor.events = [
            result(
                structured_output={
                    "issue_id": key,
                    "verdict": "buildable",
                    "evidence": "Observed.",
                }
            )
        ]
        results[key] = await admission.assess(
            request().model_copy(update={"issue_key": key})
        )
    original_records = {key: value.model_dump_json() for key, value in results.items()}
    calls = len(executor.calls)
    acquired = list(workspace.arguments)
    original_body = source.issues["criterion/a"].body
    await source.update_issue(issue_key="criterion/a", body="An amended Check body.")
    for key in keys:
        assert await admission.is_live(results[key]) is False
    assert len(executor.calls) == calls
    assert workspace.arguments == acquired
    assert {
        key: value.model_dump_json() for key, value in results.items()
    } == original_records
    assert source.comment_writes == source.workflow_writes == []
    assert source.issue_creations == []

    executor.events = [
        result(
            structured_output={
                "issue_id": "criterion/a",
                "verdict": "buildable",
                "evidence": "Re-tested.",
            }
        )
    ]
    renewed = await admission.verify(
        request().model_copy(update={"issue_key": "criterion/a"})
    )
    assert renewed.admitted_body_digest != results["criterion/a"].admitted_body_digest
    assert await admission.is_live(renewed) is True
    assert await admission.is_live(results["criterion/a"]) is False
    writes = list(source.issue_writes)
    assert (
        await source.edit_description(
            target="criterion/a",
            expected=original_body,
            replacement="An amended Check body.",
        )
        is DescriptionEditResult.UNCHANGED
    )
    assert await admission.is_live(renewed) is True
    assert source.issue_writes == writes


@pytest.mark.parametrize("method", ["assess", "verify"])
async def test_body_edit_during_session_does_not_stamp_the_later_revision(method):
    source = tracker()
    before = await source.read_issue_revision(issue_key=SUBJECT)

    class EditingExecutor(RecordingExecutor):
        async def stream(self, **kwargs):
            await source.update_issue(
                issue_key=SUBJECT, body="Written after judgment input."
            )
            async for event in super().stream(**kwargs):
                yield event

    executor = EditingExecutor([result()])
    admission = consumer(source, executor, RecordingWorkspace())
    judged = await getattr(admission, method)(request())
    assert judged.admitted_body_digest == before.body_digest
    assert before.issue.body in executor.calls[0]["prompt"]
    assert "Written after judgment input." not in executor.calls[0]["prompt"]
    assert await admission.is_live(judged) is False
    assert len(executor.calls) == 1


async def test_agent_cannot_supply_its_own_revision_metadata():
    output = {**result().structured_output, "admitted_body_digest": "invented-revision"}
    workspace = RecordingWorkspace()
    with pytest.raises(ValidationError, match="extra_forbidden"):
        await consumer(
            tracker(), RecordingExecutor([result(structured_output=output)]), workspace
        ).assess(request())
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


async def test_liveness_read_refuses_missing_or_mismatched_source_identity():
    source = tracker()
    executor = RecordingExecutor([result()])
    admission = consumer(source, executor, RecordingWorkspace())
    original = await admission.assess(request())
    saved_revision = await source.read_issue_revision(issue_key="linked/a")

    async def wrong_revision(**kwargs):
        return saved_revision

    source.read_issue_revision = wrong_revision
    with pytest.raises(OrganizeAdmissionIdentityError) as caught:
        await admission.is_live(original)
    assert caught.value.expected == SUBJECT
    assert caught.value.observed == "linked/a"

    async def missing_revision(**kwargs):
        raise KeyError("missing revision")

    source.read_issue_revision = missing_revision
    with pytest.raises(KeyError, match="missing revision"):
        await admission.is_live(original)
    assert len(executor.calls) == 1


@pytest.mark.parametrize("issue_key", [SUBJECT, "criterion/native"])
async def test_real_revision_reader_lapses_the_exact_admission_surface(issue_key):
    server = FakeLinearMcpServer(
        issues=[
            FakeMcpIssue(id=SUBJECT, description="Parent body."),
            FakeMcpIssue(
                id="criterion/native",
                parent_id=SUBJECT,
                labels=["acceptance-condition"],
                description="Criterion body.",
            ),
        ]
    )
    source = tracker_over(server)
    executor = RecordingExecutor(
        [
            result(
                structured_output={
                    "issue_id": issue_key,
                    "verdict": "buildable",
                    "evidence": "Observed.",
                }
            )
        ]
    )
    admission = consumer(source, executor, RecordingWorkspace())
    judged = await admission.verify(
        request().model_copy(update={"issue_key": issue_key})
    )
    recorded = judged.model_dump_json()
    assert await admission.is_live(judged) is True
    await source.post_comment(issue_key=issue_key, body="A later discussion.")
    assert await admission.is_live(judged) is True
    await source.update_issue(issue_key=issue_key, title="A later title")
    assert await admission.is_live(judged) is False
    await source.update_issue(issue_key=issue_key, body="A later body")
    assert await admission.is_live(judged) is False
    assert judged.model_dump_json() == recorded
    assert len(executor.calls) == 1


@pytest.mark.parametrize("method", ["assess", "verify"])
@pytest.mark.parametrize("missing", ["linked/a", "criterion-read"])
async def test_incomplete_source_reads_fail_before_any_workspace_or_session(
    method, missing
):
    source = tracker()
    if missing == "criterion-read":

        async def fail_read(**kwargs):
            raise RuntimeError("criterion read failed")

        source.read_criteria = fail_read
    else:
        del source.issues[missing]
    executor = RecordingExecutor([result()])
    workspace = RecordingWorkspace()
    with pytest.raises((KeyError, RuntimeError)):
        await getattr(consumer(source, executor, workspace), method)(request())
    assert executor.calls == []
    assert workspace.calls == []


@pytest.mark.parametrize("method", ["assess", "verify"])
@pytest.mark.parametrize(
    "events", [[], [result(structured_output=None)], [result(is_error=True)]]
)
async def test_missing_or_failed_output_releases_workspace_without_admission(
    method, events
):
    executor = RecordingExecutor(events)
    workspace = RecordingWorkspace()
    with pytest.raises(NoStructuredOutputError):
        await getattr(consumer(tracker(), executor, workspace), method)(request())
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


async def test_rejected_rate_limit_is_not_an_admission_even_with_structured_output():
    executor = RecordingExecutor(
        [
            RateLimitWarningEvent(status="rejected"),
            result(),
        ]
    )
    workspace = RecordingWorkspace()
    with pytest.raises(RateLimitedSoftFailureError):
        await consumer(tracker(), executor, workspace).assess(request())
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


@pytest.mark.parametrize("method", ["assess", "verify"])
async def test_judgment_for_another_issue_is_refused_and_workspace_is_released(method):
    wrong = result(
        structured_output={
            "issue_id": "other/42",
            "verdict": "buildable",
            "evidence": "Elsewhere.",
        }
    )
    workspace = RecordingWorkspace()
    with pytest.raises(OrganizeAdmissionIdentityError) as caught:
        await getattr(
            consumer(tracker(), RecordingExecutor([wrong]), workspace), method
        )(request())
    assert caught.value.expected == SUBJECT
    assert caught.value.observed == "other/42"
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


@pytest.mark.parametrize(
    "verdict,details",
    [
        (AdmissionVerdict.BUILDABLE, {}),
        (
            AdmissionVerdict.NOT_BUILDABLE,
            {
                "invented_decision": "Choose a response model.",
                "refusal_kind": RefusalKind.HUMAN_DECISION,
            },
        ),
        (
            AdmissionVerdict.UNVERIFIABLE,
            {"missing_artifact": "Generated schema.", "pending_blocker_id": "linked/a"},
        ),
    ],
)
async def test_all_three_verdicts_return_without_coercion_or_phase_writes(
    verdict, details
):
    output = {
        "issue_id": SUBJECT,
        "verdict": verdict,
        "evidence": "Observed.",
        **details,
    }
    source = tracker()
    actual = await consumer(
        source,
        RecordingExecutor([result(structured_output=output)]),
        RecordingWorkspace(),
    ).assess(request())
    revision = await source.read_issue_revision(issue_key=SUBJECT)
    assert actual == AdmissionResult.model_validate(
        {
            **output,
            "admitted_body_digest": revision.body_digest,
            "admitted_scope": actual.admitted_scope,
            "admitted_context_digest": actual.admitted_context_digest,
        }
    )
    assert source.workflow_writes == []


async def test_session_type_is_required_by_the_actual_runner_call():
    runner = AgentService(
        executor=RecordingExecutor([]),
        workspace=RecordingWorkspace(),
        git_base_url="https://example.invalid",
    )
    with pytest.raises(TypeError, match="session_type"):
        runner.stream_in_workspace(
            prompt="fixture",
            workspace_path="/tmp/fixture",
            permission_mode=PermissionMode.PLAN,
            allowed_tools=[],
            skills=SUPPRESS_ALL_SKILLS,
        )


@pytest.mark.parametrize(
    "extra", ["author_reasoning", "session_id", "resumed_session_id"]
)
def test_request_cannot_carry_author_context_or_a_previous_session(extra):
    with pytest.raises(ValidationError):
        OrganizeAdmissionRequest.model_validate(
            {**request().model_dump(), extra: "previous-session-text"}
        )


@pytest.mark.parametrize("method", ["assess", "verify"])
async def test_real_tracker_hydrates_evidence_before_real_runner_dispatch(
    method,
):
    server = FakeLinearMcpServer(
        issues=[
            FakeMcpIssue(
                id=SUBJECT,
                description="Native parent body.",
                relations=[("blockedBy", "linked/native")],
            ),
            FakeMcpIssue(id="linked/native", description="Native linked body."),
            FakeMcpIssue(
                id="criterion/native",
                parent_id=SUBJECT,
                labels=["acceptance-condition"],
                description="Native Check body.",
            ),
        ]
    )
    source = tracker_over(server)
    executor = RecordingExecutor([result()])
    await getattr(consumer(source, executor, RecordingWorkspace()), method)(request())
    assert len(executor.calls) == 1
    prompt = executor.calls[0]["prompt"]
    for body in ("Native parent body.", "Native linked body.", "Native Check body."):
        assert body in prompt
    reads = {call["id"] for call in server.tool_calls("get_issue")}
    assert {SUBJECT, "linked/native", "criterion/native"} <= reads
    assert not server.tool_calls("save_issue")
    assert not server.tool_calls("save_comment")


def test_the_organize_dispatch_census_names_its_type_and_read_only_policy():
    tree = ast.parse(inspect.getsource(OrganizeAdmission))
    shared = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "judge_in_workspace"
    ]
    assert len(shared) == 1
    keywords = {argument.arg: argument.value for argument in shared[0].keywords}
    assert ast.unparse(keywords["session_type"]) == "SessionType.ORGANIZE_PASS"
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"stream", "stream_in_workspace", "stream_workflow"}
        for node in ast.walk(tree)
    )
    tree = ast.parse(inspect.getsource(judge_in_workspace))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"stream", "stream_in_workspace", "stream_workflow"}
    ]
    assert [node.func.attr for node in calls] == ["stream_in_workspace"]
    keywords = {argument.arg: argument.value for argument in calls[0].keywords}
    assert ast.unparse(keywords["session_type"]) == "session_type"
    assert ast.unparse(keywords["permission_mode"]) == "EVAL_PERMISSION_MODE"
    assert ast.unparse(keywords["session_id"]) == "None"
    assert ast.unparse(keywords["agents"]) == "NO_SUBAGENTS"


BODY_MARKER = "body_ready"
MENTIONED = "other/17"


def gap_revision(key, **changes):
    return TrackerIssueRevision(
        issue=issue(key, f"Body for {key}", **changes),
        body_digest=f"opaque:{key}",
    )


def gap_admission(revision):
    return AdmissionResult(
        issue_id=revision.issue.issue_key,
        verdict=AdmissionVerdict.BUILDABLE,
        evidence="The body has a concrete implementation and verification story.",
        admitted_body_digest=revision.body_digest,
        admitted_scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT),
        admitted_context_digest="fixture-context",
    )


def organized_family(key=SUBJECT):
    return (
        gap_revision(key, issue_labels=[BODY_MARKER]),
        gap_revision(
            f"{key}/check",
            parent_key=key,
            issue_labels=["criterion"],
            state_kind=WorkflowStateKind.COMPLETED,
            state_name="Done",
        ),
    )


def gap_of(revisions, *, admissions=None, findings=(), marker=BODY_MARKER):
    return organize_gap(
        revisions=revisions,
        admissions=(
            tuple(gap_admission(revision) for revision in revisions)
            if admissions is None
            else admissions
        ),
        open_findings=findings,
        body_marker_key=marker,
    )


def test_organized_scope_has_empty_gap_and_preserves_source_records():
    revisions = (*organized_family(), *organized_family("other/17"))
    before = tuple(revision.model_dump_json() for revision in revisions)
    assert gap_of(revisions) == ()
    assert tuple(revision.model_dump_json() for revision in revisions) == before
    assert gap_of(()) == ()


@pytest.mark.parametrize("missing", ["marker", "verdict", "criterion", "liveness"])
def test_each_missing_organize_fact_puts_only_its_issue_in_gap(missing):
    parent, child = organized_family()
    sibling = organized_family("other/17")
    if missing == "marker":
        parent = parent.model_copy(
            update={
                "issue": parent.issue.model_copy(update={"issue_labels": frozenset()})
            }
        )
    revisions = (parent, *((child,) if missing != "criterion" else ()), *sibling)
    admissions = tuple(gap_admission(revision) for revision in revisions)
    if missing == "verdict":
        admissions = tuple(a for a in admissions if a.issue_id != SUBJECT)
    elif missing == "liveness":
        admissions = tuple(
            AdmissionResult.model_validate(
                {**a.model_dump(), "admitted_body_digest": "old body"}
            )
            if a.issue_id == SUBJECT
            else a
            for a in admissions
        )
    assert gap_of(revisions, admissions=admissions) == (parent.issue,)


@pytest.mark.parametrize("role", list(DefectRole))
def test_open_finding_of_either_role_puts_only_its_issue_in_gap(role):
    parent, child = organized_family()
    finding = SpecFinding(
        issue_id=SUBJECT,
        defect_class="unsupported-claim",
        evidence="The recorded claim names no supporting observation.",
        role=role,
        mandate_text="Repeat every unsupported claim."
        if role is DefectRole.MANDATE
        else None,
    )
    revisions = (parent, child, *organized_family("other/17"))
    assert gap_of(revisions, findings=(finding,)) == (parent.issue,)
    assert gap_of(revisions, findings=()) == ()


@pytest.mark.parametrize("state", list(WorkflowStateKind))
def test_child_state_only_controls_non_canceled_existence_not_code_satisfaction(state):
    parent, child = organized_family()
    child = child.model_copy(
        update={"issue": child.issue.model_copy(update={"state_kind": state})}
    )
    expected = (parent.issue,) if state is WorkflowStateKind.CANCELED else ()
    assert gap_of((parent, child)) == expected


def test_canceled_child_does_not_hide_another_live_criterion():
    parent, child = organized_family()
    canceled = gap_revision(
        "old/check",
        parent_key=SUBJECT,
        issue_labels=["criterion"],
        state_kind=WorkflowStateKind.CANCELED,
    )
    assert gap_of((parent, canceled, child)) == ()


def test_deliverable_child_does_not_count_as_a_criterion():
    parent, child = organized_family()
    child = child.model_copy(
        update={"issue": child.issue.model_copy(update={"issue_labels": frozenset()})}
    )
    assert gap_of((parent, child)) == (parent.issue, child.issue)


@pytest.mark.parametrize("record_kind", ["tracker", "decision"])
def test_record_shaped_members_are_outside_gap_even_without_markers_or_verdicts(
    record_kind,
):
    record = gap_revision("record/1", issue_labels=[record_kind])
    finding = SpecFinding(
        issue_id="record/1",
        defect_class="test",
        evidence="Open finding.",
        role=DefectRole.INSTANCE,
    )
    assert gap_of((record,), admissions=(), findings=(finding,)) == ()


def test_gap_uses_the_configured_semantic_body_marker():
    parent, child = organized_family()
    assert gap_of((parent, child), marker="different_phase") == (parent.issue,)
    assert gap_of((parent, child)) == ()


def test_gap_preserves_input_order_and_exact_records():
    parents = [gap_revision(key) for key in ("z/9", "a/1", "m/3")]
    actual = gap_of(parents)
    assert len(actual) == len(parents)
    assert all(
        result is source.issue for result, source in zip(actual, parents, strict=True)
    )


#: Every way one snapshot can be incoherent, and the name each is read by.
INCOHERENT_SNAPSHOTS = [
    "duplicate_revision",
    "duplicate_admission",
    "orphan_criterion",
    "empty_marker",
]


def incoherent_snapshot(malformed):
    """One incoherent snapshot: its revisions, its admissions and its marker.

    The gap and the pre-query are asked the same question over the same
    snapshot, so the snapshot is built once and read by both.
    """
    parent, child = organized_family()
    revisions = (parent, child)
    admissions = tuple(gap_admission(revision) for revision in revisions)
    marker = BODY_MARKER
    if malformed == "duplicate_revision":
        revisions += (parent,)
    elif malformed == "duplicate_admission":
        admissions += (admissions[0],)
    elif malformed == "orphan_criterion":
        revisions = (child,)
    else:
        assert malformed == "empty_marker"
        marker = "  "
    return revisions, admissions, marker


@pytest.mark.parametrize("malformed", INCOHERENT_SNAPSHOTS)
def test_gap_refuses_incoherent_snapshots_instead_of_dropping_evidence(malformed):
    revisions, admissions, marker = incoherent_snapshot(malformed)
    with pytest.raises(ValueError, match="organize gap requires"):
        gap_of(revisions, admissions=admissions, marker=marker)


@pytest.mark.parametrize("stale", ["missing", "changed", "canceled_changed"])
def test_child_surface_liveness_reenters_parent_without_lapsing_its_body(stale):
    parent, child = organized_family()
    extra = organized_family("other/17")
    revisions = (parent, child, *extra)
    if stale == "canceled_changed":
        old = gap_revision(
            "canceled/1",
            parent_key=SUBJECT,
            issue_labels=["criterion"],
            state_kind=WorkflowStateKind.CANCELED,
        )
        revisions = (*revisions, old)
        target = old
    else:
        target = child
    admissions = tuple(gap_admission(revision) for revision in revisions)
    if stale == "missing":
        admissions = tuple(
            a for a in admissions if a.issue_id != target.issue.issue_key
        )
    else:
        revisions = tuple(
            revision.model_copy(update={"body_digest": "later criterion body"})
            if revision is target
            else revision
            for revision in revisions
        )
    before = tuple(a.model_dump_json() for a in admissions)
    assert gap_of(revisions, admissions=admissions) == (parent.issue,)
    assert admissions[0].admitted_body_digest == parent.body_digest
    assert tuple(a.model_dump_json() for a in admissions) == before


@pytest.mark.parametrize("role", list(DefectRole))
def test_open_child_surface_finding_routes_to_parent_only(role):
    parent, child = organized_family()
    finding = SpecFinding(
        issue_id=child.issue.issue_key,
        defect_class="missing-evidence",
        evidence="The Check references an unavailable observation.",
        role=role,
        mandate_text="Require the unavailable observation."
        if role is DefectRole.MANDATE
        else None,
    )
    assert gap_of(
        (parent, child, *organized_family("other/17")), findings=(finding,)
    ) == (parent.issue,)


DELIVERABLE = "deliverable/child"
SUPERSEDED = "superseded/check"


def at_rest_of(revisions, *, admissions=None, findings=(), marker=BODY_MARKER):
    return organize_at_rest(
        revisions=revisions,
        admissions=(
            tuple(gap_admission(revision) for revision in revisions)
            if admissions is None
            else admissions
        ),
        open_findings=findings,
        body_marker_key=marker,
    )


def open_criterion_family(key=SUBJECT):
    """A specification whose one criterion has not been executed yet."""
    parent, check = organized_family(key)
    return parent, check.model_copy(
        update={
            "issue": check.issue.model_copy(
                update={
                    "state_kind": WorkflowStateKind.UNSTARTED,
                    "state_name": "Todo",
                }
            )
        }
    )


def superseded_criterion(key=SUBJECT):
    return gap_revision(
        SUPERSEDED,
        parent_key=key,
        issue_labels=["criterion"],
        state_kind=WorkflowStateKind.CANCELED,
    )


def scope_fixture(name):
    """One scope snapshot with the admissions and open findings standing over it."""
    parent, check = organized_family()
    deliverable = gap_revision(
        DELIVERABLE, parent_key=SUBJECT, issue_labels=[BODY_MARKER]
    )
    match name:
        case "at_rest":
            return (*organized_family(), *organized_family("other/17")), None, ()
        case "open_criterion":
            return open_criterion_family(), None, ()
        case "open_criterion_under_deliverable":
            return (
                (
                    parent,
                    check,
                    deliverable,
                    gap_revision(
                        f"{DELIVERABLE}/check",
                        parent_key=DELIVERABLE,
                        issue_labels=["criterion"],
                    ),
                ),
                None,
                (),
            )
        case "deliverable_child_without_criterion":
            return (parent, check, deliverable), None, ()
        case "canceled_criterion_superseded":
            return (parent, superseded_criterion(), check), None, ()
        case "canceled_criterion_alone":
            return (parent, superseded_criterion()), None, ()
        case "open_finding_on_organized_scope":
            # Organized and admitted throughout, but an open finding names
            # its criterion child, which routes to the parent.
            finding = SpecFinding(
                issue_id=check.issue.issue_key,
                defect_class="missing-evidence",
                evidence="The Check references an unavailable observation.",
                role=DefectRole.INSTANCE,
            )
            return (parent, check, *organized_family("other/17")), None, (finding,)
    assert name == "criterion_body_moved_on"
    judged = open_criterion_family()
    admissions = tuple(gap_admission(revision) for revision in judged)
    moved = judged[1].model_copy(update={"body_digest": "later criterion body"})
    return (judged[0], moved), admissions, ()


#: Every scope shape in the table, with the issues its gap holds.
SCOPE_FIXTURES = {
    "at_rest": (),
    "open_criterion": (),
    "open_criterion_under_deliverable": (),
    "canceled_criterion_superseded": (),
    "canceled_criterion_alone": (SUBJECT,),
    "criterion_body_moved_on": (SUBJECT,),
    "deliverable_child_without_criterion": (DELIVERABLE,),
    "open_finding_on_organized_scope": (SUBJECT,),
}


@pytest.mark.parametrize("name", sorted(SCOPE_FIXTURES))
def test_the_pre_query_answers_the_gap_cardinality_over_each_scope(name):
    revisions, admissions, findings = scope_fixture(name)
    before = tuple(revision.model_dump_json() for revision in revisions)
    gap = gap_of(revisions, admissions=admissions, findings=findings)
    assert tuple(item.issue_key for item in gap) == SCOPE_FIXTURES[name]
    assert at_rest_of(revisions, admissions=admissions, findings=findings) is (
        gap == ()
    )
    assert tuple(revision.model_dump_json() for revision in revisions) == before


def test_the_scope_table_answers_both_ways_so_the_agreement_is_not_vacuous():
    answered = set()
    for name in SCOPE_FIXTURES:
        revisions, admissions, findings = scope_fixture(name)
        answered.add(at_rest_of(revisions, admissions=admissions, findings=findings))
    assert answered == {True, False}


@pytest.mark.parametrize("malformed", INCOHERENT_SNAPSHOTS)
def test_the_pre_query_refuses_an_incoherent_snapshot_instead_of_answering_rest(
    malformed,
):
    revisions, admissions, marker = incoherent_snapshot(malformed)
    with pytest.raises(ValueError, match="organize gap requires"):
        at_rest_of(revisions, admissions=admissions, marker=marker)


@pytest.fixture(params=["fake", "linear"])
def organized_port(request):
    # *stamp_reads* makes every further read of this port move the change
    # stamp, whichever double is underneath. The UNCHANGED replay below writes
    # nothing, so a stamp that only a write moves cannot tell a digest taken
    # from the body alone from one that folds the stamp into it; a stamp that
    # moves on the read can. It is a switch rather than a constructor value
    # because an admission session compares the whole issue across its
    # context read and its revision read, so no session may run under it.
    revisions = (*organized_family(), *organized_family("other/17"))
    keys = tuple(revision.issue.issue_key for revision in revisions)
    if request.param == "fake":
        source = FakeTrackerPort(issues=[revision.issue for revision in revisions])

        def stamp_reads() -> None:
            source.stamp_moves_on_read = True
    else:
        server = FakeLinearMcpServer(
            issues=[
                FakeMcpIssue(
                    id=revision.issue.issue_key,
                    description=revision.issue.body,
                    parent_id=revision.issue.parent_key,
                    status=revision.issue.state_name,
                    status_type=revision.issue.state_kind.value,
                    labels=["acceptance-condition"]
                    if "criterion" in revision.issue.issue_labels
                    else ["body-phase-finished"],
                )
                for revision in revisions
            ],
            state_types={"Todo": "unstarted", "Done": "completed"},
        )
        source = tracker_over(
            server,
            issue_labels={
                "criterion": "acceptance-condition",
                BODY_MARKER: "body-phase-finished",
            },
        )

        def stamp_reads() -> None:
            server.stamp_moves_on_read = True

    return source, keys, stamp_reads


async def read_gap_revisions(source, keys):
    return tuple([await source.read_issue_revision(issue_key=key) for key in keys])


async def test_the_organized_port_moves_its_stamp_on_read_and_not_its_body_revision(
    organized_port,
):
    """Under the switch a read moves the stamp and the body revision holds.

    Stated on both arms and positively, so the replay case below cannot go
    vacuous: a revision that folded the stamp into its digest would answer two
    reads of one unwritten body with two digests, and the digest holding still
    across those reads is the prohibition itself.
    """
    source, keys, stamp_reads = organized_port
    stamp_reads()
    first = await source.read_issue(issue_key=keys[1])
    second = await source.read_issue(issue_key=keys[1])
    assert second.updated_at > first.updated_at
    assert second.body == first.body
    one = await source.read_issue_revision(issue_key=keys[1])
    two = await source.read_issue_revision(issue_key=keys[1])
    assert two.issue.updated_at > one.issue.updated_at
    assert two.body_digest == one.body_digest


@pytest.mark.parametrize("change", ["amended_body", "unchanged_body", "state_only"])
async def test_port_criterion_changes_use_only_surface_digests_for_parent_gap(
    organized_port, change
):
    source, keys, stamp_reads = organized_port
    executor = RecordingExecutor([])
    workspace = RecordingWorkspace()
    admission = consumer(source, executor, workspace)
    judged = []
    for key in keys:
        executor.events = [
            result(
                structured_output={
                    "issue_id": key,
                    "verdict": "buildable",
                    "evidence": "The current body is implementable.",
                }
            )
        ]
        judged.append(
            await admission.assess(
                request().model_copy(
                    update={
                        "issue_key": key,
                        "scope": ScopeRef(
                            kind=ScopeKind.ISSUE,
                            key=(await source.read_issue(issue_key=key)).parent_key
                            or key,
                        ),
                    }
                )
            )
        )
    baseline = tuple(value.model_dump_json() for value in judged)
    assert gap_of(await read_gap_revisions(source, keys), admissions=judged) == ()
    child_key = keys[1]
    before = await source.read_issue(issue_key=child_key)
    if change == "amended_body":
        await source.update_issue(
            issue_key=child_key, body="Check: revised runnable condition."
        )
    elif change == "unchanged_body":
        # The replay writes nothing, so from here the port moves its stamp on
        # every read: that is the only way this arm can tell a body digest from
        # one that folds the stamp in. No session runs after this point.
        stamp_reads()
        assert (
            await source.edit_description(
                target=child_key,
                expected="prior body no longer present",
                replacement=before.body,
            )
            is DescriptionEditResult.UNCHANGED
        )
    else:
        assert before.state_kind is WorkflowStateKind.COMPLETED
        moved = await source.restore_workflow_state(
            issue_key=child_key, state_name="Todo"
        )
        assert moved.state_kind is WorkflowStateKind.UNSTARTED
    calls = len(executor.calls)
    acquisitions = list(workspace.arguments)
    current = await read_gap_revisions(source, keys)
    gap = gap_of(current, admissions=judged)
    assert tuple(item.issue_key for item in gap) == (
        (SUBJECT,) if change == "amended_body" else ()
    )
    assert await admission.is_live(judged[0]) is (change == "unchanged_body")
    assert await admission.is_live(judged[1]) is (change == "unchanged_body")
    assert await admission.is_live(judged[2]) is True
    assert await admission.is_live(judged[3]) is True
    assert tuple(value.model_dump_json() for value in judged) == baseline
    assert len(executor.calls) == calls
    assert workspace.arguments == acquisitions

    if change == "amended_body":
        executor.events = [
            result(
                structured_output={
                    "issue_id": child_key,
                    "verdict": "buildable",
                    "evidence": "The revised body was re-tested.",
                }
            )
        ]
        judged[1] = await admission.verify(
            request().model_copy(update={"issue_key": child_key})
        )
        assert gap_of(await read_gap_revisions(source, keys), admissions=judged) == ()
        assert judged[0].model_dump_json() == baseline[0]


def ripple_vendor_stamp(source, issue_key, instant):
    """Move an issue's vendor change timestamp the way a mention ripple does.

    The backend bumps every issue an edit merely names, leaving its body
    byte-identical.  The port models issues, not the backend's bookkeeping,
    so the ripple is applied to the double's own record here — the point of
    the fixture is that a stamp which moved for no content reason is visible
    and still reaches no admission clause.
    """
    issue = source.issues[issue_key]
    source.issues[issue_key] = issue.model_copy(update={"updated_at": instant})


async def test_mention_ripple_bumps_the_stamp_without_entering_the_gap():
    revisions = (*organized_family(), *organized_family(MENTIONED))
    keys = tuple(revision.issue.issue_key for revision in revisions)
    ripple = datetime(2026, 6, 1, tzinfo=UTC)
    source = FakeTrackerPort(
        issues=[revision.issue for revision in revisions], clock=lambda: ripple
    )
    admissions = tuple(
        gap_admission(revision) for revision in await read_gap_revisions(source, keys)
    )
    assert gap_of(await read_gap_revisions(source, keys), admissions=admissions) == ()

    before = {key: await source.read_issue(issue_key=key) for key in keys}
    await source.update_issue(
        issue_key=SUBJECT,
        body=f"Revised body for {SUBJECT}, which mentions {MENTIONED} and edits "
        f"nothing there.",
    )
    ripple_vendor_stamp(source, MENTIONED, ripple)
    after = {key: await source.read_issue(issue_key=key) for key in keys}

    assert MENTIONED in after[SUBJECT].body
    assert after[SUBJECT].body != before[SUBJECT].body
    assert after[MENTIONED].body == before[MENTIONED].body
    assert after[SUBJECT].updated_at > before[SUBJECT].updated_at
    assert after[MENTIONED].updated_at > before[MENTIONED].updated_at

    gap = gap_of(await read_gap_revisions(source, keys), admissions=admissions)
    assert tuple(item.issue_key for item in gap) == (SUBJECT,)


async def test_two_ticks_with_nothing_changed_between_them_leave_the_second_gap_empty():
    """Nothing changed between two ticks, so the second tick's gap is empty.

    Tick one is the sequence a tick runs: read the revisions, assess every
    member, compute the gap.  Then the tick's own write lands — a record
    comment on the subject that names its neighbour — and the fake stamps the
    issue it wrote, exactly as a backend does; the mention ripple moves the
    neighbour's stamp for a body nobody touched.  Tick two reads the same
    bodies and the same admissions and finds the same empty gap, although
    every stamp in the scope moved.

    A witness, not a fix: ``organize_gap`` has no change-stamp clause at all
    and says so in its own docstring, so this passes on arrival and stays to
    say so.  It runs over the in-process fake because the ripple is written
    onto the double's own record; the adapter arm's stamp movement under a
    real edit is
    ``test_port_criterion_changes_use_only_surface_digests_for_parent_gap``.
    """
    revisions = (*organized_family(), *organized_family(MENTIONED))
    keys = tuple(revision.issue.issue_key for revision in revisions)
    clock = datetime(2026, 6, 1, tzinfo=UTC)
    source = FakeTrackerPort(
        issues=[revision.issue for revision in revisions], clock=lambda: clock
    )
    executor = RecordingExecutor([])
    workspace = RecordingWorkspace()
    admission = consumer(source, executor, workspace)

    assert gap_of(await read_gap_revisions(source, keys), admissions=()) != ()

    judged = []
    for key in keys:
        executor.events = [
            result(
                structured_output={
                    "issue_id": key,
                    "verdict": "buildable",
                    "evidence": "The current body is implementable.",
                }
            )
        ]
        judged.append(
            await admission.assess(
                request().model_copy(
                    update={
                        "issue_key": key,
                        "scope": ScopeRef(
                            kind=ScopeKind.ISSUE,
                            key=(await source.read_issue(issue_key=key)).parent_key
                            or key,
                        ),
                    }
                )
            )
        )
    baseline = tuple(value.model_dump_json() for value in judged)
    assert gap_of(await read_gap_revisions(source, keys), admissions=judged) == ()

    before = {key: await source.read_issue(issue_key=key) for key in keys}
    await source.post_comment(
        issue_key=SUBJECT, body=f"Organized {SUBJECT}; see {MENTIONED}."
    )
    ripple_vendor_stamp(source, MENTIONED, clock)
    after = {key: await source.read_issue(issue_key=key) for key in keys}

    assert after[SUBJECT].updated_at > before[SUBJECT].updated_at
    assert after[MENTIONED].updated_at > before[MENTIONED].updated_at
    assert all(after[key].body == before[key].body for key in keys)
    calls = len(executor.calls)

    current = await read_gap_revisions(source, keys)
    assert {r.issue.issue_key: r.issue.updated_at for r in current} == {
        key: after[key].updated_at for key in keys
    }
    assert gap_of(current, admissions=judged) == ()
    for value in judged:
        assert await admission.is_live(value) is True
    assert len(executor.calls) == calls
    assert tuple(value.model_dump_json() for value in judged) == baseline


def test_gap_has_no_amendment_input_or_body_judgment_branch():
    tree = ast.parse(inspect.getsource(organize_gap))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert not any("amend" in name.lower() for name in names | attributes)
    assert not attributes & {"body", "title", "evidence", "verdict", "state_name"}
    assert set(inspect.signature(organize_gap).parameters) == {
        "revisions",
        "admissions",
        "open_findings",
        "body_marker_key",
    }


CHANGE_STAMP_FIELDS = frozenset(
    {"updated_at", "updatedAt", "updated_since", "updatedSince"}
)
#: The gap arithmetic, named by the objects rather than by their words: the
#: subtree gap and the organize gap.  Only the modules defining them are read,
#: so every definition beside either one is inside the arithmetic whether it
#: is named here or not — ``in_gap`` sits beside ``compute_gap`` — and a
#: module that builds on them, ``SubtreeClosure``'s among them, reaches them
#: by its imports.  Each member is load-bearing: the modules reaching one of
#: them are not the modules reaching the other, so the exact bound below
#: reds when either is dropped.
GAP_ARITHMETIC = (compute_gap, organize_gap)
#: Every supplied module the reach below finds, re-measured off the tree
#: rather than chosen.  A change to it is a change to where the gap can be
#: computed, which is the surface this guard speaks for.
GAP_COMPUTATION_MODULES = frozenset(
    {
        "chains/delivery_coordinator.py",
        "chains/organize.py",
        "chains/scope_walker.py",
        "composition/engine.py",
        "composition/organize.py",
        "composition/passes.py",
        "composition/scope_runtime.py",
        "composition/supervisor.py",
        "domain/gap.py",
        "domain/issue_tree.py",
        "domain/organize.py",
        "main.py",
        "services/barren_record_signals.py",
        "services/escalation_signals.py",
        "services/mandate_graph.py",
        "services/organize_owner.py",
        "services/organize_tick.py",
        "services/run_shape.py",
        "services/scope_dispatcher.py",
        "services/scope_entry.py",
        "services/scope_organizer.py",
        "services/scope_runtime.py",
        "services/scope_tally.py",
    }
)
#: Every module of the tree that reads the change stamp, and the reason it
#: may.  None of them can compute the gap; a module that newly reads the
#: stamp, for any reason and however the value reached it, arrives here as a
#: decision with its reason written down, or reds.
CHANGE_STAMP_READERS = {
    "adapters/linear/tracker.py": "The adapter: it reads the stamp off the wire "
    "and exposes it, and scans by recency, which the Check allows.",
    "adapters/linear/wire.py": "The wire models the adapter parses the stamp into.",
    "domain/criterion_amendment.py": "Leaves the stamp out when it compares a "
    "criterion with the record an amendment expected.",
    "domain/fire_spec.py": "Stamps a captured fire spec with the subject version "
    "it was read at.",
    "services/audit_runtime.py": "Carries the observed stamp onto the record an "
    "audit write expects back.",
    "services/fire_dispatcher.py": "The dispatcher's exclusion memory: a lane "
    "issue stays excluded until its own stamp moves.",
    "services/native_amendments.py": "Leaves the stamp out when it compares a "
    "native write with the record it expected.",
    "services/organize_context.py": "Leaves the stamp out of the organize "
    "context digest.",
    "services/pass_gate.py": "The pass gate's recency cursor over issue and "
    "review scans.",
    "services/tracker_artifacts.py": "Leaves the stamp out of a tracker "
    "artifact's serialised child.",
    "types/domain/dispatch.py": "The self-write ledger: the stamp its own last "
    "write left on an issue.",
    "types/domain/self_writes.py": "An issue movement snapshot, kept separate "
    "from its scan stamp.",
    "types/domain/tracker.py": "The domain models that declare the stamp and the "
    "recency parameter.",
}


def change_stamp_reads(tree):
    """Every way parsed source reaches the tracker's change-timestamp field."""
    reads = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in CHANGE_STAMP_FIELDS:
            reads.add(node.attr)
        elif isinstance(node, ast.Name) and node.id in CHANGE_STAMP_FIELDS:
            reads.add(node.id)
        elif isinstance(node, ast.keyword) and node.arg in CHANGE_STAMP_FIELDS:
            reads.add(node.arg)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in CHANGE_STAMP_FIELDS
        ):
            reads.add(node.value)
    return reads


def gap_homes():
    """The modules that define the gap arithmetic, read off the objects."""
    return frozenset(defining_module(value) for value in GAP_ARITHMETIC)


def gap_computation_sites(sources):
    """Every supplied module that can compute the gap.

    A module computes the gap by reaching the arithmetic, and the only static
    way to reach a module is to import it.  So a gap site is any module whose
    imports lead, at any depth, to a module that defines the arithmetic —
    read through ``imported_modules``, which takes an import at the top, in a
    function, in a class or under ``TYPE_CHECKING``, a relative import, a
    submodule imported from its package, a dotted route through an imported
    package, and a string constant naming a module.

    Nothing here follows the gap's answer.  How a helper hands the answer
    on — returned, yielded, stored in a local, an accumulator, a field, a
    property, a dict slot, a partial, a lambda, a module alias, a method, two
    helpers deep — does not matter, because whoever consumes it imports the
    helper's module, and that module reaches the arithmetic.

    Wider than the Check, and stated so a red is read right: a module that
    imports the arithmetic's module for any reason, a type among them, is
    counted as able to compute the gap.  A module that reaches none of it and
    is handed a value at run time — by argument, attribute or callback — calls
    no gap arithmetic and is no call site; if it reads the stamp, it is a row
    of ``CHANGE_STAMP_READERS`` instead.  Not seen: a module name assembled at
    run time, a relative name handed to ``importlib.import_module`` with its
    package, and ``eval`` or ``exec``.
    """
    trees = {relative: _parsed_once(source) for relative, source in sources.items()}
    return {
        relative: trees[relative]
        for relative in sorted(_gap_reach(frozenset(sources.items())))
    }


@functools.cache
def _parsed_once(source):
    """One module's syntax tree, parsed once per text.

    The guards below read the same unchanged tree many times over and a
    planted case changes one or two modules, so each text is parsed once.
    """
    return ast.parse(source)


@functools.cache
def _gap_reach(sources):
    """The reach over one frozen snapshot of the supplied modules."""
    trees = {relative: _parsed_once(source) for relative, source in sources}
    return modules_reaching(trees, homes=gap_homes())


def _stamp_reads_of(source):
    """What one module's text reads of the change stamp."""
    return change_stamp_reads(_parsed_once(source))


def change_stamp_readers(sources):
    """Every supplied module that reads the change stamp, with what it reads."""
    return {
        relative: _stamp_reads_of(source)
        for relative, source in sources.items()
        if _stamp_reads_of(source)
    }


def gap_sites_reading_the_change_stamp(sources):
    """The discovered gap sites that reach the tracker's change-timestamp field."""
    return {
        relative: _stamp_reads_of(sources[relative])
        for relative in gap_computation_sites(sources)
        if _stamp_reads_of(sources[relative])
    }


def test_no_gap_computation_call_site_reads_the_tracker_change_timestamp():
    homes = gap_homes()
    discovered = gap_computation_sites(source_tree())
    assert homes
    assert discovered
    assert homes <= discovered.keys()
    assert discovered.keys() <= GAP_COMPUTATION_MODULES
    assert gap_sites_reading_the_change_stamp(source_tree()) == {}
    assert change_stamp_reads(ast.parse(inspect.getsource(organize_gap))) == set()


def test_the_discovered_gap_sites_are_the_upper_bound_exactly():
    """The derived surface is the whole bound, not merely inside it.

    The guard above bounds the discovered set from above, so a member dropped
    from ``GAP_ARITHMETIC`` could take gap sites off the scanned surface while
    it still holds: the modules reaching the subtree gap are not the modules
    reaching the organize gap.  Equality is what reds then.
    """
    assert gap_computation_sites(source_tree()).keys() == GAP_COMPUTATION_MODULES


def test_every_module_that_reads_the_change_stamp_is_registered_with_its_reason():
    """The stamp's readers are keyed on the stamp, not on the gap's answer.

    Every module that reads it is read off the tree and must be a row of the
    register, and every row must still read it: the adapter that exposes it,
    the models that declare it, and the few services that keep it for a
    purpose of their own, none of them in the gap's reach.  A module that
    starts reading the stamp — whatever handed it the value — reds here until
    its reason is written down beside the others.
    """
    readers = change_stamp_readers(source_tree())

    assert readers
    assert readers.keys() == CHANGE_STAMP_READERS.keys()
    assert readers.keys().isdisjoint(gap_computation_sites(source_tree()))


@pytest.mark.parametrize(
    ("relative", "anchor", "planted"),
    [
        (
            "domain/gap.py",
            "    return tuple(\n",
            "    if any(criterion.updated_at for criterion in criteria):\n"
            '        raise ValueError("a criterion changed")\n'
            "    return tuple(\n",
        ),
        (
            "services/run_shape.py",
            "    open_keys = {\n",
            "    if any(criterion.updated_at for criterion in criteria):\n"
            '        raise ValueError("a criterion changed")\n'
            "    open_keys = {\n",
        ),
    ],
)
def test_the_guard_reddens_when_a_discovered_gap_site_reads_the_field(
    relative, anchor, planted
):
    sources = source_tree()
    assert sources[relative].count(anchor) == 1
    sources[relative] = sources[relative].replace(anchor, planted)
    assert gap_sites_reading_the_change_stamp(sources) == {relative: {"updated_at"}}


#: Each static way a module can reach the arithmetic's module, as the module
#: text it arrives as.  ``{READ}`` is where a change-stamp read goes.  The
#: two-hop row reaches it through a planted helper module that imports it,
#: so the reach is pinned as a closure and not as one import deep.
IMPORT_ROUTES = {
    "aliased_import": "from kodezart.domain.gap import compute_gap as gap_of\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return gap_of([c for c in criteria{READ}])\n",
    "module_attribute": "import kodezart.domain.gap as gap_module\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return gap_module.compute_gap([c for c in criteria{READ}])\n",
    "import_inside_the_function": "def plan(criteria, since):\n"
    "    from kodezart.domain.gap import compute_gap as _g\n"
    "\n"
    "    return _g([c for c in criteria{READ}])\n",
    "relative_import": "from ..domain.gap import compute_gap\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return compute_gap([c for c in criteria{READ}])\n",
    "submodule_from_its_package": "from kodezart.domain import gap\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return gap.compute_gap([c for c in criteria{READ}])\n",
    "route_through_the_package": "import kodezart\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return kodezart.domain.organize.organize_gap([c for c in criteria{READ}])\n",
    "route_through_an_imported_package": "from kodezart import domain\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return domain.gap.compute_gap([c for c in criteria{READ}])\n",
    "type_checking_import": "from typing import TYPE_CHECKING\n"
    "\n"
    "if TYPE_CHECKING:\n"
    "    from kodezart.domain.issue_tree import SubtreeClosure\n"
    "\n"
    "def plan(closure: 'SubtreeClosure', since):\n"
    "    return [c for c in closure.open_criteria(){READ}]\n",
    "module_named_by_a_string": "import importlib\n"
    "\n"
    "def plan(criteria, since):\n"
    "    gap = importlib.import_module('kodezart.domain.gap')\n"
    "    return gap.compute_gap([c for c in criteria{READ}])\n",
    "two_hops": "from kodezart.services.planted_helper import window\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return [c for c in window(criteria){READ}]\n",
}
#: The helper the two-hop row imports: a module of its own that reaches the
#: arithmetic and reads nothing.
PLANTED_HELPER = (
    "from kodezart.domain.issue_tree import open_criteria\n"
    "\n"
    "def window(criteria):\n"
    "    return open_criteria(criteria)\n"
)


@pytest.mark.parametrize("reads", [True, False])
@pytest.mark.parametrize("route", sorted(IMPORT_ROUTES))
def test_a_gap_site_reached_under_another_spelling_is_discovered_and_scanned(
    route, reads
):
    """A module that reaches the arithmetic by any static route is a gap site.

    One row per route ``imported_modules`` reads, each with and without the
    read.  The read-free rows redden the moment the reach stops following
    that route, because the planted module drops out of the discovered set;
    the reading rows redden the guard itself.
    """
    planted = IMPORT_ROUTES[route].replace(
        "{READ}", " if c.updated_at > since" if reads else ""
    )
    sources = {
        **source_tree(),
        "services/planted.py": planted,
        "services/planted_helper.py": PLANTED_HELPER,
    }

    assert "services/planted.py" in gap_computation_sites(sources)
    assert gap_sites_reading_the_change_stamp(sources) == (
        {"services/planted.py": {"updated_at"}} if reads else {}
    )


def test_a_module_that_only_quotes_a_seed_name_is_not_a_gap_site():
    """A quoted arithmetic name is a value, and an import runs one way.

    What bounds the reach from above: a module whose only mention of the
    arithmetic is a quoted word — a vocabulary label, a log field, a
    serialised key — reaches no module by it, and a module the arithmetic
    itself imports is not thereby able to compute it.  The planted module
    does both: it quotes each arithmetic name and imports the tracker models
    ``domain/gap.py`` imports, and reads the stamp.  Planted rather than read
    off a module of the tree, so the negative holds whatever the tree's own
    prose happens to quote.
    """
    sources = source_tree()
    sources["services/planted.py"] = (
        "from kodezart.types.domain.tracker import TrackerIssue\n"
        "\n"
        "GAP_LABEL = 'in_gap'\n"
        "COLUMNS = ('compute_gap', 'organize_gap', 'SubtreeClosure')\n"
        "\n"
        "def label(row: TrackerIssue):\n"
        "    return {GAP_LABEL: row.updated_at} if GAP_LABEL in COLUMNS else {}\n"
    )

    assert "services/planted.py" not in gap_computation_sites(sources)
    assert gap_sites_reading_the_change_stamp(sources) == {}
    assert "services/planted.py" in change_stamp_readers(sources)


#: The ways a helper beside the arithmetic can hand its answer on, each as
#: ``(home, appended text, imported name, consuming expression)``.  Every one
#: is ordinary Python, and none of them is read: the consumer imports the
#: helper's module, and that is what puts it in the reach.
WRAPPER_SHAPES = {
    "returned call": (
        "domain/gap.py",
        "def gap_since(criteria):\n"
        "    return compute_gap(criteria, supersession_refs={})\n",
        "gap_since",
        "gap_since(criteria)",
    ),
    "returned local": (
        "domain/gap.py",
        "def gap_since(criteria):\n"
        "    answer = compute_gap(criteria, supersession_refs={})\n"
        "    return answer\n",
        "gap_since",
        "gap_since(criteria)",
    ),
    "aliased arithmetic": (
        "domain/gap.py",
        "def gap_since(criteria):\n"
        "    arithmetic = compute_gap\n"
        "    return arithmetic(criteria, supersession_refs={})\n",
        "gap_since",
        "gap_since(criteria)",
    ),
    "yielded answer": (
        "domain/gap.py",
        "def gap_since(criteria):\n"
        "    yield from compute_gap(criteria, supersession_refs={})\n",
        "gap_since",
        "gap_since(criteria)",
    ),
    "module-level alias": (
        "domain/gap.py",
        "_arithmetic = compute_gap\n"
        "\n"
        "def gap_since(criteria):\n"
        "    return _arithmetic(criteria, supersession_refs={})\n",
        "gap_since",
        "gap_since(criteria)",
    ),
    "import inside the wrapper": (
        "services/run_shape.py",
        "def gap_since(criteria):\n"
        "    from kodezart.domain.gap import compute_gap as _g\n"
        "\n"
        "    return _g(criteria, supersession_refs={})\n",
        "gap_since",
        "gap_since(criteria)",
    ),
    "extended accumulator": (
        "domain/gap.py",
        "def gap_since(criteria):\n"
        "    out = []\n"
        "    out.extend(compute_gap(criteria, supersession_refs={}))\n"
        "    return out\n",
        "gap_since",
        "gap_since(criteria)",
    ),
    "appended accumulator": (
        "domain/gap.py",
        "def gap_since(criteria):\n"
        "    out = []\n"
        "    for row in compute_gap(criteria, supersession_refs={}):\n"
        "        out.append(row)\n"
        "    return out\n",
        "gap_since",
        "gap_since(criteria)",
    ),
    "augmented accumulator": (
        "domain/gap.py",
        "def gap_since(criteria):\n"
        "    out = []\n"
        "    out += compute_gap(criteria, supersession_refs={})\n"
        "    return out\n",
        "gap_since",
        "gap_since(criteria)",
    ),
    "tuple unpacking": (
        "domain/gap.py",
        "def gap_since(criteria):\n"
        "    answer, _ = compute_gap(criteria, supersession_refs={}), None\n"
        "    return answer\n",
        "gap_since",
        "gap_since(criteria)",
    ),
    "match capture": (
        "domain/gap.py",
        "def gap_since(criteria):\n"
        "    match compute_gap(criteria, supersession_refs={}):\n"
        "        case answer:\n"
        "            return answer\n",
        "gap_since",
        "gap_since(criteria)",
    ),
    "dict slot": (
        "domain/gap.py",
        "def gap_since(criteria):\n"
        "    slots = {}\n"
        "    slots['gap'] = compute_gap(criteria, supersession_refs={})\n"
        "    return slots['gap']\n",
        "gap_since",
        "gap_since(criteria)",
    ),
    "conditional expression": (
        "domain/gap.py",
        "def gap_since(criteria):\n"
        "    return compute_gap(criteria, supersession_refs={}) if criteria else ()\n",
        "gap_since",
        "gap_since(criteria)",
    ),
    "method": (
        "domain/gap.py",
        "class GapWindow:\n"
        "    def since(self, criteria):\n"
        "        return compute_gap(criteria, supersession_refs={})\n",
        "GapWindow",
        "GapWindow().since(criteria)",
    ),
    "property": (
        "domain/gap.py",
        "class GapWindow:\n"
        "    def __init__(self, criteria):\n"
        "        self.criteria = criteria\n"
        "\n"
        "    @property\n"
        "    def rows(self):\n"
        "        return compute_gap(self.criteria, supersession_refs={})\n",
        "GapWindow",
        "GapWindow(criteria).rows",
    ),
    "field set in __post_init__": (
        "domain/gap.py",
        "import dataclasses\n"
        "\n"
        "@dataclasses.dataclass\n"
        "class GapWindow:\n"
        "    criteria: tuple\n"
        "    rows: tuple = ()\n"
        "\n"
        "    def __post_init__(self):\n"
        "        self.rows = tuple(compute_gap(self.criteria, supersession_refs={}))\n",
        "GapWindow",
        "GapWindow(criteria).rows",
    ),
    "module-level partial": (
        "domain/gap.py",
        "import functools\n"
        "\n"
        "gap_since = functools.partial(compute_gap, supersession_refs={})\n",
        "gap_since",
        "gap_since(criteria)",
    ),
    "lambda": (
        "domain/gap.py",
        "gap_since = lambda criteria: compute_gap(criteria, supersession_refs={})\n",
        "gap_since",
        "gap_since(criteria)",
    ),
    "bare alias export": (
        "domain/gap.py",
        "gap_since = compute_gap\n",
        "gap_since",
        "gap_since(criteria, supersession_refs={})",
    ),
    "wrapper of a wrapper": (
        "domain/issue_tree.py",
        "def open_since(criteria, *, ref):\n"
        "    return open_criteria(criteria, ref=ref)\n",
        "open_since",
        "open_since(criteria, ref=None)",
    ),
}


def wrapper_consumer(shape, reads):
    """The planted wrapper's home, its extended source, and its consumer."""
    home, appended, name, expression = WRAPPER_SHAPES[shape]
    dotted = home.removesuffix(".py").replace("/", ".")
    read = " if c.updated_at > since" if reads else ""
    return (
        home,
        appended,
        f"from kodezart.{dotted} import {name}\n"
        "\n"
        "def plan(criteria, since):\n"
        f"    return [c for c in {expression}{read}]\n",
    )


@pytest.mark.parametrize("reads", [True, False])
@pytest.mark.parametrize("shape", sorted(WRAPPER_SHAPES))
def test_a_module_reaching_the_gap_through_a_wrapper_is_discovered_and_scanned(
    shape, reads
):
    """A consumer of a helper that hands on the gap's answer is a gap site.

    The helper is planted beside the arithmetic under every shape the answer
    can be handed on in, and consumed from a module of its own that spells no
    word of the arithmetic.  The reading rows must redden the guard and the
    register of stamp readers alike; the read-free twins pin the discovery
    itself, so a reach that stopped following the consumer's import reads as
    a module missing from the discovered set rather than as one more green
    run.
    """
    home, appended, consumer = wrapper_consumer(shape, reads)
    sources = source_tree()
    sources[home] += f"\n\n{appended}"
    sources["services/planted.py"] = consumer

    assert "services/planted.py" in gap_computation_sites(sources)
    assert gap_sites_reading_the_change_stamp(sources) == (
        {"services/planted.py": {"updated_at"}} if reads else {}
    )
    assert ("services/planted.py" in change_stamp_readers(sources)) is reads


@pytest.mark.parametrize("field", sorted(CHANGE_STAMP_FIELDS))
@pytest.mark.parametrize("form", ["attribute", "name", "keyword", "wire_key"])
def test_change_stamp_detector_flags_a_gap_site_that_reads_the_field(field, form):
    snippet = {
        "attribute": f"def gap(rows, since):\n"
        f"    return [row for row in rows if row.{field} > since]\n",
        "name": f"def gap(rows, {field}):\n"
        f"    return [row for row in rows if row.body_digest != {field}]\n",
        "keyword": f"def gap(tracker):\n    return tracker.query({field}=MARK)\n",
        "wire_key": f"def gap(row):\n    return row['{field}']\n",
    }[form]
    assert change_stamp_reads(ast.parse(snippet)) == {field}
    assert change_stamp_reads(ast.parse(snippet.replace(field, "body_digest"))) == set()


#: Where the change stamp is stated: the domain issue's own field, and the
#: recency parameter both domain queries state it as.  Each is spelled twice —
#: under its field name and under the alias the model reads and writes it by.
CHANGE_STAMP_HOMES = (
    (TrackerIssue, "updated_at"),
    (IssueQuery, "updated_since"),
    (ReviewQuery, "updated_since"),
)


def test_the_change_stamp_surface_names_every_spelling_of_the_one_field():
    """The scanned spellings are exactly the ones the models state.

    The detector's rows above are parametrised over this set, so a spelling
    dropped out of it takes its own row away with it and nothing reds, and a
    spelling the models gain is never scanned.  The set is therefore derived
    from the models themselves — each home's field name, which a rename turns
    into a lookup that fails, and the alias the model carries it under — and
    the constant must equal that derivation, so it can neither lose a spelling
    nor miss one.
    """
    derived = {
        spelling
        for model, field in CHANGE_STAMP_HOMES
        for spelling in (field, model.model_fields[field].alias)
    }

    assert None not in derived
    assert CHANGE_STAMP_FIELDS == derived


#: The module that defines the gap arithmetic. Its own calls of its own
#: functions are the arithmetic, not a call site into it.
GAP_HOME = "domain/organize.py"


def called_names(node):
    """Every name a parsed definition calls, in either form it can call it."""
    names = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call):
            callee = inner.func
            if isinstance(callee, ast.Name):
                names.add(callee.id)
            elif isinstance(callee, ast.Attribute):
                names.add(callee.attr)
    return names


def gap_callees(source):
    """Every function of *source* that reaches the gap arithmetic when called.

    Derived, not listed: a helper added beside the gap that hands back the
    gap's own answer is a gap computation whatever it is named, and a list
    written here would not know about it.  The walk grows a set that only
    ever grows, so one round per definition is more than it can need.
    """
    tree = ast.parse(source)
    defined = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    reached = {"organize_gap"}
    for _round in range(len(defined) + 1):
        grown = {name for name, node in defined.items() if called_names(node) & reached}
        if grown <= reached:
            break
        reached |= grown
    return frozenset(reached)


def gap_call_sites(sources, callees):
    """Every call of a gap-computing function, outside the module defining it.

    An import renames but does not call: the local name a ``from`` import
    binds is resolved back to the imported one, so an aliased import is the
    same site under another spelling and an import on its own is no site.
    """
    trees = parsed(
        {
            relative: source
            for relative, source in sources.items()
            if relative != GAP_HOME
        }
    )
    return tuple(
        (site.module, site.line, site.name) for site in call_sites(trees, names=callees)
    )


def test_exactly_one_production_call_site_computes_the_organize_gap():
    """One arithmetic, computed in one place, for every entry into a stage.

    The set of callees is read out of the defining module rather than
    written here, so the pre-query counts as a gap computation without
    being named: ``organize_at_rest`` hands back the gap's own cardinality.
    The count is one because the pre-query has no production caller.  An
    owner that later asks it too makes this check read two, and the
    reconciliation is to drop the pre-query call, not to raise the count.
    """
    sources = source_tree()
    callees = gap_callees(sources[GAP_HOME])
    assert {"organize_gap", "organize_at_rest"} <= callees
    assert [
        (relative, name) for relative, _line, name in gap_call_sites(sources, callees)
    ] == [("services/organize_owner.py", "organize_gap")]


def test_the_derivation_reaches_a_helper_that_hands_back_the_gaps_answer():
    snippet = (
        "def organize_gap(*, revisions):\n"
        "    return tuple(revisions)\n"
        "\n"
        "def at_rest(*, revisions):\n"
        "    return not organize_gap(revisions=revisions)\n"
        "\n"
        "def unrelated(*, revisions):\n"
        "    return len(revisions)\n"
    )
    assert gap_callees(snippet) == frozenset({"organize_gap", "at_rest"})


@pytest.mark.parametrize(
    ("form", "body", "expected"),
    [
        (
            "plain",
            "from kodezart.domain.organize import organize_gap\n"
            "\n"
            "def plan(revisions):\n"
            "    return organize_gap(revisions=revisions)\n",
            1,
        ),
        (
            "aliased",
            "from kodezart.domain.organize import organize_gap as _gap\n"
            "\n"
            "def plan(revisions):\n"
            "    return _gap(revisions=revisions)\n",
            1,
        ),
        (
            "attribute",
            "import kodezart.domain.organize as organize\n"
            "\n"
            "def plan(revisions):\n"
            "    return organize.organize_gap(revisions=revisions)\n",
            1,
        ),
        (
            "pre_query",
            "from kodezart.domain.organize import organize_at_rest\n"
            "\n"
            "def plan(revisions):\n"
            "    return organize_at_rest(revisions=revisions)\n",
            1,
        ),
        ("import_alone", "from kodezart.domain.organize import organize_gap\n", 0),
    ],
)
def test_the_call_site_count_reads_each_form_the_call_can_take(form, body, expected):
    sources = source_tree()
    callees = gap_callees(sources[GAP_HOME])
    planted = gap_call_sites({"services/planted.py": body}, callees)
    assert len(planted) == expected
    assert all(relative == "services/planted.py" for relative, _line, _name in planted)
    assert all(name in callees for _relative, _line, name in planted)


def owner_harness():
    """The owner harness module, imported at call time.

    That module imports this one for its fixtures, so the edge back cannot be
    a module-level import. Every owner-driven case here reaches the harness
    through this function and through nothing else.
    """
    from tests.chains import test_organize_owner

    return test_organize_owner


def admit_as(monkeypatch, executor, *, key, payload, verify_only=False):
    """Every admission judgment for *key* answers *payload*, replacing it whole.

    Replaced rather than merged: the harness's own refusal payload always
    carries fields another verdict forbids. Every other session passes
    through untouched. With *verify_only*, only the verification sessions
    are replaced and the assessment keeps the harness's own answer. Returns
    every answer given for *key*, in order.
    """
    import re

    h = owner_harness()
    answered = []
    original = executor.stream

    async def scripted(**kwargs):
        title = kwargs["output_format"]["schema"].get("title")
        keys = re.findall(r"<issue_key>(.*?)</issue_key>", kwargs["prompt"])
        chosen = not verify_only or h.VERIFY_OPENING in kwargs["prompt"]
        async for event in original(**kwargs):
            if title == "AdmissionJudgment" and keys[-1:] == [key] and chosen:
                answer = {"issue_id": key, **payload}
                answered.append(answer)
                event = result(structured_output=answer)
            yield event

    monkeypatch.setattr(executor, "stream", scripted)
    return answered


UNVERIFIABLE_EVIDENCE = (
    "The named blocker produces the schema this issue reads; it cannot be "
    "examined until that issue lands."
)
MISSING_ARTIFACT = "The schema the named blocker produces."


@pytest.mark.parametrize("route", ["edge_in_scope", "no_edge", "out_of_scope"])
async def test_an_unverifiable_admission_marks_the_ticket_only_on_a_real_in_scope_edge(
    monkeypatch, route
):
    """The marker follows the edge and the scope, never the prose.

    The three rows answer the same words; they differ only by the relation
    planted on the subject and the blocker it names. A real ``blockedBy``
    edge to a scope member marks the stage; a named blocker with no edge, or
    one outside the scope, is re-authored, and the verdict survives the
    bounded halt unchanged.
    """
    h = owner_harness()
    owner, board, executor = h.two_lane_board(
        phases=h.ticket_only, body=h.PREPARED_BODY, bound=1
    )
    # The out-of-scope blocker is a board issue with no parent and no edge of
    # its own, so reading it into the graph context reaches nothing missing.
    named = ASSET_ISSUE if route == "out_of_scope" else "second"
    board.server.issues[CLAIMED_ISSUE].relations = (
        [] if route == "no_edge" else [("blockedBy", named)]
    )
    answered = admit_as(
        monkeypatch,
        executor,
        key=CLAIMED_ISSUE,
        payload={
            "verdict": "unverifiable",
            "evidence": UNVERIFIABLE_EVIDENCE,
            "missing_artifact": MISSING_ARTIFACT,
            "pending_blocker_id": named,
        },
    )
    # The owner's own routing call, observed: the verdict it was handed and
    # the route it chose, so a row passes only on the route the edge earns.
    routed = []
    choose = organize_owner.admission_route

    def observed_route(result, **kwargs):
        route_taken = choose(result, **kwargs)
        routed.append((result.issue_id, result.verdict, route_taken))
        return route_taken

    monkeypatch.setattr(organize_owner, "admission_route", observed_route)
    report = await h.run_owner(owner)
    labels = board.server.issues[CLAIMED_ISSUE].labels
    escalations = [
        comment
        for comment in board.server.comments
        if comment.body.startswith(h.ESCALATION_MARKER)
    ]
    _assessed, _verified, authored = h.sessions(executor, 0)
    assert answered
    expected_route = (
        AdmissionRoute.MARK_COMPLETE
        if route == "edge_in_scope"
        else AdmissionRoute.REAUTHOR
    )
    assert [entry for entry in routed if entry[0] == CLAIMED_ISSUE] and all(
        entry == (CLAIMED_ISSUE, AdmissionVerdict.UNVERIFIABLE, expected_route)
        for entry in routed
        if entry[0] == CLAIMED_ISSUE
    )
    if route == "edge_in_scope":
        assert report.halt is None
        assert report.completed_phases == (MandateKind.TICKET,)
        assert "body complete" in labels
        assert CLAIMED_ISSUE not in authored
        assert escalations == []
        return
    assert report.halt.cause == "admission_exhausted"
    assert report.halt.bound.setting == "organize.max_admission_rounds"
    assert report.halt.bound.value == report.halt.bound.rounds_used == 1
    assert [r.verdict for r in report.halt.admission_results] == [
        AdmissionVerdict.UNVERIFIABLE
    ]
    assert report.halt.admission_results[0].pending_blocker_id == named
    assert "body complete" not in labels
    assert CLAIMED_ISSUE in authored
    assert [
        comment.issue_id for comment in escalations if MISSING_ARTIFACT in comment.body
    ] == [CLAIMED_ISSUE]
    assert len(escalations) == 1


async def test_an_unverifiable_verdict_first_met_in_verification_reaches_the_halt(
    monkeypatch,
):
    """A verdict the assessment never gave is still carried to the halt.

    The assessment answers buildable, so the round authors nothing and the
    subject reaches the dry verification. That verification answers
    unverifiable, naming a blocker the subject has no edge to. The route is a
    re-author, so the round is not dry and the one convergence round is spent:
    the halt carries the verdict and its escalation quotes the missing artifact.
    """
    h = owner_harness()
    owner, board, executor = h.factory(
        under_approval=True,
        phases=h.ticket_only,
        body=h.PREPARED_BODY,
        convergence_bound=1,
    )
    answered = admit_as(
        monkeypatch,
        executor,
        key=CLAIMED_ISSUE,
        payload={
            "verdict": "unverifiable",
            "evidence": UNVERIFIABLE_EVIDENCE,
            "missing_artifact": MISSING_ARTIFACT,
            "pending_blocker_id": "second",
        },
        verify_only=True,
    )
    report = await h.run_owner(owner)
    escalations = [
        comment
        for comment in board.server.comments
        if comment.body.startswith(h.ESCALATION_MARKER)
    ]
    assessed, verified, _authored = h.sessions(executor, 0)
    assert CLAIMED_ISSUE in assessed
    assert CLAIMED_ISSUE in verified
    assert answered
    assert report.halt.cause == "convergence_exhausted"
    assert report.halt.bound.value == report.halt.bound.rounds_used == 1
    assert [
        (r.issue_id, r.verdict, r.pending_blocker_id)
        for r in report.halt.admission_results
    ] == [(CLAIMED_ISSUE, AdmissionVerdict.UNVERIFIABLE, "second")]
    assert [
        comment.issue_id for comment in escalations if MISSING_ARTIFACT in comment.body
    ] == [CLAIMED_ISSUE]
    assert "body complete" not in board.server.issues[CLAIMED_ISSUE].labels


SECOND_SURFACE = "second-surface"


def second_surface_regrowth(monkeypatch, *, body, convergence_bound=2):
    """A ticket stage whose fix defects a surface the round did not author.

    The subject's assessment names the class on the subject while its body
    is still the draft. The author carries any mandating sentence forward.
    The criterion child is named with the same class only when its
    verification reads a parent that holds both the grounded body and the
    mandating sentence, that is, only once the carried sentence has landed.
    Every scripted finding is recorded as ``(issue_id, defect_class)``, and
    the parent's description is recorded at every verification of the
    criterion child, whether or not it is named.
    """
    import re

    h = owner_harness()
    owner, board, executor = h.factory(
        under_approval=True,
        phases=h.ticket_only,
        body=body,
        convergence_bound=convergence_bound,
    )
    board.server.issues[SECOND_SURFACE] = FakeMcpIssue(
        id=SECOND_SURFACE,
        parent_id=CLAIMED_ISSUE,
        description=h.RESTATING_BODY,
        labels=["check"],
    )
    observed = []
    examined = []
    original = executor.stream

    def mandate_finding(issue_id):
        observed.append((issue_id, h.REGROWTH_CLASS))
        return {
            "issue_id": issue_id,
            "defect_class": h.REGROWTH_CLASS,
            "evidence": "The surface restates the source version in its prose.",
            "role": "mandate",
            "mandate_text": h.MANDATE_SENTENCE,
        }

    async def scripted(**kwargs):
        title = kwargs["output_format"]["schema"].get("title")
        keys = re.findall(r"<issue_key>(.*?)</issue_key>", kwargs["prompt"])
        parent = board.server.issues[CLAIMED_ISSUE].description
        verifies_second = (
            title == "AdmissionJudgment"
            and keys[-1:] == [SECOND_SURFACE]
            and h.VERIFY_OPENING in kwargs["prompt"]
        )
        if verifies_second:
            examined.append(parent)
        async for event in original(**kwargs):
            payload = event.structured_output
            if title == "OrganizeProposal" and payload.get("kind") == "body":
                source = board.server.issues[payload["issue_id"]].description
                carried = (
                    f"{h.MANDATE_SENTENCE} " if h.MANDATE_SENTENCE in source else ""
                )
                event = result(
                    structured_output={
                        **payload,
                        "body": f"{carried}{h.GROUNDED_BODY}",
                    }
                )
            elif (
                title == "AdmissionJudgment"
                and keys[-1:] == [CLAIMED_ISSUE]
                and h.DRAFT_BODY in parent
            ):
                event = result(
                    structured_output={
                        "issue_id": CLAIMED_ISSUE,
                        "verdict": "not_buildable",
                        "evidence": "The hand-drafted source is not prepared.",
                        "refusal_kind": "spec_gap",
                        "invented_decision": "Prepare the drafted source.",
                        "findings": [mandate_finding(CLAIMED_ISSUE)],
                    }
                )
            elif (
                verifies_second
                and h.GROUNDED_BODY in parent
                and h.MANDATE_SENTENCE in parent
            ):
                event = result(
                    structured_output={
                        **payload,
                        "findings": [mandate_finding(SECOND_SURFACE)],
                    }
                )
            yield event

    monkeypatch.setattr(executor, "stream", scripted)
    return owner, board, executor, observed, examined


def subject_proposals(executor):
    """Every author prompt spent on the subject, in order."""
    import re

    return [
        call["prompt"]
        for call in executor.calls
        if call["output_format"]["schema"].get("title") == "OrganizeProposal"
        and re.findall(r"<issue_key>(.*?)</issue_key>", call["prompt"])[-1:]
        == [CLAIMED_ISSUE]
    ]


async def test_a_fix_that_defects_a_second_surface_is_worked_in_the_next_round(
    monkeypatch,
):
    """Round one's fix is not taken as done: the dry round finds the class again.

    The class the assessment named on the subject is repaired, and the fix
    carries the mandating sentence that makes the criterion child restate
    the class. The criterion child is first verified over the landed fix,
    never over the draft, so its finding is one the fix introduced; it is
    worked in round two, and a mandate that keeps regrowing halts at the
    convergence bound with its surviving finding.
    """
    h = owner_harness()
    owner, board, executor, observed, examined = second_surface_regrowth(
        monkeypatch, body=f"{h.MANDATE_SENTENCE} {h.DRAFT_BODY}"
    )
    report = await h.run_owner(owner)
    fixed = f"{h.MANDATE_SENTENCE} {h.GROUNDED_BODY}"
    assert examined[0] == fixed
    assert board.server.issues[CLAIMED_ISSUE].description == fixed
    assert observed == [
        (CLAIMED_ISSUE, h.REGROWTH_CLASS),
        (SECOND_SURFACE, h.REGROWTH_CLASS),
        (SECOND_SURFACE, h.REGROWTH_CLASS),
    ]
    later = subject_proposals(executor)[1:]
    assert any(
        h.REGROWTH_CLASS
        in prompt.split("<defect_classes>", 1)[1].split("</defect_classes>", 1)[0]
        for prompt in later
    )
    halt = report.halt
    assert halt.cause == "convergence_exhausted"
    assert halt.bound.setting == "organize.max_convergence_rounds"
    assert halt.bound.loop == "convergence"
    assert halt.bound.value == halt.bound.rounds_used == 2
    assert [
        (f.issue_id, f.role, f.mandate_text, f.defect_class)
        for f in halt.surviving_findings
    ] == [(SECOND_SURFACE, DefectRole.MANDATE, h.MANDATE_SENTENCE, h.REGROWTH_CLASS)]
    assert report.completed_phases == ()
    assert not {"body complete", "criteria complete"} & set(
        board.server.issues[CLAIMED_ISSUE].labels
    )


async def test_a_round_that_writes_nothing_does_not_terminate(monkeypatch):
    """An author round with nothing to write is not a dry verification round.

    Round one opens no author session: the subject is admitted as it stands,
    and its dry verification names the class on the criterion child. Round
    two authors the subject and its proposal equals the board, so it writes
    nothing; its own verification still names the class, and round three
    follows it with a further author session and a further verification.
    The loop ends only at the bound, never on the quiet round.
    """
    h = owner_harness()
    body = f"{h.MANDATE_SENTENCE} {h.GROUNDED_BODY}"
    owner, board, executor, observed, _examined = second_surface_regrowth(
        monkeypatch, body=body, convergence_bound=3
    )
    report = await h.run_owner(owner)
    assert len(subject_proposals(executor)) == 2
    assert observed == [(SECOND_SURFACE, h.REGROWTH_CLASS)] * 3
    assert [
        args
        for name, args in board.calls
        if name == "save_issue" and "description" in args
    ] == []
    assert board.server.issues[CLAIMED_ISSUE].description == body
    assert report.halt.cause == "convergence_exhausted"
    assert report.halt.bound.rounds_used == 3
    assert report.completed_phases == ()


async def test_an_empty_work_set_still_spends_its_dry_round_and_goes_round_again(
    monkeypatch,
):
    """An empty gap is not convergence: the round it answers still verifies.

    On the refutation entry the criteria stage's first round owes no lane.
    That round opens no author session but verifies every lane once, and a
    further round follows it, because the verification found the refuted
    claim.
    """
    h = owner_harness()
    spy, _board, _executor, report = await h.entry_refutation(monkeypatch)
    rounds = spy.rounds()
    empty = [index for index, (work, _, _) in enumerate(rounds) if not work]
    assert empty
    for index in empty:
        _work, judged, authored = rounds[index]
        assert judged == dict.fromkeys(sorted(h.LANES), 1)
        assert authored == {}
        assert index + 1 < len(rounds)
    assert report.halt is None


LEAVING_MEMBER = "leaving-member"
DONE_SIBLING = "done-sibling"
DONE_SIBLING_CHECK = "done-sibling-check"


async def test_a_round_whose_own_write_empties_the_roster_still_spends_its_dry_round(
    monkeypatch,
):
    """A finding left live when the pass's own write empties the roster halts.

    The ticket stage's only unlabelled member is authored in round one, and
    its proposal clears its parent, so it leaves the scope. The labelled
    sibling's criterion child is named with a class once that member has
    gone. Round two then has no subject, because a child's finding keeps no
    subject of its own, yet the finding is live: the round still verifies,
    the finding survives, and the pass halts at the convergence bound naming
    it instead of completing the stage.
    """
    import re

    h = owner_harness()
    owner, board, executor = h.factory(
        under_approval=True,
        phases=h.ticket_only,
        body=h.PREPARED_BODY,
        convergence_bound=2,
    )
    board.server.issues[CLAIMED_ISSUE].labels.append("body complete")
    board.server.issues[LEAVING_MEMBER] = FakeMcpIssue(
        id=LEAVING_MEMBER,
        parent_id=CLAIMED_ISSUE,
        description="Missing specification",
    )
    board.server.issues[DONE_SIBLING] = FakeMcpIssue(
        id=DONE_SIBLING,
        parent_id=CLAIMED_ISSUE,
        description=h.PREPARED_BODY,
        labels=["body complete", "criteria complete"],
    )
    board.server.issues[DONE_SIBLING_CHECK] = FakeMcpIssue(
        id=DONE_SIBLING_CHECK,
        parent_id=DONE_SIBLING,
        description=h.REFERENCING_BODY,
        labels=["check"],
    )
    named = []
    original = executor.stream

    async def scripted(**kwargs):
        title = kwargs["output_format"]["schema"].get("title")
        keys = re.findall(r"<issue_key>(.*?)</issue_key>", kwargs["prompt"])
        async for event in original(**kwargs):
            if title == "OrganizeProposal" and keys[-1:] == [LEAVING_MEMBER]:
                event = result(
                    structured_output={
                        "kind": "graph",
                        "issue_id": LEAVING_MEMBER,
                        "changes": [{"kind": "parent", "parent_id": None}],
                    }
                )
            elif (
                title == "AdmissionJudgment"
                and keys[-1:] == [DONE_SIBLING_CHECK]
                and h.VERIFY_OPENING in kwargs["prompt"]
                and board.server.issues[LEAVING_MEMBER].parent_id is None
            ):
                named.append(DONE_SIBLING_CHECK)
                event = result(
                    structured_output={
                        **event.structured_output,
                        "findings": [
                            {
                                "issue_id": DONE_SIBLING_CHECK,
                                "defect_class": h.REGROWTH_CLASS,
                                "evidence": "The check reads a deliverable gone.",
                                "role": "instance",
                            }
                        ],
                    }
                )
            yield event

    monkeypatch.setattr(executor, "stream", scripted)
    report = await h.run_owner(owner)
    assert board.server.issues[LEAVING_MEMBER].parent_id is None
    assert named == [DONE_SIBLING_CHECK] * 2
    halt = report.halt
    assert halt is not None
    assert halt.cause == "convergence_exhausted"
    assert halt.bound.setting == "organize.max_convergence_rounds"
    assert halt.bound.value == halt.bound.rounds_used == 2
    assert [(f.issue_id, f.defect_class) for f in halt.surviving_findings] == [
        (DONE_SIBLING_CHECK, h.REGROWTH_CLASS)
    ]
    assert report.completed_phases == ()


def leaving_member_scope(monkeypatch, *, groom, answer):
    """The board of the round-emptying case, on either row.

    The root and the labelled sibling carry the row's marker, the leaving
    member carries none, and the leaving member's proposal clears its
    parent. Once that member has no parent, every verification of the
    sibling's criterion child is answered by ``answer(payload)``, where
    *payload* is the harness's own answer. Returns the owner, the board and
    the key of every verification so answered.
    """
    import re

    h = owner_harness()
    if groom:
        owner, board, executor = h.factory(body=h.PREPARED_BODY, convergence_bound=2)
        markers = ["graph complete"]
    else:
        owner, board, executor = h.factory(
            under_approval=True,
            phases=h.ticket_only,
            body=h.PREPARED_BODY,
            convergence_bound=2,
        )
        markers = ["body complete", "criteria complete"]
    board.server.issues[CLAIMED_ISSUE].labels.append(markers[0])
    board.server.issues[LEAVING_MEMBER] = FakeMcpIssue(
        id=LEAVING_MEMBER,
        parent_id=CLAIMED_ISSUE,
        description="Missing specification",
    )
    board.server.issues[DONE_SIBLING] = FakeMcpIssue(
        id=DONE_SIBLING,
        parent_id=CLAIMED_ISSUE,
        description=h.PREPARED_BODY,
        labels=list(markers),
    )
    board.server.issues[DONE_SIBLING_CHECK] = FakeMcpIssue(
        id=DONE_SIBLING_CHECK,
        parent_id=DONE_SIBLING,
        description=h.REFERENCING_BODY,
        labels=["check"],
    )
    answered = []
    original = executor.stream

    async def scripted(**kwargs):
        title = kwargs["output_format"]["schema"].get("title")
        keys = re.findall(r"<issue_key>(.*?)</issue_key>", kwargs["prompt"])
        async for event in original(**kwargs):
            if title == "OrganizeProposal" and keys[-1:] == [LEAVING_MEMBER]:
                event = result(
                    structured_output={
                        "kind": "graph",
                        "issue_id": LEAVING_MEMBER,
                        "changes": [{"kind": "parent", "parent_id": None}],
                    }
                )
            elif (
                title == "AdmissionJudgment"
                and keys[-1:] == [DONE_SIBLING_CHECK]
                and h.VERIFY_OPENING in kwargs["prompt"]
                and board.server.issues[LEAVING_MEMBER].parent_id is None
            ):
                answered.append(DONE_SIBLING_CHECK)
                event = result(structured_output=answer(event.structured_output))
            yield event

    monkeypatch.setattr(executor, "stream", scripted)
    return owner, board, answered


async def test_a_groom_round_whose_own_write_empties_the_roster_still_halts(
    monkeypatch,
):
    """The pre-approval row, too, spends its dry round after emptying its roster.

    Grooming admits every member through its scope gate and owes only the
    unlabelled ones. Round one grooms the only unlabelled member, whose
    proposal clears its parent, so round two admits nobody who owes the
    marker. That is not the empty roster of a first round: the sibling's
    criterion child is still named, so round two verifies again and the pass
    halts at the convergence bound naming the child's finding.
    """
    h = owner_harness()
    owner, board, answered = leaving_member_scope(
        monkeypatch,
        groom=True,
        answer=lambda payload: {
            **payload,
            "findings": [
                {
                    "issue_id": DONE_SIBLING_CHECK,
                    "defect_class": h.REGROWTH_CLASS,
                    "evidence": "The check reads a deliverable gone.",
                    "role": "instance",
                }
            ],
        },
    )
    report = await h.run_owner(owner)
    assert board.server.issues[LEAVING_MEMBER].parent_id is None
    assert answered == [DONE_SIBLING_CHECK] * 2
    halt = report.halt
    assert halt is not None
    assert halt.cause == "convergence_exhausted"
    assert halt.bound.setting == "organize.max_convergence_rounds"
    assert halt.bound.value == halt.bound.rounds_used == 2
    assert [(f.issue_id, f.defect_class) for f in halt.surviving_findings] == [
        (DONE_SIBLING_CHECK, h.REGROWTH_CLASS)
    ]
    assert report.completed_phases == ()


REFUSED_CHECK = {
    "issue_id": DONE_SIBLING_CHECK,
    "verdict": "not_buildable",
    "evidence": "The check reads a deliverable that has left the scope.",
    "refusal_kind": "spec_gap",
    "invented_decision": "Name the deliverable the check now reads.",
    "findings": [],
}


async def test_a_refusal_with_no_finding_after_the_roster_empties_still_halts(
    monkeypatch,
):
    """A dry round that fails on a refusal alone is not followed by a completion.

    The ticket stage's round one clears the leaving member's parent. Its dry
    round then refuses the sibling's criterion child and names no finding,
    so round two has no subject and no live finding, yet the dry round
    before it did not hold. Round two verifies again, the refusal stands,
    and the pass halts at the convergence bound carrying it.
    """
    h = owner_harness()
    owner, board, answered = leaving_member_scope(
        monkeypatch, groom=False, answer=lambda _payload: REFUSED_CHECK
    )
    report = await h.run_owner(owner)
    assert board.server.issues[LEAVING_MEMBER].parent_id is None
    assert answered == [DONE_SIBLING_CHECK] * 2
    halt = report.halt
    assert halt is not None
    assert halt.cause == "convergence_exhausted"
    assert halt.bound.value == halt.bound.rounds_used == 2
    assert halt.surviving_findings == ()
    assert [(r.issue_id, r.verdict) for r in halt.admission_results] == [
        (DONE_SIBLING_CHECK, AdmissionVerdict.NOT_BUILDABLE)
    ]
    assert report.completed_phases == ()


async def test_a_groom_refusal_with_no_finding_after_the_roster_empties_still_halts(
    monkeypatch,
):
    """On the pre-approval row, too, a refusal alone keeps the next round.

    Round one grooms the only unlabelled member, whose proposal clears its
    parent, so round two admits nobody who owes the marker. The dry round
    before it refused the sibling's criterion child and named no finding, so
    round two has neither a live finding nor anyone pending, yet that dry
    round did not hold. Round two verifies again, the refusal stands, and the
    pass halts at the convergence bound carrying it.
    """
    h = owner_harness()
    owner, board, answered = leaving_member_scope(
        monkeypatch, groom=True, answer=lambda _payload: REFUSED_CHECK
    )
    report = await h.run_owner(owner)
    assert board.server.issues[LEAVING_MEMBER].parent_id is None
    assert answered == [DONE_SIBLING_CHECK] * 2
    halt = report.halt
    assert halt is not None
    assert halt.cause == "convergence_exhausted"
    assert halt.bound.value == halt.bound.rounds_used == 2
    assert halt.surviving_findings == ()
    assert [(r.issue_id, r.verdict) for r in halt.admission_results] == [
        (DONE_SIBLING_CHECK, AdmissionVerdict.NOT_BUILDABLE)
    ]
    assert report.completed_phases == ()


def description_surface(key):
    return WritableSurface(
        kind=SurfaceKind.ISSUE_DESCRIPTION,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=key),
    )


def drop_the_mandate(monkeypatch, executor):
    """Every body the author proposes leaves the mandating sentence out.

    A landed write then repairs the mandate, so a run that still reports it
    is one whose write did not land.
    """
    h = owner_harness()
    original = executor.stream

    async def repaired(**kwargs):
        async for event in original(**kwargs):
            payload = event.structured_output
            if (
                kwargs["output_format"]["schema"].get("title") == "OrganizeProposal"
                and payload.get("kind") == "body"
            ):
                event = result(
                    structured_output={
                        **payload,
                        "body": payload["body"].replace(f"{h.MANDATE_SENTENCE} ", ""),
                    }
                )
            yield event

    monkeypatch.setattr(executor, "stream", repaired)


@pytest.mark.parametrize("held", [True, False])
async def test_a_surface_held_by_another_run_leaves_the_mandate_open_and_escalated(
    monkeypatch, held
):
    """An unheld in-scope surface stops the round and keeps the finding open.

    The author's fix drops the mandating sentence, so a write that lands
    repairs the mandate and the stage converges: that is the control row.
    With another pass holding the subject's description through the same
    adapter, the stage's own write cannot be made. The round writes nothing
    there, the verification that follows names the mandate class again, and
    the convergence bound reports the surviving finding and escalates it on
    the issue that owns it. Nothing is marked complete.
    """
    h = owner_harness()
    owner, board, executor, _observed = h.regrowth(monkeypatch, mandate=True)
    drop_the_mandate(monkeypatch, executor)
    if not held:
        report = await h.run_owner(owner)
        assert report.halt is None
        assert "body complete" in board.server.issues[CLAIMED_ISSUE].labels
        return
    with structlog.testing.capture_logs() as logs:
        async with RunSurfaceLease(
            tracker=board.tracker(),
            job_id="another-pass",
            surfaces=frozenset({description_surface(CLAIMED_ISSUE)}),
            lease_seconds=60.0,
        ):
            report = await h.run_owner(owner)
    halt = report.halt
    assert report.completed_phases == ()
    assert not {"body complete", "criteria complete"} & set(
        board.server.issues[CLAIMED_ISSUE].labels
    )
    assert halt.cause == "convergence_exhausted"
    assert halt.bound.rounds_used == 2
    assert [(f.issue_id, f.role, f.mandate_text) for f in halt.surviving_findings] == [
        ("restating-criterion", DefectRole.MANDATE, h.MANDATE_SENTENCE)
    ]
    escalations = [
        comment
        for comment in board.server.comments
        if comment.body.startswith(h.ESCALATION_MARKER)
    ]
    # The subject's refused admission is escalated beside the surviving
    # finding: its body is still the draft the held lease kept in place.
    assert sorted(comment.issue_id for comment in escalations) == [
        CLAIMED_ISSUE,
        "restating-criterion",
    ]
    owning = [
        comment for comment in escalations if comment.issue_id == "restating-criterion"
    ]
    assert len(owning) == 1
    assert h.MANDATE_SENTENCE in owning[0].body
    assert "needs decision" in board.server.issues["restating-criterion"].labels
    assert (
        board.server.issues[CLAIMED_ISSUE].description
        == f"{h.MANDATE_SENTENCE} {h.DRAFT_BODY}"
    )
    assert [
        args
        for name, args in board.calls
        if name == "save_issue"
        and "description" in args
        and args.get("id") == CLAIMED_ISSUE
    ] == []
    unheld = [record for record in logs if record["event"] == "organize_surface_unheld"]
    # One per round: the surface is still held when the second round reaches
    # the same write, and each round says so under its own name.
    assert len(unheld) == 2
    assert all(
        (
            record["issue_key"],
            record["phase"],
            record["current_holder"],
            record["surface_kind"],
            record["scope_key"],
        )
        == (
            CLAIMED_ISSUE,
            "ticket",
            "another-pass",
            SurfaceKind.ISSUE_DESCRIPTION.value,
            CLAIMED_ISSUE,
        )
        for record in unheld
    )


async def test_a_surface_another_run_is_bidding_for_leaves_the_mandate_open(
    monkeypatch,
):
    """A race the backend has not settled is a surviving finding, not a crash.

    Another pass takes the subject's description through the same adapter,
    and every later write the backend stamps shares that grant's instant.
    The stage's own bid then meets a grant it cannot order itself against,
    so the acquisition is refused with no settled holder. The round writes
    nothing there and the convergence bound reports the mandate it left.
    """
    h = owner_harness()
    owner, board, executor, _observed = h.regrowth(monkeypatch, mandate=True)
    drop_the_mandate(monkeypatch, executor)
    with structlog.testing.capture_logs() as logs:
        async with RunSurfaceLease(
            tracker=board.tracker(),
            job_id="another-pass",
            surfaces=frozenset({description_surface(CLAIMED_ISSUE)}),
            lease_seconds=60.0,
        ):
            (grant,) = board.grants()
            board.server.comment_instants = [grant.created_at]
            report = await h.run_owner(owner)
    halt = report.halt
    assert report.completed_phases == ()
    assert halt.cause == "convergence_exhausted"
    assert [(f.issue_id, f.role, f.mandate_text) for f in halt.surviving_findings] == [
        ("restating-criterion", DefectRole.MANDATE, h.MANDATE_SENTENCE)
    ]
    assert (
        board.server.issues[CLAIMED_ISSUE].description
        == f"{h.MANDATE_SENTENCE} {h.DRAFT_BODY}"
    )
    unheld = [record for record in logs if record["event"] == "organize_surface_unheld"]
    assert unheld
    assert all(
        (record["issue_key"], record["current_holder"], record["scope_key"])
        == (CLAIMED_ISSUE, None, CLAIMED_ISSUE)
        for record in unheld
    )


async def test_a_finding_outside_the_admitted_scope_stays_a_refusal(monkeypatch):
    """A finding naming an issue the scope does not hold is refused, not owned.

    There is no issue inside the scope for the stage to escalate on, so the
    write refusal stands where it is: nothing is escalated, the named issue
    is not classified, and no marker lands.
    """
    h = owner_harness()
    owner, board, executor = h.factory(
        under_approval=True, phases=h.ticket_only, body=h.PREPARED_BODY
    )
    admit_as(
        monkeypatch,
        executor,
        key=CLAIMED_ISSUE,
        payload={
            "verdict": "buildable",
            "evidence": "The prepared body carries its own source.",
            "findings": [
                {
                    "issue_id": APPROVED_ISSUE,
                    "defect_class": h.REGROWTH_CLASS,
                    "evidence": "The named issue restates the source version.",
                    "role": "mandate",
                    "mandate_text": h.MANDATE_SENTENCE,
                }
            ],
        },
    )
    with pytest.raises(OrganizeWriteRefusalError, match="outside the admitted scope"):
        await h.run_owner(owner)
    assert not [
        comment
        for comment in board.server.comments
        if comment.body.startswith(h.ESCALATION_MARKER)
    ]
    assert "needs decision" not in board.server.issues[APPROVED_ISSUE].labels
    assert not {"body complete", "criteria complete"} & set(
        board.server.issues[CLAIMED_ISSUE].labels
    )


def board_tally(board):
    """Every board call on the log, counted by name; the log is then cleared."""
    tally = Counter(name for name, _ in board.calls)
    board.calls.clear()
    return tally


def scaled(tally, times):
    """*tally* read *times* over, name by name."""
    return Counter({name: count * times for name, count in tally.items()})


class RosterReads:
    """Every ``organize_gap`` call the owner makes, over the reads before it.

    At each call every board call already on the board's log is counted by
    its name, whatever the name, so any board read made between a stage's
    snapshot and its gap call shows up in the tally: the snapshot's own
    reads, the stage's gate reading and each retained admission's freshness
    read are what a caller subtracts to see anything else. The inputs and the
    answer are kept whole, so the pre-query can be asked the same question
    the gap was asked.
    """

    def __init__(self, monkeypatch, board):
        self.at_gap = []
        self.inputs = []
        self.answers = []
        computed = organize_owner.organize_gap

        def recorded(**kwargs):
            answer = computed(**kwargs)
            self.at_gap.append(Counter(name for name, _ in board.calls))
            self.inputs.append(kwargs)
            self.answers.append(tuple(answer))
            return answer

        monkeypatch.setattr(organize_owner, "organize_gap", recorded)


@pytest.mark.parametrize("entry", ["fresh", "retained"])
async def test_one_tick_asks_the_gap_once_per_round_over_its_one_roster_read(
    monkeypatch, entry
):
    """The tick reads the roster once per stage and asks the gap over it.

    A heartbeat over a converged scope. The tick lists the roster four
    times: the ticket snapshot, the ticket barrier, the criteria snapshot and
    the criteria barrier. Each stage asks the gap over exactly its own
    snapshot. The obligation read the gap is built from is that listing and
    the snapshot's revision reads, with the stage's gate reading. The
    pre-query is
    not a second read — it is the cardinality of the answer the gap already
    gave.

    Every board call before each gap is counted by name, and the tally is
    asserted whole on both rows. The units are measured here with the real
    reader: one snapshot (the listing, one revision read per member and the
    stage's gate reading) and one freshness read (one issue's revision, then the
    scope's context over the listed members, with every call name that
    context read makes, its comment reads included).

    The fresh row is a new owner over the converged board, so the ticket
    gap has one snapshot before it and the criteria gap three (the ticket
    snapshot and barrier, then the criteria snapshot). The retained row runs
    the owner that converged it again, so its admissions are still standing:
    each gap also has one freshness read per retained admission whose body
    is still live, compared against the round's own roster and never a
    listing of its own. Any other board read between a snapshot and its gap,
    of any name, breaks the tally.
    """
    h = owner_harness()
    owner, board, converged = h.two_lane_board()
    assert (await h.run_owner(owner)).halt is None
    second, board, executor = h.factory(under_approval=True, board=board)
    scope = ScopeRef(kind=ScopeKind.ISSUE, key=CLAIMED_ISSUE)
    reader = board.tracker()
    board.calls.clear()
    members = await reader.scope_issues(ref=scope)
    listing = board_tally(board)
    per_read = listing["list_issues"]
    for member in members:
        await reader.read_issue_revision(issue_key=member.issue_key)
    revisions = board_tally(board)
    # Each run stage's gate, read the way the owner reads it. Both stages
    # here gate on approval or on an issue classification, which the owner
    # answers from the cascade and from each member's own labels, so the
    # gate reading reads nothing of the board.
    for phase in second._phases:
        await second._carried_members(scope, phase)
        assert board_tally(board) == Counter()
    # One read per member: the unit a re-read of the snapshot would add.
    assert revisions["get_issue"] == len(members)
    snapshot = listing + revisions
    # One retained admission's freshness read: its revision, then the
    # scope's context over the roster the round already holds.
    await reader.read_issue_revision(issue_key=CLAIMED_ISSUE)
    await OrganizeContextReader(tracker=reader, operation=declared_operation()).read(
        scope=scope, member_keys=[member.issue_key for member in members]
    )
    liveness = board_tally(board)
    assert liveness["get_issue"] > 1
    # The freshness read also reads comments, so the tally counts names the
    # snapshot never makes.
    assert liveness["list_comments"] > snapshot["list_comments"]
    ran, spent = (second, executor) if entry == "fresh" else (owner, converged)
    spent.calls.clear()
    probe = RosterReads(monkeypatch, board)
    report = await h.run_owner(ran)

    assert report.halt is None
    assert per_read >= 1
    # The stage's own snapshot, then that stage's barrier and the next
    # stage's snapshot: no listing sits between a snapshot and its gap call.
    assert [tally["list_issues"] for tally in probe.at_gap] == [
        per_read,
        3 * per_read,
    ]
    assert [name for name, _ in board.calls].count("list_issues") == 4 * per_read
    assert len(probe.at_gap) == len(report.completed_phases) == 2
    assert all(
        organize_at_rest(**inputs) is (answer == ())
        for inputs, answer in zip(probe.inputs, probe.answers, strict=True)
    )
    assert spent.calls == []
    assert not [name for name, _ in board.calls if name.startswith("save_")]
    if entry == "fresh":
        assert probe.at_gap == [snapshot, scaled(snapshot, 3)]
        assert probe.answers[0] != ()
        return
    # The ticket stage converged before any criterion child existed, so it
    # retains its two lanes; the criteria stage then gave each lane a child
    # and its dry round verified every member, so it retains them all. No
    # retained body has changed since, so each costs one full freshness read
    # before its stage's gap. The ticket admissions judged a context without
    # the children, so none of them is still live and none reaches the gap;
    # every criteria admission does.
    ticket_retained = [m for m in members if m.issue_key in h.LANES]
    criteria_retained = list(members)
    assert [len(inputs["admissions"]) for inputs in probe.inputs] == [
        0,
        len(criteria_retained),
    ]
    assert len(ticket_retained) < len(criteria_retained)
    ticket_fresh = scaled(liveness, len(ticket_retained))
    criteria_fresh = scaled(liveness, len(criteria_retained))
    assert probe.at_gap == [
        snapshot + ticket_fresh,
        scaled(snapshot, 3) + ticket_fresh + criteria_fresh,
    ]
    # A retained admission still standing answers a stage at rest, so the
    # pre-query's True side is reached at tick level.
    assert () in probe.answers


async def test_a_later_round_lists_the_roster_once_before_its_gap(monkeypatch):
    """A second round of one stage opens on one roster read, like the first.

    The refutation entry re-runs the owner that converged the scope, so its
    admissions are retained, and its criteria stage takes two rounds. Every
    board call the backend serves is counted by name at each gap call. The
    listings between two gap calls are exact multiples of one read.

    Between the two criteria gaps, round one's sessions and its dry pass
    read the board, and then round two opens on its snapshot. From that
    snapshot to round two's gap the whole tally is one snapshot (the
    listing, one revision read per member and the stage's gate reading), one
    approval reading per lane in the round's roster, and one freshness read
    per body-live retained admission, each unit measured here by running the
    real reader. Any other board read before round two's gap, of any name,
    breaks the tally.
    """
    h = owner_harness()
    served = []
    serve = FakeLinearMcpServer.call_tool

    async def counted(self, *, name, arguments):
        served.append(name)
        return await serve(self, name=name, arguments=arguments)

    monkeypatch.setattr(FakeLinearMcpServer, "call_tool", counted)
    snapshot_at = []
    taken = organize_owner.OrganizeOwner._snapshot

    async def snapshotting(self, scope):
        snapshot_at.append(len(served))
        return await taken(self, scope)

    monkeypatch.setattr(organize_owner.OrganizeOwner, "_snapshot", snapshotting)
    round_start = []
    unlabelled = organize_owner.stage_unlabelled

    def opening(**kwargs):
        # The loop reads the stage's unlabelled members right after its own
        # snapshot and gate reading, so that snapshot opens the round.
        round_start.append(snapshot_at[-1])
        return unlabelled(**kwargs)

    monkeypatch.setattr(organize_owner, "stage_unlabelled", opening)
    at_gap = []
    since_round_start = []
    admitted = []
    computed = organize_owner.organize_gap

    def recorded(**kwargs):
        at_gap.append(Counter(served))
        since_round_start.append(Counter(served[round_start[-1] :]))
        admitted.append({result.issue_id for result in kwargs["admissions"]})
        return computed(**kwargs)

    monkeypatch.setattr(organize_owner, "organize_gap", recorded)
    spy, board, _executor, report = await h.entry_refutation(monkeypatch)
    scope = ScopeRef(kind=ScopeKind.ISSUE, key=CLAIMED_ISSUE)
    reader = board.tracker()
    gated, _board, _gated_executor = h.factory(under_approval=True, board=board)
    board.calls.clear()
    members = await reader.scope_issues(ref=scope)
    listing = board_tally(board)
    per_read = listing["list_issues"]
    for member in members:
        await reader.read_issue_revision(issue_key=member.issue_key)
    snapshot = listing + board_tally(board)
    # Each run stage's gate, read the way the owner reads it: approval or an
    # issue classification, answered from the cascade and from each member's
    # own labels, so the gate reading reads nothing of the board.
    for phase in gated._phases:
        await gated._carried_members(scope, phase)
        assert board_tally(board) == Counter()
    approval = Counter()
    for lane in h.LANES:
        await reader.execution_approved(issue_key=lane)
        approval += board_tally(board)
    await reader.read_issue_revision(issue_key=CLAIMED_ISSUE)
    await OrganizeContextReader(tracker=reader, operation=declared_operation()).read(
        scope=scope, member_keys=[member.issue_key for member in members]
    )
    liveness = board_tally(board)
    rounds = at_gap[-len(spy.calls) :]
    assert report.halt is None
    assert per_read >= 1
    # Ticket gap to criteria round one: the ticket barrier and the criteria
    # snapshot. Round one to round two: the listings round one's sessions and
    # its dry pass make, measured by running, then round two's one snapshot.
    # A further listing anywhere before round two's gap adds one read.
    assert [
        later["list_issues"] - earlier["list_issues"]
        for earlier, later in pairwise(rounds)
    ] == [
        2 * per_read,
        17 * per_read,
    ]
    # Round one re-authored the lane the refutation took the label from, so
    # its admission is spent; every other member's is retained, and none of
    # their bodies has changed since.
    reopened = "second"
    assert admitted[-2:] == [
        {member.issue_key for member in members},
        {member.issue_key for member in members} - {reopened},
    ]
    # Round two's roster is both lanes: the one still owing the label and
    # the one the refutation's finding names.
    assert since_round_start[-1] == (
        snapshot + approval + scaled(liveness, len(admitted[-1]))
    )


def unavailable_network_operation():
    """The declared operation, its first repository denying the network.

    The same repository declares its history available; the second declares
    no runner environment at all.
    """
    operation = declared_operation()
    repo = operation.repos[0].model_copy(
        update={
            "runner_environment": {
                CheckPrerequisite.NETWORK: False,
                CheckPrerequisite.REPOSITORY_HISTORY: True,
            }
        }
    )
    return operation.model_copy(update={"repos": (repo, *operation.repos[1:])})


GRADABILITY_SENTENCE = "Ask gradability as well as buildability"
#: The operative phrases of the gradability paragraph, read over the prompt
#: with its line breaks folded to spaces.
GRADABILITY_PHRASES = (
    "can demonstrate is not_buildable with a repairable spec_gap",
    "no declared environment can demonstrate is not_buildable with a repairable "
    "spec_gap",
    "name in the evidence the demonstration that cannot run",
    "where it has to move to",
    "never admit it for a later run to absorb",
    "Do not assume a command, service or credential the declarations do not state",
)


@pytest.mark.parametrize("declared", ["declared", "no_checks", "no_repository"])
@pytest.mark.parametrize("method", ["assess", "verify"])
@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
async def test_both_sets_put_the_declared_environments_in_front_of_the_admission(
    set_name, method, declared
):
    """Both admission roles of both sets carry the operation's environments.

    The check chain and the runner environment arrive as data, beside the
    instruction that makes an undemonstrable deliverable a repairable
    refusal. A repository declaring no check chain renders its named
    absence, an operation declaring no repository renders the same prompt
    without the block, and no state leaves an unrendered placeholder.
    """
    operation = unavailable_network_operation()
    if declared == "no_checks":
        operation = operation.model_copy(
            update={
                "repos": tuple(
                    repo.model_copy(update={"checks": ()}) for repo in operation.repos
                )
            }
        )
    if declared == "no_repository":
        operation = operation.model_copy(update={"repos": ()})
    executor = RecordingExecutor([result()])
    boundary = consumer(
        tracker(),
        executor,
        RecordingWorkspace(),
        set_name=set_name,
        bindings=operation_bindings(operation),
    )
    await getattr(boundary, method)(request())
    prompt = executor.calls[0]["prompt"]
    folded = " ".join(prompt.split())
    assert GRADABILITY_SENTENCE in prompt
    assert [phrase for phrase in GRADABILITY_PHRASES if phrase in folded] == list(
        GRADABILITY_PHRASES
    )
    assert "{{" not in prompt
    history = f"{CheckPrerequisite.REPOSITORY_HISTORY.value}: available"
    if declared == "declared":
        step = unavailable_network_operation().repos[0].checks[0]
        assert f"check {step.name}: `{step.command}`" in prompt
        assert f"{CheckPrerequisite.NETWORK.value}: unavailable" in prompt
        assert history in prompt
        # The second repository declares no runner environment fact.
        assert "no runner environment fact is declared" in prompt
        return
    if declared == "no_checks":
        assert "no check chain is declared" in prompt
        assert f"{CheckPrerequisite.NETWORK.value}: unavailable" in prompt
        assert history in prompt
        return
    assert "declared_environments" not in prompt


#: The operative phrases of the criteria author's Evidence naming paragraph,
#: read over the prompt with its line breaks folded to spaces.
EVIDENCE_NAMING_PHRASES = (
    "Name what will fill each criterion's Evidence",
    "the exact runnable test",
    "the observation that will be recorded instead",
    "refused before it is created",
)


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
def test_both_sets_tell_the_criteria_author_to_name_what_fills_the_evidence(
    set_name,
):
    """The criteria author is told what a criterion must name before it exists."""
    from tests.prompts.test_organize_call_bindings import variables

    rendered = (
        load_registry(default_set=set_name)
        .template_for(PromptKey.ORGANIZE_CRITERIA_AUTHOR)
        .render({**variables(), "base_ref": "main", "issue_key": "external/42"})
    )
    folded = " ".join(rendered.split())
    assert [phrase for phrase in EVIDENCE_NAMING_PHRASES if phrase in folded] == list(
        EVIDENCE_NAMING_PHRASES
    )


#: The criteria author's reading of the declared environments, read over the
#: prompt with its line breaks folded to spaces.
CRITERIA_ENVIRONMENT_PHRASES = (
    "A named test is one runnable through a check the repository declares",
    "a named observation is one its declared runner environment can make",
    "Do not assume a command, service or credential the declarations do not state",
)


@pytest.mark.parametrize("declared", ["declared", "no_checks", "no_repository"])
@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
def test_both_sets_put_the_declared_environments_in_front_of_the_criteria_author(
    set_name, declared
):
    """The criteria author reads the environments a named demonstration needs.

    The same guarded block the admission roles carry: the check chain and
    the runner environment as data, a repository declaring no check chain
    rendered as its named absence, and an operation declaring no repository
    rendering the prompt without the block. Whether a named test or
    observation can run there is the author's judgement under this prompt;
    the refusal before creation asks only that one is named.
    """
    from tests.prompts.test_organize_call_bindings import variables

    operation = unavailable_network_operation()
    if declared == "no_checks":
        operation = operation.model_copy(
            update={
                "repos": tuple(
                    repo.model_copy(update={"checks": ()}) for repo in operation.repos
                )
            }
        )
    if declared == "no_repository":
        operation = operation.model_copy(update={"repos": ()})
    rendered = (
        load_registry(default_set=set_name, bindings=operation_bindings(operation))
        .template_for(PromptKey.ORGANIZE_CRITERIA_AUTHOR)
        .render({**variables(), "base_ref": "main", "issue_key": "external/42"})
    )
    folded = " ".join(rendered.split())
    assert [
        phrase for phrase in CRITERIA_ENVIRONMENT_PHRASES if phrase in folded
    ] == list(CRITERIA_ENVIRONMENT_PHRASES)
    assert "{{" not in rendered
    history = f"{CheckPrerequisite.REPOSITORY_HISTORY.value}: available"
    if declared == "declared":
        step = unavailable_network_operation().repos[0].checks[0]
        assert f"check {step.name}: `{step.command}`" in rendered
        assert f"{CheckPrerequisite.NETWORK.value}: unavailable" in rendered
        assert history in rendered
        assert "no runner environment fact is declared" in rendered
        return
    if declared == "no_checks":
        assert "no check chain is declared" in rendered
        assert f"{CheckPrerequisite.NETWORK.value}: unavailable" in rendered
        assert history in rendered
        return
    assert "declared_environments" not in rendered


UNDEMONSTRABLE_EVIDENCE = "No declared environment can run the demonstration."
RELOCATION = "Move the demonstration onto a declared check."


async def test_an_undemonstrable_deliverable_is_refused_and_relocated_on_the_board(
    monkeypatch,
):
    """A deliverable no declared environment can demonstrate is not admitted.

    The stage's own prompt carries the declared check chain. The refusal is
    scripted: a repairable spec gap whose evidence names the relocation, as
    the admission prompt asks. The relocation is put to the author as the
    repair through that evidence, and it is readable back on the board as
    the escalation of the bounded halt. Nothing is marked and no criterion
    child is created.
    """
    h = owner_harness()
    owner, board, executor = h.factory(
        under_approval=True, phases=h.ticket_only, body=h.PREPARED_BODY, bound=1
    )
    admit_as(
        monkeypatch,
        executor,
        key=CLAIMED_ISSUE,
        payload={
            "verdict": "not_buildable",
            "refusal_kind": "spec_gap",
            "evidence": f"{UNDEMONSTRABLE_EVIDENCE} {RELOCATION}",
            "invented_decision": RELOCATION,
        },
    )
    report = await h.run_owner(owner)
    assessed = [
        call["prompt"]
        for call in executor.calls
        if call["output_format"]["schema"].get("title") == "AdmissionJudgment"
    ]
    assert declared_operation().repos[0].checks[0].command in assessed[0]
    halt = report.halt
    assert halt.cause == "admission_exhausted"
    assert halt.bound.value == halt.bound.rounds_used == 1
    assert [r.refusal_kind for r in halt.admission_results] == [RefusalKind.SPEC_GAP]
    # The re-author is put the refusal's own evidence, and the evidence names
    # the relocation, so the role whose repair it is reads where the
    # demonstration has to move to.
    assert any(RELOCATION in prompt for prompt in subject_proposals(executor))
    escalations = [
        comment
        for comment in board.server.comments
        if comment.body.startswith(h.ESCALATION_MARKER)
        and comment.issue_id == CLAIMED_ISSUE
    ]
    assert len(escalations) == 1
    assert RELOCATION in escalations[0].body
    assert "body complete" not in board.server.issues[CLAIMED_ISSUE].labels
    assert not [
        native
        for native in board.server.issues.values()
        if native.parent_id == CLAIMED_ISSUE
    ]


OBSERVATION = "The recorded observation of the prepared bytes."


def rename_criteria(monkeypatch, executor, rename):
    """Every scripted criteria proposal, each of its items passed through *rename*."""
    original = executor.stream

    async def renamed(**kwargs):
        async for event in original(**kwargs):
            payload = event.structured_output
            if payload.get("kind") == "criteria":
                event = result(
                    structured_output={
                        **payload,
                        "criteria": [
                            rename(dict(item)) for item in payload["criteria"]
                        ],
                    }
                )
            yield event

    monkeypatch.setattr(executor, "stream", renamed)


def naming_nothing(item):
    return {
        name: value
        for name, value in item.items()
        if name not in ("runnable_test", "named_observation")
    }


def naming_an_observation(item):
    return {**naming_nothing(item), "named_observation": OBSERVATION}


def criterion_children(board):
    return [
        native
        for native in board.server.issues.values()
        if native.parent_id == CLAIMED_ISSUE
    ]


MIXED_CRITERIA = ("Check first bytes", "Check second bytes")


@pytest.mark.parametrize(
    "named", ["none", "test", "observation", "first_unnamed", "second_unnamed"]
)
async def test_a_criterion_naming_no_demonstration_is_refused_before_it_is_created(
    monkeypatch, named
):
    """A criterion child is created only once something can fill its Evidence.

    The author names the runnable test that will demonstrate the criterion,
    names only the observation that will be recorded instead, or names
    nothing; naming nothing is refused before the child exists. Either name
    alone is enough, and the created child's Evidence row is still empty.

    Fillability is asked of every criterion the step would create: in the
    mixed rows one of two new criteria names its runnable test and the other,
    first or second, names nothing, and the whole step is refused before
    either child exists.
    """
    h = owner_harness()
    mixed = named in ("first_unnamed", "second_unnamed")
    owner, board, executor = h.factory(
        under_approval=True,
        body=h.PREPARED_BODY,
        **({"criteria": MIXED_CRITERIA} if mixed else {}),
    )
    if named == "none":
        rename_criteria(monkeypatch, executor, naming_nothing)
    elif named == "observation":
        rename_criteria(monkeypatch, executor, naming_an_observation)
    elif mixed:
        unnamed = MIXED_CRITERIA[0 if named == "first_unnamed" else 1]
        rename_criteria(
            monkeypatch,
            executor,
            lambda item: naming_nothing(item) if item["title"] == unnamed else item,
        )

    if named == "none" or mixed:
        with pytest.raises(OrganizeWriteRefusalError, match="names no demonstration"):
            await h.run_owner(owner)
        assert criterion_children(board) == []
        assert not [
            args
            for name, args in board.calls
            if name == "save_issue" and args.get("parentId") == CLAIMED_ISSUE
        ]
        return
    assert (await h.run_owner(owner)).halt is None
    created = criterion_children(board)
    assert [native.description.endswith("**Evidence:**\n") for native in created] == [
        True
    ]


async def test_a_replayed_child_naming_nothing_stays_dry_beside_a_new_named_one(
    monkeypatch,
):
    """Only the criteria a step is about to create must name a demonstration.

    The criteria stage converges, its marker is then removed, and one
    spec_gap re-author is forced. The re-author proposes the existing child
    again naming nothing, beside a new child naming its runnable test.
    Nothing is refused: the new child is created and the existing one is
    left exactly as it was.
    """
    import re

    h = owner_harness()
    owner, board, _executor = h.factory(under_approval=True, body=h.PREPARED_BODY)
    assert (await h.run_owner(owner)).halt is None
    (existing,) = criterion_children(board)
    before = (existing.title, existing.description, list(existing.labels))
    board.server.issues[CLAIMED_ISSUE].labels.remove("criteria complete")
    again, board, executor = h.factory(
        under_approval=True,
        body=h.PREPARED_BODY,
        board=board,
        criteria=(existing.title, "Check second bytes"),
    )
    rename_criteria(
        monkeypatch,
        executor,
        lambda item: naming_nothing(item) if item["title"] == existing.title else item,
    )
    renamed = executor.stream
    refused = []

    async def refuse_once(**kwargs):
        title = kwargs["output_format"]["schema"].get("title")
        keys = re.findall(r"<issue_key>(.*?)</issue_key>", kwargs["prompt"])
        async for event in renamed(**kwargs):
            if title == "AdmissionJudgment" and keys[-1:] == [CLAIMED_ISSUE]:
                if not refused:
                    refused.append(kwargs["prompt"])
                    event = result(
                        structured_output={
                            "issue_id": CLAIMED_ISSUE,
                            "verdict": "not_buildable",
                            "refusal_kind": "spec_gap",
                            "evidence": "A second criterion is owed.",
                            "invented_decision": "Add the second criterion.",
                        }
                    )
            yield event

    monkeypatch.setattr(executor, "stream", refuse_once)
    board.calls.clear()
    report = await h.run_owner(again)
    assert refused
    assert [
        call
        for call in executor.calls
        if "Author criterion sub-issue proposals" in call["prompt"]
    ]
    assert report.halt is None
    assert [native.title for native in criterion_children(board)] == [
        existing.title,
        "Check second bytes",
    ]
    assert (existing.title, existing.description, list(existing.labels)) == before
    assert not [
        args
        for name, args in board.calls
        if name == "save_issue" and args.get("id") == existing.id
    ]
