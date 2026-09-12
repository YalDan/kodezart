"""Read the one subject and criterion set held by either fire composition."""

from kodezart.types.domain.agent import TicketDraftOutput
from kodezart.types.domain.criteria import CriteriaArtifact, ExecutionCriterion
from kodezart.types.domain.fire_spec import AuthoredSpec, FireSpec
from kodezart.types.domain.workflow import WorkflowState


def validated_artifact(state: WorkflowState) -> CriteriaArtifact:
    """The authored post-sweep document; native Checks are never an artifact."""
    artifact = state["criterion_set"]
    if not isinstance(artifact, CriteriaArtifact):
        raise RuntimeError("An authored artifact requires a completed criteria sweep")
    return artifact


def validated_criteria(state: WorkflowState) -> list[ExecutionCriterion]:
    """The criteria established by the selected composition's entry barrier."""
    criterion_set = state["criterion_set"]
    if criterion_set is None:
        raise RuntimeError("Criteria are read after the entry barrier")
    return list(criterion_set.criteria)


def original_fire_spec(state: WorkflowState) -> FireSpec:
    """The subject captured at entry, preserved across remediation rounds."""
    spec = state["fire_spec"]
    if spec is None:
        raise RuntimeError("The fire spec is read after its entry stage")
    return spec


def current_fire_spec(state: WorkflowState) -> FireSpec:
    """Use an authored remediation ticket only on the authored composition."""
    spec = original_fire_spec(state)
    remediation = state["remediation_ticket"]
    if isinstance(spec, AuthoredSpec) and remediation is not None:
        if not isinstance(remediation, TicketDraftOutput):
            raise RuntimeError("An authored remediation requires an authored ticket")
        return AuthoredSpec(ticket=remediation)
    return spec


def current_ticket(state: WorkflowState) -> TicketDraftOutput:
    """The current authored ticket, for authored generation and persistence."""
    spec = current_fire_spec(state)
    if not isinstance(spec, AuthoredSpec):
        raise RuntimeError("An authored ticket is unavailable on the tracker arm")
    return spec.ticket
