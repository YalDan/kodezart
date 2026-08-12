"""Agent-session identity and the knowledge-server grant it decides.

A session type names a KIND of agent session — what the session is for —
rather than the code path that starts one, so an operator reading the
grant list reads a policy statement and not a call graph.  Every session
the service starts carries exactly one, with no default: a session whose
type had to be guessed is a session whose grant was guessed.
"""

from enum import StrEnum
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel


class SessionType(StrEnum):
    """Every kind of agent session the service starts.

    A new member is what a new KIND of session costs — never what a new
    caller of an existing kind costs.
    """

    TICKET_FIRE = "ticket_fire"
    API_QUERY = "api_query"
    COMMIT_MESSAGE = "commit_message"
    CONTENT_AUDIT = "content_audit"
    #: The passes the scheduler fires on their configured cadence. One
    #: member for both of them: they differ in what their prompt says, not
    #: in what kind of session runs it, and nothing distinguishes the two
    #: for the grant this vocabulary exists to decide. Splitting the day a
    #: grant has to name one and not the other is additive.
    SCHEDULED_PASS = "scheduled_pass"


class KnowledgeGrant(CamelCaseModel):
    """The resolved knowledge-server grant threaded to executor sessions.

    Carries the whole server definition alongside the session types it is
    granted to, so the membership question and the definition it selects
    answer from one value rather than from two that can disagree.

    ``knowledge_map`` is the second consequence of that same decision: the
    rendered what-lives-where prelude a granted session's prompt receives.
    It rides HERE rather than beside the grant because a grant that attaches
    the server without telling the session what lives where, or a map handed
    to sessions nothing was granted to, are exactly the two switches
    disagreeing — and the rule below makes both unconstructible.
    """

    model_config = ConfigDict(frozen=True)

    granted: tuple[SessionType, ...] = ()
    server_name: str
    server_url: str
    auth_header: str
    auth_scheme: str
    credential: str | None = Field(default=None, exclude=True)
    knowledge_map: str = ""

    @model_validator(mode="after")
    def _the_map_rides_with_the_grant(self) -> Self:
        """A grant names session types and carries a map, or neither."""
        if bool(self.granted) == bool(self.knowledge_map):
            return self
        named = ", ".join(session_type.value for session_type in self.granted)
        msg = (
            f"grant names {named} but carries no knowledge map: a granted "
            f"session would be configured with the knowledge server and told "
            f"nothing about what lives where"
            if self.granted
            else (
                "grant names no session type but carries a knowledge map: "
                "nothing would ever render it"
            )
        )
        raise ValueError(msg)

    def grants(self, session_type: SessionType) -> bool:
        """Whether *session_type* receives the knowledge server."""
        return session_type in self.granted
