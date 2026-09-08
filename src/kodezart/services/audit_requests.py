"""Assemble audit requests from native scope, lane and repository identities."""

from dataclasses import dataclass
from urllib.parse import unquote

from kodezart.core.protocols import TrackerPort
from kodezart.domain.comment_markers import (
    compose_comment_marker,
    configured_marker_prefix,
)
from kodezart.domain.dispatch import clause_recorded_repository
from kodezart.domain.errors import AuditClaimReadError
from kodezart.services.audit_collection import (
    AuditCandidateSnapshot,
    read_audit_candidate_snapshot,
)
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.audit import AuditClaimRequest
from kodezart.types.domain.audit_terminal import AuditTerminalRequest
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_state import LaneRunState
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.tracker import TrackerComment, TrackerIssue


@dataclass(frozen=True)
class AuditLaneSource:
    """The addressed native inputs that determined one lane's request."""

    issue: TrackerIssue
    comment: TrackerComment
    record: LaneRunState
    repo_url: str


@dataclass(frozen=True)
class AuditRequestTarget:
    """Every scoped identity survives, including unreadable request sources."""

    issue: TrackerIssue
    request: AuditClaimRequest | AuditTerminalRequest | None
    source: AuditLaneSource | None
    unavailable_reason: str | None


@dataclass(frozen=True)
class AuditRequestSnapshot:
    """Native source assembly, without treating it as completed audit coverage."""

    candidates: AuditCandidateSnapshot
    targets: tuple[AuditRequestTarget, ...]


class AuditRequestReader:
    """Discover exact native addresses; never choose a latest or inferred lane."""

    def __init__(self, *, tracker: TrackerPort, operation: OperationConfig) -> None:
        self._tracker = tracker
        self._operation = operation.model_copy(deep=True)
        self._records = LaneRecordReader(tracker=tracker, operation=self._operation)

    async def _lane(self, issue: TrackerIssue) -> AuditLaneSource:
        prefixes = self._operation.marker_prefixes
        prefix = configured_marker_prefix(prefixes, purpose="run_state")
        comments = await self._tracker.list_comments(issue_key=issue.issue_key)
        if any(item.issue_key != issue.issue_key for item in comments):
            raise AuditClaimReadError("lane discovery returned a foreign comment")
        matching = [
            item
            for item in comments
            if item.body.partition("\n")[0].startswith(f"[{prefix}:")
        ]
        if len(matching) != 1:
            raise AuditClaimReadError("the issue has no unique native lane record")
        located = matching[0]
        marker = located.body.partition("\n")[0]
        encoded = marker[len(prefix) + 2 : -1]
        lane_key = unquote(encoded, errors="strict")
        if marker != compose_comment_marker(
            prefixes=prefixes, purpose="run_state", lane=lane_key
        ):
            raise AuditClaimReadError("the lane marker has no canonical identity")
        comment, record = await self._records.read(
            issue_key=issue.issue_key,
            lane_key=lane_key,
            record_ref=located.comment_key,
        )
        repo_url = await self._repository(issue)
        return AuditLaneSource(issue, comment, record, repo_url)

    async def _repository(self, issue: TrackerIssue) -> str:
        operation = self._operation
        if issue.team_key not in operation.teams:
            raise AuditClaimReadError(
                "the lane's team has no configured repository route"
            )
        bound = [
            repo.url
            for repo in operation.repos
            if issue.team_key in operation.teams_bound_to(repo.url)
        ]
        if len(bound) == 1:
            return bound[0]
        if bound:
            raise AuditClaimReadError(
                "the lane's team has ambiguous repository bindings"
            )
        recorded = await self._tracker.recorded_repository(issue_key=issue.issue_key)
        matches = [
            repo.url
            for repo in operation.repos
            if clause_recorded_repository(
                team_bound=False, recorded=recorded, repo_url=repo.url
            )
        ]
        if len(matches) != 1:
            raise AuditClaimReadError(
                "the lane has no unique configured recorded route"
            )
        return matches[0]

    async def read(self, *, scope: ScopeRef) -> AuditRequestSnapshot:
        """Retain all states and independently locate each subject's native input."""
        snapshot = await read_audit_candidate_snapshot(
            tracker=self._tracker, scope=scope
        )
        members = {issue.issue_key: issue for issue in snapshot.issues}
        sources: dict[str, AuditLaneSource] = {}
        failures: dict[str, str] = {}
        targets: list[AuditRequestTarget] = []
        for candidate in snapshot.candidates:
            issue = members[candidate.issue_key]
            criterion = "criterion" in issue.issue_labels
            owner_key = issue.parent_key if criterion else issue.issue_key
            source = None
            request: AuditClaimRequest | AuditTerminalRequest | None = None
            reason = None
            if owner_key is None:
                reason = "the criterion has no native owning issue"
            else:
                if owner_key not in sources and owner_key not in failures:
                    try:
                        owner = await self._tracker.read_issue(issue_key=owner_key)
                        if owner.issue_key != owner_key or (
                            owner_key in members and owner != members[owner_key]
                        ):
                            raise AuditClaimReadError("the native owning issue changed")
                        sources[owner_key] = await self._lane(owner)
                    except Exception as exc:
                        failures[owner_key] = f"{type(exc).__name__}: {exc}"
                if owner_key in failures:
                    reason = failures[owner_key]
                else:
                    source = sources[owner_key]
                    if criterion:
                        request = AuditClaimRequest(
                            criterion_key=issue.issue_key,
                            lane_issue_key=owner_key,
                            lane_key=source.record.lane_key,
                            repo_url=source.repo_url,
                            record_ref=source.comment.comment_key,
                        )
                    else:
                        request = AuditTerminalRequest(
                            issue_key=issue.issue_key,
                            lane_key=source.record.lane_key,
                            repo_url=source.repo_url,
                            record_ref=source.comment.comment_key,
                        )
            targets.append(AuditRequestTarget(issue, request, source, reason))
        current = await read_audit_candidate_snapshot(
            tracker=self._tracker, scope=scope
        )
        if current != snapshot:
            raise AuditClaimReadError(
                "the native audit scope changed during request assembly"
            )
        for source in sources.values():
            await self._require_lane_unchanged(source)
        return AuditRequestSnapshot(snapshot, tuple(targets))

    async def _require_lane_unchanged(self, source: AuditLaneSource) -> None:
        owner = await self._tracker.read_issue(issue_key=source.issue.issue_key)
        if owner != source.issue or await self._lane(owner) != source:
            raise AuditClaimReadError("the native lane or repository route changed")

    async def require_unchanged(self, snapshot: AuditRequestSnapshot) -> None:
        """Check current native membership, routing and record identity at return."""
        if await self.read(scope=snapshot.candidates.scope) != snapshot:
            raise AuditClaimReadError(
                "the native audit requests changed during the sweep"
            )
