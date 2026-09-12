"""Actual precommit graph, canonical write-back and Git publication ordering."""

import json

import pytest

from kodezart.domain.amendment import AmendmentWriteBackRefusalError
from kodezart.domain.fire_spec import criterion_field_bodies
from kodezart.types.domain.agent import NativeAmendmentEvent, ResultEvent
from kodezart.types.domain.amendment import UpheldReason
from kodezart.types.domain.amendment_write import AmendmentRecord
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.chains.test_native_fire import DIRECT_DONE, DIRECT_OWED, tracker
from tests.services.test_native_amendments import (
    Executor,
    build,
    cleanup,
    drive,
    git,
    repository,
)

__all__ = ["repository"]


async def test_done_criterion_archives_exact_evidence_then_resets_before_new_check(
    repository, monkeypatch
):
    port = tracker()
    original = port.issues[DIRECT_DONE]
    original = original.model_copy(
        update={"body": original.body + "\n**Class:** observed\n"}
    )
    port.issues[DIRECT_DONE] = original
    order = []
    edit = port.edit_description
    reset = port.reset_criterion_pending

    async def edited(**kwargs):
        stored = port.issues[DIRECT_DONE]
        records = [c for c in port.comments if c.body.startswith("[fixture-amendment:")]
        assert len(records) == 1
        archived = AmendmentRecord.model_validate_json(
            records[0].body.partition("\n")[2]
        )
        assert json.loads(archived.prior.content)[0]["body"] == original.body
        if "the amended observable Check" in kwargs["replacement"]:
            assert stored.state_kind is WorkflowStateKind.UNSTARTED
            order.append("new_check")
        else:
            assert stored.state_kind is WorkflowStateKind.COMPLETED
            order.append("clear_evidence")
        return await edit(**kwargs)

    async def reset_observed(**kwargs):
        stored = port.issues[DIRECT_DONE]
        assert criterion_field_bodies(stored.body, field="Evidence") == ("",)
        assert criterion_field_bodies(stored.body, field="Class") == ("",)
        assert criterion_field_bodies(
            stored.body, field="Check"
        ) == criterion_field_bodies(original.body, field="Check")
        order.append("reset")
        return await reset(**kwargs)

    monkeypatch.setattr(port, "edit_description", edited)
    monkeypatch.setattr(port, "reset_criterion_pending", reset_observed)

    async def observe(title, payload, kwargs):
        if title == "CommitMessageOutput":
            assert order == ["clear_evidence", "reset", "new_check"]
            assert port.issues[DIRECT_DONE].state_kind is WorkflowStateKind.UNSTARTED
            order.append("commit_message")

    executor = Executor(
        reproduced=True,
        subject={"kind": "criterion", "id": DIRECT_DONE},
        mutate=observe,
    )
    service, guard, workspace, _ = await build(repository, executor, port=port)
    try:
        events = await drive(service, guard, repository)
        report = next(e.report for e in events if isinstance(e, NativeAmendmentEvent))
        assert report.verdicts[0].subject.id == DIRECT_DONE
        assert report.verdicts[0].verdict == "amended"
        assert order[-1] == "commit_message"
        assert any(isinstance(e, ResultEvent) and e.commit_sha for e in events)
    finally:
        await cleanup(workspace)


@pytest.mark.parametrize("exhaust", [False, True])
async def test_applied_writeback_repairs_within_bound_without_rejudging_ground(
    repository, exhaust
):
    rounds = 0
    authors = 0

    async def observe(title, payload, kwargs):
        nonlocal rounds, authors
        if title == "WriteBackFinding":
            rounds += 1
            if rounds == 2 or (exhaust and rounds > 2):
                payload.update(
                    verdict="refuted",
                    evidence="The landed Check still omits the required boundary.",
                    cited_refs=["policy.py"],
                )
        elif title == "AmendmentTextOutput":
            authors += 1
            if authors == 2:
                assert (
                    "The landed Check still omits the required boundary."
                    in kwargs["prompt"]
                )
                payload["replacement"]["check"] = "the repaired observable Check"

    executor = Executor(reproduced=True, mutate=observe)
    service, guard, workspace, port = await build(repository, executor)
    prior = port.issues[DIRECT_OWED].body
    try:
        if exhaust:
            with pytest.raises(AmendmentWriteBackRefusalError) as failure:
                await drive(service, guard, repository)
            assert len(failure.value.result.rounds) == 2
            assert (
                failure.value.result.rounds[-1].evidence
                == "The landed Check still omits the required boundary."
            )
            assert (
                await git(
                    repository[0], "ls-remote", "origin", "refs/heads/native-test"
                )
                == ""
            )
        else:
            events = await drive(service, guard, repository)
            report = next(
                e.report for e in events if isinstance(e, NativeAmendmentEvent)
            )
            applied = report.verdicts[0]
            assert applied.verdict == "amended"
            assert [r.verdict.value for r in applied.applied.rounds] == [
                "refuted",
                "holds",
            ]
            assert json.loads(applied.prior.content)[0]["body"] == prior
        assert authors == 2
        assert rounds == 3
        assert (
            sum(
                c["output_format"]["schema"]["title"] == "AmendmentJudgment"
                for c in executor.calls
            )
            == 1
        )
    finally:
        await cleanup(workspace)


@pytest.mark.parametrize("affordable", [None, True, False])
async def test_cost_departure_is_recorded_not_actioned_and_uneconomic_is_escalated(
    repository, affordable
):
    async def observe(title, payload, kwargs):
        if title == "AmendmentJudgment":
            payload["finding"] = {
                "verdict": "unverifiable",
                "smallest_repair": "environment_supply",
                "cost_claim": {
                    "assertion": "The demonstration costs too much.",
                    "measurement": None
                    if affordable is None
                    else {"observed": "measured 12 minutes", "affordable": affordable},
                },
            }
            payload["measured_by"] = (
                None if affordable is None else "timed the actual base demonstration"
            )

    executor = Executor(mutate=observe)
    service, guard, workspace, port = await build(repository, executor)
    prior = port.issues[DIRECT_OWED]
    try:
        events = await drive(service, guard, repository)
        report = next(e.report for e in events if isinstance(e, NativeAmendmentEvent))
        refusal = report.upheld[0]
        assert refusal.reason is (
            UpheldReason.GROUND_NOT_REPRODUCED
            if affordable is None
            else UpheldReason.COST_MEASURED_AFFORDABLE
            if affordable
            else UpheldReason.COST_MEASURED_UNECONOMIC
        )
        record = AmendmentRecord.model_validate_json(
            refusal.publication.record.artifact.content.partition("\n")[2]
        )
        assert record.disposition == "accepted_and_not_actioned"
        assert json.loads(record.prior.content)[0]["body"] == prior.body
        assert port.issues[DIRECT_OWED].body == prior.body
        assert port.issues[DIRECT_OWED].state_kind is prior.state_kind
        assert ("decision" in port.issues[DIRECT_OWED].issue_labels) is (
            affordable is False
        )
        if affordable is False:
            assert refusal.publication.kind == "escalated"
            assert (
                "measured 12 minutes" in refusal.publication.escalation.artifact.content
            )
        assert not any(isinstance(e, ResultEvent) for e in events)
        assert (
            await git(repository[0], "ls-remote", "origin", "refs/heads/native-test")
            == ""
        )
    finally:
        await cleanup(workspace)
