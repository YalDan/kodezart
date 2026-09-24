"""A scope is either being organized or being run, and the gate is approval.

Both sides are the composed ones — the scheduled pass's tick from the
composition root and the scope run's own entry — over one board, so what
these cases read is the boundary a deployment has rather than a second
wiring written here.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from kodezart.composition import organize as organize_composition
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.domain.errors import ScopeNotApprovedError
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.operation import LifecycleStage, ScopeLabel
from tests.fakes import (
    DEFAULT_PROMPT_SET,
    SUPPRESS_ALL_SKILLS,
    TRACKER_WRITE_JOURNALS,
    handed_over,
    tracker_state,
)
from tests.integration.test_scope_entry import (
    GROOM_MARKER,
    HEARTBEAT_CONFIG,
    STAGED,
    TICKET_MARKER,
    errors,
    is_organize_session,
    staging_runtime,
    standing_board,
    standing_operation,
)
from tests.integration.test_scope_runtime import SCOPE, bounded_walk, ticks_of
from tests.prompts.test_prompt_wiring import load_registry

LANES = ("A", "B")
NOW = datetime(2026, 9, 22, tzinfo=UTC)


def triaged(port):
    """The board before anybody approved it, carrying the pre-approval gate."""
    port.scope_label_members[SCOPE] = frozenset({ScopeLabel.TRIAGE})
    return port


def grooming(harness, operation):
    """The scheduled pass's tick, built by the composition root."""
    return organize_composition.build_organize_tick(
        config=HEARTBEAT_CONFIG,
        operation=operation,
        tracker=harness.port,
        runner=harness.service,
        workspace=harness.workspace,
        git=harness.git,
        prompts=load_registry(
            default_set=DEFAULT_PROMPT_SET, bindings=operation_bindings(operation)
        ),
        skills=SUPPRESS_ALL_SKILLS,
    )


def approve(port):
    """Approval added beside the pre-approval gate rather than replacing it.

    The pre-approval row's own gate stays open, so what closes that row is
    its approval reading and nothing else.
    """
    port.scope_label_members[SCOPE] = port.scope_label_members[SCOPE] | {
        ScopeLabel.APPROVED
    }


def markers(port):
    """Every stage marker each member carries, keyed by member."""
    return {
        key: port.issues[key].issue_labels
        & frozenset({GROOM_MARKER, TICKET_MARKER, STAGED})
        for key in LANES
    }


#: Every write journal the board keeps, read off the double's own register
#: of them rather than listed here, so a journal the double gains is read by
#: every case below the day it is added.
LOGS = tuple(sorted(TRACKER_WRITE_JOURNALS))

#: The journals a side's own work lands in: its markers, the native stamps
#: its writes leave, its lease, and, for a run, the evidence row and the
#: completion its lanes' evaluations write on each criterion child they
#: close, and the run events each lane posts on its own stream. Every other
#: journal gains nothing on either side.
OWN = frozenset(
    {
        "classification_writes",
        "self_writes",
        "lease_writes",
        "lease_releases",
        "issue_writes",
        "workflow_writes",
        "comment_writes",
    }
)

#: One run event a lane posts on its own stream: the lane's marker and the
#: event as a fenced JSON document.
RUN_EVENT = re.compile(
    r"\A\[native-run-event:(?P<lane>[^\]]+)\]\n```json\n(?P<event>.*)\n```\Z",
    re.S,
)

#: The accounts a lane posts when its evaluation closes its criterion child:
#: the grading that passed it and the cross-off.
CLOSING_EVENTS = ("criterion_passed", "issue_crossed_off")


def run_events(port, then):
    """Every comment gained since *then*, read as (lane, event) run events.

    A comment that is not a run event fails here, so the only comments a
    side may write are the events its lanes post on their own streams.
    """
    events = []
    for _, body in gained(port, then, "comment_writes"):
        match = RUN_EVENT.match(body)
        assert match is not None, body
        events.append((match["lane"], json.loads(match["event"])))
    return events


def criterion(key):
    """The criterion child a lane's evaluation closes."""
    return f"{key}/check"


def standing(port, key):
    """What a mark of either side could change on one member.

    Its labels, its queue state and its workflow state, each as an entry of
    one set, so a mark spelled as a queue state or a state move shows up
    beside a label.
    """
    issue = port.issues[key]
    return frozenset(
        {
            *issue.issue_labels,
            *(f"queue_state: {state.value}" for state in issue.queue_states),
            f"state_name: {issue.state_name}",
        }
    )


@dataclass(frozen=True)
class Before:
    """Where the board stood before one side acted.

    Every member, every log, and the holders of the leases standing then.
    """

    members: dict
    logs: dict
    held: frozenset


def _reading(port, name):
    """A journal as a position to slice from, or whole where it is no list."""
    journal = getattr(port, name)
    if isinstance(journal, list):
        return len(journal)
    return tracker_state(port)[name]


def before(port):
    return Before(
        members={key: standing(port, key) for key in LANES},
        logs={name: _reading(port, name) for name in LOGS},
        held=frozenset(lease.holder for lease in port.leases.values()),
    )


def gained_labels(port, then):
    """What changed on each member since *then*, whatever its name.

    Labels, queue state and workflow state, gained or lost: a side's own
    markers are the only entries a case expects here.
    """
    return {key: standing(port, key) ^ then.members[key] for key in LANES}


def gained(port, then, name):
    """The entries journal *name* gained since *then*.

    A journal kept as a list answers its new entries; any other answers its
    whole rendering once it differs from what it was.
    """
    journal = getattr(port, name)
    if isinstance(journal, list):
        return journal[then.logs[name] :]
    now = tracker_state(port)[name]
    return [] if now == then.logs[name] else [now]


def assert_wrote_only(port, then, written, closed=()):
    """The side gained exactly the markers in *written* and its own lease.

    *written* maps each member to the markers that side wrote on it. What
    each member changed and the classification writes the board took are
    both held to exactly that; the native stamps move only when a marker
    was written; every lease the side took it released, and it released no
    lease but its own or one standing when it began. *closed* names the
    lanes whose criterion child a run's evaluation closed: the only issue
    writes are evidence rows on those children, the only state moves are
    their completions, and the only comments are run events those lanes
    post on their own streams, among them one grading and one cross-off of
    each child. Every other journal the board keeps gained nothing at all.
    """
    markers = sorted(
        (key, marker) for key, carried in written.items() for marker in carried
    )
    assert gained_labels(port, then) == {
        key: frozenset(written.get(key, ())) for key in LANES
    }
    assert sorted(gained(port, then, "classification_writes")) == markers
    assert bool(gained(port, then, "self_writes")) == bool(markers)
    taken = {lease.holder for lease in gained(port, then, "lease_writes")}
    released = {holder for _, holder in gained(port, then, "lease_releases")}
    assert taken <= released <= taken | then.held
    assert gained(port, then, "workflow_writes") == [
        (criterion(key), LifecycleStage.DONE) for key in closed
    ]
    assert {key for key, _, _ in gained(port, then, "issue_writes")} <= {
        criterion(key) for key in closed
    }
    events = run_events(port, then)
    assert {lane for lane, _ in events} <= set(closed)
    assert all(event["laneKey"] == lane for lane, event in events)
    assert sorted(
        (lane, event["kind"], event["subjectKey"])
        for lane, event in events
        if event["kind"] in CLOSING_EVENTS
    ) == sorted(
        (key, kind, criterion(key)) for key in closed for kind in CLOSING_EVENTS
    )
    assert {name: gained(port, then, name) for name in LOGS if name not in OWN} == {
        name: [] for name in LOGS if name not in OWN
    }


async def test_an_unapproved_scope_is_groomed_and_admits_no_run(monkeypatch):
    """The pre-approval row works the board; the run is refused before it reads.

    The tick leaves its own marker on every member and no lease behind. The
    entry of a run over the same board refuses on approval, so no stage
    marker joins them and no session is opened for one.
    """
    port = triaged(standing_board(LANES))
    operation = standing_operation()
    builds = []
    harness = staging_runtime(
        port, LANES, monkeypatch=monkeypatch, builds=builds, operation=operation
    )
    groomed = before(port)
    assert await grooming(harness, operation).run(NOW) is PassRun.RAN
    assert_wrote_only(port, groomed, dict.fromkeys(LANES, (GROOM_MARKER,)))
    assert port.leases == {}
    spent = len(harness.executor.organize_calls)

    refused_at = before(port)
    untouched = handed_over(port)
    organizers = len(builds)
    workspaces = len(harness.workspace.calls)
    with pytest.raises(ScopeNotApprovedError) as refused:
        await bounded_walk(harness, job="unapproved-run")
    # The whole board, every attribute of the double, as it was handed over.
    assert untouched()
    assert refused.value.ref == SCOPE
    # Refused before it reads: the entry builds no stage organizer, so none
    # runs, and no workspace is prepared for one.
    assert len(builds) == organizers
    assert len(harness.workspace.calls) == workspaces
    assert len(harness.executor.organize_calls) == spent
    assert gained_labels(port, refused_at) == dict.fromkeys(LANES, frozenset())
    for name in LOGS:
        assert gained(port, refused_at, name) == [], name
    assert port.leases == {}


async def test_approval_during_the_grooming_session_keeps_its_markers_and_frees_the_run(
    monkeypatch,
):
    """Approval landing inside the grooming session stops nothing the session does.

    The session labels through its own tools and kodezart writes nothing
    after it, so the tick ends with the pre-approval marker on every member
    and no lease anywhere; the run the same approval admits then stages every
    member and walks.
    """
    port = triaged(standing_board(LANES))
    operation = standing_operation()
    harness = staging_runtime(
        port, LANES, monkeypatch=monkeypatch, builds=[], operation=operation
    )
    original = harness.executor.stream
    landed = []

    async def approving(**kwargs):
        if is_organize_session(kwargs) and not landed:
            approve(port)
            landed.append(before(port))
        async for event in original(**kwargs):
            yield event

    monkeypatch.setattr(harness.executor, "stream", approving)
    tick = before(port)
    assert await grooming(harness, operation).run(NOW) is PassRun.RAN
    assert landed, "no grooming session ran"
    assert_wrote_only(port, tick, dict.fromkeys(LANES, (GROOM_MARKER,)))
    assert port.leases == {}

    run = before(port)
    events = await bounded_walk(harness, job="approved-run")
    assert errors(events) == []
    assert_wrote_only(
        port, run, dict.fromkeys(LANES, (TICKET_MARKER, STAGED)), closed=LANES
    )
    assert markers(port) == {
        key: frozenset({GROOM_MARKER, TICKET_MARKER, STAGED}) for key in LANES
    }
    # Three ticks: the first two fire A and then B, and the third observes
    # both dispatched with nothing left to offer.
    assert len(ticks_of(events)) == 3
    assert port.leases == {}


async def test_an_approved_scope_runs_its_stages_and_the_grooming_tick_takes_no_lease(
    monkeypatch,
):
    """After approval the pre-approval row has nobody to act on at all.

    It opens no session, writes nothing and takes no lease, so the only
    markers on the board are the two the run's stages wrote.
    """
    port = triaged(standing_board(LANES))
    operation = standing_operation()
    harness = staging_runtime(
        port, LANES, monkeypatch=monkeypatch, builds=[], operation=operation
    )
    approve(port)
    run = before(port)
    events = await bounded_walk(harness, job="staged-run")
    assert errors(events) == []
    # Three ticks: the first two fire A and then B, and the third observes
    # both dispatched with nothing left to offer.
    assert len(ticks_of(events)) == 3
    assert_wrote_only(
        port, run, dict.fromkeys(LANES, (TICKET_MARKER, STAGED)), closed=LANES
    )
    spent = len(harness.executor.organize_calls)

    tick = before(port)
    assert await grooming(harness, operation).run(NOW) is PassRun.RAN
    assert len(harness.executor.organize_calls) == spent
    assert_wrote_only(port, tick, {})
    # Taken and released inside the tick would leave the live map empty;
    # the journals are what show the tick took none at all.
    assert gained(port, tick, "lease_writes") == []
    assert gained(port, tick, "lease_releases") == []
    assert port.leases == {}
