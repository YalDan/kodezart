"""Agent-session identity and the knowledge-server grant it decides.

A session type names a KIND of agent session — what the session is for —
rather than the code path that starts one, so an operator reading the
grant list reads a policy statement and not a call graph.  Every session
the service starts carries exactly one, with no default: a session whose
type had to be guessed is a session whose grant was guessed.
"""

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Final, Self

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


class KnowledgeTransport(StrEnum):
    """How a granted session reaches the knowledge MCP server.

    Two documented client shapes exist for the knowledge vendor: a spawned
    local process speaking stdio, and an HTTP endpoint dialled with headers.
    The member is carried explicitly on the grant so the route is a stated
    fact, never an inference from which optional fields happen to be set.
    """

    HTTP = "http"
    STDIO = "stdio"


#: Command basenames refused for a stdio knowledge server.  These resolve
#: packages relative to the working directory or fetch them at spawn time,
#: and the fire session types run in a cloned, attacker-authored working
#: directory.  An absolute path to an installed binary carries neither risk.
PACKAGE_RUNNER_COMMANDS: Final[frozenset[str]] = frozenset(
    {"npx", "pnpx", "bunx", "uvx", "pipx", "npm", "pnpm", "yarn", "bun"},
)


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
    transport: KnowledgeTransport = KnowledgeTransport.HTTP
    server_name: str
    server_url: str | None = None
    auth_header: str | None = None
    auth_scheme: str | None = None
    credential: str | None = Field(default=None, exclude=True, repr=False)
    gateway_credential: str | None = Field(default=None, exclude=True, repr=False)
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    credential_env: str | None = None
    interactive_auth_hosts: tuple[str, ...] = ()
    knowledge_map: str = ""

    @model_validator(mode="after")
    def _the_shape_matches_its_transport(self) -> Self:
        """Each transport carries its own fields, and only its own.

        A value the declared route never reads is the defect class this
        model was refiled over — configuration dialled by nothing — so a
        stray member is a refusal naming it, never an ignored field.
        """
        if self.transport is KnowledgeTransport.HTTP:
            if self.server_url is None:
                msg = (
                    "an http knowledge transport carries no server_url: "
                    "there is no endpoint for a granted session to dial"
                )
                raise ValueError(msg)
            stray = self._set_fields(
                command=self.command,
                credential_env=self.credential_env,
                args=self.args or None,
                env=self.env or None,
            )
            if stray:
                msg = (
                    f"an http knowledge transport reads none of: {stray}. "
                    f"These fields belong to the stdio transport"
                )
                raise ValueError(msg)
            return self
        if self.command is None:
            msg = (
                "a stdio knowledge transport carries no command: "
                "there is no process for a granted session to spawn"
            )
            raise ValueError(msg)
        stray = self._set_fields(
            server_url=self.server_url,
            auth_header=self.auth_header,
            auth_scheme=self.auth_scheme,
            gateway_credential=self.gateway_credential,
            interactive_auth_hosts=self.interactive_auth_hosts or None,
        )
        if stray:
            msg = (
                f"a stdio knowledge transport has no endpoint and no headers, "
                f"so it reads none of: {stray}"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _a_stdio_command_resolves_nowhere_but_itself(self) -> Self:
        """The spawned server is named absolutely and is not a package runner.

        Fire sessions run in cloned, attacker-authored working directories:
        a relative command resolves against them, and a package runner
        resolves or fetches its payload at spawn time.  Both are refused by
        the value itself, wherever it was built.
        """
        if self.command is None:
            return self
        if not PurePosixPath(self.command).is_absolute():
            msg = (
                f"stdio knowledge command {self.command!r} is not an absolute "
                f"path: a relative command resolves against the session's "
                f"working directory, which a cloned repository controls"
            )
            raise ValueError(msg)
        basename = PurePosixPath(self.command).name
        if basename in PACKAGE_RUNNER_COMMANDS:
            msg = (
                f"stdio knowledge command {self.command!r} is a package "
                f"runner ({basename}): it resolves or fetches its payload at "
                f"spawn time. Name the installed server binary absolutely"
            )
            raise ValueError(msg)
        return self

    @staticmethod
    def _set_fields(**candidates: object) -> str:
        """The names among *candidates* whose value is present, joined."""
        return ", ".join(
            name for name, value in candidates.items() if value is not None
        )

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
