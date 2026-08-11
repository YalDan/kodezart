"""Agent-session identity and the knowledge-server grant it decides.

A session type names a KIND of agent session — what the session is for —
rather than the code path that starts one, so an operator reading the
grant list reads a policy statement and not a call graph.  Every session
the service starts carries exactly one, with no default: a session whose
type had to be guessed is a session whose grant was guessed.
"""

from enum import StrEnum

from pydantic import ConfigDict, Field

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


class KnowledgeGrant(CamelCaseModel):
    """The resolved knowledge-server grant threaded to executor sessions.

    Carries the whole server definition alongside the session types it is
    granted to, so the membership question and the definition it selects
    answer from one value rather than from two that can disagree.
    """

    model_config = ConfigDict(frozen=True)

    granted: tuple[SessionType, ...] = ()
    server_name: str
    server_url: str
    auth_header: str
    auth_scheme: str
    credential: str | None = Field(default=None, exclude=True)

    def grants(self, session_type: SessionType) -> bool:
        """Whether *session_type* receives the knowledge server."""
        return session_type in self.granted
