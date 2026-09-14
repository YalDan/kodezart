"""The harness's explicit invocation of one declared agent-session shape."""

from typing import Self

from pydantic import ConfigDict, Field, StrictInt, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.run_records import RunIdentity


class NodeInvocation(CamelCaseModel):
    """An invocation within an existing fire, never an inferred lane identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    run: RunIdentity
    node_key: str = Field(min_length=1, pattern=r"\S")
    invocation_key: str = Field(min_length=1, pattern=r"\S")
    declared_sessions: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def _existing_fire(self) -> Self:
        if self.run.kind is not RunKind.FIRE or not self.run.name.strip():
            raise ValueError("a node invocation requires its existing fire identity")
        return self


class NodeSessionObservationError(ValueError):
    """A native stream cannot establish the sessions it says it completed."""

    def __init__(
        self, *, invocation: NodeInvocation, failures: tuple[str, ...]
    ) -> None:
        super().__init__("; ".join(failures))
        self.invocation = invocation
        self.failures = failures
