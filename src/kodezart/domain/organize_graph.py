"""Deterministic native identity and graph checks, without semantic planning."""

from kodezart.domain.fire_spec import body_digest
from kodezart.types.domain.organize_graph import (
    IssueGraphSnapshot,
)
from kodezart.types.domain.tracker import TrackerIssue


def graph_snapshot(issue: TrackerIssue) -> IssueGraphSnapshot:
    return IssueGraphSnapshot(
        issue_key=issue.issue_key,
        body_digest=body_digest(issue.body),
        title=issue.title,
        state_kind=issue.state_kind,
        issue_labels=tuple(sorted(issue.issue_labels)),
        parent_key=issue.parent_key,
        priority=issue.priority,
        milestone_key=issue.milestone_key,
        project_id=issue.project_id,
        relations=tuple(
            sorted(issue.relations, key=lambda edge: (edge.kind.value, edge.issue_key))
        ),
    )
