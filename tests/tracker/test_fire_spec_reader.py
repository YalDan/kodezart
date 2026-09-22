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


@pytest.fixture
def server():
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
        # Depth three, reachable through two PLAIN owners and so only by the
        # recursive walk: a reading that listed the subject's direct children
        # and then asked each of them for its own criteria would find the
        # criterion under a criterion above, but never this one (KOD-710).
        FakeMcpIssue(id="nested-owner/1", parent_id="ordinary/1"),
        FakeMcpIssue(
            id="great-grandchild/1",
            parent_id="nested-owner/1",
            labels=[LABEL],
            description="**Check:** A criterion three deep is in the subtree.",
            status="Todo",
            status_type=STATE_TYPES["Todo"],
        ),
        FakeMcpIssue(
            id="empty/1",
            labels=FIRE_ENTRY_LABELS,
            description="- [x] A parent checkbox is not a child.",
        ),
    ]
    server.issues.update({issue.id: issue for issue in issues})
    return server


async def test_empty_spec_refuses_but_ordinary_criteria_read_stays_empty(tracker):
    assert tuple(await tracker.read_criteria(issue_key="empty/1")) == ()
    with pytest.raises(EmptyFireCriteriaError) as raised:
        await TrackerCriteria(tracker=tracker).read_entry(issue_key="empty/1")
    assert raised.value.issue_key == "empty/1"


async def test_failed_subject_lookup_is_not_a_successful_empty_spec(tracker):
    with pytest.raises(CriterionReadError) as raised:
        await tracker.read_fire_subject(issue_key="missing/1")
    assert raised.value.issue_key == "missing/1"


async def test_spec_captures_opaque_child_keys_verbatim_body_and_subject_version(
    tracker, tracker_writes
):
    """The subtree is read to its depth, and both halves of the entry name it.

    The deepest criterion sits under two plain owners, so it is reachable
    only by a walk that keeps descending: a reading that listed the
    subject's direct children and then asked each of them for its own
    criteria would compose a spec missing it, and the roster the loop
    starts on would be missing it too (KOD-710).
    """
    before = tracker_writes()
    spec, roster = await TrackerCriteria(tracker=tracker).read_entry(issue_key=SUBJECT)
    assert isinstance(spec, TrackerSpec)
    assert spec.subject == SUBJECT
    assert spec.body == BODY
    assert spec.criteria == (CRITERION, "grandchild/1", "great-grandchild/1")
    assert {criterion.id for criterion in roster.criteria} == set(spec.criteria)
    assert spec.read_at_version == FIXTURE_NOW.isoformat()
    assert format_fire_spec(spec) == BODY
    assert tracker_writes() == before


async def test_the_fire_spec_criterion_read_acquires_no_write_lease(
    tracker, tracker_writes, server
):
    """Fire entry reads the family; the grant belongs to whoever moves it.

    The criterion sub-issue is a leased write surface, so a spec read that
    took its grant would hold the surface against the state move entitled
    to it for the whole of the run the spec opens.
    """
    writes = tracker_writes()
    spec, _ = await TrackerCriteria(tracker=tracker).read_entry(issue_key=SUBJECT)
    assert spec.criteria == (CRITERION, "grandchild/1")
    assert tracker_writes() == writes
    if isinstance(tracker, FakeTrackerPort):
        assert tracker.lease_acquisitions == []
        assert tracker.leases == {}
    else:
        assert server.tool_calls("save_comment") == []


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
    assert spec.criteria == (CRITERION, "grandchild/1", "great-grandchild/1")
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
