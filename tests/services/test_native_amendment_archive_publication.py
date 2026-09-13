"""Verified amendment history stays live through actual commit and publication."""

import pytest

from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.types.domain.agent import ResultEvent
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


@pytest.mark.parametrize("phase", ["commit_message", "commit_receipt"])
@pytest.mark.parametrize("delete", [True, False])
async def test_archive_survives_commit_message_and_actual_commit_receipt(
    repository, monkeypatch, phase, delete
):
    port = tracker()
    reached = False
    committed = []

    def principal_change():
        nonlocal reached
        reached = True
        archives = [
            comment
            for comment in port.comments
            if comment.body.startswith("[fixture-amendment:")
        ]
        assert len(archives) == 1
        if delete:
            port.comments.remove(archives[0])

    async def answers(title, payload, kwargs):
        if phase == "commit_message" and title == "CommitMessageOutput":
            principal_change()

    executor = Executor(
        reproduced=True,
        subject={"kind": "criterion", "id": DIRECT_DONE},
        mutate=answers,
    )
    service, guard, workspace, _ = await build(repository, executor, port=port)
    actual_commit = workspace._git.commit

    async def commit(*args, **kwargs):
        receipt = await actual_commit(*args, **kwargs)
        committed.append(receipt)
        if phase == "commit_receipt":
            principal_change()
        return receipt

    monkeypatch.setattr(workspace._git, "commit", commit)
    try:
        if delete:
            with pytest.raises(NativeWriteRefusalError, match="amendment archive"):
                await drive(service, guard, repository)
            assert not await git(
                repository[0], "ls-remote", "origin", "refs/heads/native-test"
            )
            assert bool(committed) is (phase == "commit_receipt")
            if committed:
                assert (
                    await git(repository[0], "rev-parse", "native-test") == committed[0]
                )
            else:
                assert (
                    await git(repository[0], "log", "native-test", "--format=%s", "-1")
                    == "newer writer starting point"
                )
            assert workspace._workspaces, "failed native persistence retains evidence"
        else:
            events = await drive(service, guard, repository)
            assert len(committed) == 1
            assert any(
                isinstance(event, ResultEvent) and event.commit_sha == committed[0]
                for event in events
            )
            assert await git(
                repository[0], "ls-remote", "origin", "refs/heads/native-test"
            )
        assert reached
        assert [
            call["output_format"]["schema"]["title"] for call in executor.calls
        ].count("AmendmentJudgment") == 1
    finally:
        await cleanup(workspace)
