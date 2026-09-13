"""Captured command observations, without root/cascade classification."""

from pydantic import ConfigDict

from kodezart.types.base import CamelCaseModel


class CheckStepOutput(CamelCaseModel):
    """One executed step, including whether its deadline was exhausted."""

    model_config = ConfigDict(frozen=True)

    name: str
    output: str
    exit_code: int
    timed_out: bool


class CheckChainResult(CamelCaseModel):
    """All step observations in execution order, and the failed names."""

    model_config = ConfigDict(frozen=True)

    failed_step_names: frozenset[str]
    step_outputs: tuple[CheckStepOutput, ...]
