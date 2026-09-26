"""The record an audit write expects its own subject to read back as."""

from collections.abc import Mapping

from kodezart.domain.errors import AuditClaimReadError
from kodezart.types.domain.tracker import TrackerIssue


def expected_after_audit_write(
    issue: TrackerIssue,
    *,
    issue_key: str,
    current_issues: Mapping[str, TrackerIssue],
    classification: bool,
) -> TrackerIssue:
    """The record *issue* is expected to read back as after the audit's own write.

    It carries the observed stamp onto the expected record, so the
    write-back comparison does not fail on the stamp: the audit's own write
    moves the subject's change stamp, and every other fact stays pinned. A
    classification write also adds the decision label. Any record other than
    the written subject is expected back unchanged.
    """
    if issue.issue_key != issue_key:
        return issue
    if issue_key not in current_issues:
        raise AuditClaimReadError("the written audit subject left its source set")
    observed = current_issues[issue_key]
    return TrackerIssue.model_validate(
        {
            **issue.model_dump(),
            "issue_labels": issue.issue_labels | {"decision"}
            if classification
            else issue.issue_labels,
            "updated_at": observed.updated_at,
        }
    )
