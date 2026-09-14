"""The operator's bound for canonical tracker write verification."""

from pydantic import BaseModel, ConfigDict, Field


class WriteBackSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    max_verify_rounds: int = Field(
        ge=1,
        le=10,
        description="Maximum fresh verification and repair rounds per write.",
    )
