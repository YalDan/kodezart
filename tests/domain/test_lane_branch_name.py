"""A native lane's branch name is arithmetic over its issue key (KOD-839).

Naming a lane's branch involves no judgement, so the name is composed in the
module that owns ref shapes and a key that could not stand inside a ref is
refused there — before any git call and without a session.
"""

import re

import pytest
from pydantic import ValidationError

from kodezart.domain.agent import mint_lane_branches
from kodezart.domain.errors import LaneEntryError
from kodezart.types.domain.branch import LaneBranchName

#: Keys no branch ref can carry: git refuses each of these shapes outright.
UNUSABLE_KEYS = (
    "",
    "KOD 841",
    "KOD~841",
    "KOD^841",
    "KOD:841",
    "KOD?841",
    "KOD*841",
    "KOD[841]",
    "KOD..841",
    "-KOD-841",
    "KOD-841-",
    "/KOD-841",
    "KOD-841/",
    "KOD//841",
    "KOD\n841",
)


def test_the_name_is_the_issue_key_and_a_short_id() -> None:
    name = LaneBranchName(issue_key="KOD-841", short_id="0a1b2c3d")
    assert str(name) == "kodezart/KOD-841-0a1b2c3d"
    # A key that is itself a path stays one: its segments are each checked.
    assert (
        str(LaneBranchName(issue_key="fire/subject", short_id="deadbeef"))
        == "kodezart/fire/subject-deadbeef"
    )


@pytest.mark.parametrize("key", UNUSABLE_KEYS)
def test_a_key_that_cannot_be_a_ref_segment_is_refused(key: str) -> None:
    with pytest.raises(ValidationError):
        LaneBranchName(issue_key=key, short_id="0a1b2c3d")
    # And the minting function turns that into the lane's own typed refusal,
    # so a walk contains it as this lane's fault and mints nothing.
    with pytest.raises(LaneEntryError, match="cannot stand inside a branch ref"):
        mint_lane_branches(key)


@pytest.mark.parametrize(
    "short_id", ["", "0a1b2c3", "0a1b2c3de", "0A1B2C3D", "zzzzzzzz"]
)
def test_the_short_id_is_exactly_eight_lowercase_hex_digits(short_id: str) -> None:
    with pytest.raises(ValidationError):
        LaneBranchName(issue_key="KOD-841", short_id=short_id)


def test_minting_draws_a_loop_branch_derived_from_the_deliverable() -> None:
    deliverable, loop = mint_lane_branches("KOD-684")
    assert re.fullmatch(r"kodezart/KOD-684-[0-9a-f]{8}", deliverable)
    assert re.fullmatch(rf"{re.escape(deliverable)}-ralph-[0-9a-f]{{8}}", loop)
    # Two lanes of the same issue never collide: the short id is drawn.
    assert mint_lane_branches("KOD-684")[0] != deliverable
