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
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import AgentEvent, RateLimitWarningEvent, ResultEvent
from kodezart.types.domain.organize import (
    AdmissionJudgment,
    AdmissionResult,
    AdmissionVerdict,
    OrganizeAdmissionRequest,
    RefusalKind,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import (
    NO_SUBAGENTS,
    UNCONFIGURED_SESSION_POLICY,
    AgentDefinition,
    SessionPolicy,
)
from kodezart.types.domain.tracker import (
    IssuePriority,
    IssueRelation,
    IssueRelationKind,
    TrackerIssue,
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
from tests.prompts.sets import OPUS_SET, V5_SET
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
        permission_mode: str,
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
            "Apply the selected rubric.",
            "unsupported-claim",
            SUBJECT,
            BASE,
        ):
            assert body in prompt
        for excluded in (
            "Unrelated issue stays outside the prompt.",
            "Unlabelled child is not a criterion.",
            "Author rationale must never be forwarded.",
            "previous-agent-session",
        ):
            assert excluded not in prompt
        assert call["session_id"] is None
        assert call["session_type"] is SessionType.ORGANIZE_PASS
        assert call["permission_mode"] == "plan"
        assert set(call["allowed_tools"]) == {"Read", "Glob", "Grep", "Bash"}
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
        assert await admission.is_live(results[key]) is (key != "criterion/a")
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
    await source.update_issue(issue_key=issue_key, title="A later title")
    assert await admission.is_live(judged) is True
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
        {**output, "admitted_body_digest": revision.body_digest}
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
            permission_mode="plan",
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
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"stream", "stream_in_workspace", "stream_workflow"}
    ]
    assert [node.func.attr for node in calls] == ["stream_in_workspace"]
    keywords = {argument.arg: argument.value for argument in calls[0].keywords}
    assert ast.unparse(keywords["session_type"]) == "SessionType.ORGANIZE_PASS"
    assert ast.unparse(keywords["permission_mode"]) == "EVAL_PERMISSION_MODE"
    assert ast.unparse(keywords["session_id"]) == "None"
    assert ast.unparse(keywords["agents"]) == "NO_SUBAGENTS"
