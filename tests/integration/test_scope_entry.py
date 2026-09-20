"""What a composed scope run does before its first tick.

Over the same composed engine the rest of the scope suite drives, so the
entry these cases exercise is the one a request reaches and not a second
wiring written here.
"""

import json
import re

import pytest

from kodezart.composition import organize as organize_composition
from kodezart.config.organize import OrganizeSettings
from kodezart.domain.errors import OrganizeHaltError, ScopeNotApprovedError
from kodezart.types.domain.agent import SystemEvent
from kodezart.types.domain.dispatch import ExclusionClause
from kodezart.types.domain.operation import OperationConfig, ScopeLabel
from kodezart.types.domain.organize_owner import (
    OrganizeReport,
    StageHaltCause,
    StageHaltReport,
)
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import ScopeWalkEvent
from tests.chains.test_native_fire import (
    TRUNK_BRANCHES,
    WORK_SHA,
    native_evaluation,
    native_operation,
)
from tests.chains.test_organize import result as organize_result
from tests.fakes import FakeGitService
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


def staging_runtime(port, lanes, *, monkeypatch, builds):
    """The composed engine over the organize table, on a fresh organizer.

    Each admission builds its own organizer, which is what keeps the
    steady-state path and a restarted process's path one path; the wrapper
    records the objects so a cache would be visible as a reused one.
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
        operation=organize_operation(),
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
        ),
    )


def marker_writes(port, marker):
    """The indices in the fake's write journal at which *marker* was written."""
    return [
        index
        for index, (_, classification) in enumerate(port.classification_writes)
        if classification == marker
    ]


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
