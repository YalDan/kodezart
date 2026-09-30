"""Amended gap computation has one state arm and no body or I/O dependency."""

import ast
import inspect

import pytest

from kodezart.domain import gap
from kodezart.domain.criterion_cross_off import LAPSE_POINTER, lapse_observation
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


def graded_body(sha: str, test: str = GRADED_TEST) -> str:
    """A criterion body whose Evidence row records one complete grading."""
    return "**Check:** The contract.\n**Do:** The mechanism.\n" + render_evidence_field(
        CriterionEvidence(graded_sha=sha, test=test)
    )


@pytest.mark.parametrize("state,expected", list(ROWS.items()))
def test_one_membership_arm_per_tracker_state(state, expected):
    """A state the enum gains has no arm here until somebody writes one."""
    assert set(ROWS) == set(WorkflowStateKind)
    assert gap.gap_membership(criterion(state=state)) is expected
    # The kind-level askings read the same arm: a consumer holding only the
    # kind a reading carried gets the answer a criterion in it gets.
    assert gap.state_membership(state) is expected
    assert gap.open_state_kind(state) is (expected is GapMembership.OWED)


def test_canceled_and_duplicate_are_excluded_on_state_alone_and_named_beside_the_gap():
    """State decides; no reference is read, and nothing leaves in silence."""
    owed = criterion("owed")
    canceled = criterion("canceled", WorkflowStateKind.CANCELED)
    done = criterion("done", WorkflowStateKind.COMPLETED)
    duplicate = criterion("duplicate", WorkflowStateKind.DUPLICATE)

    assert gap.compute_gap([owed, canceled, done, duplicate]) == CriterionGap(
        owed=(owed,), excluded=("canceled", "duplicate")
    )
    assert gap.compute_gap([owed, duplicate, done, canceled]) == CriterionGap(
        owed=(owed,), excluded=("duplicate", "canceled")
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
    assert set(inspect.signature(gap.state_membership).parameters) == {"state_kind"}
    assert set(inspect.signature(gap.open_state_kind).parameters) == {"state_kind"}


def test_the_membership_cases_name_every_tracker_state() -> None:
    """A new state has to gain a row here, not just an arm in the source."""
    assert set(ROWS) == set(WorkflowStateKind)


#: Each way a criterion record can carry a supersession: a note in its body,
#: a label and its title. A comment is no field of the record, so it cannot
#: reach the arithmetic at all. None of them is read: the arithmetic takes
#: the record's state and nothing else (KOD-794).
SUPERSESSION_NOTES = {
    "body note": lambda issue, successor: issue.model_copy(
        update={"body": f"{issue.body}\n\nSuperseded by {successor}."}
    ),
    "label": lambda issue, successor: issue.model_copy(
        update={
            "issue_labels": issue.issue_labels
            | {"superseded", f"superseded-by:{successor}"}
        }
    ),
    "title": lambda issue, successor: issue.model_copy(
        update={"title": f"Superseded by {successor}"}
    ),
}


def carrying(issue, note, successor="opaque-successor"):
    """*issue* carrying the supersession *note* names, pointing at *successor*."""
    return SUPERSESSION_NOTES[note](issue, successor)


@pytest.mark.parametrize("note", sorted(SUPERSESSION_NOTES))
@pytest.mark.parametrize(
    "state", [WorkflowStateKind.CANCELED, WorkflowStateKind.DUPLICATE]
)
def test_a_cancellation_is_excluded_on_state_alone_whatever_supersession_it_carries(
    state, note
):
    """A supersession on the record neither closes nor keeps a cancellation.

    Converted from the reading KOD-794 rejected, in which a Canceled or
    Duplicate criterion stayed owed until its own supersession was supplied.
    The state decides: carrying a note or not, the record is excluded and
    named beside the gap, exactly as the one without.
    """
    plain = criterion("canceled", state)
    noted = carrying(plain, note)
    open_criterion = criterion("open")

    assert noted != plain
    assert gap.gap_membership(noted) is gap.gap_membership(plain)
    assert gap.gap_membership(noted) is GapMembership.EXCLUDED
    assert gap.compute_gap([noted, open_criterion]) == gap.compute_gap(
        [plain, open_criterion]
    )
    assert gap.compute_gap([noted, open_criterion]) == CriterionGap(
        owed=(open_criterion,), excluded=("canceled",)
    )


@pytest.mark.parametrize("note", sorted(SUPERSESSION_NOTES))
@pytest.mark.parametrize("state,expected", list(ROWS.items()))
def test_a_criterion_carrying_a_reference_keeps_the_answer_of_its_arm(
    state, expected, note
):
    """A reference on the record cannot move what its arm answers (KOD-420).

    A criterion re-opened after a cancellation still carries the reference
    that cancellation was given, and a filter keyed on "carries a reference"
    instead of on the state arm drops it out of the gap silently. Under the
    rule KOD-794 settled the same holds for every arm: the reference is not
    read, so an open criterion carrying one stays owed, a completed one stays
    discharged and a canceled or duplicate one is excluded on its state.
    """
    noted = carrying(criterion("carrying", state), note)

    assert gap.gap_membership(noted) is expected
    assert gap.compute_gap([noted]) == CriterionGap(
        owed=(noted,) if expected is GapMembership.OWED else (),
        excluded=("carrying",) if expected is GapMembership.EXCLUDED else (),
    )


@pytest.mark.parametrize("note", sorted(SUPERSESSION_NOTES))
@pytest.mark.parametrize("state,expected", list(ROWS.items()))
def test_a_reference_naming_a_live_successor_moves_neither_record(
    state, expected, note
):
    """The named successor is itself a criterion of the same reading (KOD-420).

    Here the reference on one record names the other, so a filter keyed on
    the naming record drops it and one keyed on the named record drops the
    successor. Both are the same silent drop, and the arms decide neither:
    each record answers its own state.
    """
    successor = criterion("successor")
    noted = carrying(criterion("carrying", state), note, successor.issue_key)
    computed = gap.compute_gap([noted, successor])

    assert gap.gap_membership(successor) is GapMembership.OWED
    assert computed.owed == (
        (noted, successor) if expected is GapMembership.OWED else (successor,)
    )
    assert computed.excluded == (
        ("carrying",) if expected is GapMembership.EXCLUDED else ()
    )


def test_membership_is_on_state_alone_whatever_labels_the_criterion_carries():
    """A label named like an excluding state or a supersession excludes nothing."""
    labelled = criterion("labelled").model_copy(
        update={
            "issue_labels": frozenset(
                {"criterion", "superseded", "canceled", "duplicate"}
            )
        }
    )

    assert gap.gap_membership(labelled) is GapMembership.OWED
    assert gap.compute_gap([labelled]) == CriterionGap(owed=(labelled,), excluded=())


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
    assert gap.compute_gap([lapsed]).owed[0].body == original.body


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
    assert (
        parse_criterion_evidence(gap.compute_gap([lapsed]).owed[0].body).graded_sha
        == GRADED_SHA
    )
    with pytest.raises(ValueError, match="Evidence"):
        parse_criterion_evidence(never.body)


def test_a_lapse_and_a_reopened_grading_are_owed_and_told_apart_by_the_lapse_pointer():
    """The sha stays in both rows; only the lapse writes its pointer beside it.

    The lapse row is the one the cross-off writer builds when a standing
    grading no longer holds: the graded sha kept, and the grading's own test
    pointer carried under the lapse pointer. A grading reopened or refuted
    keeps the sha with no such pointer.
    """
    lapsed = criterion(
        "lapsed",
        body=graded_body(GRADED_SHA, lapse_observation(observation=GRADED_TEST)),
    )
    reopened = criterion("reopened", body=graded_body(GRADED_SHA))

    assert gap.gap_membership(lapsed) is GapMembership.OWED
    assert gap.gap_membership(reopened) is GapMembership.OWED
    computed = gap.compute_gap([lapsed, reopened])
    assert computed == CriterionGap(owed=(lapsed, reopened), excluded=())
    assert [issue.body for issue in computed.owed] == [lapsed.body, reopened.body]
    from_lapse, from_reopen = (
        parse_criterion_evidence(issue.body) for issue in computed.owed
    )
    assert from_lapse.graded_sha == from_reopen.graded_sha == GRADED_SHA
    assert from_lapse.test == lapse_observation(observation=GRADED_TEST)
    assert from_reopen.test == GRADED_TEST
    assert LAPSE_POINTER in computed.owed[0].body
    assert LAPSE_POINTER not in computed.owed[1].body


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
    # A blank supersession once refused here; the arithmetic now takes no
    # supersession input at all, so there is nothing blank to refuse.
    blank = carrying(criterion("blank"), "body note", " ")
    assert gap.gap_membership(blank) is GapMembership.OWED
    assert not any(
        "supersession" in parameter
        for asking in (
            gap.state_membership,
            gap.gap_membership,
            gap.open_state_kind,
            gap.compute_gap,
        )
        for parameter in inspect.signature(asking).parameters
    )


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

    def definition(function) -> ast.FunctionDef:
        (found,) = (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == function.__name__
        )
        return found

    def without_docstring(node: ast.FunctionDef) -> list[ast.stmt]:
        statements = node.body
        if isinstance(statements[0], ast.Expr) and isinstance(
            statements[0].value, ast.Constant
        ):
            return statements[1:]
        return statements

    kind_reading = definition(gap.state_membership)
    membership = definition(gap.gap_membership)
    open_kind = definition(gap.open_state_kind)
    # The match is the kind reading's last statement: nothing after it can
    # answer for a state the arms do not name.
    assert kind_reading.body[-1] is matches[0]
    arms = [case for match in matches for case in match.cases]
    assert all(case.guard is None for case in arms)
    assert all(isinstance(case.pattern, ast.MatchValue) for case in arms)
    assert sorted(
        case.pattern.value.attr
        for case in arms
        if isinstance(case.pattern, ast.MatchValue)
    ) == sorted(WorkflowStateKind.__members__)
    # Nothing before the match can answer for a state either: after the
    # docstring, the kind reading is the match alone, whose subject is its
    # one parameter.
    assert without_docstring(kind_reading) == [matches[0]]
    match = matches[0]
    (kind,) = kind_reading.args.args
    assert ast.dump(match.subject) == ast.dump(ast.Name(id=kind.arg, ctx=ast.Load()))
    # The criterion's membership is the one label refusal and then the kind
    # reading of the parameter's own state, and nothing else.
    statements = without_docstring(membership)
    assert [type(statement) for statement in statements] == [ast.If, ast.Return]
    refusal, answer = statements
    assert [type(statement) for statement in refusal.body] == [ast.Raise]
    assert refusal.orelse == []
    (parameter,) = membership.args.args
    assert ast.dump(answer.value) == ast.dump(
        ast.Call(
            func=ast.Name(id=kind_reading.name, ctx=ast.Load()),
            args=[
                ast.Attribute(
                    value=ast.Name(id=parameter.arg, ctx=ast.Load()),
                    attr="state_kind",
                    ctx=ast.Load(),
                )
            ],
            keywords=[],
        )
    )
    # The kind-level asking is that same reading, open exactly when owed.
    asking = without_docstring(open_kind)
    assert [type(statement) for statement in asking] == [ast.Return]
    (asked,) = asking
    (open_parameter,) = open_kind.args.args
    assert isinstance(asked, ast.Return)
    assert ast.dump(asked.value) == ast.dump(
        ast.Compare(
            left=ast.Call(
                func=ast.Name(id=kind_reading.name, ctx=ast.Load()),
                args=[ast.Name(id=open_parameter.arg, ctx=ast.Load())],
                keywords=[],
            ),
            ops=[ast.Is()],
            comparators=[
                ast.Attribute(
                    value=ast.Name(id="GapMembership", ctx=ast.Load()),
                    attr=GapMembership.OWED.name,
                    ctx=ast.Load(),
                )
            ],
        )
    )
    # And no function of the module names a state outside a match pattern:
    # a name is read as the object it is bound to in the module, and a
    # string equal to a state's value names that state too.
    in_patterns = {id(node) for case in arms for node in ast.walk(case.pattern)}
    assert in_patterns
    # A parameter annotated with the enum itself names the type a kind
    # reading takes, not a state; only that bare annotation is set aside.
    annotations = {
        id(argument.annotation)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        for argument in node.args.args
        if isinstance(argument.annotation, ast.Name)
        and vars(gap).get(argument.annotation.id) is WorkflowStateKind
    }
    namespace = vars(gap)
    state_values = {member.value for member in WorkflowStateKind}

    def names_a_state(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            bound = namespace.get(node.id)
            return bound is WorkflowStateKind or isinstance(bound, WorkflowStateKind)
        return isinstance(node, ast.Constant) and node.value in state_values

    assert [
        ast.unparse(node)
        for node in ast.walk(tree)
        if id(node) not in in_patterns | annotations and names_a_state(node)
    ] == []


@pytest.mark.parametrize(
    "call",
    ["open('not-executed', 'w')", "print('not-executed')", "__import__('socket')"],
)
def test_purity_guard_rejects_builtin_io_without_executing_it(monkeypatch, call):
    source = inspect.getsource(gap) + f"\ndef unexpected_io():\n    {call}\n"
    monkeypatch.setattr(inspect, "getsource", lambda _: source)
    with pytest.raises(AssertionError):
        test_gap_module_has_only_pure_dependencies_and_no_fallback_state_arm()


_MATCH = "    match state_kind:\n"
_MEMBERSHIP = "    return state_membership(criterion.state_kind)\n"
_ASKING = "    return state_membership(state_kind) is GapMembership.OWED\n"
_MEMBERSHIPS = (
    "    memberships = [(criterion, gap_membership(criterion))"
    " for criterion in criteria]\n"
)
_CLOSED = "(States.COMPLETED, States.CANCELED, States.DUPLICATE)"


def _defaulted(closed: str) -> str:
    """compute_gap answering OWED itself for every state *closed* leaves out."""
    return (
        "    memberships = [\n"
        "        (\n"
        "            criterion,\n"
        "            gap_membership(criterion)\n"
        f"            if criterion.state_kind in {closed}\n"
        "            else GapMembership.OWED,\n"
        "        )\n"
        "        for criterion in criteria\n"
        "    ]\n"
    )


#: One default arm per spelling the shape guard reads: before the match, in
#: the match's subject, in compute_gap through the enum, through an alias the
#: module binds to it and through a state's string value, a label check
#: that excludes before the state is read, a default around the kind reading
#: in the criterion's membership, and one in the kind-level asking.
DEFAULT_ARMS: dict[str, tuple[str, str]] = {
    "before-the-match": (
        _MATCH,
        "    if state_kind not in (WorkflowStateKind.COMPLETED,):\n"
        "        return GapMembership.OWED\n" + _MATCH,
    ),
    "in-the-subject": (
        _MATCH,
        "    match (\n"
        "        state_kind\n"
        "        if state_kind in (WorkflowStateKind.COMPLETED,)\n"
        "        else WorkflowStateKind.STARTED\n"
        "    ):\n",
    ),
    "in-compute-gap": (
        _MEMBERSHIPS,
        _defaulted(_CLOSED.replace("States.", "WorkflowStateKind.")),
    ),
    "through-an-alias": (_MEMBERSHIPS, _defaulted(_CLOSED)),
    "by-value": (_MEMBERSHIPS, _defaulted('("completed", "canceled", "duplicate")')),
    "by-label": (
        _MEMBERSHIP,
        '    if "superseded" in criterion.issue_labels:\n'
        "        return GapMembership.EXCLUDED\n" + _MEMBERSHIP,
    ),
    "around-the-kind-reading": (
        _MEMBERSHIP,
        "    return (\n"
        "        state_membership(criterion.state_kind)\n"
        "        if criterion.state_kind in (WorkflowStateKind.COMPLETED,)\n"
        "        else GapMembership.OWED\n"
        "    )\n",
    ),
    "in-the-kind-asking": (
        _ASKING,
        "    if state_kind is WorkflowStateKind.CANCELED:\n"
        "        return True\n" + _ASKING,
    ),
}


@pytest.mark.parametrize("spelling", list(DEFAULT_ARMS))
def test_the_shape_guard_rejects_a_default_arm_however_it_is_spelled(
    monkeypatch, spelling
):
    """Each spelling of a default the guard reads is refused, never executed."""
    old, new = DEFAULT_ARMS[spelling]
    source = inspect.getsource(gap)
    assert source.count(old) == 1
    planted = source.replace(old, new)
    monkeypatch.setattr(inspect, "getsource", lambda _: planted)
    # The alias is bound in the module, as an import of it would bind it: the
    # guard reads the name as the object it names, not as its spelling.
    monkeypatch.setattr(gap, "States", WorkflowStateKind, raising=False)
    with pytest.raises(AssertionError):
        test_gap_module_has_only_pure_dependencies_and_no_fallback_state_arm()
