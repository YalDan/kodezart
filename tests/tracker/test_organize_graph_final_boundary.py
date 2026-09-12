"""Final native observations still refuse drift after the split identity refresh."""

import pytest

from kodezart.domain.errors import OrganizeWriteRefusalError, SurfaceLeaseError
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.surface import SurfaceKind
from tests.tracker.conftest import CLAIMED_ISSUE
from tests.tracker.test_organize_graph_writes import address, changes, expected, fixture


@pytest.mark.parametrize(
    "change", [None, "body", "relations", "labels", "team", "expiry"]
)
async def test_final_split_observation_after_refreshed_identity_is_authoritative(
    monkeypatch, change
):
    board, tracker = fixture()
    snapshot = await expected(tracker)
    original = board.call_tool
    after_state = False
    source_reads = 0
    injected = False

    async def call_tool(*, name, arguments):
        nonlocal after_state, source_reads, injected
        if name == "list_issue_statuses":
            after_state = True
        if after_state and name == "get_issue" and arguments["id"] == CLAIMED_ISSUE:
            source_reads += 1
            # Graph snapshot, identity scan's detail, then final source read.
            if source_reads == 3:
                injected = True
                source = board.server.issues[CLAIMED_ISSUE]
                if change == "body":
                    source.description = "A different current source specification"
                elif change == "relations":
                    source.relations.append(("relatedTo", "peer"))
                elif change == "labels":
                    source.labels.append("acceptance-condition")
                elif change == "team":
                    source.team = "another-native-team"
                elif change == "expiry":
                    board.advance(400)
        return await original(name=name, arguments=arguments)

    async with RunSurfaceLease(
        tracker=tracker,
        job_id="actual-job",
        surfaces=frozenset({address(CLAIMED_ISSUE, SurfaceKind.ISSUE_SPLIT_SET)}),
        lease_seconds=321.5,
    ):
        monkeypatch.setattr(board, "call_tool", call_tool)
        error = None
        try:
            await tracker.create_split_if_absent(
                source_key=CLAIMED_ISSUE,
                deliverable_key="final-current",
                title="Prepared child",
                body="Prepared specification",
                holder="actual-job",
                expected=snapshot,
            )
        except (OrganizeWriteRefusalError, SurfaceLeaseError) as exc:
            error = exc
        assert injected
        writes = [args for name, args in board.calls if name == "save_issue"]
        if change is None:
            assert error is None
            assert len(writes) == 1
        else:
            assert error is not None
            assert writes == []
    assert board.grants() == []


@pytest.mark.parametrize("kind", [SurfaceKind.ISSUE_GRAPH, SurfaceKind.ISSUE_SPLIT_SET])
@pytest.mark.parametrize("holder", [None, "other-job"])
async def test_final_writers_refuse_missing_and_foreign_holder(kind, holder):
    board, tracker = fixture()
    snapshot = await expected(tracker)
    key = "child" if kind is SurfaceKind.ISSUE_GRAPH else CLAIMED_ISSUE
    async with RunSurfaceLease(
        tracker=tracker,
        job_id="actual-job",
        surfaces=frozenset({address(key, kind)}),
        lease_seconds=321.5,
    ):
        with pytest.raises(SurfaceLeaseError):
            if kind is SurfaceKind.ISSUE_GRAPH:
                await tracker.update_issue_graph(
                    issue_key=key,
                    expected=snapshot,
                    changes=changes({"kind": "priority", "priority": "urgent"}),
                    holder=holder,
                )
            else:
                await tracker.create_split_if_absent(
                    source_key=key,
                    deliverable_key="missing-holder",
                    title="Prepared child",
                    body="Prepared specification",
                    holder=holder,
                    expected=snapshot,
                )
        assert not [args for name, args in board.calls if name == "save_issue"]
    assert board.grants() == []
