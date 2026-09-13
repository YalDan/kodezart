"""Frozen spec partition and opaque tracker identity carriage."""

import pytest
from pydantic import TypeAdapter, ValidationError

from kodezart.types.domain.fire_spec import (
    AuthoredSpec,
    CriterionRef,
    FireSpec,
    IssueRef,
    TrackerSpec,
)
from tests.fakes import make_ticket_draft


def tracker_spec(**changes):
    return TrackerSpec.model_validate(
        {
            "subject": "ISSUE-1",
            "body": "**Check:** Preserve this text.\n",
            "criteria": ["ISSUE-2", "ISSUE-3"],
            "read_at_version": "opaque-version",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "spec",
    [
        AuthoredSpec(ticket=make_ticket_draft()),
        tracker_spec(),
    ],
)
def test_partition_json_round_trip_and_frozen_fields(spec):
    adapter = TypeAdapter(FireSpec)
    assert adapter.validate_json(adapter.dump_json(spec)) == spec
    field = "ticket" if isinstance(spec, AuthoredSpec) else "body"
    with pytest.raises(ValidationError, match="frozen"):
        setattr(spec, field, getattr(spec, field))


def test_tracker_text_and_opaque_identities_are_carried_without_draft_parsing():
    spec = TrackerSpec(
        subject=IssueRef("opaque-subject"),
        body="not a ticket JSON",
        criteria=(CriterionRef("own/criterion/key"),),
        read_at_version="revision#1",
    )
    assert spec.body == "not a ticket JSON"
    assert spec.subject == "opaque-subject"
    assert spec.criteria == ("own/criterion/key",)
    assert "ticket" not in spec.model_dump()


@pytest.mark.parametrize(
    "changes",
    [
        {"subject": ""},
        {"criteria": [""]},
        {"read_at_version": ""},
        {"ticket": {}},
    ],
)
def test_invalid_tracker_provenance_is_rejected(changes):
    with pytest.raises(ValidationError):
        tracker_spec(**changes)
