"""The tracker fixture workspace, shared by every adapter under conformance.

The workspace is stated ONCE, in vendor shape, and every adapter's factory
is responsible for producing a ``TrackerPort`` that serves it.  A second
adapter joins the suite by adding one entry to ``TRACKER_ADAPTERS`` — no
test is copied, which is the whole point of a port-level suite.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from inspect import isawaitable

import pytest

from kodezart.adapters.linear.tracker import (
    _TOOL_DELETE_COMMENT,
    _TOOL_SAVE_COMMENT,
    _TOOL_SAVE_ISSUE,
    LinearMcpTracker,
)
from kodezart.core.backoff import RetryPolicy
from kodezart.core.protocols import TrackerPort
from kodezart.types.domain.dispatch import PassSignal, SelfWriteLedger
from kodezart.types.domain.operation import (
    LifecycleStage,
    QueueState,
    ScopeLabel,
    aliases_approval_member,
)
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.surface import (
    SurfaceKind,
    WritableSurface,
)
from kodezart.types.domain.tracker import IssueQuery, ReviewQuery, TrackerIssue
from tests.fakes import (
    FakeLinearMcpServer,
    FakeMcpAsset,
    FakeMcpDiff,
    FakeMcpDocument,
    FakeMcpIssue,
    FakeTrackerPort,
)
from tests.tracker.marker_config import MARKER_PREFIXES

FIXTURE_NOW: datetime = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


@dataclass
class FixtureClock:
    """The conformance clock, movable by the cases that need to cross an expiry.

    Frozen at ``FIXTURE_NOW`` unless a case advances it, so every existing
    case reads the same instant it always did.  A duration is honored by
    the implementation under test, so an expiry is shown by moving the
    clock past it rather than by asking for a degenerate duration.
    """

    now: datetime = FIXTURE_NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, *, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _frozen_now() -> datetime:
    """The instant every caller that states no clock of its own reads."""
    return FIXTURE_NOW


FIRE_SCOPE_LABEL = "execution-consent"
FIRE_STAGE_LABEL = "criteria-prepared"
FIRE_STAGE_KEY = "criteria_ready"
FIRE_ENTRY_LABELS = [FIRE_SCOPE_LABEL, FIRE_STAGE_LABEL]

APPROVER = "fixture-approver"
BYSTANDER = "fixture-bystander"
#: The non-human writer the operation declares.  A workspace member like
#: any other, so a boot over a credential belonging to it has a declared
#: agent identity to recognise itself by.
AGENT_IDENTITY = "fixture-agent"

#: The operation's semantic issue classifications, stated once: the
#: adapter is dialled with them and the double is told which of them a
#: remapped admission vocabulary would collide with.
ISSUE_LABELS: dict[str, str] = {
    "criterion": "acceptance-condition",
}

#: The admission vocabulary this workspace is dialled with unless a case
#: remaps it.  Stated beside the classifications above so the one question
#: a collision asks — do these two namespaces spell the same label — has
#: both of its halves in one place.
SCOPE_LABELS: dict[str, str] = {ScopeLabel.APPROVED.value: FIRE_SCOPE_LABEL}

QUEUE_STATE_LABELS: dict[str, str] = {
    "triage": "queue:triage",
    "proposed": "queue:proposed",
    "approved": "queue:approved",
    "done": "queue:done",
    "decision": "queue:decision",
}
WORKFLOW_STATE_NAMES: dict[LifecycleStage, str] = {
    LifecycleStage.IN_PROGRESS: "In Progress",
    LifecycleStage.IN_REVIEW: "In Review",
    LifecycleStage.DONE: "Done",
}
TEAM_IDENTIFIERS: dict[str, str] = {"engineering": "fixture-team"}

STATE_TYPES: dict[str, str] = {
    "Backlog": "backlog",
    "Todo": "unstarted",
    "In Progress": "started",
    "In Review": "started",
    "Done": "completed",
    "Canceled": "canceled",
    "Duplicate": "duplicate",
}

CLAIMED_ISSUE = "FIX-1"
APPROVED_ISSUE = "FIX-2"
ASSET_ISSUE = "FIX-3"
#: An issue on a team the configuration does not declare.  A workspace
#: holds more than one operation's board, and a fixture holding only one
#: cannot express a container boundary at all — every scan would be trivially
#: within scope and the port's scoping contract would be untested.
FOREIGN_ISSUE = "OTHER-1"
FOREIGN_TEAM = "fixture-other-team"

DOCUMENT_KEY = "doc-1"
DOCUMENT_TITLE = "checkpoint"
DOCUMENT_CONTENT = "fixture document body"
PAGE_SIZE = 50

#: The two repositories the fixture workspace mirrors reviews from.  Two,
#: for the reason there are two teams: a workspace holding one repository
#: cannot express a container boundary at all.
FIXTURE_REPO_URL = "https://example.invalid/fixture-owner/fixture-repo"
OTHER_REPO_URL = "https://example.invalid/fixture-owner/other-repo"
FIXTURE_REVIEW = "fixture-owner/fixture-repo#7"
FOREIGN_REVIEW = "fixture-owner/other-repo#9"

#: The vendor tool each signal's scan is served by.  Restated here rather
#: than read from the adapter's own mapping: a fixture deriving it from the
#: thing under test would agree with it by construction, and a signal
#: routed to the wrong tool would go unnoticed.
SCAN_TOOL_BY_SIGNAL: dict[PassSignal, str] = {
    PassSignal.issues_changed: "list_issues",
    PassSignal.triage_backlog: "list_issues",
    PassSignal.approved_changed: "list_issues",
    PassSignal.reviews_changed: "list_diffs",
}

#: What the vendor answers a call the credential holds no scope for.
SCOPE_DIAGNOSIS = "auth_insufficient_scope: the credential cannot read this"


def fixture_server(
    *,
    scope_refusals: Mapping[str, str] | None = None,
    actor: str = APPROVER,
    clock: Callable[[], datetime] = _frozen_now,
) -> FakeLinearMcpServer:
    """A fresh fake workspace — one per test, never shared.

    *actor* is the account whose credential the workspace is dialled with:
    it authors every comment this server records and it is what the
    current-user read answers.  It defaults to the approver so the
    comment-author assertions elsewhere read the same author they always
    did; a boot case that needs an attributable non-human writer passes
    ``AGENT_IDENTITY``.
    """
    return FakeLinearMcpServer(
        tool_errors=scope_refusals,
        comment_clock=clock,
        diffs=[
            FakeMcpDiff(
                full_identifier=FIXTURE_REVIEW,
                owner="fixture-owner",
                repo="fixture-repo",
                updated_at=FIXTURE_NOW,
            ),
            FakeMcpDiff(
                full_identifier=FOREIGN_REVIEW,
                owner="fixture-owner",
                repo="other-repo",
                updated_at=FIXTURE_NOW - timedelta(days=1),
            ),
        ],
        issues=[
            FakeMcpIssue(
                id=CLAIMED_ISSUE,
                title="claimable",
                description="an issue two passes race for",
                priority_raw=2,
                status="Todo",
                status_type="unstarted",
                labels=["queue:approved", "area:runtime"],
                created_at=FIXTURE_NOW - timedelta(days=3),
                updated_at=FIXTURE_NOW,
            ),
            FakeMcpIssue(
                id=APPROVED_ISSUE,
                title="approved with a blocker",
                description="body",
                priority_raw=1,
                status="Backlog",
                status_type="backlog",
                labels=["queue:approved"],
                relations=[("blockedBy", CLAIMED_ISSUE), ("relatedTo", ASSET_ISSUE)],
                parent_id="FIX-0",
                assignee=BYSTANDER,
                created_at=FIXTURE_NOW - timedelta(days=1),
                updated_at=FIXTURE_NOW,
            ),
            FakeMcpIssue(
                id=ASSET_ISSUE,
                title="carries assets",
                # The one surface in this workspace a member other than
                # the dialled account wrote.  Every other body reads as
                # this writer's own, so a case about replacing somebody
                # else's words has an address and the ordinary cases keep
                # the workspace they always had.
                description="words a member of this workspace wrote",
                created_by=BYSTANDER,
                priority_raw=0,
                status="Done",
                status_type="completed",
                labels=["queue:done"],
                attachments=[
                    FakeMcpAsset(
                        id="asset-1",
                        title="spec.pdf",
                        url="https://tracker.invalid/asset-1",
                    ),
                ],
                documents=[
                    FakeMcpAsset(
                        id=DOCUMENT_KEY,
                        title=DOCUMENT_TITLE,
                        url="https://tracker.invalid/doc-1",
                    ),
                ],
                created_at=FIXTURE_NOW - timedelta(days=10),
                updated_at=FIXTURE_NOW,
            ),
            FakeMcpIssue(
                id=FOREIGN_ISSUE,
                title="another board's issue",
                description="approved by the same person, on another team",
                priority_raw=1,
                status="Todo",
                status_type="unstarted",
                team=FOREIGN_TEAM,
                labels=["queue:approved"],
                created_at=FIXTURE_NOW - timedelta(days=30),
                updated_at=FIXTURE_NOW,
            ),
        ],
        documents=[
            FakeMcpDocument(
                id=DOCUMENT_KEY,
                title=DOCUMENT_TITLE,
                content=DOCUMENT_CONTENT,
            ),
        ],
        users=[APPROVER, BYSTANDER, AGENT_IDENTITY],
        teams=["fixture-team", FOREIGN_TEAM],
        labels=list(QUEUE_STATE_LABELS.values()),
        # Both boards in the fixture workspace offer the whole vocabulary:
        # the states are read per team, and a fixture where they differed
        # would make the ordinary case the divergent one.
        statuses={team: list(STATE_TYPES) for team in ("fixture-team", FOREIGN_TEAM)},
        state_types=STATE_TYPES,
        actor=actor,
    )


#: The classification vocabulary a workspace is dialled with unless a case
#: states its own: the operation's classifications plus the run-stage
#: marker the fire read is keyed on.
FIXTURE_ISSUE_LABELS: dict[str, str] = {
    **ISSUE_LABELS,
    FIRE_STAGE_KEY: FIRE_STAGE_LABEL,
}


def linear_over_fake_mcp(
    server: FakeLinearMcpServer,
    *,
    scope_labels: Mapping[str, str] | None = None,
    issue_labels: Mapping[str, str] | None = None,
    criteria_stage_label_key: str | None = None,
    clock: Callable[[], datetime] = _frozen_now,
) -> TrackerPort:
    """The shipped Linear adapter, dialing the in-process fake MCP server.

    *issue_labels* and *criteria_stage_label_key* are the classification
    vocabulary and the run-stage marker key this workspace is dialled
    with. A case stating an organize mandate table of its own has to dial
    the marker keys that table names, and it has to dial them into EVERY
    registered implementation rather than into an adapter it built beside
    the case — which is what threading them through the workspace is for.
    """
    return LinearMcpTracker(
        marker_prefixes=MARKER_PREFIXES,
        issue_labels=dict(
            issue_labels if issue_labels is not None else FIXTURE_ISSUE_LABELS
        ),
        criteria_stage_label_key=(
            criteria_stage_label_key
            if criteria_stage_label_key is not None
            else FIRE_STAGE_KEY
        ),
        scope_labels=scope_labels if scope_labels is not None else SCOPE_LABELS,
        caller=server,
        queue_state_labels=QUEUE_STATE_LABELS,
        workflow_state_names=WORKFLOW_STATE_NAMES,
        team_identifiers=TEAM_IDENTIFIERS,
        retry=RetryPolicy(attempts=1, initial_delay=1.0),
        clock=clock,
        ledger=SelfWriteLedger(),
    )


async def _container_ancestry(
    source: TrackerPort, *, issues: Sequence[TrackerIssue]
) -> dict[ScopeRef, ScopeContainer]:
    """The approval containers the snapshot's own members report, read through.

    A member reporting a project puts that project's label level into
    every approval reading made about it, so a double seeded from a
    workspace whose issues carry one has to hold the container too or it
    answers a question the adapter answers from the backend. Read rather
    than restated, for the reason the rest of the snapshot is read.

    The walk is bounded by the refs already seen: a backend answering a
    cycle of parents ends the walk instead of extending it.
    """
    frontier = [
        ScopeRef(kind=ScopeKind.PROJECT, key=key)
        for issue in issues
        if (key := issue.project_id) is not None
    ]
    containers: dict[ScopeRef, ScopeContainer] = {}
    while frontier:
        ref = frontier.pop()
        if ref in containers:
            continue
        container = await source.container_metadata(ref=ref)
        containers[ref] = container
        if container.parent is not None:
            frontier.append(container.parent)
    return containers


async def _snapshot(
    source: TrackerPort, *, clock: Callable[[], datetime]
) -> FakeTrackerPort:
    """Read the fixture workspace through the adapter into domain objects."""
    keys = [
        issue.issue_key
        for issue in await source.scan_issues(query=IssueQuery(page_size=PAGE_SIZE))
    ]
    issues = [await source.read_issue(issue_key=key) for key in keys]
    containers = await _container_ancestry(source, issues=issues)
    # Probed rather than declared, for the reason the rest of this snapshot
    # is read rather than restated: the double must refuse exactly what the
    # workspace behind it refuses.
    refusals = await source.verify_scan_capability(signals=list(PassSignal))
    port = FakeTrackerPort(
        issues=issues,
        marker_prefixes=MARKER_PREFIXES,
        scope_containers=list(containers.values()),
        assets={key: await source.list_issue_assets(issue_key=key) for key in keys},
        documents={
            DOCUMENT_KEY: await source.read_document(document_key=DOCUMENT_KEY),
        },
        recorded_work_refs={key: await source.work_refs(issue_key=key) for key in keys},
        recorded_base_specs={
            key: spec
            for key in keys
            if (spec := await source.read_base_spec(issue_key=key)) is not None
        },
        scan_refusals=refusals,
        writer_identities=await source.writer_identity(),
        known_identifiers=[
            *(APPROVER, BYSTANDER, AGENT_IDENTITY),
            *TEAM_IDENTIFIERS.values(),
            *QUEUE_STATE_LABELS.values(),
            *WORKFLOW_STATE_NAMES.values(),
        ],
        clock=clock,
    )
    port.body_authorship = {
        key: await source.read_surface_authorship(
            surface=WritableSurface(
                kind=SurfaceKind.ISSUE_DESCRIPTION,
                ref=ScopeRef(kind=ScopeKind.ISSUE, key=key),
            )
        )
        for key in keys
    }
    port.issue_state_changes = {
        key: (await source.read_issue_state_change(issue_key=key)).state_changed_at
        for key in keys
    }
    # A credential refused the review scan cannot read one, so the double it
    # seeds holds none — the same state the workspace behind it presents.
    if PassSignal.reviews_changed not in refusals:
        for repo_url in (FIXTURE_REPO_URL, OTHER_REPO_URL):
            port.reviews[repo_url] = list(
                await source.scan_reviews(
                    query=ReviewQuery(repo_url=repo_url, page_size=PAGE_SIZE),
                ),
            )
    return port


def approval_classifications(
    scope_labels: Mapping[str, str] | None,
    *,
    issue_labels: Mapping[str, str] | None = None,
) -> frozenset[str]:
    """The classifications this workspace resolves to the approved member.

    Empty for the ordinary vocabulary, where the two namespaces spell
    different labels. A workspace that maps them onto one label has an
    ordinary classification write that would grant admission, and every
    implementation has to know which one that is.

    Both namespaces are read from the workspace rather than one of them
    from the module default: a case that remaps its classifications and a
    case that remaps its admission vocabulary ask the same question, and
    an answer derived from half the workspace would be right for one of
    them by accident.
    """
    approved = (scope_labels or SCOPE_LABELS).get(ScopeLabel.APPROVED.value)
    return frozenset(
        key
        for key, label in (
            issue_labels if issue_labels is not None else ISSUE_LABELS
        ).items()
        if approved is not None and label == approved
    )


def approval_queue_states(
    scope_labels: Mapping[str, str] | None,
) -> frozenset[QueueState]:
    """The queue members this workspace resolves to the approved member.

    Empty for the ordinary vocabulary. The queue mapping is this module's
    constant, so only the admission vocabulary can move the two onto one
    label, which is what a colliding workspace dials.
    """
    return frozenset(
        QueueState(name)
        for name, label in QUEUE_STATE_LABELS.items()
        if aliases_approval_member(
            label=label, scope_labels=scope_labels or SCOPE_LABELS
        )
    )


async def fake_port_over_fixture(
    server: FakeLinearMcpServer,
    *,
    clock: Callable[[], datetime] = _frozen_now,
    scope_labels: Mapping[str, str] | None = None,
    issue_labels: Mapping[str, str] | None = None,
    criteria_stage_label_key: str | None = None,
) -> TrackerPort:
    """The consumer double, seeded from the SAME fixture workspace.

    Seeded by reading through the adapter rather than by restating the
    workspace in domain vocabulary: a hand-written second statement of the
    fixture is a place for the two to disagree, and the disagreement would
    show up as the double being wrong about the thing consumers trust it
    for. Use pytest's loop: an ``asyncio.run`` here displaces its current
    loop and can leak that loop's selector sockets between cases.
    """
    adapter = linear_over_fake_mcp(
        server,
        scope_labels=scope_labels,
        issue_labels=issue_labels,
        criteria_stage_label_key=criteria_stage_label_key,
        clock=clock,
    )
    port = await _snapshot(adapter, clock=clock)
    port.criteria_stage_label_key = (
        criteria_stage_label_key
        if criteria_stage_label_key is not None
        else FIRE_STAGE_KEY
    )
    port.approval_classifications = approval_classifications(
        scope_labels, issue_labels=issue_labels
    )
    port.approval_queue_states = approval_queue_states(scope_labels)
    approved_label = (scope_labels or SCOPE_LABELS).get(ScopeLabel.APPROVED.value)
    port.scope_label_members = {
        ScopeRef(kind=ScopeKind.ISSUE, key=key): frozenset({ScopeLabel.APPROVED})
        for key, issue in server.issues.items()
        if approved_label is not None and approved_label in issue.labels
    }
    # The container label levels, read through the adapter for the reason
    # the containers themselves are: approval on a project is what covers
    # that project's members, and a double blind to it would answer every
    # cascade reading "absent" while the adapter answered from the board.
    for ref in port.scope_containers:
        port.scope_label_members[ref] = await adapter.read_scope_labels(ref=ref)
    return port


@dataclass
class TrackerWorkspace:
    """Everything a registered factory needs to serve one case.

    Stated as one object rather than as a widening positional signature so
    a case that dials a workspace differently — a remapped scope
    vocabulary, say — still reaches every registered implementation
    through the registry instead of building a pair of its own beside it.
    """

    server: FakeLinearMcpServer
    clock: FixtureClock
    scope_labels: Mapping[str, str] | None = None
    #: The classification vocabulary and the run-stage marker key. A case
    #: running the organize stages states the mandate table's own marker
    #: keys here, so both arms are dialled with the one vocabulary the
    #: table names instead of the adapter arm carrying it alone.
    issue_labels: Mapping[str, str] | None = None
    criteria_stage_label_key: str | None = None


#: Real adapters — every one must serve the fixture workspace unchanged.
TRACKER_ADAPTERS: dict[str, Callable[[TrackerWorkspace], TrackerPort]] = {
    "linear-mcp": lambda workspace: linear_over_fake_mcp(
        workspace.server,
        scope_labels=workspace.scope_labels,
        issue_labels=workspace.issue_labels,
        criteria_stage_label_key=workspace.criteria_stage_label_key,
        clock=workspace.clock,
    ),
}

#: Test doubles that consumers are tested on.  They run the SAME suite, per
#: the ruling that this is what keeps them honest: a double that drifts from
#: the contract fails exactly where a non-conforming vendor adapter would.
TRACKER_DOUBLES: dict[str, Callable[[TrackerWorkspace], Awaitable[TrackerPort]]] = {
    "fake-port": lambda workspace: fake_port_over_fixture(
        workspace.server,
        clock=workspace.clock,
        scope_labels=workspace.scope_labels,
        issue_labels=workspace.issue_labels,
        criteria_stage_label_key=workspace.criteria_stage_label_key,
    ),
}

TRACKER_IMPLEMENTATIONS: dict[
    str, Callable[[TrackerWorkspace], TrackerPort | Awaitable[TrackerPort]]
] = {
    **TRACKER_ADAPTERS,
    **TRACKER_DOUBLES,
}


@pytest.fixture
def refused_signals(request: pytest.FixtureRequest) -> tuple[PassSignal, ...]:
    """The signals this workspace's credential holds no scope to scan for.

    Empty unless a case says otherwise, which it does by parametrizing this
    fixture indirectly.  Stated as a fixture rather than as a second builder
    so a case needing a refusing workspace still runs against every
    registered implementation, over the one ``tracker`` fixture.
    """
    param: Sequence[PassSignal] = getattr(request, "param", ())
    return tuple(param)


@pytest.fixture
def server(
    refused_signals: tuple[PassSignal, ...], clock: FixtureClock
) -> FakeLinearMcpServer:
    """A fresh fixture workspace, on the same clock the holders read.

    The backend's stamps are what ownership is arbitrated by, so a
    workspace whose comment log ran on a clock of its own would answer
    every question about expiry with a skew no deployment has.
    """
    return fixture_server(
        scope_refusals={
            SCAN_TOOL_BY_SIGNAL[signal]: SCOPE_DIAGNOSIS for signal in refused_signals
        },
        clock=clock,
    )


@pytest.fixture
def clock() -> FixtureClock:
    """One movable clock per case, shared by the implementation under test."""
    return FixtureClock()


@pytest.fixture(params=sorted(TRACKER_IMPLEMENTATIONS))
async def tracker(
    request: pytest.FixtureRequest,
    server: FakeLinearMcpServer,
    clock: FixtureClock,
) -> TrackerPort:
    """Every registered adapter AND double, over one fixture workspace."""
    factory = TRACKER_IMPLEMENTATIONS[request.param]
    port = factory(TrackerWorkspace(server=server, clock=clock))
    return await port if isawaitable(port) else port


@pytest.fixture(params=sorted(TRACKER_ADAPTERS))
def adapter(
    request: pytest.FixtureRequest,
    server: FakeLinearMcpServer,
    clock: FixtureClock,
) -> TrackerPort:
    """Registered ADAPTERS only — for rules about backend substitutability."""
    factory = TRACKER_ADAPTERS[request.param]
    return factory(TrackerWorkspace(server=server, clock=clock))


@pytest.fixture
def tracker_writes(
    tracker: TrackerPort, server: FakeLinearMcpServer
) -> Callable[[], tuple[object, ...]]:
    """Observe actual mutation calls independently of the port's return values."""
    return observed_writes(tracker, server)


#: The adapter's mutation tools, read off the adapter's OWN names rather
#: than spelled here.  Three, not two: the comment delete is a write like
#: the save is, and an observation that counted the saves alone reported a
#: released lease or a withdrawn comment as no write at all.  Derived from
#: the constants so a tool renamed in the adapter and left behind here
#: cannot quietly narrow what a case is allowed to call untouched.
ADAPTER_WRITE_TOOLS: frozenset[str] = frozenset(
    {_TOOL_SAVE_ISSUE, _TOOL_SAVE_COMMENT, _TOOL_DELETE_COMMENT}
)


def observed_writes(
    tracker: TrackerPort, server: FakeLinearMcpServer
) -> Callable[[], tuple[object, ...]]:
    """The same observation, for a case dialling its own workspace.

    Stated as a function beside the fixture so a case parametrised over
    the registry with a remapped vocabulary observes mutations the one
    way, rather than growing a second idea of what a write is.
    """
    if isinstance(tracker, FakeTrackerPort):
        return lambda: (
            *tracker.comment_writes,
            *tracker.issue_writes,
            *tracker.issue_creations,
            *tracker.workflow_writes,
            *tracker.restored_states,
            *tracker.queue_writes,
            *tracker.classification_writes,
        )
    return lambda: tuple(
        call for call in server.calls if call[0] in ADAPTER_WRITE_TOOLS
    )
