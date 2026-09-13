from pathlib import Path
p=Path('tests/chains/test_union_participant_order.py')
p.write_text('''"""Complete participants retain PR114's inherited-priority ordering oracle."""

import sys
from datetime import UTC, datetime

import pytest

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.types.domain.tracker import IssuePriority, IssueRelation, IssueRelationKind, priority_rank
from kodezart.types.domain.union import UnionOutcome
from tests.chains.test_delivery_coordinator import PROJECT, issue, work_ref
from tests.chains.test_union_exit_invariance import build_delivery
from tests.services import test_union_composition as pinned

OPENED = ("a", "b", "c", "d", "e")
ORDER = ("c", "e", "a", "d", "b")
PRIORITIES = {"a": IssuePriority.HIGH, "b": IssuePriority.MEDIUM,
              "c": IssuePriority.LOW, "d": IssuePriority.HIGH, "e": IssuePriority.URGENT}


async def test_complete_order_uses_inherited_priority_and_includes_blocked_head(tmp_path):
    fixture = await build_delivery(tmp_path / "world")
    # Extend the real two-lane fixture to the four-lane PR114 priority oracle
    # plus the urgent blocked member. It is ordinary and retains a branch,
    # so complete union membership includes it as well as its prerequisite.
    await pinned.git(fixture.author, "remote", "add", "upstream", str(fixture.remote))
    rows = []
    refs = {}
    for position, lane in enumerate(OPENED):
        branch = f"ordered/{lane}"
        await pinned.git(fixture.author, "checkout", "-b", branch, fixture.context.base_sha)
        (fixture.author / f"{lane}.txt").write_text(lane)
        await pinned.git(fixture.author, "add", ".")
        await pinned.git(fixture.author, "commit", "-m", lane)
        await pinned.git(fixture.author, "push", "upstream", branch)
        sha = await pinned.git(fixture.author, "rev-parse", "HEAD")
        refs[lane] = [work_ref(lane, branch, sha)]
        rows.append(issue(lane, priority=PRIORITIES[lane],
            created_at=datetime(2026, 1, position + 1, tzinfo=UTC),
            relations=(IssueRelation(kind=IssueRelationKind.BLOCKED_BY, issue_key="c"),) if lane == "e" else (),
        ))
        rows.append(issue(f"{lane}-check", parent_key=lane, issue_labels=frozenset({"criterion"})))
    fixture.tracker.issues = {row.issue_key: row for row in rows}
    fixture.tracker.scope_memberships[PROJECT] = OPENED
    fixture.tracker.recorded_work_refs = refs
    # Existing fixture's chain checks its original two files; this one states
    # the complete intended five-lane evidence explicitly.
    command = f'{sys.executable} -c "from pathlib import Path; ' + "; ".join(f"assert Path('{lane}.txt').exists()" for lane in OPENED) + '"'
    fixture.with_checks(pinned.entry(command).checks)
    await fixture.git.fetch(str(fixture.observer))
    before = await fixture.refs()
    ready = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)
    assert tuple(lane.issue.issue_key for lane in ready.ready) == ("c", "a", "d", "b")
    assert ORDER != OPENED
    assert ORDER != tuple(sorted(OPENED, reverse=True))
    assert ORDER != tuple(reversed(ORDER))
    assert ORDER != tuple(sorted(OPENED, key=lambda key: priority_rank(PRIORITIES[key])))

    result = await fixture.coordinator().verify()

    assert result.composition_order == ORDER
    assert len(result.lane_heads) == len(set(OPENED))
    assert result.outcome is UnionOutcome.GREEN
    expected = [refs[key][0].pushed_head_sha for key in ORDER]
    assert fixture.git.merged == expected
    # Read actual merge parents: a forged call log cannot satisfy the oracle.
    cursor = result.scratch_sha
    reverse_merged = []
    for _ in ORDER:
        parents = (await pinned.git(fixture.observer, "show", "-s", "--format=%P", cursor)).split()
        assert len(parents) == 2
        reverse_merged.append(parents[1])
        cursor = parents[0]
    assert tuple(reversed(reverse_merged)) == tuple(expected)
    assert cursor == fixture.context.base_sha
    assert await fixture.refs() == before
    assert fixture.git.created == fixture.git.removed == [result.scratch_path]


@pytest.mark.parametrize("classification", ["tracker", "decision"])
async def test_record_artifacts_do_not_add_a_delivery_branch(tmp_path, classification):
    fixture = await build_delivery(tmp_path / "world")
    from kodezart.types.domain.tracker import WorkflowStateKind
    record = issue("record", issue_labels=frozenset({classification}),
                   state_kind=WorkflowStateKind.COMPLETED, state_name="Done")
    fixture.tracker.issues[record.issue_key] = record
    fixture.tracker.scope_memberships[PROJECT] = ("a", "z", "record")
    result = await fixture.coordinator().verify()
    assert result.composition_order == ("z", "a")
    assert result.outcome is UnionOutcome.GREEN
''')
