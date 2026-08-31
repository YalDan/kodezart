"""Linear tracker adapter — a programmatic MCP client behind ``TrackerPort``.

Every read and write on the deterministic path is a named tool call with
no model in the loop.  The adapter owns everything vendor-shaped: the
identifier translation, queue-state-as-label mechanics, the atomic-claim
mechanism and the priority encoding.  None of it crosses the port.

This is the FIRST adapter, not the design centre.  A GitHub Issues or Jira
adapter is a peer module implementing the same protocol; consumers change
by nothing at all.

The atomic claim is built on the issue comment log, which is append-only
with server-assigned timestamps.  A claimant appends its marker, then reads
the log back and takes the EARLIEST unexpired marker as the holder.  Every
concurrent claimant computes the same winner from the same log, so exactly
one observes ``GRANTED``.

A renewal EDITS the holder's earliest marker rather than appending a second
one, so one claim costs one comment however long the run it guards lasts.
Everything that would otherwise pile up on the log is removed by the writer
that put it there: a renewal deletes this holder's own duplicates, and a
claimant whose read-back says LOST deletes the marker it just appended.
Neither ever touches a marker another holder wrote, so the order the log
records stays the order every claimant computes from it.
"""

import asyncio
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import assert_never

from pydantic import ValidationError

from kodezart.core.errors import (
    McpTransportError,
    TrackerBootValidationError,
    TrackerEnsureConflictError,
    TrackerProtocolError,
)
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import McpToolCaller, McpToolResult
from kodezart.domain.errors import DuplicateWorkRefError, TransientAPIError
from kodezart.domain.git_url import extract_owner_repo
from kodezart.types.domain.branch import BaseSpec, WorkRef, WorkRefRole
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.linear_mcp import (
    LINEAR_NAMED_ARRAY,
    LinearCommentListWire,
    LinearCommentWire,
    LinearDiffListWire,
    LinearDocumentListWire,
    LinearDocumentSummaryWire,
    LinearDocumentWire,
    LinearIssueDetailWire,
    LinearIssueListWire,
    LinearIssueWire,
    LinearLabelListWire,
    LinearNamedWire,
    LinearTeamListWire,
    LinearTeamWire,
    LinearUserListWire,
    LinearUserWire,
    LinearWireModel,
)
from kodezart.types.domain.operation import LifecycleStage, QueueState
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
    TrackerReview,
    WorkflowStateKind,
)

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
_TOOL_LIST_USERS = "list_users"
_TOOL_LIST_TEAMS = "list_teams"
_TOOL_LIST_ISSUE_LABELS = "list_issue_labels"
_TOOL_CREATE_ISSUE_LABEL = "create_issue_label"
_TOOL_LIST_ISSUE_STATUSES = "list_issue_statuses"

#: The page a capability probe asks for: the smallest a listing tool takes.
#: The probe is about reachability, so a second row would be paid for and
#: read by nobody.
_SCOPE_PROBE_LIMIT = 1

#: What the vendor's own diagnosis says when a credential lacks the scope a
#: tool needs.  Matched on the error the transport already carries, because
#: that string is the only place the distinction appears: a scope refusal
#: and an outage arrive as the same exception type.  This marker and
#: nothing broader — a status code says a request was refused and not that
#: a scope is missing, so matching one would invent a diagnosis out of a
#: failure nobody made.
_SCOPE_REFUSAL_MARKER = "auth_insufficient_scope"

_CLAIM_MARKER = re.compile(
    r"<!--\s*kodezart-claim\s+holder=\"(?P<holder>[^\"]+)\"\s+"
    r"expires-at=\"(?P<expires_at>[^\"]+)\"\s*-->",
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

_WORK_REF_MARKER = re.compile(
    r"<!--\s*kodezart-workref\s+role=\"(?P<role>[^\"]+)\"\s+"
    r"branch=\"(?P<branch>[^\"]+)\""
    r"(?:\s+pushed-head-sha=\"(?P<sha>[^\"]+)\")?\s*-->",
)

_WORK_REF_ROLE_BY_VALUE: Mapping[str, WorkRefRole] = {
    role.value: role for role in WorkRefRole
}

#: The recorded ``BaseSpec``, on the same append-only, server-timestamped
#: comment log the claim and the work refs already use.  A third marker on
#: one surface rather than a third surface: the log is what this backend
#: offers that is ordered and cannot be silently rewritten.
_BASE_SPEC_MARKER = re.compile(
    r"<!--\s*kodezart-basespec\s+(?P<payload>\{.*?\})\s*-->",
    re.DOTALL,
)


def _base_spec_marker(spec: BaseSpec) -> str:
    """The marker comment body for *spec*, carrying its whole shape.

    Serialized by alias so what goes onto the wire is the model's own
    external form; a hand-rolled encoding here would be a second statement
    of ``BaseSpec`` and a place for the two to disagree.
    """
    return f"<!-- kodezart-basespec {spec.model_dump_json(by_alias=True)} -->"


def _work_ref_marker(ref: WorkRef) -> str:
    """The marker comment body for *ref*.

    ``pushed_head_sha`` at ``None`` omits the attribute entirely: an empty
    attribute value would read back as ``""``, which is a fourth state the
    domain does not have.
    """
    sha = (
        ""
        if ref.pushed_head_sha is None
        else f' pushed-head-sha="{ref.pushed_head_sha}"'
    )
    return (
        f'<!-- kodezart-workref role="{ref.role.value}" branch="{ref.branch}"{sha} -->'
    )


_RETRY_BACKOFF_BASE = 2.0


def _label_arguments(identifier: str, container: str | None) -> dict[str, object]:
    """Create-arguments for one queue-state label.

    *container* is the team's UUID, which is the only thing ``teamId``
    accepts: its declared input schema says "Team UUID (omit for workspace
    label)", and the live server answers a name with ``teamId must be a
    UUID`` and a 400.  ``None`` creates the label at workspace scope,
    which is what a queue vocabulary spanning several configured teams
    requires — a per-team label would leave the same state unaddressable
    on another team's issues.
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
    mention marker (KOD-143 addendum 3).

    Nothing else is normalised here — case in particular.  Whether a
    lowercased identity is a config defect or a prose-versus-identity
    distinction is a question about that config, and folding it here
    would answer it silently for every workspace.
    """
    return identity.removeprefix("@")


def _utc_now() -> datetime:
    """Current instant in UTC — the adapter's default clock."""
    return datetime.now(tz=UTC)


@dataclass
class _LabelListings:
    """Every label listing this adapter read, kept apart by which one answered.

    ``list_issue_labels`` answers a DIFFERENT set depending on whether
    ``team`` was sent, so "does the workspace hold this label?" has no
    single answer — it has one per listing, and which listing carried an
    entry is what that entry's container IS.

    The entries' own ``teamId`` is never consulted for this.  The
    workspace-level listing carries no such field at all, so reading it
    would file every team-scoped label under workspace scope: exactly the
    misreading that made a freshly created label invisible to the boot
    that created it (KOD-143, the label addendum of 2026-08-25).
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
        ref declaring that team.
        """
        return name in self.workspace or (
            scope is not None and name in self.by_team.get(scope, set())
        )

    def teams_holding(self, name: str) -> tuple[str, ...]:
        """The declared teams whose own listing carried *name*."""
        return tuple(
            sorted(team for team, names in self.by_team.items() if name in names)
        )

    def record(self, name: str, scope: str | None) -> None:
        """Hold a label this adapter just created, in the scope it made it."""
        if scope is None:
            self.workspace.add(name)
        else:
            self.by_team.setdefault(scope, set()).add(name)


@dataclass(frozen=True)
class _ClaimMarker:
    """One parsed claim marker from the issue comment log. Adapter-private."""

    created_at: datetime
    comment_key: str
    holder: str
    expires_at: datetime


def _claim_marker_body(*, holder: str, expires_at: datetime) -> str:
    """The marker's wire form, written once so the writer and the reader agree.

    ``_CLAIM_MARKER`` parses what this produces; a second spelling of the
    same comment is how the two drift apart.
    """
    return (
        f'<!-- kodezart-claim holder="{holder}" '
        f'expires-at="{expires_at.isoformat()}" -->'
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
        workflow_state_names: Mapping[LifecycleStage, str],
        team_identifiers: Mapping[str, str],
        max_retries: int,
        retry_backoff_factor: float,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._caller: McpToolCaller = caller
        self._max_retries: int = max_retries
        self._retry_backoff_factor: float = retry_backoff_factor
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
        dispatch pass (KOD-156).

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

        One minimal call per DISTINCT scan: the three issue signals are
        served by one listing tool, so probing all three costs one call.
        A refusal is read off the error the transport already carries, and
        anything else it carries is re-raised — a boot that cannot reach
        the workspace at all is not a boot that learned something about
        scope.
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
        """Call *tool* once, minimally; its diagnosis when it refuses scope."""
        try:
            await self._call(tool, {"limit": _SCOPE_PROBE_LIMIT})
        except McpTransportError as exc:
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
        return self._to_issue(
            self._validate(LinearIssueWire, payload, _TOOL_SAVE_ISSUE),
        )

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
            arguments["description"] = body
        payload = await self._call(_TOOL_SAVE_ISSUE, arguments)
        return self._to_issue(
            self._validate(LinearIssueWire, payload, _TOOL_SAVE_ISSUE),
        )

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
        """Write one backend state name. The two state writers' shared tail."""
        payload = await self._call(
            _TOOL_SAVE_ISSUE,
            {"id": issue_key, "state": state_name},
        )
        return self._to_issue(
            self._validate(LinearIssueWire, payload, _TOOL_SAVE_ISSUE),
        )

    async def set_queue_state(
        self,
        *,
        issue_key: str,
        state: QueueState,
    ) -> TrackerIssue:
        """Set the semantic queue state, replacing any other member."""
        current = await self._read_issue_wire(issue_key)
        preserved = [
            label for label in current.labels if label not in self._queue_state_by_label
        ]
        payload = await self._call(
            _TOOL_SAVE_ISSUE,
            {"id": issue_key, "labels": [*preserved, self._label_for(state)]},
        )
        return self._to_issue(
            self._validate(LinearIssueWire, payload, _TOOL_SAVE_ISSUE),
        )

    async def post_comment(self, *, issue_key: str, body: str) -> TrackerComment:
        """Post a comment and return it as stored."""
        payload = await self._call(
            _TOOL_SAVE_COMMENT,
            {"issueId": issue_key, "body": body},
        )
        return self._to_comment(
            self._validate(LinearCommentWire, payload, _TOOL_SAVE_COMMENT),
            issue_key=issue_key,
        )

    async def list_comments(self, *, issue_key: str) -> Sequence[TrackerComment]:
        """Every comment on the issue, oldest first."""
        return tuple(
            self._to_comment(wire, issue_key=issue_key)
            for wire in await self._comment_wires(issue_key)
        )

    async def claim_issue(
        self,
        *,
        issue_key: str,
        holder: str,
        lease_seconds: float,
    ) -> ClaimResult:
        """Append a claim marker, then read the log back to learn the winner.

        A LOSER deletes the marker it just appended.  The append has to
        happen before the read-back — that append is what the race is
        decided over — so the decision itself is untouched, and the delete
        lands strictly after it.

        What the delete removes is a claim nobody holds.  A loser's marker
        used to sit on the log for its whole lease: it outranked every
        claimant that arrived after it, it survived the WINNER's release,
        and nothing renewed it or cleaned it up, so an issue whose work had
        long finished stayed unclaimable until that lease ran out.  The
        marker is deleted by the identifier the server assigned this
        append, so no marker another claimant wrote can be reached from
        here.
        """
        expires_at = self._clock() + timedelta(seconds=lease_seconds)
        appended = await self._append_claim_marker(
            issue_key=issue_key,
            holder=holder,
            expires_at=expires_at,
        )
        winner = await self.active_claim(issue_key=issue_key)
        if winner is not None and winner.holder == holder:
            return winner
        await self._call(_TOOL_DELETE_COMMENT, {"id": appended})
        return ClaimResult(
            issue_key=issue_key,
            status=ClaimStatus.LOST,
            holder=holder,
            expires_at=expires_at,
        )

    async def renew_claim(
        self,
        *,
        issue_key: str,
        holder: str,
        lease_seconds: float,
    ) -> ClaimResult | None:
        """Carry the holder's own marker forward, EDITED rather than appended.

        The holder's OWN unexpired markers are the whole precondition, and
        not who currently wins the log's order: a losing claimant's marker
        outliving the winner's first one takes the order for as long as it
        lasts, and a run whose work is still in flight may not stop
        renewing over that.

        The marker that moves is the holder's EARLIEST, under the same total
        order ``active_claim`` computes, and it is updated in place.  Two
        properties follow, and both are the reason this is an edit:

        ``created_at`` is the primary sort key, so editing keeps the holder
        exactly where it already stood in the order — for the whole life of
        the claim, however many times it renews.  Appending could not: a
        renewal marker carries a LATER ``created_at``, so once the original
        lapsed the holder's remaining marker could lose the order to a
        claimant that started after it.

        And a renewal costs no comment.  Appending wrote one every renewal
        interval for as long as the job ran, which on measured fire
        durations is dozens of machine comments on one issue, in a log a
        person is expected to read and on a board that mirrors publicly.

        Any FURTHER unexpired marker this holder owns is deleted in the same
        pass: they are this holder's own duplicates, they can only muddy the
        order, and converging on one marker per claim is what makes the
        first property hold.
        """
        mine = sorted(
            (
                marker
                for marker in await self._unexpired_claim_markers(issue_key)
                if marker.holder == holder
            ),
            key=lambda marker: (marker.created_at, marker.comment_key),
        )
        if not mine:
            return None
        expires_at = max(
            self._clock() + timedelta(seconds=lease_seconds),
            *(marker.expires_at for marker in mine),
        )
        earliest, *duplicates = mine
        await self._call(
            _TOOL_SAVE_COMMENT,
            {
                "id": earliest.comment_key,
                "body": _claim_marker_body(holder=holder, expires_at=expires_at),
            },
        )
        for duplicate in duplicates:
            await self._call(_TOOL_DELETE_COMMENT, {"id": duplicate.comment_key})
        return ClaimResult(
            issue_key=issue_key,
            status=ClaimStatus.GRANTED,
            holder=holder,
            expires_at=expires_at,
        )

    async def _append_claim_marker(
        self,
        *,
        issue_key: str,
        holder: str,
        expires_at: datetime,
    ) -> str:
        """Append one marker, answering with the key the server assigned it.

        The key is what makes a losing claimant able to delete its OWN
        append and nothing else.
        """
        payload = await self._call(
            _TOOL_SAVE_COMMENT,
            {
                "issueId": issue_key,
                "body": _claim_marker_body(holder=holder, expires_at=expires_at),
            },
        )
        return self._validate(LinearCommentWire, payload, _TOOL_SAVE_COMMENT).id

    async def _unexpired_claim_markers(
        self,
        issue_key: str,
    ) -> tuple[_ClaimMarker, ...]:
        """Every claim marker on the issue that has not yet lapsed."""
        now = self._clock()
        markers: list[_ClaimMarker] = []
        for wire in await self._comment_wires(issue_key):
            match = _CLAIM_MARKER.search(wire.body)
            if match is None:
                continue
            expires_at = self._parse_instant(
                match.group("expires_at"),
                _TOOL_LIST_COMMENTS,
            )
            if expires_at <= now:
                continue
            markers.append(
                _ClaimMarker(
                    created_at=wire.created_at,
                    comment_key=wire.id,
                    holder=match.group("holder"),
                    expires_at=expires_at,
                ),
            )
        return tuple(markers)

    async def release_claim(self, *, issue_key: str, holder: str) -> None:
        """Delete every claim marker *holder* wrote on the issue."""
        for wire in await self._comment_wires(issue_key):
            match = _CLAIM_MARKER.search(wire.body)
            if match is not None and match.group("holder") == holder:
                await self._call(_TOOL_DELETE_COMMENT, {"id": wire.id})

    async def active_claim(self, *, issue_key: str) -> ClaimResult | None:
        """The earliest unexpired claim marker's holder, or ``None``."""
        candidates = await self._unexpired_claim_markers(issue_key)
        if not candidates:
            return None
        # Total order over an append-only log: server timestamp first, comment
        # key to break a same-instant tie, so every claimant computes the same
        # winner from the same log.
        winner = min(
            candidates, key=lambda marker: (marker.created_at, marker.comment_key)
        )
        return ClaimResult(
            issue_key=issue_key,
            status=ClaimStatus.GRANTED,
            holder=winner.holder,
            expires_at=max(
                marker.expires_at
                for marker in candidates
                if marker.holder == winner.holder
            ),
        )

    async def list_issue_assets(self, *, issue_key: str) -> Sequence[TrackerAsset]:
        """Attachment and document metadata referenced by the issue."""
        wire = await self._read_issue_wire(issue_key)
        return tuple(
            TrackerAsset(
                asset_key=asset.id,
                title=asset.title,
                url=asset.url,
                content_type=asset.content_type,
                size_bytes=asset.size,
            )
            for asset in (*wire.attachments, *wire.documents)
        )

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
            {"issueId": ref.issue_id, "body": _work_ref_marker(ref)},
        )
        self._validate(LinearCommentWire, payload, _TOOL_SAVE_COMMENT)

    async def work_refs(self, *, issue_key: str) -> Sequence[WorkRef]:
        """Every work ref recorded on the issue, oldest first."""
        refs: list[WorkRef] = []
        for wire in await self._comment_wires(issue_key):
            match = _WORK_REF_MARKER.search(wire.body)
            if match is None:
                continue
            role = _WORK_REF_ROLE_BY_VALUE.get(match.group("role"))
            if role is None:
                raise TrackerProtocolError(
                    "work-ref marker names an unknown role",
                    tool=_TOOL_LIST_COMMENTS,
                    detail=match.group("role"),
                )
            refs.append(
                WorkRef(
                    issue_id=issue_key,
                    role=role,
                    branch=match.group("branch"),
                    pushed_head_sha=match.group("sha"),
                    recorded_at=wire.created_at,
                ),
            )
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
            {"issueId": issue_key, "body": _base_spec_marker(spec)},
        )
        self._validate(LinearCommentWire, payload, _TOOL_SAVE_COMMENT)

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
        for wire in await self._comment_wires(issue_key):
            match = _BASE_SPEC_MARKER.search(wire.body)
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
        spelling may carry the mention's leading ``@`` (KOD-143 addendum
        3).  What comes BACK unresolved is the ref exactly as configured,
        so the refusal names the spelling the operator wrote rather than
        an internal form nothing in their config contains.

        A workflow state is resolved PER TEAM and must resolve on EVERY
        team the operation declares (the fire-ruling of 2026-08-25 on
        KOD-143).  A state one declared team cannot express is not a
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
        entry.  A label a declared team holds and this ref does not declare
        would have to be re-scoped to serve it, so it raises and nothing is
        written — not for that ref and not for any ref after it, since the
        loop aborts.  A workspace-level label is adopted by a ref of any
        scope: it is already addressable on every board.

        What no listing carried is CREATED, even when the workspace holds
        the name somewhere no declared team owns.  That container is
        unobservable — no read this adapter is licensed to make reports it
        — and the vendor's own by-name refusal is what stops the write,
        loudly.  Tolerating that refusal here would be the same guess in
        the other direction (KOD-143 addendum 2 of 2026-08-25).

        Documents are instated by TITLE and carry a server-assigned id, so
        their arm of R8's definition is ``(title, id)`` and the outcome
        reports the id the workspace holds.  The document listing is read
        only when a document ref is present: a boot that declares none pays
        for none.
        """
        outcomes: list[MappingOutcome] = []
        definitions = await self._label_definitions()
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
            if definitions.serves(identifier, ref.scope):
                outcomes.append(
                    MappingOutcome(
                        ref=ref,
                        action=EnsureAction.ADOPTED,
                        identifier=identifier,
                    ),
                )
                continue
            held = definitions.teams_holding(identifier)
            if held:
                raise TrackerEnsureConflictError(
                    "the workspace defines this value in another container; "
                    f"declared {ref.scope!r}, "
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

    async def _ensure_document(
        self,
        ref: MappingRef,
        definitions: dict[str, str],
    ) -> MappingOutcome:
        """Adopt the declared document, or create one carrying its title.

        Three refusals, and each names a different workspace fact: a
        declared id the workspace does not hold (creating a second document
        would leave the config pointing at neither), a declared id whose
        document carries another title (serving this ref would rename
        somebody's document), and a title two documents share (adopting
        either one is a coin toss the operator did not ask for).
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
        payload = await self._call(_TOOL_SAVE_DOCUMENT, {"title": ref.name})
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

        The workspace-level listing UNION one team-scoped listing per
        DECLARED team, because the unscoped call answers with the
        workspace-level labels ALONE.  A boot that read only that one
        re-created the team-scoped label its own previous boot had made,
        and the vendor refused it by name (KOD-143, the label addendum of
        2026-08-25).  Idempotence comes from reading both listings, never
        from forgiving that refusal.

        One call per declared team, for the same reason the workflow-state
        vocabulary is read that way: the tool answers for one team, so
        several teams are several answers and no listing spans them.  The
        teams are named the way the configuration names them — ``team``
        takes "name or ID", and only ``create_issue_label.teamId`` insists
        on the UUID.
        """
        workspace = {entry.name for entry in await self._label_entries({})}
        by_team = {
            identifier: {
                entry.name for entry in await self._label_entries({"team": identifier})
            }
            for identifier in sorted(set(self._team_identifiers.values()))
        }
        return _LabelListings(workspace=workspace, by_team=by_team)

    async def _label_entries(
        self,
        arguments: Mapping[str, object],
    ) -> Sequence[LinearNamedWire]:
        """One label listing, scoped by *arguments* or not scoped at all."""
        tool = _MAPPING_TOOL_BY_KIND[MappingKind.QUEUE_STATE]
        payload = await self._call(tool, arguments)
        return self._validate(LinearLabelListWire, payload, tool).labels

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
            case MappingKind.QUEUE_STATE:
                return (await self._label_definitions()).names()
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

    async def _comment_wires(self, issue_key: str) -> Sequence[LinearCommentWire]:
        payload = await self._call(_TOOL_LIST_COMMENTS, {"issueId": issue_key})
        listing = self._validate(LinearCommentListWire, payload, _TOOL_LIST_COMMENTS)
        return listing.comments

    async def _call(
        self,
        tool: str,
        arguments: Mapping[str, object],
    ) -> McpToolResult:
        attempt = 0
        while True:
            try:
                return await self._caller.call_tool(name=tool, arguments=arguments)
            except (McpTransportError, TransientAPIError):
                if attempt >= self._max_retries:
                    raise
                delay = self._retry_backoff_factor * (_RETRY_BACKOFF_BASE**attempt)
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
            team_key=self._team_key_by_identifier.get(wire.team),
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
        """
        return TrackerComment(
            comment_key=wire.id,
            issue_key=issue_key,
            author_key=wire.author.name,
            body=wire.body,
            created_at=wire.created_at,
        )
