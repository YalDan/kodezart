"""What continuous integration said about a run's head, as one value.

A tri-state ``bool | None`` carried this until it needed two serializer
hacks to survive ``exclude_none=True``: a field that must always appear
and has three meanings is an enum, and the hacks existed only because it
was not one.

Four members, not three.  The fourth is the state a run is in before the
CI node executes — and it is a real distinction the classifier already
drew by consulting a SECOND field: a pull request opened with no status
and no summary is a run whose CI was never monitored, while no status
WITH a summary is a repository that configures no CI at all.  Collapsing
them would make two terminal outcomes indistinguishable.

Its own leaf module, following the one-typed-partition-per-module
precedent of ``outcome.py`` and ``consolidation.py``: the workflow state,
both events, the run-state read and the job response all name this type,
and none of them may own it.
"""

from enum import StrEnum


class CIStatus(StrEnum):
    """Four-way partition of what CI reported for a run's head."""

    passed = "passed"
    failed = "failed"
    not_configured = "not_configured"
    not_monitored = "not_monitored"
