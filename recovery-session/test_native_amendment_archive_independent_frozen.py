"""Independent current-history guards at the actual native precommit boundary."""

import pytest

from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.types.domain.agent import NativeAmendmentEvent, ResultEvent
from tests.chains.test_native_fire import DIRECT_DONE, tracker
from tests.services.test_native_amendments import (
    Executor,
    build,
    cleanup,
    drive,
    git,
    repository,
)

__all__ = ["repository"]


@pytest.mark.parametrize("phase", ["author", "final_judge"])
@pytest.mark.parametrize("change", ["replace", "delete", "unchanged"])
async def test_native_amendment_requires_preserved_current_history(
    repository, phase, change
):
    port = tracker()
    original = port.issues[DIRECT_DONE]
    original = original.model_copy(
        update={"body": original.body + "\n**Class:** observed\n"}
    )
    port.issues[DIRECT_DONE] = original
    reached = False
    judgments = 0

    async def external_principal_edit(title, payload, kwargs):
        nonlocal reached, judgments
        if title == "WriteBackFinding":
            judgments += 1
        at_boundary = title == "AmendmentTextOutput" if phase == "author" else (
            title == "WriteBackFinding" and judgments == 2
        )
        if not at_boundary:
            return
        reached = True
        records = [
            c for c in port.comments if c.body.startswith("[fixture-amendment:")
        ]
        assert len(records) == 1
        archive = records[0]
        # A principal can edit native tracker content while the automation owns
        # its advisory grant; this is no second run claiming the same grant.
        if change == "delete":
            port.comments.remove(archive)
        elif change == "replace":
            port.comments[port.comments.index(archive)] = archive.model_copy(
                update={
                    "body": archive.body.partition("\n")[0]
                    + "\nA principal replaced this historical record."
                }
            )

    executor = Executor(
        reproduced=True,
        subject={"kind": "criterion", "id": DIRECT_DONE},
        mutate=external_principal_edit,
    )
    service, guard, workspace, _ = await build(repository, executor, port=port)
    try:
        if change == "unchanged":
            events = await drive(service, guard, repository)
            report = next(
                e.report for e in events if isinstance(e, NativeAmendmentEvent)
            )
            assert report.verdicts[0].verdict == "amended"
            assert any(isinstance(e, ResultEvent) and e.commit_sha for e in events)
        else:
            with pytest.raises(NativeWriteRefusalError):
                await drive(service, guard, repository)
            if phase == "author":
                assert port.issues[DIRECT_DONE] == original
            assert await git(
                repository[0], "ls-remote", "origin", "refs/heads/native-test"
            ) == ""
        assert reached
    finally:
        await cleanup(workspace)
