"""The Organize owner consumes actual fresh tracker and session boundaries."""

# Full owner through its actual factory; the executor chooses outputs from the
# current native board and the requested wire type, not a canned verdict order.
import dataclasses
import inspect
import json
import linecache
import re
from collections import Counter
from itertools import groupby

import pytest

from kodezart.composition.organize import build_organize_owner, build_organize_tick
from kodezart.config.app import AppConfig
from kodezart.config.organize import OrganizeSettings
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.domain.errors import (
    OrganizeAdmissionIdentityError,
    OrganizeWriteRefusalError,
    SurfaceLeaseError,
)
from kodezart.domain.organize import stage_rows
from kodezart.services import organize_owner
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig, ScopeLabel
from kodezart.types.domain.organize import (
    AdmissionVerdict,
    MandateKind,
    SpecFinding,
    split_label_key,
)
from kodezart.types.domain.organize_owner import StageHaltCause
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
from tests.integration.test_organize_authority import groomer
from tests.integration.test_scope_entry import GROOM_MARKER
from tests.integration.test_scope_runtime import SCOPE
from tests.integration.test_scope_runtime import board as scope_board
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
        self,
        board,
        *,
        refuse_forever=False,
        wrong_proposal=False,
        refusal=None,
        criteria=("Check prepared bytes",),
    ):
        self.board = board
        self.calls = []
        self.refuse_forever = refuse_forever
        self.wrong_proposal = wrong_proposal
        self.refusal = refusal
        self.criteria = tuple(criteria)

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
                                "title": title,
                                "check": f"{title} match the declared source.",
                                "do": f"Compare the source and {title.lower()}.",
                                "runnable_test": "tests/fixture/test_criterion.py",
                            }
                            for title in self.criteria
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
    under_approval=False,
    criteria=("Check prepared bytes",),
    phases=None,
    board=None,
    groom_gate_key=None,
    groom_rubric_key=None,
):
    # *board* builds another owner over a board a previous owner already
    # worked: a case about a second entry into a stage needs the labels and
    # children that entry left, so the seeding below runs for a fresh board
    # only and never resets the parent a run has written.
    seeded = board is None
    board = _Board() if seeded else board
    operation_fields = declared_operation().model_dump()
    operation_fields["issue_labels"]["decision"] = "needs decision"
    # The live table gates the first run stage on approval by that exact
    # reference; grooming keeps its own pre-approval gate.
    for mandate in operation_fields["organize_mandates"]:
        if mandate["kind"] == "ticket":
            mandate["gate_label_key"] = "scope_labels.approved"
    operation_fields["marker_prefixes"]["escalation"] = "organize-question"
    # *groom_gate_key* repoints the pre-approval row's gate at another
    # configured member, so a case can tell the configured member from the
    # one the shipped table happens to name.
    if groom_gate_key is not None:
        for mandate in operation_fields["organize_mandates"]:
            if mandate["kind"] == "groom":
                mandate["gate_label_key"] = groom_gate_key
    for mandate in operation_fields["organize_mandates"]:
        mandate["rubric_prompt_key"] = "organize_assess"
        mandate["admission_prompt_key"] = "organize_assess"
    # *groom_rubric_key* gives the pre-approval row a rubric of its own, apart
    # from its admission role, so a case can see which one the owner renders.
    if groom_rubric_key is not None:
        for mandate in operation_fields["organize_mandates"]:
            if mandate["kind"] == "groom":
                mandate["rubric_prompt_key"] = groom_rubric_key
    if tick:
        operation_fields["organize_scopes"] = [
            {
                "scope": {"kind": "issue", "key": CLAIMED_ISSUE},
                "repo_url": operation_fields["repos"][0]["url"],
            }
        ]
    operation = OperationConfig.model_validate(operation_fields)
    if seeded:
        parent = board.server.issues[CLAIMED_ISSUE]
        parent.description = body if body is not None else "Missing specification"
        parent.labels = ["candidate scope"]
        if under_approval:
            parent.labels.append(operation.scope_labels[ScopeLabel.APPROVED.value])
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
        criteria=criteria,
    )
    # The built port, reachable from the board a case already holds: a case
    # about what the owner does with an answer replaces one method here
    # instead of standing up a second tracker beside this one.
    board.built_tracker = tracker
    workspace = RecordingWorkspace()
    rows = stage_rows(
        operation.resolve_organize_mandates(), under_approval=under_approval
    )
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
        **(
            {}
            if tick
            else {
                "repo_url": "https://example.invalid/repository",
                # *phases* narrows the table this owner runs, through the same
                # public constructor: a case about one row's own pre-check
                # states its table rather than reaching into the built owner.
                "phases": rows if phases is None else phases(rows),
            }
        ),
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


async def test_the_pre_approval_owner_grooms_alone_and_reentry_writes_nothing():
    """The scheduled pass's owner is given the pre-approval row and only that.

    Grooming ends at approval, so the two run-stage markers and the
    criterion child belong to a scope run and are absent here.
    """
    owner, board, executor = factory()
    report = await run_owner(owner)
    assert report.halt is None
    assert [phase.value for phase in report.completed_phases] == ["groom"]
    parent = board.server.issues[CLAIMED_ISSUE]
    assert "graph complete" in parent.labels
    assert not {"body complete", "criteria complete"} & set(parent.labels)
    assert not any(
        issue.parent_id == CLAIMED_ISSUE for issue in board.server.issues.values()
    )
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


async def test_the_run_stage_owner_does_ticket_then_criteria_and_replays_dry():
    """Both run stages over an approved scope, in the governed order.

    The replay is the label-as-record contract: every member already carries
    both markers, so both stages complete with no session and no write.
    """
    owner, board, executor = factory(under_approval=True)
    report = await run_owner(owner)
    assert report.halt is None
    assert [phase.value for phase in report.completed_phases] == ["ticket", "criteria"]
    parent = board.server.issues[CLAIMED_ISSUE]
    assert {"body complete", "criteria complete"} <= set(parent.labels)
    children = [
        issue
        for issue in board.server.issues.values()
        if issue.parent_id == CLAIMED_ISSUE
    ]
    assert len(children) == 1
    assert children[0].status_type == "unstarted"
    assert children[0].description.endswith("**Evidence:**\n")
    assert parent.labels.count("approved scope") == 1
    board.calls.clear()
    sessions = len(executor.calls)
    second = await run_owner(owner)
    assert second.halt is None
    assert [phase.value for phase in second.completed_phases] == ["ticket", "criteria"]
    assert not [(name, args) for name, args in board.calls if name.startswith("save_")]
    assert len(executor.calls) == sessions


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
    with pytest.raises(OrganizeWriteRefusalError, match="groom is not admitted"):
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


async def test_the_owner_renders_the_rows_own_rubric_into_both_judging_prompts():
    """The row's rubric, not its admission role, is what both judges are shown.

    The pre-approval row names the organizational rubric as its rubric and
    the shared assess role as its admission role. The prompts the owner
    actually sends — the assessment and the independent verification —
    each carry every part of the organizational predicate, none of the
    implementation test, and the member's own body.
    """
    from tests.prompts.test_organize_rubrics import (
        FOUR_PARTS,
        IMPLEMENTATION_TEST,
        normalised,
    )

    owner, _, executor = factory(
        body=PREPARED_BODY, groom_rubric_key="organize_groom_rubric"
    )
    report = await run_owner(owner)
    assert report.completed_phases == (MandateKind.GROOM,)
    judged = [
        call["prompt"]
        for call in executor.calls
        if call["output_format"]["schema"].get("title") == "AdmissionJudgment"
    ]
    verified = [prompt for prompt in judged if VERIFY_OPENING in prompt]
    assessed = [prompt for prompt in judged if VERIFY_OPENING not in prompt]
    assert verified
    assert assessed
    for prompt in judged:
        for part in FOUR_PARTS:
            assert part in normalised(prompt), part
        for claim in IMPLEMENTATION_TEST:
            assert claim not in prompt, claim
        assert PREPARED_BODY in prompt


async def test_an_approved_scope_admits_nobody_to_grooming_and_opens_no_session():
    """Approval ends the pre-approval phase for every member at once.

    The row is not refused and does not halt: it has nobody left to act on,
    so no session opens and no write is made. This is what stands grooming
    down the moment approval lands, and it is the same reading a run stage
    is admitted by.
    """
    owner, board, executor = factory()
    board.server.issues[CLAIMED_ISSUE].labels.append("approved scope")
    report = await run_owner(owner)
    assert report.completed_phases == ()
    assert report.halt is None
    assert executor.calls == []
    assert not [(name, args) for name, args in board.calls if name.startswith("save_")]


@pytest.mark.parametrize(
    ("members", "grooms"),
    [(("triage",), True), (("approved",), False), ((), False)],
    ids=["triage-only", "approved-only", "neither"],
)
async def test_the_triage_member_dispatches_and_the_approved_member_alone_does_not(
    members, grooms
):
    """The gate is the row's configured member and nothing else.

    The approved-only arm removes the triage member instead of adding approval
    beside it, so the case distinguishes "approval stood grooming down" from
    "the triage member is what opened the gate in the first place".
    """
    labels = declared_operation().scope_labels
    owner, board, executor = factory()
    parent = board.server.issues[CLAIMED_ISSUE]
    parent.labels = [labels[member] for member in members]
    report = await run_owner(owner)
    assert report.halt is None
    if grooms:
        assert [phase.value for phase in report.completed_phases] == ["groom"]
        assert "graph complete" in parent.labels
        assert executor.calls
        return
    assert report.completed_phases == ()
    assert executor.calls == []
    assert "graph complete" not in parent.labels
    assert not [(name, args) for name, args in board.calls if name.startswith("save_")]


@pytest.mark.parametrize(
    ("members", "grooms"),
    [(frozenset({ScopeLabel.TRIAGE}), True), (frozenset(), False)],
    ids=["project-carries-triage", "project-carries-nothing"],
)
async def test_a_project_addressed_scope_carrying_triage_dispatches_groom(
    members, grooms
):
    """The same gate on a project-addressed scope, through the production build.

    ``build_scope_organizer`` on the pre-approval side of the table, over a
    board whose project is the addressed scope: the project carrying the
    triage member grooms its lane, and the same project carrying nothing
    opens no session and writes nothing.
    """
    assert SCOPE.kind is ScopeKind.PROJECT
    lanes = ("A",)
    port = scope_board(lanes=lanes, approved=False)
    port.scope_label_members[SCOPE] = members
    organizer, operation, executor = groomer(port, lanes=lanes)

    report = await organizer.run(
        scope=SCOPE, repository=operation.repos[0], job_id="groom-job"
    )

    assert report.halt is None
    if grooms:
        assert report.completed_phases == (MandateKind.GROOM,)
        assert port.classification_writes == [("A", GROOM_MARKER)]
        assert sorted({key for key, _, _ in executor.admissions}) == ["A"]
        return
    assert report.completed_phases == ()
    assert executor.organize_calls == []
    assert port.classification_writes == []


#: A pre-approval gate on a configured member other than the shipped one.
PROPOSED_GATE = "scope_labels.proposed"


@pytest.mark.parametrize(
    ("members", "grooms"),
    [(("proposed",), True), (("triage",), False)],
    ids=["configured-member", "triage-alone"],
)
async def test_a_groom_row_gated_on_another_member_opens_on_that_member_alone(
    monkeypatch, members, grooms
):
    """The member the gate asks for is the row's, read off its configured key.

    With the pre-approval row repointed at the proposed member, a scope that
    carries it is groomed, and a scope that carries triage alone is not: the
    shipped member is not what opens the gate, the configured one is. Every
    resolution asks for the member the row's key names.
    """
    labels = declared_operation().scope_labels
    owner, board, executor = factory(groom_gate_key=PROPOSED_GATE)
    parent = board.server.issues[CLAIMED_ISSUE]
    parent.labels = [labels[member] for member in members]
    original = organize_owner.scope_carries
    seen = []

    async def recording(*, ref, member, tracker):
        seen.append(member)
        return await original(ref=ref, member=member, tracker=tracker)

    monkeypatch.setattr(organize_owner, "scope_carries", recording)
    report = await run_owner(owner)

    assert report.halt is None
    assert seen
    assert set(seen) == {ScopeLabel(split_label_key(PROPOSED_GATE)[1])}
    if grooms:
        assert report.completed_phases == (MandateKind.GROOM,)
        assert "graph complete" in parent.labels
        assert executor.calls
        return
    assert report.completed_phases == ()
    assert executor.calls == []
    assert "graph complete" not in parent.labels
    assert not [(name, args) for name, args in board.calls if name.startswith("save_")]


@pytest.mark.parametrize(
    "children", [0, 1, 2], ids=["alone", "one-child", "two-children"]
)
async def test_the_scope_gate_is_resolved_once_per_reading_by_the_one_resolver(
    monkeypatch, children
):
    """Every resolution is the addressed scope's, with the row's own member.

    A member is a property of the scope and of the containers above it, so the
    count follows the readings the pass makes and not the number of members it
    has: a resolution per member issue would be the per-issue materialization
    the trigger must not be. The scope is read with one, two and three
    members, and every reading resolves exactly once on each.
    """
    from tests.fakes import FakeMcpIssue

    owner, board, _ = factory()
    for index in range(children):
        board.server.issues[f"groom-child-{index}"] = FakeMcpIssue(
            id=f"groom-child-{index}",
            parent_id=CLAIMED_ISSUE,
            description="Missing specification",
        )
    original = organize_owner.scope_carries
    seen = []

    async def recording(*, ref, member, tracker):
        seen.append((ref, member))
        return await original(ref=ref, member=member, tracker=tracker)

    reading = organize_owner.OrganizeOwner._carried_members
    readings = []

    async def counted(self, scope, phase):
        # The reading is named by where it is made: the owner method that
        # asks, and the name the answer is bound to there.
        caller = inspect.currentframe().f_back
        line = linecache.getline(caller.f_code.co_filename, caller.f_lineno)
        readings.append((caller.f_code.co_name, line.strip().split(" = ")[0]))
        return await reading(self, scope, phase)

    monkeypatch.setattr(organize_owner, "scope_carries", recording)
    monkeypatch.setattr(organize_owner.OrganizeOwner, "_carried_members", counted)
    report = await run_owner(owner)
    assert [phase.value for phase in report.completed_phases] == ["groom"]
    scope = ScopeRef(kind=ScopeKind.ISSUE, key=CLAIMED_ISSUE)
    # The board is the size this arm says: the scope's own issue and each
    # child, read through the same port the owner reads.
    assert len(await board.built_tracker.scope_issues(ref=scope)) == children + 1
    members = {
        issue.id for issue in board.server.issues.values() if issue.id != CLAIMED_ISSUE
    }
    assert {ref for ref, _ in seen} == {scope}
    assert {member for _, member in seen} == {ScopeLabel.TRIAGE}
    # One resolution per reading, whatever the number of members.
    assert readings
    assert len(seen) == len(readings)
    # The round's own gate reading, the marker sweep's and the barrier's are
    # one each on every board: adding a member adds none of them. Only the
    # pre-write re-checks scale, one per write the owner sends.
    by_site = Counter(readings)
    rechecks = by_site.pop(("_may_write", "gate_members"))
    assert by_site == {
        ("_converge", "gate_members"): 1,
        ("_converge", "current_members"): 1,
        ("run", "settled_members"): 1,
    }, by_site
    if not children:
        # Observed, then written: seven pre-write re-checks across the author
        # write and the marker write, each of which re-asks the gate before
        # it touches the board, besides the three readings above.
        assert rechecks == 7
        assert len(seen) == 10
    else:
        assert rechecks > 7
    assert not {ref.key for ref, _ in seen} & members


def record_classification_writes(board, monkeypatch):
    """Every marker write the owner sends, recorded around the built port.

    The adapter's own write still runs, so the lease check and the board's
    answer are the real ones; the wrapper only keeps what was asked of it.
    """
    port = board.built_tracker
    original = port.set_issue_classification
    writes = []

    async def recording(*, issue_key, classification, holder=None):
        writes.append((issue_key, classification, holder))
        return await original(
            issue_key=issue_key, classification=classification, holder=holder
        )

    monkeypatch.setattr(port, "set_issue_classification", recording)
    return writes


async def test_the_marker_write_carries_the_run_as_its_holder(monkeypatch):
    """The terminal write is the leased run's, and the adapter checks it.

    Every marker write names the job the pass ran under as its holder. The
    control shows the argument is load-bearing: over a fresh board where no
    run holds the member's label surface, the same write under the same holder
    refuses before it touches the board.
    """
    owner, board, _ = factory()
    writes = record_classification_writes(board, monkeypatch)
    report = await run_owner(owner)
    assert [phase.value for phase in report.completed_phases] == ["groom"]
    assert writes
    assert {holder for _, _, holder in writes} == {"actual-organize-job"}

    _, fresh, _ = factory()
    with pytest.raises(SurfaceLeaseError):
        await fresh.built_tracker.set_issue_classification(
            issue_key=CLAIMED_ISSUE,
            classification="groomed",
            holder="actual-organize-job",
        )
    assert "graph complete" not in fresh.server.issues[CLAIMED_ISSUE].labels


async def test_the_marker_write_is_keyed_on_the_member_and_the_marker(monkeypatch):
    """One write per member, of the phase's own marker, and none on a replay.

    The write names the configured marker member; the board shows the label
    the operation spells it as. On a second entry every member already
    carries the marker, so the stage roster owes nothing and the phase opens
    no round at all: the replay is the roster's, and it never reaches the
    terminal act. The terminal act's own set-if-different is the partially
    marked case below.
    """
    owner, board, _ = factory()
    writes = record_classification_writes(board, monkeypatch)
    await run_owner(owner)
    marked = sorted(
        issue.id
        for issue in board.server.issues.values()
        if "graph complete" in issue.labels
    )
    assert marked
    assert sorted(issue_key for issue_key, _, _ in writes) == marked
    assert {classification for _, classification, _ in writes} == {"groomed"}

    writes.clear()
    report = await run_owner(owner)
    assert report.halt is None
    assert writes == []


def judged_members(executor):
    """The member each write-back judgment was opened on, in session order."""
    return [
        json.loads(
            re.search(
                r"<written_artifact>\s*(.*?)\s*</written_artifact>",
                call["prompt"],
                re.S,
            )[1]
        )["nativeRef"]
        for call in executor.calls
        if call["output_format"]["schema"].get("title") == "WriteBackFinding"
    ]


async def test_a_partially_marked_scope_marks_only_the_member_that_lacks_it(
    monkeypatch,
):
    """The terminal act is set-if-different on the member it is keyed on.

    Two members under one scope reach the marker sweep: one already carries
    the phase marker and one does not, and neither is the scope's own key.
    Exactly one marker write is sent, onto the member that lacks it, under the
    job as its holder. The marked member is sent no write, is never leased
    and is never judged.
    """
    from tests.fakes import FakeMcpIssue

    owner, board, executor = factory(body=PREPARED_BODY)
    board.server.issues[CLAIMED_ISSUE].labels.append("graph complete")
    board.server.issues["marked-child"] = FakeMcpIssue(
        id="marked-child",
        parent_id=CLAIMED_ISSUE,
        description=PREPARED_BODY,
        labels=["graph complete"],
    )
    # A configured issue label that is not the marker: the member lacking
    # the marker is told apart from one carrying any label at all.
    board.server.issues["unmarked-child"] = FakeMcpIssue(
        id="unmarked-child",
        parent_id=CLAIMED_ISSUE,
        description=PREPARED_BODY,
        labels=["candidate issue"],
    )
    writes = record_classification_writes(board, monkeypatch)
    report = await run_owner(owner)

    assert report.halt is None
    assert report.completed_phases == (MandateKind.GROOM,)
    assert writes == [("unmarked-child", "groomed", "actual-organize-job")]
    leased = [
        args.get("issueId")
        for name, args in board.calls
        if name == "save_comment"
        and "kind: lease\n" in str(args.get("body", ""))
        and args.get("issueId") is not None
    ]
    assert leased == ["unmarked-child"]
    assert judged_members(executor) == ["unmarked-child"]
    for key in (CLAIMED_ISSUE, "marked-child", "unmarked-child"):
        assert "graph complete" in board.server.issues[key].labels


def swallow_marker_writes(board, executor, monkeypatch):
    """A board that accepts the marker write and does not keep it.

    The replacement records the call and the number of sessions opened before
    it, and answers with the member exactly as the board still holds it: the
    write raised nothing, and the label set is unchanged.
    """
    port = board.built_tracker
    writes = []

    async def swallowing(*, issue_key, classification, holder=None):
        writes.append((issue_key, classification, len(executor.calls)))
        return await port.read_planning_issue(issue_key=issue_key)

    monkeypatch.setattr(port, "set_issue_classification", swallowing)
    return writes


def session_titles(calls):
    return [call["output_format"]["schema"].get("title") for call in calls]


@pytest.mark.parametrize(
    ("under_approval", "marker", "label"),
    [(False, "groomed", "graph complete"), (True, "body", "body complete")],
    ids=["pre-approval-groom", "run-stage-ticket"],
)
async def test_a_marker_the_board_does_not_report_halts_the_mandate(
    monkeypatch, under_approval, marker, label
):
    """The pass reads the member back itself, and before any judge is asked.

    The refusal is the typed write refusal, raised inside the leased write:
    no completed phase is reported, the marker is absent from the board, and
    no write-back judgment is opened for the member after the write although
    admission judgments did run before it. The same holds for the
    pre-approval row's marker and for the first run stage's, since both are
    written by the one terminal act: each row writes its own configured
    marker key, which the board shows as that key's label.
    """
    owner, board, executor = factory(under_approval=under_approval)
    # A configured label the member already carries and that is not the
    # marker: the refusal is about the marker's absence, not an empty set.
    board.server.issues[CLAIMED_ISSUE].labels.append("candidate issue")
    writes = swallow_marker_writes(board, executor, monkeypatch)
    with pytest.raises(OrganizeWriteRefusalError, match="did not read back"):
        await run_owner(owner)
    assert [(key, written) for key, written, _ in writes] == [(CLAIMED_ISSUE, marker)]
    assert "candidate issue" in board.server.issues[CLAIMED_ISSUE].labels
    assert label not in board.server.issues[CLAIMED_ISSUE].labels
    opened_before = writes[0][2]
    assert "AdmissionJudgment" in session_titles(executor.calls[:opened_before])
    assert session_titles(executor.calls[opened_before:]) == []


async def test_a_read_back_that_answers_another_member_halts_the_mandate(
    monkeypatch,
):
    """The member read back must be the member written, not one like it.

    After the marker write, the board answers the member's re-read with a
    different member that does carry the marker. The label set alone would
    pass; the pass refuses because the answer is not the member it wrote,
    and no write-back judgment is opened after the write.
    """
    from tests.fakes import FakeMcpIssue

    owner, board, executor = factory(body=PREPARED_BODY)
    board.server.issues["marked-child"] = FakeMcpIssue(
        id="marked-child",
        parent_id=CLAIMED_ISSUE,
        description=PREPARED_BODY,
        labels=["graph complete"],
    )
    port = board.built_tracker
    original_write = port.set_issue_classification
    original_read = port.read_planning_issue
    written = []

    async def writing(*, issue_key, classification, holder=None):
        answer = await original_write(
            issue_key=issue_key, classification=classification, holder=holder
        )
        written.append((issue_key, len(executor.calls)))
        return answer

    async def reading(*, issue_key):
        if written and issue_key == written[-1][0]:
            return await original_read(issue_key="marked-child")
        return await original_read(issue_key=issue_key)

    monkeypatch.setattr(port, "set_issue_classification", writing)
    monkeypatch.setattr(port, "read_planning_issue", reading)
    with pytest.raises(OrganizeWriteRefusalError, match="did not read back") as caught:
        await run_owner(owner)
    assert written == [(CLAIMED_ISSUE, written[0][1])]
    assert caught.value.issue_key == CLAIMED_ISSUE
    assert session_titles(executor.calls[written[0][1] :]) == []


async def test_a_swallowed_write_on_a_later_member_halts_the_mandate(monkeypatch):
    """Every member's marker is read back, not only the first one written.

    Two members reach the marker sweep. The board keeps the first write and
    swallows the second: the refusal names the second member, and no
    write-back judgment is opened for it.
    """
    from tests.fakes import FakeMcpIssue

    owner, board, executor = factory(body=PREPARED_BODY)
    board.server.issues["second"] = FakeMcpIssue(
        id="second", parent_id=CLAIMED_ISSUE, description=PREPARED_BODY
    )
    port = board.built_tracker
    original_write = port.set_issue_classification
    written = []

    async def second_swallowed(*, issue_key, classification, holder=None):
        written.append(issue_key)
        if len(written) == 2:
            return await port.read_planning_issue(issue_key=issue_key)
        return await original_write(
            issue_key=issue_key, classification=classification, holder=holder
        )

    monkeypatch.setattr(port, "set_issue_classification", second_swallowed)
    with pytest.raises(OrganizeWriteRefusalError, match="did not read back") as caught:
        await run_owner(owner)
    assert len(written) == 2
    first, swallowed = written
    assert first != swallowed
    assert caught.value.issue_key == swallowed
    assert "graph complete" in board.server.issues[first].labels
    assert "graph complete" not in board.server.issues[swallowed].labels
    assert judged_members(executor) == [first]


async def test_the_marker_gate_is_not_the_judges_verdict(monkeypatch):
    """Every judgment the pass did ask for held, and the pass still refused.

    The judge double answers ``holds`` for any artifact it is shown, so a
    verdict cannot be what stopped the phase: the read-back is a gate of its
    own, not a verification round the judge can pass.
    """
    owner, board, executor = factory()
    swallow_marker_writes(board, executor, monkeypatch)
    with pytest.raises(OrganizeWriteRefusalError, match="did not read back"):
        await run_owner(owner)
    judged = [
        call
        for call in executor.calls
        if call["output_format"]["schema"].get("title") == "WriteBackFinding"
    ]
    # Observed, then written: the author write's one verification round.
    assert len(judged) == 1
    assert "graph complete" not in board.server.issues[CLAIMED_ISSUE].labels


async def test_a_marker_that_reads_back_completes_the_phase(monkeypatch):
    """The green control: the same pass, over a board that keeps the write.

    Recorded from the moment the write returns, so the adapter's own read
    inside the write is not counted: the member is read twice before its
    write-back judgment opens — the pass's own read-back, then the
    verifier's re-read of the artifact — and the phase completes on the label
    the board reports. A read-back moved after the judgment would leave one.
    """
    owner, board, executor = factory()
    port = board.built_tracker
    original_write = port.set_issue_classification
    original_read = port.read_planning_issue
    events = []

    async def writing(*, issue_key, classification, holder=None):
        answer = await original_write(
            issue_key=issue_key, classification=classification, holder=holder
        )
        events.append(("write", issue_key, len(executor.calls)))
        return answer

    async def reading(*, issue_key):
        events.append(("read", issue_key, len(executor.calls)))
        return await original_read(issue_key=issue_key)

    monkeypatch.setattr(port, "set_issue_classification", writing)
    monkeypatch.setattr(port, "read_planning_issue", reading)
    report = await run_owner(owner)
    assert report.halt is None
    assert [phase.value for phase in report.completed_phases] == ["groom"]
    assert "graph complete" in board.server.issues[CLAIMED_ISSUE].labels
    written = next(i for i, event in enumerate(events) if event[0] == "write")
    titles = session_titles(executor.calls)
    judged = next(
        i
        for i, title in enumerate(titles)
        if i >= events[written][2] and title == "WriteBackFinding"
    )
    reads_before_judgment = [
        event
        for event in events[written + 1 :]
        if event[:2] == ("read", CLAIMED_ISSUE) and event[2] <= judged
    ]
    assert len(reads_before_judgment) == 2, events


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
    with pytest.raises(OrganizeWriteRefusalError, match="groom is not admitted"):
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
    # Once per row this owner was given, and the pre-approval owner is given one.
    assert observed == [(), first_round, both_rounds] * 1
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


MANDATE_SENTENCE = (
    "Every child criterion must restate the source version in its own prose."
)
REGROWTH_CLASS = "source_version_restated_in_prose"
DRAFT_BODY = "Hand-drafted source awaiting preparation."
GROUNDED_BODY = "Prepared body grounded in the source."
#: The same child with the instance of the class taken out of its prose: it
#: states its own check and restates no source version.
REFERENCING_BODY = "The child states its check and nothing about the source version."
RESTATING_BODY = "The child restates the source version in its own prose."


def regrowth(monkeypatch, *, mandate, instance=True):
    """Script one authoring step that carries any mandating sentence forward.

    Over the run-stage owner, because the body this scripts is the ticket
    stage's own write.

    *instance* plants or withholds the instance of the class in the child's
    prose and changes nothing else — in particular not the scripted
    verification, which reads the mandating sentence off the parent body. The
    sentence is therefore the only fact either run turns on.
    """
    from tests.fakes import FakeMcpIssue

    owner, board, executor = factory(
        body=f"{MANDATE_SENTENCE} {DRAFT_BODY}" if mandate else DRAFT_BODY,
        convergence_bound=2,
        under_approval=True,
    )
    board.server.issues["restating-criterion"] = FakeMcpIssue(
        id="restating-criterion",
        parent_id=CLAIMED_ISSUE,
        description=RESTATING_BODY if instance else REFERENCING_BODY,
        labels=["check"],
    )
    observed = []
    original = executor.stream

    async def scripted(**kwargs):
        title = kwargs["output_format"]["schema"].get("title")
        async for event in original(**kwargs):
            payload = event.structured_output
            if title == "OrganizeProposal" and payload.get("kind") == "body":
                source = board.server.issues[payload["issue_id"]].description
                carried = f"{MANDATE_SENTENCE} " if MANDATE_SENTENCE in source else ""
                event = result(
                    structured_output={**payload, "body": f"{carried}{GROUNDED_BODY}"}
                )
            elif (
                title == "AdmissionJudgment"
                and payload["issue_id"] == CLAIMED_ISSUE
                and DRAFT_BODY in board.server.issues[CLAIMED_ISSUE].description
            ):
                event = result(
                    structured_output={
                        "issue_id": CLAIMED_ISSUE,
                        "verdict": "not_buildable",
                        "evidence": "The hand-drafted source is not prepared.",
                        "refusal_kind": "spec_gap",
                        "invented_decision": "Prepare the drafted source.",
                    }
                )
            elif (
                title == "AdmissionJudgment"
                and payload["issue_id"] == "restating-criterion"
                and "Adversarially verify the current issue" in kwargs["prompt"]
                and MANDATE_SENTENCE in board.server.issues[CLAIMED_ISSUE].description
            ):
                event = result(
                    structured_output={
                        **payload,
                        "findings": [
                            {
                                "issue_id": "restating-criterion",
                                "defect_class": REGROWTH_CLASS,
                                "evidence": (
                                    "The child's prose restates the source version."
                                ),
                                "role": "mandate",
                                "mandate_text": MANDATE_SENTENCE,
                            }
                        ],
                    }
                )
            if title == "AdmissionJudgment":
                observed.extend(
                    f["defect_class"]
                    for f in event.structured_output.get("findings", ())
                )
            yield event

    monkeypatch.setattr(executor, "stream", scripted)
    return owner, board, executor, observed


def child_verifications(executor):
    """How many verify sessions were spent on the planted criterion child.

    One per dry round, which is what makes this a count of dry rounds. A
    criterion child is reached by the dry round's scope-wide verification
    alone — the in-round verification is of the subject the step authored —
    so a second dry round in any phase doubles this number.

    ``VERIFY_OPENING`` is the opening the verify dispatch carries and the
    assess dispatch does not; the issue a session was spent on is the last
    key its prompt names, as the module reads it everywhere else.
    """
    return len(
        [
            call
            for call in executor.calls
            if call["output_format"]["schema"].get("title") == "AdmissionJudgment"
            and VERIFY_OPENING in call["prompt"]
            and re.findall(r"<issue_key>(.*?)</issue_key>", call["prompt"])[-1:]
            == ["restating-criterion"]
        ]
    )


async def test_live_mandate_regrows_the_class_and_the_pass_does_not_converge(
    monkeypatch,
):
    owner, board, executor, observed = regrowth(monkeypatch, mandate=True)
    report = await run_owner(owner)
    assert observed == [REGROWTH_CLASS, REGROWTH_CLASS]
    assert report.halt.cause == "convergence_exhausted"
    assert report.halt.bound.setting == "organize.max_convergence_rounds"
    assert report.halt.bound.value == report.halt.bound.rounds_used == 2
    assert report.completed_phases == ()
    surviving = report.halt.surviving_findings
    assert [f.defect_class for f in surviving] == [REGROWTH_CLASS]
    assert surviving[0].mandate_text == MANDATE_SENTENCE
    # The surface the finding names is the child, whose prose is where the
    # instance was planted or withheld; the sentence sits on the parent.
    assert surviving[0].issue_id == "restating-criterion"
    assert (
        board.server.issues[CLAIMED_ISSUE].description
        == f"{MANDATE_SENTENCE} {GROUNDED_BODY}"
    )
    assert any(
        REGROWTH_CLASS in call["prompt"]
        for call in executor.calls
        if call["output_format"]["schema"].get("title") == "OrganizeProposal"
    )
    assert not set(board.server.issues[CLAIMED_ISSUE].labels) & {
        "graph complete",
        "body complete",
        "criteria complete",
    }


async def test_removed_mandate_leaves_the_same_authoring_step_dry(monkeypatch):
    owner, board, executor, observed = regrowth(monkeypatch, mandate=False)
    report = await run_owner(owner)
    assert REGROWTH_CLASS not in observed
    assert report.halt is None
    assert [phase.value for phase in report.completed_phases] == ["ticket", "criteria"]
    parent = board.server.issues[CLAIMED_ISSUE]
    assert parent.description == GROUNDED_BODY
    assert [
        call
        for call in executor.calls
        if call["output_format"]["schema"].get("title") == "OrganizeProposal"
    ]
    # One dry round per phase and no more: the first dry round of each phase
    # is the one that converges it.
    assert child_verifications(executor) == len(report.completed_phases)
    assert {"body complete", "criteria complete"} <= set(parent.labels)


def board_state(board):
    """Every native issue, whole, and every comment on the workspace.

    Whole, because "the sentence is the only difference" is a claim about
    everything either board carries, not about the handful of fields a reader
    of this file happened to think of: a title, a status or a seeded comment
    differing between the two runs would leave the claim false and the
    assertion green.
    """
    return dict(board.server.issues), list(board.server.comments)


@pytest.mark.parametrize("instance", [True, False])
@pytest.mark.parametrize("mandate", [True, False])
async def test_the_sentence_and_not_the_instance_decides_whether_the_class_regrows(
    monkeypatch, mandate, instance
):
    """The mandating sentence decides regrowth; the instance does not.

    With the sentence on the parent the class regrows and the pass does not
    converge whether or not the child's prose carries an instance of it, and
    nobody plants an instance where there was none. Without the sentence the
    same board converges, on the first dry round of each phase.
    """
    owner, board, executor, observed = regrowth(
        monkeypatch, mandate=mandate, instance=instance
    )
    report = await run_owner(owner)
    assert (REGROWTH_CLASS in observed) is mandate
    assert (report.halt is None) is (not mandate)
    if not mandate:
        assert [phase.value for phase in report.completed_phases] == [
            "ticket",
            "criteria",
        ]
        assert child_verifications(executor) == len(report.completed_phases)
        return
    assert report.halt.cause == "convergence_exhausted"
    assert report.halt.bound.value == report.halt.bound.rounds_used == 2
    surviving = report.halt.surviving_findings
    assert [f.defect_class for f in surviving] == [REGROWTH_CLASS]
    assert surviving[0].mandate_text == MANDATE_SENTENCE
    assert surviving[0].issue_id == "restating-criterion"
    assert board.server.issues["restating-criterion"].description == (
        RESTATING_BODY if instance else REFERENCING_BODY
    )


@pytest.mark.parametrize("instance", [True, False])
async def test_the_two_mandate_runs_differ_only_by_the_sentence(monkeypatch, instance):
    """One prefix on one body is the whole difference between the two runs.

    Read off both boards before either runs, the two states are equal once the
    sentence is taken off the parent of the live one — and the sentence is on
    nothing else. Equal in every field of every native issue and in every
    comment, so nothing else can be quietly differing. The landed outcomes
    then differ.
    """
    live_owner, live_board, _live_executor, live_observed = regrowth(
        monkeypatch, mandate=True, instance=instance
    )
    dry_owner, dry_board, _dry_executor, dry_observed = regrowth(
        monkeypatch, mandate=False, instance=instance
    )
    live_issues, live_comments = board_state(live_board)
    dry_issues, dry_comments = board_state(dry_board)
    assert (live_issues, live_comments) != (dry_issues, dry_comments)
    assert [
        key
        for key, native in live_issues.items()
        if MANDATE_SENTENCE in native.description
    ] == [CLAIMED_ISSUE]
    assert (
        {
            key: (
                dataclasses.replace(
                    native,
                    description=native.description.removeprefix(f"{MANDATE_SENTENCE} "),
                )
                if key == CLAIMED_ISSUE
                else native
            )
            for key, native in live_issues.items()
        },
        live_comments,
    ) == (dry_issues, dry_comments)

    live_report = await run_owner(live_owner)
    dry_report = await run_owner(dry_owner)
    assert REGROWTH_CLASS in live_observed
    assert REGROWTH_CLASS not in dry_observed
    assert live_report.halt.cause == "convergence_exhausted"
    assert live_report.completed_phases == ()
    assert dry_report.halt is None
    assert [phase.value for phase in dry_report.completed_phases] == [
        "ticket",
        "criteria",
    ]


ESCALATION_MARKER = "[organize-question:"


def stage_report_step(monkeypatch, board, *, raises=None):
    """Record every stage-report construction in the call log, optionally failing."""
    from kodezart.services import organize_owner

    base = organize_owner.StageHaltReport

    class StageReportStep:
        def __call__(self, *args, **kwargs):
            return self._step(base, *args, **kwargs)

        def model_validate(self, *args, **kwargs):
            return self._step(base.model_validate, *args, **kwargs)

        def _step(self, construct, *args, **kwargs):
            board.calls.append(("stage_report", {}))
            if raises is not None:
                raise raises
            return construct(*args, **kwargs)

    monkeypatch.setattr(organize_owner, "StageHaltReport", StageReportStep())


async def test_escalation_and_its_classification_land_before_the_stage_report(
    monkeypatch,
):
    owner, board, _ = factory(refuse_forever=True, bound=1)
    stage_report_step(monkeypatch, board)
    report = await run_owner(owner)
    assert report.halt.cause == "admission_exhausted"
    names = [name for name, _ in board.calls]
    question = next(
        index
        for index, (name, args) in enumerate(board.calls)
        if name == "save_comment"
        and str(args.get("body", "")).startswith(ESCALATION_MARKER)
    )
    classification = next(
        index
        for index, (name, args) in enumerate(board.calls)
        if name == "save_issue" and "needs decision" in args.get("addLabels", ())
    )
    assert names.count("stage_report") == 1
    assert question < classification < names.index("stage_report")


async def test_a_raising_stage_report_leaves_the_escalation_recorded(monkeypatch):
    owner, board, _ = factory(refuse_forever=True, bound=1)
    stage_report_step(monkeypatch, board, raises=RuntimeError("stage report failed"))
    with pytest.raises(RuntimeError, match="stage report failed"):
        await run_owner(owner)
    escalations = [
        comment
        for comment in board.server.comments
        if comment.body.startswith(ESCALATION_MARKER)
    ]
    assert len(escalations) == 1
    assert "The current body omits the required source." in escalations[0].body
    assert "needs decision" in board.server.issues[CLAIMED_ISSUE].labels


async def test_approval_withdrawn_during_a_stage_author_write_refuses_the_write(
    monkeypatch,
):
    """A run stage is admitted by approval, and re-asked before it writes."""
    from kodezart.domain.errors import OrganizeWriteRefusalError

    owner, board, executor = factory(under_approval=True)
    original = executor.stream

    async def withdrawn(**kwargs):
        async for event in original(**kwargs):
            if kwargs["output_format"]["schema"].get("title") == "OrganizeProposal":
                labels = board.server.issues[CLAIMED_ISSUE].labels
                if "approved scope" in labels:
                    labels.remove("approved scope")
            yield event

    monkeypatch.setattr(executor, "stream", withdrawn)
    with pytest.raises(OrganizeWriteRefusalError, match="ticket is not admitted"):
        await run_owner(owner)
    assert not [(name, args) for name, args in board.calls if name == "save_issue"]


async def test_an_escalated_member_holds_its_stage_by_name_before_any_session():
    """A member carrying the escalation label owes the stage label and lacks it.

    It is counted, named in the halt, and no session is spent on it — and the
    next stage is never reached, so its own marker never lands either.
    """
    from tests.fakes import FakeMcpIssue

    owner, board, executor = factory(under_approval=True)
    board.server.issues["escalated-child"] = FakeMcpIssue(
        id="escalated-child",
        parent_id=CLAIMED_ISSUE,
        description="A member a person still has to decide about.",
        labels=["needs decision"],
    )
    report = await run_owner(owner)
    assert report.completed_phases == ()
    assert report.halt.cause == "stage_incomplete"
    assert report.halt.phase.value == "ticket"
    assert report.halt.unlabelled_issue_ids == ("escalated-child",)
    assert report.halt.bound is None
    assert report.halt.admission_results == ()
    assert executor.calls == []
    assert not [(name, args) for name, args in board.calls if name.startswith("save_")]
    assert not set(board.server.issues[CLAIMED_ISSUE].labels) & {
        "body complete",
        "criteria complete",
    }


#: A body an admission is granted on, so the stage reaches its marker loop.
PREPARED_BODY = "Prepared body grounded in the source."


async def test_a_phase_that_leaves_a_member_unlabelled_does_not_reach_the_next(
    monkeypatch,
):
    """The barrier reads a fresh snapshot after the stage's own writes.

    Admission shuts between the pre-check and the marker loop, so nobody is
    marked. The stage neither completes nor advances: it names every member
    that still owes its marker, under its own name, and the next stage never
    opens a session — so that stage's marker never lands either.
    """
    from tests.fakes import FakeMcpIssue

    owner, board, executor = factory(under_approval=True, body=PREPARED_BODY)
    board.server.issues["second"] = FakeMcpIssue(
        id="second", parent_id=CLAIMED_ISSUE, description=PREPARED_BODY
    )
    original = executor.stream

    async def withdrawn(**kwargs):
        if "Adversarially verify the current issue" in kwargs["prompt"]:
            labels = board.server.issues[CLAIMED_ISSUE].labels
            if "approved scope" in labels:
                labels.remove("approved scope")
        async for event in original(**kwargs):
            yield event

    monkeypatch.setattr(executor, "stream", withdrawn)
    report = await run_owner(owner)

    assert report.halt is not None
    assert report.halt.cause is StageHaltCause.STAGE_INCOMPLETE
    assert report.halt.phase.value == "ticket"
    assert set(report.halt.unlabelled_issue_ids) == {CLAIMED_ISSUE, "second"}
    assert report.completed_phases == ()
    assert not set(board.server.issues[CLAIMED_ISSUE].labels) & {
        "body complete",
        "criteria complete",
    }
    assert not [
        call
        for call in executor.calls
        if "Author criterion sub-issue proposals" in call["prompt"]
    ]


async def test_the_criteria_barrier_names_a_member_left_unlabelled(monkeypatch):
    """The barrier stands after the second run stage too, not the first alone.

    The first stage runs through and lands its marker; admission then shuts
    during the second stage's dry round, so the criteria marker never lands.
    The run halts at that stage by name, with the first stage — and only the
    first — recorded as completed, so nothing downstream of the owner is
    reached while a member still owes the criteria marker.
    """
    owner, board, executor = factory(under_approval=True, body=PREPARED_BODY)
    original = executor.stream
    withdrawn_at = []

    async def withdrawn(**kwargs):
        # The ticket marker on the board is what dates a verify prompt to the
        # second stage: withdrawing on the first one would halt at the first.
        if (
            "Adversarially verify the current issue" in kwargs["prompt"]
            and "body complete" in board.server.issues[CLAIMED_ISSUE].labels
        ):
            labels = board.server.issues[CLAIMED_ISSUE].labels
            if "approved scope" in labels:
                labels.remove("approved scope")
                withdrawn_at.append(len(executor.calls))
        async for event in original(**kwargs):
            yield event

    monkeypatch.setattr(executor, "stream", withdrawn)
    report = await run_owner(owner)

    assert withdrawn_at, "the run never reached the criteria stage's dry round"
    assert report.halt is not None
    assert report.halt.cause is StageHaltCause.STAGE_INCOMPLETE
    assert report.halt.phase is MandateKind.CRITERIA
    assert report.halt.unlabelled_issue_ids == (CLAIMED_ISSUE,)
    assert report.completed_phases == (MandateKind.TICKET,)
    labels = set(board.server.issues[CLAIMED_ISSUE].labels)
    assert "body complete" in labels
    assert "criteria complete" not in labels


def _refused_after_the_ticket_marker(board, executor, monkeypatch, refuses):
    """Refuse each admission judgment *refuses* picks once the ticket marker lands.

    The ticket marker on the board is what dates a session to the criteria
    stage, so the first stage runs through untouched. Returns the session
    counts at which a refusal was answered.
    """
    original = executor.stream
    refused_at = []

    async def refused(**kwargs):
        marked = "body complete" in board.server.issues[CLAIMED_ISSUE].labels
        executor.refuse_forever = (
            marked
            and kwargs["output_format"]["schema"].get("title") == "AdmissionJudgment"
            and refuses(kwargs["prompt"])
        )
        if executor.refuse_forever:
            refused_at.append(len(executor.calls))
        async for event in original(**kwargs):
            yield event

    monkeypatch.setattr(executor, "stream", refused)
    return refused_at


def _the_dry_round_verification():
    """Pick the criteria stage's dry-round verification of the parent.

    The stage verifies the parent twice: once inside its admission round,
    after the criterion child is written, and once more in the dry round
    that would settle the stage. Only the second is refused, so the
    admission round settles and the one convergence round does not.
    """
    seen = []

    def refuses(prompt):
        key = re.findall(r"<issue_key>(.*?)</issue_key>", prompt)[-1]
        if "Adversarially verify the current issue" in prompt and key == CLAIMED_ISSUE:
            seen.append(key)
            return len(seen) == 2
        return False

    return refuses


#: Every report arm of the convergence loop, each reached in the second run
#: stage: the factory arguments, a maker of what the criteria stage's
#: admission judgments refuse (None: nothing is refused), and the halt the
#: arm reports.
LATER_STAGE_ARMS = {
    "admission-exhausted": (
        {},
        lambda: lambda _prompt: True,
        StageHaltCause.ADMISSION_EXHAUSTED,
    ),
    "escalate": (
        {"refusal": {"refusal_kind": "human_decision"}},
        lambda: lambda _prompt: True,
        StageHaltCause.HUMAN_DECISION,
    ),
    "convergence-exhausted": (
        {"convergence_bound": 1},
        _the_dry_round_verification,
        StageHaltCause.CONVERGENCE_EXHAUSTED,
    ),
    "stage-incomplete": ({}, None, StageHaltCause.STAGE_INCOMPLETE),
}


@pytest.mark.parametrize("arm", list(LATER_STAGE_ARMS))
async def test_a_halt_in_the_second_run_stage_reports_the_stage_before_it(
    monkeypatch, arm
):
    """A halt raised inside the second stage's rounds carries the first stage.

    The ticket stage runs through and lands its marker; then the criteria
    stage halts on one arm of its own convergence loop — an admission that
    exhausts its one round, a refusal that asks a person, a dry round that
    never settles within the one convergence round, or a member that arrives
    at the stage without the ticket marker. The halt is the loop's own, not
    the barrier's, and the report every arm returns still names the ticket
    stage as completed.
    """
    arguments, refuses, cause = LATER_STAGE_ARMS[arm]
    owner, board, executor = factory(
        under_approval=True, body=PREPARED_BODY, bound=1, **arguments
    )
    if refuses is None:
        from tests.fakes import FakeMcpIssue

        # A member that joins the scope between the two stages: the ticket
        # stage and its barrier never saw it, and the criteria stage finds it
        # without the ticket marker on its first reading.
        converge = owner._converge

        async def joined(**kwargs):
            if kwargs["phase"].spec.kind is MandateKind.CRITERIA:
                board.server.issues["late"] = FakeMcpIssue(
                    id="late", parent_id=CLAIMED_ISSUE, description=PREPARED_BODY
                )
            return await converge(**kwargs)

        monkeypatch.setattr(owner, "_converge", joined)
        refused_at = None
    else:
        refused_at = _refused_after_the_ticket_marker(
            board, executor, monkeypatch, refuses()
        )
    report = await run_owner(owner)

    assert refused_at is None or refused_at, (
        "the run never reached the criteria stage's admission"
    )
    assert report.halt is not None
    assert report.halt.cause is cause
    if cause is StageHaltCause.STAGE_INCOMPLETE:
        assert report.halt.phase is MandateKind.CRITERIA
        assert report.halt.unlabelled_issue_ids == ("late",)
    elif cause is StageHaltCause.HUMAN_DECISION:
        assert report.halt.bound is None
    else:
        assert (
            report.halt.bound.loop
            == {
                StageHaltCause.ADMISSION_EXHAUSTED: "admission",
                StageHaltCause.CONVERGENCE_EXHAUSTED: "convergence",
            }[cause]
        )
    assert report.completed_phases == (MandateKind.TICKET,)
    labels = set(board.server.issues[CLAIMED_ISSUE].labels)
    assert "body complete" in labels
    assert "criteria complete" not in labels


async def test_the_criteria_stage_opens_no_session_without_the_ticket_label():
    """The second run stage is gated on the first's marker, per member.

    Built over that row alone, with a member the first stage never marked:
    the stage counts and names it before anything opens, and writes nothing.
    """
    owner, board, executor = factory(
        under_approval=True,
        body=PREPARED_BODY,
        phases=lambda rows: tuple(
            row for row in rows if row.spec.kind is MandateKind.CRITERIA
        ),
    )
    report = await run_owner(owner)

    assert report.halt is not None
    assert report.halt.cause is StageHaltCause.STAGE_INCOMPLETE
    assert report.halt.phase.value == "criteria"
    assert report.halt.unlabelled_issue_ids == (CLAIMED_ISSUE,)
    assert executor.calls == []
    assert not [(name, args) for name, args in board.calls if name.startswith("save_")]
    # Free as well as named: the stage refuses on its first reading of the
    # roster. A check that let the stage through here would reach the
    # post-stage barrier and read the whole roster a second time to say the
    # same thing.
    assert [name for name, _ in board.calls].count("list_issues") == 1


#: The two members every entry below is read over. The criterion children a
#: stage creates are verified in every dry round and carry no stage marker;
#: the question here is which lane the round works, so the ledger is kept to
#: the lanes.
LANES = (CLAIMED_ISSUE, "second")


class GapSpy:
    """Every ``organize_gap`` call the owner makes, with the round it served.

    The number of sessions already spent is read at each call, so the calls
    cut the run into rounds: what a round worked is the sessions between its
    gap call and the next one.  Wrapping the real function rather than
    replacing it leaves the arithmetic and the run exactly as they are.
    """

    def __init__(self, monkeypatch, executor):
        self.calls = []
        self._executor = executor
        computed = organize_owner.organize_gap

        def recorded(**kwargs):
            answer = computed(**kwargs)
            self.calls.append(
                (frozenset(item.issue_key for item in answer), len(executor.calls))
            )
            return answer

        monkeypatch.setattr(organize_owner, "organize_gap", recorded)

    def rounds(self):
        """Per gap call: the work set it answered, then the lanes it spent."""
        ledger = []
        for index, (work, spent) in enumerate(self.calls):
            until = (
                self.calls[index + 1][1]
                if index + 1 < len(self.calls)
                else len(self._executor.calls)
            )
            judged = Counter()
            authored = Counter()
            for call in self._executor.calls[spent:until]:
                keys = re.findall(r"<issue_key>(.*?)</issue_key>", call["prompt"])
                if not keys or keys[-1] not in LANES:
                    continue
                title = call["output_format"]["schema"].get("title")
                if title == "AdmissionJudgment":
                    judged[keys[-1]] += 1
                elif title == "OrganizeProposal":
                    authored[keys[-1]] += 1
            ledger.append(
                (work, dict(sorted(judged.items())), dict(sorted(authored.items())))
            )
        return tuple(ledger)


def two_lane_board(**overrides):
    """An approved scope of two members, the second prepared from the start."""
    from tests.fakes import FakeMcpIssue

    owner, board, executor = factory(under_approval=True, **overrides)
    board.server.issues["second"] = FakeMcpIssue(
        id="second", parent_id=CLAIMED_ISSUE, description=PREPARED_BODY
    )
    return owner, board, executor


async def entry_killed(monkeypatch):
    """A killed pass re-entered: one member was labelled before it died."""
    owner, board, executor = two_lane_board()
    board.server.issues["second"].labels.append("body complete")
    spy = GapSpy(monkeypatch, executor)
    return spy, board, executor, await run_owner(owner)


async def entry_refutation(monkeypatch):
    """A refutation on a converged scope: the stage label is gone again.

    A refutation reaches organize as the removal of the refuted member's
    stage label — the one tracker fact that says a stage is owed again — and
    as a finding against a landed claim. Here the second lane loses the
    criteria label and the first lane's claim is refuted once, so the round
    that follows owes work to a lane that lost its label and to a lane that
    still carries it.
    """
    owner, board, executor = two_lane_board()
    assert (await run_owner(owner)).halt is None
    board.server.issues["second"].labels.remove("criteria complete")
    landed = executor.stream
    refuted = []

    async def refuting(**kwargs):
        async for event in landed(**kwargs):
            payload = event.structured_output
            if (
                kwargs["output_format"]["schema"].get("title") == "AdmissionJudgment"
                and payload.get("issue_id") == "second"
                and not refuted
            ):
                refuted.append(payload)
                event = result(
                    structured_output={
                        **payload,
                        "findings": [
                            {
                                "issue_id": CLAIMED_ISSUE,
                                "defect_class": "source_version_unspecified",
                                "evidence": "The landed claim names no source version.",
                                "role": "instance",
                            }
                        ],
                    }
                )
            yield event

    monkeypatch.setattr(executor, "stream", refuting)
    spy = GapSpy(monkeypatch, executor)
    report = await run_owner(owner)
    assert refuted
    return spy, board, executor, report


async def entry_heartbeat(monkeypatch):
    """A heartbeat tick: another owner over the board a converged run left."""
    owner, board, executor = two_lane_board()
    assert (await run_owner(owner)).halt is None
    second, board, executor = factory(under_approval=True, board=board)
    spy = GapSpy(monkeypatch, executor)
    board.calls.clear()
    return spy, board, executor, await run_owner(second)


#: What each entry is owed, round by round: the work set the one arithmetic
#: answered, the admission sessions each lane was spent, and the author
#: sessions each lane was spent. One row per gap call.
ENTRY_LEDGERS = {
    # The ticket stage, then the criteria stage, each converging in one
    # round. The member labelled before the kill is out of the roster, so
    # the unlabelled one is the only lane the round works; the labelled one
    # is verified once, in the dry round that follows, like every member.
    "killed": (
        (frozenset(LANES), {CLAIMED_ISSUE: 3, "second": 1}, {CLAIMED_ISSUE: 1}),
        (
            frozenset(LANES),
            {CLAIMED_ISSUE: 3, "second": 3},
            {CLAIMED_ISSUE: 1, "second": 1},
        ),
    ),
    # The ticket stage completes on its labels alone. The criteria stage
    # re-opens for the lane whose label went: on its first round every
    # member's admission and criterion child are still live, so the work
    # set is empty and no lane is worked. The refutation lands in that
    # round's dry pass; the second round owes the refuted lane, which still
    # carries the label, and it alone is authored again.
    "refutation": (
        (frozenset(LANES), {}, {}),
        (frozenset(), {CLAIMED_ISSUE: 1, "second": 1}, {}),
        (frozenset(LANES), {CLAIMED_ISSUE: 3, "second": 2}, {CLAIMED_ISSUE: 1}),
    ),
    # Both stages, one round each, no lane in either roster: the gap is
    # asked once per stage and nothing is spent on the answer.
    "heartbeat": (
        (frozenset(LANES), {}, {}),
        (frozenset(LANES), {}, {}),
    ),
}


@pytest.mark.parametrize("entry", sorted(ENTRY_LEDGERS))
async def test_the_three_entries_take_their_work_sets_from_the_same_function(
    monkeypatch, entry
):
    """Every way into a stage asks one function what the stage owes.

    A killed pass re-entered, a refutation on a converged scope and a
    heartbeat tick over an unchanged one are three entries into the same two
    stages. Each asks the gap exactly once per round it enters, before the
    roster can stand it down, and works the members the answer names — never
    the roster it started from, and never a second arithmetic of its own.
    """
    spy, board, executor, report = await {
        "killed": entry_killed,
        "refutation": entry_refutation,
        "heartbeat": entry_heartbeat,
    }[entry](monkeypatch)

    assert report.halt is None
    assert [phase.value for phase in report.completed_phases] == ["ticket", "criteria"]
    assert spy.rounds() == ENTRY_LEDGERS[entry]
    for key in LANES:
        assert {"body complete", "criteria complete"} <= set(
            board.server.issues[key].labels
        )
    if entry == "heartbeat":
        # Nothing owed and nothing spent: both stages are asked, and an
        # unchanged scope answers with no work, so no session opens and the
        # board is not written.
        assert executor.calls == []
        assert not [
            (name, args) for name, args in board.calls if name.startswith("save_")
        ]


#: A criterion child of the first lane, planted so the first lane is out of the
#: work set on re-entry: it carries the stage marker, both its surfaces are
#: live, and a non-Canceled criterion child is the last clause that could put
#: it back in. Verification still reaches it, which is the whole question here.
CHECK_CHILD = "claimed-check"
#: The opening the verify dispatch carries and the assess dispatch does not.
VERIFY_OPENING = "Adversarially verify the current issue"


def ticket_only(rows):
    return tuple(row for row in rows if row.spec.kind is MandateKind.TICKET)


def sessions(executor, since):
    """The issue each session after *since* was spent on, by what it asked.

    A write-back audit reads a written artifact rather than an issue, so it
    belongs to none of the three sets.
    """
    assessed, verified, authored = set(), set(), set()
    for call in executor.calls[since:]:
        title = call["output_format"]["schema"].get("title")
        keys = re.findall(r"<issue_key>(.*?)</issue_key>", call["prompt"])
        if title == "WriteBackFinding" or not keys:
            continue
        if title == "AdmissionJudgment":
            (verified if VERIFY_OPENING in call["prompt"] else assessed).add(keys[-1])
        elif title == "OrganizeProposal":
            authored.add(keys[-1])
    return frozenset(assessed), frozenset(verified), frozenset(authored)


async def one_issue_gap_entry(monkeypatch, **overrides):
    """A converged ticket stage re-entered owing exactly one of three members.

    The stage marker is the only fact removed, and it is the only fact that
    can be removed here: the phase markers are the one part of a member the
    admitted context digest excludes, so every other perturbation would stale
    every retained admission at once and put the whole scope in the work set.
    The assessment of the member that lost its marker refuses, which is what
    spends an author session on it; verification answers on the board.

    The first lane keeps its marker, its live surfaces and a criterion child,
    so no clause of the work-set arithmetic names it. Sessions are counted
    from the re-entry, so the first run's work is not in the census.
    """
    from tests.fakes import FakeMcpIssue

    owner, board, executor = two_lane_board(phases=ticket_only, **overrides)
    board.server.issues[CHECK_CHILD] = FakeMcpIssue(
        id=CHECK_CHILD,
        parent_id=CLAIMED_ISSUE,
        labels=["check"],
        description="The check names the source version.",
    )
    assert (await run_owner(owner)).halt is None
    board.server.issues["second"].labels.remove("body complete")
    landed = executor.stream

    async def unprepared(**kwargs):
        async for event in landed(**kwargs):
            if (
                kwargs["output_format"]["schema"].get("title") == "AdmissionJudgment"
                and event.structured_output["issue_id"] == "second"
                and VERIFY_OPENING not in kwargs["prompt"]
            ):
                event = result(
                    structured_output={
                        "issue_id": "second",
                        "verdict": "not_buildable",
                        "evidence": "The member owing the stage is not prepared.",
                        "refusal_kind": "spec_gap",
                        "invented_decision": "Prepare the member the stage owes.",
                    }
                )
            yield event

    monkeypatch.setattr(executor, "stream", unprepared)
    since = len(executor.calls)
    spy = GapSpy(monkeypatch, executor)
    return owner, board, executor, spy, since


async def test_a_one_issue_gap_verifies_the_whole_scope_and_authors_the_one_issue(
    monkeypatch,
):
    """Coverage is the scope's full issue set while the work set is one member.

    The scope's full issue set is every organize subject of the snapshot plus
    every criterion child of one, and the expectation is read off the board
    rather than listed, so a fixture that grows a member grows it too.
    """
    owner, board, executor, spy, since = await one_issue_gap_entry(monkeypatch)
    report = await run_owner(owner)
    assert report.halt is None
    assert report.completed_phases == (MandateKind.TICKET,)
    assert [work for work, _ in spy.calls] == [frozenset({"second"})]
    assessed, verified, authored = sessions(executor, since)
    assert assessed == authored == frozenset({"second"})
    assert verified == frozenset({CLAIMED_ISSUE, "second", CHECK_CHILD})
    assert verified == {CLAIMED_ISSUE} | {
        key
        for key, native in board.server.issues.items()
        if native.parent_id == CLAIMED_ISSUE
    }
    assert "body complete" in board.server.issues["second"].labels


async def test_a_member_outside_the_work_set_that_fails_verification_is_reported(
    monkeypatch,
):
    """The stage reports a refusal on a member no session of its own authored."""
    owner, board, executor, spy, _since = await one_issue_gap_entry(
        monkeypatch, convergence_bound=1
    )
    original = executor.stream

    async def refusing(**kwargs):
        async for event in original(**kwargs):
            if (
                kwargs["output_format"]["schema"].get("title") == "AdmissionJudgment"
                and event.structured_output["issue_id"] == CLAIMED_ISSUE
                and VERIFY_OPENING in kwargs["prompt"]
            ):
                event = result(
                    structured_output={
                        "issue_id": CLAIMED_ISSUE,
                        "verdict": "not_buildable",
                        "evidence": "The landed body names no source version.",
                        "refusal_kind": "spec_gap",
                        "invented_decision": "State the source version.",
                    }
                )
            yield event

    monkeypatch.setattr(executor, "stream", refusing)
    report = await run_owner(owner)
    assert spy.calls[0][0] == frozenset({"second"})
    assert report.halt.cause == "convergence_exhausted"
    assert report.halt.bound.value == report.halt.bound.rounds_used == 1
    assert [r.issue_id for r in report.halt.admission_results] == [CLAIMED_ISSUE]
    assert "needs decision" in board.server.issues[CLAIMED_ISSUE].labels
    assert "body complete" not in board.server.issues["second"].labels
