"""The one question every label seam asks of the admission vocabulary."""

from collections.abc import Mapping

from kodezart.types.domain.operation import ScopeLabel


def aliases_approval_member(*, label: str, scope_labels: Mapping[str, str]) -> bool:
    """Whether this configured label spells the vocabulary's approved member.

    Admission is the approver's own act, so every seam that writes a
    configured label has to know whether the label it is about to write
    is that member.  Stated once: the member and the spelling of its key
    are this predicate's business, and a seam asking the question carries
    only its own refusal.
    """
    return label == scope_labels.get(ScopeLabel.APPROVED.value)
