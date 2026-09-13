"""Hostile output and multi-claim source carriage through actual native services."""

import pytest
from pydantic import ValidationError

from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.types.domain.agent import NativeAmendmentEvent, ResultEvent
from tests.chains.test_native_fire import DIRECT_OWED, DIRECT_OWED_TOO, tracker
from tests.services.test_native_amendments import (
    Executor,
    build,
    cleanup,
    drive,
    git,
    repository,
)

__all__ = ["repository"]


@pytest.mark.parametrize(
    "shape", ["foreign_subject", "preserved", "extra_field", "valid"]
)
async def test_actual_amendment_author_cannot_widen_its_typed_write_role(
    repository, shape
):
    port = tracker()
    before = port.issues[DIRECT_OWED]
    reached = False

    async def hostile_output(title, payload, kwargs):
        nonlocal reached
        if title != "AmendmentTextOutput":
            return
        reached = True
        if shape == "foreign_subject":
            payload["replacement"]["subject"]["id"] = DIRECT_OWED_TOO
        elif shape == "preserved":
            payload["replacement"] = {"kind": "preserved"}
        elif shape == "extra_field":
            payload["replacement"]["evidence"] = "Invented verification"

    executor = Executor(reproduced=True, mutate=hostile_output)
    service, guard, workspace, _ = await build(repository, executor, port=port)
    try:
        if shape == "valid":
            events = await drive(service, guard, repository)
            assert any(isinstance(e, ResultEvent) and e.commit_sha for e in events)
        else:
            with pytest.raises((NativeWriteRefusalError, ValidationError)):
                await drive(service, guard, repository)
            assert port.issues[DIRECT_OWED] == before
            assert not await git(
                repository[0], "ls-remote", "origin", "refs/heads/native-test"
            )
        assert reached
    finally:
        await cleanup(workspace)


@pytest.mark.parametrize("remove_first_history", [False, True])
async def test_second_claim_requires_first_claims_current_history(
    repository, remove_first_history
):
    port = tracker()
    judged = 0
    reached_second = False
    second_before = port.issues[DIRECT_OWED_TOO]

    async def two_claims(title, payload, kwargs):
        nonlocal judged, reached_second
        if title == "NativeWriterOutput":
            second = {
                **payload["claims"][0],
                "subject": {"kind": "criterion", "id": DIRECT_OWED_TOO},
            }
            payload["claims"].append(second)
        elif title == "AmendmentJudgment":
            judged += 1
            if judged == 2:
                reached_second = True
                payload["subject"] = {"kind": "criterion", "id": DIRECT_OWED_TOO}
                records = [
                    c for c in port.comments if c.body.startswith("[fixture-amendment:")
                ]
                assert len(records) == 1
                if remove_first_history:
                    port.comments.remove(records[0])
        elif title == "AmendmentTextOutput":
            payload["replacement"]["subject"] = {
                "kind": "criterion",
                "id": DIRECT_OWED if judged == 1 else DIRECT_OWED_TOO,
            }

    executor = Executor(reproduced=True, mutate=two_claims)
    service, guard, workspace, _ = await build(repository, executor, port=port)
    try:
        if remove_first_history:
            with pytest.raises(NativeWriteRefusalError):
                await drive(service, guard, repository)
            assert port.issues[DIRECT_OWED_TOO] == second_before
            assert not await git(
                repository[0], "ls-remote", "origin", "refs/heads/native-test"
            )
        else:
            events = await drive(service, guard, repository)
            report = next(
                e.report for e in events if isinstance(e, NativeAmendmentEvent)
            )
            assert tuple(v.subject.id for v in report.verdicts) == (
                DIRECT_OWED,
                DIRECT_OWED_TOO,
            )
            assert all(v.verdict == "amended" for v in report.verdicts)
            assert any(isinstance(e, ResultEvent) and e.commit_sha for e in events)
        assert reached_second
    finally:
        await cleanup(workspace)
