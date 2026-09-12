"""Actual native sweep dispatch completes every source-addressed removal claim."""

import asyncio

import pytest
from pydantic import ValidationError

from kodezart.domain.errors import AuditClaimReadError
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.agent import (
    AUDIT_CLAIM_SCHEMA,
    AUDIT_MANDATE_SCHEMA,
    AUDIT_OVERCLAIM_SCHEMA,
    DETECTOR_REMOVAL_SCHEMA,
)
from kodezart.types.domain.assertion_drift import GitSourceBlob
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_detection_removal import DetectorRemovalReport
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.domain.test_detector_removal_report import finding
from tests.tracker.lease_fixtures import leased_comment
from tests.tracker.test_audit_evidence import Source
from tests.tracker.test_audit_overclaim_sweep import payload as overclaim_payload
from tests.tracker.test_audit_sweep import BODY, CHECK, CHILD, HEAD, PRIOR, ROOT, state
from tests.tracker.test_audit_sweep import server as server
from tests.tracker.test_audit_sweep import setup as setup


def payload(verdict="refuted", *, suffixes=("z", "a")):
    return {
        "criterionKey": CHILD,
        "verdict": verdict,
        "evidence": "The current suite is green but each removed guard now fails.",
        "findings": [finding(suffix) for suffix in suffixes]
        if verdict == "refuted"
        else [],
    }


class RevisionSource(Source):
    async def read_source(self, *, cwd, commit_sha, path):
        assert commit_sha == PRIOR
        rows = [
            quote
            for suffix in ("z", "a")
            for quote in (finding(suffix)["mechanism"], finding(suffix)["detector"])
        ]
        selected = next(row for row in rows if row["path"] == path)
        return GitSourceBlob(
            commit_sha=commit_sha,
            path=path,
            blob_sha="blob",
            content=selected["text"].encode(),
        )

    async def find_source(self, *, cwd, commit_sha, path):
        assert commit_sha != PRIOR
        return None


async def ready(tracker, server):
    await tracker.update_issue(issue_key=CHILD, body=BODY.replace(HEAD, PRIOR))
    await state(tracker, server, CHILD, "In Review", WorkflowStateKind.STARTED)


@pytest.mark.parametrize("verdict", ["holds", "refuted", "unverifiable"])
async def test_actual_sweep_preserves_source_findings_and_each_mandate(
    setup, tracker, server, tracker_writes, verdict
):
    build, executor, _, _, workspace, _, stored, *_ = setup
    await ready(tracker, server)
    executor.removal_output = payload(verdict)
    before = tracker_writes()
    child, parent = (
        await build(include_removals=True, selected_source=RevisionSource()).run()
    ).observations
    assert child.unavailable_reason is child.removal_unavailable_reason is None
    result = child.detector_removal
    assert (
        result.observation.graded_sha == PRIOR and result.observation.head_sha == HEAD
    )
    assert result.observation.record_ref == stored.comment_key
    assert result.observation.judgment.criterion_key == CHILD
    assert result.observation.check == CHECK
    assert len(result.reports) == (2 if verdict == "refuted" else 1)
    for entry in result.reports:
        assert entry.report.claim.judgment.verdict.value == verdict
        if verdict == "refuted":
            assert (
                entry.finding.model_dump_json() in entry.report.claim.judgment.evidence
            )
            assert entry.report.mandate.verdict is AuditVerdict.REFUTED
            assert {row.surface.ref.key for row in entry.report.mandate.covered} == {
                CHILD,
                ROOT,
            }
        else:
            assert entry.finding is entry.report.mandate is None
    if verdict == "refuted":
        assert [entry.finding.mechanism.path for entry in result.reports] == [
            "mechanism_z.py",
            "mechanism_a.py",
        ]
    assert [call["output_format"]["schema"] for call in executor.calls] == [
        AUDIT_CLAIM_SCHEMA,
        DETECTOR_REMOVAL_SCHEMA,
    ] + ([AUDIT_MANDATE_SCHEMA] * 2 if verdict == "refuted" else [])
    assert all(call["session_id"] is None for call in executor.calls)
    assert workspace.calls[-1][0] == "release"
    assert parent.detector_removal is parent.removal_unavailable_reason is None
    assert tracker_writes() == before


async def test_each_source_loss_gets_its_own_exact_mandate_finding(
    setup, tracker, server
):
    build, executor, *_ = setup
    await ready(tracker, server)
    executor.removal_output = payload()
    suffixes = iter(("z", "a"))

    async def during(kwargs):
        if kwargs["output_format"]["schema"] != AUDIT_MANDATE_SCHEMA:
            return
        suffix = next(suffixes)
        defect = (
            f"loss of detection for mechanism_{suffix}.py:1 "
            f"through test_{suffix}.py:1: {CHECK}"
        )
        assert defect in kwargs["prompt"]
        assert finding(suffix)["absence_demonstration"] in kwargs["prompt"]
        executor.mandate_output = {
            "verdict": "holds",
            "source_index": 1,
            "evidence": "Exact instruction.",
            "finding": {
                "issue_id": ROOT,
                "defect_class": defect,
                "role": "mandate",
                "mandate_text": "Explicit parent instructions.",
                "evidence": "The current source mandates this loss.",
            },
        }

    executor.during = during
    child = (
        await build(include_removals=True, selected_source=RevisionSource()).run()
    ).observations[0]
    assert child.removal_unavailable_reason is None
    result = child.detector_removal
    assert all(
        entry.report.mandate.verdict is AuditVerdict.HOLDS for entry in result.reports
    )
    data = result.model_dump()
    data["reports"][0]["report"]["mandate"]["finding"]["defect_class"] = "foreign loss"
    with pytest.raises(ValidationError, match="different detector loss"):
        DetectorRemovalReport.model_validate(data)


@pytest.mark.parametrize("failed", ["claim", "overclaim", "removal", "mandate"])
async def test_independent_failure_retains_every_other_available_arm(
    setup, tracker, server, failed
):
    build, executor, *_ = setup
    await ready(tracker, server)
    executor.removal_output = payload()
    executor.overclaim_output = overclaim_payload()
    reached = []

    async def during(kwargs):
        schema = kwargs["output_format"]["schema"]
        reached.append(schema)
        if (
            schema
            == {
                "claim": AUDIT_CLAIM_SCHEMA,
                "overclaim": AUDIT_OVERCLAIM_SCHEMA,
                "removal": DETECTOR_REMOVAL_SCHEMA,
                "mandate": AUDIT_MANDATE_SCHEMA,
            }[failed]
        ):
            raise RuntimeError(f"unavailable {failed} session")

    executor.during = during
    child = (
        await build(
            include_removals=True,
            include_overclaims=True,
            selected_source=RevisionSource(),
        ).run()
    ).observations[0]
    assert (child.claim is None) is (failed == "claim")
    assert (child.overclaims is None) is (failed == "overclaim")
    if failed in {"removal", "mandate"}:
        assert (
            child.detector_removal is None
            and failed in child.removal_unavailable_reason
        )
    else:
        assert (
            child.detector_removal is not None
            and child.removal_unavailable_reason is None
        )
    assert DETECTOR_REMOVAL_SCHEMA in reached


@pytest.mark.parametrize("mode", ["unconfigured", "unstarted", "lapsed"])
async def test_unavailable_revision_arm_and_lapsed_claim_remain_distinct(
    setup, tracker, server, mode
):
    build, executor, *_ = setup
    await ready(tracker, server)
    if mode != "unconfigured":
        await state(
            tracker,
            server,
            CHILD,
            "Done" if mode == "lapsed" else "Todo",
            WorkflowStateKind.COMPLETED
            if mode == "lapsed"
            else WorkflowStateKind.UNSTARTED,
        )
    executor.removal_output = payload("holds")
    child = (
        await build(
            include_removals=mode != "unconfigured", selected_source=RevisionSource()
        ).run()
    ).observations[0]
    if mode == "lapsed":
        assert child.evidence.is_lapse and child.claim is None
        assert child.detector_removal is not None
    else:
        assert child.claim is not None
        assert child.detector_removal is None
        assert (
            "not configured" if mode == "unconfigured" else "review claim"
        ) in child.removal_unavailable_reason


@pytest.mark.parametrize("phase", ["removal", "mandate"])
async def test_cancellation_is_not_downgraded_to_a_detector_refusal(
    setup, tracker, server, phase
):
    build, executor, _, _, workspace, *_ = setup
    await ready(tracker, server)
    executor.removal_output = payload()
    active = asyncio.Event()

    async def during(kwargs):
        if kwargs["output_format"]["schema"] == (
            DETECTOR_REMOVAL_SCHEMA if phase == "removal" else AUDIT_MANDATE_SCHEMA
        ):
            active.set()
            await asyncio.Future()

    executor.during = during
    task = asyncio.create_task(
        build(include_removals=True, selected_source=RevisionSource()).run()
    )
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


async def test_only_successful_removal_report_still_pins_its_observed_head(
    setup, tracker, server
):
    build, executor, git, *_ = setup
    await ready(tracker, server)
    executor.removal_output = payload()

    async def during(kwargs):
        if kwargs["output_format"]["schema"] == AUDIT_CLAIM_SCHEMA:
            raise RuntimeError("Claim session unavailable.")
        if kwargs["output_format"]["schema"] == AUDIT_MANDATE_SCHEMA:
            git._remote_branch_shas["ordinary-name"] = "c" * 40

    executor.during = during
    with pytest.raises(AuditClaimReadError, match="observed branch changed"):
        await build(include_removals=True, selected_source=RevisionSource()).run()


@pytest.mark.parametrize("damage", ["foreign", "wrong-line", "no-findings"])
async def test_unproven_removal_is_unavailable_without_erasing_the_regular_claim(
    setup, tracker, server, damage
):
    build, executor, *_ = setup
    await ready(tracker, server)
    output = payload()
    if damage == "foreign":
        output["criterionKey"] = "foreign/criterion"
    elif damage == "wrong-line":
        output["findings"][0]["mechanism"]["line"] = 2
    else:
        output["findings"] = []
    executor.removal_output = output
    child = (
        await build(include_removals=True, selected_source=RevisionSource()).run()
    ).observations[0]
    assert child.claim is not None and child.unavailable_reason is None
    assert child.detector_removal is None and child.removal_unavailable_reason
    assert len(executor.calls) == 2


@pytest.mark.parametrize("change", ["body", "record", "head-between-arms"])
async def test_final_source_and_head_checks_follow_the_actual_removal_arm(
    setup, tracker, server, monkeypatch, change
):
    build, executor, git, _, _, _, stored, operation = setup
    await ready(tracker, server)
    executor.removal_output = payload()
    sweep = build(include_removals=True, selected_source=RevisionSource())
    observed_heads = []
    if change == "head-between-arms":
        original = sweep._removals.observe

        async def after_advance(request):
            second = "c" * 40
            git._remote_branch_shas["ordinary-name"] = second
            git._ancestor_pairs.add((PRIOR, second))

            async def current_head(cwd):
                return second

            monkeypatch.setattr(git, "current_sha", current_head)
            result = await original(request)
            assert result.head_sha == second
            observed_heads.append(result.head_sha)
            return result

        monkeypatch.setattr(sweep._removals, "observe", after_advance)
    else:
        changed = False

        async def during(kwargs):
            nonlocal changed
            if kwargs["output_format"]["schema"] != AUDIT_MANDATE_SCHEMA or changed:
                return
            changed = True
            if change == "body":
                await tracker.update_issue(
                    issue_key=CHILD, body=BODY + "\nChanged Check source."
                )
            else:
                records = LaneRecordReader(tracker=tracker, operation=operation)
                _, original = await records.read(
                    issue_key=ROOT,
                    lane_key="opaque:lane λ",
                    record_ref=stored.comment_key,
                )
                replacement = stored.body.replace(
                    '"head-full-identity"', '"other-head"'
                )
                assert replacement != stored.body
                await leased_comment(
                    tracker,
                    target=ROOT,
                    marker=stored.body.splitlines()[0],
                    body=replacement,
                )
                comment, parsed = await records.read(
                    issue_key=ROOT,
                    lane_key="opaque:lane λ",
                    record_ref=stored.comment_key,
                )
                assert comment.comment_key == stored.comment_key and parsed != original

        executor.during = during
    expected = "different branch heads" if change == "head-between-arms" else "changed"
    with pytest.raises(AuditClaimReadError, match=expected):
        await sweep.run()
    if change == "head-between-arms":
        assert observed_heads == ["c" * 40]
