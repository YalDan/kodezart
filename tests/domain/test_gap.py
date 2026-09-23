"""Amended gap computation has one state arm and no body or I/O dependency."""

import ast
import inspect

import pytest

from kodezart.domain import gap
from kodezart.domain.criterion_evidence import (
    parse_criterion_evidence,
    render_evidence_field,
)
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.gap import CriterionGap, GapMembership
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import make_tracker_issue

GRADED_SHA = "c" * 40
GRADED_TEST = "tests/domain/test_gap.py::test_case"

#: A criterion nobody has graded: the template's Evidence row, unfilled.
UNGRADED_BODY = "**Check:** The contract.\n**Do:** The mechanism.\n**Evidence:** —"

#: One row per member of the state enum, and the identity assertion below is
#: what turns "one arm per state" into a fact.
ROWS: dict[WorkflowStateKind, GapMembership] = {
    WorkflowStateKind.TRIAGE: GapMembership.OWED,
    WorkflowStateKind.BACKLOG: GapMembership.OWED,
    WorkflowStateKind.UNSTARTED: GapMembership.OWED,
    WorkflowStateKind.STARTED: GapMembership.OWED,
    WorkflowStateKind.COMPLETED: GapMembership.DISCHARGED,
    WorkflowStateKind.CANCELED: GapMembership.EXCLUDED,
    WorkflowStateKind.DUPLICATE: GapMembership.EXCLUDED,
}


def criterion(key="criterion-key", state=WorkflowStateKind.UNSTARTED, body=""):
    return make_tracker_issue(key, state_kind=state, body=body).model_copy(
        update={"issue_labels": frozenset({"criterion"})}
    )


def graded_body(sha: str) -> str:
    """A criterion body whose Evidence row records one complete grading."""
    return "**Check:** The contract.\n**Do:** The mechanism.\n" + render_evidence_field(
        CriterionEvidence(graded_sha=sha, test=GRADED_TEST)
    )


@pytest.mark.parametrize("state,expected", list(ROWS.items()))
def test_one_membership_arm_per_tracker_state(state, expected):
    """A state the enum gains has no arm here until somebody writes one."""
    assert set(ROWS) == set(WorkflowStateKind)
    assert gap.gap_membership(criterion(state=state)) is expected


def test_canceled_and_duplicate_are_excluded_on_state_alone_and_named_beside_the_gap():
    """State decides; no reference is read, and nothing leaves in silence."""
    owed = criterion("owed")
    canceled = criterion("canceled", WorkflowStateKind.CANCELED)
    done = criterion("done", WorkflowStateKind.COMPLETED)
    duplicate = criterion("duplicate", WorkflowStateKind.DUPLICATE)

    assert gap.compute_gap([owed, canceled, done, duplicate]) == CriterionGap(
        owed=(owed,), excluded=("canceled", "duplicate")
    )

    with_prose = canceled.model_copy(update={"body": "Superseded by X-1"})
    assert gap.compute_gap([owed, with_prose, done, duplicate]) == CriterionGap(
        owed=(owed,), excluded=("canceled", "duplicate")
    )

    prose_owed = criterion("prose-owed", body="Superseded by X-1")
    assert gap.gap_membership(prose_owed) is GapMembership.OWED
    assert gap.compute_gap([prose_owed]) == CriterionGap(
        owed=(prose_owed,), excluded=()
    )
    prose_done = criterion(
        "prose-done", WorkflowStateKind.COMPLETED, body="Superseded by X-1"
    )
    assert gap.gap_membership(prose_done) is GapMembership.DISCHARGED

    assert set(inspect.signature(gap.gap_membership).parameters) == {"criterion"}
    assert set(inspect.signature(gap.compute_gap).parameters) == {"criteria"}


@pytest.mark.parametrize(
    "body",
    ["", "**Evidence:** —", "**Evidence:** prior-sha\nThe decision text stays exact"],
)
def test_open_record_is_retained_without_rewriting_evidence(body):
    original = criterion(body=body)
    computed = gap.compute_gap([original]).owed
    assert computed == (original,)
    assert computed[0] is original
    assert computed[0].body == body


def test_moved_back_from_done_reenters_without_parent_state_input():
    original = criterion(
        state=WorkflowStateKind.COMPLETED, body="**Evidence:** old-sha"
    )
    assert gap.compute_gap([original]).owed == ()
    lapsed = original.model_copy(update={"state_kind": WorkflowStateKind.UNSTARTED})
    assert gap.compute_gap([lapsed]).owed == (lapsed,)
    assert lapsed.body == original.body


def test_a_criterion_never_graded_is_owed_and_carries_no_grading():
    """No grading on record is a criterion still owed, never one knocked down."""
    never_graded = criterion(body=UNGRADED_BODY)
    computed = gap.compute_gap([never_graded])
    assert gap.gap_membership(never_graded) is GapMembership.OWED
    assert computed == CriterionGap(owed=(never_graded,), excluded=())
    assert computed.owed[0] is never_graded
    with pytest.raises(ValueError, match="Evidence"):
        parse_criterion_evidence(never_graded.body)
    assert set(GapMembership) == {
        GapMembership.OWED,
        GapMembership.DISCHARGED,
        GapMembership.EXCLUDED,
    }


def test_the_lapse_and_the_never_graded_are_one_membership_told_apart_by_the_sha():
    """One state, one membership, and only the Evidence row separates them."""
    lapsed = criterion("lapsed", body=graded_body(GRADED_SHA))
    never = criterion("never", body=UNGRADED_BODY)

    assert gap.gap_membership(lapsed) is gap.gap_membership(never)
    assert gap.gap_membership(lapsed) is GapMembership.OWED
    assert gap.compute_gap([lapsed, never]) == CriterionGap(
        owed=(lapsed, never), excluded=()
    )
    assert parse_criterion_evidence(lapsed.body).graded_sha == GRADED_SHA
    with pytest.raises(ValueError, match="Evidence"):
        parse_criterion_evidence(never.body)


def test_empty_gap_and_noncriterion_or_duplicate_inputs():
    assert gap.compute_gap([]) == CriterionGap(owed=(), excluded=())
    non_criterion = make_tracker_issue("deliverable")
    with pytest.raises(ValueError, match="criterion"):
        gap.compute_gap([non_criterion])
    with pytest.raises(ValueError, match="criterion"):
        gap.gap_membership(non_criterion)
    repeated = criterion()
    with pytest.raises(ValueError, match="more than once"):
        gap.compute_gap([repeated, repeated])


def test_gap_module_has_only_pure_dependencies_and_no_fallback_state_arm():
    tree = ast.parse(inspect.getsource(gap))
    imports = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert imports == {
        "collections.abc",
        "kodezart.types.domain.gap",
        "kodezart.types.domain.tracker",
    }
    assert not any(
        isinstance(node, (ast.Import, ast.AsyncFunctionDef, ast.Await))
        for node in ast.walk(tree)
    )
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert attributes <= {
        "issue_labels",
        "state_kind",
        "COMPLETED",
        "CANCELED",
        "DUPLICATE",
        "TRIAGE",
        "BACKLOG",
        "UNSTARTED",
        "STARTED",
        "issue_key",
        "OWED",
        "DISCHARGED",
        "EXCLUDED",
    }
    pure_builtins = {
        "ValueError",
        "len",
        "tuple",
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
    # Derived from the parse, not listed: a type this module imports from the
    # types layer is a pure record constructor, and naming them by hand here
    # would be a second place to keep the module's import set.
    imported_types = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith("kodezart.types.")
        for alias in node.names
    }
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        if isinstance(call.func, ast.Name):
            assert call.func.id in pure_builtins | local_functions | imported_types
        elif isinstance(call.func, ast.Attribute):
            pytest.fail("unaccounted attribute call in pure gap module")
        else:
            pytest.fail("unaccounted dynamic call in pure gap module")
    matches = [node for node in ast.walk(tree) if isinstance(node, ast.Match)]
    assert len(matches) == 1
    arms = [case for match in matches for case in match.cases]
    assert all(case.guard is None for case in arms)
    assert all(isinstance(case.pattern, ast.MatchValue) for case in arms)
    assert sorted(
        case.pattern.value.attr
        for case in arms
        if isinstance(case.pattern, ast.MatchValue)
    ) == sorted(WorkflowStateKind.__members__)


@pytest.mark.parametrize(
    "call",
    ["open('not-executed', 'w')", "print('not-executed')", "__import__('socket')"],
)
def test_purity_guard_rejects_builtin_io_without_executing_it(monkeypatch, call):
    source = inspect.getsource(gap) + f"\ndef unexpected_io():\n    {call}\n"
    monkeypatch.setattr(inspect, "getsource", lambda _: source)
    with pytest.raises(AssertionError):
        test_gap_module_has_only_pure_dependencies_and_no_fallback_state_arm()
