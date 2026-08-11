"""The one mapping between the CI port's tri-state answer and the wire type.

Pure, and deliberately not in the node: the port answers ``bool | None``
because that is what a check-runs poll produces, the wire carries an
enum, and exactly one place turns one into the other.
"""

from kodezart.types.domain.ci import CIStatus


def ci_status_of(passed: bool | None) -> CIStatus:
    """The status a CI monitor's tri-state answer names.

    ``None`` from a monitor that RAN means the repository configures no
    CI for this ref — never ``not_monitored``, which is a fact about the
    run (it never reached the CI node) and not about the repository.
    """
    if passed is True:
        return CIStatus.passed
    if passed is False:
        return CIStatus.failed
    return CIStatus.not_configured
