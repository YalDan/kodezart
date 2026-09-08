"""Admission actions computed from verdict data before any tracker write."""

from kodezart.domain.dispatch import blocker_keys
from kodezart.types.domain.organize import (
    AdmissionResult,
    AdmissionRoute,
    AdmissionVerdict,
    RefusalKind,
)
from kodezart.types.domain.tracker import TrackerIssue


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
