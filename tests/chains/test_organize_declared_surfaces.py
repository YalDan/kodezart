"""One organize round holds the set its row declares, over the real adapter.

Every case drives the composed owner through ``factory``/``run_owner``, so the
lease records read here are the ones the production path writes.
"""

import asyncio
import re

import pytest

from kodezart.config.app import AppConfig
from kodezart.config.organize import OrganizeSettings
from kodezart.domain.errors import SurfaceLeaseError, SurfaceLeaseLostError
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from tests.chains.test_organize import result
from tests.chains.test_organize_owner import factory, run_owner
from tests.fakes import FakeMcpIssue
from tests.tracker.conftest import CLAIMED_ISSUE

JOB = "actual-organize-job"
SIBLING = "FIX-SIBLING"
GROOM_LINES = ("issue_description", "issue_graph", "issue_label_set")


def settings(*, bound=2, convergence_bound=2, lease_seconds=900.0):
    return AppConfig(
        organize=OrganizeSettings(
            max_admission_rounds=bound, max_convergence_rounds=convergence_bound
        ),
        write_back={"max_verify_rounds": 2},
        tracker={"surface_lease_seconds": lease_seconds},
    )


def member(
    board, key, *, description="Prepared body grounded in the source.", labels=()
):
    """One more issue of the scope, in the vendor's own shape."""
    board.server.issues[key] = FakeMcpIssue(
        id=key,
        description=description,
        parent_id=CLAIMED_ISSUE,
        labels=list(labels),
    )
    return board.server.issues[key]


def held(board):
    """Every standing lease marker, as (holder, nonce, frozenset of addresses)."""
    return [_read(comment.body) for comment in board.grants()]


def acquisitions(board):
    """The nonce of each lease creation the round made, in call order."""
    return [_read(args["body"]) for args in board.lease_creations()]


def _read(body):
    fields = dict(re.findall(r"^(kind|holder|nonce|state): (.*)$", body, re.M))
    return (
        fields["holder"],
        fields["nonce"],
        frozenset(re.findall(r"^- (.*)$", body, re.M)),
    )


def addresses(key, kinds=GROOM_LINES):
    return frozenset(f"{kind}|issue|{key}|" for kind in kinds)


async def test_the_round_holds_the_whole_declared_set_before_its_first_write():
    """Every declared kind, on every member of the snapshot, before any write.

    The sibling already carries the marker, so the round spends no session
    on it and still declares it: the set is the snapshot's, not the work
    roster's.
    """
    owner, board, _ = factory()
    member(board, SIBLING, labels=["graph complete"])
    board.pause = lambda name, args: name == "save_issue" and "description" in args
    task = asyncio.create_task(run_owner(owner))
    try:
        await asyncio.wait_for(board.reached.wait(), timeout=10)
        declared = addresses(CLAIMED_ISSUE) | addresses(SIBLING)
        (nonce,) = {nonce for _, nonce, _ in acquisitions(board)}
        assert held(board) == [(JOB, nonce, declared)] * 2
        assert [args["issueId"] for args in board.lease_creations()] == [
            CLAIMED_ISSUE,
            SIBLING,
        ]
    finally:
        board.resume.set()
    report = await task
    assert report.halt is None
    assert "graph complete" in board.server.issues[CLAIMED_ISSUE].labels


async def test_a_contended_declared_set_writes_nothing_and_opens_no_session():
    """One address of the set held elsewhere refuses the whole acquisition."""
    owner, board, executor = factory()
    member(board, SIBLING)
    rival = WritableSurface(
        kind=SurfaceKind.ISSUE_DESCRIPTION,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=SIBLING),
    )
    async with RunSurfaceLease(
        tracker=board.tracker(),
        job_id="rival-holder",
        surfaces=frozenset({rival}),
        lease_seconds=900.0,
    ):
        with pytest.raises(SurfaceLeaseError) as refusal:
            await run_owner(owner)
        assert (
            refusal.value.surface_kind,
            refusal.value.scope_key,
            refusal.value.current_holder,
        ) == (rival.kind.value, SIBLING, "rival-holder")
        assert [holder for holder, _, _ in held(board)] == ["rival-holder"]
    assert executor.calls == []
    assert not [args for name, args in board.calls if name == "save_issue"]


async def test_a_round_whose_lease_lapsed_in_a_session_writes_nothing_more(
    monkeypatch,
):
    """A lapse inside a session is read at the next write, never re-acquired."""
    owner, board, executor = factory(settings=settings(lease_seconds=60.0))
    original = executor.stream

    async def stream(**kwargs):
        if kwargs["output_format"]["schema"].get("title") == "OrganizeProposal":
            # The board's clock crosses the whole lease while the session runs.
            board.advance(120)
        async for event in original(**kwargs):
            yield event

    monkeypatch.setattr(executor, "stream", stream)
    with pytest.raises(SurfaceLeaseLostError):
        await run_owner(owner)
    assert not [args for name, args in board.calls if name == "save_issue"]
    assert len({nonce for _, nonce, _ in acquisitions(board)}) == 1


async def test_no_halt_is_written_while_the_rounds_lease_is_held():
    """The round's set is gone before the halt's first escalation write."""
    owner, board, _ = factory(refuse_forever=True, bound=1)
    board.pause = lambda name, args: (
        name == "save_comment"
        and str(args.get("body", "")).startswith("[organize-question")
    )
    task = asyncio.create_task(run_owner(owner))
    try:
        await asyncio.wait_for(board.reached.wait(), timeout=10)
        assert not [
            record
            for record in held(board)
            if record[2] & addresses(CLAIMED_ISSUE, ("issue_description",))
        ]
    finally:
        board.resume.set()
    report = await task
    assert report.halt.cause == "admission_exhausted"
    assert "needs decision" in board.server.issues[CLAIMED_ISSUE].labels


TICKET_LINES = (
    "issue_description",
    "issue_label_set",
    "issue_split_set",
)
CHILDREN = ("first-deliverable", "second-deliverable")


def splitting(board, executor, monkeypatch):
    """An author that proposes two children once, then judges the board fresh."""
    original = executor.stream

    async def stream(**kwargs):
        key = re.findall(r"<issue_key>(.*?)</issue_key>", kwargs["prompt"])
        minted = any(
            child in issue.description
            for issue in board.server.issues.values()
            for child in CHILDREN
        )
        if key and key[-1] == CLAIMED_ISSUE and not minted:
            schema = kwargs["output_format"]["schema"]
            executor.calls.append(kwargs)
            if schema.get("title") == "OrganizeProposal":
                yield result(
                    structured_output={
                        "kind": "split",
                        "issue_id": CLAIMED_ISSUE,
                        "children": [
                            {
                                "deliverable_key": child,
                                "title": f"Prepared split {index}",
                                "body": "Prepared source-grounded child specification.",
                            }
                            for index, child in enumerate(CHILDREN)
                        ],
                    }
                )
            else:
                yield result(
                    structured_output={
                        "issue_id": CLAIMED_ISSUE,
                        "verdict": "not_buildable",
                        "evidence": "The mandate calls for independent children.",
                        "refusal_kind": "spec_gap",
                        "invented_decision": "Prepare the specified children.",
                    }
                )
            return
        async for event in original(**kwargs):
            yield event

    monkeypatch.setattr(executor, "stream", stream)


def opened(board):
    """Where in the call order each round's acquisition was first written."""
    return {
        _read(str(args.get("body", "")))[1]: index
        for index, (name, args) in reversed(list(enumerate(board.calls)))
        if name == "save_comment"
        and "id" not in args
        and "kind: lease\n" in str(args.get("body", ""))
    }


def minted_keys(board):
    return sorted(
        issue.id
        for issue in board.server.issues.values()
        if issue.parent_id == CLAIMED_ISSUE
    )


async def test_the_declared_set_is_acquired_once_a_round_not_once_a_write(monkeypatch):
    """Two writes of one round sit under one acquisition, and each round takes one."""
    owner, board, executor = factory(
        under_approval=True,
        convergence_bound=3,
        bound=3,
        phases=lambda rows: rows[:1],
    )
    splitting(board, executor, monkeypatch)
    report = await run_owner(owner)
    assert report.halt is None
    rounds = [nonce for _, nonce, _ in acquisitions(board)]
    first, second = dict.fromkeys(rounds)
    assert len(dict.fromkeys(rounds)) == 2
    created = [
        index
        for index, (name, args) in enumerate(board.calls)
        if name == "save_issue" and "parentId" in args
    ]
    assert len(created) == 2
    assert all(
        opened(board)[first] < index < opened(board)[second] for index in created
    )


async def test_a_member_the_round_mints_is_declared_by_the_next_round(monkeypatch):
    """A split child is outside the round that minted it and inside the next."""
    owner, board, executor = factory(
        under_approval=True,
        convergence_bound=3,
        bound=3,
        phases=lambda rows: rows[:1],
    )
    splitting(board, executor, monkeypatch)
    report = await run_owner(owner)
    assert report.halt is None
    children = [key for key in minted_keys(board) if key != CLAIMED_ISSUE]
    assert len(children) == 2
    first, second = dict.fromkeys(nonce for _, nonce, _ in acquisitions(board))
    declared = {nonce: lines for _, nonce, lines in acquisitions(board) if True}
    assert not declared[first] & frozenset(
        line for child in children for line in addresses(child, TICKET_LINES)
    )
    assert all(addresses(child, TICKET_LINES) <= declared[second] for child in children)
    for child in children:
        landed = min(
            index
            for index, (name, args) in enumerate(board.calls)
            if name == "save_issue" and args.get("id") == child
        )
        assert opened(board)[second] < landed
        assert "body complete" in board.server.issues[child].labels


async def test_approval_landing_before_the_groom_marker_refuses_the_marker(monkeypatch):
    """Approval that lands between two markers refuses the second one.

    The scope is approved the moment the first member's marker lands, so the
    pre-approval row's next write reads approval and is refused: the second
    member keeps no marker, and the round's declared set is released with the
    refusal.
    """
    from kodezart.domain.errors import OrganizeWriteRefusalError

    owner, board, _ = factory()
    member(board, SIBLING)
    original = board.call_tool

    async def approving(*, name, arguments):
        response = await original(name=name, arguments=arguments)
        if name == "save_issue" and "graph complete" in arguments.get("addLabels", []):
            board.server.issues[CLAIMED_ISSUE].labels.append("approved scope")
        return response

    monkeypatch.setattr(board, "call_tool", approving)
    with pytest.raises(OrganizeWriteRefusalError, match="groom is not admitted"):
        await run_owner(owner)
    marked = [
        key
        for key in (CLAIMED_ISSUE, SIBLING)
        if "graph complete" in board.server.issues[key].labels
    ]
    assert len(marked) == 1
    assert board.grants() == []


LATE = "FIX-LATE"
FOREIGN = "FIX-2"


def edging(board, executor, monkeypatch, peer, *, join=None):
    """An author that adds one dependency edge, and a judge that waits for it.

    *join* is called during each admission session, which is where a case
    about a member the round never declared has the scope gain one.
    """
    original = executor.stream

    def landed():
        return ("blockedBy", peer) in board.server.issues[CLAIMED_ISSUE].relations

    async def stream(**kwargs):
        title = kwargs["output_format"]["schema"].get("title")
        keys = re.findall(r"<issue_key>(.*?)</issue_key>", kwargs["prompt"])
        if title != "WriteBackFinding" and keys and keys[-1] == CLAIMED_ISSUE:
            if not landed():
                executor.calls.append(kwargs)
                if title == "OrganizeProposal":
                    yield result(
                        structured_output={
                            "kind": "graph",
                            "issue_id": CLAIMED_ISSUE,
                            "changes": [{"kind": "blocked_by", "add": [peer]}],
                        }
                    )
                else:
                    if join is not None:
                        join()
                    yield result(
                        structured_output={
                            "issue_id": CLAIMED_ISSUE,
                            "verdict": "not_buildable",
                            "evidence": "The recorded dependency is absent.",
                            "refusal_kind": "spec_gap",
                            "invented_decision": "Record the dependency.",
                        }
                    )
                return
        async for event in original(**kwargs):
            yield event

    monkeypatch.setattr(executor, "stream", stream)


def renewals(board, line="issue_graph|issue|"):
    """Every renewal of the round's own lease: an edit naming its own address."""
    return [
        args
        for name, args in board.calls
        if name == "save_comment"
        and "since:" in str(args.get("body", ""))
        and line in str(args.get("body", ""))
    ]


def edge_writes(board):
    return [
        args
        for name, args in board.calls
        if name == "save_issue" and "blockedBy" in args
    ]


async def test_a_graph_write_inside_the_declared_set_lands(monkeypatch):
    """Both addresses the edge needs are the round's, so the write lands."""
    owner, board, executor = factory(convergence_bound=3, bound=3)
    member(board, SIBLING)
    edging(board, executor, monkeypatch, SIBLING)
    report = await run_owner(owner)
    assert report.halt is None
    assert ("blockedBy", SIBLING) in board.server.issues[CLAIMED_ISSUE].relations
    assert ("blocks", CLAIMED_ISSUE) in board.server.issues[SIBLING].relations
    assert "graph complete" in board.server.issues[CLAIMED_ISSUE].labels


async def test_a_graph_write_naming_a_member_that_joined_late_is_a_finding_on_it(
    monkeypatch,
):
    """A member inside the scope and outside the held set is a residual.

    The scope gains the member during the admission session, so the round
    declares every address but that one. The write is refused before any
    renewal or backend call, and the finding is written to the member that
    owns the address rather than to the subject the write was authored for.
    """
    owner, board, executor = factory(convergence_bound=1, bound=2)

    def join():
        if LATE not in board.server.issues:
            member(board, LATE)

    edging(board, executor, monkeypatch, LATE, join=join)
    report = await run_owner(owner)
    assert report.halt.cause == "convergence_exhausted"
    assert [
        (finding.issue_id, finding.defect_class)
        for finding in report.halt.surviving_findings
    ] == [(LATE, "undeclared_surface")]
    assert "issue_graph" in report.halt.surviving_findings[0].evidence
    assert edge_writes(board) == []
    assert renewals(board) == []
    assert "needs decision" in board.server.issues[LATE].labels
    assert [
        comment.body
        for comment in board.server.comments
        if comment.issue_id == LATE and "undeclared_surface" in comment.body
    ]


async def test_a_graph_write_naming_an_issue_outside_the_scope_stays_a_refusal(
    monkeypatch,
):
    """A peer outside the scope is refused outright, with no finding at all."""
    from kodezart.domain.errors import OrganizeWriteRefusalError

    owner, board, executor = factory(convergence_bound=2, bound=2)
    edging(board, executor, monkeypatch, FOREIGN)
    with pytest.raises(OrganizeWriteRefusalError, match="outside the current admitted"):
        await run_owner(owner)
    assert edge_writes(board) == []
    assert "needs decision" not in board.server.issues[CLAIMED_ISSUE].labels
    assert board.grants() == []


async def test_the_refused_write_lands_once_the_next_round_declares_the_member(
    monkeypatch,
):
    """The next round snapshots the board again, so the repeated write lands."""
    owner, board, executor = factory(convergence_bound=3, bound=2)

    def join():
        if LATE not in board.server.issues:
            member(board, LATE)

    edging(board, executor, monkeypatch, LATE, join=join)
    report = await run_owner(owner)
    assert report.halt is None
    assert ("blockedBy", LATE) in board.server.issues[CLAIMED_ISSUE].relations
    assert "needs decision" not in board.server.issues[LATE].labels
    assert "graph complete" in board.server.issues[LATE].labels


async def test_an_answer_that_writes_nothing_is_not_weighed_against_the_set(
    monkeypatch,
):
    """A decision the author asks for keeps its halt on a row without the body.

    The criteria stage declares no description, and an unresolved answer
    needs no address at all, so it is the human decision it always was
    rather than a residual on the subject.
    """
    owner, board, executor = factory(under_approval=True)
    original = executor.stream

    async def unresolved(**kwargs):
        # The first stage's marker is what admits the criteria stage, so an
        # author session after it is that stage's.
        staged = "body complete" in board.server.issues[CLAIMED_ISSUE].labels
        async for event in original(**kwargs):
            if (
                staged
                and kwargs["output_format"]["schema"].get("title") == "OrganizeProposal"
            ):
                event = result(
                    structured_output={
                        "kind": "unresolved",
                        "issue_id": CLAIMED_ISSUE,
                        "question": "Which declared source names the check?",
                        "evidence": "Two sources name different checks.",
                    }
                )
            yield event

    monkeypatch.setattr(executor, "stream", unresolved)
    report = await run_owner(owner)
    assert [phase.value for phase in report.completed_phases] == ["ticket"]
    assert report.halt.cause == "human_decision"
    assert report.halt.questions[0].issue_id == CLAIMED_ISSUE
    assert "undeclared_surface" not in {
        finding.defect_class for finding in report.halt.surviving_findings
    }


STUCK = "FIX-STUCK"
GROOM_MARKER = "graph complete"
INTERIM = "Preparation stops; no completion marker or execution is authorized."


def spec_finding(owner, defect_class="missing_source", evidence=None):
    return {
        "issue_id": owner,
        "defect_class": defect_class,
        "evidence": evidence or f"{owner} cites no source for its deliverable.",
        "role": "instance",
    }


def judging(board, executor, monkeypatch, answers):
    """Judge sessions answered per subject from *answers*.

    ``answers[key](n)`` is the payload of the n-th judgement of *key*
    (counted from one), or None for the configured executor's own answer.
    Author and verifier sessions keep the configured answers.
    """
    original = executor.stream
    seen = {}

    async def stream(**kwargs):
        title = kwargs["output_format"]["schema"].get("title")
        keys = re.findall(r"<issue_key>(.*?)</issue_key>", kwargs["prompt"])
        if (
            title not in {"OrganizeProposal", "WriteBackFinding"}
            and keys
            and keys[-1] in answers
        ):
            seen[keys[-1]] = seen.get(keys[-1], 0) + 1
            payload = answers[keys[-1]](seen[keys[-1]])
            if payload is not None:
                executor.calls.append(kwargs)
                yield result(structured_output=payload)
                return
        async for event in original(**kwargs):
            yield event

    monkeypatch.setattr(executor, "stream", stream)
    return seen


def buildable(key, *findings):
    return {
        "issue_id": key,
        "verdict": "buildable",
        "evidence": f"{key} is buildable from its source.",
        "findings": list(findings),
    }


def refusal(key, kind, *findings):
    return {
        "issue_id": key,
        "verdict": "not_buildable",
        "evidence": f"{key} leaves a choice open.",
        "refusal_kind": kind,
        "invented_decision": f"Settle the open choice on {key}.",
        "findings": list(findings),
    }


def escalations(board, key, question=None):
    """The escalation records on *key*, read back as the tracker holds them."""
    from kodezart.types.domain.run_state import LaneEscalation

    records = [
        LaneEscalation.model_validate_json(comment.body.partition("\n")[2])
        for comment in board.server.comments
        if comment.issue_id == key and comment.body.startswith("[organize-question:")
    ]
    return [
        record for record in records if question is None or record.question == question
    ]


async def test_every_open_finding_is_written_to_its_own_item_before_the_halt_returns(
    monkeypatch,
):
    """Each finding lands on its own item, with its evidence and the interim reading.

    The two members carry the pass's marker, so only the dry round judges
    them; the findings it forms name them rather than the subject.
    """
    from tests.chains.test_organize_owner import stage_report_step

    owner, board, executor = factory(convergence_bound=1, bound=2)
    first, second = "FIX-A", "FIX-B"
    for key in (first, second):
        member(board, key, labels=[GROOM_MARKER])
    judging(
        board,
        executor,
        monkeypatch,
        {
            key: (lambda _, key=key: buildable(key, spec_finding(key)))
            for key in (first, second)
        },
    )
    stage_report_step(monkeypatch, board)
    report = await run_owner(owner)
    assert report.halt.cause == "convergence_exhausted"
    for key in (first, second):
        (record,) = escalations(board, key, "missing_source")
        assert record.issue_id == key
        assert record.interim_basis == spec_finding(key)["evidence"]
        assert record.interim_reading == INTERIM
        assert "needs decision" in board.server.issues[key].labels
    assert not escalations(board, CLAIMED_ISSUE)
    written = [
        index
        for index, (name, args) in enumerate(board.calls)
        if name == "save_comment"
        and str(args.get("body", "")).startswith("[organize-question:")
    ]
    reported = [
        index for index, (name, _) in enumerate(board.calls) if name == "stage_report"
    ]
    assert written
    assert reported
    assert max(written) < min(reported)


@pytest.mark.parametrize("cause", ["human_decision", "admission_exhausted", "residual"])
async def test_a_halt_inside_a_round_carries_the_findings_left_open(monkeypatch, cause):
    """A halt inside a round writes what earlier rounds and this one left open."""
    owner, board, executor = factory(convergence_bound=2, bound=1)
    if cause == "residual":
        # One subject's write needs a member that joined mid-round, then the
        # next subject exhausts its admission rounds in the same round.
        def join():
            if LATE not in board.server.issues:
                member(board, LATE)

        edging(board, executor, monkeypatch, LATE, join=join)
        member(board, STUCK)
        original = executor.stream

        async def stuck(**kwargs):
            title = kwargs["output_format"]["schema"].get("title")
            keys = re.findall(r"<issue_key>(.*?)</issue_key>", kwargs["prompt"])
            if title == "AdmissionJudgment" and keys and keys[-1] == STUCK:
                yield result(structured_output=refusal(STUCK, "spec_gap"))
                return
            async for event in original(**kwargs):
                yield event

        monkeypatch.setattr(executor, "stream", stuck)
        report = await run_owner(owner)
        assert report.halt.cause == "admission_exhausted"
        assert [
            (finding.issue_id, finding.defect_class)
            for finding in report.halt.surviving_findings
        ] == [(LATE, "undeclared_surface")]
        assert escalations(board, LATE, "undeclared_surface")
        return
    # Round one: the dry round forms a finding on the marked sibling, and
    # the working member is clean at admission but refused by the dry round.
    # Round two works both again: the sibling is clean now, and the working
    # member halts while the sibling's finding is still the round's open one.
    member(board, SIBLING, labels=[GROOM_MARKER])
    member(board, STUCK)
    kind = "human_decision" if cause == "human_decision" else "spec_gap"
    judging(
        board,
        executor,
        monkeypatch,
        {
            SIBLING: lambda n: (
                buildable(SIBLING, spec_finding(SIBLING))
                if n == 1
                else buildable(SIBLING)
            ),
            STUCK: lambda n: buildable(STUCK) if n == 1 else refusal(STUCK, kind),
        },
    )
    report = await run_owner(owner)
    assert report.halt.cause == cause
    assert (SIBLING, "missing_source") in {
        (finding.issue_id, finding.defect_class)
        for finding in report.halt.surviving_findings
    }
    (record,) = escalations(board, SIBLING, "missing_source")
    assert record.interim_basis == spec_finding(SIBLING)["evidence"]


async def test_an_admission_results_own_findings_are_written_at_its_halt(monkeypatch):
    """The refusal that halts carries findings on another item; they land there."""
    owner, board, executor = factory(convergence_bound=2, bound=1)
    member(board, SIBLING, labels=[GROOM_MARKER])
    member(board, STUCK)
    judging(
        board,
        executor,
        monkeypatch,
        {STUCK: lambda _: refusal(STUCK, "human_decision", spec_finding(SIBLING))},
    )
    report = await run_owner(owner)
    assert report.halt.cause == "human_decision"
    (record,) = escalations(board, SIBLING, "missing_source")
    assert record.interim_basis == spec_finding(SIBLING)["evidence"]
    assert escalations(board, STUCK)


@pytest.mark.parametrize("repair", ["repaired", "recurs"])
async def test_a_finding_the_next_round_repairs_never_reaches_the_board(
    monkeypatch, repair
):
    """Findings are held while the phase converges; only the open ones are written."""
    owner, board, executor = factory(convergence_bound=2, bound=2)
    member(board, SIBLING, labels=[GROOM_MARKER])
    judging(
        board,
        executor,
        monkeypatch,
        {
            SIBLING: lambda n: (
                buildable(SIBLING, spec_finding(SIBLING))
                if n == 1 or repair == "recurs"
                else buildable(SIBLING)
            )
        },
    )
    report = await run_owner(owner)
    if repair == "repaired":
        assert report.halt is None
        assert not escalations(board, SIBLING)
        assert "needs decision" not in board.server.issues[SIBLING].labels
        return
    # The sibling the finding names is a subject of the next round, and a
    # finding that keeps recurring exhausts its admission rounds there.
    assert report.halt.cause == "admission_exhausted"
    assert escalations(board, SIBLING, "missing_source")


async def test_two_findings_of_different_classes_on_one_item_are_two_records(
    monkeypatch,
):
    """One record per item and question: two classes are two, one class twice is one."""
    owner, board, executor = factory(convergence_bound=1, bound=2)
    member(board, SIBLING, labels=[GROOM_MARKER])
    judging(
        board,
        executor,
        monkeypatch,
        {
            SIBLING: lambda _: buildable(
                SIBLING,
                spec_finding(SIBLING, "missing_source", "The first source is absent."),
                spec_finding(SIBLING, "missing_source", "The second source is absent."),
                spec_finding(SIBLING, "ambiguous_scope", "Two deliverables share it."),
            )
        },
    )
    report = await run_owner(owner)
    assert report.halt.cause == "convergence_exhausted"
    assert sorted(
        record.question
        for record in escalations(board, SIBLING)
        if record.question in {"missing_source", "ambiguous_scope"}
    ) == ["ambiguous_scope", "missing_source"]
