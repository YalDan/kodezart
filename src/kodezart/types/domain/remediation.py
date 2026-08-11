"""Which failure route opened a remediation round.

A leaf module, like ``outcome`` and ``trajectory``: the event type in
``agent.py`` carries this value, and ``agent.py`` is imported by the
state module that defines the round's full request — so the enum cannot
live beside that request without cycling.

Three failure routes reach one component, so what distinguishes them has
to be a value rather than a call site: a node that could tell which
entry fired only by asking who called it would need a second code path
to ask with.
"""

from enum import StrEnum


class RemediationEntry(StrEnum):
    """The failure route a remediation round is answering."""

    ci_failure = "ci_failure"
    review_failure = "review_failure"
    loop_not_accepted = "loop_not_accepted"
