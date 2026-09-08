"""A native scope sweep actually dispatches each standing detector and mandate."""

import asyncio

import pytest
from pydantic import ValidationError

from kodezart.domain.errors import AuditClaimReadError
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.agent import (
    AUDIT_CLAIM_SCHEMA,
    AUDIT_MANDATE_SCHEMA,
    AUDIT_OVERCLAIM_SCHEMA,
)
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_overclaim import AuditOverclaimReport, OverclaimKind
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.tracker.test_audit_overclaim import payload as original_payload
from tests.tracker.test_audit_sweep import BODY, CHECK, CHILD, HEAD, PRIOR, ROOT, state
from tests.tracker.test_audit_sweep import server as server
from tests.tracker.test_audit_sweep import setup as setup


def payload(kind=None, **changes):
    result = original_payload(kind, **changes)
    result["criterionKey"] = CHILD
    return result


async def completed(tracker, server):
    await state(tracker, server, CHILD, "Done", WorkflowStateKind.COMPLETED)


@pytest.mark.parametrize("kind", list(OverclaimKind))
async def test_actual_native_sweep_preserves_each_reading_and_completes_refutations(
    setup, tracker, server, tracker_writes, kind
):
    build, executor, _, _, workspace, _, stored, *_ = setup
    await completed(tracker, server)
    changes = {
        "verdict": "refuted",
        "evidence": f"Current {kind.value} counterexample.",
    }
    if kind is OverclaimKind.AGGREGATE:
        changes["recomputedValue"] = "3"
    elif kind is OverclaimKind.COMPLETENESS:
        changes.update(verdict="unverifiable", missingArtifact="enumerable roster")
    executor.overclaim_output = payload(kind, **changes)
    before = tracker_writes()
    result = await build(include_overclaims=True).run()
    child, parent = result.observations
    assert child.unavailable_reason is child.overclaim_unavailable_reason is None
    assert child.claim.claim.judgment.verdict is AuditVerdict.HOLDS
    report = child.overclaims
    assert report.observation.head_sha == report.observation.graded_sha == HEAD
    assert report.observation.record_ref == stored.comment_key
    assert report.observation.check == CHECK
    assert report.observation.judgment.criterion_key == CHILD
    assert tuple(row.kind for row in report.reports) == tuple(OverclaimKind)
    changed = next(row for row in report.reports if row.kind is kind)
    assert changed.report.claim.judgment.verdict.value == changes["verdict"]
    assert changed.report.claim.judgment.evidence == changes["evidence"]
    observed = next(
        row for row in report.observation.judgment.checks if row.kind is kind
    )
    assert observed.recomputed_value == changes.get("recomputedValue")
    assert observed.missing_artifact == changes.get("missingArtifact")
    if changes["verdict"] == "refuted":
        assert changed.report.mandate.verdict is AuditVerdict.REFUTED
        assert {item.surface.ref.key for item in changed.report.mandate.covered} == {
            CHILD,
            ROOT,
        }
    else:
        assert changed.report.mandate is None
    schemas = [call["output_format"]["schema"] for call in executor.calls]
    assert schemas == [AUDIT_CLAIM_SCHEMA, AUDIT_OVERCLAIM_SCHEMA] + (
        [AUDIT_MANDATE_SCHEMA] if changes["verdict"] == "refuted" else []
    )
    assert all(call["session_id"] is None for call in executor.calls)
    assert workspace.calls[-1][0] == "release"
    assert parent.overclaims is parent.overclaim_unavailable_reason is None
    assert tracker_writes() == before


async def test_two_refutations_get_two_category_addressed_mandate_hunts(
    setup, tracker, server
):
    build, executor, *_ = setup
    await completed(tracker, server)
    output = payload(OverclaimKind.AGGREGATE, verdict="refuted", recomputedValue="3")
    output["checks"][3]["verdict"] = "refuted"
    executor.overclaim_output = output
    kinds = iter((OverclaimKind.AGGREGATE, OverclaimKind.SELF_RULE))

    async def during(kwargs):
        if kwargs["output_format"]["schema"] != AUDIT_MANDATE_SCHEMA:
            return
        kind = next(kinds)
        defect = f"{kind.value} over-claim: {CHECK}"
        assert defect in kwargs["prompt"]
        executor.mandate_output = {
            "verdict": "holds",
            "source_index": 1,
            "evidence": "Exact instruction.",
            "finding": {
                "issue_id": ROOT,
                "defect_class": defect,
                "role": "mandate",
                "mandate_text": "Explicit parent instructions.",
                "evidence": "Source mandates the defect.",
            },
        }

    executor.during = during
    result = (await build(include_overclaims=True).run()).observations[0]
    assert result.overclaim_unavailable_reason is None
    reports = result.overclaims.reports
    findings = [row.report.mandate.finding for row in reports if row.report.mandate]
    assert [finding.defect_class for finding in findings] == [
        f"{kind.value} over-claim: {CHECK}"
        for kind in (OverclaimKind.AGGREGATE, OverclaimKind.SELF_RULE)
    ]
    assert len(executor.calls) == 4
    data = result.overclaims.model_dump()
    data["reports"][0]["report"]["mandate"]["finding"]["defect_class"] = (
        f"self_rule over-claim: {CHECK}"
    )
    with pytest.raises(ValidationError, match="different over-claim"):
        AuditOverclaimReport.model_validate(data)


async def test_observed_category_order_is_retained_even_when_not_enum_order(
    setup, tracker, server
):
    build, executor, *_ = setup
    await completed(tracker, server)
    output = payload()
    original = output["checks"]
    output["checks"] = [original[index] for index in (3, 1, 0, 2)]
    executor.overclaim_output = output
    child = (await build(include_overclaims=True).run()).observations[0]
    assert child.overclaim_unavailable_reason is None
    assert [entry.kind.value for entry in child.overclaims.reports] == [
        row["kind"] for row in output["checks"]
    ]


@pytest.mark.parametrize(
    "damage", ["foreign", "missing-category", "duplicate-category", "missing-witness"]
)
async def test_invalid_detector_output_stays_unavailable_beside_actual_claim(
    setup, tracker, server, damage
):
    build, executor, *_ = setup
    await completed(tracker, server)
    output = payload()
    if damage == "foreign":
        output["criterionKey"] = "foreign/criterion"
    elif damage == "missing-category":
        output["checks"].pop()
    elif damage == "duplicate-category":
        output["checks"].append(output["checks"][0])
    else:
        output["checks"][1]["verdict"] = "unverifiable"
    executor.overclaim_output = output
    child = (await build(include_overclaims=True).run()).observations[0]
    assert child.claim is not None and child.unavailable_reason is None
    assert child.overclaims is None and child.overclaim_unavailable_reason
    assert [call["output_format"]["schema"] for call in executor.calls] == [
        AUDIT_CLAIM_SCHEMA,
        AUDIT_OVERCLAIM_SCHEMA,
    ]


@pytest.mark.parametrize("failed", ["claim", "overclaim", "mandate"])
async def test_failed_independent_arm_never_suppresses_the_other_actual_session(
    setup, tracker, server, failed
):
    build, executor, *_ = setup
    await completed(tracker, server)
    executor.overclaim_output = payload(OverclaimKind.SELF_RULE, verdict="refuted")
    reached = []

    async def during(kwargs):
        schema = kwargs["output_format"]["schema"]
        reached.append(schema)
        selected = {
            "claim": AUDIT_CLAIM_SCHEMA,
            "overclaim": AUDIT_OVERCLAIM_SCHEMA,
            "mandate": AUDIT_MANDATE_SCHEMA,
        }[failed]
        if schema == selected:
            raise RuntimeError(f"unavailable {failed} session")

    executor.during = during
    child = (await build(include_overclaims=True).run()).observations[0]
    if failed == "claim":
        assert child.claim is None and "claim" in child.unavailable_reason
        assert (
            child.overclaims is not None and child.overclaim_unavailable_reason is None
        )
        assert AUDIT_OVERCLAIM_SCHEMA in reached and AUDIT_MANDATE_SCHEMA in reached
    else:
        assert child.claim is not None and child.unavailable_reason is None
        assert child.overclaims is None and failed in child.overclaim_unavailable_reason


@pytest.mark.parametrize("mode", ["unconfigured", "unstarted", "lapsed"])
async def test_partial_source_availability_is_explicit_and_retains_normal_claim(
    setup, tracker, server, mode
):
    build, executor, *_ = setup
    executor.overclaim_output = payload()
    if mode != "unstarted":
        await completed(tracker, server)
    if mode == "lapsed":
        await tracker.update_issue(issue_key=CHILD, body=BODY.replace(HEAD, PRIOR))
    child = (await build(include_overclaims=mode != "unconfigured").run()).observations[
        0
    ]
    if mode == "lapsed":
        assert child.evidence.is_lapse and child.claim is None
        assert child.overclaims.observation.graded_sha == PRIOR
        assert child.overclaims.observation.head_sha == HEAD
        assert child.overclaim_unavailable_reason is None
    else:
        assert child.claim is not None and child.unavailable_reason is None
        assert child.overclaims is None
        assert ("not configured" if mode == "unconfigured" else "review claim") in (
            child.overclaim_unavailable_reason
        )


@pytest.mark.parametrize("change", ["body", "record", "head-between-arms"])
async def test_cross_arm_source_drift_cannot_become_a_coherent_sweep(
    setup, tracker, server, monkeypatch, change
):
    build, executor, git, _, _, _, stored, op = setup
    await completed(tracker, server)
    executor.overclaim_output = payload(OverclaimKind.SELF_RULE, verdict="refuted")
    sweep = build(include_overclaims=True)
    if change == "head-between-arms":
        original = sweep._overclaims.observe

        async def after_advance(request):
            second = "c" * 40
            git._remote_branch_shas["ordinary-name"] = second
            git._current_sha = second
            git._ancestor_pairs.add((HEAD, second))
            return await original(request)

        monkeypatch.setattr(sweep._overclaims, "observe", after_advance)
    else:

        async def during(kwargs):
            if kwargs["output_format"]["schema"] != AUDIT_MANDATE_SCHEMA:
                return
            if change == "body":
                await tracker.update_issue(
                    issue_key=CHILD, body=BODY + "\nChanged body."
                )
            else:
                reader = LaneRecordReader(tracker=tracker, operation=op)
                _, before = await reader.read(
                    issue_key=ROOT,
                    lane_key="opaque:lane λ",
                    record_ref=stored.comment_key,
                )
                replacement = stored.body.replace(
                    '"head-full-identity"', '"other-head"'
                )
                assert replacement != stored.body
                await tracker.upsert_comment(
                    target=ROOT, marker=stored.body.splitlines()[0], body=replacement
                )
                same, parsed = await reader.read(
                    issue_key=ROOT,
                    lane_key="opaque:lane λ",
                    record_ref=stored.comment_key,
                )
                assert same.comment_key == stored.comment_key and parsed != before

        executor.during = during
    with pytest.raises(AuditClaimReadError, match=r"changed|different branch heads"):
        await sweep.run()


async def test_only_successful_detector_still_rechecks_its_observed_head(
    setup, tracker, server
):
    build, executor, git, *_ = setup
    await completed(tracker, server)
    executor.overclaim_output = payload(OverclaimKind.SELF_RULE, verdict="refuted")

    async def during(kwargs):
        if kwargs["output_format"]["schema"] == AUDIT_CLAIM_SCHEMA:
            raise RuntimeError("Independent current claim session is unavailable.")
        if kwargs["output_format"]["schema"] == AUDIT_MANDATE_SCHEMA:
            git._remote_branch_shas["ordinary-name"] = "c" * 40

    executor.during = during
    with pytest.raises(AuditClaimReadError, match="observed branch changed"):
        await build(include_overclaims=True).run()


@pytest.mark.parametrize("phase", ["overclaim", "mandate"])
async def test_detector_or_mandate_cancellation_propagates_after_workspace_release(
    setup, tracker, server, phase
):
    build, executor, _, _, workspace, *_ = setup
    await completed(tracker, server)
    executor.overclaim_output = payload(OverclaimKind.SELF_RULE, verdict="refuted")
    active = asyncio.Event()

    async def during(kwargs):
        schema = (
            AUDIT_OVERCLAIM_SCHEMA if phase == "overclaim" else AUDIT_MANDATE_SCHEMA
        )
        if kwargs["output_format"]["schema"] == schema:
            active.set()
            await asyncio.Future()

    executor.during = during
    task = asyncio.create_task(build(include_overclaims=True).run())
    try:
        await asyncio.wait_for(active.wait(), 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert workspace.calls[-1][0] == "release"
