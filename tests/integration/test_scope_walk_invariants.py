"""Where a walk's gap measurement may appear, and where it may not.

One full walk of two lanes, read twice. Once for presence: every tick report
names each ready lane's open criteria, in the order the read that selected the
lanes carried them. Once for absence: nothing the run wrote down anywhere holds
a criterion key list, scanned over every journal the board double records plus
the scope's own status post rather than over a list of surfaces written out
here — a list drifts from the double, and the surface a criterion key list
would actually leak through is whichever one nobody thought to name.

The walk ends with one lane still owing both its criteria, so the status post
and the lane records have material to leak; a run that had nothing left to say
about a criterion would pass this scan by having nothing to write.
"""

import asyncio
import json
import re

import pytest
import structlog.testing

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.types.domain.operation import CheckStep
from kodezart.types.domain.scope_runtime import ScopeWalkEvent
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import TRACKER_WRITE_JOURNALS, tracker_state
from tests.integration.test_scope_runtime import (
    FORGE_ORIGIN,
    SCOPE,
    WALK_BOUND_SECONDS,
    WalkRepos,
    board,
    bounded_walk,
    criteria_echo,
    drive,
    lane_failures,
    resumable,
    ticks_of,
)
from tests.integration.test_scope_union import armed, delivered_scope, stated

#: Both lanes owe two criteria, so a leaked key list has more than one key in
#: it and the scan below can tell a list from a single key an ordinary record
#: names on purpose.
TWO_EACH = {"A": ("check", "second"), "B": ("check", "second")}

#: The gradings one lane's fire asks for at a budget of one iteration,
#: observed and not assumed: the echoes the next fire is answered with begin
#: after them, and one left over would grade that fire against the wrong
#: criteria.
LANE_GRADINGS = 2

#: What each tick of the walk measures, one literal per tick. A's first fire
#: closes A/check alone, so A is offered again with a gap that is no longer
#: its roster; its second fire closes A/second. B's fire closes nothing, so
#: B plateaus on the tick after it, is put back and rests.
GAPS_PER_TICK = [
    [("A", ("A/check", "A/second")), ("B", ("B/check", "B/second"))],
    [("A", ("A/second",)), ("B", ("B/check", "B/second"))],
    [("B", ("B/check", "B/second"))],
    [("B", ("B/check", "B/second"))],
]


def criterion_keys_on(port) -> tuple[str, ...]:
    """Every criterion key the board carries, as the board itself labels them."""
    return tuple(
        key
        for key, issue in sorted(port.issues.items())
        if "criterion" in issue.issue_labels
    )


def strings_in(value) -> list[str]:
    """Every string anywhere inside *value*, containers and mappings included."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            found
            for item in (*value.keys(), *value.values())
            for found in strings_in(item)
        ]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [found for item in value for found in strings_in(item)]
    return []


def criterion_key_lists(port, status, *, keys) -> list[str]:
    """Every string a run wrote that names two or more of *keys* as whole tokens.

    The scanned surface is the board double's OWN totality-checked rendering,
    restricted to the journals a write lands in, plus the scope's status posts.
    Two or more keys, because one key is what an ordinary record legitimately
    names — a lane's own criterion in a cross-off event — while a LIST of them
    is the measurement this walk is not allowed to write down.
    """
    state = tracker_state(port)
    missed = TRACKER_WRITE_JOURNALS - set(state)
    assert missed == frozenset(), f"the state rendering reaches no {sorted(missed)}"
    written = [
        found
        for name in sorted(TRACKER_WRITE_JOURNALS)
        for found in strings_in(state[name])
    ]
    written.extend(body for _, body in status.posts)
    return [
        text
        for text in written
        if len({key for key in keys if re.search(rf"(?<![\w/-]){key}(?![\w/-])", text)})
        >= 2
    ]


async def test_a_walk_names_each_lanes_gap_on_its_tick_report_and_in_no_durable_write():
    """The measurement is on every tick report and on nothing the run wrote.

    Lane A's first fire closes one of its criteria and its second fire the
    other, so A's gap shrinks between two ticks that both offer it; lane B's
    fire closes neither, so B plateaus, is put back and rests, and the walk
    ends with both of B's criteria open. What each tick's report names is
    compared with a ready read taken at that tick's report AND with a literal
    per tick, so a report that repeated an earlier read, or agreed with a
    wrong one, would still be caught.

    The supervisor's tally record and the run alarms are not this run's
    writes: no walk path writes either, and a scan over every journal the
    board double keeps would catch one if it did.
    """
    port = board(lanes=("A", "B"), checks=TWO_EACH)
    # A family that really commits, so the run leaves lane records on the board
    # for the scan to read: a walk whose lanes wrote nothing would pass the
    # absence half by having written nothing at all.
    harness = resumable(
        port=port,
        repos=WalkRepos(),
        lanes=("A", "B"),
        evaluations=[
            *(
                criteria_echo(keys=("A/check", "A/second"), passed={"A/check"})
                for _ in range(LANE_GRADINGS)
            ),
            *(
                criteria_echo(keys=("A/second",), passed={"A/second"})
                for _ in range(LANE_GRADINGS)
            ),
            *(
                criteria_echo(keys=("B/check", "B/second"), passed=set())
                for _ in range(6)
            ),
        ],
    )
    keys = criterion_keys_on(port)
    assert keys == ("A/check", "A/second", "B/check", "B/second")
    expected = [
        (row.issue.issue_key, tuple(item.issue_key for item in row.gap))
        for row in (await read_scope_ready(ref=SCOPE, tracker=port)).ready
    ]

    # Each tick's report is yielded after that tick's read and before its
    # fire, so a read taken the moment the report arrives is the tick's own.
    reads_at_reports = []
    events = []
    with structlog.testing.capture_logs() as logs:
        async with asyncio.timeout(WALK_BOUND_SECONDS):
            async for event in drive(harness):
                events.append(event)
                if isinstance(event, ScopeWalkEvent):
                    reads_at_reports.append(
                        [
                            (row.issue.issue_key, tuple(c.issue_key for c in row.gap))
                            for row in (
                                await read_scope_ready(ref=SCOPE, tracker=port)
                            ).ready
                        ]
                    )

    ticks = ticks_of(events)
    assert lane_failures(events) == ()
    assert [tick.dispatched for tick in ticks] == [
        (),
        ("A",),
        ("A", "A"),
        ("A", "A", "B"),
    ]
    measured = [[(g.lane_key, g.criterion_keys) for g in tick.gaps] for tick in ticks]
    assert measured[0] == expected
    assert measured == reads_at_reports
    assert measured == GAPS_PER_TICK
    # One entry per ready lane of the same tick, on every tick: a measurement
    # for a lane the tick did not offer would be about some other read.
    assert all(
        tuple(item.lane_key for item in tick.gaps) == tick.ready for tick in ticks
    )

    # The premises the absence half stands on. A run that wrote nothing, or one
    # that ended owing nothing, would pass the scan for the wrong reason.
    assert [event["lane"] for event in logs if event["event"] == "scope_lane_plateaued"]
    assert port.comment_writes
    assert len(harness.status.posts) == 1
    assert ticks[-1].unresolved_criteria == ("B/check", "B/second")
    for key in ("A/check", "A/second"):
        assert port.issues[key].state_kind is WorkflowStateKind.COMPLETED
        assert "**Evidence:**" in port.issues[key].body

    assert criterion_key_lists(port, harness.status, keys=keys) == []

    # The matcher's own control: a payload of exactly the shape this scan is
    # about, planted on a journal, is reported.
    port.comment_writes.append(("A", json.dumps(["A/check", "B/check"])))
    assert criterion_key_lists(port, harness.status, keys=keys) == [
        '["A/check", "B/check"]'
    ]


#: The operator's own spelling of the union step bound: ``AppConfig`` reads it
#: from the environment, and the harness passes no such keyword of its own.
STEP_TIMEOUT = "KODEZART_UNION_CHECK_STEP_TIMEOUT_SECONDS"


@pytest.mark.parametrize(
    ("bound", "composition", "remediation", "executed"),
    [
        pytest.param(None, "green", None, b"x", id="default"),
        pytest.param(
            "0.05",
            "red",
            "Repair union check roots: union-gate. Cascading checks: none.",
            b"",
            id="bounded",
        ),
    ],
)
async def test_the_union_step_timeout_an_operator_sets_changes_what_the_walk_states(
    tmp_path, monkeypatch, bound, composition, remediation, executed
):
    """A non-default step bound changes the composed walker's statement, only that.

    The declared step sleeps past the bounded arm's wall clock before it leaves
    its mark, so the shipped default lets it finish green and the operator's
    bound kills it red, naming the step whose clock ran out, before the mark is
    written. The walk itself is the same walk in both arms: the same ticks, the
    same lanes offered and rested, no lane failed. How many fires in a row may
    close nothing before a lane rests is not a setting at all; that bound stays
    the walker's own constant.
    """
    counter = tmp_path / "executions"
    port, repos, forge = await delivered_scope()
    if bound is None:
        monkeypatch.delenv(STEP_TIMEOUT, raising=False)
    else:
        monkeypatch.setenv(STEP_TIMEOUT, bound)
    try:
        walk = armed(
            port,
            repos,
            forge,
            chain=(
                CheckStep(
                    name="union-gate", command=f"sleep 0.3; printf x >> {counter}"
                ),
            ),
        )
        with structlog.testing.capture_logs() as logs:
            events = await bounded_walk(walk, job="second-job", origin=FORGE_ORIGIN)
    finally:
        await forge.close()

    observed = stated(logs, "scope_union_observed")
    assert len(observed) == 3
    assert {line["composition"] for line in observed} == {composition}
    assert {line["remediation"] for line in observed} == {remediation}
    assert (counter.read_bytes() if counter.exists() else b"") == executed
    assert lane_failures(events) == ()
    assert [
        (tick.tick, tick.dispatched, tick.rested_lanes) for tick in ticks_of(events)
    ] == [(1, (), ()), (2, (), ("A",)), (3, (), ("A", "B"))]
