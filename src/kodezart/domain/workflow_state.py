"""Read access to the criteria a run has already swept — one accessor.

Every surface downstream of ``validate_criteria`` — the loop, the review,
the fix round and the pull-request body — reads criteria through here, so
no consumer can pick up the pre-sweep list and dispatch a criterion
without the verdict the sweep computed for it.  Beside the state it reads
rather than inside the engine, because the invariant belongs to
``WorkflowState``, not to the class that happens to hold the nodes.
"""

from kodezart.types.domain.criteria import CriteriaArtifact, ValidatedCriterion
from kodezart.types.domain.workflow import WorkflowState


def validated_artifact(state: WorkflowState) -> CriteriaArtifact:
    """The post-sweep criteria document. Raises if the sweep has not run."""
    artifact = state["criteria_artifact"]
    if artifact is None:
        msg = "Criteria are read after the sweep; state['criteria_artifact'] is None."
        raise RuntimeError(msg)
    return artifact


def validated_criteria(state: WorkflowState) -> list[ValidatedCriterion]:
    """The criteria as validated — identity, text, class, verdict."""
    return validated_artifact(state).criteria
