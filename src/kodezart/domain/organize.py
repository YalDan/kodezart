"""Admission actions computed from verdict data before any tracker write."""

from collections.abc import Sequence

from kodezart.domain.dispatch import blocker_keys
from kodezart.types.domain.organize import (
    AdmissionResult,
    AdmissionRoute,
    AdmissionVerdict,
    RefusalKind,
    RefusedAdmission,
    SpecFinding,
    UnverifiableAdmission,
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
    if isinstance(result.root, UnverifiableAdmission):
        blocker = result.root.pending_blocker_id
        if blocker in scope_issue_keys and blocker in blocker_keys(issue):
            return AdmissionRoute.MARK_COMPLETE
        return AdmissionRoute.REAUTHOR
    if (
        isinstance(result.root, RefusedAdmission)
        and result.root.refusal_kind is RefusalKind.HUMAN_DECISION
    ):
        return AdmissionRoute.ESCALATE
    return AdmissionRoute.REAUTHOR


def is_organize_subject(issue: TrackerIssue) -> bool:
    """The phase work roster excludes criteria and record-shaped issues."""
    return not bool(issue.issue_labels & {"criterion", "tracker", "decision"})


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
    Each body is compared with its own admission; a stale child surface
    puts its parent in the work set without changing the parent admission.
    Criterion execution state only answers whether a non-Canceled child
    exists; a code-only regression cannot put a specification in the gap.

    A vendor change timestamp enters no clause of this computation. A
    mention bumps it without touching a body, so it reports movement that
    is not change and a work set keyed on it re-processes items nothing
    happened to. The prohibition is absolute here rather than a tuned
    window size, and it is scoped to this computation alone: it does not
    reach the reply/mention scan, whose own window is correct precisely
    because there a mention IS the signal being scanned for. Neither half
    is evidence for the other.
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
        if not is_organize_subject(issue):
            continue
        surfaces = (revision, *children.get(issue.issue_key, ()))
        has_lapsed_surface = any(
            surface.issue.issue_key not in admitted
            or not is_admission_live(
                admitted_body_digest=admitted[
                    surface.issue.issue_key
                ].admitted_body_digest,
                current_body_digest=surface.body_digest,
            )
            for surface in surfaces
        )
        has_criterion = any(
            child.issue.state_kind is not WorkflowStateKind.CANCELED
            for child in children.get(issue.issue_key, ())
        )
        if (
            body_marker_key not in issue.issue_labels
            or has_lapsed_surface
            or not has_criterion
            or any(surface.issue.issue_key in finding_keys for surface in surfaces)
        ):
            gap.append(issue)
    return tuple(gap)
