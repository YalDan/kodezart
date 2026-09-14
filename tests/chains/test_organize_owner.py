"""The Organize owner consumes actual fresh tracker and session boundaries."""

# Full owner through its actual factory; the executor chooses outputs from the
# current native board and the requested wire type, not a canned verdict order.
import json
import re
from itertools import groupby

import pytest

from kodezart.composition.organize import build_organize_owner, build_organize_tick
from kodezart.config.app import AppConfig
from kodezart.config.organize import OrganizeSettings
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.domain.errors import OrganizeAdmissionIdentityError
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.organize import AdmissionVerdict, SpecFinding
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import ToolPreset
from tests.chains.test_organize import (
    RecordingExecutor,
    RecordingWorkspace,
    consumer,
    issue,
    request,
    result,
    tracker,
)
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeGitService, PassThroughGate
from tests.prompts.test_organize_mandate_bindings import declared_operation
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_run_surface_lease import _Board
from tests.tracker.conftest import CLAIMED_ISSUE
from tests.tracker.test_linear_mcp_tracker import tracker_over


@pytest.mark.parametrize("method", ["assess", "verify"])
async def test_source_identity_is_checked_before_any_session(monkeypatch, method):
    port = tracker()
    original = port.read_issue_revision

    async def wrong_revision(*, issue_key):
        value = await original(issue_key=issue_key)
        return value.model_copy(update={"issue": issue("another", "wrong source")})

    monkeypatch.setattr(port, "read_issue_revision", wrong_revision)
    executor = RecordingExecutor([result()])
    boundary = consumer(port, executor, RecordingWorkspace())
    with pytest.raises(OrganizeAdmissionIdentityError):
        await getattr(boundary, method)(request())
    assert executor.calls == []


class BoardExecutor:
    def __init__(
        self, board, *, refuse_forever=False, wrong_proposal=False, refusal=None
    ):
        self.board = board
        self.calls = []
        self.refuse_forever = refuse_forever
        self.wrong_proposal = wrong_proposal
        self.refusal = refusal

    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        prompt = kwargs["prompt"]
        schema = kwargs["output_format"]["schema"]
        if schema.get("title") == "WriteBackFinding":
            artifact = json.loads(
                re.search(
                    r"<written_artifact>\s*(.*?)\s*</written_artifact>", prompt, re.S
                )[1]
            )
            payload = {
                "verdict": "holds",
                "evidence": f"Read the actual landed artifact: {artifact['content']}",
                "cited_refs": [],
            }
        else:
            key = re.findall(r"<issue_key>(.*?)</issue_key>", prompt)[-1]
            native = self.board.server.issues[key]
            if schema.get("title") == "OrganizeProposal":
                if self.wrong_proposal:
                    payload = {
                        "kind": "body",
                        "issue_id": "wrong-identity",
                        "body": "Prepared body",
                    }
                elif "Author criterion sub-issue proposals" in prompt:
                    payload = {
                        "kind": "criteria",
                        "issue_id": key,
                        "criteria": [
                            {
                                "title": "Check prepared bytes",
                                "check": "Prepared bytes match the declared source.",
                                "do": "Compare the source and prepared bytes.",
                            }
                        ],
                    }
                else:
                    payload = {
                        "kind": "body",
                        "issue_id": key,
                        "body": "Prepared body grounded in the source.",
                    }
            elif self.refuse_forever or native.description == "Missing specification":
                payload = {
                    "issue_id": key,
                    "verdict": "not_buildable",
                    "evidence": "The current body omits the required source.",
                    "refusal_kind": "spec_gap",
                    "invented_decision": "Restore the source requirement.",
                    **(self.refusal or {}),
                }
            else:
                payload = {
                    "issue_id": key,
                    "verdict": "buildable",
                    "evidence": f"Fresh native body checked: {native.description}",
                }
        yield result(structured_output=payload)


def factory(
    *,
    refuse_forever=False,
    wrong_proposal=False,
    refusal=None,
    body=None,
    bound=2,
    convergence_bound=2,
    write_back_bound=2,
    tick=False,
    settings=None,
    gate=None,
):
    board = _Board()
    operation_fields = declared_operation().model_dump()
    operation_fields["issue_labels"]["decision"] = "needs decision"
    operation_fields["marker_prefixes"]["escalation"] = "organize-question"
    for mandate in operation_fields["organize_mandates"]:
        mandate["rubric_prompt_key"] = "organize_assess"
        mandate["admission_prompt_key"] = "organize_assess"
    if tick:
        operation_fields["organize_scopes"] = [
            {
                "scope": {"kind": "issue", "key": CLAIMED_ISSUE},
                "repo_url": operation_fields["repos"][0]["url"],
            }
        ]
    operation = OperationConfig.model_validate(operation_fields)
    parent = board.server.issues[CLAIMED_ISSUE]
    parent.description = body if body is not None else "Missing specification"
    parent.labels = ["candidate scope"]
    tracker = tracker_over(
        board.server,
        caller=board,
        clock=lambda: board.now,
        issue_labels=operation.issue_labels,
        scope_labels=operation.scope_labels,
        criteria_stage_label_key="criteria",
    )
    executor = BoardExecutor(
        board,
        refuse_forever=refuse_forever,
        wrong_proposal=wrong_proposal,
        refusal=refusal,
    )
    workspace = RecordingWorkspace()
    constructor = build_organize_tick if tick else build_organize_owner
    owner = constructor(
        config=settings
        or AppConfig(
            organize=OrganizeSettings(
                max_admission_rounds=bound, max_convergence_rounds=convergence_bound
            ),
            write_back={"max_verify_rounds": write_back_bound},
        ),
        operation=operation,
        tracker=tracker,
        runner=AgentService(
            executor=executor,
            workspace=workspace,
            git_base_url="https://example.invalid",
        ),
        workspace=workspace,
        git=FakeGitService(
            remote_branch_shas={repo.trunk: "a" * 40 for repo in operation.repos}
        ),
        prompts=load_registry(
            default_set="claude-opus", bindings=operation_bindings(operation)
        ),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate() if gate is None else gate,
        **({} if tick else {"repo_url": "https://example.invalid/repository"}),
    )
    return owner, board, executor


async def run_owner(owner):
    return await owner.run(
        scope=ScopeRef(kind=ScopeKind.ISSUE, key=CLAIMED_ISSUE),
        repo_url="https://example.invalid/repository",
        base_ref="a" * 40,
        job_id="actual-organize-job",
        visibility=RepoVisibility.PRIVATE,
    )


async def test_actual_factory_runs_all_configured_phases_and_reentry_writes_nothing():
    owner, board, executor = factory()
    report = await run_owner(owner)
    assert report.halt is None
    assert [phase.value for phase in report.completed_phases] == [
        "groom",
        "ticket",
        "criteria",
    ]
    parent = board.server.issues[CLAIMED_ISSUE]
    assert {"graph complete", "body complete", "criteria complete"} <= set(
        parent.labels
    )
    children = [
        issue
        for issue in board.server.issues.values()
        if issue.parent_id == CLAIMED_ISSUE
    ]
    assert len(children) == 1
    assert children[0].status_type == "unstarted"
    assert children[0].description.endswith("**Evidence:**\n")
    assert "approved scope" not in parent.labels
    assert all(
        call["session_id"] is None and call["allowed_tools"] is ToolPreset.EVALUATION
        for call in executor.calls
    )
    assert any(
        "Prepared body grounded in the source." in call["prompt"]
        for call in executor.calls
        if call["output_format"]["schema"].get("title") == "AdmissionJudgment"
    )
    board.calls.clear()
    await run_owner(owner)
    assert not [(name, args) for name, args in board.calls if name.startswith("save_")]


async def test_single_admission_round_stops_with_durable_refusal_and_no_completion():
    owner, board, executor = factory(refuse_forever=True, bound=1)
    report = await run_owner(owner)
    assert report.halt.cause == "admission_exhausted"
    assert report.halt.bound.setting == "organize.max_admission_rounds"
    assert report.halt.bound.value == report.halt.bound.rounds_used == 1
    assert report.halt.bound.loop == "admission"
    assert report.completed_phases == ()
    parent = board.server.issues[CLAIMED_ISSUE]
    assert "needs decision" in parent.labels
    assert not set(parent.labels) & {
        "graph complete",
        "body complete",
        "criteria complete",
        "approved scope",
    }
    assert not any(
        issue.parent_id == CLAIMED_ISSUE for issue in board.server.issues.values()
    )
    authors = [
        call
        for call in executor.calls
        if call["output_format"]["schema"].get("title") == "OrganizeProposal"
    ]
    assert len(authors) == 1


MEASURED_REFUSAL_CLASSES = (
    pytest.param(
        "The record must be current before the probe runs.",
        "The phrase 'current' reads either as the fresh tracker revision or as "
        "the local checkout, and the two select different records.",
        "Which of the two readings of a current record the criterion means.",
        id="criterion_admits_two_readings",
    ),
    pytest.param(
        "The endpoint returns the prepared response model to the caller.",
        "No response model of that description is named anywhere in the "
        "current source.",
        "Which response model the endpoint returns.",
        id="response_model_named_nowhere",
    ),
    pytest.param(
        "The freshness probe runs before the record is read.",
        "No call site for that probe is named anywhere in the current source.",
        "Where the freshness probe is called from.",
        id="probe_call_site_named_nowhere",
    ),
    pytest.param(
        "The record is written synchronously, and the committed design of this "
        "issue queues every record write asynchronously.",
        "The body requires a synchronous write that its own committed "
        "asynchronous queue forbids.",
        "Whether the committed asynchronous queue or the body's synchronous "
        "write governs the record write.",
        id="body_contradicts_its_committed_design",
    ),
)


@pytest.mark.parametrize(
    ("body", "evidence", "invented_decision"), MEASURED_REFUSAL_CLASSES
)
async def test_measured_refusal_class_refuses_without_marker_or_criterion(
    body, evidence, invented_decision
):
    owner, board, executor = factory(
        refuse_forever=True,
        bound=1,
        body=body,
        refusal={"evidence": evidence, "invented_decision": invented_decision},
    )
    report = await run_owner(owner)
    assert report.halt.cause == "admission_exhausted"
    assert report.completed_phases == ()
    judged = [
        call["prompt"]
        for call in executor.calls
        if call["output_format"]["schema"].get("title") == "AdmissionJudgment"
    ]
    assert any(body in prompt for prompt in judged)
    refusals = [
        result
        for result in report.halt.admission_results
        if result.issue_id == CLAIMED_ISSUE
    ]
    assert [result.verdict for result in refusals] == [AdmissionVerdict.NOT_BUILDABLE]
    assert refusals[0].invented_decision == invented_decision
    assert refusals[0].evidence == evidence
    parent = board.server.issues[CLAIMED_ISSUE]
    assert "body complete" not in parent.labels
    assert not set(parent.labels) & {
        "graph complete",
        "body complete",
        "criteria complete",
        "approved scope",
    }
    assert not any(
        issue.parent_id == CLAIMED_ISSUE for issue in board.server.issues.values()
    )
    assert not [
        args
        for name, args in board.calls
        if name == "save_issue" and args.get("parentId") == CLAIMED_ISSUE
    ]


async def test_wrong_author_identity_refuses_before_native_issue_write():
    owner, board, _ = factory(wrong_proposal=True)
    with pytest.raises(OrganizeAdmissionIdentityError):
        await run_owner(owner)
    assert not [(name, args) for name, args in board.calls if name == "save_issue"]


async def test_author_source_changed_during_session_cannot_be_overwritten(monkeypatch):
    from kodezart.domain.errors import OrganizeWriteRefusalError

    owner, board, executor = factory()
    original = executor.stream

    async def changed(**kwargs):
        async for event in original(**kwargs):
            if kwargs["output_format"]["schema"].get("title") == "OrganizeProposal":
                board.server.issues[
                    CLAIMED_ISSUE
                ].description = "Concurrent author's current body"
            yield event

    monkeypatch.setattr(executor, "stream", changed)
    with pytest.raises(OrganizeWriteRefusalError, match="source revision changed"):
        await run_owner(owner)
    assert (
        board.server.issues[CLAIMED_ISSUE].description
        == "Concurrent author's current body"
    )
    assert not [(name, args) for name, args in board.calls if name == "save_issue"]


async def test_human_approval_arriving_during_authorship_ends_organize_before_write(
    monkeypatch,
):
    from kodezart.domain.errors import OrganizeWriteRefusalError

    owner, board, executor = factory()
    original = executor.stream

    async def approved(**kwargs):
        async for event in original(**kwargs):
            if kwargs["output_format"]["schema"].get("title") == "OrganizeProposal":
                board.server.issues[CLAIMED_ISSUE].labels.append("approved scope")
            yield event

    monkeypatch.setattr(executor, "stream", approved)
    with pytest.raises(OrganizeWriteRefusalError, match="approval ended"):
        await run_owner(owner)
    assert not [(name, args) for name, args in board.calls if name == "save_issue"]


async def test_cancelled_author_never_reaches_a_tracker_mutation(monkeypatch):
    import asyncio

    owner, board, executor = factory()
    original = executor.stream
    entered = asyncio.Event()

    async def paused(**kwargs):
        if kwargs["output_format"]["schema"].get("title") == "OrganizeProposal":
            entered.set()
            await asyncio.Event().wait()
        async for event in original(**kwargs):
            yield event

    monkeypatch.setattr(executor, "stream", paused)
    task = asyncio.create_task(run_owner(owner))
    await asyncio.wait_for(entered.wait(), timeout=3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not [(name, args) for name, args in board.calls if name.startswith("save_")]


async def test_landed_claim_refutation_drives_a_fresh_author_repair(monkeypatch):
    owner, _board, executor = factory()
    original = executor.stream
    checked = []

    async def source_checker(**kwargs):
        async for event in original(**kwargs):
            payload = event.structured_output
            title = kwargs["output_format"]["schema"].get("title")
            if title == "OrganizeProposal" and payload.get("kind") == "body":
                path = (
                    "tests/real.py"
                    if "tests/missing.py" in kwargs["prompt"]
                    else "tests/missing.py"
                )
                event = result(
                    structured_output={
                        **payload,
                        "body": f"The artifact is demonstrated by {path}.",
                    }
                )
            elif title == "WriteBackFinding":
                artifact = json.loads(
                    re.search(
                        r"<written_artifact>\s*(.*?)\s*</written_artifact>",
                        kwargs["prompt"],
                        re.S,
                    )[1]
                )
                checked.append(artifact["content"])
                if "tests/missing.py" in artifact["content"]:
                    event = result(
                        structured_output={
                            "verdict": "refuted",
                            "evidence": (
                                "The named test is absent from the checked repository."
                            ),
                            "cited_refs": ["tests/missing.py"],
                        }
                    )
            yield event

    monkeypatch.setattr(executor, "stream", source_checker)
    report = await run_owner(owner)
    assert report.halt is None
    assert "tests/missing.py" in checked[0]
    assert "tests/real.py" in checked[1]
    authors = [
        call
        for call in executor.calls
        if call["output_format"]["schema"].get("title") == "OrganizeProposal"
    ]
    assert "tests/missing.py" in authors[1]["prompt"]
    assert "The named test is absent" in authors[1]["prompt"]
    assert all(call["session_id"] is None for call in authors)


async def test_author_decision_is_recorded_without_inventing_an_admission(monkeypatch):
    owner, board, executor = factory()
    original = executor.stream

    async def unresolved(**kwargs):
        async for event in original(**kwargs):
            if kwargs["output_format"]["schema"].get("title") == "OrganizeProposal":
                event = result(
                    structured_output={
                        "kind": "unresolved",
                        "issue_id": CLAIMED_ISSUE,
                        "question": (
                            "Which of the two conflicting source versions "
                            "is authoritative?"
                        ),
                        "evidence": (
                            "The two current declared sources "
                            "specify incompatible bytes."
                        ),
                    }
                )
            yield event

    monkeypatch.setattr(executor, "stream", unresolved)
    report = await run_owner(owner)
    assert report.halt.cause == "human_decision"
    assert report.halt.bound is None
    assert report.halt.admission_results == ()
    assert report.halt.questions[0].issue_id == CLAIMED_ISSUE
    assert "needs decision" in board.server.issues[CLAIMED_ISSUE].labels
    assert any(
        "conflicting source versions" in comment.body
        for comment in board.server.comments
    )
    assert not any(
        args.get("description") for name, args in board.calls if name == "save_issue"
    )


async def test_unanswered_escalation_is_unrecorded_with_exact_issue_and_no_resend(
    monkeypatch,
):
    from kodezart.core.errors import McpCallUnansweredError

    owner, board, _ = factory(refuse_forever=True, bound=1)
    original = board.call_tool
    sends = []

    async def unanswered(*, name, arguments):
        if name == "save_comment" and str(arguments.get("body", "")).startswith(
            "[organize-question:"
        ):
            sends.append(dict(arguments))
            raise McpCallUnansweredError(
                "response lost after request", server_name="fixture", tool_name=name
            )
        return await original(name=name, arguments=arguments)

    monkeypatch.setattr(board, "call_tool", unanswered)
    report = await run_owner(owner)
    assert report.halt.cause == "escalation_unrecorded"
    assert report.halt.bound is None
    assert report.halt.unrecorded_escalation_issue_ids == (CLAIMED_ISSUE,)
    assert len(report.halt.admission_results) == 1
    assert len(sends) == 1
    assert not set(board.server.issues[CLAIMED_ISSUE].labels) & {
        "graph complete",
        "body complete",
        "criteria complete",
    }


async def test_assessment_dispatch_uses_the_configured_admission_role():
    from kodezart.types.domain.prompts import PromptKey

    port = tracker()
    executor = RecordingExecutor([result()])
    boundary = consumer(port, executor, RecordingWorkspace(), set_name="claude-opus")
    await boundary.assess(
        request().model_copy(update={"admission_prompt_key": PromptKey.ORGANIZE_VERIFY})
    )
    assert "Adversarially verify the current issue" in executor.calls[0]["prompt"]


async def test_already_approved_scope_starts_no_author_or_judgment():
    owner, board, executor = factory()
    board.server.issues[CLAIMED_ISSUE].labels.append("approved scope")
    report = await run_owner(owner)
    assert report.completed_phases == ()
    assert report.halt is None
    assert executor.calls == []
    assert not [(name, args) for name, args in board.calls if name.startswith("save_")]


async def test_full_scope_finding_exhausts_the_actual_convergence_bound(monkeypatch):
    from tests.fakes import FakeMcpIssue

    owner, board, executor = factory(convergence_bound=1)
    board.server.issues["untouched-child"] = FakeMcpIssue(
        id="untouched-child",
        parent_id=CLAIMED_ISSUE,
        description="Source version remains unspecified.",
    )
    original = executor.stream

    async def recurring(**kwargs):
        async for event in original(**kwargs):
            if (
                kwargs["output_format"]["schema"].get("title") == "AdmissionJudgment"
                and event.structured_output["issue_id"] == "untouched-child"
                and "Adversarially verify the current issue" in kwargs["prompt"]
            ):
                event = result(
                    structured_output={
                        **event.structured_output,
                        "findings": [
                            {
                                "issue_id": "untouched-child",
                                "defect_class": "source_version_unspecified",
                                "evidence": (
                                    "The untouched child's source version is absent."
                                ),
                                "role": "instance",
                            }
                        ],
                    }
                )
            yield event

    monkeypatch.setattr(executor, "stream", recurring)
    report = await run_owner(owner)
    assert report.halt.cause == "convergence_exhausted"
    assert report.halt.bound.setting == "organize.max_convergence_rounds"
    assert report.halt.bound.value == report.halt.bound.rounds_used == 1
    assert report.halt.surviving_findings[0].issue_id == "untouched-child"
    assert not set(board.server.issues[CLAIMED_ISSUE].labels) & {
        "graph complete",
        "body complete",
        "criteria complete",
    }
    assert "needs decision" in board.server.issues["untouched-child"].labels


async def test_missing_criterion_edit_capability_is_not_invented_as_a_human_fork(
    monkeypatch,
):
    from kodezart.domain.errors import OrganizeWriteRefusalError

    owner, board, executor = factory()
    original = executor.stream

    async def unavailable(**kwargs):
        async for event in original(**kwargs):
            if kwargs["output_format"]["schema"].get("title") == "OrganizeProposal":
                event = result(
                    structured_output={
                        "kind": "unavailable",
                        "issue_id": CLAIMED_ISSUE,
                        "capability": "criterion_edit",
                        "evidence": (
                            "The verified dependency requires a graph edge write."
                        ),
                    }
                )
            yield event

    monkeypatch.setattr(executor, "stream", unavailable)
    with pytest.raises(
        OrganizeWriteRefusalError, match="unavailable capability criterion_edit"
    ):
        await run_owner(owner)
    assert not [(name, args) for name, args in board.calls if name.startswith("save_")]
    assert "needs decision" not in board.server.issues[CLAIMED_ISSUE].labels


async def test_removed_phase_gate_refuses_author_write(monkeypatch):
    from kodezart.domain.errors import OrganizeWriteRefusalError

    owner, board, executor = factory()
    original = executor.stream

    async def gate_removed(**kwargs):
        async for event in original(**kwargs):
            if kwargs["output_format"]["schema"].get("title") == "OrganizeProposal":
                board.server.issues[CLAIMED_ISSUE].labels.remove("candidate scope")
            yield event

    monkeypatch.setattr(executor, "stream", gate_removed)
    with pytest.raises(OrganizeWriteRefusalError, match="phase gate"):
        await run_owner(owner)
    assert not [(name, args) for name, args in board.calls if name.startswith("save_")]


ROUND_ONE_CLASSES = ("criterion_admits_two_readings", "probe_call_site_named_nowhere")
ROUND_TWO_CLASSES = ("probe_call_site_named_nowhere", "response_model_named_nowhere")


def finding(defect_class):
    return {
        "issue_id": CLAIMED_ISSUE,
        "defect_class": defect_class,
        "evidence": f"The current body reproduces {defect_class}.",
        "role": "instance",
    }


async def test_remediation_round_input_is_the_accumulated_defect_class_set(monkeypatch):
    from kodezart.chains.organize import OrganizeAdmission

    owner, _board, executor = factory(convergence_bound=3)
    requests = []
    assess, verify = OrganizeAdmission.assess, OrganizeAdmission.verify

    async def record_assess(self, request):
        requests.append(request)
        return await assess(self, request)

    async def record_verify(self, request):
        requests.append(request)
        return await verify(self, request)

    monkeypatch.setattr(OrganizeAdmission, "assess", record_assess)
    monkeypatch.setattr(OrganizeAdmission, "verify", record_verify)
    original = executor.stream
    titles = []

    async def scripted(**kwargs):
        title = kwargs["output_format"]["schema"].get("title")
        dry_round = (
            title == "AdmissionJudgment"
            and "Adversarially verify the current issue" in kwargs["prompt"]
            and titles[-1:] == ["AdmissionJudgment"]
            and requests[-1].issue_key == CLAIMED_ISSUE
        )
        seen = set(requests[-1].defect_classes) if requests else set()
        titles.append(title)
        scripted_classes = ()
        if dry_round and not seen:
            scripted_classes = ROUND_ONE_CLASSES
        elif dry_round and seen == set(ROUND_ONE_CLASSES):
            scripted_classes = ROUND_TWO_CLASSES
        async for event in original(**kwargs):
            if scripted_classes:
                event = result(
                    structured_output={
                        **event.structured_output,
                        "findings": [finding(c) for c in scripted_classes],
                    }
                )
            yield event

    monkeypatch.setattr(executor, "stream", scripted)
    report = await run_owner(owner)
    assert report.halt is None
    first_round = tuple(sorted(set(ROUND_ONE_CLASSES)))
    both_rounds = tuple(sorted(set(ROUND_ONE_CLASSES) | set(ROUND_TWO_CLASSES)))
    observed = [classes for classes, _ in groupby(r.defect_classes for r in requests)]
    assert observed == [(), first_round, both_rounds] * 3
    assert all(
        not isinstance(value, SpecFinding)
        and not (
            isinstance(value, list | tuple)
            and any(isinstance(item, SpecFinding) for item in value)
        )
        for request in requests
        for value in (getattr(request, name) for name in type(request).model_fields)
    )
    evidence = finding(ROUND_ONE_CLASSES[0])["evidence"]
    assert not any(evidence in request.mandate_rubric for request in requests)
    authors = [
        call["prompt"]
        for call in executor.calls
        if call["output_format"]["schema"].get("title") == "OrganizeProposal"
    ]
    assert any(evidence in prompt for prompt in authors)
    scoped = next(r for r in reversed(requests) if r.defect_classes == both_rounds)
    from_previous_findings = scoped.model_copy(
        update={"defect_classes": tuple(sorted(set(ROUND_TWO_CLASSES)))}
    )
    assert from_previous_findings.defect_classes != scoped.defect_classes
    assert from_previous_findings != scoped
