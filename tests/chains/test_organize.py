"""Actual tracker-to-executor admission calls preserve the fresh-source boundary."""

import ast
import functools
import importlib
import inspect
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta

import pytest
import structlog.testing
from pydantic import ValidationError

from kodezart.chains.organize import OrganizeAdmission
from kodezart.core.errors import NoStructuredOutputError, RateLimitedSoftFailureError
from kodezart.domain.errors import OrganizeAdmissionIdentityError, ScopeReadError
from kodezart.domain.gap import compute_gap
from kodezart.domain.issue_tree import SubtreeClosure, open_criteria
from kodezart.domain.organize import organize_at_rest, organize_gap, stage_unlabelled
from kodezart.services.agent_service import AgentService
from kodezart.services.audit_sessions import judge_in_workspace
from kodezart.services.organize_context import OrganizeContextReader
from kodezart.types.domain.agent import AgentEvent, RateLimitWarningEvent, ResultEvent
from kodezart.types.domain.organize import (
    AdmissionJudgment,
    AdmissionResult,
    AdmissionVerdict,
    DefectRole,
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
    loaded_values,
    module_namespace,
    modules_reaching,
    parsed,
    referencing_definitions,
    source_tree,
)
from tests.prompts.sets import OPUS_SET, V5_SET
from tests.prompts.test_organize_mandate_bindings import declared_operation
from tests.prompts.test_prompt_wiring import load_registry
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


def consumer(source, executor, workspace, set_name=V5_SET):
    runner = AgentService(
        executor=executor, workspace=workspace, git_base_url="https://example.invalid"
    )
    return OrganizeAdmission(
        tracker=source,
        context=OrganizeContextReader(tracker=source, operation=declared_operation()),
        runner=runner,
        workspace=workspace,
        prompts=load_registry(default_set=set_name),
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


@pytest.mark.parametrize(
    "malformed",
    ["duplicate_revision", "duplicate_admission", "orphan_criterion", "empty_marker"],
)
def test_gap_refuses_incoherent_snapshots_instead_of_dropping_evidence(malformed):
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
        marker = "  "
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
    """One scope snapshot with the admissions standing over it."""
    parent, check = organized_family()
    deliverable = gap_revision(
        DELIVERABLE, parent_key=SUBJECT, issue_labels=[BODY_MARKER]
    )
    match name:
        case "at_rest":
            return (*organized_family(), *organized_family("other/17")), None
        case "open_criterion":
            return open_criterion_family(), None
        case "open_criterion_under_deliverable":
            return (
                parent,
                check,
                deliverable,
                gap_revision(
                    f"{DELIVERABLE}/check",
                    parent_key=DELIVERABLE,
                    issue_labels=["criterion"],
                ),
            ), None
        case "deliverable_child_without_criterion":
            return (parent, check, deliverable), None
        case "canceled_criterion_superseded":
            return (parent, superseded_criterion(), check), None
        case "canceled_criterion_alone":
            return (parent, superseded_criterion()), None
    assert name == "criterion_body_moved_on"
    judged = open_criterion_family()
    admissions = tuple(gap_admission(revision) for revision in judged)
    moved = judged[1].model_copy(update={"body_digest": "later criterion body"})
    return (judged[0], moved), admissions


#: Every scope shape in the table, with the issues its gap holds.
SCOPE_FIXTURES = {
    "at_rest": (),
    "open_criterion": (),
    "open_criterion_under_deliverable": (),
    "canceled_criterion_superseded": (),
    "canceled_criterion_alone": (SUBJECT,),
    "criterion_body_moved_on": (SUBJECT,),
    "deliverable_child_without_criterion": (DELIVERABLE,),
}


@pytest.mark.parametrize("name", sorted(SCOPE_FIXTURES))
def test_the_pre_query_answers_the_gap_cardinality_over_each_scope(name):
    revisions, admissions = scope_fixture(name)
    before = tuple(revision.model_dump_json() for revision in revisions)
    gap = gap_of(revisions, admissions=admissions)
    assert tuple(item.issue_key for item in gap) == SCOPE_FIXTURES[name]
    assert at_rest_of(revisions, admissions=admissions) is (gap == ())
    assert tuple(revision.model_dump_json() for revision in revisions) == before


def test_the_scope_table_answers_both_ways_so_the_agreement_is_not_vacuous():
    answered = set()
    for name in SCOPE_FIXTURES:
        revisions, admissions = scope_fixture(name)
        answered.add(at_rest_of(revisions, admissions=admissions))
    assert answered == {True, False}


@pytest.mark.parametrize(
    "malformed",
    ["duplicate_revision", "duplicate_admission", "orphan_criterion", "empty_marker"],
)
def test_the_pre_query_refuses_an_incoherent_snapshot_instead_of_answering_rest(
    malformed,
):
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
        marker = "  "
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


@functools.cache
def gap_home_functions():
    """Every function a gap home defines, read off the module objects.

    The homes are the modules defining ``GAP_ARITHMETIC``; every function
    whose ``__module__`` is one of them is inside the arithmetic — ``in_gap``,
    ``organize_at_rest`` and each stage helper beside them — so a definition
    referring to any one of them is a call site of the arithmetic.
    """
    homes = sorted({value.__module__ for value in GAP_ARITHMETIC})
    return tuple(
        value
        for home in homes
        for value in vars(importlib.import_module(home)).values()
        if inspect.isfunction(value) and value.__module__ == home
    )


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
#: Every module of the tree that spells the change stamp, and the reason it
#: may.  None of them is in the gap's reach, and none holds a call site of the
#: arithmetic found by object.  A module that newly spells the stamp arrives
#: here as a decision with its reason written down, or reds.  The register is
#: kept per module, so a new read inside a module already here is not seen by
#: it: the trap below holds it when the arithmetic, or a call site the
#: fixtures can run, runs it; and the call-site scan holds it when the
#: reading function refers to a function of a gap home, by the stamp's
#: spelling or by the value a name it loads is bound to.
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
    """Every spelling of the tracker's change-timestamp field in parsed source.

    As an attribute, a name, a keyword, a class pattern's keyword and a
    string constant.
    """
    reads = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in CHANGE_STAMP_FIELDS:
            reads.add(node.attr)
        elif isinstance(node, ast.MatchClass):
            reads.update(CHANGE_STAMP_FIELDS.intersection(node.kwd_attrs))
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

    Nothing here follows the gap's answer: a module that imports a helper
    beside the arithmetic is in the reach through that import, whatever shape
    the helper hands the answer on in.

    Wider than the Check, and stated so a red is read right: a module that
    imports the arithmetic's module for any reason, a type among them, is
    counted as able to compute the gap.  Not in the reach: a module handed the
    arithmetic or its answer at run time — by argument, attribute or
    callback — without importing either.  Not seen: a module name assembled
    at run time, a relative name handed to ``importlib.import_module`` with
    its package, and ``eval`` or ``exec``.
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

    Every module that spells it is read off the tree and must be a row of the
    register, and every row must still spell it: the adapter that exposes it,
    the models that declare it, and the few services that keep it for a
    purpose of their own, none of them in the gap's reach.  A module that
    starts to spell the stamp reds here until its reason is written down
    beside the others.
    """
    readers = change_stamp_readers(source_tree())

    assert readers
    assert readers.keys() == CHANGE_STAMP_READERS.keys()
    assert readers.keys().isdisjoint(gap_computation_sites(source_tree()))


def change_stamp_values(relative, tree, namespace, node):
    """The stamp fields a definition reads by value: a name bound to one.

    Every loaded name and attribute inside *node*, resolved through the
    module's globals and its imports (``loaded_values``); one bound to a
    string that is a change-stamp field is a read of it, however the name is
    spelled — a constant in the module, or one imported from another.
    """
    return {
        value
        for value in loaded_values(relative, tree, namespace, node)
        if isinstance(value, str) and value in CHANGE_STAMP_FIELDS
    }


@functools.cache
def _arithmetic_sites_of(relative, source):
    """One module's call sites of the arithmetic, with what each reads."""
    tree = _parsed_once(source)
    namespace = module_namespace(relative, source)
    return tuple(
        (
            name,
            frozenset(
                change_stamp_reads(node)
                | change_stamp_values(relative, tree, namespace, node)
            ),
        )
        for name, node in referencing_definitions(
            relative, tree, namespace, wanted=gap_home_functions()
        )
    )


def arithmetic_call_sites(sources):
    """``(module, definition)`` -> what it reads of the stamp, per call site.

    A call site is a definition whose text refers to a function of a gap
    home by object (``gap_home_functions``), called or not: under an aliased
    import, an import inside the function, a relative import, a module-level
    rebinding, a ``functools.partial``, a static method, loaded as a value
    and handed on, named by a string constant as ``module:attr`` or
    ``module.attr``, or taken as an attribute off a call handed a string
    naming its module (``referencing_definitions``).  Each is scanned whole
    for the stamp's spelling (``change_stamp_reads``) and for a loaded name
    bound to a stamp field, in its module or through an import
    (``change_stamp_values``).  Out of reach: a function handed the
    arithmetic or its answer across a call — by argument, attribute or
    callback — a name built at run time, and a read through a model property
    or method, which only running the site shows; the call sites the
    fixtures can run are run under the trap below.
    """
    return {
        (relative, name): reads
        for relative, source in sorted(sources.items())
        for name, reads in _arithmetic_sites_of(relative, source)
    }


def registered_readers_holding_a_call_site(sources):
    """The registered stamp readers that refer to the arithmetic by object."""
    return sorted(
        {module for module, _name in arithmetic_call_sites(sources)}
        & CHANGE_STAMP_READERS.keys()
    )


def test_no_call_site_of_the_arithmetic_found_by_object_reads_the_change_stamp():
    """The arithmetic's call sites are found by the objects, and read no stamp.

    Every call site is inside the gap's reach, which ties the two readings
    together: a site the reach misses reds here.  A string naming a function
    of a gap home, or its module, is an import edge of the reach as well as a
    reference, so the two readings see it alike.  No registered
    reader of the stamp is a call site: a module kept on the register for a
    reason of its own that starts to refer to the arithmetic reds, whatever
    it reads.
    """
    sites = arithmetic_call_sites(source_tree())

    assert sites
    assert {site for site, reads in sites.items() if reads} == set()
    assert {module for module, _name in sites} <= GAP_COMPUTATION_MODULES
    assert registered_readers_holding_a_call_site(source_tree()) == []


#: Each way a definition refers to the arithmetic by object, as the module
#: text it arrives as.  ``{READ}`` is where a change-stamp read goes.
CALL_SITE_ROUTES = {
    "aliased_import": "from kodezart.domain.gap import compute_gap as gap_of\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return [c for c in gap_of(criteria, supersession_refs={}){READ}]\n",
    "import_inside_the_function": "def plan(criteria, since):\n"
    "    from kodezart.domain.gap import compute_gap as _g\n"
    "\n"
    "    return [c for c in _g(criteria, supersession_refs={}){READ}]\n",
    "module_alias_inside_the_function": "def plan(criteria, since):\n"
    "    import kodezart.domain.gap as gap_module\n"
    "\n"
    "    window = gap_module.compute_gap\n"
    "    return [c for c in window(criteria, supersession_refs={}){READ}]\n",
    "dotted_route_inside_the_function": "def plan(criteria, since):\n"
    "    import kodezart.domain.gap\n"
    "\n"
    "    window = kodezart.domain.gap.compute_gap\n"
    "    return [c for c in window(criteria, supersession_refs={}){READ}]\n",
    "relative_import_inside_the_function": "def plan(criteria, since):\n"
    "    from ..domain.gap import compute_gap as window\n"
    "\n"
    "    return [c for c in window(criteria, supersession_refs={}){READ}]\n",
    "loaded_as_a_value_and_handed_on": "from kodezart.domain import gap\n"
    "\n"
    "def plan(criteria, since, run):\n"
    "    return [c for c in run(gap.compute_gap, criteria){READ}]\n",
    "module_level_rebinding": "from kodezart.domain.organize import organize_gap\n"
    "\n"
    "_ARITHMETIC = organize_gap\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return [c for c in _ARITHMETIC(**criteria){READ}]\n",
    "module_level_partial": "import functools\n"
    "\n"
    "from kodezart.domain.gap import compute_gap\n"
    "\n"
    "_WINDOW = functools.partial(compute_gap, supersession_refs={})\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return [c for c in _WINDOW(criteria){READ}]\n",
    "static_method": "from kodezart.domain.gap import compute_gap\n"
    "\n"
    "class Arithmetic:\n"
    "    window = staticmethod(compute_gap)\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return [c for c in Arithmetic.window(criteria, supersession_refs={}){READ}]\n",
    "named_as_module_colon_attr": "import pkgutil\n"
    "\n"
    "def plan(criteria, since):\n"
    "    window = pkgutil.resolve_name('kodezart.domain.gap:compute_gap')\n"
    "    return [c for c in window(criteria, supersession_refs={}){READ}]\n",
    "named_as_module_dot_attr": "import pkgutil\n"
    "\n"
    "def plan(criteria, since):\n"
    "    window = pkgutil.resolve_name('kodezart.domain.organize.organize_gap')\n"
    "    return [c for c in window(**criteria){READ}]\n",
    "predicate_named_as_module_colon_attr": "import pkgutil\n"
    "\n"
    "def plan(criteria, since):\n"
    "    still_open = pkgutil.resolve_name('kodezart.domain.gap:in_gap')\n"
    "    return [\n"
    "        c for c in criteria if still_open(c, supersession_ref=None){READ}\n"
    "    ]\n",
    "attribute_off_the_resolving_call": "import pkgutil\n"
    "\n"
    "def plan(criteria, since):\n"
    "    window = pkgutil.resolve_name('kodezart.domain:gap').compute_gap\n"
    "    return [c for c in window(criteria, supersession_refs={}){READ}]\n",
    "at_rest_named_as_module_dot_attr": "import pkgutil\n"
    "\n"
    "def plan(criteria, since, board):\n"
    "    at_rest = pkgutil.resolve_name('kodezart.domain.organize.organize_at_rest')\n"
    "    return [c for c in criteria if at_rest(**board){READ}]\n",
}


@pytest.mark.parametrize("reads", [True, False])
@pytest.mark.parametrize("route", sorted(CALL_SITE_ROUTES))
def test_a_call_site_of_the_arithmetic_is_found_by_object_and_scanned(route, reads):
    """A definition referring to the arithmetic by any route is a scanned site.

    One row per route, each with and without the read.  The read-free rows
    redden the moment resolution stops following that route, because the
    planted definition drops out of the sites; the reading rows redden the
    scan.  An import at the top of a module binds its name in the module's
    globals, which the module-level rebinding row reads; the rows importing
    inside the function are what reads an import the globals never hold,
    relative, aliased or dotted.  The partial and static-method rows are what
    unwraps a stand-in, and the two string rows what resolves a named object.
    """
    planted = CALL_SITE_ROUTES[route].replace(
        "{READ}", " if c.updated_at > since" if reads else ""
    )
    sites = arithmetic_call_sites({**source_tree(), "services/planted.py": planted})

    assert sites[("services/planted.py", "plan")] == (
        frozenset({"updated_at"}) if reads else frozenset()
    )


#: Each way a registered reader can name a function of a gap home without
#: importing it: the text that binds ``window`` inside the planted function.
REGISTERED_READER_REFERENCES = {
    "compute_gap as module:attr": "pkgutil.resolve_name("
    "'kodezart.domain.gap:compute_gap')",
    "in_gap as module:attr": "pkgutil.resolve_name('kodezart.domain.gap:in_gap')",
    "organize_at_rest as module.attr": "pkgutil.resolve_name("
    "'kodezart.domain.organize.organize_at_rest')",
    "attribute off the call naming its module": "pkgutil.resolve_name("
    "'kodezart.domain:gap').compute_gap",
}


@pytest.mark.parametrize("reference", sorted(REGISTERED_READER_REFERENCES))
def test_a_registered_reader_that_refers_to_the_arithmetic_is_a_call_site(reference):
    """A module on the register reds the moment it refers to the arithmetic.

    The planted function names a function of a gap home by a string
    constant, so it imports nothing, and it sits in a module that already
    spells the stamp, so the register's keys do not move.  Found by object,
    it is a call site, which a registered reader may not hold; and the
    string names the home's module, so the reach takes the reader in too.
    """
    sources = source_tree()
    sources["services/pass_gate.py"] += (
        "\n\n"
        "def recent_gap(criteria, since):\n"
        "    import pkgutil\n"
        "\n"
        f"    window = {REGISTERED_READER_REFERENCES[reference]}\n"
        "    return [c for c in criteria if window and c.updated_at > since]\n"
    )

    assert registered_readers_holding_a_call_site(sources) == ["services/pass_gate.py"]
    assert arithmetic_call_sites(sources)[
        ("services/pass_gate.py", "recent_gap")
    ] == frozenset({"updated_at"})
    assert "services/pass_gate.py" in gap_computation_sites(sources)


#: Each way a call site reads a stamp field through a name bound to it, as
#: the definition text; ``{FIELD}`` is where a constant is named, and the
#: rows' second text is the same definition naming a field that is no stamp.
STAMP_VALUE_ROUTES = {
    "constant in the module": (
        "STAMP_FIELD = 'updated_at'\n"
        "\n"
        "def plan(criteria):\n"
        "    return sorted(\n"
        "        compute_gap(criteria, supersession_refs={}),\n"
        "        key=lambda c: getattr(c, STAMP_FIELD),\n"
        "    )\n",
        "updated_at",
    ),
    "constant imported inside the function": (
        "def plan(criteria):\n"
        "    from kodezart.adapters.linear.tracker import _ORDER_BY_UPDATED_AT\n"
        "\n"
        "    return [\n"
        "        getattr(c, _ORDER_BY_UPDATED_AT)\n"
        "        for c in compute_gap(criteria, supersession_refs={})\n"
        "    ]\n",
        "updatedAt",
    ),
    "attribute of an imported module": (
        "import kodezart.adapters.linear.tracker as wire\n"
        "\n"
        "def plan(criteria):\n"
        "    return [\n"
        "        getattr(c, wire._ORDER_BY_UPDATED_AT)\n"
        "        for c in compute_gap(criteria, supersession_refs={})\n"
        "    ]\n",
        "updatedAt",
    ),
}


@pytest.mark.parametrize("route", sorted(STAMP_VALUE_ROUTES))
def test_a_call_site_reading_the_stamp_through_a_bound_name_reads_it(route):
    """A name bound to a stamp field is a read of the field, by its value.

    Planted beside an import of the arithmetic, so the definition is a call
    site; its own text never spells the stamp.  The same definition with
    the name bound to a field that is no stamp reads nothing.
    """
    text, field = STAMP_VALUE_ROUTES[route]
    header = "from kodezart.domain.gap import compute_gap\n\n"
    other = text.replace("'updated_at'", "'body_digest'").replace(
        "_ORDER_BY_UPDATED_AT", "_ORDER_BY_BODY"
    )
    planted = {**source_tree(), "services/planted.py": header + text}
    control = {**source_tree(), "services/planted.py": header + other}

    definition = next(
        node for node in ast.walk(ast.parse(text)) if isinstance(node, ast.FunctionDef)
    )
    assert change_stamp_reads(definition) == set()
    assert arithmetic_call_sites(planted)[("services/planted.py", "plan")] == {field}
    assert arithmetic_call_sites(control)[("services/planted.py", "plan")] == set()


class ChangeStampRead(BaseException):
    """An operation on a trapped change stamp: the arithmetic read it.

    Not an ``Exception``, so no ``except Exception`` in the arithmetic can
    turn a read into an answer.
    """


#: Every operation a value can be put to that could let it decide an answer.
TRAPPED_OPERATIONS = (
    "__eq__",
    "__ne__",
    "__lt__",
    "__le__",
    "__gt__",
    "__ge__",
    "__hash__",
    "__bool__",
    "__str__",
    "__repr__",
    "__format__",
    "__add__",
    "__radd__",
    "__sub__",
    "__rsub__",
    "__int__",
    "__float__",
    "__index__",
    "__len__",
    "__iter__",
    "__contains__",
    "__getitem__",
    "__call__",
    "__getattribute__",
)


def change_stamp_trap(reads):
    """A change stamp whose every operation is recorded in *reads* and raises.

    Comparison, hashing, truth, text, arithmetic, attribute access: each one
    appends its name and raises ``ChangeStampRead``.  The record is kept even
    when something swallows the raise.
    """

    def sprung(operation):
        def operate(*_arguments):
            reads.append(operation)
            raise ChangeStampRead(operation)

        return operate

    trap = type(
        "ChangeStampTrap",
        (),
        {operation: sprung(operation) for operation in TRAPPED_OPERATIONS},
    )
    return trap()


def arithmetic_cases(stamp):
    """Each entry point of the arithmetic over boards reaching every arm it has.

    ``case -> (entry point, keyword arguments, records)``; every record's
    change stamp is ``stamp(n)`` for the n-th record the case builds, and an
    answer is read as the positions of its records among *records*.

    The arms reached.  ``compute_gap`` and the ``in_gap`` it runs: every
    workflow state kind, each with and without a supersession, so an open
    criterion, a completed one, and a canceled and a duplicate one either
    superseded or not; an empty board; and its three refusals — a criterion
    twice, a record that is no criterion, a blank supersession.  The organize
    gap, asked by ``organize_gap`` and by ``organize_at_rest``: a board with a
    subject at rest, a nested subtree (a deliverable child under it holding
    its own criterion), a subject without its marker, one without an
    admission, one whose admission is stale, one whose criterion child's
    admission is stale, one whose only criterion is canceled, one with an
    open finding, one whose criterion child has one, and a tracker record and
    a decision record that are no subject; a board at rest; an empty board;
    and its four refusals — a blank marker, a revision twice, an admission
    twice, a criterion without its parent.
    """
    built = iter(range(1_000))

    def record(key, **changes):
        fixture = issue(key, f"Body for {key}", **changes)
        return fixture.model_copy(update={"updated_at": stamp(next(built))})

    def revision(key, **changes):
        return TrackerIssueRevision(
            issue=issue(key, f"Body for {key}"), body_digest=f"opaque:{key}"
        ).model_copy(update={"issue": record(key, **changes)})

    def criterion(key, kind, parent=SUBJECT):
        return record(
            key,
            parent_key=parent,
            issue_labels=["criterion"],
            state_kind=kind,
            state_name=kind.value,
        )

    kinds = [
        criterion(f"criterion/{kind.value}{superseded}", kind)
        for kind in WorkflowStateKind
        for superseded in ("", "/superseded")
    ]
    refs = {
        each.issue_key: "superseding/1"
        for each in kinds
        if each.issue_key.endswith("/superseded")
    }

    def subtree(criteria, refs=None):
        return (
            compute_gap,
            {"criteria": criteria, "supersession_refs": refs or {}},
            criteria,
        )

    def family(key, *, marker=True, child=WorkflowStateKind.COMPLETED, parent=None):
        return (
            revision(
                key,
                issue_labels=[BODY_MARKER] if marker else [],
                **({"parent_key": parent} if parent else {}),
            ),
            revision(
                f"{key}/check",
                parent_key=key,
                issue_labels=["criterion"],
                state_kind=child,
                state_name=child.value,
            ),
        )

    at_rest = family(SUBJECT)
    nested = family(f"{SUBJECT}/deliverable", parent=SUBJECT)
    unmarked = family("unmarked/1", marker=False)
    unadmitted = family("unadmitted/1")
    stale = family("stale/1")
    stale_child = family("stale-child/1")
    uncriterioned = family("uncriterioned/1", child=WorkflowStateKind.CANCELED)
    found = family("found/1")
    found_child = family("found-child/1")
    records = (
        revision("tracker/1", issue_labels=["tracker"]),
        revision("decision/1", issue_labels=["decision"]),
    )
    board = (
        *at_rest,
        *nested,
        *unmarked,
        *unadmitted,
        *stale,
        *stale_child,
        *uncriterioned,
        *found,
        *found_child,
        *records,
    )
    lapsed = {stale[0].issue.issue_key, stale_child[1].issue.issue_key}
    admissions = tuple(
        AdmissionResult.model_validate(
            {
                **gap_admission(each).model_dump(),
                "admitted_body_digest": "opaque:before"
                if each.issue.issue_key in lapsed
                else each.body_digest,
            }
        )
        for each in board
        if each.issue.issue_key not in {unadmitted[0].issue.issue_key}
    )
    findings = tuple(
        SpecFinding(
            issue_id=key,
            defect_class="unsupported-claim",
            evidence="The recorded claim names no supporting observation.",
            role=DefectRole.INSTANCE,
        )
        for key in (found[0].issue.issue_key, found_child[1].issue.issue_key)
    )
    orphan = revision("orphan/check", parent_key="absent/1", issue_labels=["criterion"])

    def organize(revisions, *, admitted=None, open_findings=(), marker=BODY_MARKER):
        return {
            "revisions": revisions,
            "admissions": tuple(gap_admission(each) for each in revisions)
            if admitted is None
            else admitted,
            "open_findings": open_findings,
            "body_marker_key": marker,
        }

    organize_boards = {
        "every arm": organize(board, admitted=admissions, open_findings=findings),
        "at rest": organize(at_rest),
        "empty board": organize(()),
        "refuses a blank marker": organize(at_rest, marker=" "),
        "refuses a revision twice": organize((*at_rest, at_rest[0])),
        "refuses an admission twice": organize(
            at_rest,
            admitted=tuple(gap_admission(each) for each in (*at_rest, at_rest[0])),
        ),
        "refuses a criterion without its parent": organize((*at_rest, orphan)),
    }
    return {
        "subtree gap: every state kind": subtree(kinds, refs),
        "subtree gap: empty board": subtree(()),
        "subtree gap refuses a criterion twice": subtree((kinds[0], kinds[0])),
        "subtree gap refuses a record that is no criterion": subtree(
            (record("plain/1"),)
        ),
        "subtree gap refuses a blank supersession": subtree(
            (kinds[0],), {kinds[0].issue_key: " "}
        ),
        **{
            f"{entry.__name__}: {name}": (
                entry,
                arguments,
                tuple(each.issue for each in arguments["revisions"]),
            )
            for name, arguments in organize_boards.items()
            for entry in (organize_gap, organize_at_rest)
        },
    }


def arithmetic_outcome(entry, arguments, records):
    """What one entry point answers: its records by position, or its refusal.

    A record answered by its key is read at the position of the record
    carrying that key; a subtree read refuses as a scope read error.
    """
    try:
        answer = entry(**arguments)
    except (ValueError, ScopeReadError) as refusal:
        return ("refused", str(refusal))
    if isinstance(answer, bool):
        return ("answered", answer)
    return (
        "answered",
        tuple(
            position
            for item in answer
            for position, each in enumerate(records)
            if each is item or (isinstance(item, str) and each.issue_key == item)
        ),
    )


#: The stamp every record carries in the baseline reading.
BASELINE_STAMP = datetime(2026, 1, 1, tzinfo=UTC)
#: What each case answers over records carrying ``BASELINE_STAMP``.  Written
#: out, so a board that stops reaching the arm it was built for reds.
ARITHMETIC_OUTCOMES = {
    "subtree gap: every state kind": ("answered", (0, 1, 2, 3, 4, 5, 6, 7, 10, 12)),
    "subtree gap: empty board": ("answered", ()),
    "subtree gap refuses a criterion twice": (
        "refused",
        "a criterion identity appears more than once",
    ),
    "subtree gap refuses a record that is no criterion": (
        "refused",
        "gap membership requires a criterion sub-issue",
    ),
    "subtree gap refuses a blank supersession": (
        "refused",
        "a supersession reference must be nonempty",
    ),
    "organize_gap: every arm": ("answered", (4, 6, 8, 10, 12, 14, 16)),
    "organize_at_rest: every arm": ("answered", False),
    "organize_gap: at rest": ("answered", ()),
    "organize_at_rest: at rest": ("answered", True),
    "organize_gap: empty board": ("answered", ()),
    "organize_at_rest: empty board": ("answered", True),
    **{
        f"{entry.__name__}: refuses {what}": ("refused", refusal)
        for what, refusal in {
            "a blank marker": "organize gap requires a body phase marker key",
            "a revision twice": "organize gap requires one revision per issue",
            "an admission twice": "organize gap requires one admission per surface",
            "a criterion without its parent": "organize gap requires each "
            "criterion's parent",
        }.items()
        for entry in (organize_gap, organize_at_rest)
    },
}
#: Each change stamp the records are read under besides the baseline: a trap
#: that raises on any operation, and two readings far apart whose order runs
#: opposite ways across the records, so a filter or a sort keyed on the stamp
#: answers differently under one of them.
STAMP_VARIANTS = ("trapped", "far past, ascending", "far future, descending")


def stamp_variant(variant, reads):
    """The stamp each record carries under *variant*, by the order it is built."""
    if variant == "trapped":
        trap = change_stamp_trap(reads)
        return lambda _position: trap
    if variant == "far past, ascending":
        return lambda position: datetime(1, 1, 1, tzinfo=UTC) + timedelta(days=position)
    return lambda position: (
        datetime(9999, 12, 31, tzinfo=UTC) - timedelta(days=position)
    )


def arithmetic_entry_points():
    """The arithmetic and every definition beside it that refers to it, as objects.

    Found by object in the homes read off ``GAP_ARITHMETIC``, the way the
    call sites are, so a helper added beside the arithmetic that hands on its
    answer is an entry point the trap has to run.
    """
    points = {id(value): value for value in GAP_ARITHMETIC}
    for home in sorted(gap_homes()):
        source = source_tree()[home]
        namespace = module_namespace(home, source)
        for name, _node in referencing_definitions(
            home, ast.parse(source), namespace, wanted=GAP_ARITHMETIC
        ):
            value = functools.reduce(
                getattr, name.split(".")[1:], namespace[name.split(".")[0]]
            )
            points[id(value)] = value
    return points


def test_the_trapped_boards_run_every_entry_point_and_reach_every_arm():
    """The boards the trap runs over are the ones the arithmetic answers.

    Every entry point is run, and nothing else is; each case answers what it
    was built for over the baseline stamp, so a board that stops reaching its
    arm — a refusal where an answer was meant, an empty gap where a member
    was — reds here rather than turning the trap into a vacuous pass.
    """
    points = arithmetic_entry_points()
    cases = arithmetic_cases(lambda _position: BASELINE_STAMP)

    assert points.keys() > {id(value) for value in GAP_ARITHMETIC}
    assert {id(entry) for entry, _arguments, _records in cases.values()} == (
        points.keys()
    )
    assert {
        case: arithmetic_outcome(*arguments) for case, arguments in cases.items()
    } == ARITHMETIC_OUTCOMES


@pytest.mark.parametrize("variant", STAMP_VARIANTS)
@pytest.mark.parametrize("case", sorted(ARITHMETIC_OUTCOMES))
def test_the_arithmetic_answers_alike_whatever_the_change_stamp_holds(case, variant):
    """The arithmetic runs with the change stamp trapped, and never reads it.

    The reach of the Check, shown by running it: ``compute_gap``,
    ``organize_gap`` and every definition beside them that refers to them,
    together with everything they execute — a model method, a helper in a
    module they import, a class pattern — answer every case the same with
    the stamp trapped, far in the past and far in the future, and no
    operation touches the trap.  An identity test against the trap (``is``)
    is no operation it can see, and cannot move an answer either.
    """
    reads = []
    entry, arguments, records = arithmetic_cases(stamp_variant(variant, reads))[case]

    assert arithmetic_outcome(entry, arguments, records) == ARITHMETIC_OUTCOMES[case]
    assert reads == []


def call_site_cases(stamp):
    """Each call site found by object that runs on the arithmetic's fixtures.

    ``case -> (call site, keyword arguments, records)``, read as
    ``arithmetic_cases`` is, every record's change stamp ``stamp(n)``.  The
    arms reached.  ``open_criteria``: open and completed criteria, two of
    each state kind so an order the stamp imposes shows; an empty family;
    and its refusal of a canceled or duplicate criterion with no supersession
    reader.  ``SubtreeClosure.open_criterion_keys``, run on a closure over a
    subject and a criterion of every state kind.  ``stage_unlabelled``: a
    member owing the marker, one carrying it, and a criterion and a tracker
    record that owe none; and its two refusals, a blank marker and a member
    twice.
    """
    built = iter(range(1_000))

    def record(key, **changes):
        fixture = issue(key, f"Body for {key}", **changes)
        return fixture.model_copy(update={"updated_at": stamp(next(built))})

    kinds = tuple(
        record(
            f"criterion/{kind.value}/{copy}",
            parent_key=SUBJECT,
            issue_labels=["criterion"],
            state_kind=kind,
            state_name=kind.value,
        )
        for kind in WorkflowStateKind
        for copy in (1, 2)
    )
    settled = tuple(
        each
        for each in kinds
        if each.state_kind
        not in {WorkflowStateKind.CANCELED, WorkflowStateKind.DUPLICATE}
    )
    subject = record(SUBJECT)
    ref = ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT)
    closure = SubtreeClosure(
        facts={each.issue_key: each for each in (subject, *kinds)}, ref=ref
    )
    members = (
        subject,
        record("marked/1", issue_labels=[BODY_MARKER]),
        record("tracker/1", issue_labels=["tracker"]),
        kinds[0],
        record("unmarked/1"),
    )
    return {
        "open_criteria: open and completed criteria": (
            open_criteria,
            {"criteria": settled, "ref": ref},
            settled,
        ),
        "open_criteria: an empty family": (
            open_criteria,
            {"criteria": (), "ref": ref},
            (),
        ),
        "open_criteria refuses an unresolved supersession": (
            open_criteria,
            {"criteria": kinds, "ref": ref},
            kinds,
        ),
        "SubtreeClosure.open_criterion_keys: every state kind": (
            SubtreeClosure.open_criterion_keys,
            {"self": closure},
            (subject, *kinds),
        ),
        "stage_unlabelled: owing, carrying and exempt members": (
            stage_unlabelled,
            {"issues": members, "marker": BODY_MARKER},
            members,
        ),
        "stage_unlabelled refuses a blank marker": (
            stage_unlabelled,
            {"issues": members, "marker": " "},
            members,
        ),
        "stage_unlabelled refuses a member twice": (
            stage_unlabelled,
            {"issues": (*members, members[0]), "marker": BODY_MARKER},
            members,
        ),
    }


#: What each call-site case answers over records carrying ``BASELINE_STAMP``,
#: written out as ``ARITHMETIC_OUTCOMES`` is.
CALL_SITE_OUTCOMES = {
    "open_criteria: open and completed criteria": (
        "answered",
        (0, 1, 2, 3, 4, 5, 6, 7),
    ),
    "open_criteria: an empty family": ("answered", ()),
    "open_criteria refuses an unresolved supersession": (
        "refused",
        "criterion supersession resolution is unavailable: criterion/canceled/1, "
        "criterion/canceled/2, criterion/duplicate/1, criterion/duplicate/2 "
        "(scope: issue:subject/42)",
    ),
    "SubtreeClosure.open_criterion_keys: every state kind": (
        "answered",
        (1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 13, 14),
    ),
    "stage_unlabelled: owing, carrying and exempt members": ("answered", (0, 4)),
    "stage_unlabelled refuses a blank marker": (
        "refused",
        "a stage roster requires a nonempty marker key",
    ),
    "stage_unlabelled refuses a member twice": (
        "refused",
        "a stage roster requires one record per issue",
    ),
}
#: The call sites found by object that the arithmetic's fixtures cannot run,
#: each with why; the trap does not reach them, and the call-site scan above
#: is what reads them.
CALL_SITES_NOT_RUN = {
    ("chains/organize.py", "OrganizeAdmission.is_live"): "Async; reads the "
    "current revision through the tracker port.",
    ("composition/organize.py", "build_scope_organizer"): "The composition "
    "root: it reads stage_rows over the configured mandates while it wires the "
    "organizer, and needs the whole application configuration and its ports.",
    ("services/mandate_graph.py", "observe_ruling_growth"): "Async; reads "
    "criteria and ruling projections through the tracker port.",
    ("services/organize_owner.py", "OrganizeOwner._proof_live"): "Async; a "
    "method of the organize service, reading a snapshot through its ports.",
    ("services/organize_owner.py", "OrganizeOwner._roster"): "A method of the "
    "organize service, run on its resolved mandate and configuration.",
    ("services/organize_owner.py", "OrganizeOwner._route"): "Async; a method "
    "of the organize service, routing through its ports.",
    ("services/organize_owner.py", "OrganizeOwner.run"): "Async; the organize "
    "service's whole pass over its tracker, agent and gate ports.",
    ("services/run_shape.py", "read_barren_tick"): "Async; reads criteria "
    "through the tracker port.",
    ("services/scope_tally.py", "observe_scope_tally"): "Async; reads the "
    "scope through the tracker port.",
}


def call_site_objects(sources):
    """Each call site found by object, as the object its module binds it to."""
    found = {}
    for module, name in arithmetic_call_sites(sources):
        head, *rest = name.split(".")
        found[(module, name)] = functools.reduce(
            lambda value, part: getattr(value, part, None),
            rest,
            module_namespace(module, sources[module]).get(head),
        )
    return found


def test_every_call_site_the_fixtures_can_run_is_run_under_the_trap():
    """The call sites outside the entry points are run where the fixtures can run them.

    Every call site found by object that is not an entry point the trap
    already runs is either a case of ``call_site_cases`` — ``open_criteria``,
    ``SubtreeClosure.open_criterion_keys`` and ``stage_unlabelled`` — or named
    in ``CALL_SITES_NOT_RUN`` with why: ``OrganizeAdmission.is_live``,
    ``build_scope_organizer``, ``observe_ruling_growth``, the organize
    service's ``_proof_live``, ``_roster``, ``_route`` and ``run``,
    ``read_barren_tick`` and ``observe_scope_tally``, each of which needs a
    tracker port, a service instance or the composition's wiring.  A new call
    site reds here until it is one or the other.  Each case answers what it
    was built for over the baseline stamp.
    """
    points = arithmetic_entry_points()
    cases = call_site_cases(lambda _position: BASELINE_STAMP)
    run = {id(entry) for entry, _arguments, _records in cases.values()}
    sites = call_site_objects(source_tree())
    outside = {site for site, value in sites.items() if id(value) not in points}

    assert run <= {id(sites[site]) for site in outside}
    assert {site for site in outside if id(sites[site]) not in run} == (
        CALL_SITES_NOT_RUN.keys()
    )
    assert {
        case: arithmetic_outcome(*arguments) for case, arguments in cases.items()
    } == CALL_SITE_OUTCOMES


@pytest.mark.parametrize("variant", STAMP_VARIANTS)
@pytest.mark.parametrize("case", sorted(CALL_SITE_OUTCOMES))
def test_a_call_site_answers_alike_whatever_the_change_stamp_holds(case, variant):
    """A call site the fixtures can run never reads the change stamp.

    Run the way the arithmetic is: with the stamp trapped, far in the past
    and far in the future, each answers the same and no operation touches
    the trap.
    """
    reads = []
    entry, arguments, records = call_site_cases(stamp_variant(variant, reads))[case]

    assert arithmetic_outcome(entry, arguments, records) == CALL_SITE_OUTCOMES[case]
    assert reads == []


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
    "module_named_as_package_colon_module": "import pkgutil\n"
    "\n"
    "def plan(criteria, since):\n"
    "    arithmetic = pkgutil.resolve_name('kodezart.domain:gap')\n"
    "    return arithmetic.compute_gap([c for c in criteria{READ}])\n",
    "object_named_as_module_colon_attr": "import pkgutil\n"
    "\n"
    "def plan(criteria, since):\n"
    "    still_open = pkgutil.resolve_name('kodezart.domain.gap:in_gap')\n"
    "    return [c for c in criteria if still_open(c, supersession_ref=None){READ}]\n",
    "two_hops": "from kodezart.services.planted_helper import window\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return [c for c in window(criteria){READ}]\n",
    "relative_import_in_a_package_init": "from kodezart.domain.planted import window\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return [c for c in window(criteria, supersession_refs={}){READ}]\n",
}
#: The modules the routes above import that are not in the tree: the helper
#: the two-hop row imports, a module of its own that reaches the arithmetic
#: and reads nothing, and a package whose ``__init__`` re-exports the
#: arithmetic through a relative import, resolved against the package itself.
PLANTED_HELPERS = {
    "services/planted_helper.py": "from kodezart.domain.issue_tree import"
    " open_criteria\n"
    "\n"
    "def window(criteria):\n"
    "    return open_criteria(criteria)\n",
    "domain/planted/__init__.py": "from ..gap import compute_gap as window\n",
}


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
    sources = {**source_tree(), **PLANTED_HELPERS, "services/planted.py": planted}

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


@pytest.mark.parametrize("field", sorted(CHANGE_STAMP_FIELDS))
@pytest.mark.parametrize(
    "form", ["attribute", "name", "keyword", "wire_key", "class_pattern"]
)
def test_change_stamp_detector_flags_a_gap_site_that_reads_the_field(field, form):
    snippet = {
        "attribute": f"def gap(rows, since):\n"
        f"    return [row for row in rows if row.{field} > since]\n",
        "name": f"def gap(rows, {field}):\n"
        f"    return [row for row in rows if row.body_digest != {field}]\n",
        "keyword": f"def gap(tracker):\n    return tracker.query({field}=MARK)\n",
        "wire_key": f"def gap(row):\n    return row['{field}']\n",
        "class_pattern": f"def gap(row, since):\n"
        f"    match row:\n"
        f"        case Row({field}=stamp):\n"
        f"            return stamp > since\n",
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
