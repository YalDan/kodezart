"""Linear tracker adapter behind the programmatic MCP port.

The adapter owns native identifiers, label/state mappings and tool calls.
Ownership — a claim on an issue, a lease over a set of write surfaces —
is arbitrated by what the backend actually provides: creations it orders
and stamps, a listing that answers with what it holds, an edit that keeps
a comment's place, and deletion.  There is no conditional write, so no
grant is believed from the echo of its own write: every holder re-reads
the whole live set and keeps only what that read confirms.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Final, assert_never
from urllib.parse import quote
from uuid import uuid4

from pydantic import ValidationError

from kodezart.adapters.linear_history_receipt import state_history_receipt
from kodezart.adapters.linear_issue_identity import LinearIssueIdentityCarrier
from kodezart.adapters.linear_markers import LinearMarkers
from kodezart.adapters.linear_mcp_types import (
    LINEAR_NAMED_ARRAY,
    LINEAR_WORKFLOW_STATES,
    LinearAssetWire,
    LinearCommentEntryWire,
    LinearCommentListWire,
    LinearCommentWire,
    LinearCriterionIssueWire,
    LinearDiffListWire,
    LinearDocumentListWire,
    LinearDocumentSummaryWire,
    LinearDocumentWire,
    LinearIssueDetailWire,
    LinearIssueListWire,
    LinearIssueStateHistoryWire,
    LinearIssueWire,
    LinearLabelListWire,
    LinearLabelWire,
    LinearNamedWire,
    LinearPlanningIssueWire,
    LinearProjectWire,
    LinearTeamListWire,
    LinearTeamWire,
    LinearUserListWire,
    LinearUserWire,
    LinearWireModel,
)
from kodezart.adapters.linear_scope_reader import SCOPE_READ_TOOLS, LinearScopeReader
from kodezart.adapters.linear_scope_types import (
    LinearApprovalIssueWire,
    LinearScopeIssuesWire,
)
from kodezart.adapters.pagination import cursor_pages
from kodezart.core.backoff import RetryPolicy
from kodezart.core.errors import (
    McpCallUnansweredError,
    McpCredentialRefusedError,
    McpTransportError,
    TrackerAccessDeniedError,
    TrackerBootValidationError,
    TrackerEnsureConflictError,
    TrackerProtocolError,
    TrackerUnavailableError,
)
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import McpToolCaller, McpToolResult
from kodezart.domain.criterion_creation import criterion_body, existing_criterion
from kodezart.domain.errors import (
    CriterionReadError,
    DuplicateIssueIdentityError,
    DuplicateWorkRefError,
    EscalationReadError,
    IssueLabelReadError,
    ScopeReadError,
    SurfaceLeaseError,
    SurfaceWriteAttributionError,
    TransientAPIError,
)
from kodezart.domain.escalation_resolution import resolution_from_comments
from kodezart.domain.fire_spec import require_fire_entry, tracker_spec_from_issues
from kodezart.domain.git_url import extract_owner_repo
from kodezart.domain.run_alarm_record import (
    parse_run_alarm,
    render_run_alarm,
    run_alarm_marker,
)
from kodezart.domain.run_event_stream import (
    LaneRunEvent,
    lane_run_events,
    render_run_event,
)
from kodezart.domain.scope_approval import resolve_execution_approval
from kodezart.domain.surface_lease import (
    live_conflict,
    one_ownership,
    renewed_deadline,
    renews,
    surface_address,
)
from kodezart.domain.tracker_writes import (
    comment_under_marker,
    description_replacement,
    marked_comment_body,
    require_expected_comment,
)
from kodezart.types.domain.branch import BaseSpec, WorkRef, WorkRefLanding, WorkRefRole
from kodezart.types.domain.dispatch import PassSignal, SelfWriteLedger
from kodezart.types.domain.escalation import EscalationResolution
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.issue_identity import IssueIdentity
from kodezart.types.domain.operation import (
    LifecycleStage,
    OperationMemberAbsentError,
    QueueState,
    ScopeLabel,
)
from kodezart.types.domain.run_alarm import AlarmSignal, AlarmSubject, RunAlarm
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.self_writes import (
    CommentValues,
    IssueMovementSnapshot,
    OwnMutation,
    field_value,
    field_values,
)
from kodezart.types.domain.surface import SurfaceKind, SurfaceLease, WritableSurface
from kodezart.types.domain.tracker import (
    INSTATABLE_MAPPING_KINDS,
    ClaimResult,
    ClaimStatus,
    EnsureAction,
    IssuePriority,
    IssueQuery,
    IssueRelation,
    IssueRelationKind,
    MappingKind,
    MappingOutcome,
    MappingRef,
    ReviewQuery,
    TrackerAsset,
    TrackerComment,
    TrackerIssue,
    TrackerIssueRevision,
    TrackerIssueStateChange,
    TrackerReview,
    WorkflowStateKind,
)
from kodezart.types.domain.tracker_writes import DescriptionEditResult

_TOOL_LIST_ISSUES = "list_issues"
_TOOL_LIST_DIFFS = "list_diffs"
_ORDER_BY_UPDATED_AT = "updatedAt"
_TOOL_GET_ISSUE = "get_issue"
_TOOL_SAVE_ISSUE = "save_issue"
_TOOL_SAVE_COMMENT = "save_comment"
_TOOL_LIST_COMMENTS = "list_comments"
_TOOL_DELETE_COMMENT = "delete_comment"
_TOOL_GET_DOCUMENT = "get_document"
_TOOL_LIST_DOCUMENTS = "list_documents"
_TOOL_SAVE_DOCUMENT = "save_document"
_TOOL_GET_PROJECT = "get_project"
_TOOL_LIST_USERS = "list_users"
_TOOL_GET_USER = "get_user"
_TOOL_LIST_TEAMS = "list_teams"
_TOOL_LIST_ISSUE_LABELS = "list_issue_labels"
_TOOL_CREATE_ISSUE_LABEL = "create_issue_label"
_TOOL_LIST_PROJECT_LABELS = "list_project_labels"
_TOOL_SAVE_PROJECT_LABEL = "save_project_label"
_TOOL_LIST_INITIATIVE_LABELS = "list_initiative_labels"
_TOOL_CREATE_INITIATIVE_LABEL = "create_initiative_label"
_TOOL_LIST_ISSUE_STATUSES = "list_issue_statuses"

#: The save arguments that REWRITE an issue's body, against the one that
#: moves its workflow state.  The vendor's single save takes them together
#: and applies them as one act — which is exactly what this deployment
#: never asks it for.
_BODY_SAVE_ARGUMENTS: Final[frozenset[str]] = frozenset({"description", "patch"})
_STATE_SAVE_ARGUMENT: Final[str] = "state"

#: One configured scope label has a separate native definition per kind.
#: Project creation uses the connected app's declared save tool with no id;
#: its availability to the deployment's service credential is unverified.
_SCOPE_LABEL_CREATORS: Final[dict[str, str]] = {
    _TOOL_LIST_ISSUE_LABELS: _TOOL_CREATE_ISSUE_LABEL,
    _TOOL_LIST_PROJECT_LABELS: _TOOL_SAVE_PROJECT_LABEL,
    _TOOL_LIST_INITIATIVE_LABELS: _TOOL_CREATE_INITIATIVE_LABEL,
}

#: The tools that change nothing on the board.  A call the server may have
#: performed is made again only if performing it twice is the same as once:
#: these are, and every other tool is a write.
_READ_TOOLS: Final[frozenset[str]] = SCOPE_READ_TOOLS | frozenset(
    {
        _TOOL_LIST_ISSUES,
        _TOOL_LIST_DIFFS,
        _TOOL_GET_ISSUE,
        _TOOL_LIST_COMMENTS,
        _TOOL_GET_DOCUMENT,
        _TOOL_LIST_DOCUMENTS,
        _TOOL_GET_PROJECT,
        _TOOL_LIST_USERS,
        _TOOL_GET_USER,
        _TOOL_LIST_TEAMS,
        _TOOL_LIST_ISSUE_LABELS,
        _TOOL_LIST_PROJECT_LABELS,
        _TOOL_LIST_INITIATIVE_LABELS,
        _TOOL_LIST_ISSUE_STATUSES,
    },
)

#: What the user read is asked for the account the credential belongs to:
#: the tool takes one query and answers the caller's own user for this
#: value.
_CURRENT_USER_QUERY = "me"

#: The page a capability probe asks for: the smallest a listing tool takes.
#: The probe is about reachability, so a second row would be paid for and
#: read by nobody.
_SCOPE_PROBE_LIMIT = 1

# The vendor's maximum issue page, not a bound on the identity lookup.
_ISSUE_IDENTITY_PAGE_SIZE = 250

#: What the vendor's own diagnosis says when a credential lacks the scope a
#: tool needs.  Matched on the error the transport already carries, because
#: that string is the only place the distinction appears: a scope refusal
#: and an outage arrive as the same exception type.  This marker and
#: nothing broader — a status code says a request was refused and not that
#: a scope is missing, so matching one would invent a diagnosis out of a
#: failure nobody made.
_SCOPE_REFUSAL_MARKER = "auth_insufficient_scope"

#: The vendor's long-lived personal key: the prefix it is minted with, and
#: the shortest body one has ever been measured at.  Measured 2026-09-01:
#: the operator's live key is ``lin_api_`` followed by forty
#: characters and answered ``initialize`` with HTTP 200.  Wire format, not
#: knobs — a deployment cannot choose what the vendor mints.
#:
#: The length is a FLOOR and never an equality.  What the refusal is for is
#: the lifetime split, and the prefix carries all of it: an OAuth access
#: token expires and nothing in this process renews it, a personal key does
#: not.  The length carries one thing more — that this is not a truncated
#: paste — and a vendor lengthening its own key must not brick every boot
#: on a credential that works.
_PERSONAL_KEY_PREFIX = "lin_api_"
_PERSONAL_KEY_MIN_BODY = 40

#: What a refusal quotes back, so an operator reads what to mint rather
#: than what was wrong with what they had.  Prose, because it is printed in
#: an error and stated in the setup guide, and derived from the two
#: constants above so the sentence cannot outlive the rule.
ACCEPTED_CREDENTIAL_SHAPE: Final[str] = (
    f"{_PERSONAL_KEY_PREFIX} followed by at least {_PERSONAL_KEY_MIN_BODY} characters"
)

_PRIORITY_BY_RAW: Mapping[int, IssuePriority] = {
    0: IssuePriority.NONE,
    1: IssuePriority.URGENT,
    2: IssuePriority.HIGH,
    3: IssuePriority.MEDIUM,
    4: IssuePriority.LOW,
}
_RAW_BY_PRIORITY: Mapping[IssuePriority, int] = {
    priority: raw for raw, priority in _PRIORITY_BY_RAW.items()
}

#: The workflow-state kinds the domain carries, keyed by the value the
#: vendor spells them with.  Derived from the enum, so the vocabulary this
#: adapter recognises cannot drift from the one consumers branch on.
_STATE_KIND_BY_VALUE: Mapping[str, WorkflowStateKind] = {
    kind.value: kind for kind in WorkflowStateKind
}

#: What each arm of the vendor's relations object means in the domain,
#: keyed by the vendor's own spelling.  Four arms, four kinds: the vendor
#: reports a parent as ``parentId`` on the issue itself, which the adapter
#: carries as ``parent_key`` rather than as an edge, and reports children
#: nowhere at all.
_RELATION_KIND_BY_ARM: Mapping[str, IssueRelationKind] = {
    "blocks": IssueRelationKind.BLOCKS,
    "blockedBy": IssueRelationKind.BLOCKED_BY,
    "relatedTo": IssueRelationKind.RELATED,
    "duplicateOf": IssueRelationKind.DUPLICATE,
}

_MAPPING_TOOL_BY_KIND: Mapping[MappingKind, str] = {
    MappingKind.USER: _TOOL_LIST_USERS,
    MappingKind.TEAM: _TOOL_LIST_TEAMS,
    MappingKind.QUEUE_STATE: _TOOL_LIST_ISSUE_LABELS,
    MappingKind.WORKFLOW_STATE: _TOOL_LIST_ISSUE_STATUSES,
}

_WORK_REF_ROLE_BY_VALUE: Mapping[str, WorkRefRole] = {
    role.value: role for role in WorkRefRole
}


def _label_arguments(identifier: str, container: str | None) -> dict[str, object]:
    """Create-arguments for one queue-state label.

    *container* is the team's UUID, which is the only thing ``teamId``
    accepts: its declared input schema says "Team UUID (omit for workspace
    label)", and the live server answers a name with ``teamId must be a
    UUID`` and a 400.  ``None`` creates the label at workspace scope, which
    is what an operation declaring no team at all gets; a declared team's
    ref names that team and the label is made inside it, one per board.
    """
    arguments: dict[str, object] = {"name": identifier}
    if container is not None:
        arguments["teamId"] = container
    return arguments


def _without_mention_syntax(identity: str) -> str:
    """*identity* with the vendor's mention syntax off it: one leading ``@``.

    A configured identity may be spelled the way a routine text mentions
    it, because the byte-identity gate on the pass templates wants the
    config to hold the literal those texts substitute.  The ``@`` is
    SYNTAX and the identity is what follows it, so exactly one comes off:
    a second ``@`` belongs to the name being claimed, not to a second
    mention marker.

    Nothing else is normalised here — case in particular.  Whether a
    lowercased identity is a config defect or a prose-versus-identity
    distinction is a question about that config, and folding it here
    would answer it silently for every workspace.
    """
    return identity.removeprefix("@")


def is_long_lived_credential(token: str) -> bool:
    """Whether *token* is the vendor's long-lived personal-key shape.

    The vendor takes exactly two kinds of credential in the same header,
    measured 2026-09-01: a personal key, which carries no expiry
    at all, and an OAuth access token, which does and which nothing in this
    process refreshes.  The access token is OPAQUE — it declares nothing a
    reader can inspect — so the only sound split is the shape that is known
    to outlive a boot against everything else, and it is stated here
    because the shapes are this backend's vocabulary and no other layer's.

    A positive answer is the one credential a deployment may boot on.  Every
    other string — an ``lin_oauth_`` token, a truncated key, a paste of
    something else entirely — is refused, so a boot cannot proceed on a
    credential whose lifetime this process cannot see or renew.
    """
    if not token.startswith(_PERSONAL_KEY_PREFIX):
        return False
    return len(token) - len(_PERSONAL_KEY_PREFIX) >= _PERSONAL_KEY_MIN_BODY


def _utc_now() -> datetime:
    """Current instant in UTC — the adapter's default clock."""
    return datetime.now(tz=UTC)


@dataclass
class _LabelListings:
    """Every label listing this adapter read, classified by what defines it.

    ``list_issue_labels`` answers a DIFFERENT set depending on whether
    ``team`` was sent, and the two answers are not a partition.  Measured
    2026-09-01: the unscoped call answers with the workspace-level labels
    ALONE, while a team's call answers with that team's own labels AND the
    workspace-level ones.  So a name in a team's answer proves nothing by
    itself — it is either one workspace label reaching that board or a
    team-scoped copy sitting beside it — and only the ID tells those
    apart: one member came back from both boards under a single id, while
    another came back under two distinct ones.

    ``by_team`` therefore holds each team's OWN labels: that team's
    listing MINUS the workspace listing, subtracted by id.  Taking the
    team's answer whole read every workspace label as team-held and
    refused a healthy two-board workspace on its own approval label.

    The entries' ``teamId`` is never consulted for any of this.  No
    measured listing carries the field at all, so reading it would file
    every team-scoped label under workspace scope: the misreading that
    made a freshly created label invisible to the boot that created it.
    """

    workspace: set[str]
    by_team: dict[str, set[str]]

    def names(self) -> frozenset[str]:
        """Every label name any listing answered with."""
        return frozenset(self.workspace).union(*self.by_team.values())

    def serves(self, name: str, scope: str | None) -> bool:
        """Whether *name* as already held serves a ref declaring *scope*.

        A workspace-level label serves a ref on any team — it is
        addressable on every board — and a team's own label serves only a
        ref declaring that team.  Another declared team's label serves
        neither: it is that board's definition of the member, and a ref for
        this board is answered by making this board its own.
        """
        return name in self.workspace or (
            scope is not None and name in self.by_team.get(scope, set())
        )

    def workspace_holds(self, name: str) -> bool:
        """Whether the UNSCOPED listing carried *name*."""
        return name in self.workspace

    def teams_holding(self, name: str) -> tuple[str, ...]:
        """The declared teams defining *name* THEMSELVES, workspace aside.

        A team whose listing carries *name* only because the workspace
        defines it is not one of these: it holds no definition of its own,
        and counting it would read every workspace label as contested.
        """
        return tuple(
            sorted(team for team, names in self.by_team.items() if name in names)
        )

    def record(self, name: str, scope: str | None) -> None:
        """Hold a label this adapter just created, in the scope it made it."""
        if scope is None:
            self.workspace.add(name)
        else:
            self.by_team.setdefault(scope, set()).add(name)


class _GrantKind(StrEnum):
    """The two ownership questions, held under markers that never intersect.

    A claim answers which deployment may fire an issue; a lease answers
    which run may write a surface.  One mechanism arbitrates both, and the
    kind on the marker is what keeps the two vocabularies from meeting.
    """

    CLAIM = "claim"
    LEASE = "lease"


_GRANT_KIND_BY_VALUE: Final[Mapping[str, _GrantKind]] = {
    kind.value: kind for kind in _GrantKind
}


class _GrantState(StrEnum):
    """What a marker on the board is, which is not the same as being there.

    A holder has to put its marker on the log before anything can order it
    against another holder's, and it learns the outcome only from reading
    the log back.  ``BID`` is that marker before its read-back: a party
    still in the race, which no reader may report as an owner.  ``HELD`` is
    the same marker after its own read-back confirmed it, and is the only
    state that owns anything.  So a bid its holder abandons — because the
    read-back refused it and the backend then refused to take the marker
    off — was never a hold, and the holder that wrote it holds nothing by
    construction rather than by a compensating request.  ``VOID`` is a
    marker its holder has retracted in place, for the abandonment the
    backend will accept as an edit but not as a deletion.
    """

    BID = "bid"
    HELD = "held"
    VOID = "void"


_GRANT_STATE_BY_VALUE: Final[Mapping[str, _GrantState]] = {
    state.value: state for state in _GrantState
}

#: Which parent a comment is created under, per scope kind.  A container
#: surface parks its marker on the container it addresses, so a lease over
#: an issue and a project writes one marker on each.
_COMMENT_PARENT_BY_SCOPE_KIND: Final[Mapping[ScopeKind, str]] = {
    ScopeKind.ISSUE: "issueId",
    ScopeKind.PROJECT: "projectId",
    ScopeKind.INITIATIVE: "initiativeId",
    ScopeKind.MILESTONE: "milestoneId",
}


@dataclass(frozen=True, slots=True)
class _Target:
    """The comment parent one grant marker is written on. Adapter-private."""

    field: str
    key: str


@dataclass(frozen=True, slots=True)
class _WrittenMarker:
    """One marker this holder put on the board, and how to take it back off.

    Retraction is two requests because the backend answers them
    independently: the body is rewritten as ``VOID``, which is what makes
    the marker inert for every reader, and then the marker is deleted,
    which is what keeps the log short.  The void body travels with the
    address so a holder standing down can retract a marker it has already
    stopped tracking.
    """

    target: _Target
    comment_key: str
    void_body: str


@dataclass(frozen=True, slots=True)
class _GrantMarker:
    """One ownership marker as the backend holds it, parsed. Adapter-private.

    ``deadline`` is decided in the backend's own clock, from the stamps it
    put on this marker's writes and the durations the marker declares;
    ``advertised`` is the holder's account of the same deadline in its own
    clock, which is what a caller schedules against and what no
    arbitration ever reads.
    """

    target: _Target
    comment_key: str
    created_at: datetime
    updated_at: datetime
    kind: _GrantKind
    state: _GrantState
    holder: str
    nonce: str
    lease: timedelta
    since: datetime | None
    deadline: datetime
    advertised: datetime
    lines: tuple[str, ...]

    @property
    def expires_at(self) -> datetime:
        """The deadline, under the name the shared arithmetic reads it by."""
        return self.deadline

    @property
    def in_force(self) -> bool:
        """Whether this marker's last write put the deadline it asked for on.

        The same rule the deadline is computed by, asked directly: a
        write the backend stamped at or after the deadline it was
        published against renewed nothing, and the marker keeps the
        deadline it had, which has by then already passed.
        """
        return self.since is None or renews(
            published_at=self.updated_at, since=self.since
        )

    @property
    def addresses(self) -> frozenset[str]:
        """The addresses this marker covers, for membership questions."""
        return frozenset(self.lines)

    @property
    def order(self) -> tuple[datetime, str]:
        """Server order first, identity to settle an instant it shared."""
        return (self.created_at, self.comment_key)

    @property
    def retracted(self) -> bool:
        """Whether this marker has been taken out of the race in place."""
        return self.state is _GrantState.VOID


@dataclass(frozen=True, slots=True)
class _Granted:
    """A grant that survived its own read-back."""

    expires_at: datetime


@dataclass(frozen=True, slots=True)
class _OwnGrant:
    """One grant of one holder, folded to what the self-arbitration reads.

    A grant covers a marker on every target it spans, and those markers
    carry the backend's stamps separately.  The whole grant is placed
    where its earliest marker was created and lives only as long as its
    earliest deadline, so a grant is never read as owning anything past
    the point where any part of it lapsed.
    """

    order: tuple[datetime, str]
    expires_at: datetime


def _own_grants(
    markers: Sequence[_GrantMarker], *, holder: str, addresses: frozenset[str]
) -> dict[str, _OwnGrant]:
    """This holder's confirmed grants over exactly this set, one per nonce.

    Exactly this set, because a grant over a different one is a different
    ownership rather than a second reading of this one; confirmed,
    because a bid still inside its own race owns nothing and a holder
    that withdrew into one would end up holding what that bid retracts.
    """
    grants: dict[str, _OwnGrant] = {}
    for marker in markers:
        if (
            marker.holder != holder
            or marker.state is not _GrantState.HELD
            or marker.addresses != addresses
        ):
            continue
        standing = grants.get(marker.nonce)
        grants[marker.nonce] = _OwnGrant(
            order=marker.order
            if standing is None
            else min(standing.order, marker.order),
            expires_at=(
                marker.deadline
                if standing is None
                else min(standing.expires_at, marker.deadline)
            ),
        )
    return grants


@dataclass(frozen=True, slots=True)
class _Conflict[AddressT]:
    """A requested address an earlier marker of another holder covers.

    ``settled`` says whether the backend settled that marker as an OWNER.
    A confirmed grant is settled and is named; a bid still inside its own
    race, and an instant two markers shared, are not — nobody owns the
    address in either, so there is nobody to name, and a refusal that
    named the party it met would be saying it holds the surface.
    """

    address: AddressT
    holder: str | None
    settled: bool


@dataclass(frozen=True, slots=True)
class _Refused[AddressT]:
    """A grant that withdrew, and what the read-back turned it on."""

    address: AddressT
    holder: str | None
    settled: bool
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class _Addressing[AddressT]:
    """How one grant kind spells, orders and locates what it takes."""

    kind: _GrantKind
    encode: Callable[[AddressT], str]
    target: Callable[[AddressT], _Target]
    order: Callable[[AddressT], tuple[str, ...]]

    def lines(self, addresses: frozenset[AddressT]) -> tuple[str, ...]:
        """The address set as a marker carries it, in the canonical order.

        Two holders racing for one set write it the same way round, so
        neither can read the other's marker as covering something else.
        """
        return tuple(
            self.encode(address) for address in sorted(addresses, key=self.order)
        )

    def targets(self, addresses: frozenset[AddressT]) -> tuple[_Target, ...]:
        """Each parent this grant writes a marker on, once, in that order."""
        return tuple(
            dict.fromkeys(
                self.target(address) for address in sorted(addresses, key=self.order)
            )
        )


def _surface_line(surface: WritableSurface) -> str:
    """One surface as a marker line; each component escaped, so a separator
    inside a marker name cannot read as the separator between components."""
    return "|".join(quote(component, safe="") for component in surface_address(surface))


def _surface_target(surface: WritableSurface) -> _Target:
    return _Target(
        field=_COMMENT_PARENT_BY_SCOPE_KIND[surface.ref.kind],
        key=surface.ref.key,
    )


_CLAIM_ADDRESSING: Final[_Addressing[str]] = _Addressing(
    kind=_GrantKind.CLAIM,
    encode=lambda issue_key: issue_key,
    target=lambda issue_key: _Target(field="issueId", key=issue_key),
    order=lambda issue_key: (issue_key,),
)

_LEASE_ADDRESSING: Final[_Addressing[WritableSurface]] = _Addressing(
    kind=_GrantKind.LEASE,
    encode=_surface_line,
    target=_surface_target,
    order=surface_address,
)


def _retraction(marker: _GrantMarker, *, body: str) -> _WrittenMarker:
    """Where a marker is, and the body that takes it out of the arithmetic."""
    return _WrittenMarker(
        target=marker.target, comment_key=marker.comment_key, void_body=body
    )


def _may_resend(tool: str, exc: Exception) -> bool:
    """Whether a call that failed this way may be made again.

    The transport says when a request was written and never answered:
    the server may have performed it, and a reopened session making it
    again would perform it twice.  The retry budget therefore
    buys a second attempt at a READ, which is harmless, and never at a
    write; a failure the transport could tell apart from that — the
    session gone before anything was written, or an answer that was a
    refusal — is retried as it always was.
    """
    return not isinstance(exc, McpCallUnansweredError) or tool in _READ_TOOLS


def refuse_combined_issue_write(arguments: Mapping[str, object]) -> None:
    """Refuse one save carrying both a description edit and a state move.

    The backend offers to do both in one act, and one act cannot be
    ordered and cannot be half-undone.  A body that did not land the way
    its caller asserted would have moved the workflow state anyway, and
    the issue would then read as reviewed while carrying text nobody
    reviewed — the state saying one thing about work the body does not.

    Issued separately, in one order — the description edit first, under
    ``edit_description``'s assert-then-edit precondition, and the
    transition only after it — an edit that refused leaves the state
    exactly where its reader found it.
    """
    creation_fields = {"title", "description", "team", "parentId", "labels", "state"}
    if (
        set(arguments) == creation_fields
        and all(
            isinstance(arguments[name], str) and bool(str(arguments[name]).strip())
            for name in creation_fields - {"labels"}
        )
        and isinstance(arguments["labels"], list)
        and bool(arguments["labels"])
        and all(
            isinstance(label, str) and bool(label.strip())
            for label in arguments["labels"]
        )
    ):
        # Complete native creation initializes state; it transitions no
        # existing issue. Any id, patch or other locator keeps the guard.
        return
    if _STATE_SAVE_ARGUMENT in arguments and not _BODY_SAVE_ARGUMENTS.isdisjoint(
        arguments
    ):
        raise TrackerProtocolError(
            "a description edit and a state transition are separate writes",
            tool=_TOOL_SAVE_ISSUE,
            detail=f"arguments={sorted(arguments)}",
        )


class LinearMcpTracker:
    """``TrackerPort`` over the Linear MCP server.

    The semantic mappings (queue states, lifecycle stages, teams) are
    configuration, injected here rather than read from a module: swapping
    the tracker is an adapter plus a config change, never a code change in
    a consumer.
    """

    def __init__(
        self,
        *,
        caller: McpToolCaller,
        queue_state_labels: Mapping[str, str],
        scope_labels: Mapping[str, str],
        issue_labels: Mapping[str, str],
        criteria_stage_label_key: str | None,
        workflow_state_names: Mapping[LifecycleStage, str],
        marker_prefixes: Mapping[str, str],
        team_identifiers: Mapping[str, str],
        retry: RetryPolicy,
        clock: Callable[[], datetime] = _utc_now,
        ledger: SelfWriteLedger,
    ) -> None:
        self._caller: McpToolCaller = caller
        self._marker_prefixes = dict(marker_prefixes)
        self._markers = LinearMarkers(marker_prefixes)
        self._issue_identity = LinearIssueIdentityCarrier(marker_prefixes)
        self._issue_labels = dict(issue_labels)
        self._scope_labels = dict(scope_labels)
        self._criteria_stage_label_key = criteria_stage_label_key
        self._retry = retry
        self._clock: Callable[[], datetime] = clock
        self._workflow_state_names: Mapping[LifecycleStage, str] = workflow_state_names
        self._team_identifiers: Mapping[str, str] = team_identifiers
        self._team_key_by_identifier: dict[str, str] = {
            identifier: team_key for team_key, identifier in team_identifiers.items()
        }
        #: Team name to the UUID the workspace addresses it by, read once
        #: from the teams listing.  ``None`` means "not read yet", which is
        #: not the same state as "the workspace holds no teams".
        self._team_containers: Mapping[str, str] | None = None
        #: Where every write this adapter makes leaves the stamp it landed
        #: on, so the pass gates can tell the operation's own churn from a
        #: principal's edit.  Handed in by the composition that
        #: also hands it to the gates — one process, one record of what it
        #: wrote.  Required: a tracker holding a ledger nobody else can
        #: read is a tracker whose stamps reach no gate, and the pass that
        #: waits on one would sleep through every edit it made itself.
        #: The caller that reads it is the caller that hands
        #: it in.
        self._self_writes: SelfWriteLedger = ledger
        self._log: BoundLogger = get_logger(__name__)

        known = {member.value for member in QueueState}
        self._label_by_queue_state: dict[QueueState, str] = {
            QueueState(name): label
            for name, label in queue_state_labels.items()
            if name in known
        }
        self._queue_state_by_label: dict[str, QueueState] = {
            label: state for state, label in self._label_by_queue_state.items()
        }

    async def scan_issues(self, *, query: IssueQuery) -> Sequence[TrackerIssue]:
        """Issues matching *query*, in backend order.

        An issue carrying a workflow-state kind the domain does not name is
        EXCLUDED from the answer rather than unwinding the scan, and it is
        named as it goes: its key, the tool that returned it and the raw
        value the vendor sent, once per issue.  A scan reads a whole board,
        so one such issue took every pass that read it down with it, for as
        long as it sat there — one groomed duplicate crash-looped the
        dispatch pass.

        The containment stops at this seam.  :meth:`read_issue` still
        raises on the same value, because there the issue the caller asked
        about IS the answer and excluding it would return nothing at all.
        """
        arguments: dict[str, object] = {"limit": query.page_size}
        if query.queue_state is not None:
            arguments["label"] = self._label_for(query.queue_state)
        if query.team_key is not None:
            arguments["team"] = self._team_identifier(query.team_key)
        if query.updated_since is not None:
            arguments["updatedAt"] = query.updated_since.isoformat()
        payload = await self._call(_TOOL_LIST_ISSUES, arguments)
        listing = self._validate(LinearIssueListWire, payload, _TOOL_LIST_ISSUES)
        found: list[TrackerIssue] = []
        for wire in listing.issues:
            if wire.status_type not in _STATE_KIND_BY_VALUE:
                await self._log.aerror(
                    "tracker_scan_issue_excluded",
                    issue_key=wire.id,
                    tool=_TOOL_LIST_ISSUES,
                    status_type=wire.status_type,
                )
                continue
            found.append(self._to_issue(wire))
        return tuple(found)

    async def scan_reviews(self, *, query: ReviewQuery) -> Sequence[TrackerReview]:
        """Reviews matching *query*, newest first.

        Ordering is asked of the vendor and recency is applied here: the
        listing tool takes an order but no recency predicate, so pushing
        the filter down is not on offer.  Ordering newest-first is what
        makes that acceptable — the answer to "did anything move since
        *t*" is at the head of the first page, not spread over the set.
        """
        arguments: dict[str, object] = {
            "limit": query.page_size,
            "orderBy": _ORDER_BY_UPDATED_AT,
        }
        if query.repo_url is not None:
            owner, repo = extract_owner_repo(query.repo_url)
            arguments["owner"] = owner
            arguments["repo"] = repo
        payload = await self._call(_TOOL_LIST_DIFFS, arguments)
        listing = self._validate(LinearDiffListWire, payload, _TOOL_LIST_DIFFS)
        reviews = tuple(
            TrackerReview(
                review_key=wire.full_identifier,
                updated_at=wire.updated_at,
            )
            for wire in listing.diffs
        )
        if query.updated_since is None:
            return reviews
        # Strictly after: the mark is the newest thing the last tick SAW,
        # so an equal stamp is that same thing and reporting it again
        # would keep a quiet board looking busy forever.
        return tuple(
            review for review in reviews if review.updated_at > query.updated_since
        )

    async def verify_scan_capability(
        self,
        *,
        signals: Sequence[PassSignal],
    ) -> Mapping[PassSignal, str]:
        """Which of *signals* this credential cannot scan for, and why.

        One minimal probe per DISTINCT scan: the three issue signals are
        served by one listing tool, so probing all three probes once.  A
        refusal is read off the error the transport already carries, and
        anything else it carries is re-raised — a boot that cannot reach
        the workspace at all is not a boot that learned something about
        scope.

        One PROBE is not one call when the answer is a refusal, and that
        cost is taken deliberately.  See :meth:`_probe_scope`.
        """
        probed: dict[str, str | None] = {}
        refused: dict[PassSignal, str] = {}
        for signal in signals:
            tool = self._scan_tool(signal)
            if tool not in probed:
                probed[tool] = await self._probe_scope(tool)
            diagnosis = probed[tool]
            if diagnosis is not None:
                refused[signal] = diagnosis
        return refused

    async def _probe_scope(self, tool: str) -> str | None:
        """Call *tool* minimally; its diagnosis when it refuses scope.

        The call goes through the ordinary retry seam, so a REFUSAL pays
        the whole configured budget — the backoff sleeps and a
        ``tracker_mcp_retry`` warning per attempt — before the marker is
        even looked at.  That trade is taken rather than overlooked: the
        backend answers a scope refusal and an outage with one exception
        type, so probing without retries would buy a cheaper refusal by
        making boot fail on a blip.  A bounded one-off delay on a boot that
        is about to abort is the cheaper half, and the retry warnings under
        a refused probe are this working rather than a transport problem.
        """
        try:
            await self._call(tool, {"limit": _SCOPE_PROBE_LIMIT})
        except TrackerUnavailableError as exc:
            diagnosis = str(exc)
            if _SCOPE_REFUSAL_MARKER in diagnosis:
                return diagnosis
            raise
        return None

    def _scan_tool(self, signal: PassSignal) -> str:
        """The tool whose scan answers *signal*.

        Total over the vocabulary by construction: a new member with no arm
        here fails type checking rather than reaching a probe that cannot
        name the tool it is supposed to call.
        """
        match signal:
            case PassSignal.reviews_changed:
                return _TOOL_LIST_DIFFS
            case (
                PassSignal.triage_backlog
                | PassSignal.approved_changed
                | PassSignal.issues_changed
            ):
                return _TOOL_LIST_ISSUES
            case _:
                assert_never(signal)

    async def read_issue(self, *, issue_key: str) -> TrackerIssue:
        """The full issue — body, state, relations, parent, assignee."""
        return self._to_issue(await self._read_issue_wire(issue_key))

    async def read_planning_issue(self, *, issue_key: str) -> TrackerIssue:
        payload = await self._call(
            _TOOL_GET_ISSUE, {"id": issue_key, "includeRelations": True}
        )
        return self._to_issue(
            self._validate(LinearPlanningIssueWire, payload, _TOOL_GET_ISSUE)
        )

    async def read_labeled_issues(
        self, *, classification: str
    ) -> Sequence[TrackerIssue]:
        label = self._issue_labels.get(classification)
        if label is None or not label.strip():
            raise OperationMemberAbsentError(
                missing=f"issue_labels[{classification!r}]",
                stops="complete labeled issue membership cannot be read",
            )
        arguments: dict[str, object] = {
            "label": label,
            "includeArchived": True,
            "fields": ["id"],
            "limit": _ISSUE_IDENTITY_PAGE_SIZE,
        }
        members: dict[str, TrackerIssue] = {}
        try:

            async def read(
                request: Mapping[str, object],
            ) -> tuple[LinearScopeIssuesWire, bool, str | None]:
                payload = await self._call(_TOOL_LIST_ISSUES, request)
                page = self._validate(LinearScopeIssuesWire, payload, _TOOL_LIST_ISSUES)
                return page, page.has_next_page, page.cursor

            async for page in cursor_pages(
                read,
                arguments=arguments,
                refusal=lambda _: IssueLabelReadError(
                    classification=classification,
                    reason="membership pagination cannot advance",
                ),
            ):
                for entry in page.issues:
                    if entry.id in members:
                        raise IssueLabelReadError(
                            classification=classification,
                            reason=f"duplicate listed identity {entry.id!r}",
                        )
                    issue = await self.read_planning_issue(issue_key=entry.id)
                    if (
                        issue.issue_key != entry.id
                        or classification not in issue.issue_labels
                    ):
                        raise IssueLabelReadError(
                            classification=classification,
                            reason=f"listed identity or label changed for {entry.id!r}",
                        )
                    members[entry.id] = issue
            return tuple(members[key] for key in sorted(members))
        except (TrackerUnavailableError, TrackerProtocolError) as exc:
            raise IssueLabelReadError(
                classification=classification,
                reason="the tracker membership read failed or was incomplete",
            ) from exc

    def require_scope_plan_reads(self) -> None:
        """A clean plan must be able to see both criteria and open decisions."""
        for classification in ("criterion", "decision"):
            if classification not in self._issue_labels:
                raise OperationMemberAbsentError(
                    missing=f"issue_labels[{classification!r}]",
                    stops="scope plan barriers cannot be read",
                )

    def require_issue_classification_reads(
        self, *, additional_keys: frozenset[str] = frozenset()
    ) -> None:
        self.require_scope_plan_reads()
        for key in sorted({"criterion", "decision", "tracker", *additional_keys}):
            if not self._issue_labels.get(key, "").strip():
                raise OperationMemberAbsentError(
                    missing=f"issue_labels[{key!r}]",
                    stops="required issue classifications cannot be read",
                )

    async def read_issue_state_change(
        self, *, issue_key: str
    ) -> TrackerIssueStateChange:
        """Use the matching open state interval from the same native payload."""
        payload = await self._call(
            _TOOL_GET_ISSUE, {"id": issue_key, "includeRelations": True}
        )
        wire = self._validate(LinearIssueStateHistoryWire, payload, _TOOL_GET_ISSUE)
        current = [entry for entry in wire.state_history if entry.ended_at is None]
        if wire.id != issue_key or len(current) != 1:
            raise TrackerProtocolError(
                "state history has no unique current issue interval",
                tool=_TOOL_GET_ISSUE,
                detail=f"target={issue_key}; returned={wire.id}",
            )
        entry = current[0]
        if (
            entry.state.name != wire.status
            or entry.state.type != wire.status_type
            or wire.created_at.utcoffset() is None
            or wire.updated_at.utcoffset() is None
            or not wire.created_at <= entry.started_at <= wire.updated_at
            or any(
                item.ended_at is not None
                and not wire.created_at
                <= item.started_at
                <= item.ended_at
                <= entry.started_at
                for item in wire.state_history
            )
        ):
            raise TrackerProtocolError(
                "state history does not agree with the issue snapshot",
                tool=_TOOL_GET_ISSUE,
                detail=f"target={issue_key}",
            )
        return TrackerIssueStateChange(
            issue=self._to_issue(wire), state_changed_at=entry.started_at
        )

    async def read_issue_revision(self, *, issue_key: str) -> TrackerIssueRevision:
        """Hash exactly the returned body, independently of vendor timestamps."""
        issue = await self.read_issue(issue_key=issue_key)
        return TrackerIssueRevision(
            issue=issue,
            body_digest=sha256(issue.body.encode("utf-8")).hexdigest(),
        )

    async def scope_issues(self, *, ref: ScopeRef) -> Sequence[TrackerIssue]:
        """Resolve live container membership or an issue's whole subtree."""
        return await LinearScopeReader(
            call=self._call,
            read_issue=self.read_issue,
        ).scope_issues(ref=ref)

    async def execution_approved(self, *, issue_key: str) -> bool:
        """Resolve configured label presence through fresh native ancestry."""
        _, approved = await self._read_execution_approval(issue_key=issue_key)
        return approved

    def _scope_label_members(self, labels: Sequence[str]) -> frozenset[ScopeLabel]:
        return frozenset(
            member
            for member in ScopeLabel
            if self._scope_labels.get(member.value) in labels
        )

    async def _read_scope_issue(
        self, issue_key: str
    ) -> tuple[TrackerIssue, frozenset[ScopeLabel]]:
        payload = await self._call(
            _TOOL_GET_ISSUE, {"id": issue_key, "includeRelations": True}
        )
        wire = self._validate(LinearApprovalIssueWire, payload, _TOOL_GET_ISSUE)
        issue = self._to_issue(wire)
        if issue.issue_key != issue_key:
            raise ScopeReadError(
                "scope label identity changed",
                ref=ScopeRef(kind=ScopeKind.ISSUE, key=issue_key),
            )
        return issue, self._scope_label_members(wire.labels)

    async def read_scope_labels(self, *, ref: ScopeRef) -> frozenset[ScopeLabel]:
        if ref.kind is ScopeKind.ISSUE:
            _, members = await self._read_scope_issue(ref.key)
            return members
        reader = LinearScopeReader(call=self._call, read_issue=self.read_issue)
        if ref.kind is ScopeKind.MILESTONE:
            await reader.container_metadata(ref=ref)
            return frozenset()
        labels, _ = await reader.labels_parent(ref=ref)
        return self._scope_label_members(tuple(labels))

    async def _read_execution_approval(
        self, *, issue_key: str
    ) -> tuple[TrackerIssue, bool]:
        label = self._scope_labels.get(ScopeLabel.APPROVED.value)
        if not label:
            raise OperationMemberAbsentError(
                missing=f"scope_labels.{ScopeLabel.APPROVED.value}",
                stops="cannot resolve scope approval",
            )
        reader = LinearScopeReader(call=self._call, read_issue=self.read_issue)

        async def hydrate(key: str) -> tuple[TrackerIssue, bool]:
            issue, members = await self._read_scope_issue(key)
            return issue, ScopeLabel.APPROVED in members

        subject = await hydrate(issue_key)

        async def read_issue(key: str) -> tuple[TrackerIssue, bool]:
            return subject if key == issue_key else await hydrate(key)

        async def read_container(ref: ScopeRef) -> tuple[bool, ScopeRef | None]:
            return await reader.approval_parent(ref=ref, approved_label=label)

        approved = await resolve_execution_approval(
            issue_key=issue_key,
            read_issue=read_issue,
            read_container=read_container,
        )
        return subject[0], approved

    async def container_metadata(self, *, ref: ScopeRef) -> ScopeContainer:
        """Read a container without fabricating a URL or choosing a parent."""
        return await LinearScopeReader(
            call=self._call,
            read_issue=self.read_issue,
        ).container_metadata(ref=ref)

    def _wrote(self, issue: TrackerIssue) -> TrackerIssue:
        """Record what this write left on the issue, and hand it back.

        Threaded through the RESPONSE wherever the backend answers a write
        with the stored issue, because that answer already carries the
        stamp the write produced and a second read would be a round trip
        for a value in hand.
        """
        self._self_writes.record(issue_key=issue.issue_key, updated_at=issue.updated_at)
        return issue

    def _saved_issue(
        self, payload: McpToolResult, *, written: Mapping[str, object] | None = None
    ) -> TrackerIssue:
        """The stored issue a save_issue answer carries, recorded as a write.

        The one tail every issue-write shares: validate the save_issue
        envelope, read it as the stored issue, and record the stamp the
        write left (:meth:`_wrote`).  One place, so a change to how a
        write is read back cannot land on three of four call sites.
        """
        issue = self._to_issue(
            self._validate(LinearIssueWire, payload, _TOOL_SAVE_ISSUE)
        )
        if written is not None:
            assert isinstance(payload, Mapping)
            fields: dict[str, object] = {
                key: written[key]
                for key in ("title", "description", "labels")
                if key in written
            }
            if "state" in written:
                fields.update(
                    {
                        key: payload[key]
                        for key in (
                            "status",
                            "statusType",
                            "startedAt",
                            "completedAt",
                            "canceledAt",
                        )
                        if key in payload
                    }
                )
            additions: tuple[tuple[str, tuple[str, ...]], ...] = ()
            if "addLabels" in written:
                values = written["addLabels"]
                assert isinstance(values, list)
                additions = (("labels", tuple(field_value(value) for value in values)),)
            self._self_writes.record_mutation(
                issue_key=str(payload["id"]),
                mutation=OwnMutation(fields=field_values(fields), additions=additions),
            )
        return self._wrote(issue)

    def _comment_written(
        self, *, issue_key: str, payload: McpToolResult, created: bool
    ) -> TrackerComment:
        wire = self._validate(LinearCommentWire, payload, _TOOL_SAVE_COMMENT)
        assert isinstance(payload, Mapping)
        values = field_values(
            payload
            if created
            else {
                key: value
                for key, value in payload.items()
                if key in {"body", "updatedAt"}
            }
        )
        mutation = (
            OwnMutation(created=((wire.id, values),))
            if created
            else OwnMutation(edited=((wire.id, values),))
        )
        self._self_writes.record_mutation(issue_key=issue_key, mutation=mutation)
        return self._to_comment(wire, issue_key=issue_key)

    async def _delete_own_comment(self, *, issue_key: str, comment_key: str) -> None:
        await self._call(_TOOL_DELETE_COMMENT, {"id": comment_key})
        self._self_writes.record_mutation(
            issue_key=issue_key, mutation=OwnMutation(deleted=(comment_key,))
        )

    async def writer_identity(self) -> frozenset[str]:
        """Both spellings of the account this credential writes as."""
        payload = await self._call(_TOOL_GET_USER, {"query": _CURRENT_USER_QUERY})
        wire = self._validate(LinearUserWire, payload, _TOOL_GET_USER)
        return frozenset({wire.name, wire.display_name})

    async def read_issue_movement(self, *, issue_key: str) -> IssueMovementSnapshot:
        """Retain the whole native projection, including unconfigured fields.

        The two complete comment listings and bounding full issue reads
        must agree. Unknown fields are retained as opaque JSON, not dropped
        by the normal domain projection. No read creates a write receipt.
        """

        async def issue_payload() -> tuple[McpToolResult, LinearPlanningIssueWire]:
            payload = await self._call(
                _TOOL_GET_ISSUE, {"id": issue_key, "includeRelations": True}
            )
            wire = self._validate(LinearPlanningIssueWire, payload, _TOOL_GET_ISSUE)
            if wire.id != issue_key:
                raise TrackerProtocolError(
                    "movement read returned another issue",
                    tool=_TOOL_GET_ISSUE,
                    detail=issue_key,
                )
            return payload, wire

        before, _ = await issue_payload()
        assert isinstance(before, Mapping)
        initial_fields = field_values(before)
        comments = await self._movement_comments(issue_key)
        repeated_comments = await self._movement_comments(issue_key)
        after, issue = await issue_payload()
        assert isinstance(after, Mapping)
        if initial_fields != field_values(after) or comments != repeated_comments:
            raise TrackerProtocolError(
                "issue or comments changed during movement read",
                tool=_TOOL_GET_ISSUE,
                detail=issue_key,
            )
        assert isinstance(after, Mapping)
        return IssueMovementSnapshot(
            issue_key=issue.id,
            updated_at=issue.updated_at,
            fields=field_values(
                {key: value for key, value in after.items() if key != "updatedAt"}
            ),
            comments=comments,
        )

    async def _movement_comments(self, issue_key: str) -> CommentValues:
        arguments: dict[str, object] = {"issueId": issue_key}
        comments: dict[str, tuple[tuple[str, str], ...]] = {}

        async def read(
            request: Mapping[str, object],
        ) -> tuple[tuple[McpToolResult, LinearCommentListWire], bool, str | None]:
            payload = await self._call(_TOOL_LIST_COMMENTS, request)
            page = self._validate(LinearCommentListWire, payload, _TOOL_LIST_COMMENTS)
            return (payload, page), page.has_next_page, page.cursor

        async for payload, page in cursor_pages(
            read,
            arguments=arguments,
            refusal=lambda _: TrackerProtocolError(
                "movement comment pagination cannot advance",
                tool=_TOOL_LIST_COMMENTS,
                detail=issue_key,
            ),
        ):
            assert isinstance(payload, Mapping)
            raw_comments = payload["comments"]
            assert isinstance(raw_comments, list)
            for wire, raw in zip(page.comments, raw_comments, strict=True):
                if wire.id in comments or not isinstance(raw, Mapping):
                    raise TrackerProtocolError(
                        "movement comments are repeated or malformed",
                        tool=_TOOL_LIST_COMMENTS,
                        detail=issue_key,
                    )
                comments[wire.id] = field_values(raw)
        return tuple(sorted(comments.items()))

    async def _unstarted_state_id(self, *, team_id: str, issue_key: str) -> str:
        payload = await self._call(_TOOL_LIST_ISSUE_STATUSES, {"team": team_id})
        try:
            states = LINEAR_WORKFLOW_STATES.validate_python(payload)
        except ValidationError as exc:
            raise TrackerProtocolError(
                "invalid initial state vocabulary",
                tool=_TOOL_LIST_ISSUE_STATUSES,
                detail=str(exc),
            ) from exc
        unstarted = [
            state.id
            for state in states
            if state.type == WorkflowStateKind.UNSTARTED.value
        ]
        if len(unstarted) != 1:
            raise CriterionReadError(
                issue_key=issue_key,
                reason="initialization requires exactly one unstarted team state",
            )
        return unstarted[0]

    async def create_criterion_if_absent(
        self, *, parent_key: str, title: str, check: str, do: str, holder: str
    ) -> TrackerIssue:
        body = criterion_body(parent_key=parent_key, check=check, do=do)
        if not title.strip():
            raise CriterionReadError(
                issue_key=parent_key, reason="criterion title is empty"
            )
        children = await self.read_criteria(issue_key=parent_key)
        existing = existing_criterion(
            parent_key=parent_key, check=check, children=children
        )
        if existing is not None:
            return existing
        parent = await self.read_issue(issue_key=parent_key)
        if parent.issue_key != parent_key or parent.team_key is None:
            raise CriterionReadError(
                issue_key=parent_key, reason="criterion parent has no declared team"
            )
        label = self._issue_labels.get("criterion")
        if not label:
            raise OperationMemberAbsentError(
                missing="issue_labels.criterion", stops="criterion creation"
            )
        if label == self._scope_labels.get("approved"):
            raise CriterionReadError(
                issue_key=parent_key,
                reason="criterion classification aliases human approval",
            )
        team = self._team_identifier(parent.team_key)
        state = await self._unstarted_state_id(team_id=team, issue_key=parent_key)
        surface = WritableSurface(
            kind=SurfaceKind.CRITERION_CHILD_SET,
            ref=ScopeRef(kind=ScopeKind.ISSUE, key=parent_key),
        )
        await self._require_surface_holder(surface=surface, holder=holder)
        created = self._saved_issue(
            await self._call(
                _TOOL_SAVE_ISSUE,
                {
                    "title": title,
                    "description": body,
                    "team": team,
                    "parentId": parent_key,
                    "labels": [label],
                    "state": state,
                },
            )
        )
        current = await self.read_issue(issue_key=created.issue_key)
        if (
            current.issue_key != created.issue_key
            or current.parent_key != parent_key
            or current.body != body
            or current.title != title
            or current.state_kind is not WorkflowStateKind.UNSTARTED
            or "criterion" not in current.issue_labels
        ):
            raise CriterionReadError(
                issue_key=parent_key,
                reason="created criterion did not retain its required shape",
            )
        return current

    async def create_issue(
        self,
        *,
        title: str,
        body: str,
        team_key: str,
        priority: IssuePriority,
    ) -> TrackerIssue:
        """Create an issue on *team_key* and return it as stored."""
        payload = await self._call(
            _TOOL_SAVE_ISSUE,
            {
                "title": title,
                "description": body,
                "team": self._team_identifier(team_key),
                "priority": _RAW_BY_PRIORITY[priority],
            },
        )
        return self._saved_issue(payload)

    async def update_issue(
        self,
        *,
        issue_key: str,
        title: str | None = None,
        body: str | None = None,
    ) -> TrackerIssue:
        """Update the given fields; ``None`` leaves a field untouched."""
        arguments: dict[str, object] = {"id": issue_key}
        if title is not None:
            arguments["title"] = title
        if body is not None:
            current = await self._read_issue_wire(issue_key)
            identity = self._issue_identity.decode(
                current.description or "", issue_key=issue_key
            )
            if identity is not None:
                body = self._issue_identity.encode(
                    identity, body=body, issue_key=issue_key
                )
            arguments["description"] = body
        payload = await self._call(_TOOL_SAVE_ISSUE, arguments)
        return self._saved_issue(payload, written=arguments)

    async def read_criteria(self, *, issue_key: str) -> Sequence[TrackerIssue]:
        _, criteria = await self._read_criterion_family(issue_key=issue_key)
        return criteria

    async def read_fire_spec(self, *, issue_key: str) -> TrackerSpec:
        if self._criteria_stage_label_key is not None and not self._issue_labels.get(
            self._criteria_stage_label_key
        ):
            raise OperationMemberAbsentError(
                missing=f"issue_labels.{self._criteria_stage_label_key}",
                stops="cannot establish criteria-stage completion at fire entry",
            )
        try:
            subject, approved = await self._read_execution_approval(issue_key=issue_key)
            require_fire_entry(
                subject=subject,
                approved=approved,
                criteria_stage_label_key=self._criteria_stage_label_key,
            )
            _, criteria = await self._read_criterion_family(
                issue_key=issue_key, subject=subject
            )
        except (TrackerUnavailableError, TrackerProtocolError) as exc:
            raise CriterionReadError(
                issue_key=issue_key, reason="the tracker read failed or was incomplete"
            ) from exc
        return tracker_spec_from_issues(subject=subject, criteria=criteria)

    async def _read_criterion_family(
        self, *, issue_key: str, subject: TrackerIssue | None = None
    ) -> tuple[TrackerIssue, tuple[TrackerIssue, ...]]:
        if "criterion" not in self._issue_labels:
            raise OperationMemberAbsentError(
                missing="issue_labels['criterion']",
                stops="criterion sub-issue membership cannot be read",
            )
        try:
            parent = (
                subject
                if subject is not None
                else await self.read_issue(issue_key=issue_key)
            )
            return parent, await self._read_criteria(parent=parent)
        except (TrackerUnavailableError, TrackerProtocolError) as exc:
            raise CriterionReadError(
                issue_key=issue_key, reason="the tracker read failed or was incomplete"
            ) from exc

    async def _read_criteria(self, *, parent: TrackerIssue) -> tuple[TrackerIssue, ...]:
        issue_key = parent.issue_key
        arguments: dict[str, object] = {
            "parentId": parent.issue_key,
            "includeArchived": True,
            "limit": _ISSUE_IDENTITY_PAGE_SIZE,
            "fields": ["id"],
        }
        seen_keys: set[str] = set()
        criteria: list[TrackerIssue] = []

        async def read(
            request: Mapping[str, object],
        ) -> tuple[LinearScopeIssuesWire, bool, str | None]:
            payload = await self._call(_TOOL_LIST_ISSUES, request)
            page = self._validate(LinearScopeIssuesWire, payload, _TOOL_LIST_ISSUES)
            return page, page.has_next_page, page.cursor

        async for page in cursor_pages(
            read,
            arguments=arguments,
            refusal=lambda _: CriterionReadError(
                issue_key=issue_key,
                reason="child listing pagination cannot advance",
            ),
        ):
            for entry in page.issues:
                if entry.id in seen_keys:
                    continue
                seen_keys.add(entry.id)
                detail = await self._call(
                    _TOOL_GET_ISSUE, {"id": entry.id, "includeRelations": True}
                )
                child = self._to_issue(
                    self._validate(LinearCriterionIssueWire, detail, _TOOL_GET_ISSUE)
                )
                if child.issue_key != entry.id or child.parent_key != parent.issue_key:
                    raise CriterionReadError(
                        issue_key=issue_key,
                        reason="child differs from its current identity or parent",
                    )
                if "criterion" in child.issue_labels:
                    criteria.append(child)
        return tuple(sorted(criteria, key=lambda criterion: criterion.issue_key))

    async def read_issue_identity(self, *, issue_key: str) -> IssueIdentity | None:
        self._issue_identity.require_prefix()
        current = await self._read_issue_wire(issue_key)
        return self._issue_identity.decode(
            current.description or "", issue_key=issue_key
        )

    async def upsert_issue(
        self,
        *,
        scope_key: ScopeRef,
        deliverable_key: str,
        title: str,
        body: str,
        team_key: str,
        priority: IssuePriority,
    ) -> TrackerIssue:
        identity = IssueIdentity(scope_key=scope_key, deliverable_key=deliverable_key)
        self._issue_identity.require_prefix()
        current = await self._find_issue_identity(identity)
        content = self._issue_identity.encode(
            identity, body=body, issue_key=current.issue_key if current else "new issue"
        )
        if current is None:
            return await self.create_issue(
                title=title, body=content, team_key=team_key, priority=priority
            )
        if current.body != content:
            await self.edit_description(
                target=current.issue_key, expected=current.body, replacement=content
            )
        if current.title != title:
            await self.update_issue(issue_key=current.issue_key, title=title)
        return await self.read_issue(issue_key=current.issue_key)

    async def _find_issue_identity(
        self, identity: IssueIdentity
    ) -> TrackerIssue | None:
        arguments: dict[str, object] = {
            "includeArchived": True,
            "limit": _ISSUE_IDENTITY_PAGE_SIZE,
            "fields": ["id"],
        }
        seen_keys: set[str] = set()
        matches: list[TrackerIssue] = []

        async def read(
            request: Mapping[str, object],
        ) -> tuple[LinearScopeIssuesWire, bool, str | None]:
            payload = await self._call(_TOOL_LIST_ISSUES, request)
            page = self._validate(LinearScopeIssuesWire, payload, _TOOL_LIST_ISSUES)
            return page, page.has_next_page, page.cursor

        async for page in cursor_pages(
            read,
            arguments=arguments,
            refusal=lambda _: TrackerProtocolError(
                "issue identity lookup pagination cannot advance",
                tool=_TOOL_LIST_ISSUES,
                detail="missing or repeated cursor",
            ),
        ):
            for entry in page.issues:
                if entry.id in seen_keys:
                    continue
                seen_keys.add(entry.id)
                # Listing descriptions truncate even with fields=['description'];
                # only the full read can establish that a carrier is absent.
                wire = await self._read_issue_wire(entry.id)
                held = self._issue_identity.decode(
                    wire.description or "", issue_key=wire.id
                )
                if held == identity:
                    matches.append(self._to_issue(wire))
        if len(matches) > 1:
            raise DuplicateIssueIdentityError(
                scope_key=identity.scope_key,
                deliverable_key=identity.deliverable_key,
                issue_keys=[issue.issue_key for issue in matches],
            )
        return matches[0] if matches else None

    async def edit_description(
        self, *, target: str, expected: str, replacement: str
    ) -> DescriptionEditResult:
        """Assert the complete expected body before a description-only write."""
        current = await self.read_issue(issue_key=target)
        body = description_replacement(
            target=target, body=current.body, expected=expected, replacement=replacement
        )
        if body is None:
            return DescriptionEditResult.UNCHANGED
        await self.update_issue(issue_key=target, body=body)
        return DescriptionEditResult.EDITED

    async def set_workflow_state(
        self,
        *,
        issue_key: str,
        stage: LifecycleStage,
    ) -> TrackerIssue:
        """Move the issue to the state the configuration binds *stage* to."""
        state_name = self._workflow_state_names.get(stage)
        if state_name is None:
            raise TrackerProtocolError(
                "no workflow state is configured for this lifecycle stage",
                tool=_TOOL_SAVE_ISSUE,
                detail=f"stage={stage.value}",
            )
        return await self._save_state(issue_key=issue_key, state_name=state_name)

    async def restore_workflow_state(
        self,
        *,
        issue_key: str,
        state_name: str,
    ) -> TrackerIssue:
        """Put the issue back in the state a reader found it in."""
        return await self._save_state(issue_key=issue_key, state_name=state_name)

    async def _save_state(self, *, issue_key: str, state_name: str) -> TrackerIssue:
        """Read first; matching state writes produce no history entry."""
        before = await self._call(
            _TOOL_GET_ISSUE, {"id": issue_key, "includeRelations": True}
        )
        current = self._to_issue(
            self._validate(LinearIssueDetailWire, before, _TOOL_GET_ISSUE)
        )
        if current.state_name == state_name:
            return current
        payload = await self._call(
            _TOOL_SAVE_ISSUE,
            {"id": issue_key, "state": state_name},
        )
        issue = self._saved_issue(payload, written={"state": state_name})
        assert isinstance(before, Mapping) and isinstance(payload, Mapping)
        await self._record_state_history(before=before, saved=payload, issue=issue)
        return issue

    async def _record_state_history(
        self,
        *,
        before: Mapping[str, object],
        saved: Mapping[str, object],
        issue: TrackerIssue,
    ) -> None:
        """Enrich only history, without failing or restamping a landed write.

        Native save_issue can omit stateHistory. A single full read may supply
        it only at the already-known atomic write stamp. A later or unreadable
        snapshot leaves this optional receipt unavailable and the gate wakes.
        """
        after: McpToolResult = saved
        if "stateHistory" not in saved:
            try:
                after = await self._call(
                    _TOOL_GET_ISSUE,
                    {"id": issue.issue_key, "includeRelations": True},
                )
            except (
                TrackerAccessDeniedError,
                TrackerUnavailableError,
                TrackerProtocolError,
                TransientAPIError,
            ):
                return
        try:
            start = LinearIssueWire.model_validate(before)
            end = LinearIssueWire.model_validate(after)
        except ValidationError:
            return
        if (
            start.id != issue.issue_key
            or end.id != issue.issue_key
            or end.updated_at != issue.updated_at
            or end.status != issue.state_name
            or end.status_type != saved["statusType"]
        ):
            return
        assert isinstance(after, Mapping)
        mutation = state_history_receipt(
            before=before.get("stateHistory"),
            after=after.get("stateHistory"),
            previous_state=start.status,
            previous_type=start.status_type,
            written_state=end.status,
            written_type=end.status_type,
            before_stamp=start.updated_at,
            write_stamp=issue.updated_at,
        )
        if mutation is not None:
            self._self_writes.record_mutation(
                issue_key=issue.issue_key, mutation=mutation
            )

    async def set_queue_state(
        self,
        *,
        issue_key: str,
        state: QueueState,
    ) -> TrackerIssue:
        """Set the semantic queue state, replacing any other member."""
        current = await self._read_issue_wire(issue_key)
        issue = self._to_issue(current)
        if issue.queue_states == frozenset({state}):
            return issue
        preserved = [
            label for label in current.labels if label not in self._queue_state_by_label
        ]
        payload = await self._call(
            _TOOL_SAVE_ISSUE,
            {"id": issue_key, "labels": [*preserved, self._label_for(state)]},
        )
        return self._saved_issue(
            payload, written={"labels": [*preserved, self._label_for(state)]}
        )

    async def set_issue_classification(
        self, *, issue_key: str, classification: str
    ) -> TrackerIssue:
        if classification not in self._issue_labels:
            raise OperationMemberAbsentError(
                missing=f"issue_labels[{classification!r}]",
                stops="this issue classification cannot be written",
            )
        current = await self.read_issue(issue_key=issue_key)
        if classification in current.issue_labels:
            return current
        payload = await self._call(
            _TOOL_SAVE_ISSUE,
            {
                "id": current.issue_key,
                "addLabels": [self._issue_labels[classification]],
            },
        )
        return self._saved_issue(
            payload, written={"addLabels": [self._issue_labels[classification]]}
        )

    async def post_comment(self, *, issue_key: str, body: str) -> TrackerComment:
        """Post a comment and return it as stored."""
        payload = await self._call(
            _TOOL_SAVE_COMMENT,
            {"issueId": issue_key, "body": body},
        )
        return self._comment_written(issue_key=issue_key, payload=payload, created=True)

    async def list_comments(self, *, issue_key: str) -> Sequence[TrackerComment]:
        """Every comment on the issue, oldest first."""
        return tuple(
            self._to_comment(wire, issue_key=issue_key)
            for wire in await self._comment_wires(issue_key)
        )

    async def record_run_alarm(
        self, *, issue_key: str, alarm: RunAlarm, holder: str
    ) -> None:
        """Keep one whole-subject record under the existing leased upsert policy."""
        await self.read_run_alarm(
            issue_key=issue_key, subject=alarm.subject, signal=alarm.signal
        )

        def validate_existing(stored: TrackerComment) -> None:
            self._parse_alarm_comment(
                stored=stored, subject=alarm.subject, signal=alarm.signal
            )

        await self._upsert_comment(
            target=issue_key,
            marker=run_alarm_marker(
                subject=alarm.subject,
                signal=alarm.signal,
                marker_prefixes=self._marker_prefixes,
            ),
            body=render_run_alarm(alarm=alarm),
            holder=holder,
            validate_existing=validate_existing,
        )

    async def read_run_alarm(
        self, *, issue_key: str, subject: AlarmSubject, signal: AlarmSignal
    ) -> RunAlarm | None:
        """Resolve the full subject and signal across the native comment log."""
        marker = run_alarm_marker(
            subject=subject, signal=signal, marker_prefixes=self._marker_prefixes
        )
        stored = comment_under_marker(
            target=issue_key,
            marker=marker,
            comments=await self.list_comments(issue_key=issue_key),
        )
        if stored is None:
            return None
        return self._parse_alarm_comment(stored=stored, subject=subject, signal=signal)

    def _parse_alarm_comment(
        self, *, stored: TrackerComment, subject: AlarmSubject, signal: AlarmSignal
    ) -> RunAlarm:
        """Decode one actual native snapshot, preserving a typed protocol refusal."""
        try:
            return parse_run_alarm(
                body=stored.body,
                subject=subject,
                signal=signal,
                marker_prefixes=self._marker_prefixes,
            )
        except ValueError as exc:
            raise TrackerProtocolError(
                "run-alarm record does not match its declared shape",
                tool=_TOOL_LIST_COMMENTS,
                detail=stored.comment_key,
            ) from exc

    async def post_run_event(
        self, *, issue_key: str, event: LaneRunEvent
    ) -> LaneRunEvent:
        """Append the event under this stream's configured lane marker."""
        await self.post_comment(
            issue_key=issue_key,
            body=render_run_event(event=event, marker_prefixes=self._marker_prefixes),
        )
        return event

    async def lane_run_events(
        self, *, issue_key: str, lane_key: str
    ) -> Sequence[LaneRunEvent]:
        """Read the whole log with its reply links, then order by creation.

        The reply links are REQUIRED rather than taken where offered: a
        listing that omitted them could not distinguish a threaded decision
        record from a posted event, and an omission would silently widen
        the stream instead of failing.
        """
        return lane_run_events(
            comments=tuple(
                self._to_comment(wire, issue_key=issue_key)
                for wire in await self._comment_wires(issue_key)
            ),
            lane_key=lane_key,
            marker_prefixes=self._marker_prefixes,
        )

    async def read_escalation_resolution(
        self, *, issue_key: str, lane_key: str, escalation_key: str
    ) -> EscalationResolution:
        """Read current native reply links across the entire comment listing."""
        try:
            comments = tuple(
                self._to_comment(wire, issue_key=issue_key)
                for wire in await self._comment_wires(issue_key)
            )
        except (
            TrackerUnavailableError,
            TrackerAccessDeniedError,
            TrackerProtocolError,
            TransientAPIError,
            ValidationError,
        ) as exc:
            raise EscalationReadError(
                issue_key=issue_key,
                lane_key=lane_key,
                escalation_key=escalation_key,
                reason="the tracker read failed or was incomplete",
            ) from exc
        return resolution_from_comments(
            issue_key=issue_key,
            lane_key=lane_key,
            escalation_key=escalation_key,
            prefixes=self._marker_prefixes,
            comments=comments,
        )

    async def upsert_comment(
        self,
        *,
        target: str,
        marker: str,
        body: str,
        holder: str | None = None,
        expected: TrackerComment | None = None,
    ) -> TrackerComment:
        """Resolve the marker through the single attributed, leased writer."""
        return await self._upsert_comment(
            target=target, marker=marker, body=body, holder=holder, expected=expected
        )

    async def _upsert_comment(
        self,
        *,
        target: str,
        marker: str,
        body: str,
        holder: str | None,
        expected: TrackerComment | None = None,
        validate_existing: Callable[[TrackerComment], None] | None = None,
    ) -> TrackerComment:
        """Retry an unsent mutation only after repeating its complete precondition."""

        async def attempt() -> TrackerComment:
            return await self._upsert_comment_once(
                target=target,
                marker=marker,
                body=body,
                holder=holder,
                expected=expected,
                validate_existing=validate_existing,
            )

        return await self._retry_call(_TOOL_SAVE_COMMENT, attempt)

    async def _upsert_comment_once(
        self,
        *,
        target: str,
        marker: str,
        body: str,
        holder: str | None,
        expected: TrackerComment | None,
        validate_existing: Callable[[TrackerComment], None] | None,
    ) -> TrackerComment:
        """Validate the exact addressed snapshot before issuing its mutation.

        The synchronous precondition sees the same comment used by this
        writer, after attribution and ownership checks. The backend offers
        no conditional update to fence changes unseen after that read.
        """
        content = marked_comment_body(marker=marker, body=body)
        existing = comment_under_marker(
            target=target,
            marker=marker,
            comments=await self.list_comments(issue_key=target),
        )
        surface = WritableSurface(
            kind=SurfaceKind.MARKER_COMMENT,
            ref=ScopeRef(kind=ScopeKind.ISSUE, key=target),
            marker=marker,
        )
        authors = None
        if existing is not None and existing.body != content:
            authors = await self.writer_identity()
            if existing.author_key not in authors:
                raise SurfaceWriteAttributionError(
                    surface=surface, author=existing.author_key
                )
        address = _LEASE_ADDRESSING.target(surface)
        wires = await self._comment_wires(address.key, parent_field=address.field)
        self._assert_surface_holder(
            surface=surface,
            holder=holder,
            markers=self._markers_from_wires(
                _GrantKind.LEASE, target=address, wires=wires
            ),
        )
        current_comments = tuple(
            self._to_comment(wire, issue_key=target) for wire in wires
        )
        existing = comment_under_marker(
            target=target, marker=marker, comments=current_comments
        )
        if expected is not None:
            require_expected_comment(
                target=target,
                marker=marker,
                expected=expected,
                current=existing,
                replacement=content,
            )
        if existing is not None and validate_existing is not None:
            validate_existing(existing)
        if existing is not None and existing.body != content:
            if authors is None or existing.author_key not in authors:
                raise SurfaceWriteAttributionError(
                    surface=surface, author=existing.author_key
                )
        if existing is None:
            payload = await self._send(
                _TOOL_SAVE_COMMENT, {"issueId": target, "body": content}
            )
            return self._comment_written(
                issue_key=target, payload=payload, created=True
            )
        if existing.body == content:
            return existing
        payload = await self._send(
            _TOOL_SAVE_COMMENT, {"id": existing.comment_key, "body": content}
        )
        return self._comment_written(issue_key=target, payload=payload, created=False)

    async def _require_surface_holder(
        self, *, surface: WritableSurface, holder: str | None
    ) -> None:
        """Refuse a write whose caller does not own the addressed live marker.

        This reads the same ordered markers as acquisition and renewal.
        It checks authority immediately before issuing the write; the
        backend provides no conditional write that could fence a request
        still in flight when its lease expires.
        """
        target = _LEASE_ADDRESSING.target(surface)
        markers = await self._markers_on(_GrantKind.LEASE, targets=(target,))
        self._assert_surface_holder(surface=surface, holder=holder, markers=markers)

    def _assert_surface_holder(
        self,
        *,
        surface: WritableSurface,
        holder: str | None,
        markers: Sequence[_GrantMarker],
    ) -> None:
        """Apply the existing lease arithmetic to the final native snapshot."""
        encoded = _LEASE_ADDRESSING.encode(surface)
        now = self._clock()
        live = [
            entry
            for entry in markers
            if not entry.retracted
            and entry.in_force
            and entry.deadline > now
            and encoded in entry.addresses
        ]
        owner: str | None = None
        if live:
            earliest = min(live, key=lambda entry: entry.order)
            tying = {
                entry.holder
                for entry in live
                if entry.created_at == earliest.created_at
            }
            if earliest.state is _GrantState.HELD and len(tying) == 1:
                owner = earliest.holder
        if holder is None or owner != holder:
            raise SurfaceLeaseError(
                "the writing job does not hold this live surface",
                surface=surface,
                current_holder=owner,
            )

    async def claim_issue(
        self,
        *,
        issue_key: str,
        holder: str,
        lease_seconds: float,
    ) -> ClaimResult:
        """Take the issue for *holder*, decided by re-reading what was written."""
        outcome = await self._grant(
            addressing=_CLAIM_ADDRESSING,
            addresses=frozenset({issue_key}),
            holder=holder,
            lease_seconds=lease_seconds,
        )
        if isinstance(outcome, _Refused):
            return ClaimResult(
                issue_key=issue_key,
                status=(ClaimStatus.LOST if outcome.settled else ClaimStatus.CONTENDED),
                holder=holder,
                expires_at=outcome.expires_at,
                current_holder=outcome.holder,
            )
        return ClaimResult(
            issue_key=issue_key,
            status=ClaimStatus.GRANTED,
            holder=holder,
            expires_at=outcome.expires_at,
        )

    async def renew_claim(
        self,
        *,
        issue_key: str,
        holder: str,
        lease_seconds: float,
    ) -> ClaimResult | None:
        """Extend the claim *holder* still holds; a lapsed one stays lapsed."""
        extended = await self._extend(
            addressing=_CLAIM_ADDRESSING,
            addresses=frozenset({issue_key}),
            holder=holder,
            lease_seconds=lease_seconds,
        )
        if extended is None:
            return None
        return ClaimResult(
            issue_key=issue_key,
            status=ClaimStatus.GRANTED,
            holder=holder,
            expires_at=extended.expires_at,
        )

    async def release_claim(self, *, issue_key: str, holder: str) -> None:
        """Delete every claim marker *holder* wrote on the issue."""
        await self._withdraw(
            addressing=_CLAIM_ADDRESSING,
            addresses=frozenset({issue_key}),
            holder=holder,
        )

    async def active_claim(self, *, issue_key: str) -> ClaimResult | None:
        """The earliest live claim on the issue, or ``None`` when unclaimed.

        Only a marker its own read-back confirmed is a claim: a bid still
        in its race owns nothing, and neither does one its holder
        retracted.  Two holders whose confirmed markers carry one instant
        are an order the backend did not settle, and reporting either of
        them as the owner would be this adapter inventing one: the issue
        reads unclaimed until they withdraw.

        This is a report and not a grant, and it is the one place the
        reader's own clock is asked anything: nothing the backend answers
        a listing with says what time it is there, and a claim nobody has
        written since would otherwise read live for ever.  The error is
        the skew between two clocks and never the latency of a write, and
        it can hand nobody an issue — every path that GRANTS one weighs
        the board at an instant the backend itself assigned.
        """
        now = self._clock()
        target = _CLAIM_ADDRESSING.target(issue_key)
        markers = [
            marker
            for marker in (await self._markers_on(_GrantKind.CLAIM, targets=(target,)))
            if marker.state is _GrantState.HELD and marker.deadline > now
        ]
        if not markers:
            return None
        earliest = min(markers, key=lambda marker: marker.order)
        tying = {
            marker.holder
            for marker in markers
            if marker.created_at == earliest.created_at
        }
        if len(tying) > 1:
            return None
        return ClaimResult(
            issue_key=issue_key,
            status=ClaimStatus.GRANTED,
            holder=earliest.holder,
            expires_at=max(
                marker.advertised
                for marker in markers
                if marker.holder == earliest.holder
            ),
        )

    async def acquire_surfaces(
        self,
        *,
        surfaces: frozenset[WritableSurface],
        holder: str,
        lease_seconds: float,
    ) -> SurfaceLease:
        """Take the WHOLE set for *holder*, or take nothing and name the owner."""
        outcome = await self._grant(
            addressing=_LEASE_ADDRESSING,
            addresses=surfaces,
            holder=holder,
            lease_seconds=lease_seconds,
        )
        if isinstance(outcome, _Refused):
            raise SurfaceLeaseError(
                (
                    "surface set intersects a live lease"
                    if outcome.settled
                    else "surface set meets a race the backend has not settled"
                ),
                surface=outcome.address,
                current_holder=outcome.holder,
            )
        return SurfaceLease(
            holder=holder,
            surfaces=surfaces,
            expires_at=outcome.expires_at,
        )

    async def renew_surfaces(
        self,
        *,
        surfaces: frozenset[WritableSurface],
        holder: str,
        lease_seconds: float,
    ) -> SurfaceLease | None:
        """Extend the lease *holder* holds over the whole set, or nothing."""
        extended = await self._extend(
            addressing=_LEASE_ADDRESSING,
            addresses=surfaces,
            holder=holder,
            lease_seconds=lease_seconds,
        )
        if extended is None:
            return None
        return SurfaceLease(
            holder=holder,
            surfaces=surfaces,
            expires_at=extended.expires_at,
        )

    async def release_surfaces(
        self,
        *,
        surfaces: frozenset[WritableSurface],
        holder: str,
    ) -> None:
        """Delete the markers *holder* wrote over any of these surfaces."""
        await self._withdraw(
            addressing=_LEASE_ADDRESSING,
            addresses=surfaces,
            holder=holder,
        )

    async def _grant[AddressT](
        self,
        *,
        addressing: _Addressing[AddressT],
        addresses: frozenset[AddressT],
        holder: str,
        lease_seconds: float,
    ) -> _Granted | _Refused[AddressT]:
        """Bid for the whole set, then take it only where a read-back says so.

        The backend orders creations and answers a listing with what it
        has; it offers no conditional write.  So ownership is never
        claimed from the echo of the write.  A marker goes on every target
        as a BID, the whole set is read back, and the bid survives only
        where no other holder's marker over a requested address was
        created no later than this one and is still live at the instant
        the backend stamped this bid.  A bid that survives is then
        confirmed in place and read back a second time, and only that
        second read-back makes it a hold.

        That is what makes a refusal hold nothing without depending on a
        request the backend may turn down: a bid the read-back refused is
        never confirmed, so whether or not its retraction lands, no reader
        will ever answer for it, and it lapses on the bound it declared.
        """
        advertised = self._clock() + timedelta(seconds=lease_seconds)
        nonce = uuid4().hex
        encoded = addressing.lines(addresses)
        targets = addressing.targets(addresses)

        def stated(state: _GrantState) -> str:
            return self._grant_body(
                addressing=addressing,
                holder=holder,
                nonce=nonce,
                state=state,
                lease_seconds=lease_seconds,
                advertised=advertised,
                addresses=encoded,
            )

        written = {
            target: await self._write_marker(
                target=target, body=stated(_GrantState.BID)
            )
            for target in targets
        }
        markers = await self._markers_on(addressing.kind, targets=targets)
        mine = self._own(markers, holder=holder, nonce=nonce)
        if set(mine) != set(written):
            await self._stand_down(
                tuple(
                    _WrittenMarker(
                        target=target,
                        comment_key=key,
                        void_body=stated(_GrantState.VOID),
                    )
                    for target, key in written.items()
                )
            )
            raise TrackerProtocolError(
                "the grant marker is absent from the log it was written to",
                tool=_TOOL_LIST_COMMENTS,
                detail=f"holder={holder!r}; kind={addressing.kind.value}",
            )
        refused = await self._refuse_bid(
            addressing=addressing,
            addresses=addresses,
            markers=markers,
            mine=mine,
            holder=holder,
            expires_at=advertised,
        )
        if refused is not None:
            return refused
        for target, marker in mine.items():
            await self._edit_marker(
                target=target,
                comment_key=marker.comment_key,
                body=stated(_GrantState.HELD),
            )
        after = await self._markers_on(addressing.kind, targets=targets)
        held = self._own(after, holder=holder, nonce=nonce, state=_GrantState.HELD)
        confirmed = set(held) == set(targets)
        refused = await self._refuse_bid(
            addressing=addressing,
            addresses=addresses,
            markers=after,
            mine=held if confirmed else mine,
            holder=holder,
            expires_at=advertised,
            confirmed=confirmed,
        )
        if refused is not None:
            return refused
        # A grant of this holder's own over exactly this set is the same
        # ownership observed twice, never a competitor: the earliest of
        # them stands for all, and this one either is it or withdraws
        # into it.  Both parties read one log and reach one answer, so a
        # holder meeting itself ends holding one marker and never none.
        #
        # Measured on the real board: when the log hid each grant's
        # confirmation from the other's read, both stood, and two markers
        # of ONE holder were left.  That is a duplicate and not a second
        # owner — every reader names the same holder, a rival is refused
        # in that name, and the marker nothing renews lapses on its own —
        # so it is left to lapse rather than compensated for by deleting a
        # marker another live grant of this holder may still be reading.
        covered = frozenset(encoded)
        instant = min(marker.updated_at for marker in held.values())
        grants = _own_grants(after, holder=holder, addresses=covered)
        standing = one_ownership(
            mine=grants[nonce],
            siblings=[
                grant for its_nonce, grant in grants.items() if its_nonce != nonce
            ],
            now=instant,
        )
        if standing is not grants[nonce]:
            return await self._withdraw_into(
                addressing=addressing,
                addresses=addresses,
                holder=holder,
                lease_seconds=lease_seconds,
                mine=held,
                expires_at=advertised,
            )
        # What is left of this holder's earlier attempts owns nothing and
        # nobody but this holder may take it off the log.
        await self._stand_down(
            tuple(
                _retraction(marker, body=self._void_body(marker))
                for marker in after
                if marker.holder == holder
                and marker.nonce != nonce
                and marker.addresses == covered
                and marker.deadline <= instant
            )
        )
        return _Granted(expires_at=advertised)

    async def _withdraw_into[AddressT](
        self,
        *,
        addressing: _Addressing[AddressT],
        addresses: frozenset[AddressT],
        holder: str,
        lease_seconds: float,
        mine: Mapping[_Target, _GrantMarker],
        expires_at: datetime,
    ) -> _Granted | _Refused[AddressT]:
        """Stand this grant down into the holder's own earlier one, and renew it.

        Withdrawing into an ownership is not withdrawing from the set: the
        holder still holds it, under the marker the backend ordered first,
        so this grant takes its own markers back off and then carries that
        one forward for the duration it was asked for.  The renewal is the
        same fenced write every renewal is, which is what keeps a restart
        from resurrecting a grant that lapsed while it was standing down —
        it renews nothing, and the address stays with whoever took it.
        """
        await self._stand_down(
            tuple(
                _retraction(marker, body=self._void_body(marker))
                for marker in mine.values()
            )
        )
        extended = await self._extend(
            addressing=addressing,
            addresses=addresses,
            holder=holder,
            lease_seconds=lease_seconds,
        )
        if extended is None:
            return _Refused(
                address=min(addresses, key=addressing.order),
                holder=None,
                settled=False,
                expires_at=expires_at,
            )
        return extended

    async def _refuse_bid[AddressT](
        self,
        *,
        addressing: _Addressing[AddressT],
        addresses: frozenset[AddressT],
        markers: Sequence[_GrantMarker],
        mine: Mapping[_Target, _GrantMarker],
        holder: str,
        expires_at: datetime,
        confirmed: bool = True,
    ) -> _Refused[AddressT] | None:
        """Retract the whole bid and name what refused it, or hold on.

        A refusal names an OWNER or nobody.  An earlier confirmed grant is
        an owner; a bid still inside its own race and an instant two
        markers shared settle nothing, and neither does a confirmation the
        log did not answer with or one the backend stamped after the bid's
        own bound — a grant that lapsed before it was ever held.
        """
        conflict = self._conflict(
            addressing=addressing,
            addresses=addresses,
            markers=markers,
            mine=mine,
            holder=holder,
        )
        lapsed = any(marker.deadline <= marker.updated_at for marker in mine.values())
        if conflict is None and confirmed and not lapsed:
            return None
        await self._stand_down(
            tuple(
                _retraction(marker, body=self._void_body(marker))
                for marker in mine.values()
            )
        )
        if conflict is None:
            return _Refused(
                address=min(addresses, key=addressing.order),
                holder=None,
                settled=False,
                expires_at=expires_at,
            )
        return _Refused(
            address=conflict.address,
            holder=conflict.holder,
            settled=conflict.settled,
            expires_at=expires_at,
        )

    @staticmethod
    def _own(
        markers: Sequence[_GrantMarker],
        *,
        holder: str,
        nonce: str,
        state: _GrantState | None = None,
    ) -> dict[_Target, _GrantMarker]:
        """This holder's own markers for one grant, one per target."""
        return {
            marker.target: marker
            for marker in markers
            if marker.holder == holder
            and marker.nonce == nonce
            and (state is None or marker.state is state)
        }

    async def _extend[AddressT](
        self,
        *,
        addressing: _Addressing[AddressT],
        addresses: frozenset[AddressT],
        holder: str,
        lease_seconds: float,
    ) -> _Granted | None:
        """Move this holder's own marker forward, or report holding nothing.

        Renewal extends and never acquires, so it starts by reading: a
        holder with no confirmed marker over the whole set writes nothing
        at all, and takes down the litter this very request would have
        left — a marker for exactly this set that is no longer a hold.

        The write itself can outlive the lease it was extending — that is
        the delayed renewal — so the extension states the deadline it was
        published against, in the backend's own clock: the stamp the
        backend put on the write before it, plus the duration that write
        bought.  A backend that stamps this one at or after that deadline
        has renewed nothing, for this holder and for every reader alike,
        so the holder that took the address meanwhile is the sole owner
        from the moment it acquired and stays so whether or not this
        holder's own retraction ever lands.  Nothing here reads a clock
        of the holder's, so neither skew nor the time a write took to
        land can move the fence.
        """
        encoded = frozenset(addressing.lines(addresses))
        targets = addressing.targets(addresses)
        markers = await self._markers_on(addressing.kind, targets=targets)
        mine: dict[_Target, _GrantMarker] = {}
        for marker in markers:
            if (
                marker.holder != holder
                or marker.state is not _GrantState.HELD
                or not encoded <= marker.addresses
            ):
                continue
            standing = mine.get(marker.target)
            if standing is None or marker.order < standing.order:
                mine[marker.target] = marker
        if set(mine) != set(targets):
            await self._stand_down(
                tuple(
                    _retraction(marker, body=self._void_body(marker))
                    for marker in markers
                    if marker.holder == holder and marker.addresses == encoded
                )
            )
            return None
        if (
            self._conflict(
                addressing=addressing,
                addresses=addresses,
                markers=markers,
                mine=mine,
                holder=holder,
            )
            is not None
        ):
            await self._stand_down(
                tuple(
                    _retraction(marker, body=self._void_body(marker))
                    for marker in mine.values()
                )
            )
            return None
        advertised = self._clock() + timedelta(seconds=lease_seconds)
        for target, marker in mine.items():
            await self._edit_marker(
                target=target,
                comment_key=marker.comment_key,
                body=self._grant_body(
                    addressing=addressing,
                    holder=holder,
                    nonce=marker.nonce,
                    state=_GrantState.HELD,
                    lease_seconds=lease_seconds,
                    advertised=advertised,
                    since=marker.deadline,
                    addresses=addressing.lines(addresses),
                ),
            )
        after = await self._markers_on(addressing.kind, targets=targets)
        renewed = {
            marker.target: marker
            for marker in after
            if marker.target in mine
            and marker.nonce == mine[marker.target].nonce
            and marker.holder == holder
            and marker.state is _GrantState.HELD
            and marker.in_force
        }
        if set(renewed) != set(targets) or (
            self._conflict(
                addressing=addressing,
                addresses=addresses,
                markers=after,
                mine=renewed,
                holder=holder,
            )
            is not None
        ):
            await self._stand_down(
                tuple(
                    _retraction(marker, body=self._void_body(marker))
                    for marker in mine.values()
                )
            )
            return None
        return _Granted(expires_at=advertised)

    async def _withdraw[AddressT](
        self,
        *,
        addressing: _Addressing[AddressT],
        addresses: frozenset[AddressT],
        holder: str,
    ) -> None:
        """Delete this holder's markers over these addresses; never another's.

        A release is the caller's own request rather than compensation
        for a decision, so a marker the backend refuses to remove is
        raised: a holder told its release succeeded would stop renewing a
        grant that is still standing.
        """
        encoded = frozenset(addressing.lines(addresses))
        refused = await self._delete_markers(
            tuple(
                _retraction(marker, body=self._void_body(marker))
                for marker in await self._markers_on(
                    addressing.kind, targets=addressing.targets(addresses)
                )
                if marker.holder == holder and marker.addresses & encoded
            )
        )
        if refused:
            raise refused[0][1]

    def _conflict[AddressT](
        self,
        *,
        addressing: _Addressing[AddressT],
        addresses: frozenset[AddressT],
        markers: Sequence[_GrantMarker],
        mine: Mapping[_Target, _GrantMarker],
        holder: str,
    ) -> _Conflict[AddressT] | None:
        """The requested address another holder's earlier LIVE marker covers.

        The instant every marker is weighed at is the one the backend put
        on this holder's own last write to its own marker.  Both sides of
        every comparison are therefore stamps the backend assigned, and no
        conversion between two clocks — nor the time a write took to land,
        which is not a clock offset at all — can move the answer.

        A lapsed marker is not a grant and is not what the address is
        weighed against: only its own holder may take it off, so one left
        behind sits on the log at the earliest order there is, and reading
        the earliest marker of any kind would let it stand in front of the
        holder that took the address after it — answering a live grant
        with the expired one it outlived, and granting the same address
        twice.  A retracted marker answers for nothing at all.
        """
        held: dict[AddressT, _GrantMarker] = {}
        for address in addresses:
            target = addressing.target(address)
            own = mine[target]
            covering = [
                marker
                for marker in markers
                if marker.target == target
                and marker.holder != holder
                and not marker.retracted
                and addressing.encode(address) in marker.addresses
                and marker.created_at <= own.created_at
                and marker.deadline > own.updated_at
            ]
            if covering:
                held[address] = min(covering, key=lambda marker: marker.order)
        conflict = live_conflict(
            requested=addresses,
            held=held,
            holder=holder,
            now=min(own.updated_at for own in mine.values()),
            order=addressing.order,
        )
        if conflict is None:
            return None
        address, owner = conflict
        answering = held[address]
        settled = (
            answering.state is _GrantState.HELD
            and answering.created_at != mine[addressing.target(address)].created_at
        )
        return _Conflict(
            address=address, holder=owner if settled else None, settled=settled
        )

    def _grant_body[AddressT](
        self,
        *,
        addressing: _Addressing[AddressT],
        holder: str,
        nonce: str,
        state: _GrantState,
        lease_seconds: float,
        advertised: datetime,
        since: datetime | None = None,
        addresses: Sequence[str],
    ) -> str:
        """One marker's whole state, so any reader decides from the marker.

        ``lease`` is a duration and not an instant, so the deadline it
        buys is only ever the backend's own stamp on the write plus that
        duration — there is no reading of a holder's clock for anyone to
        convert.  A renewal states ``since``, the deadline it was
        published against, which is itself the backend's stamp on the
        write before it plus the duration that write bought.

        ``expires-at`` is the holder's own account of the same deadline in
        its own clock.  It is what a caller schedules its next renewal
        against and what a person reading the board sees; no arbitration
        reads it, and none may, which is the whole reason it is named
        apart from the fields that decide.
        """
        stated = {
            "kind": addressing.kind.value,
            "holder": holder,
            "nonce": nonce,
            "state": state.value,
            "lease": repr(float(lease_seconds)),
            "expires-at": advertised.isoformat(),
        }
        if since is not None:
            stated["since"] = since.isoformat()
        return self._markers.grant_body(lines=stated, addresses=addresses)

    def _void_body(self, marker: _GrantMarker) -> str:
        """The same marker, retracted in place and answering for nothing."""
        stated = {
            "kind": marker.kind.value,
            "holder": marker.holder,
            "nonce": marker.nonce,
            "state": _GrantState.VOID.value,
            "lease": repr(marker.lease.total_seconds()),
            "expires-at": marker.advertised.isoformat(),
        }
        return self._markers.grant_body(lines=stated, addresses=marker.lines)

    async def _markers_on(
        self, kind: _GrantKind, *, targets: Sequence[_Target]
    ) -> tuple[_GrantMarker, ...]:
        """Every ownership marker of *kind* currently on these targets."""
        found: list[_GrantMarker] = []
        for target in targets:
            wires = await self._comment_wires(target.key, parent_field=target.field)
            found.extend(self._markers_from_wires(kind, target=target, wires=wires))
        return tuple(found)

    def _markers_from_wires(
        self,
        kind: _GrantKind,
        *,
        target: _Target,
        wires: Sequence[LinearCommentEntryWire],
    ) -> tuple[_GrantMarker, ...]:
        """Parse grants once; ordinary acquisition and final writes share this rule."""
        found: list[_GrantMarker] = []
        pattern = self._markers.grant_pattern
        for wire in wires:
            match = pattern.search(wire.body)
            if match is None:
                continue
            marker = self._parsed_marker(match["payload"], wire=wire, target=target)
            if marker.kind is kind:
                found.append(marker)
        return tuple(found)

    def _parsed_marker(
        self, payload: str, *, wire: LinearCommentEntryWire, target: _Target
    ) -> _GrantMarker:
        """One marker's declared fields and addresses, or a protocol refusal.

        The deadline the marker is read by is the one the BACKEND's stamps
        put in force: a bid runs one lease from the creation the backend
        ordered it by, and a renewal the backend stamped at or after the
        deadline it was published against renews nothing and leaves the
        marker where it was — for every reader, including the holder that
        wrote it.
        """
        stated: dict[str, str] = {}
        addresses: list[str] = []
        listing = False
        for line in payload.splitlines():
            if listing:
                if not line.startswith("- "):
                    raise self._malformed_marker(wire, detail=f"address line {line!r}")
                addresses.append(line.removeprefix("- "))
                continue
            if line == "surfaces:":
                listing = True
                continue
            name, separator, value = line.partition(": ")
            if not separator:
                raise self._malformed_marker(wire, detail=f"field line {line!r}")
            stated[name] = value
        missing = {"kind", "holder", "nonce", "state", "lease", "expires-at"} - set(
            stated
        )
        if missing or not addresses:
            raise self._malformed_marker(
                wire, detail=f"absent: {', '.join(sorted(missing) or ['surfaces'])}"
            )
        if stated["kind"] not in _GRANT_KIND_BY_VALUE:
            raise self._malformed_marker(wire, detail=f"kind {stated['kind']!r}")
        if stated["state"] not in _GRANT_STATE_BY_VALUE:
            raise self._malformed_marker(wire, detail=f"state {stated['state']!r}")
        lease = timedelta(seconds=self._parse_seconds(stated["lease"], wire=wire))
        stamped = stated.get("since")
        since = (
            None
            if stamped is None
            else self._parse_instant(stamped, _TOOL_LIST_COMMENTS)
        )
        return _GrantMarker(
            target=target,
            comment_key=wire.id,
            created_at=wire.created_at,
            updated_at=wire.updated_at,
            kind=_GRANT_KIND_BY_VALUE[stated["kind"]],
            state=_GRANT_STATE_BY_VALUE[stated["state"]],
            holder=stated["holder"],
            nonce=stated["nonce"],
            lease=lease,
            since=since,
            deadline=(
                wire.created_at + lease
                if since is None
                else renewed_deadline(
                    published_at=wire.updated_at, lease=lease, since=since
                )
            ),
            advertised=self._parse_instant(stated["expires-at"], _TOOL_LIST_COMMENTS),
            lines=tuple(addresses),
        )

    def _parse_seconds(self, stated: str, *, wire: LinearCommentWire) -> float:
        """A declared duration, which is a number and never an instant."""
        try:
            return float(stated)
        except ValueError as exc:
            raise self._malformed_marker(wire, detail=f"lease {stated!r}") from exc

    def _malformed_marker(
        self, wire: LinearCommentWire, *, detail: str
    ) -> TrackerProtocolError:
        return TrackerProtocolError(
            "ownership marker does not carry the fields it is read by",
            tool=_TOOL_LIST_COMMENTS,
            detail=f"comment={wire.id}; {detail}",
        )

    async def _write_marker(self, *, target: _Target, body: str) -> str:
        """Create one marker on *target* and answer the identity it was given."""
        payload = await self._call(
            _TOOL_SAVE_COMMENT, {target.field: target.key, "body": body}
        )
        wire = self._validate(LinearCommentWire, payload, _TOOL_SAVE_COMMENT)
        assert isinstance(payload, Mapping)
        self._self_writes.record_mutation(
            issue_key=target.key,
            mutation=OwnMutation(created=((wire.id, field_values(payload)),)),
        )
        return wire.id

    async def _edit_marker(
        self, *, target: _Target, comment_key: str, body: str
    ) -> None:
        """Rewrite one marker in place, which is what preserves its order."""
        payload = await self._call(
            _TOOL_SAVE_COMMENT, {"id": comment_key, "body": body}
        )
        assert isinstance(payload, Mapping)
        self._self_writes.record_mutation(
            issue_key=target.key,
            mutation=OwnMutation(
                edited=(
                    (
                        comment_key,
                        field_values(
                            {
                                key: value
                                for key, value in payload.items()
                                if key in {"body", "updatedAt"}
                            }
                        ),
                    ),
                )
            ),
        )

    async def _delete_markers(
        self, markers: Sequence[_WrittenMarker]
    ) -> Sequence[tuple[_WrittenMarker, Exception]]:
        """Take every one of these markers off; answer with what stayed on.

        A marker the backend refuses stops nothing: the rest are still
        this holder's to withdraw, and abandoning them would leave grants
        standing that only this holder can remove.  Whether a refusal is
        the caller's answer or only a fact to record is the caller's to
        decide, so it is answered rather than raised.
        """
        refused: list[tuple[_WrittenMarker, Exception]] = []
        for marker in markers:
            try:
                await self._delete_own_comment(
                    issue_key=marker.target.key, comment_key=marker.comment_key
                )
            except (
                TrackerAccessDeniedError,
                TrackerUnavailableError,
                TransientAPIError,
            ) as exc:
                refused.append((marker, exc))
        return tuple(refused)

    async def _stand_down(self, markers: Sequence[_WrittenMarker]) -> None:
        """Retract a grant this holder has already been told it does not have.

        Two requests, because the backend answers them independently and
        binds neither to the write they compensate for.  The body is
        rewritten as retracted FIRST, which takes the marker out of every
        reader's arithmetic without needing the log to shrink, and the
        marker is then deleted, which is only tidiness.  The outcome is
        decided before either, so a backend that refuses one cannot turn a
        decided outcome into a transport failure: what stayed on the board
        is recorded for the operator.

        A refusal of BOTH leaves a marker that still holds nothing — a bid
        was never a hold, and a confirmed marker retracted here was
        already outranked by an earlier grant — and it lapses on the bound
        it declared without anybody having to act.
        """
        for marker in markers:
            try:
                await self._edit_marker(
                    target=marker.target,
                    comment_key=marker.comment_key,
                    body=marker.void_body,
                )
            except (
                TrackerAccessDeniedError,
                TrackerUnavailableError,
                TransientAPIError,
            ) as exc:
                await self._log.aerror(
                    "tracker_retraction_incomplete",
                    tool=_TOOL_SAVE_COMMENT,
                    detail=str(exc),
                    comments=[marker.comment_key],
                )
        refused = await self._delete_markers(markers)
        if refused:
            await self._log.aerror(
                "tracker_withdrawal_incomplete",
                tool=_TOOL_DELETE_COMMENT,
                detail=str(refused[0][1]),
                comments=[marker.comment_key for marker, _ in refused],
            )

    async def list_issue_assets(self, *, issue_key: str) -> Sequence[TrackerAsset]:
        """Attachment and document metadata referenced by the issue."""
        wire = await self._read_issue_wire(issue_key)
        assets = []
        for asset in (*wire.attachments, *wire.documents):
            url = asset.url
            if url is None:
                payload = await self._call(_TOOL_GET_DOCUMENT, {"id": asset.id})
                document = self._validate(LinearAssetWire, payload, _TOOL_GET_DOCUMENT)
                if document.id != asset.id or document.title != asset.title:
                    raise TrackerProtocolError(
                        "document metadata differs from the issue reference",
                        tool=_TOOL_GET_DOCUMENT,
                        detail=f"expected document {asset.id!r}",
                    )
                url = document.url
            assets.append(
                TrackerAsset(
                    asset_key=asset.id,
                    title=asset.title,
                    url=url,
                    content_type=asset.content_type,
                    size_bytes=asset.size,
                )
            )
        return tuple(assets)

    async def read_document(self, *, document_key: str) -> str:
        """The document's text content."""
        payload = await self._call(_TOOL_GET_DOCUMENT, {"id": document_key})
        return self._validate(
            LinearDocumentWire,
            payload,
            _TOOL_GET_DOCUMENT,
        ).content

    async def record_work_ref(self, *, ref: WorkRef) -> None:
        """Append a work-ref marker comment; the read is ``work_refs``.

        The comment log is the same append-only, server-timestamped surface
        the claim mechanism uses; a work ref is a second marker on it.  The
        sha attribute is OMITTED when the ref is not pushed, so ``None``
        round-trips as ``None`` rather than as an empty string.
        """
        existing = await self.work_refs(issue_key=ref.issue_id)
        for held in existing:
            if held.identity() == ref.identity():
                return
            if held.role is WorkRefRole.DELIVERABLE is ref.role:
                raise DuplicateWorkRefError(
                    "an issue carries at most one deliverable ref",
                    issue_id=ref.issue_id,
                    role=ref.role.value,
                    existing_branch=held.branch,
                    offered_branch=ref.branch,
                )
        payload = await self._call(
            _TOOL_SAVE_COMMENT,
            {"issueId": ref.issue_id, "body": self._markers.work_ref_body(ref)},
        )
        self._comment_written(issue_key=ref.issue_id, payload=payload, created=True)

    async def work_refs(self, *, issue_key: str) -> Sequence[WorkRef]:
        """Every work ref recorded on the issue, oldest first."""
        refs: list[WorkRef] = []
        pattern = self._markers.work_ref_pattern
        marker = self._markers.work_ref_marker_pattern
        for wire in await self._comment_wires(issue_key):
            occurrences = tuple(marker.finditer(wire.body))
            if not occurrences:
                continue
            match = pattern.search(wire.body)
            if match is None or len(occurrences) != 1:
                raise TrackerProtocolError(
                    "work-ref marker is malformed or repeated",
                    tool=_TOOL_LIST_COMMENTS,
                    detail=wire.id,
                )
            role = _WORK_REF_ROLE_BY_VALUE.get(match.group("role"))
            if role is None:
                raise TrackerProtocolError(
                    "work-ref marker names an unknown role",
                    tool=_TOOL_LIST_COMMENTS,
                    detail=match.group("role"),
                )
            try:
                landing = match.group("landing")
                ref = WorkRef(
                    issue_id=issue_key,
                    role=role,
                    branch=match.group("branch"),
                    pushed_head_sha=match.group("sha"),
                    landing=(
                        WorkRefLanding.UNKNOWN
                        if landing is None
                        else WorkRefLanding(landing)
                    ),
                    recorded_at=wire.created_at,
                )
            except ValueError as exc:
                raise TrackerProtocolError(
                    "work-ref marker does not match its declared shape",
                    tool=_TOOL_LIST_COMMENTS,
                    detail=wire.id,
                ) from exc
            refs.append(ref)
        return tuple(refs)

    async def record_base_spec(self, *, issue_key: str, spec: BaseSpec) -> None:
        """Append a base-spec marker; the read is ``read_base_spec``.

        Idempotent for an unchanged spec: re-recording what is already the
        latest writes nothing, so a pass that re-resolves the same base
        does not grow the log.
        """
        if await self.read_base_spec(issue_key=issue_key) == spec:
            return
        payload = await self._call(
            _TOOL_SAVE_COMMENT,
            {"issueId": issue_key, "body": self._markers.base_spec_body(spec)},
        )
        self._comment_written(issue_key=issue_key, payload=payload, created=True)

    async def read_base_spec(self, *, issue_key: str) -> BaseSpec | None:
        """The latest recorded spec, or ``None`` when none was ever recorded.

        Latest wins, because the log is append-only and a lane dispatched
        twice was dispatched on the base of the second dispatch.  A marker
        the model cannot read is a protocol error and never a ``None``:
        "no spec recorded" and "a spec recorded in a shape I do not
        understand" are different states and only one of them is a first
        dispatch.
        """
        latest: BaseSpec | None = None
        pattern = self._markers.base_spec_pattern
        for wire in await self._comment_wires(issue_key):
            match = pattern.search(wire.body)
            if match is None:
                continue
            try:
                latest = BaseSpec.model_validate_json(match.group("payload"))
            except ValidationError as exc:
                raise TrackerProtocolError(
                    "base-spec marker does not match its declared shape",
                    tool=_TOOL_LIST_COMMENTS,
                    detail=str(exc),
                ) from exc
        return latest

    async def recorded_repository(self, *, issue_key: str) -> str | None:
        """The latest recorded target repository, or ``None`` when none is.

        Latest wins on the same append-only comment log the claim, the
        work refs and the base spec already ride: a re-staged fire is
        re-routed by its newest record.  Read regardless of
        author — the marker is judgment's to write and anyone's to
        correct, so authorship is deliberately not checked here.
        """
        latest: str | None = None
        pattern = self._markers.repository_pattern
        for wire in await self._comment_wires(issue_key):
            match = pattern.search(wire.body)
            if match is not None:
                latest = match.group("url")
        return latest

    async def initiative_identifiers(self, *, project_id: str) -> frozenset[str]:
        """Every name and id of every initiative the project belongs to.

        One ``get_project`` read per ask; the dispatch caller caches per
        distinct project for its own lifetime, because
        initiative membership does not move under a running pass and a
        read per issue would pay the same answer repeatedly.
        """
        payload = await self._call(_TOOL_GET_PROJECT, {"query": project_id})
        wire = self._validate(LinearProjectWire, payload, _TOOL_GET_PROJECT)
        return frozenset(
            identifier for ref in wire.initiatives for identifier in (ref.id, ref.name)
        )

    async def resolve_mappings(
        self,
        *,
        refs: Sequence[MappingRef],
    ) -> Sequence[MappingRef]:
        """The subset of *refs* the workspace does not resolve.

        A ref carrying no identifier resolves to nothing by construction —
        it names something the workspace has not assigned a value to yet —
        so it is reported rather than looked up.

        A USER resolves under either identity the workspace answers to,
        its account name or its mention handle, and the configured
        spelling may carry the mention's leading ``@``.  What comes BACK
        unresolved is the ref exactly as configured,
        so the refusal names the spelling the operator wrote rather than
        an internal form nothing in their config contains.

        A workflow state is resolved PER TEAM and must resolve on EVERY
        team the operation declares.  A state one declared team cannot
        express is not a
        narrower vocabulary, it is a hole exactly where the lifecycle
        writer sets that state on an issue dispatched from that team, so
        a vocabulary the operation's teams do not share is refused HERE,
        naming the team and the state, rather than surviving boot to fail
        on a live issue.  A state no declared team holds at all is the
        ordinary unresolved case and is reported through the return value
        like every other kind, because there is no one team to name.
        """
        known: dict[MappingKind, frozenset[str]] = {}
        states_by_team: Mapping[str, frozenset[str]] | None = None
        unresolved: list[MappingRef] = []
        divergent: list[str] = []
        for ref in refs:
            if ref.kind is MappingKind.SCOPE_LABEL and ref.scope is not None:
                unresolved.append(ref)
                continue
            if ref.kind is MappingKind.WORKFLOW_STATE:
                if states_by_team is None:
                    states_by_team = await self._workflow_states_by_team()
                # No declared team is no vocabulary to resolve against: the
                # tool cannot be called without one, so nothing was checked
                # and nothing may pass as checked.
                if not states_by_team:
                    unresolved.append(ref)
                    continue
                absent = [
                    team
                    for team, states in states_by_team.items()
                    if ref.identifier is None or ref.identifier not in states
                ]
                if not absent:
                    continue
                if len(absent) == len(states_by_team):
                    unresolved.append(ref)
                    continue
                divergent.extend(
                    f"{ref.describe()} on team {team!r}" for team in absent
                )
                continue
            if ref.kind not in known:
                known[ref.kind] = await self._identifiers_of(ref.kind)
            identifier = ref.identifier
            if identifier is not None and ref.kind is MappingKind.USER:
                identifier = _without_mention_syntax(identifier)
            if identifier is None or identifier not in known[ref.kind]:
                unresolved.append(ref)
        if divergent:
            raise TrackerBootValidationError(
                "the operation's teams do not share one workflow-state "
                "vocabulary, so the lifecycle writer cannot set a declared "
                "state on every board it dispatches from",
                unresolved=divergent,
            )
        return tuple(unresolved)

    async def ensure_mappings(
        self,
        *,
        refs: Sequence[MappingRef],
    ) -> Sequence[MappingOutcome]:
        """Instate every OWNED ref, creating only what is absent.

        Queue states are labels here.  A label already carrying the
        configured name is adopted verbatim — never renamed, never
        recoloured, never re-scoped — so a second boot over the same
        workspace writes nothing at all.

        R8's definition of "an existing definition" is ``(name, container)``,
        which is exactly what a create writes, and the container is the
        LISTING that answered with the label rather than any field on the
        entry.  So a ref resolves WITHIN the container it declares: its own
        team's label is adopted, and a team whose listing lacks the member
        is given its own, team-scoped.  Another declared team's copy is
        that board's definition and settles nothing here — an operation
        whose boards each carry their own queue vocabulary is the ordinary
        two-team shape, not a conflict.

        A workspace-level label is adopted by a ref of any scope: it is
        already addressable on every board.  What is refused is the pair —
        a workspace-level definition BESIDE team-level ones, where which
        one a write resolves to is undecidable — and a ref belonging to the
        workspace while the value is defined inside containers, which is
        the same undecidability approached from the other side.  Both name
        every container found and write nothing, for that ref and for every
        ref after it, since the loop aborts.

        What no listing carried is CREATED, even when the workspace holds
        the name somewhere no declared team owns.  That container is
        unobservable — no read this adapter is licensed to make reports it
        — and a name already defined in the container being written to is
        refused by the vendor itself, loudly.  Tolerating that refusal here
        would be a guess about a container nothing observed.

        Documents are instated by TITLE and carry a server-assigned id, so
        their arm of R8's definition is ``(title, id)`` and the outcome
        reports the id the workspace holds.  The document listing is read
        only when a document ref is present: a boot that declares none pays
        for none.
        """
        outcomes: list[MappingOutcome] = []
        definitions = await self._label_definitions()
        scope_definitions = (
            await self._scope_label_definitions(definitions)
            if any(ref.kind is MappingKind.SCOPE_LABEL for ref in refs)
            else {}
        )
        documents = (
            await self._document_definitions()
            if any(ref.kind is MappingKind.DOCUMENT for ref in refs)
            else {}
        )
        for ref in refs:
            if ref.kind not in INSTATABLE_MAPPING_KINDS:
                raise TrackerEnsureConflictError(
                    "this kind belongs to no field the operation owns",
                    entry=ref.describe(),
                )
            if ref.kind is MappingKind.DOCUMENT:
                outcomes.append(await self._ensure_document(ref, documents))
                continue
            if ref.kind is MappingKind.SCOPE_LABEL:
                outcomes.append(
                    await self._ensure_scope_label(ref, definitions, scope_definitions),
                )
                continue
            identifier = ref.identifier
            if identifier is None:
                raise TrackerEnsureConflictError(
                    "this kind is declared by its own identifier and this ref "
                    "carries none",
                    entry=ref.describe(),
                )
            declared = (
                None if ref.scope is None else await self._team_container(ref.scope)
            )
            held = definitions.teams_holding(identifier)
            if definitions.workspace_holds(identifier) and held:
                raise TrackerEnsureConflictError(
                    "the workspace defines this value at workspace level AND "
                    f"inside a container; declared {ref.scope!r}, found the "
                    f"workspace and {', '.join(repr(team) for team in held)}",
                    entry=ref.describe(),
                )
            if definitions.serves(identifier, ref.scope):
                outcomes.append(
                    MappingOutcome(
                        ref=ref,
                        action=EnsureAction.ADOPTED,
                        identifier=identifier,
                    ),
                )
                continue
            if ref.scope is None and held:
                raise TrackerEnsureConflictError(
                    "this ref belongs to the workspace and the value is "
                    f"defined inside a container; declared {ref.scope!r}, "
                    f"found {', '.join(repr(team) for team in held)}",
                    entry=ref.describe(),
                )
            await self._call(
                _TOOL_CREATE_ISSUE_LABEL,
                _label_arguments(identifier, declared),
            )
            definitions.record(identifier, ref.scope)
            outcomes.append(
                MappingOutcome(
                    ref=ref,
                    action=EnsureAction.CREATED,
                    identifier=identifier,
                ),
            )
            await self._log.ainfo(
                "tracker_queue_label_created",
                name=ref.name,
                label=identifier,
                team=ref.scope,
            )
        return tuple(outcomes)

    async def _scope_label_definitions(
        self,
        issue_definitions: _LabelListings,
    ) -> dict[str, set[str]]:
        """Keep native namespaces apart: one label id cannot stand for all."""
        return {
            tool: (
                issue_definitions.workspace
                if tool == _TOOL_LIST_ISSUE_LABELS
                else {entry.name for entry in await self._label_entries({}, tool=tool)}
            )
            for tool in _SCOPE_LABEL_CREATORS
        }

    async def _ensure_scope_label(
        self,
        ref: MappingRef,
        issues: _LabelListings,
        definitions: dict[str, set[str]],
    ) -> MappingOutcome:
        """Create missing definitions only; never apply approval to an entity.

        Scope labels are workspace-level, including the issue namespace.
        A declared team's own copy is refused before any namespace write:
        preserving it and adding a workspace copy would leave issue writes
        ambiguous. Undeclared teams remain unobservable, as for queue labels.

        Create replies were not captured by the connected-app measurement.
        Resolve the name through a fresh listing instead of inventing a
        write-response schema or treating a successful call as readback.
        """
        identifier = ref.identifier
        if identifier is None or ref.scope is not None:
            raise TrackerEnsureConflictError(
                "a scope label requires its configured identifier and workspace scope",
                entry=ref.describe(),
            )
        held = issues.teams_holding(identifier)
        if held:
            raise TrackerEnsureConflictError(
                "a workspace scope label conflicts with an existing team label; "
                f"found {', '.join(repr(team) for team in held)}",
                entry=ref.describe(),
            )
        action = EnsureAction.ADOPTED
        for tool, creator in _SCOPE_LABEL_CREATORS.items():
            names = definitions[tool]
            if identifier in names:
                continue
            await self._call(creator, {"name": identifier})
            observed = {
                entry.name for entry in await self._label_entries({}, tool=tool)
            }
            if identifier not in observed:
                raise TrackerProtocolError(
                    "the created scope label is absent from its namespace readback",
                    tool=tool,
                    detail=ref.describe(),
                )
            names.clear()
            names.update(observed)
            action = EnsureAction.CREATED
        return MappingOutcome(ref=ref, action=action, identifier=identifier)

    async def _ensure_document(
        self,
        ref: MappingRef,
        definitions: dict[str, str],
    ) -> MappingOutcome:
        """Adopt the declared document, or create one carrying its title.

        Four refusals, and each names a different fact: a declared id the
        workspace does not hold (creating a second document would leave the
        config pointing at neither), a declared id whose document carries
        another title (serving this ref would rename somebody's document),
        a title two documents share (adopting either one is a coin toss the
        operator did not ask for), and a create with no declared container
        (the backend files every document in one and refuses a bare create
        — refused HERE, before the call, rather than after the transport
        retries a deterministic vendor refusal).
        """
        if ref.identifier is not None:
            title = definitions.get(ref.identifier)
            if title is None:
                raise TrackerEnsureConflictError(
                    "the workspace holds no document with this identifier",
                    entry=ref.describe(),
                )
            if title != ref.name:
                raise TrackerEnsureConflictError(
                    "the workspace holds this document under another title; "
                    f"declared {ref.name!r}, found {title!r}",
                    entry=ref.describe(),
                )
            return MappingOutcome(
                ref=ref,
                action=EnsureAction.ADOPTED,
                identifier=ref.identifier,
            )
        held = sorted(
            identifier for identifier, title in definitions.items() if title == ref.name
        )
        if len(held) > 1:
            raise TrackerEnsureConflictError(
                "the workspace holds several documents under this title",
                entry=ref.describe(),
            )
        if held:
            return MappingOutcome(
                ref=ref,
                action=EnsureAction.ADOPTED,
                identifier=held[0],
            )
        if ref.scope is None:
            raise TrackerEnsureConflictError(
                "creating this document needs a container: the backend files "
                "every document in one and refuses a create naming none "
                "(KOD-166); declare the entry's container (a declared team) "
                "or pre-create the document and declare its id",
                entry=ref.describe(),
            )
        payload = await self._call(
            _TOOL_SAVE_DOCUMENT,
            {"title": ref.name, "team": ref.scope},
        )
        created = self._validate(
            LinearDocumentSummaryWire,
            payload,
            _TOOL_SAVE_DOCUMENT,
        )
        definitions[created.id] = created.title
        await self._log.ainfo(
            "tracker_document_created",
            title=ref.name,
            document=created.id,
        )
        return MappingOutcome(
            ref=ref,
            action=EnsureAction.CREATED,
            identifier=created.id,
        )

    async def _label_definitions(self) -> _LabelListings:
        """Every queue-state label the workspace resolves, by listing.

        The workspace-level listing plus one team-scoped listing per
        DECLARED team, because the unscoped call answers with the
        workspace-level labels ALONE.  A boot that read only that one
        re-created the team-scoped label its own previous boot had made,
        and the vendor refused it.  Idempotence comes from reading both
        listings, never
        from forgiving that refusal.

        Not a union, though: a team's listing carries the workspace-level
        labels too, so each team's OWN labels are its listing minus the
        workspace one, subtracted BY ID.  By name would subtract nothing —
        the shared name is exactly what makes the two shapes look alike —
        and taking the listing whole makes every workspace label look
        team-held, which refused a healthy workspace.

        One call per declared team, for the same reason the workflow-state
        vocabulary is read that way: the tool answers for one team, so
        several teams are several answers and no listing spans them.  The
        teams are named the way the configuration names them — ``team``
        takes "name or ID", and only ``create_issue_label.teamId`` insists
        on the UUID.
        """
        held = await self._label_entries({})
        workspace_ids = {entry.id for entry in held}
        by_team = {
            identifier: {
                entry.name
                for entry in await self._label_entries({"team": identifier})
                if entry.id not in workspace_ids
            }
            for identifier in sorted(set(self._team_identifiers.values()))
        }
        return _LabelListings(
            workspace={entry.name for entry in held},
            by_team=by_team,
        )

    async def _label_entries(
        self,
        arguments: Mapping[str, object],
        *,
        tool: str = _TOOL_LIST_ISSUE_LABELS,
    ) -> Sequence[LinearLabelWire]:
        """Every page of one label namespace, preserving the listing scope."""
        entries: list[LinearLabelWire] = []

        async def read(
            request: Mapping[str, object],
        ) -> tuple[LinearLabelListWire, bool, str | None]:
            payload = await self._call(tool, request)
            listing = self._validate(LinearLabelListWire, payload, tool)
            return listing, listing.has_next_page, listing.cursor

        async for listing in cursor_pages(
            read,
            arguments=arguments,
            refusal=lambda cursor: TrackerProtocolError(
                "label pagination did not provide a new continuation cursor",
                tool=tool,
                detail=f"cursor={cursor!r}",
            ),
        ):
            entries.extend(listing.labels)
        return entries

    async def _team_listing(self) -> Sequence[LinearTeamWire]:
        """Every team the workspace holds, with the UUID it is addressed by."""
        tool = _MAPPING_TOOL_BY_KIND[MappingKind.TEAM]
        payload = await self._call(tool, {})
        return self._validate(LinearTeamListWire, payload, tool).teams

    async def _team_container(self, name: str) -> str:
        """The UUID the workspace addresses the team *name* by.

        Read once and held: a team's identifier does not move under a
        running process, and the ensure loop asks for the same one per
        declared queue state.

        This translation exists because exactly one argument this adapter
        sends demands the UUID form — ``create_issue_label.teamId``.
        Everywhere else the vendor takes "name or ID", which is why the
        operation config names teams the way a person does and why this
        does not belong in that config.
        """
        cached = self._team_containers
        if cached is None:
            cached = {entry.name: entry.id for entry in await self._team_listing()}
            self._team_containers = cached
        container = cached.get(name)
        if container is None:
            raise TrackerProtocolError(
                "the workspace holds no team under this name",
                tool=_TOOL_CREATE_ISSUE_LABEL,
                detail=f"team={name!r}",
            )
        return container

    async def _document_definitions(self) -> dict[str, str]:
        """Every document the workspace holds, id to title."""
        payload = await self._call(_TOOL_LIST_DOCUMENTS, {})
        listing = self._validate(
            LinearDocumentListWire,
            payload,
            _TOOL_LIST_DOCUMENTS,
        )
        return {entry.id: entry.title for entry in listing.documents}

    async def _identifiers_of(self, kind: MappingKind) -> frozenset[str]:
        """Every identifier the workspace resolves for *kind*.

        A document is addressed by its id and everything else by its name,
        which is why this is not one listing read one way.

        A user answers to TWO names — the account name and the mention
        handle — and both are identities a config may legitimately carry,
        so both are here.  No other kind has a second spelling.

        A queue state resolves against the WHOLE union of label listings.
        The refs this answers carry no container — the validation pass
        names what the workspace must hold, not where — so a label on a
        declared team resolves one as readily as a workspace-level label
        does.  Reading the unscoped listing alone left the boot that had
        just created a team-scoped label unable to see it.

        A workflow state has no answer here and says so: it resolves per
        team, against a vocabulary this listing cannot express, and the
        caller routes it away before reaching this call.  Exhaustive over
        the vocabulary with no default arm, so a kind added later fails
        to type-check rather than being answered with team names.
        """
        match kind:
            case MappingKind.DOCUMENT:
                return frozenset(await self._document_definitions())
            case MappingKind.QUEUE_STATE | MappingKind.ISSUE_LABEL:
                return (await self._label_definitions()).names()
            case MappingKind.SCOPE_LABEL:
                issues = await self._label_definitions()
                definitions = await self._scope_label_definitions(issues)
                shared = set.intersection(*definitions.values())
                return frozenset(
                    name for name in shared if not issues.teams_holding(name)
                )
            case MappingKind.USER:
                return frozenset(
                    identity
                    for entry in await self._user_listing()
                    for identity in (entry.name, entry.display_name)
                )
            case MappingKind.TEAM:
                return frozenset(entry.name for entry in await self._team_listing())
            case MappingKind.WORKFLOW_STATE:
                msg = (
                    "a workflow state resolves per team and has no "
                    "workspace-wide identifier listing; its caller reads "
                    "_workflow_states_by_team instead of reaching here"
                )
                raise RuntimeError(msg)

    async def _workflow_states_by_team(self) -> Mapping[str, frozenset[str]]:
        """The workflow-state vocabulary of each DECLARED team, held apart.

        One call per declared team, because the tool takes one: its input
        schema declares ``team`` required, and a call without it is a 400
        rather than a workspace-wide answer.  The vendor replies with a
        BARE ARRAY of ``{id, type, name}`` — no envelope to unwrap.

        The results are never merged.  These are per-team entities on this
        backend, so a union would let a state one team holds stand in for
        a team that cannot express it, and the operation would boot with a
        hole exactly where the lifecycle writer needs that state.  The
        adapter reads the NAME because the name is what it writes back:
        ``save_issue`` takes a state by name, so the id is a field nothing
        on this path has a use for.

        Empty when the operation declares no team, which is not an empty
        vocabulary — it is no vocabulary read at all, and the caller
        treats it as such.
        """
        tool = _MAPPING_TOOL_BY_KIND[MappingKind.WORKFLOW_STATE]
        by_team: dict[str, frozenset[str]] = {}
        for identifier in sorted(set(self._team_identifiers.values())):
            payload = await self._call(tool, {"team": identifier})
            by_team[identifier] = frozenset(
                entry.name for entry in self._validate_named_array(payload, tool)
            )
        return by_team

    async def _user_listing(self) -> Sequence[LinearUserWire]:
        """Every user the workspace holds, under both names it answers to.

        One method per listing, because that is what the server sends:
        each list tool keys its array after itself and there is no shared
        envelope to read generically.  Nothing dispatches over the kind
        here — a listing that answers for one team only
        (:meth:`_workflow_states_by_team`) and one that answers a
        different set scoped than unscoped (:meth:`_label_definitions`)
        are not the same act as this one, and pretending otherwise is what
        hid both of those from their readers.
        """
        tool = _MAPPING_TOOL_BY_KIND[MappingKind.USER]
        payload = await self._call(tool, {})
        return self._validate(LinearUserListWire, payload, tool).users

    async def _read_issue_wire(self, issue_key: str) -> LinearIssueDetailWire:
        payload = await self._call(
            _TOOL_GET_ISSUE,
            {"id": issue_key, "includeRelations": True},
        )
        return self._validate(LinearIssueDetailWire, payload, _TOOL_GET_ISSUE)

    async def _comment_wires(
        self,
        issue_key: str,
        *,
        parent_field: str = "issueId",
    ) -> Sequence[LinearCommentEntryWire]:
        arguments: dict[str, object] = {parent_field: issue_key}
        comments: dict[str, LinearCommentEntryWire] = {}

        async def read(
            request: Mapping[str, object],
        ) -> tuple[LinearCommentListWire, bool, str | None]:
            payload = await self._call(_TOOL_LIST_COMMENTS, request)
            listing = self._validate(
                LinearCommentListWire, payload, _TOOL_LIST_COMMENTS
            )
            return listing, listing.has_next_page, listing.cursor

        async for listing in cursor_pages(
            read,
            arguments=arguments,
            refusal=lambda cursor: TrackerProtocolError(
                "comment listing cannot advance to its next page",
                tool=_TOOL_LIST_COMMENTS,
                detail=f"target={issue_key}; cursor={cursor!r}",
            ),
        ):
            for comment in listing.comments:
                previous = comments.get(comment.id)
                if previous is not None and previous != comment:
                    raise TrackerProtocolError(
                        "comment changed across listing pages",
                        tool=_TOOL_LIST_COMMENTS,
                        detail=f"target={issue_key}; comment={comment.id}",
                    )
                comments[comment.id] = comment
        return tuple(sorted(comments.values(), key=lambda c: (c.created_at, c.id)))

    async def _call(
        self,
        tool: str,
        arguments: Mapping[str, object],
    ) -> McpToolResult:
        async def attempt() -> McpToolResult:
            return await self._send(tool, arguments)

        return await self._retry_call(tool, attempt)

    async def _send(self, tool: str, arguments: Mapping[str, object]) -> McpToolResult:
        """Issue exactly one transport attempt; its owner supplies the retry scope."""
        if tool == _TOOL_SAVE_ISSUE:
            # Every issue write funnels through here, so the refusal is
            # stated once and no future write path can route around it.
            refuse_combined_issue_write(arguments)
        return await self._caller.call_tool(name=tool, arguments=arguments)

    async def _retry_call[ResultT](
        self, tool: str, invoke: Callable[[], Awaitable[ResultT]]
    ) -> ResultT:
        """Use the existing policy around one complete, safe-to-repeat attempt.

        Protected mutations include their fresh preconditions in ``invoke``.
        They end at the write receipt; subsequent awaited readback belongs
        outside this scope so a read failure cannot resend a completed write.
        """
        attempt = 0
        while True:
            try:
                return await invoke()
            except McpCredentialRefusedError as exc:
                # Named once and raised, never retried: the refusal is the
                # same on every attempt, so a budget spent on it buys the
                # first answer again and delays the one event an operator
                # can act on by the whole back-off.
                await self._log.aerror(
                    "tracker_credential_refused",
                    tool=tool,
                    server_name=exc.server_name,
                )
                raise TrackerAccessDeniedError(str(exc)) from exc
            except (McpTransportError, TransientAPIError) as exc:
                if attempt + 1 >= self._retry.attempts or not _may_resend(tool, exc):
                    raise TrackerUnavailableError(str(exc)) from exc
                delay = self._retry.delay(attempt)
                await self._log.awarning(
                    "tracker_mcp_retry",
                    tool=tool,
                    attempt=attempt,
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)
                attempt += 1

    def _validate[WireT: LinearWireModel](
        self,
        shape: type[WireT],
        payload: McpToolResult,
        tool: str,
    ) -> WireT:
        try:
            return shape.model_validate(payload)
        except ValidationError as exc:
            raise TrackerProtocolError(
                "tracker response does not match its declared shape",
                tool=tool,
                detail=str(exc),
            ) from exc

    def _validate_named_array(
        self,
        payload: McpToolResult,
        tool: str,
    ) -> Sequence[LinearNamedWire]:
        """The bare-array listing shape, refused on the same terms."""
        try:
            return LINEAR_NAMED_ARRAY.validate_python(payload)
        except ValidationError as exc:
            raise TrackerProtocolError(
                "tracker response does not match its declared shape",
                tool=tool,
                detail=str(exc),
            ) from exc

    def _label_for(self, state: QueueState) -> str:
        label = self._label_by_queue_state.get(state)
        if label is None:
            raise TrackerProtocolError(
                "no tracker label is configured for this queue state",
                tool=_TOOL_SAVE_ISSUE,
                detail=f"queue_state={state.value}",
            )
        return label

    def _team_identifier(self, team_key: str) -> str:
        identifier = self._team_identifiers.get(team_key)
        if identifier is None:
            raise TrackerProtocolError(
                "no tracker team is configured under this key",
                tool=_TOOL_LIST_ISSUES,
                detail=f"team_key={team_key}",
            )
        return identifier

    def _parse_instant(self, raw: str, tool: str) -> datetime:
        try:
            return datetime.fromisoformat(raw)
        except ValueError as exc:
            raise TrackerProtocolError(
                "tracker timestamp is not an ISO-8601 instant",
                tool=tool,
                detail=f"value={raw!r}",
            ) from exc

    def _to_issue(self, wire: LinearIssueWire) -> TrackerIssue:
        priority = _PRIORITY_BY_RAW.get(wire.priority.value)
        if priority is None:
            raise TrackerProtocolError(
                "tracker priority value has no domain mapping",
                tool=_TOOL_GET_ISSUE,
                detail=f"issue={wire.id} raw={wire.priority.value}",
            )
        state_kind = _STATE_KIND_BY_VALUE.get(wire.status_type)
        if state_kind is None:
            raise TrackerProtocolError(
                "tracker workflow state kind has no domain mapping",
                tool=_TOOL_GET_ISSUE,
                detail=f"issue={wire.id} status_type={wire.status_type!r}",
            )
        relations: list[IssueRelation] = []
        if wire.relations is not None:
            for arm, edges in wire.relations.arms():
                kind = _RELATION_KIND_BY_ARM[arm]
                relations.extend(
                    IssueRelation(kind=kind, issue_key=edge.id) for edge in edges
                )
        return TrackerIssue(
            issue_key=wire.id,
            title=wire.title,
            body=wire.description or "",
            priority=priority,
            state_name=wire.status,
            state_kind=state_kind,
            queue_states=frozenset(
                self._queue_state_by_label[label]
                for label in wire.labels
                if label in self._queue_state_by_label
            ),
            issue_labels=frozenset(
                name
                for name, label in self._issue_labels.items()
                if label in wire.labels
            ),
            team_key=self._team_key_by_identifier.get(wire.team),
            project=wire.project,
            project_id=wire.project_id,
            milestone_key=(
                wire.project_milestone.id
                if wire.project_milestone is not None
                else None
            ),
            relations=tuple(relations),
            parent_key=wire.parent_id,
            assignee_key=wire.assignee,
            created_at=wire.created_at,
            updated_at=wire.updated_at,
            url=wire.url,
        )

    def _to_comment(
        self,
        wire: LinearCommentWire,
        *,
        issue_key: str,
    ) -> TrackerComment:
        """The port's comment, on the issue the CALLER asked about.

        The vendor's comment entry names no issue, so the issue key comes
        from the read that produced it rather than from the payload — the
        one place it is known for certain.  The author is the name the
        vendor attributes the comment to, which is the only authorship
        this surface attests at all.

        A comment the vendor attributes to nobody keeps that state whole:
        the port's ``author_key`` is ``None`` and no name is put in its
        place.  There is nothing to substitute that would be true, and a
        substitution would say a removed user's words were somebody
        else's.
        """
        return TrackerComment(
            reply_to=wire.parent_id,
            comment_key=wire.id,
            issue_key=issue_key,
            author_key=None if wire.author is None else wire.author.name,
            body=wire.body,
            created_at=wire.created_at,
        )
