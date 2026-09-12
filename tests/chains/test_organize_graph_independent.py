"""Independent configured-owner proof of the final observed split-source gap."""

from kodezart.domain.errors import OrganizeWriteRefusalError
from tests.chains.test_organize import result
from tests.chains.test_organize_owner import factory, run_owner
from tests.tracker.conftest import CLAIMED_ISSUE


async def test_configured_owner_refuses_observed_final_split_source_drift(monkeypatch):
    owner, board, executor = factory()
    original_stream = executor.stream

    async def stream(**kwargs):
        if kwargs["output_format"]["schema"].get("title") == "OrganizeProposal":
            yield result(
                structured_output={
                    "kind": "split",
                    "issue_id": CLAIMED_ISSUE,
                    "children": [
                        {
                            "deliverable_key": "independent",
                            "title": "Split child",
                            "body": "Prepared child specification",
                        }
                    ],
                }
            )
            return
        async for event in original_stream(**kwargs):
            yield event

    original_call = board.call_tool
    after_state = False
    reads = 0
    injected = False

    async def call_tool(*, name, arguments):
        nonlocal after_state, reads, injected
        if name == "list_issue_statuses":
            after_state = True
        if after_state and name == "get_issue" and arguments["id"] == CLAIMED_ISSUE:
            reads += 1
            if reads == 2:
                injected = True
                board.server.issues[
                    CLAIMED_ISSUE
                ].description = (
                    "The current source no longer asks for the authored child."
                )
        return await original_call(name=name, arguments=arguments)

    monkeypatch.setattr(executor, "stream", stream)
    monkeypatch.setattr(board, "call_tool", call_tool)
    error = None
    try:
        await run_owner(owner)
    except OrganizeWriteRefusalError as exc:
        error = exc
    assert injected
    assert [
        args for name, args in board.calls if name == "save_issue" and "id" not in args
    ] == []
    assert error is not None
    assert not board.grants()
