"""Each arm reads the one carrier its own composition holds, and no other."""

import pytest

from kodezart.domain.errors import PersistedCriterionSetError
from kodezart.domain.workflow_state import recorded_native_roster, validated_artifact
from kodezart.types.domain.criteria import (
    ConjunctionVerdict,
    CriteriaArtifact,
    CriterionId,
    TrackerCriterion,
    TrackerCriterionSet,
)
from tests.fakes import make_criteria


def persisted_set() -> CriteriaArtifact:
    """The document the authored sweep writes to the branch."""
    return CriteriaArtifact(
        criteria=make_criteria("recorded"),
        conjunction=ConjunctionVerdict(satisfiable=True),
    )


def tracker_roster() -> TrackerCriterionSet:
    """The roster a native barrier reads off the tracker."""
    return TrackerCriterionSet(
        criteria=[TrackerCriterion(id=CriterionId("k"), text="the check")]
    )


def test_a_persisted_artifact_is_refused_and_the_two_honest_inputs_pass_through():
    """A document where a tracker read belongs is refused by type.

    The native arm reads its criterion set from the tracker at every
    barrier, so the only two things this question can honestly be asked
    with are nothing at all — a first entry — and a roster already read.
    A persisted artifact is neither, and reading it as a first entry would
    let a run carry its criteria in on a branch file instead.
    """
    with pytest.raises(PersistedCriterionSetError):
        recorded_native_roster(persisted_set())

    assert recorded_native_roster(None) is None
    held = tracker_roster()
    assert recorded_native_roster(held) is held


@pytest.mark.parametrize(
    "carrier", [None, tracker_roster()], ids=["nothing", "tracker roster"]
)
def test_the_authored_arm_requires_its_artifact_and_refuses_anything_else(carrier):
    """The converse: the authored arm reads the artifact and nothing else.

    The two carriers the native arm accepts are exactly what this one
    refuses, so neither arm can be handed the other's set and proceed.
    """
    with pytest.raises(RuntimeError, match="completed criteria sweep"):
        validated_artifact({"criterion_set": carrier})

    artifact = persisted_set()
    assert validated_artifact({"criterion_set": artifact}) is artifact
