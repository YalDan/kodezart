"""Required runtime budget for an explicitly configured standing audit."""

from pydantic import BaseModel, ConfigDict, Field


class AuditSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
