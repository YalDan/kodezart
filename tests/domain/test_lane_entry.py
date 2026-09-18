"""The entry decision, one row of the re-entry rule per case (KOD-684).

Every expected value is written out here rather than computed from the facts,
so the table in the design and the table in the code are compared and not
merely both derived from the same expression.
"""

import pytest

from kodezart.domain.errors import LaneEntryError
from kodezart.domain.lane_entry import decide_lane_entry, recorded_branches
from kodezart.types.domain.branch import BranchAssociation, BranchRole
from kodezart.types.domain.lane_entry import DeliverOnlyLane, NewLane, ResumedLane
from kodezart.types.domain.run_state import LaneCommit, LanePR, LaneRunState

LANE = "KOD-684"
BASE = "trunk"
LOOP = "kodezart/KOD-684-0a1b2c3d-ralph-11112222"
DELIVERABLE = "kodezart/KOD-684-0a1b2c3d"
RECORDED_HEAD = "a" * 40
REMOTE_HEAD = "c" * 40
DIGEST = "d" * 64


def record(
    *,
    loop: str = LOOP,
    deliverable: str = DELIVERABLE,
    base: str = BASE,
    head: str = RECORDED_HEAD,
    digest: str | None = DIGEST,
    pr: LanePR | None = None,
    extra: tuple[BranchAssociation, ...] = (),
) -> LaneRunState:
    """One lane's record, as the writer leaves it after a pushed commit."""
    return LaneRunState(
        lane_key=LANE,
        branch=loop,
        branch_url=f"https://forge.invalid/{loop}",
        head_sha=head,
        pushed_head_sha=head,
        commits_ahead=1,
        files_changed=1,
        commits=[LaneCommit(sha=head, subject="feat: one", issue_id=LANE)],
        pr=pr,
        body_digest=digest,
        associations=[
            BranchAssociation(
                branch=deliverable,
                role=BranchRole.DELIVERABLE,
                derived_from=base,
                run_id="first-job",
            ),
            BranchAssociation(
                branch=loop,
                role=BranchRole.LOOP,
                derived_from=deliverable,
                run_id="first-job",
            ),
            *extra,
        ],
    )


def decide(**overrides):
    facts: dict[str, object] = {
        "issue_key": LANE,
        "record": None,
        "remote_loop_head": None,
        "open_criteria": (),
        "resolved_base": BASE,
    }
    return decide_lane_entry(**{**facts, **overrides})


PR = LanePR(url="https://forge.invalid/pull/7", number=7, state="open")

#: Every row of the re-entry rule, with the value it decides written out.
ROWS = (
    ("no record, gap open", {"open_criteria": ("KOD-684/check",)}, NewLane()),
    ("no record, gap empty", {}, None),
    (
        "record, gap open",
        {
            "record": record(),
            "remote_loop_head": REMOTE_HEAD,
            "open_criteria": ("KOD-684/check",),
        },
        ResumedLane(
            deliverable_branch=DELIVERABLE,
            loop_branch=LOOP,
            head_sha=REMOTE_HEAD,
            body_digest=DIGEST,
        ),
    ),
    (
        "record with a pull request, gap open",
        {
            "record": record(pr=PR),
            "remote_loop_head": REMOTE_HEAD,
            "open_criteria": ("KOD-684/check",),
        },
        ResumedLane(
            deliverable_branch=DELIVERABLE,
            loop_branch=LOOP,
            head_sha=REMOTE_HEAD,
            body_digest=DIGEST,
        ),
    ),
    (
        "record, gap empty, no pull request",
        {"record": record(), "remote_loop_head": REMOTE_HEAD},
        DeliverOnlyLane(
            deliverable_branch=DELIVERABLE,
            loop_branch=LOOP,
            head_sha=REMOTE_HEAD,
            body_digest=DIGEST,
        ),
    ),
    (
        "record, gap empty, pull request recorded",
        {"record": record(pr=PR), "remote_loop_head": REMOTE_HEAD},
        None,
    ),
)


@pytest.mark.parametrize(
    ("facts", "expected"),
    [(facts, expected) for _, facts, expected in ROWS],
    ids=[name for name, _, _ in ROWS],
)
def test_each_row_of_the_entry_table(facts, expected) -> None:
    assert decide(**facts) == expected


def test_a_record_written_before_the_pin_carries_no_digest_to_compare() -> None:
    """A record with no digest is not compared, and is pinned by its next write."""
    entry = decide(
        record=record(digest=None),
        remote_loop_head=REMOTE_HEAD,
        open_criteria=("KOD-684/check",),
    )
    assert isinstance(entry, ResumedLane)
    assert entry.body_digest is None


def test_a_resumed_lane_carries_the_remote_head_not_the_recorded_one() -> None:
    """The remote is the truth about what the branch contains.

    A commit pushed while its record write failed leaves the record behind;
    refusing would strand exactly that lane, so the entry takes the remote
    head and the next record write brings the record level again.
    """
    entry = decide(
        record=record(),
        remote_loop_head=REMOTE_HEAD,
        open_criteria=("KOD-684/check",),
    )
    assert isinstance(entry, ResumedLane)
    assert entry.head_sha == REMOTE_HEAD != RECORDED_HEAD


def test_a_recorded_branch_absent_from_the_remote_refuses_and_mints_nothing() -> None:
    with pytest.raises(LaneEntryError, match="absent from the remote") as caught:
        decide(record=record(), open_criteria=("KOD-684/check",))
    assert caught.value.branches == (LOOP,)


def test_a_recorded_base_that_is_no_longer_the_resolved_base_refuses() -> None:
    with pytest.raises(LaneEntryError, match="is not the base") as caught:
        decide(
            record=record(),
            remote_loop_head=REMOTE_HEAD,
            open_criteria=("KOD-684/check",),
            resolved_base="some-blocker-branch",
        )
    assert caught.value.branches == (DELIVERABLE,)


def test_roles_are_resolved_from_associations_not_names() -> None:
    """A name is a name: the loop branch here has no ralph in it and the
    deliverable does, and the roles still resolve the other way round."""
    resolved = recorded_branches(
        record=record(loop="ordinary", deliverable="has-ralph-in-it")
    )
    assert resolved.loop_branch == "ordinary"
    assert resolved.deliverable_branch == "has-ralph-in-it"
    assert resolved.recorded_base == BASE


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        (
            (
                BranchAssociation(
                    branch=LOOP,
                    role=BranchRole.LOOP,
                    derived_from="another-deliverable",
                    run_id="second-job",
                ),
            ),
            "deliverable branches, not one",
        ),
        (
            (
                BranchAssociation(
                    branch=DELIVERABLE,
                    role=BranchRole.DELIVERABLE,
                    derived_from="another-base",
                    run_id="second-job",
                ),
            ),
            "bases, not one",
        ),
    ],
    ids=["two-deliverables", "two-bases"],
)
def test_associations_that_do_not_resolve_refuse(extra, reason) -> None:
    with pytest.raises(LaneEntryError, match=reason):
        decide(
            record=record(extra=extra),
            remote_loop_head=REMOTE_HEAD,
            open_criteria=("KOD-684/check",),
        )


def test_a_loop_association_with_no_derived_from_refuses() -> None:
    """A record that names no deliverable for its branch is not "no record"."""
    damaged = record().model_copy(
        update={
            "associations": [
                BranchAssociation(
                    branch=LOOP,
                    role=BranchRole.LOOP,
                    derived_from=None,
                    run_id="first-job",
                )
            ]
        }
    )
    with pytest.raises(LaneEntryError, match="deliverable branches, not one"):
        decide(
            record=damaged,
            remote_loop_head=REMOTE_HEAD,
            open_criteria=("KOD-684/check",),
        )
