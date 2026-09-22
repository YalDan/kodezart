"""Actual tracker-to-executor admission calls preserve the fresh-source boundary."""

import ast
import inspect
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

import pytest
import structlog.testing
from pydantic import ValidationError

from kodezart.chains.organize import OrganizeAdmission
from kodezart.core.errors import NoStructuredOutputError, RateLimitedSoftFailureError
from kodezart.domain.errors import OrganizeAdmissionIdentityError
from kodezart.domain.organize import organize_at_rest, organize_gap
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
    bound_names,
    call_sites,
    parsed,
    reaches,
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
GAP_ARITHMETIC_NAMES = frozenset(
    {"compute_gap", "in_gap", "organize_gap", "SubtreeClosure"}
)
#: Every supplied module the derivation below discovers, re-measured off the
#: tree rather than chosen: the seven that spell a seed of the arithmetic, and
#: the five that reach one only through a helper handing back a value grown
#: from the gap's answer.
GAP_COMPUTATION_MODULES = frozenset(
    {
        "domain/gap.py",
        "domain/organize.py",
        "domain/issue_tree.py",
        "chains/scope_walker.py",
        "services/mandate_graph.py",
        "services/organize_owner.py",
        "services/run_shape.py",
        "composition/organize.py",
        "composition/supervisor.py",
        "services/barren_record_signals.py",
        "services/scope_dispatcher.py",
        "services/scope_runtime.py",
    }
)


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


def _handed_back(statement):
    """Every value *statement* hands its caller, by return or by yield."""
    return tuple(
        node.value
        for node in ast.walk(statement)
        if isinstance(node, ast.Return | ast.Yield | ast.YieldFrom)
        and node.value is not None
    )


def _hands_back(tree, statement, names):
    """Whether *statement* hands back a value reaching one of *names*.

    Read against the whole enclosing definition, not against the handed-back
    expression alone: the ordinary spelling of a wrapper binds the
    arithmetic's answer to a local and hands the local back, and a local alias
    of the arithmetic is called under a word the module never imports, so a
    walk that sees only the ``return`` recognises a wrapper by the spelling
    inside it rather than by what it hands back.  The definition's own
    bindings are therefore grown from *names* to a fixed point, so a local
    bound from the answer, and a local bound from that local, are names of it
    here; each value is read beside the module's imports, so an aliased or
    routed spelling resolves the way the whole-module walk resolves it; and a
    ``return``, a ``yield`` or a ``yield from`` of any of them hands it back.

    A consequence, stated because it widens the surface: a definition that
    computes the gap on the way to its own answer hands that answer back
    whenever the answer is grown from the gap's, so a reading derived from the
    open set carries the guard to whoever reads the reading.  A definition
    that calls the arithmetic and hands back nothing grown from it — a
    refusal, a count of something else, a value bound before the call — binds
    no name this walk follows and is no wrapper of it.
    """
    imports = [
        node for node in tree.body if isinstance(node, ast.Import | ast.ImportFrom)
    ]

    def beside_imports(value):
        return ast.Module(body=[*imports, ast.Expr(value=value)], type_ignores=[])

    handed = bound_names(
        ast.Module(body=[*imports, *statement.body], type_ignores=[]),
        yields=lambda value, bound: bool(reaches(beside_imports(value), names=bound)),
        seeds=names,
    )
    return any(
        reaches(beside_imports(value), names=handed)
        for value in _handed_back(statement)
    )


def gap_wrappers(trees):
    """Every top-level definition of *trees* that hands back the gap's answer.

    Derived, not listed, on the standard ``gap_callees`` already sets for the
    gap home: a helper that returns what the arithmetic returned is a gap
    computation whatever it is named, and a module consuming the gap through
    one spells no seed of its own.

    One hop out of the arithmetic's own modules and no further.  A closure
    over the whole call graph would make every caller of every such helper a
    gap site, reach the adapters and readers that legitimately expose the
    change stamp, and turn the guard red on the shape the Check explicitly
    allows.  One hop is also why re-discovery settles at once: the enlarged
    seed set is fixed before any module joins, so a module joining adds no
    seed and the round after it finds nothing new.
    """
    return frozenset(
        statement.name
        for tree in trees
        for statement in tree.body
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef)
        and statement.name not in GAP_ARITHMETIC_NAMES
        and _hands_back(tree, statement, GAP_ARITHMETIC_NAMES)
    )


def gap_computation_sites(sources):
    """Every supplied module that defines or reaches the gap arithmetic.

    Reached under any spelling: the imported name, an ``as`` alias, a module
    route, an assignment alias, a declaration, a bare or attribute spelling.
    A string constant is not a route: a module whose only mention of a seed is
    a quoted word — a vocabulary label, a message, a serialised key — reaches
    no gap arithmetic and is no gap site, which is what keeps the module list
    above an upper bound rather than a name search.  That negative is pinned by
    an injected module below and not by whichever module of the tree happens to
    quote a seed today, because none of them need to.

    Then the wrappers: a module that calls a helper handing back the gap's own
    answer consumes the gap without spelling any seed, so it joins too.  Those
    helpers are derived one hop out of the modules above, and a call of one is
    read through the resolver, so an aliased or routed call counts and a
    parameter or a field that merely shares a helper's name does not.

    Two shapes are no route here, neither of them in the package at head: a
    gap consumer handed a ``SubtreeClosure`` on an unannotated parameter, whose
    root is the resolver's handed-parameter walk and is not wired into this
    guard; and a parameter annotated with the quoted string
    ``'SubtreeClosure'``, since a string constant is not a route, while a
    parameter annotated with the type itself is discovered.
    """
    trees = parsed(sources)
    found = {
        relative: tree
        for relative, tree in trees.items()
        if reaches(tree, names=GAP_ARITHMETIC_NAMES)
    }
    for site in call_sites(trees, names=gap_wrappers(found.values())):
        found.setdefault(site.module, trees[site.module])
    return found


def gap_sites_reading_the_change_stamp(sources):
    """The discovered gap sites that reach the tracker's change-timestamp field."""
    return {
        relative: change_stamp_reads(tree)
        for relative, tree in gap_computation_sites(sources).items()
        if change_stamp_reads(tree)
    }


def test_no_gap_computation_call_site_reads_the_tracker_change_timestamp():
    discovered = gap_computation_sites(source_tree())
    assert discovered
    assert {
        "domain/gap.py",
        "domain/organize.py",
        "domain/issue_tree.py",
    } <= discovered.keys()
    assert discovered.keys() <= GAP_COMPUTATION_MODULES
    assert gap_sites_reading_the_change_stamp(source_tree()) == {}
    assert change_stamp_reads(ast.parse(inspect.getsource(organize_gap))) == set()


def test_the_discovered_gap_sites_are_the_upper_bound_exactly():
    """The derived surface is the whole bound, not merely inside it.

    The guard above bounds the discovered set from above and holds a
    three-module floor, so a seed dropped from ``GAP_ARITHMETIC_NAMES`` can
    take a gap consumer off the scanned surface while both still hold:
    ``chains/scope_walker.py`` is reached by ``SubtreeClosure`` alone and
    consumes the gap through it. Equality is what reds then.
    """
    assert gap_computation_sites(source_tree()).keys() == GAP_COMPUTATION_MODULES


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


@pytest.mark.parametrize("reads", [True, False])
@pytest.mark.parametrize(
    ("route", "body"),
    [
        (
            "aliased_import",
            "from kodezart.domain.gap import compute_gap as gap_of\n"
            "\n"
            "def plan(criteria, since):\n"
            "    return gap_of([c for c in criteria{READ}])\n",
        ),
        (
            "module_attribute",
            "import kodezart.domain.gap as gap_module\n"
            "\n"
            "def plan(criteria, since):\n"
            "    return gap_module.compute_gap([c for c in criteria{READ}])\n",
        ),
    ],
)
def test_a_gap_site_reached_under_another_spelling_is_discovered_and_scanned(
    route, body, reads
):
    """A module that names the arithmetic under another spelling is a gap site.

    Discovery that collected bare words alone answered an aliased import and
    a module route with silence, so a change-timestamp read behind either
    spelling was never scanned.  Both rows are discovered here whether or not
    they read the field: the read-free rows redden the moment discovery stops
    resolving the spelling, because the planted module drops out of the
    discovered set.
    """
    planted = body.replace("{READ}", " if c.updated_at > since" if reads else "")
    sources = {**source_tree(), "services/planted.py": planted}

    assert "services/planted.py" in gap_computation_sites(sources)
    assert gap_sites_reading_the_change_stamp(sources) == (
        {"services/planted.py": {"updated_at"}} if reads else {}
    )


def test_a_module_that_only_quotes_a_seed_name_is_not_a_gap_site():
    """A quoted seed name is a value, never a route into the arithmetic.

    This is what bounds the discovered surface from above: were a string
    constant a route, every module carrying a vocabulary label, a log field or
    a serialised key that happens to spell a seed would be scanned, and the
    module bound would stop being a statement about what computes the gap.
    Planted rather than read off a module of the tree, so the negative holds
    whatever the tree's own prose happens to quote.
    """
    sources = source_tree()
    sources["services/planted.py"] = (
        "GAP_LABEL = 'in_gap'\n"
        "COLUMNS = ('compute_gap', 'organize_gap', 'SubtreeClosure')\n"
        "\n"
        "def label(row):\n"
        "    return {GAP_LABEL: row} if GAP_LABEL in COLUMNS else {}\n"
    )

    assert "services/planted.py" not in gap_computation_sites(sources)
    assert gap_sites_reading_the_change_stamp(sources) == {}


def test_the_gap_wrappers_at_head_are_the_helpers_that_hand_back_its_answer():
    """The wrapper derivation is not vacuous: the tree already holds five.

    Two hand back what the arithmetic returned — the subtree's open criteria
    and the organize gap's emptiness.  The other three hand back a value grown
    from it: a ready set carrying each lane's own gap, and two alarm readings
    computed out of the open-key set the arithmetic answered.  Each carries the
    guard to whoever calls it, which is why the module bound below grew when
    the walk began following the answer through a definition's own bindings.  A
    sixth helper written beside the arithmetic reds here, which is where a new
    gap surface should be read rather than in the module bound.
    """
    discovered = gap_computation_sites(source_tree())

    assert gap_wrappers(discovered.values()) == frozenset(
        {
            "open_criteria",
            "organize_at_rest",
            "read_scope_ready",
            "read_barren_tick",
            "observe_ruling_growth",
        }
    )


#: The ways a helper can hand the gap's answer back: straight out of the
#: call, out of a local the call was bound to, out of a local alias of the
#: arithmetic called under a word the module never imports, and out of a
#: generator.  The middle two are the spelling the package itself writes — a
#: local bound to the arithmetic, then handed on — and reading the whole
#: definition rather than the returned expression alone is what sees them.
WRAPPER_SHAPES = {
    "returned call": "    return compute_gap(criteria, supersession_refs={})\n",
    "returned local": "    answer = compute_gap(criteria, supersession_refs={})\n"
    "    return answer\n",
    "aliased arithmetic": "    arithmetic = compute_gap\n"
    "    return arithmetic(criteria, supersession_refs={})\n",
    "yielded answer": "    yield from compute_gap(criteria, supersession_refs={})\n",
}


@pytest.mark.parametrize("reads", [True, False])
@pytest.mark.parametrize("shape", sorted(WRAPPER_SHAPES))
def test_a_module_reaching_the_gap_through_a_wrapper_is_discovered_and_scanned(
    shape, reads
):
    """A consumer of a helper that hands back the gap's answer is a gap site.

    Discovery that collected the arithmetic's own names alone answered such a
    consumer with silence: the consuming module spells no seed, nothing
    scanned it, and a change-timestamp read behind the helper was never seen.
    The wrapper is planted beside the arithmetic and consumed from a module of
    its own, under a name the guard cannot have been written around.

    One row per shape the helper can hand the answer back in, each with and
    without the read: the reading rows must redden the guard, and the
    read-free twins pin the discovery itself, so a derivation that stopped
    resolving the helper reads as a module missing from the discovered set
    rather than as one more green run.
    """
    sources = source_tree()
    sources["domain/gap.py"] += f"\n\ndef gap_since(criteria):\n{WRAPPER_SHAPES[shape]}"
    read = " if c.updated_at > since" if reads else ""
    sources["services/planted.py"] = (
        "from kodezart.domain.gap import gap_since\n"
        "\n"
        "def plan(criteria, since):\n"
        f"    return [c for c in gap_since(criteria){read}]\n"
    )

    assert "gap_since" in gap_wrappers([ast.parse(sources["domain/gap.py"])])
    assert "services/planted.py" in gap_computation_sites(sources)
    assert gap_sites_reading_the_change_stamp(sources) == (
        {"services/planted.py": {"updated_at"}} if reads else {}
    )


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
