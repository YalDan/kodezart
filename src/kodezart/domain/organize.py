"""Admission actions computed from verdict data before any tracker write."""

from collections.abc import Sequence

from kodezart.domain.dispatch import blocker_keys
from kodezart.types.domain.organize import (
    AdmissionResult,
    AdmissionRoute,
    AdmissionVerdict,
    RefusalKind,
    SpecFinding,
)
from kodezart.types.domain.tracker import (
    TrackerIssue,
    TrackerIssueRevision,
    WorkflowStateKind,
)


def is_admission_live(*, admitted_body_digest: str, current_body_digest: str) -> bool:
    """A judgment applies only to the exact body revision it examined.

    Digests are opaque; this predicate neither normalizes them nor fills a
    missing one. Reading liveness never changes the original judgment.
    """
    if not admitted_body_digest.strip() or not current_body_digest.strip():
        raise ValueError("admission liveness requires both nonempty body digests")
    return admitted_body_digest == current_body_digest


def admission_route(
    result: AdmissionResult,
    *,
    issue: TrackerIssue,
    scope_issue_keys: frozenset[str],
) -> AdmissionRoute:
    """Choose the next action without judging refusal prose or changing verdicts.

    Only an explicitly classified human decision escalates at admission.
    A spec gap can be re-authored. An unverifiable result can advance only
    when its named blocker is both a real ``blockedBy`` edge and inside
    the scope. Parentage, reverse edges and prose supply neither fact.

    The caller supplies the port's current issue and resolved scope members.
    This function preserves the three-state verdict and only returns an
    action: it does not set phase markers, write escalations, or re-author.
    """
    if result.issue_id != issue.issue_key:
        raise ValueError(
            f"admission issue {result.issue_id} does not match tracker issue "
            f"{issue.issue_key}"
        )
    if result.verdict is AdmissionVerdict.BUILDABLE:
        return AdmissionRoute.MARK_COMPLETE
    if result.verdict is AdmissionVerdict.UNVERIFIABLE:
        blocker = result.pending_blocker_id
        if blocker in scope_issue_keys and blocker in blocker_keys(issue):
            return AdmissionRoute.MARK_COMPLETE
        return AdmissionRoute.REAUTHOR
    if result.refusal_kind is RefusalKind.HUMAN_DECISION:
        return AdmissionRoute.ESCALATE
    return AdmissionRoute.REAUTHOR


def organize_gap(
    *,
    revisions: Sequence[TrackerIssueRevision],
    admissions: Sequence[AdmissionResult],
    open_findings: Sequence[SpecFinding],
    body_marker_key: str,
) -> tuple[TrackerIssue, ...]:
    """Return the exact issue records whose specifications need organize work.

    The caller supplies a complete scope snapshot, including full criterion
    child revisions, and only findings that remain open. Label values are
    configured semantic issue-label keys. A missing admission is not live.
    Record-shaped members and criterion children are never work targets.

    This function reads no tracker, judges no text and dispatches no author.
    Criterion execution state only answers whether a non-Canceled child
    exists; a code-only regression cannot put a specification in the gap.
    """
    if not body_marker_key.strip():
        raise ValueError("organize gap requires a body phase marker key")
    by_key = {revision.issue.issue_key: revision for revision in revisions}
    if len(by_key) != len(revisions):
        raise ValueError("organize gap requires one revision per issue")
    admitted = {result.issue_id: result for result in admissions}
    if len(admitted) != len(admissions):
        raise ValueError("organize gap requires one admission per surface")
    children: dict[str, list[TrackerIssueRevision]] = {}
    for revision in revisions:
        issue = revision.issue
        if "criterion" in issue.issue_labels:
            if issue.parent_key not in by_key:
                raise ValueError("organize gap requires each criterion's parent")
            children.setdefault(issue.parent_key, []).append(revision)
    finding_keys = {finding.issue_id for finding in open_findings}
    gap: list[TrackerIssue] = []
    for revision in revisions:
        issue = revision.issue
        if issue.issue_labels & {"criterion", "tracker", "decision"}:
            continue
        result = admitted.get(issue.issue_key)
        has_criterion = any(
            child.issue.state_kind is not WorkflowStateKind.CANCELED
            for child in children.get(issue.issue_key, ())
        )
        if (
            body_marker_key not in issue.issue_labels
            or result is None
            or not is_admission_live(
                admitted_body_digest=result.admitted_body_digest,
                current_body_digest=revision.body_digest,
            )
            or not has_criterion
            or issue.issue_key in finding_keys
        ):
            gap.append(issue)
    return tuple(gap)
