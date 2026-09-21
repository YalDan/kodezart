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
from kodezart.domain.errors import CriterionReadError, CriterionResolutionError
from kodezart.domain.rulings import addressable_issues
from kodezart.services.criterion_sources import resolve_criterion
from kodezart.types.domain.agent import RulingProtectedTestRef
from kodezart.types.domain.audit_forge import AuditForgeRequest
from kodezart.types.domain.criteria import DraftedCriterion
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_lifecycle import CriterionCrossOff, CrossOffState
from kodezart.types.domain.tracker import IssueRelationKind, WorkflowStateKind
from tests.fakes import FakeMcpIssue, FakeTrackerPort
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

EVIDENCE = "**Evidence:** sha abc123 · independent verification"
SUBJECT_BODY = "**Outcome:** Three checks, numbered in their titles.\n\nNot a draft."

#: The token a title carries and the key never does.
TOKENS = {FIRST: "AC-1", SECOND: "AC-2", THIRD: "AC-3"}

GRADED_SHA = "a" * 40
QUESTION = "Which of the two readings of this check applies?"
REPO_URL = "https://example.invalid/fixture-owner/fixture-repo"


def title_for(key: str) -> str:
    return f"{TOKENS[key]} — the check {key} states"


def criterion_issue(key: str, *, parent: str = SUBJECT, **changes) -> FakeMcpIssue:
    fields: dict[str, object] = {
        "id": key,
        "title": title_for(key) if key in TOKENS else f"the check {key} states",
        "parent_id": parent,
        "labels": [LABEL],
        "description": f"**Check:** the behaviour {key} names\n\n{EVIDENCE}",
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
    # The spec read mints identity from the key and from nothing else.
    spec = await tracker.read_fire_spec(issue_key=SUBJECT)
    assert spec.criteria == (FIRST, THIRD)
    # The retired identity resolves to nothing, naming both identities — the
    # neighbour that took its token never answers for it.
    with pytest.raises(CriterionResolutionError, match="0 current") as raised:
        await resolve_criterion(
            tracker=tracker, issue_key=SUBJECT, criterion_key=SECOND
        )
    assert raised.value.issue_key == SUBJECT and raised.value.criterion_key == SECOND
    assert SUBJECT in str(raised.value) and SECOND in str(raised.value)
    # And the renumbered neighbour resolves to its own row, under its own
    # unchanged key, with the new title and its full source.
    third_after = await resolve_criterion(
        tracker=tracker, issue_key=SUBJECT, criterion_key=THIRD
    )
    assert third_after.issue_key == THIRD == third_before.issue_key
    assert third_after.title == title_for(THIRD).replace(TOKENS[THIRD], TOKENS[SECOND])
    assert TOKENS[SECOND] in third_after.title and TOKENS[SECOND] not in THIRD
    assert third_after.body == third_before.body and third_after.body.endswith(EVIDENCE)
    # The token is not free to move under an amendment either.
    with pytest.raises(CriterionReadError, match="facts changed before amendment"):
        require_criterion_source(expected=third_before, current=third_after)
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
    survivor = await resolve_criterion(
        tracker=tracker, issue_key=SUBJECT, criterion_key=THIRD
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
        await resolve_criterion(
            tracker=tracker,
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
        await resolve_criterion(
            tracker=tracker,
            issue_key=retired_request.lane_issue_key,
            criterion_key=retired_request.criterion_key,
        )
    # Non-vacuous: the family the survivor was read from held three members.
    assert len(family) == 3
    assert tracker_writes() == before


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
    superseded = await resolve_criterion(
        tracker=tracker,
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
    successor = await resolve_criterion(
        tracker=tracker,
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
