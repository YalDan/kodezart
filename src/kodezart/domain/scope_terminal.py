"""The configured bound a halt was reached by, named as its own env field.

An organize halt records the bound it exhausted as a settings PATH —
``organize.max_admission_rounds`` — which is how the application reads it,
not how an operator fixed it.  A declared stop is data about the
deployment, so the terminal names the environment variable that carried
the value instead: the same path spelled the way ``AppConfig`` loads it,
with its ``KODEZART_`` prefix and its ``__`` nesting delimiter.

The mapping is total over the settings the halt can name and nothing is
derived by string surgery at the terminal, so a settings field that is
renamed or added shows up as a missing entry here rather than as a
plausible environment name that was never read.
"""

from collections.abc import Mapping

from kodezart.types.domain.organize_owner import OrganizeBoundEvidence
from kodezart.types.domain.scope_terminal import ScopeStoppingRule

#: Every bound an organize halt can name, against the environment variable
#: ``AppConfig`` loads it from (prefix ``KODEZART_``, nesting ``__``).
BOUND_CONFIG_FIELD: Mapping[str, str] = {
    "organize.max_admission_rounds": "KODEZART_ORGANIZE__MAX_ADMISSION_ROUNDS",
    "organize.max_convergence_rounds": "KODEZART_ORGANIZE__MAX_CONVERGENCE_ROUNDS",
    "write_back.max_verify_rounds": "KODEZART_WRITE_BACK__MAX_VERIFY_ROUNDS",
}


def stopping_rule_of(bound: OrganizeBoundEvidence | None) -> ScopeStoppingRule | None:
    """The stopping rule a halt's bound declares, or nothing for no bound.

    The value and the rounds travel unchanged: the halt already established
    that the run exhausted the bound exactly, and the terminal restates that
    fact rather than recomputing it.
    """
    if bound is None:
        return None
    return ScopeStoppingRule(
        config_field=BOUND_CONFIG_FIELD[bound.setting],
        configured_value=bound.value,
        rounds_used=bound.rounds_used,
    )
