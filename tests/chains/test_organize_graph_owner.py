"""Configured graph repairs run through the actual author, owner and adapter."""

import re

from tests.chains.test_organize import result
from tests.chains.test_organize_owner import factory, run_owner
from tests.tracker.conftest import CLAIMED_ISSUE


async def test_configured_owner_applies_graph_priority_then_reentry_writes_nothing(
    monkeypatch,
):
    owner, board, executor = factory(convergence_bound=4, bound=3)
    original = executor.stream

    async def stream(**kwargs):
        schema = kwargs["output_format"]["schema"]
        key = re.findall(r"<issue_key>(.*?)</issue_key>", kwargs["prompt"])
        if (
            key
            and key[-1] == CLAIMED_ISSUE
            and board.server.issues[CLAIMED_ISSUE].priority_raw != 1
        ):
            executor.calls.append(kwargs)
            if schema.get("title") == "OrganizeProposal":
                yield result(
                    structured_output={
                        "kind": "graph",
                        "issue_id": CLAIMED_ISSUE,
                        "changes": [{"kind": "priority", "priority": "urgent"}],
                    }
                )
            else:
                yield result(
                    structured_output={
                        "issue_id": CLAIMED_ISSUE,
                        "verdict": "not_buildable",
                        "evidence": "The configured mandate requires urgent priority.",
                        "refusal_kind": "spec_gap",
                        "invented_decision": "Apply the recorded urgent priority.",
                    }
                )
            return
        async for event in original(**kwargs):
            yield event

    monkeypatch.setattr(executor, "stream", stream)
    report = await run_owner(owner)
    assert report.halt is None
    assert board.server.issues[CLAIMED_ISSUE].priority_raw == 1
    before = [call for call in board.calls if call[0] in {"save_issue", "save_comment"}]
    assert any(
        name == "save_issue" and args.get("priority") == 1 for name, args in before
    )
    await run_owner(owner)
    assert [
        call for call in board.calls if call[0] in {"save_issue", "save_comment"}
    ] == before


async def test_configured_split_prepares_children_without_execution_and_replays_cleanly(
    monkeypatch,
):
    owner, board, executor = factory(convergence_bound=4, bound=3)
    original = executor.stream

    async def stream(**kwargs):
        key = re.findall(r"<issue_key>(.*?)</issue_key>", kwargs["prompt"])
        if (
            key
            and key[-1] == CLAIMED_ISSUE
            and not any(
                issue.title == "Prepared split"
                for issue in board.server.issues.values()
            )
        ):
            schema = kwargs["output_format"]["schema"]
            if schema.get("title") == "OrganizeProposal":
                payload = {
                    "kind": "split",
                    "issue_id": CLAIMED_ISSUE,
                    "children": [
                        {
                            "deliverable_key": "stable-deliverable",
                            "title": "Prepared split",
                            "body": "Prepared source-grounded child specification.",
                        }
                    ],
                }
            else:
                payload = {
                    "issue_id": CLAIMED_ISSUE,
                    "verdict": "not_buildable",
                    "refusal_kind": "spec_gap",
                    "evidence": (
                        "The settled mandate calls for an independent "
                        "child deliverable."
                    ),
                    "invented_decision": "Prepare the specified split.",
                }
            executor.calls.append(kwargs)
            yield result(structured_output=payload)
            return
        async for event in original(**kwargs):
            yield event

    monkeypatch.setattr(executor, "stream", stream)
    report = await run_owner(owner)
    assert report.halt is None
    children = [
        issue
        for issue in board.server.issues.values()
        if issue.title == "Prepared split"
    ]
    assert len(children) == 1
    assert children[0].parent_id == CLAIMED_ISSUE
    assert children[0].status_type == "unstarted"
    assert "stable-deliverable" in children[0].description
    writes = [call for call in board.calls if call[0] in {"save_issue", "save_comment"}]
    await run_owner(owner)
    assert [
        call for call in board.calls if call[0] in {"save_issue", "save_comment"}
    ] == writes
    assert not any(
        "id" in args and "state" in args
        for name, args in writes
        if name == "save_issue"
    )
    assert not any(
        call["session_type"].value not in {"organize_pass"} for call in executor.calls
    )


async def test_graph_author_metadata_drift_refuses_before_native_write(monkeypatch):
    import pytest

    from kodezart.domain.errors import OrganizeWriteRefusalError

    owner, board, executor = factory()
    original = executor.stream

    async def stream(**kwargs):
        if kwargs["output_format"]["schema"].get("title") == "OrganizeProposal":
            board.server.issues[CLAIMED_ISSUE].priority_raw = 4
            yield result(
                structured_output={
                    "kind": "graph",
                    "issue_id": CLAIMED_ISSUE,
                    "changes": [{"kind": "priority", "priority": "urgent"}],
                }
            )
            return
        async for event in original(**kwargs):
            yield event

    monkeypatch.setattr(executor, "stream", stream)
    with pytest.raises(OrganizeWriteRefusalError, match="graph context changed"):
        await run_owner(owner)
    assert not [args for name, args in board.calls if name == "save_issue"]


async def test_cancel_during_issued_split_settles_before_lease_release(monkeypatch):
    import asyncio

    import pytest

    owner, board, executor = factory()
    original = executor.stream

    async def stream(**kwargs):
        if kwargs["output_format"]["schema"].get("title") == "OrganizeProposal":
            yield result(
                structured_output={
                    "kind": "split",
                    "issue_id": CLAIMED_ISSUE,
                    "children": [
                        {
                            "deliverable_key": "cancellation-child",
                            "title": "Split child",
                            "body": "Prepared specification",
                        }
                    ],
                }
            )
            return
        async for event in original(**kwargs):
            yield event

    monkeypatch.setattr(executor, "stream", stream)
    board.pause = lambda name, args: name == "save_issue" and "id" not in args
    task = asyncio.create_task(run_owner(owner))
    try:
        await asyncio.wait_for(board.reached.wait(), timeout=5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert any("issue_split_set|issue|" in row.body for row in board.grants())
    finally:
        board.resume.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not board.grants()
    assert (
        len(
            [
                issue
                for issue in board.server.issues.values()
                if issue.title == "Split child"
            ]
        )
        == 1
    )
    assert "graph complete" not in board.server.issues[CLAIMED_ISSUE].labels


async def test_peer_refutation_retains_citations_and_bound_without_peer_repair(
    monkeypatch,
):
    import json

    from tests.fakes import FakeMcpIssue

    owner, board, executor = factory(bound=2)
    board.server.issues["peer"] = FakeMcpIssue(
        id="peer",
        parent_id=CLAIMED_ISSUE,
        description="Prepared peer body",
        relations=[("relatedTo", CLAIMED_ISSUE)],
    )
    board.server.issues[CLAIMED_ISSUE].relations = [("relatedTo", "peer")]
    original = executor.stream
    peer_judgments = []

    async def stream(**kwargs):
        title = kwargs["output_format"]["schema"].get("title")
        if title == "OrganizeProposal":
            yield result(
                structured_output={
                    "kind": "graph",
                    "issue_id": CLAIMED_ISSUE,
                    "changes": [{"kind": "related_to", "remove": ["peer"]}],
                }
            )
            return
        if title == "WriteBackFinding":
            artifact = json.loads(
                re.findall(
                    r"<written_artifact>\s*(.*?)\s*</written_artifact>",
                    kwargs["prompt"],
                    re.S,
                )[-1]
            )
            if (
                artifact["nativeRef"] == "peer"
                and artifact["surface"]["kind"] == "issue_graph"
            ):
                peer_judgments.append(kwargs)
                yield result(
                    structured_output={
                        "verdict": "refuted",
                        "evidence": "The peer's retained claim names an absent test.",
                        "cited_refs": ["tests/absent_peer.py"],
                    }
                )
                return
        async for event in original(**kwargs):
            yield event

    monkeypatch.setattr(executor, "stream", stream)
    report = await run_owner(owner)
    assert report.halt is not None
    assert report.halt.bound.value == report.halt.bound.rounds_used == 2
    assert report.halt.bound.loop == "write_back"
    actual = report.halt.write_back_results[0]
    assert actual.artifact.native_ref == "peer"
    assert [finding.cited_refs for finding in actual.rounds] == [
        ("tests/absent_peer.py",)
    ] * 2
    assert len(peer_judgments) == 2
    assert all(call["session_id"] is None for call in peer_judgments)
    assert [
        args
        for name, args in board.calls
        if name == "save_issue" and args.get("id") == "peer"
    ] == [{"id": "peer", "addLabels": ["needs decision"]}]
    assert [
        args
        for name, args in board.calls
        if name == "save_issue" and "removeRelatedTo" in args
    ] == [{"id": CLAIMED_ISSUE, "removeRelatedTo": ["peer"]}]


async def test_recorded_ruling_context_reaches_author_and_lapses_on_actual_edit(
    monkeypatch,
):
    from kodezart.domain.rulings import render_ruling
    from kodezart.types.domain.agent import Ruling
    from tests.domain.test_rulings import ruling_data

    owner, board, executor = factory()
    ruling = Ruling.model_validate(
        ruling_data(issue_ref=CLAIMED_ISSUE, question="Which graph is authorized?")
    )
    body = render_ruling(
        ruling=ruling,
        lane_key="historical:lane/café",
        marker_prefixes=owner._operation.marker_prefixes,
    )
    comment = await owner._tracker.post_comment(issue_key=CLAIMED_ISSUE, body=body)
    original = executor.stream
    seen = []

    async def stream(**kwargs):
        if kwargs["output_format"]["schema"].get("title") == "OrganizeProposal":
            import json

            context = json.loads(
                re.findall(
                    r"<organize_context>\s*(.*?)\s*</organize_context>",
                    kwargs["prompt"],
                    re.S,
                )[-1]
            )
            seen.extend(context["ruling_comments"])
            native = next(
                row for row in board.server.comments if row.id == comment.comment_key
            )
            # The native ruling record actually changes after the author read.
            changed = Ruling.model_validate(
                {**ruling.model_dump(), "resolution": "A different authorized graph."}
            )
            native.body = render_ruling(
                ruling=changed,
                lane_key="historical:lane/café",
                marker_prefixes=owner._operation.marker_prefixes,
            )
        async for event in original(**kwargs):
            yield event

    import pytest

    from kodezart.domain.errors import OrganizeWriteRefusalError

    monkeypatch.setattr(executor, "stream", stream)
    with pytest.raises(OrganizeWriteRefusalError, match="graph context changed"):
        await run_owner(owner)
    assert len(seen) == 1
    assert seen[0]["comment_key"] == comment.comment_key
    assert seen[0]["body"] == body
    assert not [args for name, args in board.calls if name == "save_issue"]
