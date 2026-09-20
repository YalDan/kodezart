"""The organize replay contract, over every registered tracker implementation.

A stage's roster is keyed on its own label and on nothing else: not on a
provenance footer in the body, and not on what the owner object happens to
remember. Both readings are port-level facts — a stage asks the tracker who
carries the marker — so the cases are stated once here and run over every
entry in the conformance registry, the shipped adapter and the consumer
double alike. An implementation that answered the marker question its own
way would fail exactly where a non-conforming vendor adapter would.

The workspace these cases dial differs from the ordinary conformance one in
three stated ways, and in no others: the mandate table's own classification
vocabulary and run-stage marker key, one scope holding two members, and a
project that carries the admission vocabulary's approved member — approval
is read through the cascade from the project, never set on either member,
so the owner's one admission reading is exercised as deployments reach it.
"""

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from inspect import isawaitable

import pytest

from kodezart.composition.organize import build_organize_owner
from kodezart.config.app import AppConfig
from kodezart.config.organize import OrganizeSettings
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.core.protocols import TrackerPort
from kodezart.domain.organize import stage_rows
from kodezart.services.agent_service import AgentService
from kodezart.services.organize_owner import OrganizeOwner
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig, ScopeLabel
from kodezart.types.domain.organize_owner import OrganizeReport
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from tests.chains.test_organize import RecordingWorkspace, result
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeGitService,
    FakeLinearMcpServer,
    PassThroughGate,
)
from tests.prompts.test_organize_mandate_bindings import declared_operation
from tests.prompts.test_prompt_wiring import load_registry
from tests.tracker.conftest import (
    APPROVED_ISSUE,
    CLAIMED_ISSUE,
    TRACKER_IMPLEMENTATIONS,
    FixtureClock,
    TrackerWorkspace,
    fixture_server,
    observed_writes,
)


def _operation() -> OperationConfig:
    """The declared mandate table, with the organize prompts every row uses.

    The table itself is the declared one: the run stages are gated and
    marked by the labels it names, so the marker keys these cases dial into
    both implementations are read off this configuration rather than spelled
    a second time here.
    """
    fields = declared_operation().model_dump()
    for mandate in fields["organize_mandates"]:
        mandate["rubric_prompt_key"] = "organize_assess"
        mandate["admission_prompt_key"] = "organize_assess"
    return OperationConfig.model_validate(fields)


OPERATION = _operation()
PROMPTS = load_registry(
    default_set="claude-opus", bindings=operation_bindings(OPERATION)
)

#: The run-stage rows and the marker each one is complete by, read off the
#: table rather than restated: a case naming a label the configuration does
#: not is testing a vocabulary no deployment has.
RUN_STAGES = stage_rows(OPERATION.resolve_organize_mandates(), under_approval=True)
STAGE_MARKERS = tuple(row.terminal_marker for row in RUN_STAGES)
GROOM_MARKER = next(
    row.terminal_marker
    for row in OPERATION.resolve_organize_mandates()
    if not row.role.runs_under_approval
)
APPROVED_LABEL = OPERATION.scope_labels[ScopeLabel.APPROVED.value]
#: The marker keys, as a snapshot reports them: the semantic side of the
#: mapping, which is what the roster compares.
STAGE_MARKER_KEYS = tuple(
    key for key, label in OPERATION.issue_labels.items() if label in STAGE_MARKERS
)
CRITERIA_STAGE_LABEL_KEY = next(
    key
    for key, label in OPERATION.issue_labels.items()
    if label
    == next(row.terminal_marker for row in RUN_STAGES if row.role.marks_execution_stage)
)

#: The two members of one scope. Two, because "every member" is not a claim
#: a one-member board can refute: the first pass has to label both and the
#: replay has to leave both alone.
SCOPE = ScopeRef(kind=ScopeKind.ISSUE, key=CLAIMED_ISSUE)
MEMBERS = (CLAIMED_ISSUE, APPROVED_ISSUE)

#: The project the members belong to, and the container the approved member
#: is set on. Approval reaches a member through the cascade here, which is
#: how an operator grants it: per project, not per issue.
PROJECT = ScopeRef(kind=ScopeKind.PROJECT, key="fixture-project")
PROJECT_NAME = "fixture project"

REPO_URL = "https://example.invalid/repository"
BASE_REF = "a" * 40
JOB_ID = "organize-replay-job"

#: A body that needs no rework: the session answers buildable on it, so a
#: first pass over an unlabelled member spends no body write and the cases
#: are about the marker rather than about the author.
SUFFICIENT_BODY = "Sufficient body grounded in the declared source."
#: A provenance line of the shape a writer leaves behind. It is not a
#: label, and a roster that read it as one would treat a member nothing has
#: admitted as already done.
PROVENANCE_FOOTER = "\n\n---\norganized by kodezart"

CRITERIA = ("Check prepared bytes",)
ADMISSION_SCHEMA = "AdmissionJudgment"
PROPOSAL_SCHEMA = "OrganizeProposal"
WRITE_BACK_SCHEMA = "WriteBackFinding"
CRITERIA_AUTHOR_PROMPT = "Author criterion sub-issue proposals"


def _project_payload(labels: Sequence[str]) -> dict[str, object]:
    """``get_project`` as the vendor answers it, in the fields read here."""
    return {
        "id": PROJECT.key,
        "name": PROJECT_NAME,
        "description": f"Complete description of {PROJECT.key}",
        "url": f"https://tracker.invalid/project/{PROJECT.key}",
        "initiatives": [],
        "labels": list(labels),
    }


def _schema_title(call: Mapping[str, object]) -> str | None:
    """The output schema one opened session asked for, by name."""
    output_format = call["output_format"]
    assert isinstance(output_format, Mapping)
    schema = output_format["schema"]
    assert isinstance(schema, Mapping)
    title = schema.get("title")
    return None if title is None else str(title)


class PortExecutor:
    """Sessions answered from the CURRENT board, read through the port.

    Shaped like the owner suite's board executor, with one difference that
    the registry forces: the board is read through the implementation under
    test rather than out of one backend's own store. A criterion child this
    run creates exists on whichever board serves the case, and an executor
    reaching past the port would have found it on one arm only.
    """

    def __init__(self, tracker: TrackerPort, *, criteria: Sequence[str] = CRITERIA):
        self._tracker = tracker
        self.criteria = tuple(criteria)
        self.calls: list[dict[str, object]] = []

    def schemas(self, title: str) -> list[dict[str, object]]:
        """The sessions of one schema, in the order they were opened."""
        return [call for call in self.calls if _schema_title(call) == title]

    def named(self, title: str, issue_key: str) -> list[dict[str, object]]:
        """The sessions of one schema whose subject is *issue_key*."""
        return [
            call
            for call in self.schemas(title)
            if f"<issue_key>{issue_key}</issue_key>" in str(call["prompt"])
        ]

    async def stream(self, **kwargs: object):
        self.calls.append(kwargs)
        prompt = str(kwargs["prompt"])
        title = _schema_title(kwargs)
        if title == WRITE_BACK_SCHEMA:
            match = re.search(
                r"<written_artifact>\s*(.*?)\s*</written_artifact>", prompt, re.S
            )
            assert match is not None, "a write-back session carries its artifact"
            artifact = json.loads(match[1])
            payload: dict[str, object] = {
                "verdict": "holds",
                "evidence": f"Read the actual landed artifact: {artifact['content']}",
                "cited_refs": [],
            }
        else:
            key = re.findall(r"<issue_key>(.*?)</issue_key>", prompt)[-1]
            issue = await self._tracker.read_issue(issue_key=key)
            if title == PROPOSAL_SCHEMA and CRITERIA_AUTHOR_PROMPT in prompt:
                payload = {
                    "kind": "criteria",
                    "issue_id": key,
                    "criteria": [
                        {
                            "title": title_text,
                            "check": f"{title_text} match the declared source.",
                            "do": f"Compare the source and {title_text.lower()}.",
                        }
                        for title_text in self.criteria
                    ],
                }
            elif title == PROPOSAL_SCHEMA:
                payload = {
                    "kind": "body",
                    "issue_id": key,
                    "body": SUFFICIENT_BODY,
                }
            else:
                payload = {
                    "issue_id": key,
                    "verdict": "buildable",
                    "evidence": f"Fresh board body checked: {issue.body}",
                }
        yield result(structured_output=payload)


@dataclass
class ReplayBoard:
    """One implementation, the board behind it, and a fresh owner per ask."""

    tracker: TrackerPort
    server: FakeLinearMcpServer
    observed: Callable[[], tuple[object, ...]]

    def owner(self) -> tuple[OrganizeOwner, PortExecutor]:
        """A NEW owner over the same board, through the public constructor.

        Each ask builds its own owner and its own session log, so a case
        about what a restarted process reads can hold two owners over one
        board and count the second one's sessions on their own.
        """
        sessions = PortExecutor(self.tracker)
        workspace = RecordingWorkspace()
        owner = build_organize_owner(
            config=AppConfig(
                organize=OrganizeSettings(
                    max_admission_rounds=2, max_convergence_rounds=2
                ),
                write_back={"max_verify_rounds": 2},
            ),
            operation=OPERATION,
            tracker=self.tracker,
            runner=AgentService(
                executor=sessions,
                workspace=workspace,
                git_base_url="https://example.invalid",
            ),
            workspace=workspace,
            git=FakeGitService(
                remote_branch_shas={repo.trunk: BASE_REF for repo in OPERATION.repos}
            ),
            prompts=PROMPTS,
            skills=SUPPRESS_ALL_SKILLS,
            gate=PassThroughGate(),
            repo_url=REPO_URL,
            phases=RUN_STAGES,
        )
        return owner, sessions

    async def run(self) -> tuple[OrganizeReport, PortExecutor]:
        """One pass of a fresh owner over this board."""
        owner, sessions = self.owner()
        return await self.replay(owner), sessions

    async def replay(self, owner: OrganizeOwner) -> OrganizeReport:
        """Another pass of an owner that already ran."""
        return await owner.run(
            scope=SCOPE,
            repo_url=REPO_URL,
            base_ref=BASE_REF,
            job_id=JOB_ID,
            visibility=RepoVisibility.PRIVATE,
        )

    async def marker_keys(self, issue_key: str) -> frozenset[str]:
        """The stage marker keys *issue_key* carries, as the port reports."""
        issue = await self.tracker.read_issue(issue_key=issue_key)
        return issue.issue_labels & frozenset(STAGE_MARKER_KEYS)


def _board(
    clock: FixtureClock,
    *,
    bodies: Mapping[str, str],
    marked: Sequence[str] = (),
) -> FakeLinearMcpServer:
    """The fixture workspace, widened by exactly what a run stage needs.

    One scope of two members, both groomed and both in the approved
    project; *bodies* states each member's own body and *marked* the
    members that already carry both stage markers.
    """
    server = fixture_server(clock=clock)
    server.projects[PROJECT.key] = _project_payload([APPROVED_LABEL])
    server.issues[APPROVED_ISSUE].parent_id = CLAIMED_ISSUE
    for key in MEMBERS:
        native = server.issues[key]
        native.project = PROJECT_NAME
        native.project_id = PROJECT.key
        native.description = bodies[key]
        native.labels = [
            *native.labels,
            GROOM_MARKER,
            *(STAGE_MARKERS if key in marked else ()),
        ]
    return server


async def _dialled(
    name: str, server: FakeLinearMcpServer, clock: FixtureClock
) -> ReplayBoard:
    """*server* served by the registered implementation *name*."""
    port = TRACKER_IMPLEMENTATIONS[name](
        TrackerWorkspace(
            server=server,
            clock=clock,
            scope_labels=OPERATION.scope_labels,
            issue_labels=OPERATION.issue_labels,
            criteria_stage_label_key=CRITERIA_STAGE_LABEL_KEY,
        ),
    )
    tracker = await port if isawaitable(port) else port
    return ReplayBoard(
        tracker=tracker, server=server, observed=observed_writes(tracker, server)
    )


@pytest.fixture(params=sorted(TRACKER_IMPLEMENTATIONS))
async def unlabelled_board(
    request: pytest.FixtureRequest, clock: FixtureClock
) -> ReplayBoard:
    """Every implementation, over a board no stage has marked yet."""
    return await _dialled(
        request.param,
        _board(clock, bodies=dict.fromkeys(MEMBERS, SUFFICIENT_BODY)),
        clock,
    )


@pytest.fixture(params=sorted(TRACKER_IMPLEMENTATIONS))
async def footer_board(
    request: pytest.FixtureRequest, clock: FixtureClock
) -> ReplayBoard:
    """Every implementation, over a board one member's footer describes.

    The marked member carries the markers and no trailing text; the other
    carries a provenance footer and no marker at all. Only a roster reading
    the footer as a record confuses the two.
    """
    return await _dialled(
        request.param,
        _board(
            clock,
            bodies={
                CLAIMED_ISSUE: SUFFICIENT_BODY,
                APPROVED_ISSUE: SUFFICIENT_BODY + PROVENANCE_FOOTER,
            },
            marked=(CLAIMED_ISSUE,),
        ),
        clock,
    )


async def test_the_first_pass_labels_every_member_on_every_implementation(
    unlabelled_board: ReplayBoard,
) -> None:
    """Both run stages complete, and each member ends carrying both markers.

    The stage is complete only when every member carries its marker, so an
    implementation that labelled the addressed issue and left its child
    unmarked has not served the contract, whatever it returned.
    """
    report, sessions = await unlabelled_board.run()

    assert report.halt is None, report.halt
    assert [phase.value for phase in report.completed_phases] == [
        row.spec.kind.value for row in RUN_STAGES
    ]
    for key in MEMBERS:
        assert await unlabelled_board.marker_keys(key) == frozenset(STAGE_MARKER_KEYS)
    assert sessions.schemas(ADMISSION_SCHEMA), "an unmarked member is admitted first"
    for key in MEMBERS:
        children = await unlabelled_board.tracker.read_criteria(issue_key=key)
        assert len(children) == len(CRITERIA)


async def test_a_second_pass_over_the_same_board_issues_zero_writes(
    unlabelled_board: ReplayBoard,
) -> None:
    """A second pass over the board the first one labelled writes nothing.

    Every write, not the classification writes alone: a replay that spent a
    lease comment or re-edited a body would be re-doing the stage quietly.
    """
    owner, sessions = unlabelled_board.owner()
    first = await unlabelled_board.replay(owner)
    assert first.halt is None, first.halt
    assert sessions.calls, "the first pass over an unmarked board opens sessions"

    settled = len(sessions.calls)
    written = len(unlabelled_board.observed())
    second = await unlabelled_board.replay(owner)

    assert second.halt is None, second.halt
    assert [phase.value for phase in second.completed_phases] == [
        row.spec.kind.value for row in RUN_STAGES
    ]
    assert len(sessions.calls) == settled
    assert len(unlabelled_board.observed()) == written


async def test_a_fresh_owner_reads_every_labelled_member_as_labelled(
    unlabelled_board: ReplayBoard,
) -> None:
    """A restarted process reads the board, not what an owner remembered.

    The second pass here is made by a DIFFERENT owner object, which is what
    a restarted process has: nothing remembered, and the labels the first
    pass left as the only record of the admission test that set them. Its
    first round must read every member as labelled — no session, no write —
    or a restart re-runs a stage that is already complete.
    """
    first_owner, first_sessions = unlabelled_board.owner()
    first = await unlabelled_board.replay(first_owner)
    assert first.halt is None, first.halt
    assert first_sessions.calls, "the first pass over an unmarked board opens sessions"

    written = len(unlabelled_board.observed())
    fresh_owner, fresh_sessions = unlabelled_board.owner()
    assert fresh_owner is not first_owner
    second = await unlabelled_board.replay(fresh_owner)

    assert second.halt is None, second.halt
    assert [phase.value for phase in second.completed_phases] == [
        row.spec.kind.value for row in RUN_STAGES
    ]
    assert fresh_sessions.calls == []
    assert len(unlabelled_board.observed()) == written


async def test_the_label_decides_the_roster_and_a_provenance_footer_does_not(
    footer_board: ReplayBoard,
) -> None:
    """An "organized by" line in a body is prose, never the stage's record.

    The member whose body ends in that footer carries no marker, so it owes
    both and is admitted, worked and marked. The member that carries the
    markers and no footer is out of the roster and is not touched.
    """
    report, sessions = await footer_board.run()

    assert report.halt is None, report.halt
    assert [phase.value for phase in report.completed_phases] == [
        row.spec.kind.value for row in RUN_STAGES
    ]
    assert await footer_board.marker_keys(APPROVED_ISSUE) == frozenset(
        STAGE_MARKER_KEYS
    )
    assert sessions.named(ADMISSION_SCHEMA, APPROVED_ISSUE), (
        "a member owing its marker is admitted, whatever its body says"
    )
    assert sessions.named(PROPOSAL_SCHEMA, CLAIMED_ISSUE) == [], (
        "the labelled member is out of the roster and is never re-authored"
    )
