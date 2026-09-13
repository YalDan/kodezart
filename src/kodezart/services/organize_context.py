"""One current graph and recorded-ruling context for Organize readers and writers."""

import json
from hashlib import sha256

from kodezart.core.protocols import TrackerPort
from kodezart.domain.comment_markers import configured_marker_prefix
from kodezart.domain.errors import OrganizeWriteRefusalError
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.organize import split_label_key
from kodezart.types.domain.organize_graph import OrganizeContext
from kodezart.types.domain.scope import ScopeContainer, ScopeKind
from kodezart.types.domain.scope_address import ScopeRef
from kodezart.types.domain.tracker import (
    TrackerComment,
    TrackerIssue,
    TrackerIssueRevision,
)


class OrganizeContextReader:
    """Read actual native membership, graph closure and configured ruling records.

    Digest inputs omit tracker timestamps and presentation-only metadata. The owner's
    configured phase markers do not invalidate its own semantic judgments;
    current gate and approval authority are separately checked before writes.
    """

    def __init__(self, *, tracker: TrackerPort, operation: OperationConfig) -> None:
        configured_marker_prefix(operation.marker_prefixes, purpose="ruling")
        self._tracker = tracker
        self._rulings = RulingRecordReader(tracker=tracker, operation=operation)
        self._phase_labels = frozenset(
            split_label_key(row.terminal_marker_key)[1]
            for row in operation.organize_mandates
        )

    async def read(self, *, scope: ScopeRef) -> OrganizeContext:
        members = await self._tracker.scope_issues(ref=scope)
        member_keys = tuple(sorted(issue.issue_key for issue in members))
        if len(set(member_keys)) != len(member_keys):
            raise OrganizeWriteRefusalError(
                issue_key=scope.key, reason="duplicate native scope identities"
            )
        facts: dict[str, TrackerIssue] = {}
        pending = list(member_keys)
        while pending:
            key = pending.pop()
            if key in facts:
                continue
            current = await self._tracker.read_issue(issue_key=key)
            if current.issue_key != key:
                raise OrganizeWriteRefusalError(
                    issue_key=key, reason="graph context read returned another identity"
                )
            facts[key] = current
            pending.extend(relation.issue_key for relation in current.relations)
            if current.parent_key is not None:
                pending.append(current.parent_key)
        rulings: list[TrackerComment] = []
        for key in sorted(facts):
            rulings.extend(
                comment for comment, _ in await self._rulings.read_issue(issue_key=key)
            )
        milestones: list[ScopeContainer] = []
        milestone_keys: set[str] = set()
        for project_key in sorted(
            {
                issue.project_id
                for issue in facts.values()
                if issue.project_id is not None
            }
        ):
            for milestone in await self._tracker.project_milestones(
                project_key=project_key
            ):
                if (
                    milestone.ref.kind is not ScopeKind.MILESTONE
                    or milestone.ref.key in milestone_keys
                    or milestone.parent
                    != ScopeRef(kind=ScopeKind.PROJECT, key=project_key)
                ):
                    raise OrganizeWriteRefusalError(
                        issue_key=scope.key,
                        reason=(
                            "milestone context has a duplicate or wrong "
                            "project identity"
                        ),
                    )
                milestone_keys.add(milestone.ref.key)
                milestones.append(milestone)
        return OrganizeContext(
            milestones=tuple(sorted(milestones, key=lambda item: item.ref.key)),
            scope=scope,
            member_keys=member_keys,
            issues=tuple(facts[key] for key in sorted(facts)),
            ruling_comments=tuple(
                sorted(
                    rulings,
                    key=lambda comment: (comment.issue_key, comment.comment_key),
                )
            ),
        )

    @staticmethod
    def require_revision(
        context: OrganizeContext, revision: TrackerIssueRevision
    ) -> None:
        key = revision.issue.issue_key
        matches = [issue for issue in context.issues if issue.issue_key == key]
        if (
            key not in context.member_keys
            or len(matches) != 1
            or matches[0] != revision.issue
        ):
            raise OrganizeWriteRefusalError(
                issue_key=key,
                reason=(
                    "the requested revision is not the current admitted graph subject"
                ),
            )

    def digest(self, context: OrganizeContext) -> str:
        facts = []
        for issue in context.issues:
            row = issue.model_dump(
                mode="json",
                exclude={
                    "created_at",
                    "updated_at",
                    "url",
                    "assignee_key",
                    "queue_states",
                    "issue_labels",
                },
            )
            row["issue_labels"] = sorted(issue.issue_labels - self._phase_labels)
            row["relations"] = sorted(
                (relation.kind.value, relation.issue_key)
                for relation in issue.relations
            )
            facts.append(row)
        comments = [
            comment.model_dump(mode="json", exclude={"created_at", "updated_at"})
            for comment in context.ruling_comments
        ]
        payload = {
            "scope": context.scope.model_dump(mode="json"),
            "members": context.member_keys,
            "issues": facts,
            "rulings": comments,
            "milestones": [item.model_dump(mode="json") for item in context.milestones],
        }
        return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    async def matches(self, *, scope: ScopeRef, digest: str) -> bool:
        return self.digest(await self.read(scope=scope)) == digest
