"""Deployment knowledge capability and its typed transport."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kodezart.types.domain.session import (
    KnowledgeConnection,
    KnowledgeGrant,
    SessionType,
)


class KnowledgeSettings(BaseModel):
    """Settings consumed by knowledge sessions and the programmatic recorder."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    session_grants: tuple[SessionType, ...] = Field(
        default=(), description="Session kinds permitted to access knowledge."
    )
    server_name: str = Field(
        default="notion", min_length=1, description="Knowledge MCP server identity."
    )
    connection: KnowledgeConnection | None = Field(
        default=None,
        description="HTTP or stdio server; absent disables the connection.",
    )
    call_timeout_seconds: float = Field(
        default=60, ge=1, le=120, description="Maximum seconds for one MCP call."
    )
    error_detail_limit: int = Field(
        default=500,
        ge=80,
        le=8000,
        description="Maximum characters retained from a transport error.",
    )

    @field_validator("session_grants", mode="before")
    @classmethod
    def _grant_entries_name_session_types(cls, value: object) -> object:
        if not isinstance(value, list | tuple):
            return value
        legal = {member.value for member in SessionType}
        offending = [str(entry) for entry in value if str(entry) not in legal]
        if offending:
            raise ValueError(
                f"session_grants names no session type: {', '.join(offending)}; "
                f"legal values: {', '.join(sorted(legal))}"
            )
        return value

    @model_validator(mode="after")
    def _grants_have_credentials(self) -> Self:
        if self.session_grants and (
            self.connection is None or not self.connection.authenticated
        ):
            raise ValueError(
                "session_grants requires connection with credential "
                "(or HTTP gateway_credential)"
            )
        return self

    def grant(self, *, knowledge_map: str) -> KnowledgeGrant:
        """Attach the rendered map to the already validated connection."""
        return KnowledgeGrant(
            granted=self.session_grants,
            server_name=self.server_name,
            connection=self.connection,
            knowledge_map=knowledge_map,
        )
