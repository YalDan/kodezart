"""What a composed scope run does before its first tick.

Over the same composed engine the rest of the scope suite drives, so the
entry these cases exercise is the one a request reaches and not a second
wiring written here.
"""

import asyncio
import json
import re

import pytest

from kodezart.composition import organize as organize_composition
from kodezart.composition.jobs import build_job_queue
from kodezart.config.app import AppConfig
from kodezart.config.job_queue import JobQueueSettings
from kodezart.config.organize import OrganizeSettings
from kodezart.config.write_back import WriteBackSettings
from kodezart.domain.errors import OrganizeHaltError, ScopeNotApprovedError
from kodezart.types.domain.agent import ErrorEvent, SystemEvent
from kodezart.types.domain.dispatch import ExclusionClause, PassRun
from kodezart.types.domain.job import JobState
from kodezart.types.domain.operation import OperationConfig, ScopeLabel
from kodezart.types.domain.organize import MandateKind
from kodezart.types.domain.organize_owner import (
    OrganizeReport,
    StageHaltCause,
    StageHaltReport,
)
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.scope_heartbeat import HeartbeatOutcome
from kodezart.types.domain.scope_runtime import ScopeWalkEvent
from tests.chains.test_native_fire import (
    TRUNK_BRANCHES,
    WORK_SHA,
    native_evaluation,
    native_operation,
)
from tests.chains.test_organize import result as organize_result
from tests.fakes import FIXTURE_EPOCH, FakeGitService, handed_over
from tests.integration.test_scope_runtime import (
    ORIGIN,
    SCOPE,
    STAGED,
    ObservedNativeExecutor,
    board,
    drive,
    runtime,
)

MILESTONE = ScopeRef(kind=ScopeKind.MILESTONE, key="first-milestone")

#: The ticket stage's completion marker. The criteria stage's is ``STAGED``,
#: the label every lane's own fire read already refuses without.
TICKET_MARKER = "body complete"
#: The pre-approval row's marker, which no run stage of this table writes.
GROOM_MARKER = "graph complete"
#: The body every member's admission is granted on.
PREPARED = "Prepared body grounded in the source."


def organize_operation():
    """``native_operation()`` plus the three-row table the stages need.

    The first run-stage row is gated on approval by that exact reference and
    completes with its own marker; the second is gated on the first's marker
    and completes with the one the fire read requires.
    """
    fields = native_operation().model_dump()
    # The consumer double writes a classification under the name it is asked
    # for, so each marker's mapping key and its tracker label are one name.
    fields["issue_labels"] = {
        "decision": "decision",
        "criterion": "criterion",
        "candidate": "candidate issue",
        GROOM_MARKER: GROOM_MARKER,
        TICKET_MARKER: TICKET_MARKER,
        STAGED: STAGED,
    }
    fields["scope_labels"] = {
        "triage": "candidate scope",
        "proposed": "proposed scope",
        "approved": "approved scope",
    }
    fields["repos"] = [{"url": ORIGIN, "trunk": "trunk"}]
    fields["organize_mandates"] = [
        {
            "kind": "groom",
            "gate_label_key": "scope_labels.triage",
            "terminal_marker_key": f"issue_labels.{GROOM_MARKER}",
        },
        {
            "kind": "ticket",
            "gate_label_key": "scope_labels.approved",
            "terminal_marker_key": f"issue_labels.{TICKET_MARKER}",
        },
        {
            "kind": "criteria",
            "gate_label_key": f"issue_labels.{TICKET_MARKER}",
            "terminal_marker_key": f"issue_labels.{STAGED}",
        },
    ]
    for mandate in fields["organize_mandates"]:
        mandate["rubric_prompt_key"] = "organize_assess"
        mandate["admission_prompt_key"] = "organize_assess"
    return OperationConfig.model_validate(fields)


class OrganizingExecutor(ObservedNativeExecutor):
    """Answers the organize schemas; every other schema is the native double's.

    The criteria proposal quotes the Checks the board's children already
    carry, so the stage's creation step finds nothing absent and the run
    reaches its walk without authoring a criterion.
    """

    def __init__(self, evaluations, *, port):
        super().__init__(evaluations)
        self.port = port
        self.organize_calls = []
        #: One row per admission session: the lane it named, how long the
        #: board's ordered classification journal was at the time, and the
        #: stage markers that lane carried then. The length is what makes
        #: "this marker write follows that session" a comparison rather than
        #: a story, and the markers are what says which stage the session
        #: belonged to — the same lane is assessed once per stage.
        self.admissions = []

    def _checks(self, issue_key):
        return [
            re.search(r"\*\*Check:\*\*\s*(.*)", issue.body)[1].strip()
            for issue in self.port.issues.values()
            if issue.parent_key == issue_key and "criterion" in issue.issue_labels
        ]

    async def stream(self, **kwargs):
        title = kwargs["output_format"]["schema"].get("title")
        if title not in {"AdmissionJudgment", "OrganizeProposal", "WriteBackFinding"}:
            async for event in super().stream(**kwargs):
                yield event
            return
        prompt = kwargs["prompt"]
        self.organize_calls.append(kwargs)
        if title == "WriteBackFinding":
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
            if title == "AdmissionJudgment":
                body = self.port.issues[key].body
                self.admissions.append(
                    (
                        key,
                        len(self.port.classification_writes),
                        self.port.issues[key].issue_labels
                        & frozenset({TICKET_MARKER, STAGED}),
                    )
                )
                payload = {
                    "issue_id": key,
                    "verdict": "buildable",
                    "evidence": f"Fresh native body checked: {body}",
                }
            elif "Author criterion sub-issue proposals" in prompt:
                payload = {
                    "kind": "criteria",
                    "issue_id": key,
                    "criteria": [
                        {
                            "title": check,
                            "check": check,
                            "do": f"Compare the source and {check}.",
                        }
                        for check in self._checks(key)
                    ],
                }
            else:
                payload = {"kind": "body", "issue_id": key, "body": PREPARED}
        yield SystemEvent(subtype="init", data={"session_id": "organize-session"})
        yield organize_result(structured_output=payload)


def under_milestone(port):
    """Address the run at a milestone whose owning project is the board's."""
    port.scope_containers[MILESTONE] = ScopeContainer(
        ref=MILESTONE,
        name="first milestone",
        description="",
        url=None,
        parent=SCOPE,
    )
    port.scope_memberships[MILESTONE] = port.scope_memberships[SCOPE]
    return port


async def test_a_milestone_addressed_run_walks_under_its_projects_approval():
    """A milestone carries no label of its own; its project's admits the run."""
    port = under_milestone(board(lanes=("A",)))
    harness = runtime(port=port)
    events = [event async for event in drive(harness, scope=MILESTONE)]
    walks = [event for event in events if isinstance(event, ScopeWalkEvent)]
    assert walks[-1].observation.dispatched == ("A",)

    port.scope_label_members[SCOPE] = frozenset()
    with pytest.raises(ScopeNotApprovedError) as caught:
        _ = [event async for event in drive(harness, scope=MILESTONE, job="second")]
    assert caught.value.ref == MILESTONE


async def test_the_addressed_scopes_own_approval_admits_the_run():
    """The question is asked of the address, not of any one member."""
    port = board(lanes=("A",))
    port.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key="A")] = frozenset(
        {ScopeLabel.APPROVED}
    )
    port.scope_label_members[SCOPE] = frozenset()
    harness = runtime(port=port)
    with pytest.raises(ScopeNotApprovedError):
        _ = [event async for event in drive(harness)]


async def test_the_stages_precede_the_first_ready_read_and_a_halt_prevents_it():
    """Whatever the entry does, it is finished before a member is read.

    The engine's own collaborator is wrapped here rather than replaced, so
    the order asserted is the composed engine's and not a second wiring's.
    A halt raised by the entry reaches the caller and the walk never starts.
    """
    port = board(lanes=("A",))
    harness = runtime(port=port)
    entry = harness.engine._scoped_arm._entry
    order = []

    async def recorded_admit(**kwargs):
        order.append("admit")
        return await entry.admit(**kwargs)

    async def recorded_scope_issues(*, ref):
        order.append("member read")
        return await original_scope_issues(ref=ref)

    original_scope_issues = port.scope_issues
    harness.engine._scoped_arm._entry = type(
        "RecordingEntry", (), {"admit": staticmethod(recorded_admit)}
    )()
    port.scope_issues = recorded_scope_issues
    events = [event async for event in drive(harness)]
    assert [event for event in events if isinstance(event, ScopeWalkEvent)]
    assert order[0] == "admit"
    assert order.count("admit") == 1

    halting = board(lanes=("A",))
    second = runtime(port=halting)
    report = OrganizeReport(
        halt=StageHaltReport.model_validate(
            {
                "cause": "human_decision",
                "questions": [
                    {
                        "kind": "unresolved",
                        "issueId": "A",
                        "question": "Which reading of the subject governs?",
                        "evidence": "The body admits two readings.",
                    }
                ],
            }
        )
    )

    async def halting_admit(*, scope, repository, job_id):
        raise OrganizeHaltError(scope=scope, report=report)

    second.engine._scoped_arm._entry = type(
        "HaltingEntry", (), {"admit": staticmethod(halting_admit)}
    )()
    with pytest.raises(OrganizeHaltError) as caught:
        _ = [event async for event in drive(second, job="halted")]
    assert caught.value.report.halt.cause is StageHaltCause.HUMAN_DECISION
    assert second.executor.schema_calls == []


def staging_runtime(port, lanes, *, monkeypatch, builds, operation=None, executor=None):
    """The composed engine over the organize table, on a fresh organizer.

    Each admission builds its own organizer, which is what keeps the
    steady-state path and a restarted process's path one path; the wrapper
    records the objects so a cache would be visible as a reused one.

    *operation* defaults to the three-row table; a case about the pass that
    submits a run declares its standing scope on top of it. *executor*
    defaults to the organize-answering double; a case about an escalation
    supplies one that refuses a named lane's stage.
    """
    original = organize_composition.build_scope_organizer

    def recorded(**kwargs):
        organizer = original(**kwargs)
        builds.append(organizer)
        return organizer

    monkeypatch.setattr(organize_composition, "build_scope_organizer", recorded)
    return runtime(
        port=port,
        lanes=lanes,
        operation=organize_operation() if operation is None else operation,
        # The stages' write-back reads the tree it wrote in at the commit it
        # was cut at, so the trunk this fixture reports and the head its
        # workspaces stand at are one repository's answer.
        git=FakeGitService(remote_branch_shas=dict.fromkeys(TRUNK_BRANCHES, WORK_SHA)),
        organize=OrganizeSettings(max_admission_rounds=2, max_convergence_rounds=2),
        executor=OrganizingExecutor(
            [
                native_evaluation(checks={f"{key}/check": f"{key} live Check  bytes"})
                for key in lanes
                for _ in range(2)
            ],
            port=port,
        )
        if executor is None
        else executor,
    )


def marker_writes(port, marker, *, on=None):
    """The indices in the fake's write journal at which *marker* was written.

    *on* narrows the answer to one lane, which is what a per-lane order
    claim needs: the journal is ordered across every member, so a claim
    about one member's marker has to select that member's own writes.
    """
    return [
        index
        for index, (issue_key, classification) in enumerate(port.classification_writes)
        if classification == marker and on in (None, issue_key)
    ]


def recording_stage_writes(port):
    """Record the two writes no stage of this table makes, as they are made.

    Returns the lists the cases read: the parents a criterion was authored
    under, and the surfaces whose description was rewritten. Both wrap the
    port rather than replace it, so what is recorded is what the composed
    run actually asked the board for. Shared by both acceptance cases
    because it is the same clause about the same stages, and a second copy
    of a wrapper is a second thing to keep true.
    """
    authored = []
    edited = []
    original_criterion = port.create_criterion_if_absent
    original_description = port.edit_description

    async def recorded_criterion(**kwargs):
        authored.append(kwargs["parent_key"])
        return await original_criterion(**kwargs)

    async def recorded_description(**kwargs):
        edited.append(kwargs["target"])
        return await original_description(**kwargs)

    port.create_criterion_if_absent = recorded_criterion
    port.edit_description = recorded_description
    return authored, edited


def markers_ahead_of_their_admission(port, executor, *, marker, lanes):
    """The lanes whose *marker* write does not follow the sessions that owed it.

    A stage's marker is the durable record of the admission test that set
    it, so on the board's ORDERED write journal every write of it has to sit
    after the last session that assessed that lane while the lane still
    owed the marker. Sessions from the other stage are excluded by that same
    reading: a lane being assessed for the criteria stage already carries the
    ticket stage's marker.

    A lane marked with no such session ahead of it is reported too: a label
    nothing tested is exactly what this clause exists to refuse.
    """
    faults = []
    for key in lanes:
        owed = [
            at
            for named, at, carried in executor.admissions
            if named == key and marker not in carried
        ]
        written = marker_writes(port, marker, on=key)
        if not owed or not written or min(written) < max(owed):
            faults.append(key)
    return faults


async def test_a_scope_run_stages_every_member_before_its_first_ready_read(monkeypatch):
    """Both stages finish on every member before a lane is selected.

    Nothing about the scope is read and no lane is dispatched until each
    stage's marker sits on every member, so the marker writes land ahead of
    the first observation and in the table's order. A second run over the
    same board takes the same path: the stages are satisfied by what is
    already there, so they open no session, write nothing, and the run
    starts at its walk.
    """
    lanes = ("A", "B", "C")
    port = board(lanes=lanes, blocked={"B": ("A",)}, staged=False)
    builds = []
    harness = staging_runtime(port, lanes, monkeypatch=monkeypatch, builds=builds)
    authored, edited = recording_stage_writes(port)

    events = []
    at_first_walk = None
    async for event in drive(harness):
        if at_first_walk is None and isinstance(event, ScopeWalkEvent):
            at_first_walk = len(port.classification_writes)
        events.append(event)

    for key in lanes:
        assert {TICKET_MARKER, STAGED} <= port.issues[key].issue_labels
    ticket_writes = marker_writes(port, TICKET_MARKER)
    criteria_writes = marker_writes(port, STAGED)
    assert len(ticket_writes) == len(criteria_writes) == len(lanes)
    assert max(ticket_writes) < min(criteria_writes)
    # Every marker the stages wrote was on the board before the walk produced
    # its first observation.
    assert at_first_walk == 2 * len(lanes)
    # The stages authored no criterion and rewrote no member body: the board
    # already carried the Checks the criteria proposal quotes.
    assert authored == []
    assert [target for target in edited if target in lanes] == []
    # And each marker sits after the session that admitted that lane for its
    # own stage: a marker written ahead of its admission is a label nothing
    # tested.
    for marker in (TICKET_MARKER, STAGED):
        assert (
            markers_ahead_of_their_admission(
                port, harness.executor, marker=marker, lanes=lanes
            )
            == []
        )

    # The stages left a board the walk reads the ordinary way: the two lanes
    # nothing waits on are the tick's ready set, and the one waiting on a
    # live blocker is excluded by name rather than offered.
    walks = [event for event in events if isinstance(event, ScopeWalkEvent)]
    first = walks[0].observation
    assert first.ready == ("A", "C")
    assert [
        exclusion.issue_key
        for exclusion in first.exclusions
        if exclusion.clause is ExclusionClause.LIVE_BLOCKER
    ] == ["B"]

    replayed = len(port.classification_writes)
    organize_calls = len(harness.executor.organize_calls)
    assert organize_calls
    second = []
    async for event in drive(harness, job="second"):
        second.append(event)
    assert len(harness.executor.organize_calls) == organize_calls
    assert len(port.classification_writes) == replayed
    assert isinstance(second[0], ScopeWalkEvent)
    # A fresh organizer per admission: a cache would hand back one object.
    assert len(builds) == 2
    assert builds[0] is not builds[1]


# ---------------------------------------------------------------------------
# KOD-832 clause 1 — setting the approval label is what starts a run
# ---------------------------------------------------------------------------

#: Every run this module submits is bounded: a walk offered a lane it never
#: finishes would hang, and a hang is not a failing assertion.
RUN_BUDGET_SECONDS = 60


def standing_operation():
    """``organize_operation()`` plus the standing-scope row for the project.

    One declared row, bound to the one declared repository: that row is what
    the heartbeat reads, and it is the whole of what makes the pass exist.
    """
    fields = organize_operation().model_dump()
    fields["organize_scopes"] = [
        {"scope": SCOPE.model_dump(mode="json"), "repo_url": ORIGIN}
    ]
    return OperationConfig.model_validate(fields)


def standing_board(lanes, blocked=None):
    """The scratch board before anybody approved it: no label anywhere.

    Each lane names the project it belongs to, so the one write this test
    makes — the approval label on that project — is what admits both the
    heartbeat's submission and every member's own reading of it.
    """
    port = board(lanes=lanes, blocked=blocked, approved=False, staged=False)
    for key in lanes:
        port.issues[key] = port.issues[key].model_copy(update={"project_id": SCOPE.key})
    return port


def standing_heartbeat(port, queue, operation):
    """The composed pass over the same board and the same process's queue."""
    beat = organize_composition.build_scope_heartbeat(
        config=AppConfig(
            organize=OrganizeSettings(max_admission_rounds=2, max_convergence_rounds=2),
            write_back=WriteBackSettings(max_verify_rounds=2),
        ),
        operation=operation,
        tracker=port,
        queue=queue,
        registry=queue,
    )
    assert beat is not None
    return beat


def approve(port):
    """The one tracker write of these cases, and it is the test's own."""
    port.scope_label_members[SCOPE] = frozenset({ScopeLabel.APPROVED})


async def drain(queue, job_id, *, port=None):
    """Every event of one job, and where the stage writes stood at its walk.

    The marker count at the first observation is read while the stream is
    being consumed, because it is a statement about ORDER: read at the end
    it would be the same number whichever side of the walk the writes fell.
    """
    events = []
    at_first_walk = None
    async with asyncio.timeout(RUN_BUDGET_SECONDS):
        async for event in queue.attach(job_id=job_id):
            if (
                at_first_walk is None
                and port is not None
                and isinstance(event, ScopeWalkEvent)
            ):
                at_first_walk = len(port.classification_writes)
            events.append(event)
    return events, at_first_walk


def errors(events):
    return [event for event in events if isinstance(event, ErrorEvent)]


async def test_setting_the_label_starts_a_run_that_stages_every_issue_then_walks(
    monkeypatch,
):
    """The whole clause, through the pass and the queue a deployment runs.

    Nothing about this board says "run me" until the approval label lands on
    the project. The tick before it reports the row unapproved and costs the
    board nothing; the tick after it submits exactly one scope run, and that
    run stages every member before it reads a single lane. While the job is
    live the row is not submitted again; once it is terminal the next tick is
    the next round, and a fresh pass over the same doubles — a restarted
    process — submits on its first tick.
    """
    lanes = ("A", "B", "C")
    port = standing_board(lanes, {"B": ("A",)})
    operation = standing_operation()
    harness = staging_runtime(
        port, lanes, monkeypatch=monkeypatch, builds=[], operation=operation
    )
    authored, edited = recording_stage_writes(port)
    queue = build_job_queue(settings=JobQueueSettings(), workflow_engine=harness.engine)
    await queue.start()
    try:
        beat = standing_heartbeat(port, queue, operation)
        untouched = handed_over(port)

        # (1) Nobody has approved the project, so nothing runs and nothing is
        # spent: no submission, no session, and the board is byte for byte
        # what it was handed over — every journal of it, rather than the one
        # a case naming journals would have thought to look at.
        assert await beat.run(FIXTURE_EPOCH) is PassRun.SKIPPED
        unapproved = await beat.tick()
        assert [(entry.scope, entry.outcome) for entry in unapproved.entries] == [
            (SCOPE, HeartbeatOutcome.UNAPPROVED)
        ]
        assert list(queue._records) == []
        assert harness.executor.organize_calls == []
        assert untouched()

        # (2) The label lands, and the next tick submits exactly one run.
        approve(port)
        started = await beat.tick()
        (submitted,) = started.entries
        assert submitted.outcome is HeartbeatOutcome.SUBMITTED
        assert submitted.scope == SCOPE
        assert submitted.job_id is not None

        # (4) Asked again while that job is live, the row is not submitted a
        # second time: two runs of one scope would contend over every lane.
        live = await beat.tick()
        assert [entry.outcome for entry in live.entries] == [HeartbeatOutcome.LIVE]
        assert live.entries[0].job_id == submitted.job_id
        assert list(queue._records) == [submitted.job_id]

        events, at_first_walk = await drain(queue, submitted.job_id, port=port)
        assert errors(events) == []
        assert (await queue.get(job_id=submitted.job_id)).state is JobState.TERMINAL

        # (3) What the run left: both markers on every member, the ticket
        # stage's writes ahead of the criteria stage's, and every one of them
        # on the board before the walk produced its first observation.
        for key in lanes:
            assert {TICKET_MARKER, STAGED} <= port.issues[key].issue_labels
        ticket_writes = marker_writes(port, TICKET_MARKER)
        criteria_writes = marker_writes(port, STAGED)
        assert len(ticket_writes) == len(criteria_writes) == len(lanes)
        assert max(ticket_writes) < min(criteria_writes)
        assert at_first_walk == len(port.classification_writes)
        # The stages authored no criterion and rewrote no member body: the
        # board already carried the Checks the criteria proposal quotes.
        assert authored == []
        assert [target for target in edited if target in lanes] == []
        # And every marker write follows the session that admitted that lane
        # for the stage the marker belongs to.
        for marker in (TICKET_MARKER, STAGED):
            assert (
                markers_ahead_of_their_admission(
                    port, harness.executor, marker=marker, lanes=lanes
                )
                == []
            )
        walks = [event for event in events if isinstance(event, ScopeWalkEvent)]
        first = walks[0].observation
        assert first.ready == ("A", "C")
        assert [
            exclusion.issue_key
            for exclusion in first.exclusions
            if exclusion.clause is ExclusionClause.LIVE_BLOCKER
        ] == ["B"]

        # (5) Terminal frees the row, and the next run finds its work done:
        # no organize session, no write, and its first event is its walk.
        staged_writes = len(port.classification_writes)
        organize_calls = len(harness.executor.organize_calls)
        assert organize_calls
        again = await beat.tick()
        (resubmitted,) = again.entries
        assert resubmitted.outcome is HeartbeatOutcome.SUBMITTED
        assert resubmitted.job_id != submitted.job_id
        second, _ = await drain(queue, resubmitted.job_id)
        assert errors(second) == []
        assert isinstance(second[0], ScopeWalkEvent)
        assert len(harness.executor.organize_calls) == organize_calls
        assert len(port.classification_writes) == staged_writes

        # (6) The restart: a fresh pass holds no memory of a submitted job,
        # and the queue it inherits holds none either.
        restarted = await standing_heartbeat(port, queue, operation).tick()
        (third,) = restarted.entries
        assert third.outcome is HeartbeatOutcome.SUBMITTED
        assert third.job_id not in {submitted.job_id, resubmitted.job_id}
        await drain(queue, third.job_id)
        assert len(port.classification_writes) == staged_writes
    finally:
        await queue.stop()


class EscalatingExecutor(OrganizingExecutor):
    """Answers one lane's SECOND-stage admission with an unresolved choice.

    The stage is read off the board rather than counted: a member reaching
    the criteria stage already carries the ticket stage's marker, so the same
    lane's first-stage admission is answered the ordinary way and only its
    second refuses. A double that refused every admission of that lane would
    halt the run one stage earlier and prove nothing about stage two.
    """

    def __init__(self, evaluations, *, port, refuses):
        super().__init__(evaluations, port=port)
        self.refuses = refuses

    async def stream(self, **kwargs):
        title = kwargs["output_format"]["schema"].get("title")
        if title == "AdmissionJudgment":
            key = re.findall(r"<issue_key>(.*?)</issue_key>", kwargs["prompt"])[-1]
            if (
                key == self.refuses
                and TICKET_MARKER in self.port.issues[key].issue_labels
            ):
                self.organize_calls.append(kwargs)
                yield SystemEvent(
                    subtype="init", data={"session_id": "organize-session"}
                )
                yield organize_result(
                    structured_output={
                        "issue_id": key,
                        "verdict": "not_buildable",
                        "invented_decision": "Which reading of the subject governs?",
                        "evidence": "The prepared body admits two readings.",
                        "refusal_kind": "human_decision",
                    }
                )
                return
        async for event in super().stream(**kwargs):
            yield event


async def test_a_stage_two_escalation_holds_the_run_before_any_fire_and_stays_visible(
    monkeypatch,
):
    """Blocked, visible, and costing nothing on the next round (KOD-828).

    The run reaches the criteria stage and one member's admission answers
    with an unresolved choice. The job ends on the halt its caller already
    knows: no lane is offered, no execution prompt is sent, and the member
    carries the decision label a person reads. The next run of the same scope
    is held by the barrier instead — the label that member lacks is the whole
    record — and it spends no session on any member to find that out.
    """
    lanes = ("A", "B", "C")
    port = standing_board(lanes, {"B": ("A",)})
    operation = standing_operation()
    harness = staging_runtime(
        port,
        lanes,
        monkeypatch=monkeypatch,
        builds=[],
        operation=operation,
        executor=EscalatingExecutor(
            [
                native_evaluation(checks={f"{key}/check": f"{key} live Check  bytes"})
                for key in lanes
                for _ in range(2)
            ],
            port=port,
            refuses="B",
        ),
    )
    queue = build_job_queue(settings=JobQueueSettings(), workflow_engine=harness.engine)
    await queue.start()
    try:
        beat = standing_heartbeat(port, queue, operation)
        approve(port)
        (submitted,) = (await beat.tick()).entries
        events, _ = await drain(queue, submitted.job_id)

        (halted,) = errors(events)
        assert halted.error_kind == "OrganizeHaltError"
        assert StageHaltCause.HUMAN_DECISION.value in halted.error
        assert "decision" in port.issues["B"].issue_labels
        # Nothing was offered and nothing fired: the halt reached the caller
        # before the walk began.
        assert [event for event in events if isinstance(event, ScopeWalkEvent)] == []
        assert harness.executor.execution_prompts == []
        # Stage one finished for everybody; stage two labelled nobody, because
        # the halt is the stage's and not the member's.
        for key in lanes:
            assert TICKET_MARKER in port.issues[key].issue_labels
            assert STAGED not in port.issues[key].issue_labels

        # The next submission is held by the barrier, and it costs nothing:
        # the escalated member is not re-admitted, and the members that owe
        # nothing are read off their own labels.
        spent = len(harness.executor.organize_calls)
        assert spent, "the first run did open the sessions this one does not"
        writes = len(port.classification_writes)
        (again,) = (await beat.tick()).entries
        assert again.outcome is HeartbeatOutcome.SUBMITTED
        second, _ = await drain(queue, again.job_id)
        (barred,) = errors(second)
        assert barred.error_kind == "OrganizeHaltError"
        assert StageHaltCause.STAGE_INCOMPLETE.value in barred.error
        assert [event for event in second if isinstance(event, ScopeWalkEvent)] == []
        assert len(harness.executor.organize_calls) == spent
        assert len(port.classification_writes) == writes

        # The typed report behind that error, read where a caller reads it:
        # the stage it held and the one member it names.
        with pytest.raises(OrganizeHaltError) as caught:
            _ = [event async for event in drive(harness, job="barrier")]
        halt = caught.value.report.halt
        assert halt.cause is StageHaltCause.STAGE_INCOMPLETE
        assert halt.phase is MandateKind.CRITERIA
        assert halt.unlabelled_issue_ids == ("B",)
        assert len(harness.executor.organize_calls) == spent
        assert len(port.classification_writes) == writes
    finally:
        await queue.stop()
