"""Parent workflow fields cannot affect actual live scope gap selection."""

import pytest

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind
from tests.chains.test_scope_ready import PROJECT, keys, pair, row
from tests.chains.test_scope_ready import ready_fixture as ready_fixture


@pytest.mark.parametrize("parent_kind", list(WorkflowStateKind))
async def test_gap_alone_selects_original_ungraded_criterion_without_parent_state_reads(
    ready_fixture, monkeypatch, parent_kind
):
    rows = [
        row("met", kind=parent_kind.value),
        row("met-first", parent="met", label="criterion", kind="completed"),
        row("met-second", parent="met", label="criterion", kind="completed"),
        row("gap", kind=parent_kind.value),
        row("gap-met", parent="gap", label="criterion", kind="completed"),
        row("gap-open", parent="gap", label="criterion"),
    ]
    fixture = await ready_fixture(rows)
    expected = await fixture.tracker.read_issue(issue_key="gap-open")
    parent_state_reads = []
    original = TrackerIssue.__getattribute__

    def checked(issue, name):
        if name in {"state_name", "state_kind"} and original(issue, "issue_key") in {
            "met",
            "gap",
        }:
            parent_state_reads.append((original(issue, "issue_key"), name))
            raise AssertionError("parent workflow field read during scope selection")
        return original(issue, name)

    monkeypatch.setattr(TrackerIssue, "__getattribute__", checked)
    selected = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)
    assert parent_state_reads == []
    assert keys(selected) == ["gap"]
    assert selected.ready[0].gap == (expected,)
    assert selected.blocked == ()
    fixture.assert_read_only()


async def test_subtree_blocking_never_consults_either_deliverable_parent_state(
    ready_fixture, monkeypatch
):
    fixture = await ready_fixture(pair())
    fixture.state("blocker", "completed")
    original = TrackerIssue.__getattribute__
    parent_state_reads = []

    def checked(issue, name):
        if name in {"state_name", "state_kind"} and original(issue, "issue_key") in {
            "blocker",
            "lane",
        }:
            parent_state_reads.append(name)
            raise AssertionError("blocker parent state was used as subtree closure")
        return original(issue, name)

    monkeypatch.setattr(TrackerIssue, "__getattribute__", checked)
    first = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)
    assert keys(first) == ["blocker"]
    assert first.blocked[0].issue_key == "lane"
    fixture.state("blocker-check", "completed")
    second = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)
    assert keys(second) == ["lane"]
    assert second.blocked == ()
    assert parent_state_reads == []
