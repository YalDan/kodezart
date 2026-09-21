"""Two authored Checks become two criterion sub-issues, created before the marker.

The criteria stage's record is a label on the parent. That label is truthful
only if the children it speaks for already exist on the board, and only if the
admission that licensed it read them. This module runs the real run-stage owner
over a two-criteria fixture and reads one ordered log — every tracker call and
every session in the order the run issued them — so the facts the stage
promises are observable rather than inferred: one sub-issue per authored Check,
each resolvable alone, and every creation before the marker write.

A one-Check fixture cannot show either fact. With a single criterion, "one per
Check" and "at least one" are the same assertion, and an inexact Check
comparison between two similar texts never gets the chance to collapse them.
"""

import re

import pytest

from kodezart.chains.criteria import TrackerCriteria
from kodezart.domain.errors import EmptyFireCriteriaError
from kodezart.domain.fire_spec import criterion_check
from kodezart.types.domain.organize_owner import CriteriaProposal
from tests.chains.test_organize_owner import factory, run_owner
from tests.prompts.test_organize_mandate_bindings import declared_operation
from tests.tracker.conftest import CLAIMED_ISSUE
from tests.tracker.test_linear_mcp_tracker import tracker_over

# The two Checks this module authors. Two is the smallest roster that tells
# "one sub-issue per Check" apart from "a sub-issue", and both titles open on
# the same word, so an inexact Check comparison collapses them into one child.
AUTHORED_TITLES = ("Check prepared bytes", "Check recorded head")
# What the fixture executor makes of each title: `BoardExecutor.stream`
# authors `f"{title} match the declared source."`
# (tests/chains/test_organize_owner.py:107).
AUTHORED_CHECKS = frozenset(
    f"{title} match the declared source." for title in AUTHORED_TITLES
)
# The criteria mandate's terminal_marker_key is issue_labels.criteria, and that
# reference resolves to "criteria complete" in the declared fixture operation
# (tests/domain/test_organize.py:594, :606).
CRITERIA_MARKER = "criteria complete"
# The key side of that same reference, which the factory hands the adapter as
# its criteria-stage label key (tests/chains/test_organize_owner.py:184).
CRITERIA_STAGE_KEY = "criteria"
# issue_labels.criterion, the classification every criterion child carries
# (tests/domain/test_organize.py:590); `existing_criterion` refuses a child
# without it (domain/criterion_creation.py:38-43).
CRITERION_KEY = "criterion"
# The adapter writes both a criterion creation and a classification through
# this one MCP tool: a creation carries "parentId" and no "id"
# (adapters/linear/tracker.py:1841-1851); a classification carries "id" and
# "addLabels" (:2444-2447). One log, two distinguishable shapes.
SAVE_ISSUE = "save_issue"
# The ORGANIZE_VERIFY template's opening sentence, which no other organize
# prompt carries (src/kodezart/prompts/sets/claude-opus/organize_verify.md:1).
VERIFY_OPENING = "Adversarially verify the current issue"
# The log entry this module adds for a session. Not an MCP tool name, so it
# never collides with a tracker call.
SESSION = "organize-session"
# The template addresses its subject in one tagged block
# (organize_verify.md:24), read the way the fixture executor reads it
# (tests/chains/test_organize_owner.py:91).
SUBJECT_TAG = re.compile(r"<issue_key>(.*?)</issue_key>")


class StagedRun:
    """One two-criteria scope run and the ordered log it issues.

    *calls* interleaves the board's MCP calls with this run's sessions, so one
    index orders a creation against a marker write, and a marker write against
    the admission that came before it.
    """

    def __init__(self) -> None:
        self.owner, self.board, self.executor = factory(
            under_approval=True, criteria=AUTHORED_TITLES
        )
        self.authored: list[CriteriaProposal] = []
        original = self.executor.stream

        def stream(**kwargs: object):
            async def events():
                self.board.calls.append((SESSION, dict(kwargs)))
                async for event in original(**kwargs):
                    payload = event.structured_output
                    if payload is not None and payload.get("kind") == "criteria":
                        self.authored.append(CriteriaProposal.model_validate(payload))
                    yield event

            return events()

        self.executor.stream = stream

    @property
    def calls(self):
        return self.board.calls

    def reader(self):
        """A second adapter over the same board, kept off the run's call log.

        A read a test issues is not part of what the run did, so it goes to the
        fake server directly rather than through *board*.
        """
        operation = declared_operation()
        return tracker_over(
            self.board.server,
            caller=self.board.server,
            clock=lambda: self.board.now,
            issue_labels=operation.issue_labels,
            scope_labels=operation.scope_labels,
            criteria_stage_label_key=CRITERIA_STAGE_KEY,
        )

    def children(self):
        """Every board issue parented to the scope subject."""
        return [
            issue
            for issue in self.board.server.issues.values()
            if issue.parent_id == CLAIMED_ISSUE
        ]

    def authored_checks(self) -> list[str]:
        """Every Check the stage's own sessions proposed, in order."""
        return [item.check for proposal in self.authored for item in proposal.criteria]

    def creation_indices(self) -> list[int]:
        """Every criterion creation, by its position in the run's log."""
        return [
            index
            for index, (name, arguments) in enumerate(self.calls)
            if name == SAVE_ISSUE
            and arguments.get("parentId") == CLAIMED_ISSUE
            and "id" not in arguments
        ]

    def marker_indices(self) -> list[int]:
        """Every write recording the criteria stage on the scope subject."""
        return [
            index
            for index, (name, arguments) in enumerate(self.calls)
            if name == SAVE_ISSUE
            and arguments.get("id") == CLAIMED_ISSUE
            and arguments.get("addLabels") == [CRITERIA_MARKER]
        ]

    def verify_indices(self) -> list[int]:
        """Every verify session addressing the scope subject, by position."""
        return [
            index
            for index, (name, arguments) in enumerate(self.calls)
            if name == SESSION
            and VERIFY_OPENING in arguments["prompt"]
            and SUBJECT_TAG.findall(arguments["prompt"])[-1] == CLAIMED_ISSUE
        ]


@pytest.fixture
async def staged():
    """The run itself, completed, with both run stages behind it."""
    run = StagedRun()
    report = await run_owner(run.owner)
    assert report.halt is None
    assert [phase.value for phase in report.completed_phases] == ["ticket", "criteria"]
    return run


async def test_every_authored_check_becomes_exactly_one_sub_issue(staged):
    """The set of Checks on the board equals the set the stage authored.

    Both sides are read, neither counted against the other: the authored side
    from the criteria proposal the session returned, the landed side from the
    children's own bodies through the production Check reader. The authored
    side is then held to the two Checks this module names, so a stage that
    quietly authors one Check cannot satisfy the equality.
    """
    proposed = staged.authored_checks()
    assert set(proposed) == AUTHORED_CHECKS
    assert len(proposed) == len(AUTHORED_CHECKS)
    children = await staged.reader().read_criteria(issue_key=CLAIMED_ISSUE)
    landed = [
        criterion_check(criterion=child, issue_key=CLAIMED_ISSUE) for child in children
    ]
    assert set(landed) == AUTHORED_CHECKS
    assert len(landed) == len(AUTHORED_CHECKS)


async def test_each_created_criterion_sub_issue_resolves_by_its_own_key(staged):
    """Each child is addressable alone and is a member of the parent's spec."""
    reader = staged.reader()
    keys = sorted(child.id for child in staged.children())
    assert len(keys) == len(AUTHORED_CHECKS)
    for key in keys:
        resolved = await reader.read_issue(issue_key=key)
        assert resolved.issue_key == key
        assert resolved.parent_key == CLAIMED_ISSUE
        assert CRITERION_KEY in resolved.issue_labels
    spec = await TrackerCriteria(tracker=reader).read_spec(issue_key=CLAIMED_ISSUE)
    assert sorted(spec.criteria) == keys
    assert spec.subject == CLAIMED_ISSUE


async def test_every_criterion_sub_issue_is_created_before_the_stage_marker(staged):
    """No creation follows the write that records the stage as complete."""
    creations = staged.creation_indices()
    markers = staged.marker_indices()
    assert len(creations) == len(AUTHORED_CHECKS)
    assert len(markers) == 1
    assert max(creations) < min(markers)


async def test_the_marker_follows_the_stage_two_admission_that_verified_the_criteria(
    staged,
):
    """The last verify of the subject sits between the creations and the marker.

    The admission that licenses the marker therefore read a board that already
    carried both children — its prompt quotes both Checks — and the marker
    records that reading rather than preceding it.
    """
    creations = staged.creation_indices()
    verifies = staged.verify_indices()
    markers = staged.marker_indices()
    assert verifies
    assert max(creations) < max(verifies) < min(markers)
    verified = staged.calls[max(verifies)][1]["prompt"]
    for check in sorted(AUTHORED_CHECKS):
        assert check in verified


async def test_stage_marker_without_criterion_sub_issues_refuses_admission(staged):
    """A marker the children no longer back cannot open the fire read.

    The subject here is one the stage itself produced and labelled, not a
    hand-built issue: the marker and the approval are both real, so the
    refusal comes from the empty family alone.
    """
    parent = staged.board.server.issues[CLAIMED_ISSUE]
    assert CRITERIA_MARKER in parent.labels
    for child in staged.children():
        del staged.board.server.issues[child.id]
    reader = staged.reader()
    assert tuple(await reader.read_criteria(issue_key=CLAIMED_ISSUE)) == ()
    with pytest.raises(EmptyFireCriteriaError) as caught:
        await TrackerCriteria(tracker=reader).read_spec(issue_key=CLAIMED_ISSUE)
    assert caught.value.issue_key == CLAIMED_ISSUE
