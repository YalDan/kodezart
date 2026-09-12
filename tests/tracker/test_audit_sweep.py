"""The native sweep reaches real verifiers and fresh AgentService dispatch."""

import asyncio
from dataclasses import fields
from inspect import signature

import pytest

from kodezart.chains.audit_detection_removal import DetectorRemovalVerifier
from kodezart.chains.audit_evidence import AuditEvidenceVerifier
from kodezart.chains.audit_overclaim import AuditOverclaimVerifier
from kodezart.chains.audit_pass import AuditClaimVerifier, AuditMandateHunt
from kodezart.chains.audit_sweep import AuditReadSweep
from kodezart.core.config import AppConfig
from kodezart.core.constants import EVAL_PERMISSION_MODE
from kodezart.domain.criterion_evidence import render_evidence_field
from kodezart.domain.errors import AuditClaimReadError
from kodezart.services.agent_service import AgentService
from kodezart.services.audit_sessions import FreshAuditSession
from kodezart.services.audit_sources import AuditSourceReader
from kodezart.services.audit_terminal import AuditTerminalReader
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.agent import (
    AUDIT_CLAIM_SCHEMA,
    AUDIT_MANDATE_SCHEMA,
    AUDIT_OVERCLAIM_SCHEMA,
    DETECTOR_REMOVAL_SCHEMA,
)
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.pr_state import PRLifecycle, PRState
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import ToolPreset
from kodezart.types.domain.surface import SurfaceKind
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeGitService,
    FakePRStateReader,
    FakeRepoCache,
    FakeTrackerPort,
    FakeWorkspaceProvider,
)
from tests.prompts.test_prompt_wiring import load_registry
from tests.tracker.conftest import WORKFLOW_STATE_NAMES
from tests.tracker.test_audit_claim import result_event
from tests.tracker.test_audit_evidence import Source
from tests.tracker.test_audit_requests import (
    CHILD,
    REPO,
    ROOT,
    SCOPE,
    lane_record,
    operation,
)
from tests.tracker.test_audit_requests import server as server

HEAD = "a" * 40
PRIOR = "b" * 40
CHECK = "The repository check reads the current committed contents."
BODY = f"**Check:** {CHECK}\n**Do:** AUTHOR_REASONING\n" + render_evidence_field(
    CriterionEvidence(graded_sha=HEAD, test="OLD_RECORDED_TEST")
)


class Executor(FakeAgentExecutor):
    def __init__(self):
        super().__init__([])
        self.verdict = "holds"
        self.during = None
        self.overclaim_output = None
        self.removal_output = None
        self.mandate_output = {
            "verdict": "refuted",
            "finding": None,
            "source_index": None,
            "evidence": "No instruction mandates this defect in the supplied body set.",
        }

    def _is_criteria_validation_schema(self, output_format):
        if output_format and output_format["schema"] == DETECTOR_REMOVAL_SCHEMA:
            # The broad fake's findings heuristic otherwise fabricates authored
            # feasibility output instead of forwarding this scripted detector.
            return False
        return super()._is_criteria_validation_schema(output_format)

    async def stream(self, **kwargs):
        if self.during:
            await self.during(kwargs)
        if kwargs["output_format"]["schema"] == AUDIT_CLAIM_SCHEMA:
            payload = {
                "criterionKey": CHILD,
                "verdict": self.verdict,
                "evidence": "Measured current repository contents.",
            }
        elif kwargs["output_format"]["schema"] == AUDIT_OVERCLAIM_SCHEMA:
            payload = self.overclaim_output
        elif kwargs["output_format"]["schema"] == DETECTOR_REMOVAL_SCHEMA:
            payload = self.removal_output
        else:
            assert kwargs["output_format"]["schema"] == AUDIT_MANDATE_SCHEMA
            payload = self.mandate_output
        self._events = [result_event(subtype="success", structured_output=payload)]
        async for event in super().stream(**kwargs):
            yield event


async def state(tracker, server, key, name, kind):
    if isinstance(tracker, FakeTrackerPort):
        tracker.issues[key] = tracker.issues[key].model_copy(
            update={"state_name": name, "state_kind": kind}
        )
    else:
        server.issues[key].status = name
        server.issues[key].status_type = kind.value


@pytest.fixture
async def setup(tracker, server):
    await tracker.update_issue(issue_key=CHILD, body=BODY)
    await tracker.update_issue(issue_key=ROOT, body="Explicit parent instructions.")
    await state(tracker, server, CHILD, "Todo", WorkflowStateKind.UNSTARTED)
    stored = await lane_record(
        tracker, data={"pr": {"number": 7, "url": f"{REPO}/pull/7", "state": "old"}}
    )
    config = AppConfig(git={"remote": "configured-remote"})
    op = operation().model_copy(update={"workflow_states": WORKFLOW_STATE_NAMES})
    executor = Executor()
    git = FakeGitService(remote_branch_shas={"ordinary-name": HEAD})
    git._ancestor_pairs.update({(HEAD, HEAD), (PRIOR, HEAD)})
    cache = FakeRepoCache()
    workspace = FakeWorkspaceProvider()
    forge = FakePRStateReader(
        records={
            (REPO, 7): PRState(
                url=f"{REPO}/pull/7",
                number=7,
                head_repo_url=REPO,
                base_repo_url=REPO,
                base_branch="main",
                head_branch="ordinary-name",
                head_sha=HEAD,
                lifecycle=PRLifecycle.OPEN,
            )
        }
    )

    def build(
        *,
        scope=SCOPE,
        selected_op=op,
        selected_git=git,
        selected_cache=cache,
        selected_workspace=workspace,
        selected_source=None,
        include_overclaims=False,
        include_removals=False,
        selected_forge=None,
    ):
        runner = AgentService(
            executor=executor, workspace=selected_workspace, git_base_url=REPO
        )
        records = LaneRecordReader(tracker=tracker, operation=selected_op)
        prompts = load_registry()
        claims = AuditClaimVerifier(
            tracker=tracker,
            records=records,
            git=selected_git,
            cache=selected_cache,
            workspace=selected_workspace,
            runner=runner,
            prompts=prompts,
            skills=SUPPRESS_ALL_SKILLS,
            remote=config.git.remote,
        )
        evidence = AuditEvidenceVerifier(
            tracker=tracker,
            records=records,
            git=selected_git,
            cache=selected_cache,
            source=selected_source or Source(),
            claims=claims,
            operation=selected_op,
            remote=config.git.remote,
        )
        mandates = AuditMandateHunt(
            tracker=tracker,
            runner=runner,
            workspace=selected_workspace,
            git=selected_git,
            prompts=prompts,
            skills=SUPPRESS_ALL_SKILLS,
        )
        terminals = AuditTerminalReader(
            tracker=tracker,
            records=records,
            forge=forge,
            git=selected_git,
            cache=selected_cache,
            operation=selected_op,
            remote=config.git.remote,
        )
        overclaims = (
            AuditOverclaimVerifier(
                sources=AuditSourceReader(
                    tracker=tracker,
                    records=records,
                    git=selected_git,
                    source=selected_source or Source(),
                    cache=selected_cache,
                    operation=selected_op,
                    remote=config.git.remote,
                ),
                sessions=FreshAuditSession(
                    git=selected_git,
                    workspace=selected_workspace,
                    runner=runner,
                    prompts=prompts,
                    skills=SUPPRESS_ALL_SKILLS,
                ),
                prompts=prompts,
                git=selected_source or Source(),
            )
            if include_overclaims
            else None
        )
        removals = (
            DetectorRemovalVerifier(
                sources=AuditSourceReader(
                    tracker=tracker,
                    records=records,
                    git=selected_git,
                    source=selected_source or Source(),
                    cache=selected_cache,
                    operation=selected_op,
                    remote=config.git.remote,
                ),
                sessions=FreshAuditSession(
                    git=selected_git,
                    workspace=selected_workspace,
                    runner=runner,
                    prompts=prompts,
                    skills=SUPPRESS_ALL_SKILLS,
                ),
                prompts=prompts,
                git=selected_source or Source(),
            )
            if include_removals
            else None
        )
        return AuditReadSweep(
            scope=scope,
            tracker=tracker,
            operation=selected_op,
            claims=claims,
            evidence=evidence,
            mandates=mandates,
            terminals=terminals,
            git=selected_git,
            cache=selected_cache,
            remote=config.git.remote,
            overclaims=overclaims,
            removals=removals,
            forge=selected_forge,
        )

    return build, executor, git, cache, workspace, forge, stored, op


@pytest.mark.parametrize("kind", list(WorkflowStateKind))
async def test_every_state_reaches_actual_fresh_claim_dispatch(
    setup, tracker, server, tracker_writes, kind
):
    build, executor, git, _, workspace, *_ = setup
    await state(
        tracker,
        server,
        CHILD,
        "Done" if kind is WorkflowStateKind.COMPLETED else "other",
        kind,
    )
    before = tracker_writes()
    sweep = build()
    assert not signature(sweep.run).parameters
    result = await sweep.run()
    assert [item.target.issue.issue_key for item in result.observations] == [
        CHILD,
        ROOT,
    ]
    child, parent = result.observations
    assert child.claim.claim.judgment.verdict is AuditVerdict.HOLDS
    assert child.claim.mandate is None and child.unavailable_reason is None
    assert child.claim.claim.record_ref == child.target.source.comment.comment_key
    assert child.claim.claim.check == CHECK
    assert parent.unavailable_reason and parent.terminal is None
    assert {item.ref.key for item in result.audited_surfaces} == {CHILD, ROOT}
    assert all(
        item.kind is SurfaceKind.ISSUE_DESCRIPTION for item in result.audited_surfaces
    )
    (call,) = executor.calls
    assert call["session_id"] is None
    assert call["permission_mode"] == EVAL_PERMISSION_MODE
    assert call["allowed_tools"] == ToolPreset.EVALUATION
    assert CHECK in call["prompt"] and HEAD in call["prompt"]
    assert (
        "AUTHOR_REASONING" not in call["prompt"]
        and "OLD_RECORDED_TEST" not in call["prompt"]
    )
    assert workspace.calls[-1][0] == "release"
    assert {call[2] for call in git.calls if call[0] == "remote_branch_sha"} == {
        "configured-remote"
    }
    assert tracker_writes() == before
    assert not {"covered", "complete", "coverage"} & {
        field.name for field in fields(result)
    }
    await sweep.run()
    assert len(executor.calls) == 2  # a partial pass never suppresses unchanged work


@pytest.mark.parametrize("mode", ["lapse", "review"])
async def test_recorded_grading_uses_existing_lapse_and_review_arms(
    setup, tracker, server, mode
):
    build, executor, *_ = setup
    await tracker.update_issue(issue_key=CHILD, body=BODY.replace(HEAD, PRIOR))
    await state(
        tracker,
        server,
        CHILD,
        "Done" if mode == "lapse" else "In Review",
        WorkflowStateKind.COMPLETED if mode == "lapse" else WorkflowStateKind.STARTED,
    )
    observation = (await build().run()).observations[0]
    assert observation.evidence.recorded_evidence.graded_sha == PRIOR
    assert observation.evidence.is_lapse is (mode == "lapse")
    assert len(executor.calls) == (0 if mode == "lapse" else 1)
    if mode == "lapse":
        assert observation.claim is None
        assert observation.evidence.verdict is AuditVerdict.UNVERIFIABLE
    else:
        assert observation.claim.claim == observation.evidence.current_claim


@pytest.mark.parametrize("verdict", ["holds", "refuted"])
async def test_refutation_completes_actual_mandate_hunt(
    setup, tracker, tracker_writes, verdict
):
    build, executor, *_ = setup
    executor.verdict = "refuted"
    if verdict == "holds":
        quote = "Explicit parent instructions."
        executor.mandate_output = {
            "verdict": "holds",
            "source_index": 1,
            "evidence": "Exact instruction.",
            "finding": {
                "issue_id": ROOT,
                "defect_class": f"violation of the current Check: {CHECK}",
                "role": "mandate",
                "mandate_text": quote,
                "evidence": "Source mandates the defect.",
            },
        }
    before = tracker_writes()
    result = await build().run()
    report = result.observations[0].claim
    assert report.claim.judgment.verdict is AuditVerdict.REFUTED
    assert report.mandate.verdict.value == verdict
    assert {artifact.surface.ref.key for artifact in report.mandate.covered} == {
        CHILD,
        ROOT,
    }
    assert len(executor.calls) == 2 and all(
        call["session_id"] is None for call in executor.calls
    )
    assert tracker_writes() == before


@pytest.mark.parametrize("mode", ["healthy", "refuted", "bad-check", "bad-evidence"])
async def test_independent_terminal_read_survives_failed_criterion(
    setup, tracker, server, mode
):
    build, executor, _, _, _, forge, *_ = setup
    await state(tracker, server, ROOT, "In Review", WorkflowStateKind.STARTED)
    await state(tracker, server, CHILD, "Done", WorkflowStateKind.COMPLETED)
    if mode == "bad-check":
        await tracker.update_issue(issue_key=CHILD, body="**Evidence:** unreadable")
    elif mode == "bad-evidence":
        await tracker.update_issue(
            issue_key=CHILD, body=f"**Check:** {CHECK}\n**Evidence:** legacy only"
        )
    elif mode == "refuted":
        forge.records[(REPO, 7)] = forge.records[(REPO, 7)].model_copy(
            update={"lifecycle": PRLifecycle.CLOSED}
        )
    child, parent = (await build().run()).observations
    if mode.startswith("bad"):
        assert child.unavailable_reason and child.claim is child.evidence is None
        assert (
            not executor.calls
        )  # unreadable Evidence never falls back to an optimistic fresh arm
    if mode == "refuted":
        assert parent.terminal.verdict is AuditVerdict.REFUTED
        assert parent.unavailable_reason is None
        assert parent.terminal_report.observation == parent.terminal
        assert parent.terminal_report.mandate.verdict is AuditVerdict.REFUTED
    else:
        assert (
            parent.terminal.verdict is AuditVerdict.HOLDS
            and parent.unavailable_reason is None
        )


async def test_criterion_only_scope_keeps_native_parent_in_mandate_set(setup):
    build, executor, *_ = setup
    executor.verdict = "refuted"
    result = await build(scope=ScopeRef(kind=ScopeKind.ISSUE, key=CHILD)).run()
    assert len(result.observations) == 1
    assert {surface.ref.key for surface in result.audited_surfaces} == {CHILD, ROOT}
    assert result.observations[0].claim.mandate is not None


@pytest.mark.parametrize("change", ["head", "body"])
async def test_change_after_claim_during_mandate_refuses_whole_read_result(
    setup, tracker, change
):
    build, executor, git, *_ = setup
    executor.verdict = "refuted"

    async def during(kwargs):
        if kwargs["output_format"]["schema"] == AUDIT_MANDATE_SCHEMA:
            if change == "head":
                git._remote_branch_shas["ordinary-name"] = "c" * 40
            else:
                await tracker.update_issue(issue_key=CHILD, body=BODY + "\nchanged")

    executor.during = during
    with pytest.raises(AuditClaimReadError, match="changed"):
        await build().run()


async def test_cancellation_in_actual_session_propagates_after_release(setup):
    build, executor, _, _, workspace, *_ = setup

    async def cancel(_kwargs):
        raise asyncio.CancelledError

    executor.during = cancel
    with pytest.raises(asyncio.CancelledError):
        await build().run()
    assert workspace.calls[-1][0] == "release"
