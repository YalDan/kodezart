"""The entry decision, one row of the re-entry rule per case (KOD-684).

Every expected value is written out here rather than computed from the facts,
so the table in the design and the table in the code are compared and not
merely both derived from the same expression.
"""

import pytest

from kodezart.domain import lane_entry as lane_entry_module
from kodezart.domain.errors import LaneEntryError, SubjectAmendedError
from kodezart.domain.fire_spec import body_digest
from kodezart.domain.lane_entry import (
    RecordedBranches,
    RecordedLane,
    decide_lane_entry,
    recorded_branches,
    recorded_commit,
    recorded_lane,
    require_unamended_subject,
)
from kodezart.domain.lane_record import LANDING_ROW_SUBJECT, next_lane_record
from kodezart.types.domain.branch import (
    BaseInput,
    BaseSpec,
    BranchAssociation,
    BranchRole,
    WorkRefRole,
    trunk_base,
)
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
    dispatch_base: BaseSpec | None = None,
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
        dispatch_base=dispatch_base,
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


def recorded(source: LaneRunState) -> RecordedLane:
    """A record with the branches its associations resolve to and its head.

    The reader resolves them before it reads the remote, because resolving
    them refuses and that refusal is a fact of the record alone.
    """
    return recorded_lane(record=source)


def decide(**overrides):
    facts: dict[str, object] = {
        "issue_key": LANE,
        "recorded": None,
        "remote_loop_head": None,
        "remote_deliverable_head": DELIVERABLE_HEAD,
        "open_criteria": (),
        "implied_base": trunk_base(BASE),
    }
    return decide_lane_entry(**{**facts, **overrides})


PR = LanePR(url="https://forge.invalid/pull/7", number=7, state="open")

#: Every row of the re-entry rule, with the value it decides written out.
#: "level" is the remote holding the recorded loop branch at the record's head;
#: "past" is the remote holding it anywhere else. The head is the record's in
#: every row, and only a level loop branch is carried to be continued.
ROWS = (
    ("no record, gap open", {"open_criteria": ("KOD-684/check",)}, NewLane()),
    ("no record, gap empty", {}, None),
    (
        "record, gap open, level",
        {
            "recorded": recorded(record()),
            "remote_loop_head": RECORDED_HEAD,
            "open_criteria": ("KOD-684/check",),
        },
        ResumedLane(
            deliverable_branch=DELIVERABLE,
            loop_branch=LOOP,
            head_sha=RECORDED_HEAD,
            deliverable_head_sha=DELIVERABLE_HEAD,
            body_digest=DIGEST,
            base_stale=False,
        ),
    ),
    (
        "record, gap open, loop branch past the head",
        {
            "recorded": recorded(record()),
            "remote_loop_head": REMOTE_HEAD,
            "open_criteria": ("KOD-684/check",),
        },
        ResumedLane(
            deliverable_branch=DELIVERABLE,
            loop_branch=None,
            head_sha=RECORDED_HEAD,
            deliverable_head_sha=DELIVERABLE_HEAD,
            body_digest=DIGEST,
            base_stale=False,
        ),
    ),
    (
        "record with a pull request, gap open, level",
        {
            "recorded": recorded(record(pr=PR)),
            "remote_loop_head": RECORDED_HEAD,
            "open_criteria": ("KOD-684/check",),
        },
        ResumedLane(
            deliverable_branch=DELIVERABLE,
            loop_branch=LOOP,
            head_sha=RECORDED_HEAD,
            deliverable_head_sha=DELIVERABLE_HEAD,
            body_digest=DIGEST,
            base_stale=False,
        ),
    ),
    (
        "record, gap empty, no pull request, level",
        {"recorded": recorded(record()), "remote_loop_head": RECORDED_HEAD},
        DeliverOnlyLane(
            deliverable_branch=DELIVERABLE,
            loop_branch=LOOP,
            head_sha=RECORDED_HEAD,
            deliverable_head_sha=DELIVERABLE_HEAD,
            body_digest=DIGEST,
            base_stale=False,
        ),
    ),
    (
        "record, gap empty, pull request recorded, level",
        {"recorded": recorded(record(pr=PR)), "remote_loop_head": RECORDED_HEAD},
        None,
    ),
    (
        "record, gap empty, pull request recorded, loop branch past the head",
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


def test_a_resumed_lane_carries_the_recorded_head_not_the_remote_one() -> None:
    """The record is the truth about where the lane stands (KOD-705, KOD-96).

    A loop branch the remote holds past the record's head — a landing chose an
    earlier commit, or a commit was pushed while its record write failed — is
    not resumed from: the entry carries the head the record names and no loop
    branch, so the fire cuts a fresh one from that head instead of resuming at
    a tip the record does not name, and the lane is not stranded either.
    """
    entry = decide(
        recorded=recorded(record()),
        remote_loop_head=REMOTE_HEAD,
        open_criteria=("KOD-684/check",),
    )
    assert isinstance(entry, ResumedLane)
    assert entry.head_sha == RECORDED_HEAD != REMOTE_HEAD
    assert entry.loop_branch is None


def test_a_lane_owing_nothing_whose_loop_branch_left_its_head_refuses() -> None:
    """Delivering a loop branch off the record's head delivers an unnamed commit.

    A lane owing no criterion and holding no pull request is dispatched to
    deliver its loop branch as it stands; where that branch is not at the head
    the record names, what it would deliver is a commit the record does not
    name, so the entry refuses before any backend call, naming the branch.
    Not vacuous: the same record with the loop branch level is delivered.
    """
    with pytest.raises(
        LaneEntryError, match="does not stand at the record's last"
    ) as caught:
        decide(recorded=recorded(record()), remote_loop_head=REMOTE_HEAD)
    assert caught.value.branches == (LOOP,)

    level = decide(recorded=recorded(record()), remote_loop_head=RECORDED_HEAD)
    assert isinstance(level, DeliverOnlyLane)
    assert level.head_sha == RECORDED_HEAD


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
    assert entry.head_sha == RECORDED_HEAD


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
        base=trunk_base(BASE),
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
    are followed by the landing, and what re-entry resolves is that act, by
    sha. The tip the run slipped back to is a row of this record too, and it is
    not the answer — and the entry over a remote whose loop branch still stands
    at that tip resumes at the landed act, on no loop branch.
    """
    source = composed(*ACTS, landed=LANDED)

    resolved = recorded_lane(record=source)

    assert resolved.head.sha == LANDED
    assert [row.sha for row in source.commits] == [*ACTS, LANDED]
    assert resolved.head.sha != PRE_LANDING_TIP
    assert resolved.head.sha != source.commits[0].sha
    assert resolved.head.subject == LANDING_ROW_SUBJECT
    # The loop branch is still where the loop left it: the landing consolidated
    # onto the other level and moved no branch of this lane's own.
    assert source.pushed_head_sha == PRE_LANDING_TIP
    assert resolved.branches.loop_branch == LOOP != resolved.branches.deliverable_branch

    entry = decide(
        recorded=resolved,
        remote_loop_head=PRE_LANDING_TIP,
        remote_deliverable_head=LANDED,
        open_criteria=("KOD-684/check",),
    )
    assert entry == ResumedLane(
        deliverable_branch=DELIVERABLE,
        loop_branch=None,
        head_sha=LANDED,
        deliverable_head_sha=LANDED,
        body_digest=DIGEST,
        base_stale=False,
    )


def test_the_recorded_commit_is_the_last_row_on_the_role_resolved_branch() -> None:
    """At re-entry the record names the commit, and the roles name the branch.

    The rows are the commit acts, so the last of them is where the lane
    stands — not the head field, which a record may disagree with, and not a
    ref composed from another ref's text. Asserted by sha, and the branch by
    what the LOOP associations resolve.

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

    resolved = recorded_lane(record=source)

    assert resolved.head == LaneCommit(
        sha=BEST_COMMIT, subject="feat: two", issue_id=LANE
    )
    assert resolved.head.sha != source.head_sha
    assert resolved.branches.loop_branch == LOOP != resolved.branches.deliverable_branch


def test_a_record_naming_no_commit_act_refuses_at_re_entry() -> None:
    """A lane resumed against no recorded commit has nothing to grade.

    The head field would answer with a sha no row accounts for, so the
    resolution refuses and names the branch it was asked about instead.
    """
    source = record(rows=())
    with pytest.raises(LaneEntryError, match="names no commit act") as caught:
        recorded_commit(record=source)
    assert caught.value.branches == (LOOP,)
    with pytest.raises(LaneEntryError, match="names no commit act"):
        recorded_lane(record=source)


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
            implied_base=trunk_base("some-blocker-branch"),
        )
    assert caught.value.branches == (DELIVERABLE,)


def role_decoys() -> list[tuple[BranchAssociation, BranchAssociation, str]]:
    """Every association a role resolution must ignore, each with its control.

    The roles are derived from ``BranchRole`` itself, so a role added later is
    a decoy here the day it exists. Each row is a decoy, the same association
    with its role (or branch) changed to the one the resolution keys on, and
    the refusal that control provokes — so each decoy is shown to be one the
    resolution WOULD read if it keyed on anything but that role on that branch.

    * on the loop branch, every role but LOOP, naming another deliverable;
    * on the deliverable branch, every role but DELIVERABLE, naming another
      base;
    * DELIVERABLE on a third branch, naming another base: read only if the
      base side keyed on "not the loop branch" rather than on the deliverable
      branch the LOOP association names.
    """
    third = "a-third-branch"
    rows: list[tuple[BranchAssociation, BranchAssociation, str]] = []
    for role in BranchRole:
        if role is not BranchRole.LOOP:
            decoy = BranchAssociation(
                branch=LOOP,
                role=role,
                derived_from="another-deliverable",
                run_id=f"loop-{role}",
            )
            rows.append(
                (
                    decoy,
                    decoy.model_copy(update={"role": BranchRole.LOOP}),
                    "deliverable branches, not one",
                )
            )
        if role is not BranchRole.DELIVERABLE:
            decoy = BranchAssociation(
                branch=DELIVERABLE,
                role=role,
                derived_from="another-base",
                run_id=f"deliverable-{role}",
            )
            rows.append(
                (
                    decoy,
                    decoy.model_copy(update={"role": BranchRole.DELIVERABLE}),
                    "bases, not one",
                )
            )
    decoy = BranchAssociation(
        branch=third,
        role=BranchRole.DELIVERABLE,
        derived_from="another-base",
        run_id="third-branch",
    )
    rows.append(
        (decoy, decoy.model_copy(update={"branch": DELIVERABLE}), "bases, not one")
    )
    return rows


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

    Then every decoy ``role_decoys`` derives from the enum, together and each
    alone, leaves the resolution unmoved, and each one's control refuses. So a
    resolution that keys on "not the other role" rather than on its own role
    reads the RECOVERY decoy, and one that keys on "not the loop branch" rather
    than on the deliverable branch reads the third-branch decoy — both refuse.
    """
    unmoved = RecordedBranches(
        loop_branch=LOOP, deliverable_branch=DELIVERABLE, recorded_base=BASE
    )
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

    assert resolved == unmoved

    decoys = role_decoys()
    assert {decoy.role for decoy, _, _ in decoys} == set(BranchRole)
    assert (
        recorded_branches(record=record(extra=tuple(d for d, _, _ in decoys)))
        == unmoved
    )
    for decoy, control, reason in decoys:
        assert recorded_branches(record=record(extra=(decoy,))) == unmoved, decoy
        with pytest.raises(LaneEntryError, match=reason):
            recorded_branches(record=record(extra=(control,)))


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


#: A base one blocker's delivery implies: the arm whose branch name survives
#: that delivery advancing, so the name check passes and the reading decides.
DISPATCHED = BaseSpec(
    inputs=(
        BaseInput(blocker_issue_id="KOD-1", branch="kodezart/KOD-1-d", sha="1" * 40),
    ),
    base_branch="kodezart/KOD-1-d",
    base_role=WorkRefRole.DELIVERABLE,
)
#: The same base after the blocker's delivery advanced by one commit.
ADVANCED = DISPATCHED.model_copy(
    update={"inputs": (DISPATCHED.inputs[0].model_copy(update={"sha": "2" * 40}),)}
)

#: How each lane kind is reached from one record: criteria still open, or
#: none open and no pull request recorded.
KINDS = {
    "resumed": (("KOD-684/check",), ResumedLane),
    "deliver-only": ((), DeliverOnlyLane),
}

#: The record's pinned base, the base resolving now, and the reading owed.
READINGS = {
    "live": (DISPATCHED, DISPATCHED.model_copy(deep=True), False),
    "stale": (DISPATCHED, ADVANCED, True),
    "unpinned": (None, ADVANCED, False),
}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("reading", READINGS, ids=[f"{r}-base" for r in READINGS])
def test_the_entry_reads_the_recorded_dispatch_base_against_the_implied_one(
    kind, reading
) -> None:
    """Live when the pinned base is the one resolving now, stale when it moved.

    Both entry kinds carry the reading, because a deliver-only round runs the
    loop too. A record that pinned no base reads live whatever resolves now.
    """
    open_criteria, entry_kind = KINDS[kind]
    pinned, implied, stale = READINGS[reading]
    assert implied.base_branch == DISPATCHED.base_branch

    # The loop branch stands at the record's last commit act, the one remote
    # reading under which both kinds enter: a deliver-only lane whose branch
    # has left that head refuses before any base is read.
    entry = decide(
        recorded=recorded(record(base=DISPATCHED.base_branch, dispatch_base=pinned)),
        remote_loop_head=RECORDED_HEAD,
        open_criteria=open_criteria,
        implied_base=implied,
    )

    assert isinstance(entry, entry_kind)
    assert entry.base_stale is stale


@pytest.mark.parametrize(
    ("implied_of", "says"),
    [
        (lambda: DISPATCHED.model_copy(deep=True), True),
        (lambda: ADVANCED, False),
    ],
    ids=["equal-bases-read-stale", "advanced-base-reads-live"],
)
def test_the_entry_reading_is_is_base_stales_answer(
    monkeypatch, implied_of, says
) -> None:
    """The entry asks the landed comparison once and carries what it says.

    The recorder answers the opposite of the real comparison, so an entry that
    compared the two bases itself would read live on the equal pair and stale
    on the advanced one.  Both directions, so a second comparison ORed or
    ANDed beside the landed one is wrong on one of them.
    """
    calls: list[tuple[BaseSpec, BaseSpec]] = []

    def recorder(recorded_base: BaseSpec, implied_base: BaseSpec) -> bool:
        calls.append((recorded_base, implied_base))
        return recorded_base == implied_base

    monkeypatch.setattr(lane_entry_module, "is_base_stale", recorder)
    source = record(base=DISPATCHED.base_branch, dispatch_base=DISPATCHED)
    implied = implied_of()

    entry = decide(
        recorded=recorded(source),
        remote_loop_head=REMOTE_HEAD,
        open_criteria=("KOD-684/check",),
        implied_base=implied,
    )

    assert isinstance(entry, ResumedLane)
    assert entry.base_stale is says
    assert len(calls) == 1
    assert calls[0][0] is source.dispatch_base
    assert calls[0][1] is implied
    assert calls[0][0] is not calls[0][1]
