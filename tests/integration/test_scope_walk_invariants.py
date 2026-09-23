"""Where a walk's gap measurement may appear, and where it may not.

One full walk of two lanes, read twice. Once for presence: every tick report
names each ready lane's open criteria, in the order the read that selected the
lanes carried them. Once for absence: nothing the run wrote down holds a
criterion key list, scanned over the write journals the board double declares
(``TRACKER_WRITE_JOURNALS``) plus the scope's own status posts. Each scanned
surface is shown to be read by a payload planted on it, so a scan that
stopped reading one of them fails here rather than passing beside a leak.

The walk ends with one lane still owing both its criteria, so the status post
and the lane records have material to leak; a run that had nothing left to say
about a criterion would pass this scan by having nothing to write.
"""

import asyncio
import copy
import dataclasses
import json
import re
from datetime import UTC, datetime

import pytest
import structlog.testing
from pydantic import BaseModel

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.domain.lane_entry import recorded_branches
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.dispatch import SelfWriteLedger
from kodezart.types.domain.operation import CheckStep
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import ScopeWalkEvent
from kodezart.types.domain.surface import SurfaceKind, SurfaceLease, WritableSurface
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import TRACKER_WRITE_JOURNALS, make_tracker_issue, tracker_state
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
    lane_record,
    recorded_so_far,
    resumable,
    ticks_of,
)
from tests.integration.test_scope_union import (
    ScratchGit,
    armed,
    delivered_scope,
    executions,
    stated,
)

#: Each lane owes two criteria or more, so a leaked key list has more than one
#: key in it and the scan below can tell a list from a single key an ordinary
#: record names on purpose. Lane A owes a third, still open after its first
#: fire, so the Evidence written on that fire has two OTHER owed keys a leak
#: could list beside it.
OWED = {"A": ("check", "second", "third"), "B": ("check", "second")}

#: The gradings one lane's fire asks for at a budget of one iteration,
#: observed and not assumed: the echoes the next fire is answered with begin
#: after them, and one left over would grade that fire against the wrong
#: criteria.
LANE_GRADINGS = 2

#: What each tick of the walk measures, one literal per tick. A's first fire
#: closes A/check alone, so A is offered again with a gap that is no longer
#: its roster; its second fire closes A/second and A/third. B's fire closes
#: nothing, so B plateaus on the tick after it, is put back and rests.
GAPS_PER_TICK = [
    [("A", ("A/check", "A/second", "A/third")), ("B", ("B/check", "B/second"))],
    [("A", ("A/second", "A/third")), ("B", ("B/check", "B/second"))],
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
    """Every string anywhere inside *value*, records and containers included.

    A pydantic model and a dataclass instance are descended through their own
    field dump: the board double's rendering keeps any value that compares
    itself as it is, so a record in a journal reaches this scan whole.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, BaseModel):
        return strings_in(value.model_dump())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return strings_in(dataclasses.asdict(value))
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

    The scanned surface is the write journals the board double declares
    (``TRACKER_WRITE_JOURNALS``), read through its own rendering, plus the
    scope's status posts.
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


def foreign_keys_on_criterion_writes(port, *, keys) -> list[tuple[str, str]]:
    """Every body the run wrote onto criterion X that names a key other than X.

    The rule is the write's own target: a criterion's own body (its Check,
    its Evidence) is about that criterion alone, so even ONE other key on it
    is a gap reading written down, which the two-key rule above cannot see.
    """
    return [
        (written, body)
        for written, _, body in port.issue_writes
        if written in keys and body is not None
        for key in keys
        if key != written and re.search(rf"(?<![\w/-]){key}(?![\w/-])", body)
    ]


def planted(port, name: str, payload: str) -> None:
    """*payload* written into the journal *name* the way that journal grows.

    A list journal gains an entry, a set journal a member, a mapping journal
    a key, and the write ledger a stamp through its own ``record``. A
    journal of any other kind fails here, so one the double adds later is
    planted on before it is trusted to be scanned.
    """
    journal = getattr(port, name)
    if isinstance(journal, list):
        journal.append(payload)
    elif isinstance(journal, set):
        journal.add(payload)
    elif isinstance(journal, dict):
        journal[payload] = payload
    elif isinstance(journal, SelfWriteLedger):
        journal.record(issue_key=payload, updated_at=datetime(2026, 1, 1, tzinfo=UTC))
    else:
        pytest.fail(f"no way to plant on the journal {name}")


#: One canceled criterion under each lane, so the walk has excluded keys a
#: durable write could leak.
DROPPED = ("A/dropped", "B/dropped")


async def test_a_walk_names_each_lanes_gap_on_its_tick_report_and_in_no_durable_write():
    """The measurement is on every tick report and on nothing the run wrote.

    Lane A's first fire closes one of its three criteria and its second fire
    the other two, so A's gap shrinks between two ticks that both offer it; lane B's
    fire closes neither, so B plateaus, is put back and rests, and the walk
    ends with both of B's criteria open. What each tick's report names is
    compared with a ready read taken at that tick's report AND with a literal
    per tick, so a report that repeated an earlier read, or agreed with a
    wrong one, would still be caught.

    The supervisor's tally record and the run alarms are not this run's
    writes: no walk path writes either, and a scan over every declared write
    journal would catch one if it did.
    """
    port = board(lanes=("A", "B"), checks=OWED)
    for key in DROPPED:
        port.issues[key] = make_tracker_issue(
            key,
            parent_key=key.split("/")[0],
            issue_labels=frozenset({"criterion"}),
            state_name="Canceled",
            state_kind=WorkflowStateKind.CANCELED,
            body=f"**Check:** {key} live Check  bytes\n**Evidence:** —",
        )
    # A family that really commits, so the run leaves lane records on the board
    # for the scan to read: a walk whose lanes wrote nothing would pass the
    # absence half by having written nothing at all.
    harness = resumable(
        port=port,
        repos=WalkRepos(),
        lanes=("A", "B"),
        evaluations=[
            *(
                criteria_echo(
                    keys=("A/check", "A/second", "A/third"), passed={"A/check"}
                )
                for _ in range(LANE_GRADINGS)
            ),
            *(
                criteria_echo(
                    keys=("A/second", "A/third"), passed={"A/second", "A/third"}
                )
                for _ in range(LANE_GRADINGS)
            ),
            *(
                criteria_echo(keys=("B/check", "B/second"), passed=set())
                for _ in range(6)
            ),
        ],
    )
    keys = criterion_keys_on(port)
    assert keys == (
        "A/check",
        "A/dropped",
        "A/second",
        "A/third",
        "B/check",
        "B/dropped",
        "B/second",
    )
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
    assert await recorded_so_far(port, "A") is not None
    assert await recorded_so_far(port, "B") is not None
    assert len(harness.status.posts) == 1
    assert ticks[-1].unresolved_criteria == ("B/check", "B/second")
    assert ticks[-1].excluded_criteria == DROPPED
    for key in ("A/check", "A/second", "A/third"):
        assert port.issues[key].state_kind is WorkflowStateKind.COMPLETED
        assert "**Evidence:**" in port.issues[key].body
        assert "**Evidence:** —" not in port.issues[key].body
        assert [
            body
            for written, _, body in port.issue_writes
            if written == key and body is not None and "**Evidence:**" in body
        ]

    assert criterion_key_lists(port, harness.status, keys=keys) == []
    assert foreign_keys_on_criterion_writes(port, keys=keys) == []

    # The per-target rule's own controls: one other key on a criterion's
    # body is reported, and the criterion's own key on it is not.
    port.issue_writes.append(("A/check", None, "**Evidence:** see A/second"))
    assert foreign_keys_on_criterion_writes(port, keys=keys) == [
        ("A/check", "**Evidence:** see A/second")
    ]
    port.issue_writes[-1] = ("A/check", None, "**Evidence:** A/check at a sha")
    assert foreign_keys_on_criterion_writes(port, keys=keys) == []
    port.issue_writes.pop()

    # The matcher's own controls: a payload of exactly the shape this scan is
    # about is reported from each surface it scans, one surface at a time.
    payload = json.dumps(["A/check", "B/check"])
    for name in sorted(TRACKER_WRITE_JOURNALS):
        kept = copy.deepcopy(getattr(port, name))
        planted(port, name, payload)
        assert criterion_key_lists(port, harness.status, keys=keys), name
        setattr(port, name, kept)
    harness.status.posts.append((SCOPE, payload))
    assert criterion_key_lists(port, harness.status, keys=keys) == [payload]
    harness.status.posts.pop()
    # A key list inside a record-typed entry is reported too: a pydantic
    # model in the base-spec journal and a dataclass in the lease journal,
    # each of which the board's rendering keeps whole.
    port.recorded_base_specs["planted"] = trunk_base(payload)
    assert criterion_key_lists(port, harness.status, keys=keys) == [payload]
    del port.recorded_base_specs["planted"]
    port.lease_writes.append(
        SurfaceLease(
            holder=payload,
            surfaces=frozenset(
                {
                    WritableSurface(
                        kind=SurfaceKind.CRITERION_SUB_ISSUE,
                        ref=ScopeRef(kind=ScopeKind.ISSUE, key="A/check"),
                    )
                }
            ),
            expires_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    assert criterion_key_lists(port, harness.status, keys=keys) == [payload]
    port.lease_writes.pop()
    assert criterion_key_lists(port, harness.status, keys=keys) == []


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
        (
            tick.tick,
            tick.dispatched,
            tick.rested_lanes,
            tick.skipped_lanes,
            tick.failed_lanes,
        )
        for tick in ticks_of(events)
    ] == [(1, (), (), (), ()), (2, (), ("A",), (), ()), (3, (), ("A", "B"), (), ())]


#: The operator's own spelling of how many union attempts a tick makes before
#: moving heads refuse it; ``AppConfig`` reads it from the environment.
STALE_ATTEMPTS = "KODEZART_UNION_STALE_MAX_ATTEMPTS"


@pytest.mark.parametrize(
    ("attempts", "per_measurement"),
    [pytest.param(None, 3, id="default"), pytest.param("1", 1, id="bounded")],
)
async def test_the_stale_attempts_an_operator_sets_changes_what_the_walk_does(
    tmp_path, monkeypatch, attempts, per_measurement
):
    """A non-default stale-attempt bound changes how often the walk composes.

    The declared step appends a mark, and every time it has run the branch
    lane A's record names moves on the remote before the heads are read
    again, so no composition ever outlives its own heads. Each tick then
    composes once per allowed attempt and states the scope unmeasured: the
    shipped default composes three times a tick, and the operator's bound of
    one composes once. The walk itself is the same walk in both arms: the
    same ticks, the same lanes offered and rested, none skipped or failed.
    """
    counter = tmp_path / "executions"
    port, repos, forge = await delivered_scope()
    moved = recorded_branches(record=await lane_record(port, "A")).deliverable_branch
    if attempts is None:
        monkeypatch.delenv(STALE_ATTEMPTS, raising=False)
    else:
        monkeypatch.setenv(STALE_ATTEMPTS, attempts)
    read_head = ScratchGit.remote_branch_sha
    moves: list[int] = []

    async def moving(self, cwd, remote, branch):
        # A composition that ran since the last move is followed by a move,
        # so the read after it sees a head the composition did not measure.
        ran = executions(counter)
        if ran > len(moves):
            before = repos.current
            head = repos.of(moved)
            head.commit()
            head.publish()
            repos.committing = before
            moves.append(ran)
        return await read_head(self, cwd, remote, branch)

    monkeypatch.setattr(ScratchGit, "remote_branch_sha", moving)
    events = []
    ran_at_reports = []
    try:
        walk = armed(
            port,
            repos,
            forge,
            chain=(CheckStep(name="union-gate", command=f"printf x >> {counter}"),),
        )
        with structlog.testing.capture_logs() as logs:
            async with asyncio.timeout(WALK_BOUND_SECONDS):
                async for event in drive(walk, job="second-job", origin=FORGE_ORIGIN):
                    events.append(event)
                    if isinstance(event, ScopeWalkEvent):
                        ran_at_reports.append(executions(counter))
    finally:
        await forge.close()

    # Each tick states its report after it measured, so the executions
    # between two reports are that tick's measurement.
    assert [
        ran - before
        for before, ran in zip([0, *ran_at_reports], ran_at_reports, strict=False)
    ] == [per_measurement] * 3
    assert stated(logs, "scope_union_observed") == []
    assert len(stated(logs, "scope_union_unmeasured")) == 3
    assert lane_failures(events) == ()
    assert [
        (
            tick.tick,
            tick.dispatched,
            tick.rested_lanes,
            tick.skipped_lanes,
            tick.failed_lanes,
        )
        for tick in ticks_of(events)
    ] == [(1, (), (), (), ()), (2, (), ("A",), (), ()), (3, (), ("A", "B"), (), ())]
