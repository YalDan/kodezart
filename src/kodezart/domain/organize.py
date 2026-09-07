"""Admission actions computed from verdict data before any tracker write."""

from kodezart.types.domain.organize import (
    AdmissionResult,
    AdmissionRoute,
    AdmissionVerdict,
    RefusalKind,
)


def admission_route(result: AdmissionResult) -> AdmissionRoute:
    """Choose the next action without judging refusal prose or changing verdicts.

    Only an explicitly classified human decision escalates at admission.
    A spec gap can be re-authored. An unverifiable result cannot advance
    without a separately established in-scope blocker edge.
    """
    if result.verdict is AdmissionVerdict.BUILDABLE:
        return AdmissionRoute.MARK_COMPLETE
    if result.refusal_kind is RefusalKind.HUMAN_DECISION:
        return AdmissionRoute.ESCALATE
    return AdmissionRoute.REAUTHOR
