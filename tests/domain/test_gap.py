"""Amended gap computation has one state arm and no body or I/O dependency."""

import ast
import inspect

import pytest

from kodezart.domain import gap
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import make_tracker_issue


def criterion(key="criterion-key", state=WorkflowStateKind.UNSTARTED, body=""):
    return make_tracker_issue(key, state_kind=state, body=body).model_copy(
        update={"issue_labels": frozenset({"criterion"})}
    )


#: Every tracker state with the membership answer its arm gives at no
#: supersession reference, held as one list so the arm case below and the
#: exhaustiveness check read the same source instead of two hand-kept
#: lists that can drift apart.
CASES = [
    (WorkflowStateKind.TRIAGE, True),
    (WorkflowStateKind.BACKLOG, True),
    (WorkflowStateKind.UNSTARTED, True),
    (WorkflowStateKind.STARTED, True),
    (WorkflowStateKind.COMPLETED, False),
    (WorkflowStateKind.CANCELED, True),
    (WorkflowStateKind.DUPLICATE, True),
]

#: The two states whose answer is itself a question about the supersession
#: reference. What they read is KOD-794's to pin, so they are asked here at
#: no reference only.
REFERENCE_SENSITIVE = frozenset(
    {WorkflowStateKind.CANCELED, WorkflowStateKind.DUPLICATE}
)

#: Each row of CASES at every reference its answer has to survive: the open
#: states and COMPLETED at both, the reference-sensitive pair at none.
REFERENCE_CASES = [
    (state, expected, supersession_ref)
    for state, expected in CASES
    for supersession_ref in (
        (None,) if state in REFERENCE_SENSITIVE else (None, "opaque-successor")
    )
]


@pytest.mark.parametrize("state,expected,supersession_ref", REFERENCE_CASES)
def test_one_membership_arm_per_tracker_state(state, expected, supersession_ref):
    """One arm per state, and for most of them the reference cannot move it.

    A criterion re-opened after a cancellation still carries the reference
    that cancellation was given, and the caller passes whatever reference it
    holds. An open arm that answered "in the gap only while no reference is
    present" would drop such a criterion out of the gap silently, so every
    arm outside REFERENCE_SENSITIVE is asked at both references (KOD-420).
    """
    assert (
        gap.in_gap(criterion(state=state), supersession_ref=supersession_ref)
        is expected
    )


def test_the_membership_cases_name_every_tracker_state() -> None:
    """A new state has to gain a row here, not just an arm in the source."""
    assert {state for state, _ in CASES} == set(WorkflowStateKind)


@pytest.mark.parametrize(
    "state", [WorkflowStateKind.CANCELED, WorkflowStateKind.DUPLICATE]
)
def test_cancellation_requires_its_own_explicit_supersession(state):
    canceled = criterion("canceled", state)
    open_criterion = criterion("open")
    assert gap.compute_gap(
        [canceled, open_criterion], supersession_refs={"another": "replacement"}
    ) == (canceled, open_criterion)
    assert gap.compute_gap(
        [canceled, open_criterion],
        supersession_refs={"canceled": "opaque-replacement-reference"},
    ) == (open_criterion,)


#: The rows whose answer a reference cannot move, asked one call deeper at
#: `compute_gap`. The reference-sensitive pair is left exactly where KOD-794
#: puts it: what those two arms read is that criterion's to pin, not this one's.
UNSUPERSEDED_CASES = [
    (state, expected) for state, expected in CASES if state not in REFERENCE_SENSITIVE
]


@pytest.mark.parametrize("state,expected", UNSUPERSEDED_CASES)
def test_a_criterion_carrying_a_reference_keeps_the_answer_of_its_arm(state, expected):
    """A reference on the record cannot close what its arm leaves open (KOD-420).

    The arm cases above ask `in_gap` at both references, but they pin the
    arms where nothing carries one: `compute_gap` is the only production
    path that supplies a reference at all. A criterion re-opened after a
    cancellation still carries the reference that cancellation was given,
    and a filter over the criteria keyed on "carries a reference" instead of
    on the state arm drops it out of the gap silently -- the drop the arm
    case's own docstring says must never happen.

    Bound here: `compute_gap` itself, and no caller of it. Its callers that
    pass a reference are not composed into the running service (KOD-799),
    so they are not bound here.
    """
    carrying = criterion("carrying", state)
    computed = gap.compute_gap(
        [carrying], supersession_refs={"carrying": "opaque-successor"}
    )
    assert computed == ((carrying,) if expected else ())


@pytest.mark.parametrize("state,expected", UNSUPERSEDED_CASES)
def test_a_reference_naming_a_live_successor_moves_neither_record(state, expected):
    """The named successor is itself a criterion of the same reading (KOD-420).

    Here the reference appears on both sides of the supplied mapping, so a
    filter keyed on its keys drops the criterion carrying the reference and
    one keyed on its values drops the successor. Both are the same silent
    drop, and the arms decide neither.

    Bound here: `compute_gap` itself, and no caller of it. Its callers that
    pass a reference are not composed into the running service (KOD-799),
    so they are not bound here.
    """
    carrying = criterion("carrying", state)
    successor = criterion("successor")
    computed = gap.compute_gap(
        [carrying, successor], supersession_refs={"carrying": "successor"}
    )
    assert computed == ((carrying, successor) if expected else (successor,))


@pytest.mark.parametrize(
    "body",
    ["", "**Evidence:** —", "**Evidence:** prior-sha\nRuling text remains exact"],
)
def test_open_record_is_retained_without_rewriting_evidence(body):
    original = criterion(body=body)
    computed = gap.compute_gap([original], supersession_refs={})
    assert computed == (original,)
    assert computed[0] is original
    assert computed[0].body == body


def test_moved_back_from_done_reenters_without_parent_state_input():
    original = criterion(
        state=WorkflowStateKind.COMPLETED, body="**Evidence:** old-sha"
    )
    assert gap.compute_gap([original], supersession_refs={}) == ()
    lapsed = original.model_copy(update={"state_kind": WorkflowStateKind.UNSTARTED})
    assert gap.compute_gap([lapsed], supersession_refs={}) == (lapsed,)
    assert lapsed.body == original.body


def test_empty_gap_and_noncriterion_or_duplicate_inputs():
    assert gap.compute_gap([], supersession_refs={}) == ()
    with pytest.raises(ValueError, match="criterion"):
        gap.compute_gap([make_tracker_issue("deliverable")], supersession_refs={})
    duplicate = criterion()
    with pytest.raises(ValueError, match="more than once"):
        gap.compute_gap([duplicate, duplicate], supersession_refs={})
    with pytest.raises(ValueError, match="nonempty"):
        gap.in_gap(duplicate, supersession_ref=" ")


def test_gap_module_has_only_pure_dependencies_and_no_fallback_state_arm():
    tree = ast.parse(inspect.getsource(gap))
    imports = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert imports == {"collections.abc", "kodezart.types.domain.tracker"}
    assert not any(
        isinstance(node, (ast.Import, ast.AsyncFunctionDef, ast.Await))
        for node in ast.walk(tree)
    )
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert attributes <= {
        "issue_labels",
        "strip",
        "state_kind",
        "COMPLETED",
        "CANCELED",
        "DUPLICATE",
        "TRIAGE",
        "BACKLOG",
        "UNSTARTED",
        "STARTED",
        "issue_key",
        "get",
    }
    pure_builtins = {
        "ValueError",
        "len",
        "tuple",
        "list",
        "dict",
        "set",
        "frozenset",
        "bool",
        "all",
        "any",
        "sorted",
        "enumerate",
        "zip",
        "min",
        "max",
    }
    local_functions = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        if isinstance(call.func, ast.Name):
            assert call.func.id in pure_builtins | local_functions
        elif isinstance(call.func, ast.Attribute):
            assert call.func.attr in {"strip", "get"}
        else:
            pytest.fail("unaccounted dynamic call in pure gap module")
    matches = [node for node in ast.walk(tree) if isinstance(node, ast.Match)]
    arms = [case.pattern for match in matches for case in match.cases]
    assert {
        pattern.value.attr for pattern in arms if isinstance(pattern, ast.MatchValue)
    } == set(WorkflowStateKind.__members__)
    assert all(isinstance(pattern, ast.MatchValue) for pattern in arms)


@pytest.mark.parametrize(
    "call",
    ["open('not-executed', 'w')", "print('not-executed')", "__import__('socket')"],
)
def test_purity_guard_rejects_builtin_io_without_executing_it(monkeypatch, call):
    source = inspect.getsource(gap) + f"\ndef unexpected_io():\n    {call}\n"
    monkeypatch.setattr(inspect, "getsource", lambda _: source)
    with pytest.raises(AssertionError):
        test_gap_module_has_only_pure_dependencies_and_no_fallback_state_arm()
