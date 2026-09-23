"""A native criterion's identity is its own sub-issue key, and nothing moves it.

Every case here runs over each registered implementation — the shipped adapter
against the in-process vendor server, and the consumer double seeded from the
same workspace — because what a consumer may rely on about identity is a fact
about the port and not about one backend.

The `AC-n` token in a criterion's title is deliberately present and
deliberately not identity: the board may rewrite it, and the key it sits beside
does not follow. The authored arm enumerates its own ids from 1 each round and
is not bound by any of this; the last case pins that positively so nothing here
can be read as binding it.
"""

import pytest

from kodezart.domain.agent import mint_ruling_id
from kodezart.domain.criteria import mint_criteria, mint_criterion_id
from kodezart.domain.criterion_amendment import require_criterion_source
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.domain.errors import (
    CriterionReadError,
    CriterionResolutionError,
    StaleWriteError,
)
from kodezart.domain.fire_spec import tracker_spec_from_issues
from kodezart.domain.rulings import addressable_issues
from kodezart.services.criterion_sources import NativeCriterionResolver
from kodezart.services.lane_state_writer import TrackerLaneStateWriter
from kodezart.types.domain.agent import RulingProtectedTestRef
from kodezart.types.domain.audit_forge import AuditForgeRequest
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.criteria import DraftedCriterion, TrackerCriterion
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_lifecycle import CriterionCrossOff, CrossOffState
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.run_state import LaneBinding
from kodezart.types.domain.tracker import IssueRelationKind, WorkflowStateKind
from tests.fakes import FakeGitService, FakeMcpIssue, FakeTrackerPort, PassThroughGate
from tests.lane_fixture import lane_operation
from tests.tracker.conftest import (
    FIRE_ENTRY_LABELS,
    FIXTURE_NOW,
    ISSUE_LABELS,
    fixture_server,
)

LABEL = ISSUE_LABELS["criterion"]

SUBJECT = "subject/renumbering"
#: Three criterion sub-issues whose keys carry no position, in the order both
#: implementations report them: the double sorts by key and the adapter serves
#: the child listing, so a fixture whose insertion order is its key order reads
#: the same way through either.
FIRST = "condition/alpha"
SECOND = "condition/beta"
THIRD = "condition/gamma"

#: A second subject, for the supersession arm alone: keeping it off the three-
#: child family lets the removal case state its family exactly.
ABSORBING_SUBJECT = "subject/supersession"
SUPERSEDED = "condition/delta"
SUCCESSOR = "condition/epsilon"

#: A third subject whose family numbers its titles BACKWARDS against the
#: order its keys sort in.  Every other family here numbers them the same way
#: round, which makes an order taken from the position token and an order
#: taken from the identity the same list — and a read that took the mutable
#: token for the order indistinguishable from one that took the key.
INVERTED_SUBJECT = "subject/inverted"
INVERTED_FIRST = "condition/kappa"
INVERTED_SECOND = "condition/lambda"
INVERTED_THIRD = "condition/mu"
INVERTED_FAMILY = (INVERTED_FIRST, INVERTED_SECOND, INVERTED_THIRD)

EVIDENCE = "**Evidence:** sha abc123 · independent verification"
SUBJECT_BODY = "**Outcome:** Three checks, numbered in their titles.\n\nNot a draft."

#: The token a title carries and the key never does.
TOKENS = {FIRST: "AC-1", SECOND: "AC-2", THIRD: "AC-3"}
#: The same, running the other way round its family's keys.
INVERTED_TOKENS = dict(zip(INVERTED_FAMILY, ("AC-3", "AC-2", "AC-1"), strict=True))
#: Every title-carried position in this module, so one title helper serves
#: both families and neither can be numbered by accident.
POSITIONS = {**TOKENS, **INVERTED_TOKENS}

GRADED_SHA = "a" * 40
QUESTION = "Which of the two readings of this check applies?"
REPO_URL = "https://example.invalid/fixture-owner/fixture-repo"


def title_for(key: str) -> str:
    return f"{POSITIONS[key]} — the check {key} states"


def check_of(key: str) -> str:
    """The Check this sub-issue's own body carries, as a roster reads it."""
    return f"the behaviour {key} names"


def criterion_issue(key: str, *, parent: str = SUBJECT, **changes) -> FakeMcpIssue:
    fields: dict[str, object] = {
        "id": key,
        "title": title_for(key) if key in POSITIONS else f"the check {key} states",
        "parent_id": parent,
        "labels": [LABEL],
        # Unstarted, which is the state a criterion awaiting its first
        # verdict is in: a verdict is written from that state or from the
        # finished one, and a family in neither could take none at all.
        "status": "Todo",
        "status_type": "unstarted",
        "description": f"**Check:** {check_of(key)}\n\n{EVIDENCE}",
    }
    fields.update(changes)
    return FakeMcpIssue(**fields)


@pytest.fixture
def server():
    server = fixture_server()
    issues = [
        FakeMcpIssue(
            id=SUBJECT,
            labels=FIRE_ENTRY_LABELS,
            description=SUBJECT_BODY,
            updated_at=FIXTURE_NOW,
        ),
        criterion_issue(FIRST),
        criterion_issue(SECOND),
        criterion_issue(THIRD),
        FakeMcpIssue(
            id=ABSORBING_SUBJECT,
            labels=FIRE_ENTRY_LABELS,
            description=SUBJECT_BODY,
            updated_at=FIXTURE_NOW,
        ),
        # Closed as a duplicate of its successor, which is how a board says
        # one condition was absorbed by another.  Spelled in vendor shape so
        # the relations object refuses any arm the vendor has not got.
        criterion_issue(
            SUPERSEDED,
            parent=ABSORBING_SUBJECT,
            status="Duplicate",
            status_type="duplicate",
            relations=[("duplicateOf", SUCCESSOR)],
        ),
        criterion_issue(SUCCESSOR, parent=ABSORBING_SUBJECT),
        FakeMcpIssue(
            id=INVERTED_SUBJECT,
            labels=FIRE_ENTRY_LABELS,
            description=SUBJECT_BODY,
            updated_at=FIXTURE_NOW,
        ),
        *(criterion_issue(key, parent=INVERTED_SUBJECT) for key in INVERTED_FAMILY),
    ]
    server.issues.update({issue.id: issue for issue in issues})
    return server


def retire(tracker, server, key: str) -> None:
    """The board takes one criterion out of its family.

    Not a write by any code under test: the double's own issue table and the
    vendor server's own issue are moved directly, so a mutation observation
    stays equal across it.
    """
    if isinstance(tracker, FakeTrackerPort):
        tracker.issues[key] = tracker.issues[key].model_copy(
            update={"issue_labels": frozenset()}
        )
    else:
        server.issues[key].labels = []


def renumber(tracker, server, key: str, title: str) -> None:
    """The board rewrites the position token in one criterion's title."""
    if isinstance(tracker, FakeTrackerPort):
        tracker.issues[key] = tracker.issues[key].model_copy(update={"title": title})
    else:
        server.issues[key].title = title


def lane_writer(tracker) -> TrackerLaneStateWriter:
    """The component that puts a verdict on the sub-issue its key names.

    Only the tracker is under test: the verdict write reads no repository and
    composes no forge address, so the git service is a stub and there is no
    forge to ask.
    """
    return TrackerLaneStateWriter(
        tracker=tracker,
        operation=lane_operation(),
        git=FakeGitService(),
        git_remote="origin",
        forge=None,
        gate=PassThroughGate(),
    )


def lane_of(subject: str) -> LaneBinding:
    """The lane whose own work a verdict on *subject*'s criteria comes from."""
    return LaneBinding(
        lane_key=subject,
        loop_branch=f"loop/{subject}",
        deliverable_branch=f"deliverable/{subject}",
        base=trunk_base("trunk"),
        body_digest="f" * 64,
        repo_url=REPO_URL,
        repo_path=None,
        run_id="fixture-run",
        visibility=RepoVisibility.PRIVATE,
    )


def verdict_on(key: str) -> CriterionCrossOff:
    """One passing verdict, keyed by the criterion it addresses."""
    return CriterionCrossOff(
        criterion=key,
        state=CrossOffState.passed,
        evidence=CriterionEvidence(graded_sha=GRADED_SHA, test="the case above"),
    )


async def test_a_removed_criterion_and_a_renumbered_remainder_move_no_identity(
    tracker, tracker_writes, server
):
    """One condition leaves, the remainder's token is rewritten, no key moves."""
    before = tracker_writes()
    family = tuple(await tracker.read_criteria(issue_key=SUBJECT))
    assert [row.issue_key for row in family] == [FIRST, SECOND, THIRD]
    assert [row.title for row in family] == [title_for(key) for key in TOKENS]
    third_before = family[2]

    retire(tracker, server, SECOND)
    renumber(
        tracker,
        server,
        THIRD,
        title=title_for(THIRD).replace(TOKENS[THIRD], TOKENS[SECOND]),
    )

    # Nothing compacted, reordered or reused: the two survivors are the same
    # two strings in the same relative order, and neither took the value of
    # the key that left.
    surviving = tuple(await tracker.read_criteria(issue_key=SUBJECT))
    assert [row.issue_key for row in surviving] == [FIRST, THIRD]
    assert SECOND not in {row.issue_key for row in surviving}
    # The composed specification mints identity from the key and from nothing
    # else.  The composition moved to the stage that is its one caller, so it is
    # reached here through the domain composer over the family read above.
    spec = tracker_spec_from_issues(
        subject=await tracker.read_issue(issue_key=SUBJECT), criteria=surviving
    )
    assert spec.criteria == (FIRST, THIRD)
    # The retired identity resolves to nothing, naming both identities — the
    # neighbour that took its token never answers for it.
    with pytest.raises(CriterionResolutionError, match="0 current") as raised:
        await NativeCriterionResolver(tracker=tracker).resolve_criterion(
            issue_key=SUBJECT, criterion_key=SECOND
        )
    assert raised.value.issue_key == SUBJECT and raised.value.criterion_key == SECOND
    assert SUBJECT in str(raised.value) and SECOND in str(raised.value)
    # And the renumbered neighbour resolves to its own row, under its own
    # unchanged key, with the new title and its full source.
    third_after = await NativeCriterionResolver(tracker=tracker).resolve_criterion(
        issue_key=SUBJECT, criterion_key=THIRD
    )
    assert third_after.issue_key == THIRD == third_before.issue_key
    assert third_after.title == title_for(THIRD).replace(TOKENS[THIRD], TOKENS[SECOND])
    assert TOKENS[SECOND] in third_after.title and TOKENS[SECOND] not in THIRD
    assert third_after.body == third_before.body and third_after.body.endswith(EVIDENCE)
    # The token is not free to move under an amendment either.
    with pytest.raises(CriterionReadError, match="facts changed before amendment"):
        require_criterion_source(expected=third_before, current=third_after)
    assert tracker_writes() == before


async def test_the_family_is_ordered_by_its_keys_and_not_by_the_position_token(
    tracker, tracker_writes, server
):
    """Over a family numbered backwards, the two possible orders differ.

    The relative order the case above asserts is only a reading of identity
    while the order the keys sort in and the order the tokens count in are
    different lists. On the three-child family they are the same list, by the
    coincidence that fixture is built on. Here they are opposites, so the read
    names which of the two it is — and the rewrite that follows shows the
    order surviving a token the board moved, which is what it is for.
    """
    before = tracker_writes()

    family = tuple(await tracker.read_criteria(issue_key=INVERTED_SUBJECT))

    assert [row.issue_key for row in family] == list(INVERTED_FAMILY)
    assert [row.title for row in family] == [title_for(key) for key in INVERTED_FAMILY]
    # The fixture's own property, stated so it cannot be lost: sorted by
    # title these same three rows come back in exactly the opposite order.
    assert sorted(row.title for row in family) == [
        title_for(key) for key in reversed(INVERTED_FAMILY)
    ]
    # The composed specification reports the same order, from the keys and
    # nothing else.
    spec = tracker_spec_from_issues(
        subject=await tracker.read_issue(issue_key=INVERTED_SUBJECT), criteria=family
    )
    assert spec.criteria == INVERTED_FAMILY
    # The same removal and rewrite as the case above, over this family: the
    # last condition leaves and the middle one takes the number it vacated.
    retire(tracker, server, INVERTED_THIRD)
    renumber(
        tracker,
        server,
        INVERTED_SECOND,
        title=title_for(INVERTED_SECOND).replace(
            INVERTED_TOKENS[INVERTED_SECOND], INVERTED_TOKENS[INVERTED_THIRD]
        ),
    )
    surviving = tuple(await tracker.read_criteria(issue_key=INVERTED_SUBJECT))

    # The two survivors in the order their keys sort, which is still the
    # opposite of the order their rewritten titles count in.
    assert [row.issue_key for row in surviving] == [INVERTED_FIRST, INVERTED_SECOND]
    assert sorted(row.title for row in surviving) == [
        row.title for row in reversed(surviving)
    ]
    assert tracker_spec_from_issues(
        subject=await tracker.read_issue(issue_key=INVERTED_SUBJECT),
        criteria=surviving,
    ).criteria == (INVERTED_FIRST, INVERTED_SECOND)
    assert tracker_writes() == before


async def test_a_verdict_a_designation_and_an_audit_request_resolve_through_the_key(
    tracker, tracker_writes, server
):
    """The three referents keyed by identity, re-resolved after the rewrite."""
    before = tracker_writes()
    family = tuple(await tracker.read_criteria(issue_key=SUBJECT))
    designation_before = RulingProtectedTestRef(
        source_ref=mint_ruling_id(issue_ref=THIRD, question=QUESTION),
        path="tests/tracker/test_criterion_renumbering.py",
        qualified_name="test_a_removed_criterion_and_a_renumbered_remainder_move_no_identity",
    )

    retire(tracker, server, SECOND)
    renumber(
        tracker,
        server,
        THIRD,
        title=title_for(THIRD).replace(TOKENS[THIRD], TOKENS[SECOND]),
    )
    survivor = await NativeCriterionResolver(tracker=tracker).resolve_criterion(
        issue_key=SUBJECT, criterion_key=THIRD
    )

    # A recorded verdict, resolved the way the lane's state writer resolves
    # one: by reading the issue the identity names.
    cross_off = CriterionCrossOff(
        criterion=THIRD,
        state=CrossOffState.passed,
        evidence=CriterionEvidence(graded_sha=GRADED_SHA, test="the case above"),
    )
    assert await tracker.read_issue(issue_key=cross_off.criterion) == survivor
    # A designation: the identity minted before the rewrite is the identity
    # minted after it, and the key is a member of the set an answer may
    # address, composed from the family as it now reads.
    assert designation_before.source_ref == mint_ruling_id(
        issue_ref=THIRD, question=QUESTION
    )
    fresh = await tracker.read_criteria(issue_key=SUBJECT)
    assert THIRD in addressable_issues(
        subject=SUBJECT, criteria=(row.issue_key for row in fresh)
    )
    # An audit request, resolved through the shared native resolver.
    request = AuditForgeRequest(
        criterion_key=THIRD, lane_issue_key=SUBJECT, repo_url=REPO_URL
    )
    assert (
        await NativeCriterionResolver(tracker=tracker).resolve_criterion(
            issue_key=request.lane_issue_key,
            criterion_key=request.criterion_key,
        )
        == survivor
    )
    # Each of the three keyed by the retired identity answers with nothing
    # rather than with the neighbour that took its token.
    retired_verdict = cross_off.model_copy(update={"criterion": SECOND})
    assert await tracker.read_issue(issue_key=retired_verdict.criterion) != survivor
    retired_designation = mint_ruling_id(issue_ref=SECOND, question=QUESTION)
    assert retired_designation != designation_before.source_ref
    assert SECOND not in addressable_issues(
        subject=SUBJECT, criteria=(row.issue_key for row in fresh)
    )
    retired_request = request.model_copy(update={"criterion_key": SECOND})
    with pytest.raises(CriterionResolutionError, match="0 current"):
        await NativeCriterionResolver(tracker=tracker).resolve_criterion(
            issue_key=retired_request.lane_issue_key,
            criterion_key=retired_request.criterion_key,
        )
    # Non-vacuous: the family the survivor was read from held three members.
    assert len(family) == 3
    assert tracker_writes() == before


async def test_a_verdict_is_written_onto_the_sub_issue_its_own_key_names(
    tracker, server
):
    """The tie between a verdict in hand and its sub-issue, after a rewrite.

    Made by the component that makes it in production rather than by a read
    this fixture performs for itself: a writer resolving the verdict by the
    criterion's position in the family would satisfy any statement the
    fixture made on its own, and would put this verdict on the neighbour
    that took the retired number.
    """
    retire(tracker, server, SECOND)
    renumber(
        tracker,
        server,
        THIRD,
        title=title_for(THIRD).replace(TOKENS[THIRD], TOKENS[SECOND]),
    )
    writer = lane_writer(tracker)

    await writer.write_cross_offs(
        lane=lane_of(SUBJECT),
        dispatched=(TrackerCriterion(id=THIRD, text=check_of(THIRD)),),
        cross_offs=(verdict_on(THIRD),),
    )

    # Finished, at the sha it was graded at, on the row its key names.
    ticked = await tracker.read_issue(issue_key=THIRD)
    assert ticked.state_kind is WorkflowStateKind.COMPLETED
    assert parse_criterion_evidence(ticked.body).graded_sha == GRADED_SHA
    # And on no other row of the family: the one that kept its own token is
    # untouched, and so is the one whose number this row took.
    for untouched in (FIRST, SECOND):
        other = await tracker.read_issue(issue_key=untouched)
        assert other.state_kind is not WorkflowStateKind.COMPLETED
        assert GRADED_SHA not in other.body and other.body.endswith(EVIDENCE)

    # The retired identity's verdict is refused rather than written onto the
    # neighbour that took its number.
    with pytest.raises(StaleWriteError) as raised:
        await writer.write_cross_offs(
            lane=lane_of(SUBJECT),
            dispatched=(TrackerCriterion(id=SECOND, text=check_of(SECOND)),),
            cross_offs=(verdict_on(SECOND),),
        )

    assert raised.value.target == SECOND
    survivor = await tracker.read_issue(issue_key=FIRST)
    assert survivor.state_kind is not WorkflowStateKind.COMPLETED
    assert GRADED_SHA not in survivor.body


async def test_a_superseded_identity_stays_resolvable_and_names_what_absorbed_it(
    tracker, tracker_writes
):
    """Absorbed by a successor, an identity is still a name for what it named."""
    before = tracker_writes()

    family = tuple(await tracker.read_criteria(issue_key=ABSORBING_SUBJECT))

    # The identity did not stop resolving when it was absorbed.
    assert [row.issue_key for row in family] == [SUPERSEDED, SUCCESSOR]
    assert SUPERSEDED in addressable_issues(
        subject=ABSORBING_SUBJECT, criteria=(row.issue_key for row in family)
    )
    superseded = await NativeCriterionResolver(tracker=tracker).resolve_criterion(
        issue_key=ABSORBING_SUBJECT,
        criterion_key=SUPERSEDED,
    )
    # It resolves to its own row, in its own state, with its own full source.
    assert superseded.issue_key == SUPERSEDED
    assert superseded.state_kind is WorkflowStateKind.DUPLICATE
    assert superseded.body.endswith(EVIDENCE)
    # What absorbed it is readable off that same row, as a tracker fact.
    assert [
        relation.issue_key
        for relation in superseded.relations
        if relation.kind is IssueRelationKind.DUPLICATE
    ] == [SUCCESSOR]
    # And nothing rebinds: the identity is not the successor's row.
    successor = await NativeCriterionResolver(tracker=tracker).resolve_criterion(
        issue_key=ABSORBING_SUBJECT,
        criterion_key=SUCCESSOR,
    )
    assert superseded != successor and successor.issue_key == SUCCESSOR
    assert tracker_writes() == before


def test_the_authored_arm_keeps_enumerating_from_one() -> None:
    """The authored ids are positions within one round and are not bound here.

    Placed beside the native cases because what it records is the boundary
    between the two arms: `GeneratedCriteriaOutput` states this outright, and
    this asserts it rather than contradicting it.
    """
    first_round = mint_criteria(
        [DraftedCriterion(text=f"round one check {index}") for index in range(1, 4)]
    )
    second_round = mint_criteria(
        [DraftedCriterion(text=f"round two check {index}") for index in range(1, 4)]
    )

    assert [criterion.id for criterion in first_round] == ["AC-1", "AC-2", "AC-3"]
    # Round two's third id is the same string, and none of round one's text is
    # under it: the id is the position in the round, not a lasting identity.
    assert second_round[2].id == "AC-3" == first_round[2].id
    assert second_round[2].text == "round two check 3"
    assert all(criterion.text != second_round[2].text for criterion in first_round)
    assert mint_criterion_id(1) == "AC-1"
