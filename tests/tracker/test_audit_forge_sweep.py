"""Actual scope assembly reaches the existing native forge and mandate readers."""

import asyncio

import pytest

from kodezart.chains.audit_forge import AuditForgeVerifier
from kodezart.core.config import AppConfig
from kodezart.domain.errors import AuditClaimReadError
from kodezart.domain.lane_record import parse_lane_record, render_lane_record
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.agent import AUDIT_CLAIM_SCHEMA, AUDIT_MANDATE_SCHEMA
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.delivery import CheckRedClass
from kodezart.types.domain.operation import CheckPrerequisite
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.tracker.test_audit_forge import NAMES, REPO, REPOSITORY, SHA, forge
from tests.tracker.test_audit_overclaim_sweep import completed
from tests.tracker.test_audit_sweep import BODY, CHECK, CHILD, ROOT, state
from tests.tracker.test_audit_sweep import server as server
from tests.tracker.test_audit_sweep import setup as setup


def selected_operation(operation, case="green"):
    repository = REPOSITORY
    if case == "environment":
        repository = repository.model_copy(
            update={"runner_environment": {CheckPrerequisite.REPOSITORY_HISTORY: False}}
        )
    return operation.model_copy(update={"repos": [repository]})


def verifier(tracker, operation, ci, reader):
    return AuditForgeVerifier(
        tracker=tracker,
        ci=ci,
        observations=reader,
        operation=operation,
        config=AppConfig(_env_file=None, delivery_red_rerun_max_attempts=1),
    )


@pytest.mark.parametrize("backend", ["fake", "github"])
@pytest.mark.parametrize(
    "case,verdict,red_class",
    [
        ("green", AuditVerdict.HOLDS, None),
        ("work", AuditVerdict.REFUTED, CheckRedClass.WORK_DEFECT),
        ("flake", AuditVerdict.HOLDS, CheckRedClass.RUNNER_FLAKE),
        ("unknown", AuditVerdict.UNVERIFIABLE, CheckRedClass.UNCLASSIFIED),
        (
            "environment",
            AuditVerdict.UNVERIFIABLE,
            CheckRedClass.ENVIRONMENT_PREREQUISITE_UNMET,
        ),
        ("absent", AuditVerdict.UNVERIFIABLE, None),
        ("foreign-sha", AuditVerdict.UNVERIFIABLE, None),
        ("roster", AuditVerdict.UNVERIFIABLE, None),
    ],
)
async def test_native_sweep_preserves_exact_forge_observation_and_mandate(
    setup, tracker, server, tracker_writes, backend, case, verdict, red_class
):
    build, executor, _, _, workspace, _, stored, operation = setup
    await completed(tracker, server)
    op = selected_operation(operation, case)
    before = tracker_writes()
    async with forge(backend, case) as (ci, reader, calls):
        child, parent = (
            await build(
                selected_op=op, selected_forge=verifier(tracker, op, ci, reader)
            ).run()
        ).observations
        assert child.forge_unavailable_reason is None
        observed = child.forge
        assert observed.criterion == child.target.issue
        assert observed.recorded_evidence.graded_sha == SHA
        assert observed.recorded_evidence.test == "OLD_RECORDED_TEST"
        assert observed.required_check_names == NAMES
        assert observed.verdict is verdict
        assert (observed.red.red_class if observed.red else None) is red_class
        assert observed.checks is None or observed.checks.commit_sha == SHA
        report = child.forge_report
        assert report.claim.judgment.criterion_key == CHILD
        assert report.claim.judgment.verdict is verdict
        assert report.claim.judgment.evidence == observed.reason
        assert report.claim.head_sha == SHA and report.claim.check == CHECK
        assert report.claim.record_ref == stored.comment_key
        if verdict is AuditVerdict.REFUTED:
            assert report.mandate.verdict is AuditVerdict.REFUTED
            assert {item.surface.ref.key for item in report.mandate.covered} == {
                ROOT,
                CHILD,
            }
            assert SHA in executor.calls[-1]["prompt"]
            assert observed.reason in executor.calls[-1]["prompt"]
        else:
            assert report.mandate is None
        assert [call["output_format"]["schema"] for call in executor.calls] == [
            AUDIT_CLAIM_SCHEMA
        ] + ([AUDIT_MANDATE_SCHEMA] if verdict is AuditVerdict.REFUTED else [])
        assert all(call["session_id"] is None for call in executor.calls)
        reruns = (
            calls if backend == "fake" else [r for r in calls if r.method == "POST"]
        )
        assert len(reruns) == (1 if case in {"work", "flake", "unknown"} else 0)
        if backend == "fake":
            assert {row["repo_url"] for row in ci.calls} == {REPO}
            assert {row["ref"] for row in ci.calls} == {SHA}
        else:
            assert all(
                f"/commits/{SHA}" in row.url.path
                for row in calls
                if "/commits/" in row.url.path
            )
        assert (
            parent.forge
            is parent.forge_report
            is parent.forge_unavailable_reason
            is None
        )
    assert workspace.calls[-1][0] == "release"
    assert tracker_writes() == before


@pytest.mark.parametrize(
    "kind", [k for k in WorkflowStateKind if k is not WorkflowStateKind.COMPLETED]
)
async def test_noncompleted_criteria_keep_other_arms_without_entering_forge(
    setup, tracker, server, monkeypatch, kind
):
    build, _, _, _, _, _, _, operation = setup
    await state(tracker, server, CHILD, "In Review", kind)
    op = selected_operation(operation)
    async with forge("fake", "green") as (ci, reader, _):
        selected = verifier(tracker, op, ci, reader)
        original = selected.observe
        invoked = []

        async def observed(request):
            invoked.append(request)
            return await original(request)

        monkeypatch.setattr(selected, "observe", observed)
        child = (
            await build(selected_op=op, selected_forge=selected).run()
        ).observations[0]
        assert child.claim is not None
        assert child.forge is child.forge_report is None
        assert "completed criterion" in child.forge_unavailable_reason
        assert not ci.calls
        assert not invoked


@pytest.mark.parametrize("missing", ["verifier", "capabilities", "request"])
async def test_unavailable_inputs_are_retained_without_clean_forge_claim(
    setup, tracker, server, missing
):
    build, _, _, _, _, _, _, operation = setup
    await completed(tracker, server)
    op = selected_operation(operation)
    if missing == "request":
        op = op.model_copy(update={"teams": {}})
    child = (
        await build(
            selected_op=op,
            selected_forge=None
            if missing == "verifier"
            else verifier(tracker, op, None, None),
        ).run()
    ).observations[0]
    if missing == "capabilities":
        assert child.forge.verdict is AuditVerdict.UNVERIFIABLE
        assert "capabilities are unavailable" in child.forge.reason
        assert child.forge_report.claim.judgment.verdict is AuditVerdict.UNVERIFIABLE
    else:
        assert child.forge is child.forge_report is None
        assert child.forge_unavailable_reason


@pytest.mark.parametrize("failed_arm", ["claim", "mandate"])
async def test_forge_survives_independent_failure_and_retains_raw_mandate_failure(
    setup, tracker, server, failed_arm
):
    build, executor, _, _, _, _, _, operation = setup
    await completed(tracker, server)
    op = selected_operation(operation)

    async def failing(kwargs):
        target = AUDIT_CLAIM_SCHEMA if failed_arm == "claim" else AUDIT_MANDATE_SCHEMA
        if kwargs["output_format"]["schema"] == target:
            raise RuntimeError(f"unavailable {failed_arm} session")

    executor.during = failing
    async with forge("fake", "work") as (ci, reader, _):
        child = (
            await build(
                selected_op=op, selected_forge=verifier(tracker, op, ci, reader)
            ).run()
        ).observations[0]
    assert child.forge.verdict is AuditVerdict.REFUTED
    assert child.forge.red.red_class is CheckRedClass.WORK_DEFECT
    assert child.forge.checks.commit_sha == SHA
    if failed_arm == "claim":
        assert child.unavailable_reason and child.claim is None
        assert child.forge_report.mandate is not None
        assert child.forge_unavailable_reason is None
    else:
        assert child.claim is not None
        assert child.forge_report is None
        assert "unavailable mandate session" in child.forge_unavailable_reason


async def test_missing_own_evidence_never_borrows_a_parent_grading(
    setup, tracker, server
):
    build, _, _, _, _, _, _, operation = setup
    await completed(tracker, server)
    await tracker.update_issue(issue_key=ROOT, body=BODY)
    await tracker.update_issue(issue_key=CHILD, body=f"**Check:** {CHECK}")
    op = selected_operation(operation)
    async with forge("fake", "green") as (ci, reader, _):
        child = (
            await build(
                selected_op=op, selected_forge=verifier(tracker, op, ci, reader)
            ).run()
        ).observations[0]
        assert child.forge is child.forge_report is None
        assert child.forge_unavailable_reason
        assert not ci.calls


async def test_a_mandate_report_cannot_replace_the_actual_forge_claim(
    setup, tracker, server, monkeypatch
):
    build, _, _, _, _, _, _, operation = setup
    await completed(tracker, server)
    op = selected_operation(operation)
    async with forge("fake", "work") as (ci, reader, _):
        sweep = build(selected_op=op, selected_forge=verifier(tracker, op, ci, reader))
        original = sweep._mandates.complete

        async def changed(request):
            report = await original(request)
            if request.defect_class.startswith("forge checks"):
                return report.model_copy(
                    update={
                        "claim": report.claim.model_copy(update={"head_sha": "b" * 40})
                    }
                )
            return report

        monkeypatch.setattr(sweep._mandates, "complete", changed)
        child = (await sweep.run()).observations[0]
    assert child.claim is not None
    assert child.forge.verdict is AuditVerdict.REFUTED
    assert child.forge.recorded_evidence.graded_sha == SHA
    assert child.forge_report is None
    assert "changed the observed claim" in child.forge_unavailable_reason


@pytest.mark.parametrize("damage", ["body", "state", "key"])
async def test_forge_observation_must_equal_the_collected_native_criterion(
    setup, tracker, server, monkeypatch, damage
):
    build, _, _, _, _, _, _, operation = setup
    await completed(tracker, server)
    op = selected_operation(operation)
    async with forge("fake", "work") as (ci, reader, _):
        selected = verifier(tracker, op, ci, reader)
        original = selected.observe

        async def corrupted(request):
            result = await original(request)
            field, value = {
                "body": ("body", BODY + "\nDifferent instruction."),
                "state": ("state_kind", WorkflowStateKind.UNSTARTED),
                "key": ("issue_key", "another/native-key"),
            }[damage]
            return result.model_copy(
                update={"criterion": result.criterion.model_copy(update={field: value})}
            )

        monkeypatch.setattr(selected, "observe", corrupted)
        child = (
            await build(selected_op=op, selected_forge=selected).run()
        ).observations[0]
    assert child.forge is child.forge_report is None
    assert "differs from the collected target" in child.forge_unavailable_reason


@pytest.mark.parametrize("phase", ["watch", "mandate"])
async def test_forge_and_mandate_cancellation_propagate_without_partial_result(
    setup, tracker, server, monkeypatch, phase
):
    build, executor, _, _, workspace, _, _, operation = setup
    await completed(tracker, server)
    op = selected_operation(operation)
    entered = asyncio.Event()

    async def wait():
        entered.set()
        await asyncio.Future()

    async with forge("fake", "work") as (ci, reader, _):
        if phase == "watch":

            async def watching(**kwargs):
                await wait()

            monkeypatch.setattr(ci, "wait_for_checks", watching)
        else:

            async def during(kwargs):
                if kwargs["output_format"]["schema"] == AUDIT_MANDATE_SCHEMA:
                    await wait()

            executor.during = during
        task = asyncio.create_task(
            build(
                selected_op=op, selected_forge=verifier(tracker, op, ci, reader)
            ).run()
        )
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert workspace.calls[-1][0] == "release"


@pytest.mark.parametrize("damage", ["criterion", "record"])
async def test_final_native_snapshot_rejects_changes_after_completed_forge_hunt(
    setup, tracker, server, damage
):
    build, executor, _, _, _, _, stored, operation = setup
    await completed(tracker, server)
    op = selected_operation(operation)

    records = LaneRecordReader(tracker=tracker, operation=op)
    marker = stored.body.split("\n", 1)[0]
    original_record = parse_lane_record(
        body=stored.body, lane_key="opaque:lane λ", marker_prefixes=op.marker_prefixes
    )

    async def changing(kwargs):
        if kwargs["output_format"]["schema"] != AUDIT_MANDATE_SCHEMA:
            return
        if damage == "criterion":
            await tracker.update_issue(
                issue_key=CHILD, body=BODY + "\nChanged after watch."
            )
        else:
            # Edit the same keyed record into a different, still-readable value.
            record = original_record.model_copy(update={"files_changed": 17})
            _, body = render_lane_record(
                record=record, marker_prefixes=op.marker_prefixes
            ).split("\n", 1)
            changed = await tracker.upsert_comment(
                target=ROOT, marker=marker, body=body
            )
            assert changed.comment_key == stored.comment_key
            _, parsed = await records.read(
                issue_key=ROOT, lane_key=record.lane_key, record_ref=stored.comment_key
            )
            assert parsed == record

    executor.during = changing
    async with forge("fake", "work") as (ci, reader, _):
        with pytest.raises(AuditClaimReadError):
            await build(
                selected_op=op, selected_forge=verifier(tracker, op, ci, reader)
            ).run()
