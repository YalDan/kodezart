"""The scope's union, asked once a tick at scope grain by the walk itself.

The witness that a check ran is the chain itself. The declared repository
carries one step whose command appends one byte to a file outside the scratch
tree, so the file's length IS the number of times the union check executed —
counted through the shipped subprocess runner the composition now builds, not
through a double of it. The lines the walk states say which heads each
execution measured.

Everything here drives the real composition over the walk family's own
doubles: ``build_workflow_engine`` through ``runtime``, the shipped runner, the
shipped coordinator, and the record reader the composition hands it. The one
double this module adds is a Git service that makes the scratch directory the
port is asked for, because the family's service records the acquisition
without making one and a command cannot run in a directory that is not there.
"""

import asyncio
from pathlib import Path

import pytest
import structlog.testing

from kodezart.domain.errors import MergeConflictError, UnionHeadReadError
from kodezart.domain.lane_entry import recorded_branches
from kodezart.types.domain.operation import CheckStep
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.scope_runtime import ScopeWalkEvent
from kodezart.types.domain.scope_terminal import ScopeTerminalEvent
from kodezart.types.domain.tracker import IssuePriority
from kodezart.types.domain.union_tick import ScopeUnionRequest
from tests.adapters.test_github_api import _make_client
from tests.fakes import FakeGitService
from tests.integration.test_scope_runtime import (
    FORGE_ORIGIN,
    ORIGIN,
    SCOPE,
    WALK_BOUND_SECONDS,
    WalkGit,
    WalkRepos,
    board,
    bounded_walk,
    drive,
    finish_by_hand,
    lane_failures,
    lane_record,
    one_check_echoes,
    resumable,
    runtime,
    ticks_of,
)
from tests.lane_fixture import ScopeForgeWire

#: Two lanes, ranked apart so the order the union composes in is the planner's
#: own answer and not the order the rows happen to be built in.
LANES = ("A", "B")
UNION_PRIORITIES = {"A": IssuePriority.URGENT, "B": IssuePriority.HIGH}


class ScratchGit(WalkGit):
    """The walk family's service, making the scratch tree it is asked for.

    The family records an acquisition and creates nothing, which is right for
    a lane whose tree is the workspace double's business. A union composition
    really runs a command in the tree it asked for, so this one makes the
    directory and then records the call exactly as the family does. The
    enclosing temporary directory still removes it.
    """

    async def create_worktree(
        self,
        repo_path: str,
        base_ref: str,
        worktree_path: str,
        branch_name: str | None = None,
        create_branch: bool = True,
    ) -> None:
        Path(worktree_path).mkdir(parents=True, exist_ok=True)
        await super().create_worktree(
            repo_path, base_ref, worktree_path, branch_name, create_branch
        )


def counting_chain(counter: Path, *, failing: bool = False) -> tuple[CheckStep, ...]:
    """One declared step that appends a byte per execution, red on request."""
    tail = "; exit 1" if failing else ""
    return (CheckStep(name="union-gate", command=f"printf x >> {counter}{tail}"),)


def executions(counter: Path) -> int:
    """How many times the declared chain ran, read off its own side effect."""
    return len(counter.read_bytes()) if counter.exists() else 0


def stated(logs, event: str) -> list[dict]:
    """Every line of *event* the walk stated, in the order it stated them."""
    return [line for line in logs if line.get("event") == event]


def head_sets(logs) -> list[tuple[str, ...]]:
    """The measured head set each stated composition carried."""
    return [line["heads"] for line in stated(logs, "scope_union_observed")]


async def delivered_scope():
    """Two lanes finished, delivered and recorded by one walk over one family.

    The premise every grain case needs: a scope whose lanes each have a branch
    on the remote and a record naming it, and nothing left for a later walk to
    fire. A walk that fired would move the heads it measures, so a case about
    how many compositions three ticks make has to start from a scope at rest.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=LANES, priorities=UNION_PRIORITIES)
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    first = resumable(
        port=port,
        repos=repos,
        lanes=LANES,
        origin=FORGE_ORIGIN,
        forge=forge,
        trunk="main",
        evaluations=[
            *one_check_echoes("A", rounds=4),
            *one_check_echoes("B", rounds=4),
        ],
    )
    events = await bounded_walk(first, job="first-job", origin=FORGE_ORIGIN)
    assert lane_failures(events) == ()
    for key in LANES:
        assert (await lane_record(port, key)).pr is not None
    return port, repos, forge


def armed(port, repos, forge, *, chain, git=None):
    """A second walk over the same family, with the union armed by *chain*."""
    return resumable(
        port=port,
        repos=repos,
        lanes=LANES,
        origin=FORGE_ORIGIN,
        forge=forge,
        trunk="main",
        git=ScratchGit(repos) if git is None else git,
        repo_checks=chain,
    )


async def test_a_two_lane_scope_is_composed_once_for_the_whole_walk(tmp_path):
    """Three ticks, two lanes, one composition — and two ticks that reuse it.

    The walk asks its union on every tick and the check runs on the tick whose
    lane heads moved, which here is the first: nothing between the three ticks
    touches either lane's branch on the remote. So the counter holds one byte
    for three ticks, and the two later ticks restate the measurement the first
    one made rather than making one.

    Never per lane, in the same fixture: two lanes and one execution, and no
    stated composition names one lane alone. A measurement taken inside a
    lane's turn would name whichever lane was having it.
    """
    counter = tmp_path / "executions"
    port, repos, forge = await delivered_scope()
    try:
        second = armed(port, repos, forge, chain=counting_chain(counter))

        with structlog.testing.capture_logs() as logs:
            events = await bounded_walk(second, job="second-job", origin=FORGE_ORIGIN)

        # One execution of the declared chain for the whole walk.
        assert executions(counter) == 1
        # Three ticks: one per lane offered for its delivery and found already
        # delivered, and the one with nothing left to offer.
        assert len(ticks_of(events)) == 3
        assert lane_failures(events) == ()
        observed = stated(logs, "scope_union_observed")
        assert len(observed) == 3
        assert stated(logs, "scope_union_unmeasured") == []
        # Every tick measured the whole scope: both lanes, in the planner's
        # order, at the same heads throughout.
        assert {line["lanes"] for line in observed} == {2}
        assert len(set(head_sets(logs))) == 1
        assert tuple(entry.split(":")[0] for entry in observed[0]["heads"]) == LANES
        assert not [line for line in observed if line["lanes"] != 2]
        for key in LANES:
            recorded = recorded_branches(record=await lane_record(port, key))
            assert (
                repos.head_of(recorded.deliverable_branch)
                in observed[0]["heads"][LANES.index(key)]
            )
    finally:
        await forge.close()


async def test_a_moved_lane_head_is_composed_again_before_the_walk_states_it(tmp_path):
    """A head that moves between two ticks is measured again, not reported.

    The first tick composes and the walk states what it measured. The test
    then advances the branch one lane's record names and publishes it, so the
    heads the next tick reads differ from the ones the stated measurement
    carries. The chain runs a second time, and no line after the move restates
    the head set the first one carried.
    """
    counter = tmp_path / "executions"
    port, repos, forge = await delivered_scope()
    try:
        second = armed(port, repos, forge, chain=counting_chain(counter))
        moved = recorded_branches(
            record=await lane_record(port, "A")
        ).deliverable_branch

        events = []
        branch = None
        with structlog.testing.capture_logs() as logs:
            async with asyncio.timeout(WALK_BOUND_SECONDS):
                async for event in drive(second, job="second-job", origin=FORGE_ORIGIN):
                    events.append(event)
                    if isinstance(event, ScopeWalkEvent) and branch is None:
                        before = repos.current
                        branch = repos.of(moved)
                        branch.commit()
                        branch.publish()
                        repos.committing = before

        assert branch is not None
        # Two executions: the tick that first measured the scope, and the tick
        # after the move, which could not report what it no longer held.
        assert executions(counter) == 2
        sets = head_sets(logs)
        assert len(sets) == len(ticks_of(events)) == 3
        assert f"A:{branch.pushed}" in sets[1]
        assert f"A:{branch.pushed}" not in sets[0]
        assert sets[0] not in sets[1:]
    finally:
        await forge.close()


async def test_a_red_composition_stops_no_lane_and_is_stated_once(tmp_path):
    """A failing chain is stated with its repair and changes nothing else.

    The walk that meets a red union observes what the green one observes,
    offers the same lanes and reaches the same terminal report: nothing is
    gated on a composition, no lane is skipped and no state is written from
    one.
    """
    green_counter, red_counter = tmp_path / "green", tmp_path / "red"
    port, repos, forge = await delivered_scope()
    try:
        walk = armed(port, repos, forge, chain=counting_chain(green_counter))
        with structlog.testing.capture_logs() as green_logs:
            green_events = await bounded_walk(
                walk, job="green-job", origin=FORGE_ORIGIN
            )
    finally:
        await forge.close()

    port, repos, forge = await delivered_scope()
    try:
        walk = armed(
            port, repos, forge, chain=counting_chain(red_counter, failing=True)
        )
        with structlog.testing.capture_logs() as red_logs:
            red_events = await bounded_walk(walk, job="green-job", origin=FORGE_ORIGIN)
    finally:
        await forge.close()

    assert executions(green_counter) == executions(red_counter) == 1
    green = stated(green_logs, "scope_union_observed")
    red = stated(red_logs, "scope_union_observed")
    assert {line["composition"] for line in green} == {"green"}
    assert {line["composition"] for line in red} == {"red"}
    assert {line["remediation"] for line in green} == {None}
    assert [line["remediation"] for line in red] == [
        "Repair union check roots: union-gate. Cascading checks: none."
    ] * len(red)
    # One statement per tick, as on the green walk: the one entry is stated
    # once each time the union is asked, never twice.
    assert len(red) == len(green) == len(ticks_of(red_events))
    # The walk itself is the same walk: the same ticks, the same lanes offered
    # and rested, and one terminal report with the same reading of the vector.
    assert [
        (tick.tick, tick.dispatched, tick.rested_lanes, tick.failed_lanes)
        for tick in ticks_of(red_events)
    ] == [
        (tick.tick, tick.dispatched, tick.rested_lanes, tick.failed_lanes)
        for tick in ticks_of(green_events)
    ]
    assert terminal_of(red_events) == terminal_of(green_events)
    # And that report is the vector's own reading: both lanes delivered.
    assert terminal_of(red_events)[0] is WorkflowOutcome.scope_converged


#: The path both lanes' deliveries edit, where composing the second onto the
#: first stops.
SHARED_PATH = "shared.py"


class ConflictingScratchGit(ScratchGit):
    """The scratch tree, where one lane's delivery conflicts with the other's.

    Both deliveries edit one path, so merging the named head onto the tree
    that already holds the other stops on that path, the way the composing
    merge would, and reports the path it stopped on.
    """

    def __init__(self, repos: WalkRepos, *, conflicting_head: str) -> None:
        super().__init__(repos)
        self.conflicting_head = conflicting_head

    async def merge_scratch_head(
        self, *, cwd: str, head_sha: str, author_name: str, author_email: str
    ) -> None:
        await super().merge_scratch_head(
            cwd=cwd,
            head_sha=head_sha,
            author_name=author_name,
            author_email=author_email,
        )
        if head_sha == self.conflicting_head:
            raise MergeConflictError(
                "scratch merge conflict", source_branch=head_sha, paths=(SHARED_PATH,)
            )


async def test_a_conflicting_composition_stops_no_lane_and_is_stated_once(tmp_path):
    """A merge conflict is the other red, and it reaches the same terminal.

    The second lane's delivery conflicts with the first's on one path, so the
    composition stops before any check runs and states its one repair, naming
    that lane and that path.  The walk offers what the green walk offers and
    reaches the same terminal report.
    """
    green_counter, conflict_counter = tmp_path / "green", tmp_path / "conflict"
    port, repos, forge = await delivered_scope()
    try:
        walk = armed(port, repos, forge, chain=counting_chain(green_counter))
        with structlog.testing.capture_logs() as green_logs:
            green_events = await bounded_walk(
                walk, job="green-job", origin=FORGE_ORIGIN
            )
    finally:
        await forge.close()

    port, repos, forge = await delivered_scope()
    try:
        second = LANES[-1]
        head = repos.head_of(
            recorded_branches(record=await lane_record(port, second)).deliverable_branch
        )
        walk = armed(
            port,
            repos,
            forge,
            chain=counting_chain(conflict_counter),
            git=ConflictingScratchGit(repos, conflicting_head=head),
        )
        with structlog.testing.capture_logs() as conflict_logs:
            conflict_events = await bounded_walk(
                walk, job="green-job", origin=FORGE_ORIGIN
            )
    finally:
        await forge.close()

    # The conflict stopped the composition before its chain: nothing ran.
    assert executions(green_counter) == 1
    assert executions(conflict_counter) == 0
    green = stated(green_logs, "scope_union_observed")
    conflict = stated(conflict_logs, "scope_union_observed")
    assert {line["composition"] for line in conflict} == {"red"}
    assert [line["remediation"] for line in conflict] == [
        f"Resolve union merge conflict for lane {second} in: {SHARED_PATH}."
    ] * len(conflict)
    assert len(conflict) == len(green) == len(ticks_of(conflict_events))
    assert lane_failures(conflict_events) == ()
    assert [
        (tick.tick, tick.dispatched, tick.rested_lanes, tick.failed_lanes)
        for tick in ticks_of(conflict_events)
    ] == [
        (tick.tick, tick.dispatched, tick.rested_lanes, tick.failed_lanes)
        for tick in ticks_of(green_events)
    ]
    assert terminal_of(conflict_events) == terminal_of(green_events)
    assert terminal_of(conflict_events)[0] is WorkflowOutcome.scope_converged


def terminal_of(events):
    """The one terminal report a walk reached, as its outcome and its vector.

    The branch each entry names is minted with a fresh suffix per run, so what
    two walks are compared on is the reading of the vector: which lane is
    reported done and which delivery it carries.
    """
    reports = [event for event in events if isinstance(event, ScopeTerminalEvent)]
    assert len(reports) == 1
    return reports[0].outcome, tuple(
        (entry.issue, entry.done, entry.pr) for entry in reports[0].lanes
    )


async def test_a_lane_that_published_no_branch_leaves_the_walk_running(tmp_path):
    """Tick one of every fresh scope, and nothing about it ends the walk.

    One of two lanes records no branch at all, so the roster refuses before a
    single head is read: the counter stays empty and every tick states one
    contained refusal. The walk fires the other lane, contains the refusal
    where no lane failure is reported, and reaches its terminal report — and
    what it offered and rested is what the same walk with no union armed
    offered and rested.
    """
    counter = tmp_path / "executions"

    def unpublished():
        """Lane A with a branch to publish, lane B finished somewhere else."""
        port = board(lanes=LANES, priorities=UNION_PRIORITIES)
        finish_by_hand(port, "B")
        return port

    def walking(port, repos, **rest):
        return resumable(
            port=port,
            repos=repos,
            lanes=LANES,
            git=ScratchGit(repos),
            evaluations=one_check_echoes("A", rounds=4),
            **rest,
        )

    port, repos = unpublished(), WalkRepos()
    walk = walking(port, repos, repo_checks=counting_chain(counter))
    with structlog.testing.capture_logs() as logs:
        events = await bounded_walk(walk)

    assert executions(counter) == 0
    assert stated(logs, "scope_union_observed") == []
    assert len(stated(logs, "scope_union_unmeasured")) == len(ticks_of(events))
    assert {line["scope"] for line in stated(logs, "scope_union_unmeasured")} == {
        SCOPE.key
    }
    # A union refusal is not a lane failure, and the lane that could run ran.
    assert lane_failures(events) == ()
    assert ticks_of(events)[-1].dispatched == ("A",)
    assert terminal_of(events)
    # A really published and recorded; B is the lane nothing records.
    assert recorded_branches(record=await lane_record(port, "A")).deliverable_branch
    assert await walk.port.list_comments(issue_key="B") == ()

    # The refusal is the typed one, read where a typed error can be read: the
    # same composed factory the walk holds, asked the same request. The roster
    # refuses before any head read, so asking adds no execution.
    union = await walk.engine._scoped_arm._union_for(
        ScopeUnionRequest(
            scope=SCOPE,
            repo=declared_repo(walk),
            repo_url=ORIGIN,
            job_id="second-job",
        )
    )
    with pytest.raises(UnionHeadReadError, match="records no deliverable ref: B"):
        await union.verify()
    assert executions(counter) == 0

    # And the same walk with no union armed offered and rested the same lanes.
    bare = walking(unpublished(), WalkRepos())
    bare_events = await bounded_walk(bare)
    assert [(tick.dispatched, tick.rested_lanes) for tick in ticks_of(bare_events)] == [
        (tick.dispatched, tick.rested_lanes) for tick in ticks_of(events)
    ]


def declared_repo(harness):
    """The one repository the walk's composition declared."""
    return harness.engine._scoped_arm._repositories[0]


async def test_a_repository_declaring_no_check_chain_is_never_composed(tmp_path):
    """The shipped default: nothing is composed and the invocation says so.

    This is the case every walk of the suite is in. Answering with nothing
    means exactly one thing — this repository declares no chain — so it is
    stated once for the invocation, no clone is opened at the scope's own key,
    and no tick states either a measurement or a refusal.
    """
    counter = tmp_path / "executions"
    port = board(lanes=LANES, priorities=UNION_PRIORITIES)
    finish_by_hand(port, "A")
    finish_by_hand(port, "B")
    walk = runtime(port=port, lanes=LANES)

    with structlog.testing.capture_logs() as logs:
        events = await bounded_walk(walk)

    assert executions(counter) == 0
    assert len(stated(logs, "scope_union_unarmed")) == 1
    assert stated(logs, "scope_union_observed") == []
    assert stated(logs, "scope_union_unmeasured") == []
    assert terminal_of(events)
    # No clone at the scope's own key: nothing was opened for a union that was
    # never composed, and a lane's clone is named by a digest and never by the
    # word this key ends in.
    cache = walk.engine._scoped_arm._cache
    assert [
        call for call in cache.calls if str(call["cache_key"]).endswith("-scope-union")
    ] == []


async def test_a_trunk_absent_from_the_remote_composes_nothing(tmp_path):
    """A base nothing settles refuses, once, and the walk goes on without one.

    The refusal is the shipped typed one and it is raised where the union is
    composed, before any backend call is made with a bad base. It is stated
    once for the invocation rather than once per tick, because a walk with no
    union asks for none.
    """
    counter = tmp_path / "executions"
    port = board(lanes=LANES, priorities=UNION_PRIORITIES)
    finish_by_hand(port, "A")
    finish_by_hand(port, "B")
    walk = runtime(
        port=port,
        lanes=LANES,
        trunk="release",
        repo_checks=counting_chain(counter),
        git=FakeGitService(remote_branch_shas={"release": None}),
    )

    with structlog.testing.capture_logs() as logs:
        events = await bounded_walk(walk)

    assert executions(counter) == 0
    assert len(stated(logs, "scope_union_unmeasured")) == 1
    assert stated(logs, "scope_union_observed") == []
    assert terminal_of(events)

    union_for = walk.engine._scoped_arm._union_for
    with pytest.raises(UnionHeadReadError, match="trunk head does not read") as raised:
        await union_for(
            ScopeUnionRequest(
                scope=SCOPE,
                repo=declared_repo(walk),
                repo_url=ORIGIN,
                job_id="second-job",
            )
        )
    assert raised.value.branch == "release"
