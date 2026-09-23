"""The tracker spec read captures a subject or refuses before loop construction."""

from datetime import timedelta

import pytest

from kodezart.chains.criteria import TrackerCriteria
from kodezart.core.errors import TrackerUnavailableError
from kodezart.domain.errors import (
    CriterionReadError,
    EmptyFireCriteriaError,
    FireSpecEntryError,
    InvalidFireCriterionError,
)
from kodezart.domain.ticket import format_fire_spec
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.tracker import WorkflowStateKind, is_non_counting
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue, FakeTrackerPort
from tests.tracker.conftest import (
    FIRE_ENTRY_LABELS,
    FIXTURE_NOW,
    STATE_TYPES,
    fixture_server,
)
from tests.tracker.test_linear_mcp_tracker import tracker_over

SUBJECT = "subject/42"
CRITERION = "condition/alpha"
LABEL = "acceptance-condition"
BODY = "**Outcome:** Preserve this tracker-native text.\n\nIt is not a ticket draft."

#: A subject whose every criterion was abandoned, and the one criterion it
#: carries.  Its own subject so the wide-subtree cases keep reading SUBJECT.
ABANDONED_SUBJECT = "abandoned/subject"
ABANDONED_ONLY = "abandoned/only"

#: The pair a case adds to SUBJECT's subtree: a criterion carrying no Check
#: at all, and one that is Done and carries one.
ABANDONED = "abandoned/1"
GRADED = "graded/1"
NO_CHECK_BODY = "**Do:** no check"
GRADED_BODY = "**Check:** The delivered behavior is observable."


def criterion_row(key: str, *, status: str, description: str) -> FakeMcpIssue:
    """One criterion sub-issue of SUBJECT, in the vendor's own state pair."""
    return FakeMcpIssue(
        id=key,
        parent_id=SUBJECT,
        labels=[LABEL],
        description=description,
        status=status,
        status_type=STATE_TYPES[status],
    )


@pytest.fixture
def abandoned_pair(request: pytest.FixtureRequest) -> tuple[FakeMcpIssue, ...]:
    """The two criteria a case widens SUBJECT's subtree by, or none.

    Stated as a fixture the workspace consumes, indirectly parametrized by
    the state the abandoned one sits in, rather than as a second server
    builder: a case needing the wider subtree still runs against every
    registered implementation over the one ``tracker`` fixture, and the rows
    are built per case so no case can mutate another's board.
    """
    state: str | None = getattr(request, "param", None)
    if state is None:
        return ()
    return (
        criterion_row(ABANDONED, status=state, description=NO_CHECK_BODY),
        criterion_row(GRADED, status="Done", description=GRADED_BODY),
    )


@pytest.fixture
def server(abandoned_pair: tuple[FakeMcpIssue, ...]):
    server = fixture_server()
    issues = [
        FakeMcpIssue(
            id=SUBJECT,
            labels=FIRE_ENTRY_LABELS,
            description=BODY,
            updated_at=FIXTURE_NOW,
        ),
        # Both criteria are unstarted, which is what a criterion the subject
        # still owes sits in: the entry composes its spec out of the same
        # reading it selects that obligation from, so a subject whose criteria
        # were all in the backlog kind is one no fire can enter at all.
        FakeMcpIssue(
            id=CRITERION,
            parent_id=SUBJECT,
            labels=[LABEL],
            description="**Check:** The behavior is observable.\n\n**Evidence:** —",
            status="Todo",
            status_type=STATE_TYPES["Todo"],
        ),
        FakeMcpIssue(id="ordinary/1", parent_id=SUBJECT),
        FakeMcpIssue(
            id="grandchild/1",
            parent_id=CRITERION,
            labels=[LABEL],
            description="**Check:** A criterion under a criterion is in the subtree.",
            status="Todo",
            status_type=STATE_TYPES["Todo"],
        ),
        FakeMcpIssue(
            id="empty/1",
            labels=FIRE_ENTRY_LABELS,
            description="- [x] A parent checkbox is not a child.",
        ),
        FakeMcpIssue(
            id=ABANDONED_SUBJECT,
            labels=FIRE_ENTRY_LABELS,
            description=BODY,
            updated_at=FIXTURE_NOW,
        ),
        FakeMcpIssue(
            id=ABANDONED_ONLY,
            parent_id=ABANDONED_SUBJECT,
            labels=[LABEL],
            description=NO_CHECK_BODY,
            status="Canceled",
            status_type=STATE_TYPES["Canceled"],
        ),
    ]
    server.issues.update({issue.id: issue for issue in (*issues, *abandoned_pair)})
    return server


async def test_empty_spec_refuses_but_ordinary_criteria_read_stays_empty(tracker):
    assert tuple(await tracker.read_criteria(issue_key="empty/1")) == ()
    with pytest.raises(EmptyFireCriteriaError) as raised:
        await TrackerCriteria(tracker=tracker).read_entry(issue_key="empty/1")
    assert raised.value.issue_key == "empty/1"


@pytest.mark.parametrize(
    "abandoned_pair", ["Canceled", "Duplicate"], indirect=True, ids=str
)
async def test_an_abandoned_criterion_without_a_check_neither_joins_nor_refuses(
    tracker, server, tracker_writes
):
    """Abandoned work is dropped before the Check validation, not refused.

    A criterion the board Canceled or closed as a Duplicate counts for
    nothing, so it neither joins the composed specification nor refuses the
    read it is present in, however unreadable its own body is (KOD-794).
    """
    before = tracker_writes()

    spec, _ = await TrackerCriteria(tracker=tracker).read_entry(issue_key=SUBJECT)

    assert spec.criteria == (CRITERION, GRADED, "grandchild/1")
    assert ABANDONED not in spec.criteria
    assert tracker_writes() == before


#: Every state a criterion counts in and still owes work in, read off the
#: fixture's own state table so a state added there is covered here too.
COUNTING_OPEN_STATES: tuple[str, ...] = tuple(
    name
    for name, kind in STATE_TYPES.items()
    if not is_non_counting(WorkflowStateKind(kind))
    and WorkflowStateKind(kind) is not WorkflowStateKind.COMPLETED
)


@pytest.mark.parametrize("delivering", [False, True], ids=["entry", "delivering"])
@pytest.mark.parametrize("state", COUNTING_OPEN_STATES)
@pytest.mark.parametrize("abandoned_pair", ["Canceled"], indirect=True, ids=str)
async def test_a_counting_criterion_without_a_check_refuses_at_the_spec_read(
    tracker, server, tracker_writes, state, delivering
):
    """The Check validation is the spec read's own, in every counting state.

    The same body the abandoned case drops is moved into each state a
    criterion counts in.  Only the owed kind is read again one step deeper,
    so a refusal in any other state is the spec read's alone, and it names
    the subject and the criterion before anything is written.
    """
    assert COUNTING_OPEN_STATES
    if isinstance(tracker, FakeTrackerPort):
        tracker.issues[ABANDONED] = tracker.issues[ABANDONED].model_copy(
            update={"state_kind": WorkflowStateKind(STATE_TYPES[state])}
        )
    else:
        server.issues[ABANDONED].status = state
        server.issues[ABANDONED].status_type = STATE_TYPES[state]
    before = tracker_writes()

    with pytest.raises(InvalidFireCriterionError) as raised:
        await TrackerCriteria(tracker=tracker).read_entry(
            issue_key=SUBJECT, delivering=delivering
        )

    assert raised.value.issue_key == SUBJECT
    assert raised.value.criterion_key == ABANDONED
    assert tracker_writes() == before


async def test_a_subtree_of_abandoned_criteria_refuses_as_empty_and_writes_nothing(
    tracker, tracker_writes
):
    """Emptiness is judged over what counts, and it is the empty refusal.

    The subject's one criterion counts for nothing, so what is left to
    compose a specification from is nothing at all: the read refuses as an
    empty subtree rather than as an unreadable criterion, and it writes
    nothing on the way there.
    """
    before = tracker_writes()

    with pytest.raises(EmptyFireCriteriaError) as raised:
        await TrackerCriteria(tracker=tracker).read_entry(issue_key=ABANDONED_SUBJECT)

    assert raised.value.issue_key == ABANDONED_SUBJECT
    assert [
        criterion.issue_key
        for criterion in await tracker.read_criteria(issue_key=ABANDONED_SUBJECT)
    ] == [ABANDONED_ONLY]
    assert tracker_writes() == before


async def test_failed_subject_lookup_is_not_a_successful_empty_spec(tracker):
    with pytest.raises(CriterionReadError) as raised:
        await tracker.read_fire_subject(issue_key="missing/1")
    assert raised.value.issue_key == "missing/1"


async def test_spec_captures_opaque_child_keys_verbatim_body_and_subject_version(
    tracker, tracker_writes
):
    before = tracker_writes()
    spec, _ = await TrackerCriteria(tracker=tracker).read_entry(issue_key=SUBJECT)
    assert isinstance(spec, TrackerSpec)
    assert spec.subject == SUBJECT
    assert spec.body == BODY
    assert spec.criteria == (CRITERION, "grandchild/1")
    assert spec.read_at_version == FIXTURE_NOW.isoformat()
    assert format_fire_spec(spec) == BODY
    assert tracker_writes() == before


async def test_a_spec_read_hydrates_its_subject_exactly_once(tracker, server):
    if isinstance(tracker, FakeTrackerPort):
        tracker.issue_reads.clear()
    else:
        server.calls.clear()

    await tracker.read_fire_subject(issue_key=SUBJECT)

    if isinstance(tracker, FakeTrackerPort):
        assert tracker.issue_reads.count(SUBJECT) == 1
    else:
        assert [
            call for call in server.tool_calls("get_issue") if call["id"] == SUBJECT
        ] == [{"id": SUBJECT, "includeRelations": True}]


async def test_incomplete_child_listing_is_not_an_empty_spec():
    server = FakeLinearMcpServer(
        issues=[FakeMcpIssue(id=SUBJECT, labels=FIRE_ENTRY_LABELS)],
        tool_errors={"list_issues": "unavailable"},
    )
    with pytest.raises(FireSpecEntryError) as raised:
        await TrackerCriteria(tracker=tracker_over(server)).read_entry(
            issue_key=SUBJECT
        )
    assert isinstance(raised.value.__cause__, TrackerUnavailableError)
    assert not server.tool_calls("save_issue")


async def test_subject_changed_during_membership_read_does_not_mix_text_and_version():
    class ChangingParentServer(FakeLinearMcpServer):
        def _tool_list_issues(self, arguments):
            self.issues[SUBJECT].description = "A later amendment."
            self.issues[SUBJECT].updated_at = FIXTURE_NOW + timedelta(hours=1)
            return super()._tool_list_issues(arguments)

    server = ChangingParentServer(
        issues=[
            FakeMcpIssue(
                id=SUBJECT,
                labels=FIRE_ENTRY_LABELS,
                description=BODY,
                updated_at=FIXTURE_NOW,
            ),
            FakeMcpIssue(
                id=CRITERION,
                parent_id=SUBJECT,
                labels=[LABEL],
                description="**Check:** The behavior is observable.",
                status="Todo",
                status_type=STATE_TYPES["Todo"],
            ),
        ]
    )
    spec, _ = await TrackerCriteria(tracker=tracker_over(server)).read_entry(
        issue_key=SUBJECT
    )
    assert spec.body == BODY
    assert spec.read_at_version == FIXTURE_NOW.isoformat()
    assert server.issues[SUBJECT].description == "A later amendment."


@pytest.mark.parametrize(
    "body",
    [
        "",
        "The criterion title and prose cannot substitute for its Check field.",
        "**Check:**\n\n**Do:** Build it.\n\n**Evidence:** sha + test",
        "**Check:**  \n\t\n**Class:** hard",
        "```markdown\n**Check:** Example only.\n```",
        "~~~~\n**Check:** Example only.\n~~~~",
        "<!--\n**Check:** Hidden example.\n-->",
        "**Check:** <!-- TODO: author the Check -->\n**Do:** Implement later.",
        "**Check:** <!--\n**Do:** ignored -->\n**Evidence:** —",
        "> **Check:** A quoted example.",
        "    **Check:** An indented code example.",
        "**Check:** First.\n\n**Check:** A conflicting second field.",
    ],
)
async def test_missing_empty_or_ambiguous_check_refuses_at_the_spec_read(
    tracker, server, tracker_writes, body
):
    if isinstance(tracker, FakeTrackerPort):
        tracker.issues[CRITERION] = tracker.issues[CRITERION].model_copy(
            update={"body": body}
        )
    else:
        server.issues[CRITERION].description = body
    before = tracker_writes()
    with pytest.raises(InvalidFireCriterionError) as raised:
        await TrackerCriteria(tracker=tracker).read_entry(issue_key=SUBJECT)
    assert raised.value.issue_key == SUBJECT
    assert raised.value.criterion_key == CRITERION
    assert tracker_writes() == before
    assert [
        criterion.issue_key
        for criterion in await tracker.read_criteria(issue_key=SUBJECT)
    ] == [CRITERION]


@pytest.mark.parametrize(
    "body",
    [
        "**Check:** A behavior can be checked.",
        "**Check:**\nA behavior can be checked.\n\nAnother paragraph.\n\n**Do:** Act.",
        "**Do:** Act.\n\n**Check:** A behavior can be checked.\n\n**Evidence:** —",
        "<!-- persisted-identity -->\n\n**Check:** A behavior can be checked.",
        "```\n**Check:** Example only.\n```\n\n**Check:** The actual behavior.",
        "**Check:** Preserve this example.\n```\n**Check:** nested example\n```",
        "**Check:** Keep <!-- private annotation --> visible behavior.",
        "**Check:** <!--\n**Do:** ignored --> Visible behavior.\n\n**Evidence:** —",
    ],
)
async def test_check_content_is_read_without_rewriting_criterion_or_parent(
    tracker, server, tracker_writes, body
):
    if isinstance(tracker, FakeTrackerPort):
        tracker.issues[CRITERION] = tracker.issues[CRITERION].model_copy(
            update={"body": body}
        )
    else:
        server.issues[CRITERION].description = body
    before = tracker_writes()
    spec, _ = await TrackerCriteria(tracker=tracker).read_entry(issue_key=SUBJECT)
    assert spec.criteria == (CRITERION, "grandchild/1")
    assert spec.body == BODY
    assert (await tracker.read_issue(issue_key=CRITERION)).body == body
    assert tracker_writes() == before


async def test_unknown_backend_state_refuses_at_spec_read_without_guessing_by_name():
    server = FakeLinearMcpServer(
        issues=[
            FakeMcpIssue(id=SUBJECT, labels=FIRE_ENTRY_LABELS, description=BODY),
            FakeMcpIssue(
                id=CRITERION,
                parent_id=SUBJECT,
                labels=[LABEL],
                description="**Check:** The behavior can be checked.",
                status="Done",
                status_type="unknown-backend-state",
            ),
        ]
    )
    with pytest.raises(CriterionReadError) as raised:
        await TrackerCriteria(tracker=tracker_over(server)).read_entry(
            issue_key=SUBJECT
        )
    assert raised.value.__cause__ is not None
    assert "no domain mapping" in str(raised.value.__cause__)
    assert not server.tool_calls("save_issue")
