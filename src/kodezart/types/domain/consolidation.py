"""Domain types for the branch consolidation primitive.

`ConsolidationStatus` is the four-way partition of the consolidation
decision tree.  `ConsolidationOutcome` is the typed return value of
`BranchMerger.consolidate(...)`.  `ChangesetDigest` is the data shape
the engine pre-computes via `GitService.diff_summary` and inlines into
the evaluator prompt — replacing the prior pattern where the prompt
asked the agent to run shell commands.
"""

from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel


class ConsolidationStatus(StrEnum):
    """Four-way partition of the consolidation decision tree.

    - ``ALREADY_INTEGRATED``: source tip is an ancestor of feature HEAD.
    - ``FAST_FORWARDED``: feature HEAD is an ancestor of source tip; merge.
    - ``DIVERGENT``: neither is an ancestor of the other; do NOT raise.
    - ``SOURCE_MISSING``: source branch absent on remote; do NOT raise.
    """

    ALREADY_INTEGRATED = "already_integrated"
    FAST_FORWARDED = "fast_forwarded"
    DIVERGENT = "divergent"
    SOURCE_MISSING = "source_missing"


class ConsolidationOutcome(CamelCaseModel):
    """Typed return of `BranchMerger.consolidate`.

    Two fields only.  No counts, no source SHA, no error — those would
    be YAGNI given the four-status partition is all the caller routes on.
    """

    model_config = ConfigDict(frozen=True)

    status: ConsolidationStatus
    feature_tip_sha: str = Field(min_length=40, max_length=40)


class ChangesetDigest(CamelCaseModel):
    """Typed digest of commits between two refs (base..head).

    Inlined verbatim into the evaluator prompt by `evaluation.build_prompt`.
    The engine pre-computes this via `GitService.diff_summary` so the
    prompt receives DATA, not shell commands the agent must run.
    """

    model_config = ConfigDict(frozen=True)

    file_paths: list[str]
    commit_subjects: list[str]
    commit_count: int = Field(ge=0)

    @property
    def is_empty(self) -> bool:
        """True iff there are no commits between base and head."""
        return self.commit_count == 0
