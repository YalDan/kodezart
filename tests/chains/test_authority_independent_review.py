"""Independent actual-owner authorization probes; only external answers change."""

import asyncio
import copy
import re

import pytest

from kodezart.core.errors import McpCallUnansweredError, McpTransportError
from kodezart.domain.errors import OrganizeWriteRefusalError, SurfaceLeaseError
from tests.chains import test_organize_owner as fixtures
from tests.chains.test_organize import result
from tests.fakes import FakeMcpIssue
from tests.tracker.conftest import CLAIMED_ISSUE

PARENT = "authority-parent"
PROJECT = "authority-project"


#: The surfaces the criteria stage owns. That stage runs inside an approved
#: scope run, so the fact that governs its write authority is approval
#: PRESENT; every other surface here is authored by the pre-approval row,
#: where the governing fact is approval ABSENT.
RUN_STAGE_KINDS = ("criteria",)


def configured(monkeypatch, kind, change=None):
    original_tracker = fixtures.tracker_over

    def retried(*args, **kwargs):
        return original_tracker(*args, **kwargs, max_retries=1)

    monkeypatch.setattr(fixtures, "tracker_over", retried)
    under_approval = kind in RUN_STAGE_KINDS
    owner, board, executor = fixtures.factory(under_approval=under_approval)
    subject = board.server.issues[CLAIMED_ISSUE]
    subject.parent_id = PARENT
    subject.project_id = PROJECT
    board.server.issues[PARENT] = FakeMcpIssue(
        id=PARENT, description="A separately held parent specification."
    )
    board.server.projects[PROJECT] = {
        "id": PROJECT,
        "name": "Authority project",
        "description": "External project metadata.",
        "labels": [],
        "initiatives": [],
        "url": "https://tracker.invalid/project/authority-project",
    }
    if under_approval and change in {"parent_approval", "project_approval"}:
        # Hold the run stage's approval exactly where the change withdraws it
        # from, so each case still names one external holder of one fact.
        subject.labels.remove("approved scope")
        holder = (
            board.server.issues[PARENT].labels
            if change == "parent_approval"
            else board.server.projects[PROJECT]["labels"]
        )
        holder.append("approved scope")
    original_call = board.call_tool

    async def external_call(*, name, arguments):
        if name == "list_milestones":
            board.calls.append((name, dict(arguments)))
            return {"milestones": [], "hasNextPage": False}
        return await original_call(name=name, arguments=arguments)

    monkeypatch.setattr(board, "call_tool", external_call)
    original_stream = executor.stream

    async def stream(**kwargs):
        if (
            kind in {"graph", "split"}
            and kwargs["output_format"]["schema"].get("title") == "OrganizeProposal"
        ):
            data = (
                {"changes": [{"kind": "priority", "priority": "urgent"}]}
                if kind == "graph"
                else {
                    "children": [
                        {
                            "deliverable_key": "independent-split",
                            "title": "Independent split",
                            "body": "The independently specified child.",
                        }
                    ]
                }
            )
            yield result(
                structured_output={"kind": kind, "issue_id": CLAIMED_ISSUE, **data}
            )
            return
        async for event in original_stream(**kwargs):
            yield event

    monkeypatch.setattr(executor, "stream", stream)

    def selected(name, arguments):
        if name != "save_issue":
            return False
        if kind == "body":
            return arguments.get("id") == CLAIMED_ISSUE and "description" in arguments
        if kind == "graph":
            return "priority" in arguments
        return "id" not in arguments

    return owner, board, selected, under_approval


@pytest.mark.parametrize("kind", ["body", "graph", "split", "criteria"])
@pytest.mark.parametrize(
    "change",
    ["parent_approval", "project_approval", "context", "membership", "phase", "lease"],
)
async def test_unsent_retry_preserves_every_owner_precondition(
    monkeypatch, kind, change
):
    owner, board, selected, under_approval = configured(monkeypatch, kind, change)
    original = board.call_tool
    attempts = 0
    before_subject = None

    async def call_tool(*, name, arguments):
        nonlocal attempts, before_subject
        if selected(name, arguments):
            attempts += 1
            if attempts == 1:
                subject = board.server.issues[CLAIMED_ISSUE]
                before_subject = copy.deepcopy(subject)
                if change in {"parent_approval", "project_approval"}:
                    holder = (
                        board.server.issues[PARENT].labels
                        if change == "parent_approval"
                        else board.server.projects[PROJECT]["labels"]
                    )
                    # The move that takes this surface's authority away: for a
                    # run stage, approval withdrawn; before approval, granted.
                    if under_approval:
                        holder.remove("approved scope")
                    else:
                        holder.append("approved scope")
                elif change == "context":
                    board.server.issues[
                        PARENT
                    ].description = "Externally revised context."
                elif change == "membership":
                    board.server.issues["new-member"] = FakeMcpIssue(
                        id="new-member",
                        parent_id=CLAIMED_ISSUE,
                        description="An externally added scope member.",
                    )
                elif change == "phase":
                    subject.labels = [
                        label
                        for label in subject.labels
                        if label
                        not in {"candidate scope", "graph complete", "body complete"}
                    ]
                else:
                    board.advance(10000)
                raise McpTransportError(
                    "Known unsent interruption", server_name="fixture", tool_name=name
                )
        return await original(name=name, arguments=arguments)

    monkeypatch.setattr(board, "call_tool", call_tool)
    # A lease that lapsed under the run is the write's own failure: the base
    # SurfaceLeaseError, never the contention a surviving finding absorbs.
    expected = (
        SurfaceLeaseError
        if change == "lease"
        else (OrganizeWriteRefusalError, SurfaceLeaseError)
    )
    with pytest.raises(expected) as raised:
        await fixtures.run_owner(owner)
    if change == "lease":
        assert type(raised.value) is SurfaceLeaseError
    assert attempts == 1
    assert not [args for name, args in board.calls if selected(name, args)]
    assert not board.grants()
    if change in {
        "parent_approval",
        "project_approval",
        "context",
        "membership",
        "lease",
    }:
        current = board.server.issues[CLAIMED_ISSUE]
        assert current.description == before_subject.description
        assert current.labels == before_subject.labels
        assert current.parent_id == before_subject.parent_id
        assert current.project_id == before_subject.project_id
        assert current.priority_raw == before_subject.priority_raw


@pytest.mark.parametrize("kind", ["body", "graph", "split", "criteria"])
@pytest.mark.parametrize("outcome", ["issued_unknown", "receipt_read_failure"])
async def test_actual_completed_or_uncertain_write_is_never_resent(
    monkeypatch, kind, outcome
):
    owner, board, selected, _ = configured(monkeypatch, kind)
    original = board.call_tool
    issued = 0

    async def call_tool(*, name, arguments):
        nonlocal issued
        if selected(name, arguments):
            issued += 1
            value = await original(name=name, arguments=arguments)
            if outcome == "issued_unknown":
                raise McpCallUnansweredError(
                    "Issued with no answer", server_name="fixture", tool_name=name
                )
            return value
        if issued and name == "get_issue":
            raise McpTransportError(
                "Readback unavailable", server_name="fixture", tool_name=name
            )
        return await original(name=name, arguments=arguments)

    monkeypatch.setattr(board, "call_tool", call_tool)
    from kodezart.core.errors import TrackerUnavailableError

    with pytest.raises(TrackerUnavailableError):
        await fixtures.run_owner(owner)
    assert issued == 1
    assert len([args for name, args in board.calls if selected(name, args)]) == 1
    assert not board.grants()


@pytest.mark.parametrize("refuse", [False, True])
async def test_repeated_cancellation_during_retry_validation_settles_before_release(
    monkeypatch, refuse
):
    owner, board, selected, _ = configured(monkeypatch, "body")
    original = board.call_tool
    attempts = 0
    reached = asyncio.Event()
    resume = asyncio.Event()
    paused = False

    async def call_tool(*, name, arguments):
        nonlocal attempts, paused
        if selected(name, arguments):
            attempts += 1
            if attempts == 1:
                raise McpTransportError(
                    "Known unsent interruption", server_name="fixture", tool_name=name
                )
        if attempts == 1 and not paused and name == "get_issue":
            paused = True
            reached.set()
            await resume.wait()
        return await original(name=name, arguments=arguments)

    monkeypatch.setattr(board, "call_tool", call_tool)
    task = asyncio.create_task(fixtures.run_owner(owner))
    try:
        await asyncio.wait_for(reached.wait(), timeout=3)
        assert board.grants()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert board.grants()
        if refuse:
            board.server.projects[PROJECT]["labels"].append("approved scope")
        resume.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3)
        writes = [args for name, args in board.calls if selected(name, args)]
        assert len(writes) == (0 if refuse else 1)
        assert attempts == (1 if refuse else 2)
        assert not board.grants()
    finally:
        resume.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("kind", ["split", "criteria"])
async def test_second_child_retry_retains_context_with_first_owned_creation(
    monkeypatch, kind
):
    original_tracker = fixtures.tracker_over

    def retried(*args, **kwargs):
        return original_tracker(*args, **kwargs, max_retries=1)

    monkeypatch.setattr(fixtures, "tracker_over", retried)
    owner, board, executor = fixtures.factory(
        convergence_bound=4, bound=3, under_approval=kind in RUN_STAGE_KINDS
    )
    original_stream = executor.stream

    async def stream(**kwargs):
        schema = kwargs["output_format"]["schema"].get("title")
        keys = re.findall(r"<issue_key>(.*?)</issue_key>", kwargs["prompt"])
        child_titles = {issue.title for issue in board.server.issues.values()}
        if (
            kind == "split"
            and keys
            and keys[-1] == CLAIMED_ISSUE
            and "Split 2" not in child_titles
        ):
            data = (
                {
                    "kind": "split",
                    "issue_id": CLAIMED_ISSUE,
                    "children": [
                        {
                            "deliverable_key": f"independent-{i}",
                            "title": f"Split {i}",
                            "body": f"The specified independent deliverable {i}.",
                        }
                        for i in [1, 2]
                    ],
                }
                if schema == "OrganizeProposal"
                else {
                    "issue_id": CLAIMED_ISSUE,
                    "verdict": "not_buildable",
                    "refusal_kind": "spec_gap",
                    "evidence": "Two deliverables are required.",
                    "invented_decision": "Prepare both specified deliverables.",
                }
            )
            yield result(structured_output=data)
            return
        if (
            kind == "criteria"
            and schema == "OrganizeProposal"
            and "Author criterion sub-issue proposals" in kwargs["prompt"]
        ):
            yield result(
                structured_output={
                    "kind": "criteria",
                    "issue_id": keys[-1],
                    "criteria": [
                        {
                            "title": f"Criterion {i}",
                            "check": f"The source property {i} holds.",
                            "do": (
                                f"Compare source property {i} "
                                "with the current artifact."
                            ),
                            "runnable_test": f"tests/fixture/test_property_{i}.py",
                        }
                        for i in [1, 2]
                    ],
                }
            )
            return
        async for event in original_stream(**kwargs):
            yield event

    monkeypatch.setattr(executor, "stream", stream)
    original_call = board.call_tool
    attempts = 0
    second_title = "Split 2" if kind == "split" else "Criterion 2"

    async def call_tool(*, name, arguments):
        nonlocal attempts
        if name == "save_issue" and arguments.get("title") == second_title:
            attempts += 1
            if attempts == 1:
                raise McpTransportError(
                    "Second creation known unsent",
                    server_name="fixture",
                    tool_name=name,
                )
        return await original_call(name=name, arguments=arguments)

    monkeypatch.setattr(board, "call_tool", call_tool)
    report = await fixtures.run_owner(owner)
    assert report.halt is None
    assert attempts == 2
    assert (
        len(
            [
                issue
                for issue in board.server.issues.values()
                if issue.title == second_title
            ]
        )
        == 1
    )
    assert not board.grants()
    writes = [call for call in board.calls if call[0].startswith("save_")]
    await fixtures.run_owner(owner)
    assert [call for call in board.calls if call[0].startswith("save_")] == writes
