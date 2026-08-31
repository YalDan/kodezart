"""The deterministic pre-query, over the in-process fake tracker.

The gate's whole claim is that it costs nothing: one port call, no prompt,
no session, no model.  Two of the tests below check that claim structurally
— the module's imports and the gate's own collaborators — because a claim
about cost that is only asserted behaviourally survives someone adding an
executor to the constructor.

Its second claim is that every question it asks is asked WITHIN a
container, and that one is checked structurally too: a signal added
without a scope would pass every behavioural case in this file by asking
about the whole workspace, and answer about a board this operation does
not own.
"""

import ast
import re
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import pytest
import structlog.testing

from kodezart.core.errors import McpTransportError, PassGateScopeError
from kodezart.services.pass_gate import PassGate
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.operation import OperationMemberAbsentError, QueueState
from kodezart.types.domain.tracker import (
    IssueQuery,
    ReviewQuery,
    TrackerIssue,
    TrackerReview,
)
from tests.fakes import (
    FIXTURE_EPOCH,
    FIXTURE_TEAM_KEY,
    FakeTrackerPort,
    make_tracker_issue,
    make_tracker_review,
)

GATE_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "kodezart"
    / "services"
    / "pass_gate.py"
)

PAGE_SIZE = 50
#: A page small enough for one board's activity to fill it whole.
CROWDED_PAGE = 2
LATER = FIXTURE_EPOCH + timedelta(hours=1)
LATEST = FIXTURE_EPOCH + timedelta(hours=2)

TEAM = FIXTURE_TEAM_KEY
OTHER_TEAM = "design"
REPO = "https://example.invalid/owner/primary"
#: A local bare repository — the sanctioned smoke origin, and one no forge
#: stands behind, so no review scan can name it.
FILE_ORIGIN = "file:///tmp/fixture-origin.git"

#: The two events one tick reports its outcome through.
_OUTCOMES = frozenset({"pass_gate_delta", "pass_gate_no_delta"})


def gate(tracker: FakeTrackerPort) -> PassGate:
    """The dispatch gate, now expressed as its one signal.

    Every behaviour test below is unchanged from when this was a hardcoded
    queue state.  That is the point of the generalisation: dispatch is one
    configuration of the general mechanism, not a rewrite of it, and this
    file passing untouched is what says so.
    """
    return PassGate(
        tracker=tracker,
        signals=[PassSignal.approved_changed],
        team_keys=[TEAM],
        repo_urls=[REPO],
        page_size=PAGE_SIZE,
    )


async def test_the_gate_reports_every_issue_that_moved() -> None:
    """A board with approved work yields a delta naming it."""
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue("FIX-1"), make_tracker_issue("FIX-2")],
    )
    delta = await gate(tracker).delta()

    assert delta.has_delta()
    assert set(delta.changed) == {"FIX-1", "FIX-2"}


async def test_a_quiet_board_yields_no_delta_at_all() -> None:
    """Nothing approved is nothing to wake for."""
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue("FIX-1", queue_states=[QueueState.TRIAGE])],
    )
    delta = await gate(tracker).delta()

    assert not delta.has_delta()
    assert delta.changed == ()


async def test_the_gate_asks_the_tracker_exactly_once_and_for_one_state() -> None:
    """AC-19: the pre-query is a port call — one, scoped, and parameterised."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue("FIX-1")])
    await gate(tracker).delta()

    assert len(tracker.scans) == 1
    query = tracker.scans[0]
    assert query.queue_state is QueueState.APPROVED
    assert query.team_key == TEAM
    assert query.page_size == PAGE_SIZE
    assert query.updated_since is None


async def test_the_gate_writes_nothing_while_deciding() -> None:
    """A gate that mutated the board would not be a gate."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue("FIX-1")])
    await gate(tracker).delta()

    assert tracker.claims == {}
    assert tracker.comments == []
    assert tracker.queue_writes == []
    assert tracker.workflow_writes == []


async def test_the_mark_advances_to_the_newest_thing_the_gate_saw() -> None:
    """The next tick asks from the high-water stamp, not from the epoch."""
    tracker = FakeTrackerPort(
        issues=[
            make_tracker_issue("FIX-1", created_at=LATER),
            make_tracker_issue("FIX-2", created_at=LATEST),
        ],
    )
    subject = gate(tracker)
    first = await subject.delta()
    assert first.mark == LATEST
    assert subject.mark(PassSignal.approved_changed, container=TEAM) == LATEST

    await subject.delta()
    assert tracker.scans[1].updated_since == LATEST


async def test_a_tick_that_saw_nothing_leaves_the_mark_where_it_was() -> None:
    """A missed window is re-read rather than skipped over."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue("FIX-1", created_at=LATER)])
    subject = gate(tracker)
    await subject.delta()
    tracker.issues.clear()

    quiet = await subject.delta()

    assert not quiet.has_delta()
    assert quiet.mark == LATER
    assert subject.mark(PassSignal.approved_changed, container=TEAM) == LATER
    assert tracker.scans[-1].updated_since == LATER


#: The method each cost-bearing port is recognised by: ``AgentExecutor``
#: streams, ``PromptProvider`` serves templates, ``ContentScanner`` scans.
#: A collaborator answering to any of them is a session waiting to happen.
_COST_BEARING_METHODS: tuple[str, ...] = (
    "stream",
    "template_for",
    "resolution_table",
    "scan",
)


def _could_reach_a_model(value: object) -> bool:
    return any(hasattr(value, name) for name in _COST_BEARING_METHODS)


class _Executorish:
    """Stands in for the collaborator the gate must never acquire."""

    def stream(self) -> None: ...


def test_the_cost_predicate_recognises_an_executor_shaped_collaborator() -> None:
    """Guards the test below: a predicate that never fires proves nothing."""
    assert _could_reach_a_model(_Executorish())


async def test_the_gate_holds_no_collaborator_that_could_reach_a_model() -> None:
    """AC-19: zero model involvement, asserted over the object, not the prose."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue("FIX-1")])
    subject = gate(tracker)

    assert [
        value for value in vars(subject).values() if _could_reach_a_model(value)
    ] == []


def test_the_gate_module_imports_nothing_that_could_reach_a_model() -> None:
    """A prompt, an executor or a skills selection here would be a cost."""
    tree = ast.parse(GATE_SOURCE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    assert not [
        name
        for name in imported
        if re.search(r"executor|prompt|agent|claude|skills", name)
    ]


def signal_gate(
    tracker: FakeTrackerPort,
    *signals: PassSignal,
    team_keys: Sequence[str] = (TEAM,),
    repo_urls: Sequence[str] = (REPO,),
    page_size: int = PAGE_SIZE,
) -> PassGate:
    return PassGate(
        tracker=tracker,
        signals=list(signals),
        team_keys=team_keys,
        repo_urls=repo_urls,
        page_size=page_size,
    )


async def test_the_backlog_signal_is_a_size_question_not_a_delta() -> None:
    """A standing triage backlog is work even on a board where nothing moved."""
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue("FIX-1", queue_states=[QueueState.TRIAGE])],
    )
    subject = signal_gate(tracker, PassSignal.triage_backlog)

    first = await subject.delta()
    second = await subject.delta()

    assert first.has_delta()
    assert second.has_delta(), "a backlog does not drain by being looked at"
    assert tracker.scans[0].queue_state is QueueState.TRIAGE
    # No mark, ever: asking "is the backlog empty" from a high-water stamp
    # would answer "did the backlog change", which is a different question.
    assert [scan.updated_since for scan in tracker.scans] == [None, None]
    assert subject.mark(PassSignal.triage_backlog, container=TEAM) is None


async def test_the_review_signal_reads_reviews_and_no_issue_scan_sees_them() -> None:
    """Reviews are a separate object class; an issue scan cannot reach one."""
    tracker = FakeTrackerPort()
    tracker.reviews[REPO] = [make_tracker_review("acme/repo#7", updated_at=LATER)]

    delta = await signal_gate(tracker, PassSignal.reviews_changed).delta()

    assert delta.has_delta()
    assert set(delta.changed) == {"acme/repo#7"}
    assert len(tracker.review_scans) == 1
    assert tracker.scans == [], "a review question must not become an issue scan"


async def test_any_one_signal_reporting_work_runs_the_pass() -> None:
    """The gate is a disjunction: a quiet signal does not veto a loud one."""
    tracker = FakeTrackerPort()
    tracker.reviews[REPO] = [make_tracker_review("acme/repo#7", updated_at=LATER)]

    delta = await signal_gate(
        tracker,
        PassSignal.issues_changed,
        PassSignal.triage_backlog,
        PassSignal.reviews_changed,
    ).delta()

    assert delta.has_delta()
    assert set(delta.changed) == {"acme/repo#7"}


async def test_marks_are_per_signal_so_issue_activity_cannot_skip_a_review() -> None:
    """One shared stamp would advance past review activity nobody has read."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue("FIX-1", created_at=LATEST)])
    subject = signal_gate(
        tracker,
        PassSignal.issues_changed,
        PassSignal.reviews_changed,
    )

    await subject.delta()

    assert subject.mark(PassSignal.issues_changed, container=TEAM) == LATEST
    assert subject.mark(PassSignal.reviews_changed, container=REPO) is None
    # The review window was never consumed, so a review stamped before the
    # issue activity is still reported on the next tick.
    tracker.reviews[REPO] = [make_tracker_review("acme/repo#7", updated_at=LATER)]
    assert set((await subject.delta()).changed) == {"acme/repo#7"}


async def test_a_gate_holding_no_signals_asks_nothing_at_all() -> None:
    """Why the composition builds no gate rather than an empty one.

    An empty gate would report no delta forever and pin its pass shut.
    The composition therefore resolves "no signals" to no gate, and this
    records what the object would do if one were ever built anyway.
    """
    tracker = FakeTrackerPort(issues=[make_tracker_issue("FIX-1")])

    delta = await signal_gate(tracker).delta()

    assert not delta.has_delta()
    assert tracker.scans == []
    assert tracker.review_scans == []


def test_every_query_the_gate_builds_names_its_container() -> None:
    """KOD-153: a further signal cannot be added unscoped.

    Structural rather than behavioural, and deliberately: a signal added
    later gets its own arm and its own query, and every behavioural case
    in this file would keep passing while that one query asked about the
    whole workspace.  The scope has to be a property of the source.
    """
    scoped = {"IssueQuery": "team_key", "ReviewQuery": "repo_url"}
    tree = ast.parse(GATE_SOURCE.read_text(encoding="utf-8"))
    built = [
        (node.func.id, {keyword.arg for keyword in node.keywords})
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in scoped
    ]

    assert built, "a scan finding no queries at all would pass vacuously"
    assert [name for name, keywords in built if scoped[name] not in keywords] == []


async def test_every_query_a_tick_actually_issues_carries_its_scope() -> None:
    """The same rule at the seam: every ask the port received names a container."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue("FIX-1")])
    tracker.reviews[REPO] = [make_tracker_review("acme/repo#7", updated_at=LATER)]

    await signal_gate(tracker, *PassSignal).delta()

    assert tracker.scans, "the issue signals must have asked something"
    assert tracker.review_scans, "the review signal must have asked something"
    assert [query for query in tracker.scans if query.team_key is None] == []
    assert [query for query in tracker.review_scans if query.repo_url is None] == []


async def test_each_issue_signal_asks_once_per_declared_team() -> None:
    """N declared boards are N queries, not one query over the workspace."""
    tracker = FakeTrackerPort()

    await signal_gate(
        tracker,
        PassSignal.issues_changed,
        team_keys=(TEAM, OTHER_TEAM),
    ).delta()

    assert [query.team_key for query in tracker.scans] == [TEAM, OTHER_TEAM]


async def test_an_issue_outside_the_declared_containers_never_reaches_a_delta() -> None:
    """A workspace holds more than one operation's board; this reads one."""
    tracker = FakeTrackerPort(
        issues=[
            make_tracker_issue("K-MINE", team_key=TEAM),
            make_tracker_issue("K-THEIRS", team_key=OTHER_TEAM),
        ],
    )

    delta = await signal_gate(tracker, PassSignal.issues_changed).delta()

    assert set(delta.changed) == {"K-MINE"}


async def test_a_full_page_of_another_board_cannot_crowd_out_this_ones_work() -> None:
    """KOD-153: the failure an unscoped scan produces, not merely its shape.

    A page is finite and the vendor fills it in its own order.  An unscoped
    query over a busy workspace comes back full of another board's activity,
    the one issue this operation owns falls off the end, and the gate
    reports a quiet board — every tick, for as long as the other board
    stays busier.
    """
    tracker = FakeTrackerPort(
        issues=[
            make_tracker_issue("K-THEIRS-1", team_key=OTHER_TEAM, created_at=LATEST),
            make_tracker_issue("K-THEIRS-2", team_key=OTHER_TEAM, created_at=LATEST),
            make_tracker_issue("K-MINE", team_key=TEAM, created_at=LATER),
        ],
    )

    delta = await signal_gate(
        tracker,
        PassSignal.issues_changed,
        page_size=CROWDED_PAGE,
    ).delta()

    assert set(delta.changed) == {"K-MINE"}


async def test_marks_are_per_container_so_a_busy_board_cannot_skip_a_quiet_one() -> (
    None
):
    """N containers are N timelines, and one stamp across them loses work."""
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue("K-MINE", team_key=TEAM, created_at=LATEST)],
    )
    subject = signal_gate(
        tracker,
        PassSignal.issues_changed,
        team_keys=(TEAM, OTHER_TEAM),
    )

    await subject.delta()

    assert subject.mark(PassSignal.issues_changed, container=TEAM) == LATEST
    assert subject.mark(PassSignal.issues_changed, container=OTHER_TEAM) is None
    # The second board's window was never consumed, so work stamped BEFORE
    # the first board's activity is still reported on the next tick.
    tracker.issues["K-THEIRS"] = make_tracker_issue(
        "K-THEIRS",
        team_key=OTHER_TEAM,
        created_at=LATER,
    )
    assert set((await subject.delta()).changed) == {"K-THEIRS"}


async def test_a_gate_with_no_team_refuses_rather_than_reading_the_workspace() -> None:
    """The dispatcher's refusal, at the gate: no container, no scan."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue("FIX-1")])

    with pytest.raises(OperationMemberAbsentError) as caught:
        signal_gate(tracker, PassSignal.issues_changed, team_keys=())

    assert PassSignal.issues_changed.value in str(caught.value)
    assert tracker.scans == []


async def test_a_gate_over_a_forge_less_origin_refuses_its_review_signal() -> None:
    """The other way a container fails a signal: declared, and unable to serve.

    A review scan resolves an owner and a repository out of a forge URL,
    and a local bare origin has neither — so the ask raises, identically,
    on every tick of a pass that would otherwise run forever.  Refused at
    construction, where the container and the signal first meet.
    """
    tracker = FakeTrackerPort()

    with pytest.raises(PassGateScopeError) as caught:
        signal_gate(tracker, PassSignal.reviews_changed, repo_urls=(FILE_ORIGIN,))

    assert caught.value.signal == PassSignal.reviews_changed.value
    assert caught.value.container == FILE_ORIGIN
    assert tracker.review_scans == []


async def test_the_same_origin_without_the_review_signal_is_no_obstacle() -> None:
    """The refusal is about the QUESTION, not about the repository.

    An operation firing into a local bare origin gates on its issue signals
    perfectly well; nothing about that origin is a problem until something
    asks it a question it cannot answer.
    """
    tracker = FakeTrackerPort(issues=[make_tracker_issue("K-MINE")])

    subject = signal_gate(
        tracker,
        PassSignal.issues_changed,
        repo_urls=(FILE_ORIGIN,),
    )

    assert set((await subject.delta()).changed) == {"K-MINE"}


async def test_a_forge_origin_carries_the_review_signal_as_before() -> None:
    """The paired positive: the refusal is not a ban on the review signal."""
    tracker = FakeTrackerPort()
    tracker.reviews[REPO] = [make_tracker_review("acme/repo#7", updated_at=LATER)]

    subject = signal_gate(tracker, PassSignal.reviews_changed, repo_urls=(REPO,))

    assert set((await subject.delta()).changed) == {"acme/repo#7"}


async def test_a_gate_with_no_repository_refuses_its_review_signal() -> None:
    """The other container class, refused on the same terms."""
    tracker = FakeTrackerPort()

    with pytest.raises(OperationMemberAbsentError) as caught:
        signal_gate(tracker, PassSignal.reviews_changed, repo_urls=())

    assert PassSignal.reviews_changed.value in str(caught.value)
    assert tracker.review_scans == []


class RefusingTracker(FakeTrackerPort):
    """A double that cannot ANSWER for named containers.

    ``refused`` holds the containers whose scan fails — team keys for the
    issue scan, repository urls for the review scan — and it is mutable,
    because the state under test is a transport that fails and then
    recovers.  The exception is the one the tracker port surfaces a failed
    call as; a caller cannot tell it from a scope refusal by type.
    """

    def __init__(
        self,
        *,
        issues: Sequence[TrackerIssue] = (),
        refused: Sequence[str] = (),
    ) -> None:
        super().__init__(issues=issues)
        self.refused: set[str] = set(refused)

    async def scan_issues(self, *, query: IssueQuery) -> Sequence[TrackerIssue]:
        self._refuse(query.team_key)
        return await super().scan_issues(query=query)

    async def scan_reviews(self, *, query: ReviewQuery) -> Sequence[TrackerReview]:
        self._refuse(query.repo_url)
        return await super().scan_reviews(query=query)

    def _refuse(self, container: str | None) -> None:
        if container in self.refused:
            raise McpTransportError(
                "the MCP tool call failed in transport",
                server_name="fixture",
                tool_name="scan",
            )


async def test_an_ask_that_could_not_be_answered_never_reads_as_nothing_moved() -> None:
    """KOD-151: three states, not two — and the third keeps its own window."""
    tracker = RefusingTracker(
        issues=[make_tracker_issue("K-MINE", created_at=LATEST)],
        refused=[REPO],
    )
    subject = signal_gate(
        tracker,
        PassSignal.issues_changed,
        PassSignal.reviews_changed,
    )
    tracker.reviews[REPO] = [make_tracker_review("acme/repo#7", updated_at=LATER)]

    with structlog.testing.capture_logs() as logs:
        delta = await subject.delta()

    assert set(delta.changed) == {"K-MINE"}, "an answerable signal still answers"
    assert [
        (entry["signal"], entry["container"])
        for entry in logs
        if entry["event"] == "pass_gate_signal_unanswerable"
    ] == [(PassSignal.reviews_changed.value, REPO)]
    assert subject.mark(PassSignal.reviews_changed, container=REPO) is None
    # The window it could not read is re-read once the transport recovers,
    # rather than skipped over by a mark it never earned.
    tracker.refused.clear()
    assert set((await subject.delta()).changed) == {"acme/repo#7"}


async def test_a_container_that_could_not_answer_leaves_its_siblings_alone() -> None:
    """Per (signal, container): one board's outage is not the signal's."""
    tracker = RefusingTracker(
        issues=[make_tracker_issue("K-MINE", team_key=TEAM, created_at=LATEST)],
        refused=[OTHER_TEAM],
    )
    subject = signal_gate(
        tracker,
        PassSignal.issues_changed,
        team_keys=(TEAM, OTHER_TEAM),
    )

    delta = await subject.delta()

    assert set(delta.changed) == {"K-MINE"}
    assert subject.mark(PassSignal.issues_changed, container=TEAM) == LATEST
    assert subject.mark(PassSignal.issues_changed, container=OTHER_TEAM) is None


async def test_the_outcome_event_tells_an_unanswered_ask_from_a_quiet_board() -> None:
    """Two ticks that report no delta, and the log distinguishes them."""
    tracker = RefusingTracker(refused=[TEAM])
    subject = signal_gate(tracker, PassSignal.issues_changed)

    with structlog.testing.capture_logs() as unanswered:
        assert not (await subject.delta()).has_delta()
    tracker.refused.clear()
    with structlog.testing.capture_logs() as quiet:
        assert not (await subject.delta()).has_delta()

    assert [
        entry["unanswerable"] for entry in unanswered if entry["event"] in _OUTCOMES
    ] == [[f"{PassSignal.issues_changed.value}@{TEAM}"]]
    assert [
        entry["unanswerable"] for entry in quiet if entry["event"] in _OUTCOMES
    ] == [[]]
