"""Capture a tracker subject and its own criterion identities without I/O."""

import re

from collections.abc import Mapping, Sequence

from hashlib import sha256

from typing import Literal

from kodezart.domain.errors import (
    EmptyFireCriteriaError,
    FireSpecEntryError,
    InvalidFireCriterionError,
)

from kodezart.types.domain.fire_spec import CriterionRef, IssueRef, TrackerSpec

from kodezart.types.domain.operation import OperationMemberAbsentError

from kodezart.types.domain.tracker import TrackerIssue

def body_digest(body: str) -> str:
    """The sha256 hex of an issue body, as every reader of one pins it.

    The one place in the source that turns a body into a digest: a lane's
    record pins the subject it entered on, a revision stamps the body it
    read, and an organize snapshot carries the body it judged. One rule, so
    two readings of the same bytes cannot disagree by construction.

    Arithmetic over the bytes that were read: two processes that read the
    same body agree on it without either of them asking anything.
    """
    return sha256(body.encode("utf-8")).hexdigest()

def tracker_spec_from_issues(
    *, subject: TrackerIssue, criteria: Sequence[TrackerIssue]
) -> TrackerSpec:
    """Capture the subject version; a successful empty query is unfireable."""
    if not criteria:
        raise EmptyFireCriteriaError(issue_key=subject.issue_key)
    for criterion in criteria:
        criterion_check(criterion=criterion, issue_key=subject.issue_key)
    return TrackerSpec(
        subject=IssueRef(subject.issue_key),
        body=subject.body,
        criteria=tuple(criterion_ref(criterion.issue_key) for criterion in criteria),
        read_at_version=subject.updated_at.isoformat(),
    )

def require_fire_entry(
    *, subject: TrackerIssue, approved: bool, criteria_stage_label_key: str | None
) -> None:
    """Require the configured phase completion and the live approval fact."""
    if criteria_stage_label_key is None:
        raise OperationMemberAbsentError(
            missing="organize_mandates criteria terminal_marker_key",
            stops="cannot establish criteria-stage completion at fire entry",
        )
    if criteria_stage_label_key not in subject.issue_labels:
        raise FireSpecEntryError(
            issue_key=subject.issue_key, reason="criteria-stage completion is absent"
        )
    if not approved:
        raise FireSpecEntryError(
            issue_key=subject.issue_key, reason="execution approval is absent"
        )
