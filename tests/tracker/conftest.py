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

from kodezart.adapters.linear.tracker import LinearMcpTracker
from kodezart.core.backoff import RetryPolicy
from kodezart.core.protocols import TrackerPort
from kodezart.domain.approval_alias import aliases_approval_member
from kodezart.types.domain.dispatch import PassSignal, SelfWriteLedger
from kodezart.types.domain.operation import LifecycleStage, ScopeLabel
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import (
    SurfaceKind,
    WritableSurface,
)
from kodezart.types.domain.tracker import IssueQuery, ReviewQuery
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
#: A criterion sub-issue of the asset-carrying issue, whose body a member
#: of the workspace wrote.  A criterion's body is the surface a run amends
#: under its own grant, so a workspace holding none written by a person
#: cannot express what replacing a principal's criterion would be.
CRITERION_ISSUE = "FIX-4"
CRITERION_BODY = (
    "**Check:** a member's predicate\n\n**Do:** their guidance\n\n**Evidence:**\n"
)
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
                id=CRITERION_ISSUE,
                title="a criterion a member wrote",
                description=CRITERION_BODY,
                created_by=BYSTANDER,
                priority_raw=2,
                status="Todo",
                status_type="unstarted",
                parent_id=ASSET_ISSUE,
                labels=[ISSUE_LABELS["criterion"]],
                created_at=FIXTURE_NOW - timedelta(days=2),
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


def linear_over_fake_mcp(
    server: FakeLinearMcpServer,
    *,
    scope_labels: Mapping[str, str] | None = None,
    clock: Callable[[], datetime] = _frozen_now,
) -> TrackerPort:
    """The shipped Linear adapter, dialing the in-process fake MCP server."""
    return LinearMcpTracker(
        marker_prefixes=MARKER_PREFIXES,
        issue_labels={**ISSUE_LABELS, FIRE_STAGE_KEY: FIRE_STAGE_LABEL},
        criteria_stage_label_key=FIRE_STAGE_KEY,
        scope_labels=scope_labels if scope_labels is not None else SCOPE_LABELS,
        caller=server,
        queue_state_labels=QUEUE_STATE_LABELS,
        workflow_state_names=WORKFLOW_STATE_NAMES,
        team_identifiers=TEAM_IDENTIFIERS,
        retry=RetryPolicy(attempts=1, initial_delay=1.0),
        clock=clock,
        ledger=SelfWriteLedger(),
    )


async def _snapshot(
    source: TrackerPort, *, clock: Callable[[], datetime]
) -> FakeTrackerPort:
    """Read the fixture workspace through the adapter into domain objects."""
    keys = [
        issue.issue_key
        for issue in await source.scan_issues(query=IssueQuery(page_size=PAGE_SIZE))
    ]
    issues = [await source.read_issue(issue_key=key) for key in keys]
    # Probed rather than declared, for the reason the rest of this snapshot
    # is read rather than restated: the double must refuse exactly what the
    # workspace behind it refuses.
    refusals = await source.verify_scan_capability(signals=list(PassSignal))
    port = FakeTrackerPort(
        issues=issues,
        marker_prefixes=MARKER_PREFIXES,
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


def approval_classifications(scope_labels: Mapping[str, str] | None) -> frozenset[str]:
    """The classifications this workspace resolves to the approved member.

    Empty for the ordinary vocabulary, where the two namespaces spell
    different labels. A workspace that maps them onto one label has an
    ordinary classification write that would grant admission, and every
    implementation has to know which one that is.
    """
    vocabulary = scope_labels if scope_labels is not None else SCOPE_LABELS
    return frozenset(
        key
        for key, label in ISSUE_LABELS.items()
        if aliases_approval_member(label=label, scope_labels=vocabulary)
    )


async def fake_port_over_fixture(
    server: FakeLinearMcpServer,
    *,
    clock: Callable[[], datetime] = _frozen_now,
    scope_labels: Mapping[str, str] | None = None,
) -> TrackerPort:
    """The consumer double, seeded from the SAME fixture workspace.

    Seeded by reading through the adapter rather than by restating the
    workspace in domain vocabulary: a hand-written second statement of the
    fixture is a place for the two to disagree, and the disagreement would
    show up as the double being wrong about the thing consumers trust it
    for. Use pytest's loop: an ``asyncio.run`` here displaces its current
    loop and can leak that loop's selector sockets between cases.
    """
    port = await _snapshot(
        linear_over_fake_mcp(server, scope_labels=scope_labels, clock=clock),
        clock=clock,
    )
    port.criteria_stage_label_key = FIRE_STAGE_KEY
    port.approval_classifications = approval_classifications(scope_labels)
    port.scope_label_members = {
        ScopeRef(kind=ScopeKind.ISSUE, key=key): frozenset({ScopeLabel.APPROVED})
        for key, issue in server.issues.items()
        if FIRE_SCOPE_LABEL in issue.labels
    }
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


#: Real adapters — every one must serve the fixture workspace unchanged.
TRACKER_ADAPTERS: dict[str, Callable[[TrackerWorkspace], TrackerPort]] = {
    "linear-mcp": lambda workspace: linear_over_fake_mcp(
        workspace.server,
        scope_labels=workspace.scope_labels,
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


@pytest.fixture
def scope_labels(request: pytest.FixtureRequest) -> Mapping[str, str] | None:
    """The admission vocabulary this workspace is dialled with.

    ``None`` unless a case says otherwise, which it does by parametrizing
    this fixture indirectly.  Stated as a fixture rather than as a second
    tracker so a case needing a differently dialled workspace — one that
    spells the approved member the same as a semantic classification, say
    — still runs against every registered implementation, over the one
    ``tracker`` fixture.
    """
    param: Mapping[str, str] | None = getattr(request, "param", None)
    return param


@pytest.fixture(params=sorted(TRACKER_IMPLEMENTATIONS))
async def tracker(
    request: pytest.FixtureRequest,
    server: FakeLinearMcpServer,
    clock: FixtureClock,
    scope_labels: Mapping[str, str] | None,
) -> TrackerPort:
    """Every registered adapter AND double, over one fixture workspace."""
    factory = TRACKER_IMPLEMENTATIONS[request.param]
    port = factory(
        TrackerWorkspace(server=server, clock=clock, scope_labels=scope_labels)
    )
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
        call for call in server.calls if call[0] in {"save_comment", "save_issue"}
    )
