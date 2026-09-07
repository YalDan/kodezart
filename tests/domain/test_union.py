"""Scratch identities survive serialization without becoming branch claims."""

import pytest
from pydantic import ValidationError

from kodezart.types.domain.union import UnionLaneHead, UnionScratchObservation


def head(key="z", sha="a" * 40):
    return UnionLaneHead(lane_key=key, branch=f"work/{key}", head_sha=sha)


def observation(**changes):
    data = dict(
        scope_key="scope",
        repository_url="file:///repo",
        base_sha="b" * 40,
        lane_heads=(head(), head("a", "c" * 40)),
        scratch_path="/scratch/discarded",
        scratch_sha="d" * 40,
    )
    data.update(changes)
    return UnionScratchObservation(**data)


def test_measured_heads_keep_planner_order_and_scratch_identity():
    original = observation()
    result = UnionScratchObservation.model_validate_json(original.model_dump_json())
    assert result == original
    assert result.composition_order == ("z", "a")
    assert [h.head_sha for h in result.lane_heads] == ["a" * 40, "c" * 40]
    assert result.artifact_kind == "scratch"
    assert "branch" not in result.model_dump()
    with pytest.raises(ValidationError):
        result.lane_heads[0].head_sha = "e" * 40


@pytest.mark.parametrize(
    "changes",
    [
        {"lane_heads": ()},
        {"lane_heads": (head(), head())},
        {"artifact_kind": "branch"},
        {"base_sha": "main"},
        {"scratch_sha": "HEAD"},
        {"scope_key": " "},
    ],
)
def test_invalid_or_unpinned_scratch_claim_refuses(changes):
    with pytest.raises(ValidationError):
        observation(**changes)
