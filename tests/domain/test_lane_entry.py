"""The entry decision, one row of the re-entry rule per case (KOD-684).

Every expected value is written out here rather than computed from the facts,
so the table in the design and the table in the code are compared and not
merely both derived from the same expression.
"""

import pytest

from kodezart.domain.errors import LaneEntryError, SubjectAmendedError
from kodezart.domain.fire_spec import body_digest
from kodezart.domain.lane_entry import (
    RecordedBranches,
    RecordedCommit,
    decide_lane_entry,
    recorded_branches,
    recorded_commit,
    require_unamended_subject,
)
from kodezart.types.domain.branch import BranchAssociation, BranchRole
from kodezart.types.domain.fire_spec import TrackerSpec
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
    rows: tuple[LaneCommit, ...] | None = None,
    extra: tuple[BranchAssociation, ...] = (),
) -> LaneRunState:
    """One lane's record, as the writer leaves it after a pushed commit.

    ``rows`` is the commit-act sequence, which production writes ending at the
    head; a case that gives its own says what the two may disagree about.
    """
    return LaneRunState(
        lane_key=LANE,
        branch=loop,
        branch_url=f"https://forge.invalid/{loop}",
        head_sha=head,
        pushed_head_sha=head,
        commits_ahead=1,
        files_changed=1,
        commits=list(
            rows
            if rows is not None
            else (LaneCommit(sha=head, subject="feat: one", issue_id=LANE),)
        ),
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


def recorded(source: LaneRunState) -> tuple[LaneRunState, RecordedBranches]:
    """A record with the branches its associations resolve to.

    The reader pairs them before it reads the remote, because resolving them
    refuses and that refusal is a fact of the record alone.
    """
    return (source, recorded_branches(record=source))


def decide(**overrides):
    facts: dict[str, object] = {
        "issue_key": LANE,
        "recorded": None,
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
            "recorded": recorded(record()),
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
            "recorded": recorded(record(pr=PR)),
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
        {"recorded": recorded(record()), "remote_loop_head": REMOTE_HEAD},
        DeliverOnlyLane(
            deliverable_branch=DELIVERABLE,
            loop_branch=LOOP,
            head_sha=REMOTE_HEAD,
            body_digest=DIGEST,
        ),
    ),
    (
        "record, gap empty, pull request recorded",
        {"recorded": recorded(record(pr=PR)), "remote_loop_head": REMOTE_HEAD},
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
    """A record with no digest hands the fire nothing to compare the text with.

    That such a record is pinned by its next write is the composer's half, in
    ``tests/domain/test_lane_record.py``.
    """
    entry = decide(
        recorded=recorded(record(digest=None)),
        remote_loop_head=REMOTE_HEAD,
        open_criteria=("KOD-684/check",),
    )
    assert isinstance(entry, ResumedLane)
    assert entry.body_digest is None


def spec(body: str) -> TrackerSpec:
    """The subject text a fire read at its entry, as the reader hands it over."""
    return TrackerSpec(
        subject=LANE, body=body, criteria=(), read_at_version="read-at-entry"
    )


def test_a_digest_less_record_is_entered_without_a_comparison() -> None:
    """A record written before the pin existed is not compared against anything.

    Every lane record written before the digest field existed carries none, so
    this is the reading a resumed lane meets first; comparing it would refuse
    every such lane for good, with no text to blame for the difference.
    """
    entry = decide(
        recorded=recorded(record(digest=None)),
        remote_loop_head=REMOTE_HEAD,
        open_criteria=("KOD-684/check",),
    )

    require_unamended_subject(issue_key=LANE, entry=entry, spec=spec("any text at all"))

    # Not vacuous: the same reading with a digest on the record refuses, so the
    # absent digest is what the comparison was skipped for.
    compared = decide(
        recorded=recorded(record()),
        remote_loop_head=REMOTE_HEAD,
        open_criteria=("KOD-684/check",),
    )
    with pytest.raises(
        SubjectAmendedError, match="differs from the recorded"
    ) as caught:
        require_unamended_subject(
            issue_key=LANE, entry=compared, spec=spec("any text at all")
        )
    assert caught.value.recorded_digest == DIGEST
    assert caught.value.current_digest == body_digest("any text at all")


def test_a_resumed_lane_carries_the_remote_head_not_the_recorded_one() -> None:
    """The remote is the truth about what the branch contains.

    A commit pushed while its record write failed leaves the record behind;
    refusing would strand exactly that lane, so the entry takes the remote
    head and the next record write brings the record level again.
    """
    entry = decide(
        recorded=recorded(record()),
        remote_loop_head=REMOTE_HEAD,
        open_criteria=("KOD-684/check",),
    )
    assert isinstance(entry, ResumedLane)
    assert entry.head_sha == REMOTE_HEAD != RECORDED_HEAD


#: The best commit of a run that did not converge: recorded after the head
#: field's sha, so neither the head nor a branch name can stand in for it.
BEST_COMMIT = "b" * 40


def test_the_recorded_commit_is_the_last_row_on_the_role_resolved_branch() -> None:
    """At re-entry the record names the commit, and the roles name the branch.

    The rows are the commit acts, so the last of them is the best state the
    lane reached — not the head field, which a record may disagree with, and
    not a ref composed from another ref's text. Asserted by sha, and the
    branch by what the LOOP associations resolve.
    """
    source = record(
        rows=(
            LaneCommit(sha=RECORDED_HEAD, subject="feat: one", issue_id=LANE),
            LaneCommit(sha=BEST_COMMIT, subject="feat: two", issue_id=LANE),
        )
    )
    branches = recorded_branches(record=source)

    resolved = recorded_commit(record=source, branches=branches)

    assert resolved == RecordedCommit(branch=LOOP, sha=BEST_COMMIT)
    assert resolved.sha != source.head_sha
    assert resolved.branch == branches.loop_branch != branches.deliverable_branch


def test_a_record_naming_no_commit_act_refuses_at_re_entry() -> None:
    """A lane resumed against no recorded commit has nothing to grade.

    The head field would answer with a sha no row accounts for, so the
    resolution refuses and names the branch it was asked about instead.
    """
    source = record(rows=())
    with pytest.raises(LaneEntryError, match="names no commit act") as caught:
        recorded_commit(record=source, branches=recorded_branches(record=source))
    assert caught.value.branches == (LOOP,)


def test_a_recorded_branch_absent_from_the_remote_refuses_and_mints_nothing() -> None:
    with pytest.raises(LaneEntryError, match="absent from the remote") as caught:
        decide(recorded=recorded(record()), open_criteria=("KOD-684/check",))
    assert caught.value.branches == (LOOP,)


def test_a_recorded_base_that_is_no_longer_the_resolved_base_refuses() -> None:
    with pytest.raises(LaneEntryError, match="is not the base") as caught:
        decide(
            recorded=recorded(record()),
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
    ("extra", "reason", "named"),
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
            ("another-deliverable", DELIVERABLE),
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
            ("another-base", BASE),
        ),
    ],
    ids=["two-deliverables", "two-bases"],
)
def test_associations_that_do_not_resolve_refuse(extra, reason, named) -> None:
    """Asked of the record alone, which is what puts it before the remote read.

    ``tests/services/test_lane_entry.py`` pins that order over the reader;
    here it is the refusal itself.

    The refusal names the two the record could not settle between, sorted, so
    a person reading the failure knows what to repair; *named* is written out
    in that order rather than built from the fixture, so a refusal that
    answered in association order is visible here.
    """
    with pytest.raises(LaneEntryError, match=reason) as caught:
        recorded_branches(record=record(extra=extra))

    assert caught.value.branches == named


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
        recorded_branches(record=damaged)
