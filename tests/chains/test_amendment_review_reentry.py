"""The actual reviewed native graph keeps a pinned refusal ahead of persistence."""

import json

import pytest

from kodezart.domain.amendment import NativeAmendmentRefusalError
from kodezart.domain.criteria_grading import grade_iteration
from kodezart.domain.rulings import render_ruling
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import (
    AcceptanceCriteriaOutput,
    NativeAmendmentEvent,
    Ruling,
    WorkflowIterationEvent,
    WorkflowRemediationEvent,
)
from kodezart.types.domain.amendment_write import AmendmentRecord
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode, ToolPreset
from tests.chains.test_native_amendment_runtime import make_runtime
from tests.chains.test_native_fire import SUBJECT, native_evaluation
from tests.domain.test_rulings import ruling_data
from tests.services.test_native_amendments import Executor, cleanup, git, repository

__all__ = ["repository"]


@pytest.mark.parametrize("gate_green", [False, True])
async def test_review_fix_upheld_keeps_branch_head_and_records_blocking_ruling(
    repository, gate_green
):
    ruling = Ruling.model_validate(ruling_data(issue_ref=SUBJECT))
    proposed_grade = native_evaluation(failed=not gate_green)

    async def answers(title, payload, kwargs):
        if title == "RemediationPlan":
            payload.clear()
            payload["instructions"] = (
                "The review's only available fix changes the pinned behavior. "
                "Propose that departure explicitly for independent judgment."
            )
        elif title == "NativeWriterOutput":
            assert "review's only available fix" in kwargs["prompt"]
            assert ruling.resolution in kwargs["prompt"]
        elif title == "AcceptanceCriteriaOutput":
            payload.clear()
            payload.update(proposed_grade)

    executor = Executor(
        subject={"kind": "ruling", "id": ruling.ruling_id}, mutate=answers
    )
    fire, spec, current, _, workspace, _ = await make_runtime(
        repository, executor, max_iterations=1
    )
    port = fire.criteria._tracker
    await port.post_comment(
        issue_key=SUBJECT,
        body=render_ruling(
            ruling=ruling,
            lane_key="original-ruling-lane",
            marker_prefixes={"ruling": "fixture-pinned"},
        ),
    )
    # Both counterfactual gate outcomes are deliberate. The existing evaluator
    # oracle would accept the green variant; it cannot authorize the departure.
    assert (
        grade_iteration(
            current.criteria, AcceptanceCriteriaOutput.model_validate(proposed_grade)
        ).verdict
        is AcceptVerdict.accepted
    ) is gate_green
    await git(repository[0], "branch", "native-feature", "main")
    before = await git(repository[0], "rev-parse", "native-feature")
    state, config = fire.prepare(
        prompt="Implement the exact subject",
        repo_path=str(repository[0]),
        repo_url=None,
        base_spec=trunk_base(repository[1]),
        scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT),
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=ToolPreset.IMPLEMENTATION,
        cache_key="review-reentry-job",
        surface_holder="review-reentry-job",
    )
    state.update(
        fire_spec=spec,
        criterion_set=current,
        feature_branch="native-feature",
        ralph_branch="prior-loop",
        work_base_ref="native-feature",
        accept_verdict=AcceptVerdict.accepted,
        merged=True,
        review_passed=False,
        review_feedback="The only review fix contradicts the existing pinned ruling.",
        repo_visibility=RepoVisibility.PUBLIC,
    )
    await fire.native_graph.aupdate_state(
        config, state, as_node="review_against_ticket"
    )
    assert (await fire.native_graph.aget_state(config)).next == ("remediate",)
    events = []
    try:
        with pytest.raises(NativeAmendmentRefusalError):
            async for event in fire.native_graph.astream(
                None, config=config, stream_mode="custom"
            ):
                events.append(event)
        assert await git(repository[0], "rev-parse", "native-feature") == before
        saved = await fire.native_graph.aget_state(config)
        assert (
            await git(repository[0], "rev-parse", saved.values["ralph_branch"])
            == before
        )
        assert any(isinstance(e, WorkflowRemediationEvent) for e in events)
        assert not any(isinstance(e, WorkflowIterationEvent) for e in events)
        report = next(e.report for e in events if isinstance(e, NativeAmendmentEvent))
        assert report.upheld[0].subject.id == ruling.ruling_id
        records = [c for c in port.comments if c.body.startswith("[fixture-amendment:")]
        assert len(records) == 1
        assert records[0].issue_key == SUBJECT
        recorded = AmendmentRecord.model_validate_json(
            records[0].body.partition("\n")[2]
        )
        assert recorded.disposition == "accepted_and_not_actioned"
        assert recorded.claim.subject.id == ruling.ruling_id
        assert ruling.resolution in recorded.prior.content
        assert (
            json.loads(recorded.model_dump_json())["claim"]["subject"]["id"]
            == ruling.ruling_id
        )
        titles = [c["output_format"]["schema"]["title"] for c in executor.calls]
        assert titles == [
            "RemediationPlan",
            # The round passes the question step again on its way back to the
            # loop, and the board already carries every answer it raises.
            "RulingOutput",
            "NativeWriterOutput",
            "AmendmentJudgment",
            "WriteBackFinding",
        ]
        assert not await git(
            repository[0],
            "ls-remote",
            "origin",
            f"refs/heads/{saved.values['ralph_branch']}",
        )
    finally:
        await cleanup(workspace)
