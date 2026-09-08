"""Agent-session identity and the knowledge-server grant it decides.

A session type names a KIND of agent session — what the session is for —
rather than the code path that starts one, so an operator reading the
grant list reads a policy statement and not a call graph.  Every session
the service starts carries exactly one, with no default: a session whose
type had to be guessed is a session whose grant was guessed.
"""

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Final, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

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
    ORGANIZE_PASS = "organize_pass"
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


class HttpKnowledge(BaseModel):
    """One HTTP endpoint and the credentials it receives."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True, extra="forbid")

    transport: Literal[KnowledgeTransport.HTTP] = KnowledgeTransport.HTTP
    server_url: str = Field(min_length=1)
    auth_header: str = Field(default="Authorization", min_length=1)
    auth_scheme: str | None = Field(default="Bearer", min_length=1)
    credential: SecretStr | None = Field(default=None, exclude=True)
    gateway_credential: SecretStr | None = Field(default=None, exclude=True)
    interactive_auth_hosts: tuple[str, ...] = ("mcp.notion.com",)
    timeout_seconds: float = Field(default=30, ge=5, le=120)
    sse_read_timeout_seconds: float = Field(default=300, ge=30, le=3600)

    @model_validator(mode="after")
    def _authentication_is_coherent(self) -> Self:
        if self.credential is not None and self.gateway_credential is not None:
            if self.auth_header.casefold() == "authorization":
                raise ValueError(
                    "auth_header collides with the gateway Authorization header"
                )
        if (
            self.authenticated
            and urlsplit(self.server_url).hostname in self.interactive_auth_hosts
        ):
            raise ValueError(
                "server_url authenticates interactively (OAuth) "
                "and accepts no static credential"
            )
        return self

    @property
    def authenticated(self) -> bool:
        """Whether at least one configured credential reaches the server."""
        return self.credential is not None or self.gateway_credential is not None

    def headers(self) -> dict[str, str]:
        """The same complete header mapping for SDK and programmatic clients."""
        headers = {}
        if self.gateway_credential is not None:
            headers["Authorization"] = (
                f"Bearer {self.gateway_credential.get_secret_value()}"
            )
        if self.credential is not None:
            token = self.credential.get_secret_value()
            headers[self.auth_header] = (
                token if self.auth_scheme is None else f"{self.auth_scheme} {token}"
            )
        return headers


class StdioKnowledge(BaseModel):
    """One installed process and its explicit credential environment entry."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True, extra="forbid")

    transport: Literal[KnowledgeTransport.STDIO] = KnowledgeTransport.STDIO
    command: str = Field(min_length=1)
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    credential: SecretStr | None = Field(default=None, exclude=True)
    credential_env: str | None = Field(default=None, min_length=1)
    stderr_tail_limit: int = Field(default=2000, ge=200, le=20000)

    @field_validator("command")
    @classmethod
    def _installed_absolute_command(cls, command: str) -> str:
        path = PurePosixPath(command)
        if not path.is_absolute():
            raise ValueError("stdio command must be an absolute installed binary path")
        if path.name in PACKAGE_RUNNER_COMMANDS:
            raise ValueError("stdio command cannot be a package runner")
        return command

    @model_validator(mode="after")
    def _credential_has_one_delivery_entry(self) -> Self:
        if (self.credential is None) != (self.credential_env is None):
            raise ValueError("credential and credential_env must be supplied together")
        if self.credential_env is not None and self.credential_env in self.env:
            raise ValueError("credential_env collides with an existing env entry")
        return self

    @property
    def authenticated(self) -> bool:
        """Whether the credential has a validated delivery entry."""
        return self.credential is not None

    def environment(self) -> dict[str, str]:
        """A fresh process environment with the explicit credential inserted."""
        env = dict(self.env)
        if self.credential_env is not None and self.credential is not None:
            env[self.credential_env] = self.credential.get_secret_value()
        return env


KnowledgeConnection = Annotated[
    HttpKnowledge | StdioKnowledge, Field(discriminator="transport")
]


class KnowledgeGrant(CamelCaseModel):
    """One resolved capability decision and its rendered knowledge map."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True, extra="forbid")

    granted: tuple[SessionType, ...] = ()
    server_name: str
    connection: KnowledgeConnection | None = None
    knowledge_map: str = ""

    @model_validator(mode="after")
    def _the_map_rides_with_the_grant(self) -> Self:
        if bool(self.granted) != bool(self.knowledge_map):
            raise ValueError(
                "knowledge_map and granted session types must be supplied together"
            )
        if self.granted and (
            self.connection is None or not self.connection.authenticated
        ):
            raise ValueError(
                "granted session types require an authenticated knowledge connection"
            )
        return self

    def grants(self, session_type: SessionType) -> bool:
        """Whether *session_type* receives the knowledge server."""
        return session_type in self.granted
