"""Bounded retry timing shared by transport and content-scan adapters."""

from random import Random, SystemRandom

from pydantic import BaseModel, ConfigDict, Field

_RANDOM = SystemRandom()


class RetryPolicy(BaseModel):
    """Total attempts and exponential delay, with positive fractional jitter."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    attempts: int = Field(ge=1, strict=True)
    initial_delay: float = Field(ge=0)
    factor: float = Field(default=2, ge=1)
    jitter: float = Field(default=0, ge=0)

    def delay(
        self,
        attempt: int,
        *,
        retry_after: float | None = None,
        rng: Random = _RANDOM,
    ) -> float:
        """Delay after a zero-based attempt; a provider delay replaces backoff."""
        base = (
            self.initial_delay * self.factor**attempt
            if retry_after is None
            else retry_after
        )
        return base + rng.uniform(0, base * self.jitter) if self.jitter else base
