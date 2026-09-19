"""Closed alarm identities and evidence retain types until persistence."""

import pytest
from pydantic import TypeAdapter, ValidationError

from kodezart.types.domain.run_alarm import AlarmReading, AlarmSubject


def test_subject_schema_exposes_six_discriminated_regions():
    schema = TypeAdapter(AlarmSubject).json_schema()
    assert set(schema["discriminator"]["mapping"]) == {
        "scope",
        "lane",
        "issue",
        "criterion",
        "surface",
        "escalation",
    }


def test_reading_refuses_opaque_json_in_place_of_typed_evidence():
    with pytest.raises(ValidationError):
        AlarmReading(source_ref="native-record", value='{"laneKey":"lane"}')


def test_every_evidence_arm_roundtrips_without_reparsing_domain_payloads():
    from kodezart.types.domain.escalation import (
        EscalationResolution,
        EscalationResolutionState,
    )
    from kodezart.types.domain.mandate_graph import (
        LaneGraphSnapshot,
        LaneRulingSnapshot,
    )
    from kodezart.types.domain.run_alarm import (
        AlarmEvidence,
        CommitsEvidence,
        CountEvidence,
        EscalationEvidence,
        GraphEvidence,
        LabelsEvidence,
        LaneFieldEvidence,
        LaneFieldValue,
        PresenceEvidence,
        ReferencesEvidence,
        ResolutionEvidence,
        RulingsEvidence,
        ScopeEvidence,
        SurfaceEvidence,
        TextEvidence,
    )
    from kodezart.types.domain.run_state import LaneCommit, LaneEscalation
    from kodezart.types.domain.scope import ScopeKind, ScopeRef
    from kodezart.types.domain.surface import SurfaceKind, WritableSurface

    scope = ScopeRef(kind=ScopeKind.MILESTONE, key="actual-milestone")
    values = (
        TextEvidence(value=" untouched\n"),
        CountEvidence(value=7),
        PresenceEvidence(value=False),
        ReferencesEvidence(value=("a", "b")),
        LabelsEvidence(value=None),
        SurfaceEvidence(
            value=WritableSurface(kind=SurfaceKind.CONTAINER_DESCRIPTION, ref=scope)
        ),
        EscalationEvidence(
            value=LaneEscalation(
                issue_id="issue",
                escalation_key="question",
                raised_by="job",
                question="Which source?",
                interim_reading="Keep current behavior",
                interim_basis="Current approved requirement",
                raised_at_sha="head",
            )
        ),
        ResolutionEvidence(
            value=EscalationResolution(
                state=EscalationResolutionState.UNRESOLVED, decision_ref=None
            )
        ),
        CommitsEvidence(value=(LaneCommit(sha="head", subject="", issue_id="issue"),)),
        LaneFieldEvidence(
            value=LaneFieldValue(lane_key="lane", field_key="quality", value="red")
        ),
        ScopeEvidence(value=scope),
        RulingsEvidence(
            value=LaneRulingSnapshot(lane_key="lane", issue_keys=("issue",), rulings=())
        ),
        GraphEvidence(
            value=LaneGraphSnapshot(
                lane_key="lane",
                fire_key="issue",
                milestone=scope,
                subtree=(),
                milestone_members=(),
                supersessions=(),
            )
        ),
    )
    assert len(values) == len(
        TypeAdapter(AlarmEvidence).json_schema()["discriminator"]["mapping"]
    )
    for value in values:
        reading = AlarmReading(
            source_ref="actual/native-record", value=value, at_sha="head"
        )
        restored = AlarmReading.model_validate_json(
            reading.model_dump_json(), strict=True
        )
        assert restored == reading
        assert type(restored.value) is type(value)
        with pytest.raises(ValidationError, match="frozen_instance"):
            restored.value.value = None


def test_evidence_union_is_closed_and_requires_its_discriminator():
    for value in (
        {"kind": "guessed", "value": 2},
        {"value": 2},
        {"kind": "count", "value": 2, "diagnosis": "invented"},
    ):
        with pytest.raises(ValidationError):
            AlarmReading(source_ref="actual-record", value=value)
