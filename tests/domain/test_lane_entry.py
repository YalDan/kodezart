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
from kodezart.domain.lane_record import LANDING_ROW_SUBJECT, next_lane_record
from kodezart.types.domain.branch import BranchAssociation, BranchRole
from kodezart.types.domain.consolidation import ChangesetDigest
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.lane_entry import DeliverOnlyLane, NewLane, ResumedLane
from kodezart.types.domain.run_state import (
    LaneBinding,
    LaneCommit,
    LanePR,
    LaneRunState,
)

LANE = "KOD-684"
BASE = "trunk"
LOOP = "kodezart/KOD-684-0a1b2c3d-ralph-11112222"
DELIVERABLE = "kodezart/KOD-684-0a1b2c3d"
RECORDED_HEAD = "a" * 40
REMOTE_HEAD = "c" * 40
#: Where the remote holds the deliverable branch: its own sha, read at its own
#: level, so neither level's head can stand in for the other's.
DELIVERABLE_HEAD = "e" * 40
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

    ``rows`` is the commit-act sequence, and only two cases give their own: the
    record whose last act the head field disagrees with, and the record naming
    no act at all. Neither is a state the one constructor composes — every row
    it writes carries the head it was written at — and each is the state its
    case is about. ``composed`` below builds the records production writes.
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
        "remote_deliverable_head": DELIVERABLE_HEAD,
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
            deliverable_head_sha=DELIVERABLE_HEAD,
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
            deliverable_head_sha=DELIVERABLE_HEAD,
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
            deliverable_head_sha=DELIVERABLE_HEAD,
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


def test_a_deliverable_branch_the_remote_does_not_hold_is_carried_as_absent() -> None:
    """Absence at the deliverable level is its own reading, and not a refusal.

    A lane that pushed a loop branch and no deliverable one is exactly the lane
    a resumed entry exists for, so the entry is decided and says the branch is
    not there — it does not borrow the loop level's sha to answer for it.
    """
    entry = decide(
        recorded=recorded(record()),
        remote_loop_head=REMOTE_HEAD,
        remote_deliverable_head=None,
        open_criteria=("KOD-684/check",),
    )
    assert isinstance(entry, ResumedLane)
    assert entry.deliverable_head_sha is None
    assert entry.head_sha == REMOTE_HEAD


#: The commit acts of a run that did not converge, in the order it made them:
#: the second is the best of them by the trajectory's own rule, and the third
#: is the tip the run slipped back to.
ACTS = ("1" * 40, "2" * 40, "3" * 40)
#: What the stall landing consolidated onto the deliverable branch: the best
#: act, which is where that branch then stood.
LANDED = ACTS[1]
PRE_LANDING_TIP = ACTS[2]
#: A commit act recorded after the head field's own sha: the state only a
#: record nothing composed can be in, and the one the head-field case needs.
BEST_COMMIT = "b" * 40


def composed(*acts: str, landed: str | None = None) -> LaneRunState:
    """This lane's record, composed act by act the only way production is.

    Every row goes through ``next_lane_record`` (KOD-685), so the value is one
    the writer could have left. *landed* is the stall landing's act — the tip
    the consolidation left the deliverable branch at — and the push stays where
    the loop left it, because that act moved no branch of this lane's own.
    """
    binding = LaneBinding(
        lane_key=LANE,
        loop_branch=LOOP,
        deliverable_branch=DELIVERABLE,
        base_ref=BASE,
        body_digest=DIGEST,
        repo_url=None,
        repo_path=None,
        run_id="first-job",
        visibility=RepoVisibility.PRIVATE,
    )
    state: LaneRunState | None = None
    for index, sha in enumerate(acts):
        state = next_lane_record(
            prior=state,
            lane=binding,
            branch_url=f"https://forge.invalid/{LOOP}",
            head_sha=sha,
            pushed_head_sha=sha,
            changeset=ChangesetDigest(
                file_paths=[f"lane-{step}.py" for step in range(index + 1)],
                commit_subjects=[f"feat: act {step + 1}" for step in range(index + 1)],
                commit_count=index + 1,
            ),
            subject=f"feat: act {index + 1}",
        )
    assert state is not None
    if landed is None:
        return state
    return next_lane_record(
        prior=state,
        lane=binding,
        branch_url=f"https://forge.invalid/{LOOP}",
        head_sha=landed,
        pushed_head_sha=state.pushed_head_sha,
        changeset=ChangesetDigest(file_paths=[], commit_subjects=[], commit_count=0),
        subject=LANDING_ROW_SUBJECT,
    )


def test_the_landed_best_is_the_commit_a_re_entry_resolves() -> None:
    """The landing act is the last row, so the resolution answers with it.

    The record is composed act by act through the one constructor, which is why
    it is the pin: this is the record a stalled lane's own writer leaves, so the
    reading holds for lanes rather than only for a fixture. The run's three acts
    are followed by the landing, and what re-entry resolves is that act — on the
    branch the LOOP associations resolve, by sha. The tip the run slipped back
    to is a row of this record too, and it is not the answer.
    """
    source = composed(*ACTS, landed=LANDED)
    branches = recorded_branches(record=source)

    resolved = recorded_commit(record=source, branches=branches)

    assert resolved == RecordedCommit(branch=LOOP, sha=LANDED)
    assert [row.sha for row in source.commits] == [*ACTS, LANDED]
    assert resolved.sha != PRE_LANDING_TIP
    assert resolved.sha != source.commits[0].sha
    assert source.commits[-1].subject == LANDING_ROW_SUBJECT
    # The loop branch is still where the loop left it: the landing consolidated
    # onto the other level and moved no branch of this lane's own.
    assert source.pushed_head_sha == PRE_LANDING_TIP
    assert resolved.branch == branches.loop_branch != branches.deliverable_branch


def test_the_recorded_commit_is_the_last_row_on_the_role_resolved_branch() -> None:
    """At re-entry the record names the commit, and the roles name the branch.

    The rows are the commit acts, so the last of them is the best state the
    lane reached — not the head field, which a record may disagree with, and
    not a ref composed from another ref's text. Asserted by sha, and the
    branch by what the LOOP associations resolve.

    Kept on a hand-built record deliberately: every row the one constructor
    composes carries the head it was written at, so no record production writes
    can say WHICH of the two the resolution reads, and a reachable fixture
    would answer the same whichever it read.
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


def test_associations_no_role_claims_for_this_branch_are_ignored() -> None:
    """Each resolution reads its OWN role, on its OWN branch, and nothing else.

    The record's model permits a DELIVERABLE association on the loop branch and
    LOOP associations on branches that are not the recorded one, so "resolved
    by ROLE" has to be the thing doing the work rather than branch equality
    standing in for it. Three associations that would each be read if one of
    the three conditions were dropped, and the resolution is unmoved:

    * DELIVERABLE on the loop branch — read only if the deliverable side stopped
      requiring the LOOP role;
    * LOOP on a branch this record is not on — read only if that side stopped
      requiring the recorded branch;
    * LOOP on the deliverable branch — read as a second base only if the base
      side stopped requiring the DELIVERABLE role, and as a second deliverable
      if it stopped requiring the recorded branch.
    """
    resolved = recorded_branches(
        record=record(
            extra=(
                BranchAssociation(
                    branch=LOOP,
                    role=BranchRole.DELIVERABLE,
                    derived_from="a-base-no-role-resolves",
                    run_id="second-job",
                ),
                BranchAssociation(
                    branch="an-unrelated-branch",
                    role=BranchRole.LOOP,
                    derived_from="another-deliverable",
                    run_id="third-job",
                ),
                BranchAssociation(
                    branch=DELIVERABLE,
                    role=BranchRole.LOOP,
                    derived_from="another-base",
                    run_id="fourth-job",
                ),
            )
        )
    )

    assert resolved == RecordedBranches(
        loop_branch=LOOP, deliverable_branch=DELIVERABLE, recorded_base=BASE
    )


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
