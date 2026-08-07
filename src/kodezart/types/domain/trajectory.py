"""Domain types for the Ralph loop's progress memory.

``IterationRecord`` is one iteration's contribution; ``LoopTrajectory``
is the fold over every record so far.  They live in this leaf module —
not in ``workflow.py`` — because ``types/domain/agent.py`` carries them
on ``WorkflowIterationEvent`` and ``WorkflowCompleteEvent`` while
``workflow.py`` already imports ``agent.py``.  ``consolidation.py`` is
the precedent: one typed partition per leaf module, re-exported from
``workflow.py`` so consumers keep a single import site.
"""

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel


class IterationRecord(CamelCaseModel):
    """One iteration's contribution to the loop's progress memory.

    ``failing_criterion_ids`` carries criterion TEXT verbatim
    (``CriterionResult.criterion``) — no synthetic ids are minted.
    ``commit_sha`` is that iteration's own commit, never overwritten by
    a later iteration.
    """

    model_config = ConfigDict(frozen=True)

    iteration: int = Field(ge=1)
    passed_count: int = Field(ge=0)
    failing_criterion_ids: list[str]
    commit_sha: str | None = None


class LoopTrajectory(CamelCaseModel):
    """Folded view of every iteration the loop has run so far."""

    model_config = ConfigDict(frozen=True)

    records: list[IterationRecord]
    never_passed_ids: list[str]
    best_passed_count: int = Field(ge=0)
    best_iteration: int = Field(ge=0)
    best_commit_sha: str | None = None
    plateaued: bool
