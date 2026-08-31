"""The deterministic pre-query, over the in-process fake tracker.

The gate's whole claim is that it costs nothing: one port call, no prompt,
no session, no model.  Two of the tests below check that claim structurally
— the module's imports and the gate's own collaborators — because a claim
about cost that is only asserted behaviourally survives someone adding an
executor to the constructor.
"""

import ast
import re
from datetime import timedelta
from pathlib import Path

from kodezart.services.pass_gate import PassGate
from kodezart.types.domain.operation import QueueState
from tests.fakes import FIXTURE_EPOCH, FakeTrackerPort, make_tracker_issue

GATE_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "kodezart"
    / "services"
    / "pass_gate.py"
)

PAGE_SIZE = 50
LATER = FIXTURE_EPOCH + timedelta(hours=1)
LATEST = FIXTURE_EPOCH + timedelta(hours=2)


def gate(tracker: FakeTrackerPort) -> PassGate:
    return PassGate(
        tracker=tracker,
        queue_state=QueueState.APPROVED,
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
    assert subject.mark == LATEST

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
    assert subject.mark == LATER
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
