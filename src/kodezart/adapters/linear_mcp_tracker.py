"""Linear tracker adapter — a programmatic MCP client behind ``TrackerPort``.

Every read and write on the deterministic path is a named tool call with
no model in the loop.  The adapter owns everything vendor-shaped: the
identifier translation, queue-state-as-label mechanics, the atomic-claim
mechanism, the priority encoding, and provenance reads from issue history.
None of it crosses the port.

This is the FIRST adapter, not the design centre.  A GitHub Issues or Jira
adapter is a peer module implementing the same protocol; consumers change
by nothing at all.

The atomic claim is built on the issue comment log, which is append-only
with server-assigned timestamps.  A claimant appends its marker, then reads
the log back and takes the EARLIEST unexpired marker as the holder.  Every
concurrent claimant computes the same winner from the same log, so exactly
one observes ``GRANTED``.
"""

import asyncio
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from kodezart.core.errors import TrackerProtocolError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import McpToolCaller
from kodezart.domain.errors import DuplicateWorkRefError, TransientAPIError
from kodezart.types.domain.branch import WorkRef, WorkRefRole
from kodezart.types.domain.linear_mcp import (
    LinearCommentListWire,
    LinearCommentWire,
    LinearDocumentWire,
    LinearHistoryWire,
    LinearIssueListWire,
    LinearIssueWire,
    LinearNamedListWire,
    LinearWireModel,
)
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.tracker import (
    ClaimResult,
    ClaimStatus,
    IssuePriority,
    IssueQuery,
    IssueRelation,
    IssueRelationKind,
    MappingKind,
    MappingRef,
    StateTransition,
    TrackerAsset,
    TrackerComment,
    TrackerIssue,
    WorkflowStateKind,
)

_TOOL_LIST_ISSUES = "list_issues"
_TOOL_GET_ISSUE = "get_issue"
_TOOL_SAVE_ISSUE = "save_issue"
_TOOL_SAVE_COMMENT = "save_comment"
_TOOL_LIST_COMMENTS = "list_comments"
_TOOL_DELETE_COMMENT = "delete_comment"
_TOOL_LIST_ISSUE_HISTORY = "list_issue_history"
_TOOL_GET_DOCUMENT = "get_document"
_TOOL_LIST_USERS = "list_users"
_TOOL_LIST_TEAMS = "list_teams"
_TOOL_LIST_ISSUE_LABELS = "list_issue_labels"
_TOOL_LIST_ISSUE_STATUSES = "list_issue_statuses"

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

_RELATION_KIND_BY_WIRE: Mapping[str, IssueRelationKind] = {
    "blocks": IssueRelationKind.BLOCKS,
    "blockedBy": IssueRelationKind.BLOCKED_BY,
    "parent": IssueRelationKind.PARENT,
    "child": IssueRelationKind.CHILD,
    "related": IssueRelationKind.RELATED,
    "duplicate": IssueRelationKind.DUPLICATE,
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


def _utc_now() -> datetime:
    """Current instant in UTC — the adapter's default clock."""
    return datetime.now(tz=UTC)


@dataclass(frozen=True)
class _ClaimMarker:
    """One parsed claim marker from the issue comment log. Adapter-private."""

    created_at: datetime
    comment_key: str
    holder: str
    expires_at: datetime


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
        """Issues matching *query*, in backend order."""
        arguments: dict[str, object] = {"limit": query.page_size}
        if query.queue_state is not None:
            arguments["label"] = self._label_for(query.queue_state)
        if query.team_key is not None:
            arguments["team"] = self._team_identifier(query.team_key)
        if query.updated_since is not None:
            arguments["updatedAt"] = query.updated_since.isoformat()
        payload = await self._call(_TOOL_LIST_ISSUES, arguments)
        listing = self._validate(LinearIssueListWire, payload, _TOOL_LIST_ISSUES)
        return tuple(self._to_issue(wire) for wire in listing.issues)

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
        )

    async def list_comments(self, *, issue_key: str) -> Sequence[TrackerComment]:
        """Every comment on the issue, oldest first."""
        return tuple(
            self._to_comment(wire) for wire in await self._comment_wires(issue_key)
        )

    async def claim_issue(
        self,
        *,
        issue_key: str,
        holder: str,
        lease_seconds: float,
    ) -> ClaimResult:
        """Append a claim marker, then read the log back to learn the winner."""
        expires_at = self._clock() + timedelta(seconds=lease_seconds)
        await self._call(
            _TOOL_SAVE_COMMENT,
            {
                "issueId": issue_key,
                "body": (
                    f'<!-- kodezart-claim holder="{holder}" '
                    f'expires-at="{expires_at.isoformat()}" -->'
                ),
            },
        )
        winner = await self.active_claim(issue_key=issue_key)
        if winner is not None and winner.holder == holder:
            return winner
        return ClaimResult(
            issue_key=issue_key,
            status=ClaimStatus.LOST,
            holder=holder,
            expires_at=expires_at,
        )

    async def release_claim(self, *, issue_key: str, holder: str) -> None:
        """Delete every claim marker *holder* wrote on the issue."""
        for wire in await self._comment_wires(issue_key):
            match = _CLAIM_MARKER.search(wire.body)
            if match is not None and match.group("holder") == holder:
                await self._call(_TOOL_DELETE_COMMENT, {"id": wire.id})

    async def active_claim(self, *, issue_key: str) -> ClaimResult | None:
        """The earliest unexpired claim marker on the issue, or ``None``."""
        now = self._clock()
        candidates: list[_ClaimMarker] = []
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
            candidates.append(
                _ClaimMarker(
                    created_at=wire.created_at,
                    comment_key=wire.id,
                    holder=match.group("holder"),
                    expires_at=expires_at,
                ),
            )
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
            expires_at=winner.expires_at,
        )

    async def queue_state_provenance(
        self,
        *,
        issue_key: str,
        state: QueueState,
    ) -> StateTransition | None:
        """Who most recently set *state*, or ``None`` if it never was set."""
        label = self._label_for(state)
        payload = await self._call(_TOOL_LIST_ISSUE_HISTORY, {"id": issue_key})
        history = self._validate(
            LinearHistoryWire,
            payload,
            _TOOL_LIST_ISSUE_HISTORY,
        )
        latest: StateTransition | None = None
        for entry in history.history:
            if label in entry.removed_labels:
                latest = None
            if label in entry.added_labels:
                latest = StateTransition(
                    issue_key=issue_key,
                    queue_state=state,
                    actor_key=entry.actor,
                    occurred_at=entry.created_at,
                )
        return latest

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

    async def record_work_ref(self, *, ref: WorkRef) -> WorkRef:
        """Append a work-ref marker comment and return the ref as stored.

        The comment log is the same append-only, server-timestamped surface
        the claim mechanism uses; a work ref is a second marker on it.  The
        sha attribute is OMITTED when the ref is not pushed, so ``None``
        round-trips as ``None`` rather than as an empty string.
        """
        existing = await self.list_work_refs(issue_key=ref.issue_id)
        for held in existing:
            if held.identity() == ref.identity():
                return held
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
        wire = self._validate(LinearCommentWire, payload, _TOOL_SAVE_COMMENT)
        return ref.model_copy(update={"recorded_at": wire.created_at})

    async def list_work_refs(self, *, issue_key: str) -> Sequence[WorkRef]:
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

    async def resolve_mappings(
        self,
        *,
        refs: Sequence[MappingRef],
    ) -> Sequence[MappingRef]:
        """The subset of *refs* the workspace does not resolve."""
        known: dict[MappingKind, frozenset[str]] = {}
        unresolved: list[MappingRef] = []
        for ref in refs:
            if ref.kind not in known:
                known[ref.kind] = await self._names_of(ref.kind)
            if ref.identifier not in known[ref.kind]:
                unresolved.append(ref)
        return tuple(unresolved)

    async def _names_of(self, kind: MappingKind) -> frozenset[str]:
        tool = _MAPPING_TOOL_BY_KIND[kind]
        payload = await self._call(tool, {})
        listing = self._validate(LinearNamedListWire, payload, tool)
        return frozenset(entry.name for entry in listing.entries)

    async def _read_issue_wire(self, issue_key: str) -> LinearIssueWire:
        payload = await self._call(
            _TOOL_GET_ISSUE,
            {"id": issue_key, "includeRelations": True},
        )
        return self._validate(LinearIssueWire, payload, _TOOL_GET_ISSUE)

    async def _comment_wires(self, issue_key: str) -> Sequence[LinearCommentWire]:
        payload = await self._call(_TOOL_LIST_COMMENTS, {"issueId": issue_key})
        listing = self._validate(LinearCommentListWire, payload, _TOOL_LIST_COMMENTS)
        return listing.comments

    async def _call(
        self,
        tool: str,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        attempt = 0
        while True:
            try:
                return await self._caller.call_tool(name=tool, arguments=arguments)
            except TransientAPIError:
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
        payload: Mapping[str, object],
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
        try:
            state_kind = WorkflowStateKind(wire.status_type)
        except ValueError as exc:
            raise TrackerProtocolError(
                "tracker workflow state kind has no domain mapping",
                tool=_TOOL_GET_ISSUE,
                detail=f"issue={wire.id} status_type={wire.status_type!r}",
            ) from exc
        relations: list[IssueRelation] = []
        for edge in wire.relations:
            kind = _RELATION_KIND_BY_WIRE.get(edge.type)
            if kind is None:
                raise TrackerProtocolError(
                    "tracker relation type has no domain mapping",
                    tool=_TOOL_GET_ISSUE,
                    detail=f"issue={wire.id} type={edge.type!r}",
                )
            relations.append(IssueRelation(kind=kind, issue_key=edge.identifier))
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
            relations=tuple(relations),
            parent_key=wire.parent_id,
            assignee_key=wire.assignee,
            created_at=wire.created_at,
            updated_at=wire.updated_at,
            url=wire.url,
        )

    def _to_comment(self, wire: LinearCommentWire) -> TrackerComment:
        return TrackerComment(
            comment_key=wire.id,
            issue_key=wire.issue_id,
            author_key=wire.user,
            body=wire.body,
            created_at=wire.created_at,
        )
