"""Actual tracker source, native identities and fresh feasibility sessions."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from kodezart.chains.tracker_feasibility import TrackerFeasibilityValidator
from kodezart.core.config import AppConfig
from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS
from kodezart.core.errors import NoStructuredOutputError
from kodezart.domain.errors import (
    CriteriaFanInError,
    InvalidFireCriterionError,
    TrackerFeasibilityReadError,
    UngroundedVerdictError,
)
from kodezart.types.domain.agent import (
    TRACKER_CRITERIA_VALIDATION_SCHEMA,
    TicketDraftOutput,
)
from kodezart.types.domain.criteria import CriterionVerdict
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.subagents import NO_SUBAGENTS
from kodezart.types.domain.tracker_feasibility import TrackerFeasibilityRequest
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
    FakeGitService,
    FakeMcpIssue,
    FakeRepoCache,
    FakeWorkspaceProvider,
)
from tests.prompts.sets import OPUS_SET, V5_SET
from tests.prompts.test_prompt_wiring import load_registry
from tests.tracker.conftest import FIXTURE_NOW, fixture_server
from tests.tracker.test_audit_claim import result_event

SUBJECT = "subject/42"
KEYS = ("condition/café", "condition/二")
DONE = "condition/prior"
HEAD = "a" * 40
BODY = "**Outcome:** Literal source {{not_interpolated}}.\r\nKeep this capture."
REQUEST = TrackerFeasibilityRequest(
    issue_key=SUBJECT,
    repo_url="https://forge.invalid/team/project",
    head_sha=HEAD,
    cache_key="configured-cache",
)


def finding(key, **changes):
    return {
        "criterionId": key,
        "verdict": "feasible",
        "smallestRepair": "none",
        **changes,
    }


def output(**changes):
    return {"findings": [finding(key) for key in KEYS], **changes}


@pytest.fixture
def server():
    server = fixture_server()
    server.issues[SUBJECT] = FakeMcpIssue(
        id=SUBJECT, description=BODY, updated_at=FIXTURE_NOW
    )
    server.state_types["Todo"] = "unstarted"
    for index, key in enumerate((*KEYS, DONE)):
        server.issues[key] = FakeMcpIssue(
            id=key,
            created_at=FIXTURE_NOW + timedelta(seconds=index),
            updated_at=FIXTURE_NOW + timedelta(seconds=index),
            parent_id=SUBJECT,
            labels=["acceptance-condition"],
            description=(
                f"**Check:** Current check for {key}.\n"
                "**Do:** AUTHOR_RATIONALE\n**Evidence:** OLD_VERDICT"
            ),
            status="Done" if key == DONE else "Todo",
            status_type="completed" if key == DONE else "unstarted",
        )
    return server


class Runner(FakeAgentRunner):
    def __init__(self):
        super().__init__([])
        self.answers = [output()]
        self.arguments = []
        self.during = None
        self.error = False

    async def stream_in_workspace(self, **kwargs):
        self.arguments.append(kwargs)
        if self.during:
            await self.during()
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        yield result_event(
            subtype="success", is_error=self.error, structured_output=answer
        )


@pytest.fixture
async def setup(tracker):
    runner, git, cache, workspace = (
        Runner(),
        FakeGitService(),
        FakeRepoCache(),
        FakeWorkspaceProvider(),
    )

    def build(set_name=V5_SET, **changes):
        values = {
            "tracker": tracker,
            "cache": cache,
            "git": git,
            "workspace": workspace,
            "runner": runner,
            "prompts": load_registry(default_set=set_name),
            "skills": SUPPRESS_ALL_SKILLS,
            "config": AppConfig(_env_file=None, fan_in_max_attempts=2),
        }
        return TrackerFeasibilityValidator(**{**values, **changes})

    return build, runner, git, cache, workspace


@pytest.mark.parametrize("family", [OPUS_SET, V5_SET])
async def test_actual_tracker_family_reaches_fresh_native_key_session(
    setup, tracker, tracker_writes, family, monkeypatch
):
    build, runner, _git, cache, workspace = setup
    read = AsyncMock(wraps=tracker.read_fire_spec)
    monkeypatch.setattr(tracker, "read_fire_spec", read)
    acquire = AsyncMock(wraps=workspace.acquire)
    monkeypatch.setattr(workspace, "acquire", acquire)

    def no_draft(*args, **kwargs):
        raise AssertionError("tracker input cannot construct an authored draft")

    monkeypatch.setattr(TicketDraftOutput, "__init__", no_draft)
    monkeypatch.setattr(TicketDraftOutput, "model_validate", no_draft)
    writes = tracker_writes()
    observed = await build(family).validate(REQUEST)
    assert observed.spec.body == BODY
    assert observed.spec.read_at_version == FIXTURE_NOW.isoformat()
    assert tuple(row.issue_key for row in observed.criteria) == tuple(
        sorted((*KEYS, DONE))
    )
    assert tuple(row.criterion_id for row in observed.judgment.findings) == KEYS
    assert all(row.verdict is CriterionVerdict.feasible for row in observed.derivations)
    assert observed.head_sha == HEAD and observed.correction is None
    read.assert_awaited_once_with(issue_key=SUBJECT)
    args = runner.arguments[0]
    assert BODY in args["prompt"]
    assert all(f"{key} Current check for {key}." in args["prompt"] for key in KEYS)
    assert DONE not in args["prompt"]
    assert (
        "AUTHOR_RATIONALE" not in args["prompt"] and "OLD_VERDICT" not in args["prompt"]
    )
    assert (
        args["session_id"] is None and args["session_type"] is SessionType.TICKET_FIRE
    )
    assert args["permission_mode"] == EVAL_PERMISSION_MODE
    assert args["allowed_tools"] == list(EVAL_TOOLS) and args["agents"] == NO_SUBAGENTS
    assert args["output_format"]["schema"] == TRACKER_CRITERIA_VALIDATION_SCHEMA
    acquire.assert_awaited_once_with(
        repo_path="/tmp/fake-cache", ref=HEAD, create_branch=False
    )
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")
    assert cache.calls == [{"url": REQUEST.repo_url, "cache_key": REQUEST.cache_key}]
    assert tracker_writes() == writes


@pytest.mark.parametrize(
    "verdict,repair,evidence",
    [
        ("feasible", "none", {}),
        (
            "infeasible",
            "criterion_text",
            {"refutation": "The named switch lacks this arm."},
        ),
        (
            "unverifiable",
            "environment_supply",
            {"missingResource": "The test database."},
        ),
    ],
)
async def test_shared_classifier_preserves_all_three_native_judgments(
    setup, tracker_writes, verdict, repair, evidence
):
    build, runner, *_ = setup
    runner.answers = [
        output(
            findings=[
                finding(key, verdict=verdict, smallestRepair=repair, **evidence)
                for key in KEYS
            ]
        )
    ]
    before = tracker_writes()
    observed = await build().validate(REQUEST)
    assert [row.verdict.value for row in observed.derivations] == [verdict, verdict]
    assert tracker_writes() == before


async def test_inflight_subject_amendment_does_not_reread_or_replace_capture(
    setup, tracker, monkeypatch
):
    build, runner, *_ = setup

    async def amend():
        await tracker.update_issue(issue_key=SUBJECT, body="later subject text")

    runner.during = amend
    read = AsyncMock(wraps=tracker.read_fire_spec)
    monkeypatch.setattr(tracker, "read_fire_spec", read)
    observed = await build().validate(REQUEST)
    assert observed.spec.body == BODY and BODY in runner.arguments[0]["prompt"]
    read.assert_awaited_once()


@pytest.mark.parametrize(
    "body",
    [
        "**Evidence:** nothing",
        "**Check:**\n**Do:** empty",
        "**Check:** <!-- hidden -->",
    ],
)
async def test_missing_check_refuses_before_any_session_or_workspace(
    setup, tracker, body
):
    build, runner, _, cache, workspace = setup
    await tracker.update_issue(issue_key=KEYS[0], body=body)
    with pytest.raises(InvalidFireCriterionError):
        await build().validate(REQUEST)
    assert not runner.arguments and not cache.calls and not workspace.calls


@pytest.mark.parametrize(
    "damage", ["key", "parent", "membership", "duplicate", "order"]
)
async def test_mismatched_full_family_refuses_before_git(
    setup, tracker, monkeypatch, damage
):
    build, runner, _, cache, workspace = setup
    rows = list(await tracker.read_criteria(issue_key=SUBJECT))
    if damage == "duplicate":
        rows[1] = rows[0]
    elif damage == "order":
        rows.reverse()
    else:
        field, value = {
            "key": ("issue_key", "different"),
            "parent": ("parent_key", "elsewhere"),
            "membership": ("issue_labels", frozenset()),
        }[damage]
        rows[0] = rows[0].model_copy(update={field: value})
    monkeypatch.setattr(tracker, "read_criteria", AsyncMock(return_value=rows))
    with pytest.raises(TrackerFeasibilityReadError):
        await build().validate(REQUEST)
    assert not runner.arguments and not cache.calls and not workspace.calls


@pytest.mark.parametrize(
    "damage", ["body", "state", "reparent", "workspace", "dirty", "cancel"]
)
async def test_mid_session_drift_and_cancellation_release_workspace(
    setup, tracker, monkeypatch, damage
):
    build, runner, git, _, workspace = setup

    async def change():
        if damage == "body":
            await tracker.update_issue(issue_key=KEYS[0], body="**Check:** changed")
        elif damage == "state":
            await tracker.restore_workflow_state(issue_key=KEYS[0], state_name="Done")
        elif damage == "reparent":
            original = tracker.read_criteria

            async def moved(**kwargs):
                rows = list(await original(**kwargs))
                rows[0] = rows[0].model_copy(update={"parent_key": "elsewhere"})
                return rows

            monkeypatch.setattr(tracker, "read_criteria", moved)
        elif damage == "workspace":
            monkeypatch.setattr(git, "current_sha", AsyncMock(return_value="other"))
        elif damage == "dirty":
            git.has_changes_result = True
        else:
            raise asyncio.CancelledError

    runner.during = change
    error = (
        asyncio.CancelledError if damage == "cancel" else TrackerFeasibilityReadError
    )
    with pytest.raises(error):
        await build().validate(REQUEST)
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


async def test_dirty_initial_workspace_refuses_before_session(setup):
    build, runner, git, _, workspace = setup
    git.has_changes_result = True
    with pytest.raises(TrackerFeasibilityReadError, match="contains changes"):
        await build().validate(REQUEST)
    assert not runner.arguments
    assert workspace.calls[-1][0] == "release"


@pytest.mark.parametrize(
    "damage",
    ["missing", "unknown", "duplicate", "foreign-conflict", "ungrounded", "error"],
)
async def test_invalid_judgment_never_becomes_a_validation(setup, damage):
    build, runner, _, _, workspace = setup
    if damage == "missing":
        runner.answers = [output(findings=[finding(KEYS[0])])]
    elif damage == "unknown":
        runner.answers = [output(findings=[finding("AC-1"), finding(KEYS[1])])]
    elif damage == "duplicate":
        runner.answers = [output(findings=[finding(KEYS[0]), finding(KEYS[0])])]
    elif damage == "foreign-conflict":
        runner.answers = [
            output(
                contradictions=[
                    {"criterionIds": [KEYS[0], "AC-1"], "explanation": "foreign"}
                ]
            )
        ]
    elif damage == "ungrounded":
        runner.answers = [
            output(
                findings=[
                    finding(
                        key,
                        verdict="infeasible",
                        smallestRepair="criterion_text",
                        refutation="An argued expense",
                        costClaim={"assertion": "looks expensive"},
                    )
                    for key in KEYS
                ]
            )
        ]
    else:
        runner.error = True
    error = (
        UngroundedVerdictError
        if damage == "ungrounded"
        else NoStructuredOutputError
        if damage == "error"
        else CriteriaFanInError
    )
    with pytest.raises(error):
        await build().validate(REQUEST)
    assert len(runner.arguments) == (1 if damage == "error" else 2)
    assert workspace.calls[-1][0] == "release"


async def test_corrected_native_finding_keeps_ids_order_and_fresh_context(setup):
    build, runner, *_ = setup
    runner.answers = [
        output(findings=[finding("AC-1")]),
        output(findings=[finding(key) for key in reversed(KEYS)]),
    ]
    observed = await build().validate(REQUEST)
    assert tuple(row.criterion_id for row in observed.judgment.findings) == KEYS
    assert observed.correction.attempts == 2
    assert all(args["session_id"] is None for args in runner.arguments)


async def test_no_todo_children_requires_no_session_and_keeps_prior_states(
    setup, tracker, tracker_writes
):
    build, runner, *_ = setup
    for key in KEYS:
        await tracker.restore_workflow_state(issue_key=key, state_name="Done")
    before = tracker_writes()
    observed = await build().validate(REQUEST)
    assert observed.judgment is None and observed.derivations == ()
    assert not runner.arguments and tracker_writes() == before


@pytest.mark.parametrize("field", ["spec", "criteria", "body", "session_id"])
def test_request_cannot_carry_side_channel_source_or_prior_context(field):
    with pytest.raises(ValidationError):
        TrackerFeasibilityRequest.model_validate(
            {**REQUEST.model_dump(), field: "invented"}
        )


async def test_actual_agent_service_forwards_native_schema_and_fresh_plan_session(
    setup,
):
    from kodezart.services.agent_service import AgentService
    from tests.fakes import FakeAgentExecutor

    build, _, _, _, workspace = setup

    class NativeExecutor(FakeAgentExecutor):
        def _is_criteria_validation_schema(self, output_format):
            return False  # Use the scripted native response, not the authored fixture.

    executor = NativeExecutor(
        [result_event(subtype="success", structured_output=output())]
    )
    service = AgentService(
        executor=executor, workspace=workspace, git_base_url="https://forge.invalid"
    )
    observed = await build(runner=service).validate(REQUEST)
    assert tuple(row.criterion_id for row in observed.judgment.findings) == KEYS
    (call,) = executor.calls
    assert call["session_id"] is None and call["cwd"] == "/tmp/fake-workspace"
    assert call["permission_mode"] == EVAL_PERMISSION_MODE
    assert call["allowed_tools"] == list(EVAL_TOOLS)
    assert call["output_format"]["schema"] == TRACKER_CRITERIA_VALIDATION_SCHEMA
    assert "OLD_VERDICT" not in call["prompt"]


@pytest.mark.parametrize("phase", ["acquire", "release"])
async def test_repeated_cancellation_settles_owned_workspace(setup, monkeypatch, phase):
    build, runner, _, _, workspace = setup
    entered = asyncio.Event()
    finish = asyncio.Event()
    released = []

    async def acquire(**kwargs):
        if phase == "acquire":
            entered.set()
            await finish.wait()
        return "/tmp/owned-feasibility"

    async def release(path):
        if phase == "release":
            entered.set()
            await finish.wait()
        released.append(path)

    monkeypatch.setattr(workspace, "acquire", acquire)
    monkeypatch.setattr(workspace, "release", release)
    task = asyncio.create_task(build().validate(REQUEST))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert released == ["/tmp/owned-feasibility"]
    if phase == "acquire":
        assert not runner.arguments


@pytest.mark.parametrize("when", ["before", "during"])
async def test_real_git_changed_tree_at_same_commit_refuses(
    setup, tmp_path, monkeypatch, when
):
    import subprocess

    from kodezart.adapters.subprocess_git_service import SubprocessGitService

    def command(*args):
        return subprocess.check_output(
            ["git", *args], cwd=tmp_path, text=True, stderr=subprocess.STDOUT
        ).strip()

    command("init")
    command("config", "user.email", "fixture@example.invalid")
    command("config", "user.name", "Fixture")
    file = tmp_path / "evidence.txt"
    file.write_text("original evidence\n")
    command("add", "evidence.txt")
    command("commit", "-m", "fixture")
    head = command("rev-parse", "HEAD")
    command("checkout", "--detach", head)
    build, runner, _, _, workspace = setup
    monkeypatch.setattr(workspace, "acquire", AsyncMock(return_value=str(tmp_path)))

    async def change():
        file.write_text("uncommitted evidence\n")
        assert command("rev-parse", "HEAD") == head

    if when == "before":
        await change()
    else:
        runner.during = change
    with pytest.raises(TrackerFeasibilityReadError, match="contains changes"):
        await build(git=SubprocessGitService(remote="origin")).validate(
            REQUEST.model_copy(update={"head_sha": head})
        )
    assert workspace.calls[-1] == ("release", str(tmp_path))
    if when == "before":
        assert not runner.arguments


async def test_post_session_tracker_outage_cannot_become_a_result(
    setup, tracker, monkeypatch
):
    from kodezart.core.errors import McpTransportError

    build, runner, _, _, workspace = setup
    failure = McpTransportError("unreachable", server_name="fixture")

    async def lost_tracker():
        monkeypatch.setattr(tracker, "read_criteria", AsyncMock(side_effect=failure))

    runner.during = lost_tracker
    with pytest.raises(McpTransportError) as raised:
        await build().validate(REQUEST)
    assert raised.value is failure
    assert workspace.calls[-1][0] == "release"


async def test_a_refused_session_cannot_pass_a_dirty_workspace_to_its_retry(setup):
    build, runner, git, _, workspace = setup
    runner.answers = [output(findings=[finding("wrong")]), output()]

    async def dirty():
        git.has_changes_result = True

    runner.during = dirty
    with pytest.raises(TrackerFeasibilityReadError, match="contains changes"):
        await build().validate(REQUEST)
    assert len(runner.arguments) == 1
    assert workspace.calls[-1][0] == "release"


async def test_coherent_foreign_capture_still_refuses_the_requested_subject(
    setup, tracker, monkeypatch
):
    build, runner, _, cache, workspace = setup
    spec = await tracker.read_fire_spec(issue_key=SUBJECT)
    rows = await tracker.read_criteria(issue_key=SUBJECT)
    monkeypatch.setattr(
        tracker,
        "read_fire_spec",
        AsyncMock(return_value=spec.model_copy(update={"subject": "foreign/42"})),
    )
    monkeypatch.setattr(
        tracker,
        "read_criteria",
        AsyncMock(
            return_value=[
                row.model_copy(update={"parent_key": "foreign/42"}) for row in rows
            ]
        ),
    )
    with pytest.raises(TrackerFeasibilityReadError, match="different subject"):
        await build().validate(REQUEST)
    assert not runner.arguments and not cache.calls and not workspace.calls


async def test_wrong_initial_head_refuses_before_session_and_releases(
    setup, monkeypatch
):
    build, runner, git, _, workspace = setup
    monkeypatch.setattr(git, "current_sha", AsyncMock(return_value="wrong-head"))
    with pytest.raises(TrackerFeasibilityReadError, match="dispatch head"):
        await build().validate(REQUEST)
    assert not runner.arguments
    assert workspace.calls[-1][0] == "release"
