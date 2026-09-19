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
from kodezart.services.agent_service import AgentService
from kodezart.services.audit_runtime import AuditRunIncompleteError
from kodezart.types.domain.agent import (
    AUDIT_CLAIM_SCHEMA,
    AUDIT_MANDATE_SCHEMA,
    AUDIT_OVERCLAIM_SCHEMA,
    DETECTOR_REMOVAL_SCHEMA,
    WRITE_BACK_SCHEMA,
)
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.pr_state import PRLifecycle, PRState
from tests.chains.test_organize import RecordingExecutor, result
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeCIMonitor,
    FakePRStateReader,
    PassThroughGate,
)
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


class NativeExecutor(RecordingExecutor):
    def __init__(self, head):
        super().__init__([])
        self.head = head
        self.during = None
        self.claim_verdict = "holds"
        self.write_verdict = "holds"
        self.claim_key = CHILD
        self.instruction = False
        self.refuse_after_first_refutation = False
        self.write_calls = 0

    async def stream(self, **kwargs):
        assert kwargs["session_id"] is None
        assert (
            Path(kwargs["cwd"], "check.txt").read_text()
            == "current committed contents\n"
        )
        if self.during is not None:
            await self.during(kwargs)
        schema = kwargs["output_format"]["schema"]
        if schema == AUDIT_CLAIM_SCHEMA:
            payload = {
                "criterionKey": self.claim_key,
                "verdict": self.claim_verdict,
                "evidence": "Read check.txt at the current commit.",
            }
        elif schema == AUDIT_OVERCLAIM_SCHEMA:
            payload = overclaims()
        elif schema == DETECTOR_REMOVAL_SCHEMA:
            payload = {
                "criterionKey": CHILD,
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
                    "source_index": 1,
                    "evidence": (
                        "The exact current parent instruction mandates this defect."
                    ),
                    "finding": {
                        "issue_id": ROOT,
                        "defect_class": (
                            "violation of the current Check: "
                            "The current check.txt contains the committed contents."
                        ),
                        "role": "mandate",
                        "mandate_text": "Explicit parent instructions.",
                        "evidence": (
                            "Read the exact parent body and measured check.txt."
                        ),
                    },
                }
        else:
            assert schema == WRITE_BACK_SCHEMA
            self.write_calls += 1
            if self.refuse_after_first_refutation and self.write_calls == 1:
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


@pytest.fixture
async def native_audit(repository, server, tmp_path):
    remote, _author, _observer, _prior, head = repository
    fields = base_operation(repos=(remote.as_uri(),)).model_dump()
    fields["repos"][0].update(
        trunk="ordinary-name",
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
    fields["audit_scopes"] = [
        {
            "scope": SCOPE.model_dump(),
            "repo_url": remote.as_uri(),
            "report_issue_key": APPROVED_ISSUE,
        }
    ]
    operation = OperationConfig.model_validate(fields)
    server._comment_clock = lambda: FIXTURE_NOW
    tracker = tracker_over(
        server,
        issue_labels=operation.issue_labels,
        marker_prefixes=operation.marker_prefixes,
    )
    server.issues[ROOT].status = "In Review"
    server.issues[ROOT].status_type = "started"
    server.issues[ROOT].description = "Explicit parent instructions."
    server.issues[CHILD].description = (
        "**Check:** The current check.txt contains the committed contents.\n"
        "**Do:** AUTHOR REASONING\n"
        + render_evidence_field(
            CriterionEvidence(graded_sha=head, test="historical author reasoning")
        )
    )
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
    ci = FakeCIMonitor(
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
        gate=PassThroughGate(),
    )
    return audit, executor, server, tracker, git, workspace, repository


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
    with pytest.raises(AuditRunIncompleteError) as raised:
        await audit.run(FIXTURE_NOW)
    report = raised.value.report
    scope = report.scopes[0]
    assert "workflow-state authority" in scope.unavailable[0].reason, (
        report.model_dump_json()
    )
    escalations = [
        row
        for row in server.comments
        if row.body.startswith("[native-audit-escalation:")
    ]
    assert len(escalations) == 1
    assert "needs-decision" in server.issues[CHILD].labels
    assert scope.writes[0].artifact.native_ref == escalations[0].id
    assert scope.writes[1].artifact.surface.kind.value == "criterion_sub_issue"
    assert escalations[0].id in scope.writes[2].artifact.content
    assert all(row.verdict.value == "holds" for row in scope.writes)
    assert not any(
        row.issue_id == APPROVED_ISSUE and row.body.startswith("[native-audit:")
        for row in server.comments
    )
    assert server.issues[ROOT].status == "In Review"
    assert server.issues[CHILD].status == "Done"
    first = escalations[0].id
    with pytest.raises(AuditRunIncompleteError):
        await audit.run(FIXTURE_NOW + timedelta(seconds=60))
    assert [
        row.id
        for row in server.comments
        if row.body.startswith("[native-audit-escalation:")
    ] == [first]
    assert not workspace._workspaces


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
