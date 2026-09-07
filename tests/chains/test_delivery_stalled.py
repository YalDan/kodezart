"""Recorded stalled facts take the same actual delivery path as accepted work."""

import inspect
import json

import httpx
import pytest

from kodezart.chains.delivery_coordinator import DeliveryCoordinator
from kodezart.domain.errors import DeliveryContextError, DeliveryRouteUnavailableError
from kodezart.domain.stall_report import DO_NOT_MERGE_PREFIX, NOT_CONVERGED_HEADING
from kodezart.domain.trajectory import fold_trajectory
from kodezart.types.domain.agent import (
    WorkflowCompleteEvent,
    WorkflowTicketEvent,
    WorkflowVisibilityEvent,
)
from kodezart.types.domain.delivery import DeliveryContext
from kodezart.types.domain.fire_spec import AuthoredSpec, TrackerSpec
from kodezart.types.domain.gating import GateDecision, GateVerdict, OutboundDestination
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.trajectory import IterationRecord
from tests.adapters.test_github_api import _completed_run, _empty_runs, _make_client
from tests.chains.test_delivery_runtime import (
    BASE,
    HEAD,
    REPOSITORY,
    SHA,
    context,
    deliver,
    dispatch,
    setup,
)
from tests.chains.test_ralph_workflow import _make_engine
from tests.fakes import (
    FakeArtifactPersister,
    FakeCIMonitor,
    FakeForgeQuery,
    FakeGitService,
    FakePRContentEditor,
    FakePRCreator,
    FakeQualityGate,
    PassThroughGate,
    make_failing_evaluation,
)


def stalled_context():
    facts = context()
    criterion = facts.criteria[0]
    trajectory = fold_trajectory(
        [
            IterationRecord(
                iteration=index,
                passed_count=0,
                failing_criterion_ids=[criterion.id],
                commit_sha=SHA,
            )
            for index in range(1, 4)
        ],
        plateau_window=2,
    )
    terminal = WorkflowCompleteEvent(
        feature_branch=HEAD,
        ralph_branch="iteration/ref",
        total_iterations=len(trajectory.records),
        accepted=False,
        outcome=WorkflowOutcome.stalled_pr_opened,
        final_commit_sha=SHA,
        trajectory=trajectory,
    )
    observed = DeliveryContext.from_terminal(
        terminal=terminal,
        execution=facts.execution,
        spec=facts.spec,
        criteria=facts.criteria,
        flagged_items=facts.flagged_items,
        visibility=facts.visibility,
    )
    assert observed.trajectory is terminal.trajectory
    return observed


@pytest.mark.parametrize("passed", [True, None])
async def test_stalled_facts_open_and_watch_without_a_description_session(passed):
    fixture = setup(monitor=FakeCIMonitor(passed=passed, declared=False))
    facts = stalled_context()
    result = await deliver(fixture.coordinator, facts=facts)
    assert result.outcome is WorkflowOutcome.stalled_pr_opened
    assert result.checks_passed is passed and result.pr.state == "open"
    assert fixture.runner.calls == []
    assert fixture.monitor.calls == [{"repo_url": REPOSITORY, "ref": HEAD}]
    call = fixture.forge.calls[0]
    assert call["title"] == DO_NOT_MERGE_PREFIX + " Recorded work"
    assert NOT_CONVERGED_HEADING in call["body"]
    assert "AC-1: Recorded criterion" in call["body"]
    assert "| best pass count | 0 of 1 |" in call["body"]
    assert "| iterations run | 3 |" in call["body"]
    assert SHA in call["body"]
    assert call["body"].endswith("Tracker issue: subject/42")
    assert [gate[0] for gate in fixture.gate.calls] == [
        call["title"],
        call["body"],
    ]


async def test_recovered_checks_do_not_turn_stalled_work_into_accepted_work():
    monitor = FakeCIMonitor(
        passed=False,
        failed_names=frozenset({"lint"}),
        rerun_results=[(True, "Recovered", frozenset())],
    )
    fixture = setup(monitor=monitor)
    result = await deliver(fixture.coordinator, facts=stalled_context())
    assert result.outcome is WorkflowOutcome.stalled_pr_opened
    assert result.checks_passed is True
    assert monitor.rerun_calls == [(REPOSITORY, SHA)]
    assert fixture.runner.calls == []


async def test_actual_stalled_terminal_is_consumed_without_a_second_pull_request():
    original = stalled_context()
    editor = FakePRContentEditor()
    forge = FakePRCreator(content_store=editor.records)
    quality = FakeQualityGate(
        events=[],
        evaluation=make_failing_evaluation(),
        total_iterations=original.total_iterations,
        last_commit_sha=SHA,
        trajectory=original.trajectory,
    )
    engine = _make_engine(
        quality_gate=quality,
        pr_creator=forge,
        git=FakeGitService(remote_branch_shas={BASE: "b" * 40}),
    )
    execution = original.execution
    events = [
        event
        async for event in engine.run(
            scope=None,
            prompt=execution.prompt,
            issue_key="subject/42",
            run_identity=execution.run_identity,
            repo_path=execution.repo_path,
            repo_url=execution.repo_url,
            base_spec=execution.base_spec,
            permission_mode=execution.permission_mode,
            allowed_tools=execution.allowed_tools,
            cache_key=execution.cache_key,
        )
    ]
    terminal = next(
        event for event in events if isinstance(event, WorkflowCompleteEvent)
    )
    assert terminal.outcome is WorkflowOutcome.stalled_pr_opened
    assert terminal.trajectory is not None
    execution = execution.model_copy(update={"repo_url": quality.calls[0]["repo_url"]})
    ticket = next(
        event.ticket for event in events if isinstance(event, WorkflowTicketEvent)
    )
    visibility = next(
        event.visibility
        for event in events
        if isinstance(event, WorkflowVisibilityEvent)
    )
    facts = DeliveryContext.from_terminal(
        terminal=terminal,
        execution=execution,
        spec=AuthoredSpec(ticket=ticket),
        criteria=tuple(quality.calls[0]["acceptance_criteria"]),
        flagged_items=(),
        visibility=visibility,
    )
    fixture = setup(
        forge=forge,
        editor=editor,
        query=FakeForgeQuery(
            open_prs={
                (execution.repo_url, terminal.feature_branch): (
                    terminal.pr_url,
                    terminal.pr_number,
                )
            }
        ),
        git=FakeGitService(
            remote_branch_shas={
                BASE: "b" * 40,
                terminal.feature_branch: terminal.final_commit_sha,
            }
        ),
    )
    result = await fixture.coordinator.deliver(
        dispatch(head_branch=terminal.feature_branch),
        feature_branch=terminal.feature_branch,
        final_commit_sha=terminal.final_commit_sha,
        context=facts,
    )
    assert result.pr.number == terminal.pr_number
    assert result.outcome is WorkflowOutcome.stalled_pr_opened
    assert result.checks_passed is True and fixture.runner.calls == []
    assert [call["method"] for call in forge.calls] == ["create_pr"]
    assert any(call["method"] == "edit_pr" for call in editor.calls)


async def test_same_cleaner_runs_before_the_factual_published_head_is_rendered():
    cleaned = "c" * 40
    cleaner = FakeArtifactPersister()
    fixture = setup(
        cleaner=cleaner,
        git=FakeGitService(
            remote_branch_shas={BASE: "b" * 40, HEAD: cleaned},
            remote_branch_sha_sequences={HEAD: [SHA, cleaned]},
        ),
    )
    await deliver(fixture.coordinator, facts=stalled_context())
    assert cleaner.clean_calls == [("/checkout", REPOSITORY, HEAD)]
    assert f"| head commit | `{cleaned}` |" in fixture.forge.calls[0]["body"]


@pytest.mark.parametrize("damage", ["missing", "no-commit", "foreign-key", "count"])
async def test_unreadable_stalled_facts_refuse_before_any_remote_activity(damage):
    facts = stalled_context()
    trajectory = facts.trajectory
    if damage == "missing":
        facts = facts.model_copy(update={"trajectory": None})
    elif damage == "no-commit":
        trajectory = trajectory.model_copy(update={"records": []})
        facts = facts.model_copy(update={"trajectory": trajectory})
    elif damage == "foreign-key":
        trajectory = trajectory.model_copy(update={"never_passed_ids": ["AC-99"]})
        facts = facts.model_copy(update={"trajectory": trajectory})
    else:
        facts = facts.model_copy(update={"total_iterations": 99})
    fixture = setup()
    with pytest.raises((DeliveryContextError, DeliveryRouteUnavailableError)):
        await deliver(fixture.coordinator, facts=facts)
    assert fixture.forge.calls == fixture.query.calls == fixture.runner.calls == []
    assert fixture.monitor.calls == []


async def test_tracker_stall_cannot_reinterpret_authored_trajectory_identities():
    facts = stalled_context().model_copy(
        update={
            "spec": TrackerSpec(
                subject="subject/42",
                body="Raw tracker",
                criteria=("child/8",),
                read_at_version="opaque-version",
            )
        }
    )
    fixture = setup()
    with pytest.raises(DeliveryRouteUnavailableError, match="own criterion trajectory"):
        await deliver(fixture.coordinator, facts=facts)
    assert fixture.query.calls == fixture.forge.calls == []


@pytest.mark.parametrize(
    "destination", [OutboundDestination.PR_TITLE, OutboundDestination.PR_BODY]
)
async def test_gated_stall_disposition_cannot_be_removed_before_publication(
    destination,
):
    class RemoveDisposition(PassThroughGate):
        async def gate(self, **kwargs):
            await super().gate(**kwargs)
            content = kwargs["content"]
            if kwargs["destination"] is destination:
                content = content.replace(DO_NOT_MERGE_PREFIX, "").replace(
                    NOT_CONVERGED_HEADING, ""
                )
            return GateDecision(verdict=GateVerdict.CLEAN, content=content)

    fixture = setup(gate=RemoveDisposition())
    with pytest.raises(DeliveryContextError, match="lost its required disposition"):
        await deliver(fixture.coordinator, facts=stalled_context())
    assert fixture.forge.calls == fixture.monitor.calls == []


@pytest.mark.parametrize("field", ["title", "body"])
async def test_stalled_disposition_removed_during_watch_cannot_return_success(field):
    editor = FakePRContentEditor()

    class RemovedDuringWatch(FakeCIMonitor):
        async def wait_for_checks(self, *, repo_url, ref):
            result = await super().wait_for_checks(repo_url=repo_url, ref=ref)
            key, record = next(iter(editor.records.items()))
            changed = (
                record.title.replace(DO_NOT_MERGE_PREFIX, "")
                if field == "title"
                else record.body.replace(NOT_CONVERGED_HEADING, "")
            )
            editor.records[key] = record.model_copy(update={field: changed})
            return result

    fixture = setup(editor=editor, monitor=RemovedDuringWatch())
    with pytest.raises(DeliveryContextError, match="lost its required disposition"):
        await deliver(fixture.coordinator, facts=stalled_context())
    assert len(fixture.forge.calls) == len(fixture.monitor.calls) == 1


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("passed", [True, None])
async def test_native_stalled_delivery_creates_or_edits_then_watches(existing, passed):
    rows = []
    requests = []
    if existing:
        rows.append(
            {
                "html_url": REPOSITORY + "/pull/7",
                "number": 7,
                "title": "Earlier title",
                "body": "Earlier body",
                "head": {"ref": HEAD},
                "base": {"ref": "earlier-base"},
            }
        )

    def handler(request):
        requests.append(request)
        path = request.url.path
        if path == "/repos/example/project/pulls":
            if request.method == "GET":
                return httpx.Response(200, json=rows)
            assert request.method == "POST" and not rows
            body = json.loads(request.content)
            rows.append(
                {
                    "html_url": REPOSITORY + "/pull/7",
                    "number": 7,
                    "title": body["title"],
                    "body": body["body"],
                    "head": {"ref": body["head"]},
                    "base": {"ref": body["base"]},
                }
            )
            return httpx.Response(201, json=rows[0])
        if path == "/repos/example/project/pulls/7":
            assert request.method == "PATCH"
            body = json.loads(request.content)
            rows[0].update({key: value for key, value in body.items() if key != "base"})
            if "base" in body:
                rows[0]["base"] = {"ref": body["base"]}
            return httpx.Response(200, json=rows[0])
        if path.endswith("/check-runs"):
            assert request.method == "GET" and f"/commits/{HEAD}/" in path
            return _completed_run() if passed is True else _empty_runs()
        if path.endswith("/actions/workflows"):
            return httpx.Response(200, json={"total_count": 0, "workflows": []})
        raise AssertionError((request.method, path))

    client = _make_client(handler, ci_no_workflows_grace_polls=1)
    fixture = setup(forge=client, query=client, editor=client, monitor=client)
    try:
        result = await deliver(fixture.coordinator, facts=stalled_context())
    finally:
        await client.close()
    assert result.outcome is WorkflowOutcome.stalled_pr_opened
    assert result.checks_passed is passed
    assert result.pr.number == 7 and result.pr.state == "open"
    assert rows[0]["base"] == {"ref": BASE}
    assert rows[0]["title"].startswith(DO_NOT_MERGE_PREFIX)
    assert "Tracker issue: subject/42" in rows[0]["body"]
    writes = [request for request in requests if request.method != "GET"]
    assert [request.method for request in writes] == ["PATCH" if existing else "POST"]
    assert fixture.runner.calls == []


def test_common_coordinator_has_no_disposition_marker_or_stalled_route():
    source = inspect.getsource(DeliveryCoordinator)
    assert DO_NOT_MERGE_PREFIX not in source
    assert "stalled_pr_opened" not in source
