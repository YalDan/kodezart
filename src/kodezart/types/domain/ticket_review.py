"""What the ticket loop's review arm is, and what happened to it.

Three cross-component conventions live here because three components read
them and none owns them: the composition root configures the mode, the
prompt set composes a member from it, and the loop compiles a different
graph under each value.  A mode spelled as a string at those three places
is three spellings that can disagree.

``TicketApproval`` is the wire's answer to a question a boolean could not
express.  A run whose reviewer rejected the draft, a run that spent its
review budget without approval, and a run that had no reviewer at all are
three different facts about a ticket, and a consumer that has to route on
them cannot recover the third from ``false``.
"""

from enum import StrEnum
from typing import Final


class TicketReviewMode(StrEnum):
    """Whether the ticket loop compiles the harness-level review arm."""

    #: create -> review -> [revise | finalize]: the reviewer is a session.
    REVIEWED = "reviewed"
    #: create -> finalize: the critique happens inside the creator's session.
    CREATE_ONLY = "create_only"


class TicketApproval(StrEnum):
    """What a finished ticket's review says, as three distinguishable facts."""

    #: A reviewer read the draft and approved it.
    APPROVED = "approved"
    #: A reviewer read the draft and did not approve it — whether it was
    #: rejected once or the budget ran out is ``review_rounds``' answer.
    UNAPPROVED = "unapproved"
    #: No reviewer ran.  Never the same statement as "not approved".
    NOT_REVIEWED = "not_reviewed"


#: The review budget a deployment that configures none runs at.  Written
#: here rather than at the field declaration so the loop and the setting
#: read one value: the loop is handed ``None`` when nothing was configured,
#: and "nothing was configured" has to resolve to the same number the
#: setting would have supplied.
DEFAULT_MAX_REVIEWS: Final[int] = 2

#: The lens a create-only creator session must be able to dispatch.  Named
#: once because the loop demands it and the set declares it, and a mandate
#: matched by string literal on both sides is a mandate that stops holding
#: the day one of them is renamed.
DRAFT_CRITIC_LENS: Final[str] = "draft-critic"
