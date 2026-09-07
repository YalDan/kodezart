"""Capture a tracker subject and its own criterion identities without I/O."""

from collections.abc import Sequence

from kodezart.domain.errors import EmptyFireCriteriaError
from kodezart.types.domain.fire_spec import CriterionRef, IssueRef, TrackerSpec
from kodezart.types.domain.tracker import TrackerIssue


def tracker_spec_from_issues(
    *, subject: TrackerIssue, criteria: Sequence[TrackerIssue]
) -> TrackerSpec:
    """Capture the subject version; a successful empty query is unfireable."""
    if not criteria:
        raise EmptyFireCriteriaError(issue_key=subject.issue_key)
    return TrackerSpec(
        subject=IssueRef(subject.issue_key),
        body=subject.body,
        criteria=tuple(CriterionRef(criterion.issue_key) for criterion in criteria),
        read_at_version=subject.updated_at.isoformat(),
    )
