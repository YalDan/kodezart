"""Run-state domain type — what a checkpoint knows about an execution.

Its own leaf module, not a third type in ``job.py``: that module answers
for the *queue handle* (``JobState``, ``JobRecord``), while this one
answers for the *execution* the handle addresses.  One typed partition
per leaf module, as ``consolidation.py``, ``outcome.py`` and
``trajectory.py`` already are.
"""

from pydantic import ConfigDict

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.accept import AcceptVerdict


class RunState(CamelCaseModel):
    """Checkpoint-derived view of a run, read through ``RunStateReader``.

    ``last_completed_node`` is what the checkpoint actually knows — the
    last node that finished — never a claimed "current node".
    """

    model_config = ConfigDict(frozen=True)

    last_completed_node: str | None = None
    total_iterations: int = 0
    remediation_rounds_used: int = 0
    accept_verdict: AcceptVerdict | None = None
    merged: bool = False
    review_passed: bool = False
    ci_passed: bool | None = None
    ci_summary: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    feature_branch: str | None = None
    ralph_branch: str | None = None
