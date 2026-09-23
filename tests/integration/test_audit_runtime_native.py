"""Configured audit uses actual Git, native tracker and fresh structured dispatch."""

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest

from kodezart.adapters.git.bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.git.service import SubprocessGitService
from kodezart.adapters.git.worktree_provider import GitWorktreeProvider
from kodezart.composition.audit import build_audit_pass
from kodezart.config.app import AppConfig
from kodezart.domain.criterion_evidence import render_evidence_field
from kodezart.domain.run_event_stream import LaneRunEvent
from kodezart.services.agent_service import AgentService
from kodezart.services.audit_runtime import AuditRunIncompleteError
from kodezart.types.domain.agent import (
    AUDIT_CLAIM_SCHEMA,
    AUDIT_MANDATE_SCHEMA,
    AUDIT_OVERCLAIM_SCHEMA,
    DETECTOR_REMOVAL_SCHEMA,
    WRITE_BACK_SCHEMA,
)
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_evidence import AuditRestampTrace
from kodezart.types.domain.audit_overclaim import OverclaimKind
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.pr_state import PRLifecycle, PRState
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.surface import SurfaceKind
from tests.chains.test_organize import RecordingExecutor, result
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeCIMonitor,
    FakeMcpIssue,
    FakePRStateReader,
    PassThroughGate,
)
from tests.integration.test_audit_scheduler import declare_organize_owner
from tests.prompts.test_prompt_wiring import load_registry
from tests.tracker.conftest import APPROVED_ISSUE, FIXTURE_NOW, WORKFLOW_STATE_NAMES
from tests.tracker.test_audit_evidence_git import repository as repository
from tests.tracker.test_audit_overclaim_sweep import payload as overclaims
from tests.tracker.test_audit_requests import (
    CHILD,
    ROOT,
    SCOPE,
    lane_record,
)
from tests.tracker.test_audit_requests import (
    operation as base_operation,
)
from tests.tracker.test_linear_mcp_tracker import tracker_over
from tests.tracker.test_state_history import server as server

#: The Check sentence the base fixture's one criterion carries. A fixture
#: adding criteria gives each its own sentence, and the doubles below read the
#: subject of a session off the prompt rather than off a module constant, so a
#: scope with several criteria answers per criterion.
BASE_CHECK = "The current check.txt contains the committed contents."

#: The lane the fixture's own record is written under, so an event seeded
#: into the stream is one this scope's requests actually read.
LANE_KEY = "opaque:lane \u03bb"


def criterion_body(*, check: str, graded_sha: str) -> str:
    """One criterion sub-issue body: its Check, its Do and its Evidence row."""
    return f"**Check:** {check}\n**Do:** AUTHOR REASONING\n" + render_evidence_field(
        CriterionEvidence(graded_sha=graded_sha, test="historical author reasoning")
    )


def add_criterion(server, key, *, status, status_type, graded_sha, check):
    """Add one more criterion sub-issue under ``ROOT`` to the fake workspace."""
    template = server.issues[CHILD]
    server.issues[key] = FakeMcpIssue(
        id=key,
        parent_id=ROOT,
        labels=list(template.labels),
        created_at=template.created_at,
        state_changed_at=template.state_changed_at,
        updated_at=template.updated_at,
        status=status,
        status_type=status_type,
        description=criterion_body(check=check, graded_sha=graded_sha),
    )
    return server.issues[key]


def state_writes(server):
    """The ``save_issue`` calls that carry a state, in the order they landed."""
    return [
        dict(arguments)
        for name, arguments in server.calls
        if name == "save_issue" and "state" in arguments
    ]


def unstarted_state(server, team="fixture-team"):
    """How the workspace addresses the team's one unstarted state."""
    names = [
        name
        for name in server.statuses[team]
        if server.state_types.get(name) == "unstarted"
    ]
    assert len(names) == 1, names
    return f"{team}-{names[0]}-id"


def landed(server, tool, **match):
    """The index of the one *tool* call whose arguments carry *match*."""
    found = [
        index
        for index, (name, arguments) in enumerate(server.calls)
        if name == tool
        and all(arguments.get(field) == value for field, value in match.items())
    ]
    assert len(found) == 1, found
    return found[0]


class NativeExecutor(RecordingExecutor):
    def __init__(self, head):
        super().__init__([])
        self.head = head
        self.during = None
        self.claim_verdict = "holds"
        #: One verdict per criterion, keyed by criterion key. A key with no
        #: entry falls back to ``claim_verdict``.
        self.claim_verdicts = {}
        self.overclaim_verdict = "holds"
        self.write_verdict = "holds"
        self.claim_key = None
        self.instruction = False
        #: The mandate prose the hunt reports. Model-authored text, so a
        #: fixture varies it to show the escalation identity does not move
        #: with the wording.
        self.mandate_text = "Explicit parent instructions."
        self.refuse_after_first_refutation = False
        #: A criterion key whose reopen every write-back round refutes, so the
        #: move exhausts its verification budget. Every other round holds.
        self.refute_reopen_of = None
        self.write_calls = 0
        #: The Check sentence of each fixture criterion, so a session's
        #: subject is read off the prompt it was given.
        self.checks = {CHILD: BASE_CHECK}

    def subject(self, prompt):
        """Which criterion this session is about, by the Check it carries."""
        matched = [key for key, check in self.checks.items() if check in prompt]
        assert len(matched) == 1, matched
        return matched[0]

    @staticmethod
    def tagged(prompt, tag):
        return prompt.split(f"<{tag}>", 1)[1].split(f"</{tag}>", 1)[0]

    def defect_class(self, prompt):
        """The exact defect class this mandate hunt was asked about."""
        return self.tagged(prompt, "defect_class")

    def judges_a_reopen(self, prompt):
        """Whether this write-back round judges ``refute_reopen_of``'s move.

        Read off the written artifact the round was handed, the way a judge
        reads it: that criterion's own sub-issue surface, carrying an
        unstarted state. A classification label on the same surface and a
        comment about the criterion are other artifacts and other rounds.
        """
        if self.refute_reopen_of is None:
            return False
        artifact = json.loads(self.tagged(prompt, "written_artifact"))
        surface = artifact["surface"]
        return (
            surface["kind"] == SurfaceKind.CRITERION_SUB_ISSUE.value
            and surface["ref"]["key"] == self.refute_reopen_of
            and '"state_kind": "unstarted"' in artifact["content"]
        )

    def mandating_surface(self, prompt):
        """The index the hunt supplied for the parent body carrying the text."""
        surfaces = json.loads(self.tagged(prompt, "audited_surfaces"))
        return next(row["index"] for row in surfaces if row["tracker_key"] == ROOT)

    async def stream(self, **kwargs):
        assert kwargs["session_id"] is None
        schema = kwargs["output_format"]["schema"]
        if schema == AUDIT_MANDATE_SCHEMA and not self.tagged(
            kwargs["prompt"], "head_sha"
        ):
            # A hunt with no head pin stands in an empty directory: the
            # refuted branch is gone and there is no repository to read.
            assert not any(Path(kwargs["cwd"]).iterdir())
        else:
            assert (
                Path(kwargs["cwd"], "check.txt").read_text()
                == "current committed contents\n"
            )
        if self.during is not None:
            await self.during(kwargs)
        if schema == AUDIT_CLAIM_SCHEMA:
            about = self.subject(kwargs["prompt"])
            payload = {
                "criterionKey": self.claim_key or about,
                "verdict": self.claim_verdicts.get(about, self.claim_verdict),
                "evidence": "Read check.txt at the current commit.",
            }
        elif schema == AUDIT_OVERCLAIM_SCHEMA:
            payload = (
                overclaims()
                if self.overclaim_verdict == "holds"
                else overclaims(
                    OverclaimKind.AGGREGATE,
                    verdict=self.overclaim_verdict,
                    evidence="A recount of the claimed total refutes it.",
                    recomputedValue="3",
                )
            )
            payload["criterionKey"] = self.subject(kwargs["prompt"])
        elif schema == DETECTOR_REMOVAL_SCHEMA:
            payload = {
                "criterionKey": self.subject(kwargs["prompt"]),
                "verdict": "holds",
                "evidence": "Compared actual committed revisions.",
                "findings": [],
            }
        elif schema == AUDIT_MANDATE_SCHEMA:
            payload = {
                "verdict": "refuted",
                "finding": None,
                "source_index": None,
                "evidence": "No instruction mandates the observed defect.",
            }
            if self.instruction:
                payload = {
                    "verdict": "holds",
                    "source_index": self.mandating_surface(kwargs["prompt"]),
                    "evidence": (
                        "The exact current parent instruction mandates this defect."
                    ),
                    "finding": {
                        "issue_id": ROOT,
                        # The hunt refuses a finding whose defect class differs
                        # from the one it was asked about, so the double answers
                        # the question it was given rather than a fixed one.
                        "defect_class": self.defect_class(kwargs["prompt"]),
                        "role": "mandate",
                        "mandate_text": self.mandate_text,
                        "evidence": (
                            "Read the exact parent body and measured check.txt."
                        ),
                    },
                }
        else:
            assert schema == WRITE_BACK_SCHEMA
            self.write_calls += 1
            if self.judges_a_reopen(kwargs["prompt"]):
                self.write_verdict = "refuted"
            elif self.refuse_after_first_refutation and self.write_calls == 1:
                self.write_verdict = "refuted"
                self.claim_verdict = "unverifiable"
            else:
                self.write_verdict = "holds"
            payload = {
                "verdict": self.write_verdict,
                "evidence": "Read actual native artifact against check.txt.",
                "cited_refs": ["check.txt"] if self.write_verdict == "refuted" else [],
            }
        self.events = [result(structured_output=payload)]
        async for event in super().stream(**kwargs):
            yield event


def native_operation(repo_url, *, trunk="ordinary-name"):
    """The configured operation the native audit fixtures run under."""
    fields = base_operation(repos=(repo_url,)).model_dump()
    fields["repos"][0].update(
        trunk=trunk,
        checks=[{"name": "test", "command": "cat check.txt", "forge_check": "test"}],
    )
    fields["workflow_states"] = WORKFLOW_STATE_NAMES
    fields["marker_prefixes"].update(
        audit="native-audit", escalation="native-audit-escalation"
    )
    fields["issue_labels"].update(
        criterion="acceptance-condition",
        decision="needs-decision",
        criteria_ready="criteria-prepared",
    )
    fields["organize_scopes"] = [
        {
            "scope": SCOPE.model_dump(),
            "repo_url": repo_url,
            "report_issue_key": APPROVED_ISSUE,
        }
    ]
    return OperationConfig.model_validate(declare_organize_owner(fields))


async def build_native_audit(
    repository, server, tmp_path, *, gate, trunk="ordinary-name", ci=None
):
    """The composed audit over the native doubles, under the supplied gate.

    *ci* replaces the checks double, which otherwise answers green at the
    head and has no run at any other commit.
    """
    remote, _author, _observer, _prior, head = repository
    operation = native_operation(remote.as_uri(), trunk=trunk)
    server._comment_clock = lambda: FIXTURE_NOW
    tracker = tracker_over(
        server,
        issue_labels=operation.issue_labels,
        marker_prefixes=operation.marker_prefixes,
    )
    server.issues[ROOT].status = "In Review"
    server.issues[ROOT].status_type = "started"
    server.issues[ROOT].description = "Explicit parent instructions."
    server.issues[CHILD].description = criterion_body(check=BASE_CHECK, graded_sha=head)
    await lane_record(
        tracker,
        data={
            "headSha": head,
            "pushedHeadSha": head,
            "commits": [{"sha": head, "subject": "current", "issueId": ROOT}],
            "commitsAhead": 1,
            "pr": {"number": 7, "url": f"{remote.as_uri()}/pull/7", "state": "OPEN"},
        },
    )
    git = SubprocessGitService(remote="configured-remote")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "actual-audit-cache"))
    workspace = GitWorktreeProvider(git=git, cache=cache)
    executor = NativeExecutor(head)
    forge = FakePRStateReader(
        records={
            (remote.as_uri(), 7): PRState(
                url=f"{remote.as_uri()}/pull/7",
                number=7,
                head_repo_url=remote.as_uri(),
                base_repo_url=remote.as_uri(),
                base_branch="ordinary-name",
                head_branch="ordinary-name",
                head_sha=head,
                lifecycle=PRLifecycle.OPEN,
            )
        }
    )
    ci = ci or FakeCIMonitor(
        observed_sha_by_ref={head: head}, check_names=frozenset({"test"})
    )
    config = AppConfig(
        _env_file=None,
        git={"remote": "configured-remote"},
        audit={"timeout_seconds": 90},
        write_back={"max_verify_rounds": 2},
        audit_sweep_interval_seconds=60,
        audit_full_sweep_interval_seconds=120,
    )
    runner = AgentService(
        executor=executor, workspace=workspace, git_base_url=remote.as_uri()
    )
    audit = build_audit_pass(
        config=config,
        operation=operation,
        tracker=tracker,
        forge=forge,
        ci=ci,
        git=git,
        cache=cache,
        workspace=workspace,
        runner=runner,
        prompts=load_registry(),
        skills=SUPPRESS_ALL_SKILLS,
        gate=gate,
    )
    return audit, executor, server, tracker, git, workspace, repository


@pytest.fixture
async def native_audit(repository, server, tmp_path):
    return await build_native_audit(
        repository, server, tmp_path, gate=PassThroughGate()
    )


async def test_current_native_scope_publishes_verified_records_then_summary(
    native_audit,
):
    audit, executor, server, _tracker, _git, workspace, _repository = native_audit
    assert await audit.run(FIXTURE_NOW) is PassRun.RAN
    report = audit.last_report
    assert report.scopes[0].status == "complete", report.model_dump_json()
    assert {row.issue_key for row in report.scopes[0].coverage.covered} == {ROOT, CHILD}
    assert report.scopes[0].writes[-1].artifact.surface.ref.key == APPROVED_ISSUE
    assert report.scopes[0].writes[-1].artifact.native_ref in {
        row.id for row in server.comments
    }
    assert len(report.scopes[0].writes) == 9
    judged = json.loads(
        executor.calls[-1]["prompt"]
        .split("<written_artifact>\n", 1)[1]
        .split("\n</written_artifact>", 1)[0]
    )
    summary = json.loads(judged["content"].partition("\n")[2])
    assert len(summary["records"]) == 8
    for ref, snapshot in zip(summary["record_refs"], summary["records"], strict=True):
        actual = next(row for row in server.comments if row.id == ref)
        assert snapshot["native_ref"] == actual.id
        assert snapshot["content"] == actual.body
    assert all(row.verdict.value == "holds" for row in report.scopes[0].writes)
    assert server.issues[ROOT].status == "In Review"
    assert server.issues[CHILD].status == "Done"
    assert not workspace._workspaces
    first_calls = len(executor.calls)
    await audit.run(FIXTURE_NOW + timedelta(seconds=60))
    assert not audit.last_report.scopes[0].coverage.full
    assert not audit.last_report.scopes[0].coverage.covered
    assert len(executor.calls) == first_calls + 1
    await audit.run(FIXTURE_NOW + timedelta(seconds=120))
    assert audit.last_report.scopes[0].coverage.full
    assert len(audit.last_report.scopes[0].coverage.covered) == 2


@pytest.mark.parametrize("change", ["source", "wrong_identity", "cancel", "head"])
async def test_native_source_or_identity_failure_cannot_publish_clean_coverage(
    native_audit, change
):
    audit, executor, server, _tracker, _git, workspace, repository = native_audit
    if change == "wrong_identity":
        executor.claim_key = "another/native-criterion"
    else:

        async def during(kwargs):
            if kwargs["output_format"]["schema"] == AUDIT_CLAIM_SCHEMA:
                if change == "cancel":
                    raise asyncio.CancelledError
                if change == "head":
                    from tests.tracker.test_audit_evidence_git import command

                    command(
                        repository[1],
                        "push",
                        "configured-remote",
                        "--delete",
                        "ordinary-name",
                    )
                else:
                    server.issues[
                        CHILD
                    ].description += "\nnew source after session began"

        executor.during = during
    with pytest.raises(
        asyncio.CancelledError if change == "cancel" else AuditRunIncompleteError
    ):
        await audit.run(FIXTURE_NOW)
    records = [row for row in server.comments if row.body.startswith("[native-audit:")]
    if change in {"source", "cancel"}:
        assert records == []
    else:
        assert audit.last_report.scopes[0].status == "incomplete"
        assert not any(row.issue_id == APPROVED_ISSUE for row in records)
        payloads = [
            json.loads(row.body.partition("\n")[2])["publication"] for row in records
        ]
        assert not any(row.get("detector") == "current_check" for row in payloads)
        forge = [row for row in payloads if row["kind"] == "forge"]
        assert len(forge) == 1
        assert forge[0]["graded_sha"] == repository[4]
        assert forge[0]["report"]["claim"]["head_sha"] == repository[4]
        assert all("another/native-criterion" not in row.body for row in records)
    assert not workspace._workspaces
    assert server.issues[ROOT].status == "In Review"


async def test_instructed_refutation_records_verified_escalation_before_claim(
    native_audit,
):
    audit, executor, server, _tracker, _git, workspace, _repository = native_audit
    executor.claim_verdict = "refuted"
    executor.instruction = True
    assert await audit.run(FIXTURE_NOW) is PassRun.RAN
    report = audit.last_report
    scope = report.scopes[0]
    assert scope.status == "complete", report.model_dump_json()
    escalations = [
        row
        for row in server.comments
        if row.body.startswith("[native-audit-escalation:")
    ]
    assert len(escalations) == 1
    assert "returned to unstarted on the demonstrated refutation" in escalations[0].body
    assert "needs-decision" in server.issues[CHILD].labels
    assert scope.writes[0].artifact.native_ref == escalations[0].id
    assert scope.writes[1].artifact.surface.kind.value == "criterion_sub_issue"
    assert escalations[0].id in scope.writes[2].artifact.content
    assert all(row.verdict.value == "holds" for row in scope.writes)

    # The refutation is published, then the criterion goes back, once.
    refutation = next(
        row
        for row in server.comments
        if row.issue_id == CHILD and row.body.startswith("[native-audit:")
    )
    assert state_writes(server) == [{"id": CHILD, "state": unstarted_state(server)}]
    assert landed(server, "save_comment", body=refutation.body) < landed(
        server, "save_issue", id=CHILD, state=unstarted_state(server)
    )
    assert server.issues[CHILD].status == "Todo"
    assert server.issues[CHILD].status_type == "unstarted"
    assert scope.writes[-1].artifact.surface.kind.value == "criterion_sub_issue"
    assert scope.writes[-1].artifact.surface.ref.key == CHILD
    assert scope.writes[-1].verdict.value == "holds"

    # A reopened refutation is resolved coverage, so the scope reports it.
    summaries = [
        row
        for row in server.comments
        if row.issue_id == APPROVED_ISSUE and row.body.startswith("[native-audit:")
    ]
    assert len(summaries) == 1
    assert (
        refutation.id in json.loads(summaries[0].body.partition("\n")[2])["record_refs"]
    )
    assert server.issues[ROOT].status == "In Review"

    first = escalations[0].id
    sessions = len(executor.calls)
    claims = len(
        [
            call
            for call in executor.calls
            if call["output_format"]["schema"] == AUDIT_CLAIM_SCHEMA
        ]
    )
    assert await audit.run(FIXTURE_NOW + timedelta(seconds=60)) is PassRun.RAN
    second = audit.last_report.scopes[0]
    assert second.status == "complete", audit.last_report.model_dump_json()
    assert [(row.subject.key, row.reason.value) for row in second.deferred] == [
        (CHILD, "claim_not_made")
    ]
    assert [
        row.id
        for row in server.comments
        if row.body.startswith("[native-audit-escalation:")
    ] == [first]
    assert len(state_writes(server)) == 1
    assert (
        len(
            [
                call
                for call in executor.calls
                if call["output_format"]["schema"] == AUDIT_CLAIM_SCHEMA
            ]
        )
        == claims
    )
    assert len(executor.calls) > sessions  # the second tick's own summary judge
    assert not workspace._workspaces


#: Two exact sentences of the parent body, so a sweep can quote either one.
#: The hunt refuses a quotation that is not exact source text, so the prose
#: a fixture varies has to be prose the audited surface actually holds.
EARLIER_WORDING = "Explicit parent instructions."
LATER_WORDING = "The same instruction, restated at greater length."


def escalation_comments(server):
    """Every escalation object on the board, newest read included."""
    return [
        row
        for row in server.comments
        if row.body.startswith("[native-audit-escalation:")
    ]


def escalation_record(server):
    """The one escalation object's own decoded body."""
    rows = escalation_comments(server)
    assert len(rows) == 1, [row.id for row in rows]
    return rows[0].id, json.loads(rows[0].body.partition("\n")[2])


async def test_two_unchanged_sweeps_hold_one_escalation_for_a_reworded_mandate(
    native_audit,
):
    """One criterion, one escalation object, two wordings of one instruction.

    The window is genuinely unchanged, which is the whole point: the
    criterion is still ``Done`` at the second tick, so the second tick
    audits it again instead of deferring it. The assertion that shows that
    is the per-tick session count, ``len(executor.calls) == spent``,
    fourteen then twenty-eight: a tick that deferred its criterion
    dispatches no session for it, so the running total would not move.
    ``scope.deferred == ()`` is a guard that the tick was not deferred and
    is not the proof of it — a changed window reds an earlier assertion
    every time and never reaches that line. A sweep that deferred the
    criterion would leave one escalation for a reason that has nothing to
    do with its identity.

    The refutation is the over-claim arm, because only a refuted
    current-Check claim takes a criterion back: an over-claim refutation
    carries the instructed mandate the escalation is raised for and moves
    no state, so the window at the second tick is byte-identical to the
    first. Its own unresolved workflow-state authority ends each tick
    incomplete, which is the refusal that arm has always had.

    The limitation that leaves, stated rather than resolved here: both
    ticks end in ``AuditRunIncompleteError``, so this fixture shows two
    sweeps over an unchanged window holding one escalation object, while
    ``test_instructed_refutation_records_verified_escalation_before_claim``
    shows a run that completes — over a window that moved. "Unchanged
    window" and "the run completes" are demonstrated by two fixtures and by
    no single one, and stay that way while the over-claim arm's
    workflow-state authority is unresolved.
    """
    audit, executor, server, _tracker, _git, workspace, _repository = native_audit
    assert EARLIER_WORDING != LATER_WORDING
    server.issues[ROOT].description = f"{EARLIER_WORDING} {LATER_WORDING}"
    executor.overclaim_verdict = "refuted"
    executor.instruction = True
    criterion_body_before = server.issues[CHILD].description
    assert server.issues[CHILD].status == "Done"

    # Two ticks, and only two: each spends the same fourteen sessions, so a
    # tick that quietly deferred its criterion would spend fewer. This
    # running total is the fixture's evidence that the second tick swept.
    ticks = (
        (FIXTURE_NOW, EARLIER_WORDING, 14),
        (FIXTURE_NOW + timedelta(seconds=60), LATER_WORDING, 28),
    )
    raised = []
    for started_at, wording, spent in ticks:
        executor.mandate_text = wording
        with pytest.raises(AuditRunIncompleteError) as incomplete:
            await audit.run(started_at)
        scope = incomplete.value.report.scopes[0]
        assert [row.reason for row in scope.unavailable] == [
            "AuditClaimReadError: the refutation is published; its "
            "workflow-state authority remains unresolved"
        ]
        # A guard that the tick was not deferred, kept for what it rules
        # out rather than as the proof: nothing reaches it first.
        assert scope.deferred == ()
        # The proof the criterion really was swept again: the sessions were
        # spent a second time, on the same count as the first tick.
        assert len(executor.calls) == spent
        # The window did not move: state, Evidence row and body all stand.
        assert server.issues[CHILD].status == "Done"
        assert server.issues[CHILD].description == criterion_body_before
        assert state_writes(server) == []
        comment_id, record = escalation_record(server)
        raised.append((comment_id, record["question"]))

    # One object across both ticks, keyed on the criterion sub-issue key and
    # nothing else: no digest segment, no defect class, nothing appended.
    assert [row[0] for row in raised] == [raised[0][0]] * 2
    _, record = escalation_record(server)
    assert record["escalationKey"] == CHILD
    assert ":mandate:" not in record["escalationKey"]
    # The second tick did re-raise, with the other wording, and the one
    # object carries it — so the wording moved and the identity did not.
    assert EARLIER_WORDING in raised[0][1]
    assert LATER_WORDING in raised[1][1]
    assert raised[0][1] != raised[1][1]
    assert not workspace._workspaces


async def test_the_composed_sweep_traces_a_criterion_restamp_to_its_lanes_gradings(
    native_audit,
):
    """The forged restamp is reachable from composition, not only from a unit.

    The lane's stream holds one grading of CHILD, at the commit before the one
    its Evidence row names, and nothing at that row's own commit — which is
    what a lane leaves for a row moved forward by something other than its
    own cross-off, since every cross-off records its grading (KOD-506). So the
    trace refuses and says why. It is an observation and not a publication:
    the refusal moves no state, writes no comment of its own and leaves the
    scope complete.
    """
    audit, _executor, server, tracker, _git, workspace, repository = native_audit
    _remote, _author, _observer, prior, head = repository
    await tracker.post_run_event(
        issue_key=ROOT,
        event=LaneRunEvent(
            kind=RunEventKind.CRITERION_REFUTED,
            lane_key=LANE_KEY,
            subject_key=CHILD,
            graded_sha=prior,
        ),
    )
    comments_before = len(server.comments)

    assert await audit.run(FIXTURE_NOW) is PassRun.RAN
    scope = audit.last_report.scopes[0]
    assert scope.status == "complete", audit.last_report.model_dump_json()

    traces = [
        row for row in scope.raw_observations if isinstance(row, AuditRestampTrace)
    ]
    assert len(traces) == 1, [type(row).__name__ for row in scope.raw_observations]
    assert traces[0].criterion_key == CHILD
    assert traces[0].recorded_evidence.graded_sha == head
    assert traces[0].history == (prior,)
    assert traces[0].verdict is AuditVerdict.REFUTED

    # An observation, not a publication: no state move and no extra comment
    # beyond the records this pass already published.
    assert state_writes(server) == []
    assert server.issues[CHILD].status == "Done"
    assert len(server.comments) - comments_before == len(scope.writes)
    assert not workspace._workspaces


async def test_the_composed_sweep_holds_a_restamp_its_lane_recorded_a_pass_for(
    native_audit,
):
    """The ordinary lifecycle, seeded as a lane's own writes leave it.

    Refuted at the earlier commit, then passed at the one the Evidence row
    names: two entries, the last of them at the row's commit, so the trace
    holds. Before a passing cross-off recorded its grading this state was
    unreachable — the stream ended at the refutation and the row had moved on
    — and the case above was indistinguishable from it (KOD-506).
    """
    audit, _executor, server, tracker, _git, workspace, repository = native_audit
    _remote, _author, _observer, prior, head = repository
    for kind, graded_sha in (
        (RunEventKind.CRITERION_REFUTED, prior),
        (RunEventKind.CRITERION_PASSED, head),
    ):
        await tracker.post_run_event(
            issue_key=ROOT,
            event=LaneRunEvent(
                kind=kind,
                lane_key=LANE_KEY,
                subject_key=CHILD,
                graded_sha=graded_sha,
            ),
        )

    assert await audit.run(FIXTURE_NOW) is PassRun.RAN
    scope = audit.last_report.scopes[0]
    assert scope.status == "complete", audit.last_report.model_dump_json()

    traces = [
        row for row in scope.raw_observations if isinstance(row, AuditRestampTrace)
    ]
    assert len(traces) == 1, [type(row).__name__ for row in scope.raw_observations]
    assert traces[0].criterion_key == CHILD
    assert traces[0].recorded_evidence.graded_sha == head
    assert traces[0].history == (prior, head)
    assert traces[0].verdict is AuditVerdict.HOLDS
    assert state_writes(server) == []
    assert not workspace._workspaces


#: A trunk kept apart from the lane's branch, so the lane's branch can go.
TRUNK = "trunk-name"


async def missing_branch(repository, server, tmp_path):
    """The composed audit after the lane's branch was deleted from the remote.

    The trunk is one commit past the lane's head and leaves check.txt as the
    head has it, so a write-back judged at the trunk head reads the same
    contents and the trunk head is still a commit no report names.  Every
    criterion is Done and the parent is under review, so the recorded branch
    being gone is the demonstrated defect.
    """
    from tests.tracker.test_audit_evidence_git import command

    _remote, author, *_ = repository
    (author / "trunk.txt").write_text("trunk only\n")
    command(author, "add", "--all")
    command(author, "commit", "-qm", "trunk")
    trunk_head = command(author, "rev-parse", "HEAD")
    command(author, "push", "-q", "configured-remote", f"HEAD:refs/heads/{TRUNK}")
    built = await build_native_audit(
        repository, server, tmp_path, gate=PassThroughGate(), trunk=TRUNK
    )
    command(author, "push", "-q", "configured-remote", "--delete", "ordinary-name")
    return built, trunk_head


def terminal_records(server):
    """The published terminal records on the audited parent, decoded."""
    return [
        (row, json.loads(row.body.partition("\n")[2])["publication"])
        for row in server.comments
        if row.issue_id == ROOT
        and row.body.startswith("[native-audit:")
        and json.loads(row.body.partition("\n")[2])["publication"]["kind"] == "terminal"
    ]


def judged_at(executor, native_ref):
    """The refs every write-back round over one landed artifact was judged at."""
    return [
        NativeExecutor.tagged(call["prompt"], "base_ref")
        for call in executor.calls
        if call["output_format"]["schema"] == WRITE_BACK_SCHEMA
        and json.loads(NativeExecutor.tagged(call["prompt"], "written_artifact"))[
            "nativeRef"
        ]
        == native_ref
    ]


@pytest.mark.parametrize("outcome", ["holds", "refuted"])
async def test_a_missing_branch_refutation_is_published_leaving_the_mandate_unedited(
    repository, server, tmp_path, outcome
):
    """A gone branch is published as a refutation, judged at the trunk head.

    The report names no head, so the write-back that lands it is judged at
    the remote trunk head, the read the scope's summary is judged at.  The
    hunt read the parent's instructions and edited none of them: no
    description of any issue was written.  The tick itself ends incomplete,
    because the criterion's own claim needs the branch that is gone.
    """
    built, trunk_head = await missing_branch(repository, server, tmp_path)
    audit, executor, server, *_ = built
    executor.instruction = outcome == "holds"
    bodies = {key: issue.description for key, issue in server.issues.items()}

    with pytest.raises(AuditRunIncompleteError):
        await audit.run(FIXTURE_NOW)

    ((record, publication),) = terminal_records(server)
    observation = publication["report"]["observation"]
    assert observation["branch_head"] is None
    assert "no_branch" in observation["discrepancies"]
    assert publication["report"]["mandate"]["verdict"] == outcome
    assert set(judged_at(executor, record.id)) == {trunk_head}
    hunts = [
        call
        for call in executor.calls
        if call["output_format"]["schema"] == AUDIT_MANDATE_SCHEMA
    ]
    assert [NativeExecutor.tagged(call["prompt"], "head_sha") for call in hunts] == [""]
    escalations = escalation_comments(server)
    assert len(escalations) == (1 if outcome == "holds" else 0)
    for row in escalations:
        assert row.issue_id == ROOT
        assert set(judged_at(executor, row.id)) == {trunk_head}
        assert json.loads(row.body.partition("\n")[2])["raisedAtSha"] == trunk_head
    assert not [
        arguments
        for name, arguments in server.calls
        if name == "save_issue" and "description" in arguments
    ]
    assert {key: issue.description for key, issue in server.issues.items()} == bodies


async def test_a_refutation_whose_mandate_hunt_fails_leaves_the_audit_run_incomplete(
    repository, server, tmp_path
):
    """A hunt that fails leaves the refutation raw, named and unpublished.

    The sweep keeps the raw REFUTED terminal beside the reason its hunt
    could not run, and the runtime refuses the subject on that reason, so
    the tick ends incomplete rather than reporting coverage it lacks.
    """
    from kodezart.domain.errors import AgentSDKError
    from kodezart.types.domain.audit_terminal import AuditTerminalObservation

    built, _trunk_head = await missing_branch(repository, server, tmp_path)
    audit, executor, server, *_ = built

    async def during(kwargs):
        if kwargs["output_format"]["schema"] == AUDIT_MANDATE_SCHEMA:
            raise AgentSDKError("mandate session unavailable", error_kind="fixture")

    executor.during = during
    with pytest.raises(AuditRunIncompleteError) as incomplete:
        await audit.run(FIXTURE_NOW)

    scope = incomplete.value.report.scopes[0]
    assert any(
        row.subject.key == ROOT and "mandate session unavailable" in row.reason
        for row in scope.unavailable
    ), [row.model_dump() for row in scope.unavailable]
    raw = [
        row
        for row in scope.raw_observations
        if isinstance(row, AuditTerminalObservation)
    ]
    assert [row.verdict for row in raw] == [AuditVerdict.REFUTED]
    assert raw[0].branch_head is None
    assert terminal_records(server) == []


@pytest.mark.parametrize("arm", ["restamp", "forge"])
async def test_a_lapse_whose_mandate_hunt_fails_leaves_the_audit_run_incomplete(
    repository, server, tmp_path, arm
):
    """A lapsed criterion whose refutation lost its hunt is refused, not deferred.

    CHILD's grading is behind the head, which alone defers it.  Here it also
    carries a refutation, the restamp trace or the forge reading at its
    graded commit, whose mandate hunt failed, so the refutation stands with
    no mandate verdict.  The runtime refuses CHILD on that reason and the
    tick ends incomplete, with no deferral recorded for it.
    """
    from kodezart.domain.errors import AgentSDKError
    from kodezart.types.domain.audit_forge import AuditForgeObservation

    _remote, _author, _observer, prior, head = repository
    red = FakeCIMonitor(
        passed=False,
        failed_names=frozenset({"test"}),
        observed_sha_by_ref={head: head, prior: prior},
        check_names=frozenset({"test"}),
    )
    audit, executor, server, tracker, *_ = await build_native_audit(
        repository,
        server,
        tmp_path,
        gate=PassThroughGate(),
        ci=red if arm == "forge" else None,
    )
    server.issues[CHILD].description = criterion_body(
        check=BASE_CHECK, graded_sha=prior
    )
    if arm == "restamp":
        await tracker.post_run_event(
            issue_key=ROOT,
            event=LaneRunEvent(
                kind=RunEventKind.CRITERION_REFUTED,
                lane_key=LANE_KEY,
                subject_key=CHILD,
                graded_sha=head,
            ),
        )
    session = executor.stream

    async def stream(**kwargs):
        if kwargs["output_format"]["schema"] == AUDIT_MANDATE_SCHEMA:
            raise AgentSDKError(
                "lapse mandate session unavailable", error_kind="fixture"
            )
        async for event in session(**kwargs):
            yield event

    executor.stream = stream
    with pytest.raises(AuditRunIncompleteError) as incomplete:
        await audit.run(FIXTURE_NOW)

    scope = incomplete.value.report.scopes[0]
    assert [
        row.subject.key
        for row in scope.unavailable
        if "lapse mandate session unavailable" in row.reason
    ] == [CHILD], [row.model_dump() for row in scope.unavailable]
    assert CHILD not in {row.subject.key for row in scope.deferred}
    raw = AuditRestampTrace if arm == "restamp" else AuditForgeObservation
    assert [row.verdict for row in scope.raw_observations if isinstance(row, raw)] == [
        AuditVerdict.REFUTED
    ]


async def test_executor_programming_failure_escapes_the_native_owner(native_audit):
    audit, executor, server, _tracker, _git, workspace, _repository = native_audit
    error = RuntimeError("executor implementation defect")

    async def during(kwargs):
        if kwargs["output_format"]["schema"] != WRITE_BACK_SCHEMA:
            raise error

    executor.during = during
    with pytest.raises(RuntimeError) as raised:
        await audit.run(FIXTURE_NOW)
    assert raised.value is error
    assert not any(row.body.startswith("[native-audit:") for row in server.comments)
    assert not workspace._workspaces


async def test_repair_refusal_retains_the_actual_prior_writeback_finding(native_audit):
    audit, executor, _server, _tracker, _git, _workspace, _repository = native_audit
    executor.refuse_after_first_refutation = True
    with pytest.raises(AuditRunIncompleteError) as raised:
        await audit.run(FIXTURE_NOW)
    scope = raised.value.report.scopes[0]
    assert any(
        "check.txt" in entry.finding.cited_refs for entry in scope.repair_inputs
    ), raised.value.report.model_dump_json()
    assert any(
        row.kind == "claim"
        and row.report.claim.judgment.verdict.value == "unverifiable"
        for row in scope.observations
    )


async def test_declared_executor_outage_retains_unavailable_and_other_observations(
    native_audit,
):
    from kodezart.domain.errors import AgentSDKError

    audit, executor, server, _tracker, _git, workspace, _repository = native_audit

    async def during(kwargs):
        if kwargs["output_format"]["schema"] == AUDIT_CLAIM_SCHEMA:
            raise AgentSDKError(
                "actual provider unavailable", error_kind="fixture-provider"
            )

    executor.during = during
    with pytest.raises(AuditRunIncompleteError) as raised:
        await audit.run(FIXTURE_NOW)
    scope = raised.value.report.scopes[0]
    assert any(
        "actual provider unavailable" in reason.reason for reason in scope.unavailable
    )
    assert any(row.kind == "overclaim" for row in scope.observations)
    assert not any('"detector":"current_check"' in row.body for row in server.comments)
    assert not workspace._workspaces
