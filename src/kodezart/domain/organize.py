"""Which scope members are organize work subjects."""

from kodezart.types.domain.tracker import TrackerIssue


def is_organize_subject(issue: TrackerIssue) -> bool:
    """The phase work roster excludes criteria and record-shaped issues."""
    return not bool(issue.issue_labels & {"criterion", "tracker", "decision"})
