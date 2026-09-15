"""Capture a tracker subject and its own criterion identities without I/O."""

import re

from collections.abc import Mapping, Sequence

from typing import Literal

from kodezart.domain.errors import (
    EmptyFireCriteriaError,
    FireSpecEntryError,
    InvalidFireCriterionError,
)

from kodezart.types.domain.fire_spec import CriterionRef, IssueRef, TrackerSpec

from kodezart.types.domain.operation import OperationMemberAbsentError

from kodezart.types.domain.tracker import TrackerIssue

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
        criteria=tuple(CriterionRef(criterion.issue_key) for criterion in criteria),
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
