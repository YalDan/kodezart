"""One body decides whether a criterion family is finished (KOD-443).

A fire's finished state is the rollup over its criterion sub-issues, and a
second body deciding the same thing from a criterion's own state is how two
answers to one question come to disagree without anything red saying so.
The rule therefore lives in one function, every reader consults it, and the
results are applied at one named seam.

Nothing here is listed by hand that the tree can be asked for.  The closed
kinds are read off the vocabulary's own openness predicate and cross-checked
against the rule's answers, so neither can move without the other saying so;
each owner's module is read off the symbol; the scanned tree is the package
the rule is packaged in.  What IS listed is the exemptions, each with the
reason it is one, and the table is checked against the walk in both
directions, so an exemption for a site that no longer exists is as red as an
unexempted site.

The alias rule of this walk is its own, and narrow by decision: only a
binding whose value IS the member attribute binds an alias for it.  The
stage-move finder beside it answers a different question — which names hold
the member a ``stage=`` keyword is handed, where the value is the member
itself — and its rule reports a name bound to a comparison against the
member, which here would report a body that decided nothing.

A closed kind is found by object after import, tree-wide: a name bound
anywhere to a closed member or to a collection of nothing but closed members,
followed through imports (``HELD_CRITERION_STATE``, ``_CLOSED_STATE_KINDS``);
the vocabulary under an ``as`` import or reached through a module attribute;
any attribute chain ending in a closed member; a comparison of a
``state_kind`` read against a string equal to a closed member's value; and
the openness predicate called, held uncalled, passed on, or imported under
another name.  The narrow textual rules stay beside that reading, so a
planted body that never imports the vocabulary is read too.

Outside this reach, as for every static guard: a value handed across a
function boundary, where the other function is not resolved at this site
(returned from a helper, stored on an object and read elsewhere, or passed
through a container built elsewhere); a name built at run time; and a
binding made only when a function runs (``setattr`` or ``globals()`` inside
a function body).  Outside it too, by the Check's own words: a selection
over the open kinds that decides which criteria a fire works on.
"""

import ast
import inspect
import sys
import textwrap
from pathlib import Path

import pytest

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.core.protocols import TrackerPort
from kodezart.domain.gap import compute_gap, in_gap
from kodezart.domain.issue_tree import SubtreeClosure, open_criteria
from kodezart.types.domain.criteria import TrackerCriterion
from kodezart.types.domain.operation import LifecycleStage
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind, is_open
from tests.domain.test_criterion_cross_off import (
    qualified_names,
    source_tree,
    stage_moves,
)
from tests.fakes import make_tracker_issue
from tests.object_resolution import UNBOUND, denoted, names_of, names_of_tree

#: The package the rule is packaged in, and so the tree it speaks for.
SOURCE = Path(sys.modules[in_gap.__module__].__file__ or "").resolve().parents[1]
#: The four symbols this arithmetic is stated through, each with its owner.
ARITHMETIC = (in_gap, compute_gap, SubtreeClosure, open_criteria)
#: The kinds the vocabulary treats as closed, read off its own predicate.
TERMINAL = frozenset(kind.name for kind in WorkflowStateKind if not is_open(kind))
VOCABULARY = WorkflowStateKind.__name__
OPENNESS = is_open.__name__
#: The closed members themselves, and the values they are persisted as.
CLOSED = frozenset(kind for kind in WorkflowStateKind if not is_open(kind))
CLOSED_VALUES = frozenset(kind.value for kind in CLOSED)
#: The row field a criterion's kind is read from, read off the row itself.
(STATE_FIELD,) = (
    name
    for name, info in TrackerIssue.model_fields.items()
    if info.annotation is WorkflowStateKind
)
CRITERION_LABEL = "criterion"
MODULE_BODY = "<module>"


def module_of(symbol: object) -> str:
    """The module a symbol is declared in, as a path inside the package."""
    return (
        Path(sys.modules[symbol.__module__].__file__ or "")
        .resolve()
        .relative_to(SOURCE)
        .as_posix()
    )


RULE_SITE = f"{module_of(in_gap)}::{in_gap.__qualname__}"
#: Each arithmetic name against the module that owns it, read off the symbol.
OWNERS = {symbol.__name__: symbol.__module__ for symbol in ARITHMETIC}

#: Every body that reads a terminal criterion state and is not the rule, each
#: with the reason it is not the rule's business.
EXEMPT = {
    "domain/issue_tree.py::open_criteria": (
        "the rollup's own arm: a supersession that has not been established is "
        "refused rather than decided, before the rule is consulted (KOD-794)"
    ),
    "types/domain/tracker.py::<module>": (
        "the vocabulary itself: the closed-kind set the openness predicate is "
        "written from decides nothing"
    ),
    "types/domain/tracker.py::is_open": (
        "the vocabulary's own openness predicate over one kind: it decides "
        "nothing about a family, and each body that consults it is reported "
        "where it does"
    ),
    "chains/criteria.py::TrackerCriteria._owed": (
        "selects the criteria this fire works on (the unstarted ones, plus the "
        "ones this fire already crossed off); decides nothing about whether "
        "anything is finished"
    ),
    "services/lane_state_writer.py::TrackerLaneStateWriter._take_back": (
        "the precondition of one write on one criterion: only a criterion the "
        "board still holds finished is moved back, and nothing about whether a "
        "family is finished is decided"
    ),
    "domain/criterion_cross_off.py::<module>": (
        "the vocabulary itself: the constant naming the state a held grading "
        "sits in decides nothing"
    ),
    "chains/audit_evidence.py::AuditEvidenceVerifier._observe": (
        "one claim's admissibility: whether this criterion has made a claim "
        "worth auditing yet, never whether a set is finished"
    ),
    "chains/audit_forge.py::AuditForgeVerifier.observe": (
        "one claim's admissibility, at the forge arm's entry"
    ),
    "chains/audit_sweep.py::AuditReadSweep._observe": (
        "one claim's admissibility, where the sweep selects a claim to read"
    ),
    "chains/audit_sweep.py::AuditReadSweep._observe_forge": (
        "the same admissibility for the forge half of the same sweep"
    ),
    "domain/audit_claims.py::audit_deferral": (
        "one claim's admissibility, named as the deferral it produces"
    ),
    "services/audit_sources.py::AuditSourceReader._criterion": (
        "one claim's admissibility, where the audited criterion is resolved"
    ),
    "types/domain/audit_evidence.py::AuditEvidenceObservation.is_lapse": (
        "one claim's admissibility, on the record that carries the claim"
    ),
    "domain/dispatch.py::clause_open": (
        "selection over issues that are not criteria: a blocker is no criterion "
        "sub-issue and no rollup answers whether one blocks"
    ),
    "domain/topology.py::plan_topology": (
        "the same selection over blockers, while the plan's edges are ordered"
    ),
    "domain/organize.py::organize_gap": (
        "selection over organize children, which are not criterion sub-issues"
    ),
    "services/base_resolver.py::BaseResolver._input_for": (
        "selection over decision records, which are not criterion sub-issues"
    ),
    "services/base_resolver.py::BaseResolver.unrecorded_closed_blockers": (
        "the same selection over blockers, read for the ones nobody recorded"
    ),
    "services/organize_owner.py::OrganizeOwner.run": (
        "selection over organize children, at the heartbeat that walks them"
    ),
    "services/scope_planning.py::read_scope_plan": (
        "selection over dispatch candidates, which are not criterion sub-issues"
    ),
    "chains/criteria.py::TrackerCriteria._finished": (
        "consults the one arithmetic once KOD-453's gap (Canceled and Duplicate "
        "excluded on state alone, KOD-794) is on the union; KOD-443, decided "
        "2026-09-23 09:15 UTC"
    ),
    "domain/mandate_graph.py::structural_write_uncrosses_milestone": (
        "consults the one arithmetic once KOD-453's gap (Canceled and Duplicate "
        "excluded on state alone, KOD-794) is on the union; KOD-443, decided "
        "2026-09-23 09:15 UTC"
    ),
}


def _member(node: ast.AST) -> bool:
    """Whether *node* is the vocabulary's own attribute for a closed kind."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr in TERMINAL
        and isinstance(node.value, ast.Name)
        and node.value.id == VOCABULARY
    )


def _aliases(tree: ast.Module) -> frozenset[str]:
    """Each name this module binds directly to a closed-kind member."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _member(node.value):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and _member(node.value)
            and isinstance(node.target, ast.Name)
        ):
            names.add(node.target.id)
    return frozenset(names)


def _closed(value: object) -> bool:
    """Whether an object is a closed member, or a non-empty collection of
    nothing but closed members."""
    if isinstance(value, WorkflowStateKind):
        return value in CLOSED
    return (
        isinstance(value, set | frozenset | tuple | list)
        and bool(value)
        and all(
            isinstance(member, WorkflowStateKind) and member in CLOSED
            for member in value
        )
    )


def _reads_a_closed_kind(node: ast.AST, names: dict[str, object]) -> bool:
    """Whether *node* reads a closed kind or the openness predicate, by
    object: a name or attribute chain denoting either, however it is bound,
    imported or aliased, and wherever it stands (called, held, or passed)."""
    if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
        return False
    if not isinstance(node, ast.Name | ast.Attribute):
        return False
    found = denoted(node, names)
    return found is not UNBOUND and (found is is_open or _closed(found))


def _state_kind_read(node: ast.expr) -> bool:
    """A read of a row's kind, or of the value it is persisted as."""
    if isinstance(node, ast.Attribute) and node.attr == "value":
        node = node.value
    return isinstance(node, ast.Attribute) and node.attr == STATE_FIELD


def _compares_a_closed_value(node: ast.AST) -> bool:
    """A comparison of a row's kind against a string that IS a closed value."""
    if not isinstance(node, ast.Compare):
        return False
    operands = [node.left, *node.comparators]
    return any(_state_kind_read(operand) for operand in operands) and any(
        isinstance(operand, ast.Constant) and operand.value in CLOSED_VALUES
        for operand in operands
    )


def _calls_openness(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id == OPENNESS) or (
        isinstance(func, ast.Attribute) and func.attr == OPENNESS
    )


def _sites(tree: ast.Module, names: dict[str, object] | None = None) -> frozenset[str]:
    """Every body of *tree* that reads a criterion's finishedness.

    *names* is what the module's names denote; without it they are read
    off the tree's own imports.
    """
    where = qualified_names(tree)
    named = _aliases(tree)
    resolved = names_of_tree(tree) if names is None else names
    return frozenset(
        where.get(id(node), "") or MODULE_BODY
        for node in ast.walk(tree)
        if _member(node)
        or (isinstance(node, ast.Name) and node.id in named)
        or _calls_openness(node)
        or _reads_a_closed_kind(node, resolved)
        or _compares_a_closed_value(node)
    )


def surface(root: Path) -> frozenset[str]:
    """Every such body under *root*, as module::qualname."""
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        text = path.read_text()
        found.update(
            f"{module}::{site}"
            for site in _sites(ast.parse(text), names_of(module, text, root))
        )
    return frozenset(found)


def _declarations(root: Path, names: frozenset[str]) -> dict[str, list[str]]:
    """Each module under *root* declaring one of *names*, by module."""
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        declared = sorted(
            {
                node.name
                for node in ast.walk(ast.parse(path.read_text()))
                if isinstance(
                    node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
                )
                and node.name in names
            }
        )
        if declared:
            found[module] = declared
    return found


def _imports(root: Path, owners: dict[str, str]) -> dict[str, list[str]]:
    """Each import of an owned name that does not name its owning module."""
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        wrong = sorted(
            {
                f"{alias.name} from {node.module}"
                for node in ast.walk(ast.parse(path.read_text()))
                if isinstance(node, ast.ImportFrom)
                for alias in node.names
                if alias.name in owners
                and (node.module != owners[alias.name] or alias.asname == alias.name)
            }
        )
        if wrong:
            found[module] = wrong
    return found


def probe(kind: WorkflowStateKind, *, label: str = CRITERION_LABEL):
    return make_tracker_issue(
        "fire/criterion", state_kind=kind, issue_labels=frozenset({label})
    )


def test_the_scanned_vocabulary_is_the_rules_own_terminal_set():
    """The scanned kinds are the ones the rule itself treats as closed.

    Read twice over: off the openness predicate, and off the rule's answers
    for every member of the vocabulary.  A closed kind is one the rule
    answers "not owed" for, or answers differently for once a supersession
    reference is established; every other kind is owed whatever is
    established.  Neither reading can move without the other saying so.
    """
    assert TERMINAL == {"COMPLETED", "CANCELED", "DUPLICATE"}
    closed = set()
    for kind in WorkflowStateKind:
        owed = in_gap(probe(kind), supersession_ref=None)
        superseded = in_gap(probe(kind), supersession_ref="fire/other")
        if not owed or owed != superseded:
            closed.add(kind.name)
        else:
            assert kind.name not in TERMINAL, kind
    assert closed == TERMINAL


def test_a_name_bound_to_a_comparison_is_not_an_alias_of_the_member():
    """The alias rule is narrow: a bool is not the member under another word.

    A name bound to a comparison against the member holds the answer, not
    the vocabulary, and a body that only reads that answer decided nothing.
    """
    tree = ast.parse(
        f"def observe(row):\n"
        f"    finished = row.state_kind is {VOCABULARY}.COMPLETED\n"
        f"    return finished\n"
    )
    assert _aliases(tree) == frozenset()
    assert _sites(tree) == frozenset({"observe"})
    bound = ast.parse(
        f"def observe(row):\n"
        f"    done = {VOCABULARY}.COMPLETED\n"
        f"    return row.state_kind is done\n"
    )
    assert _aliases(bound) == frozenset({"done"})
    assert _sites(bound) == frozenset({"observe"})


def test_the_closure_arithmetic_is_declared_once_and_only_there():
    """One owner per symbol, and no second declaration of any of the names."""
    assert module_of(in_gap) == module_of(compute_gap) == "domain/gap.py"
    assert (
        module_of(SubtreeClosure) == module_of(open_criteria) == "domain/issue_tree.py"
    )
    names = frozenset(OWNERS)
    places = {symbol.__name__: module_of(symbol) for symbol in ARITHMETIC}
    assert _declarations(SOURCE, names) == {
        module: sorted(name for name, owner in places.items() if owner == module)
        for module in sorted(set(places.values()))
    }


def _imports_of(root: Path, name: str) -> dict[str, str]:
    """Each module importing *name*, with the module it imports it from."""
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == name for alias in node.names
            ):
                found[module] = node.module or ""
    return found


def test_every_closure_reader_imports_the_arithmetic_from_its_owner():
    """Every reader names the owning module, and nobody re-exports a name.

    The composed readers need no listing here: each caller of
    ``read_scope_ready`` imports it from its owner, that module reaches the
    closure from its owner, and the closure reaches the rule from its owner,
    so a new composed reader reaches the same arithmetic with no edit here.
    """
    assert _imports(SOURCE, OWNERS) == {}
    readers = _imports_of(SOURCE, read_scope_ready.__name__)
    assert readers
    assert set(readers.values()) == {read_scope_ready.__module__}
    walker = module_of(read_scope_ready)
    assert _imports_of(SOURCE, SubtreeClosure.__name__)[walker] == (
        SubtreeClosure.__module__
    )
    assert _imports_of(SOURCE, compute_gap.__name__)[module_of(SubtreeClosure)] == (
        compute_gap.__module__
    )


def test_a_re_exported_arithmetic_name_is_reported(tmp_path):
    """An ``X as X`` re-export offers a second module to reach the rule by."""
    (tmp_path / "shim.py").write_text(
        f"from {compute_gap.__module__} import {compute_gap.__name__} as "
        f"{compute_gap.__name__}\n"
    )
    assert _imports(tmp_path, OWNERS) == {
        "shim.py": [f"{compute_gap.__name__} from {compute_gap.__module__}"]
    }


def test_one_body_decides_finishedness_from_a_criterion_state():
    found = surface(SOURCE)
    assert found - frozenset(EXEMPT) == frozenset({RULE_SITE}), sorted(
        found - frozenset(EXEMPT)
    )


def _closed_readings(
    statements: list[ast.stmt], names: dict[str, object]
) -> list[ast.AST]:
    """Every node of *statements* that reads a closed kind or the predicate."""
    named = _aliases(ast.Module(body=statements, type_ignores=[]))
    return [
        node
        for statement in statements
        for node in ast.walk(statement)
        if _member(node)
        or (isinstance(node, ast.Name) and node.id in named)
        or _calls_openness(node)
        or _reads_a_closed_kind(node, names)
        or _compares_a_closed_value(node)
    ]


def rollup_arm(function: ast.FunctionDef) -> tuple[list[ast.stmt], ast.Return]:
    """The refusal of the rollup's exempt arm, and the return after it.

    The refusal is the body up to and including the first ``if`` that
    raises; the rest must be one ``return``.
    """
    body = [
        statement
        for statement in function.body
        if not (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        )
    ]
    refusal_end = next(
        index
        for index, statement in enumerate(body)
        if isinstance(statement, ast.If)
        and any(isinstance(node, ast.Raise) for node in ast.walk(statement))
    )
    (returned,) = body[refusal_end + 1 :]
    assert isinstance(returned, ast.Return)
    return body[: refusal_end + 1], returned


def test_the_rollups_exempt_arm_hands_its_decision_to_the_rule():
    """The exempt arm refuses what the rule cannot answer, then asks the rule.

    Its return is a call of the rule, resolved by object in the module that
    declares it, and a closed kind is read only inside the refusal before
    it: a body that decided the family itself after refusing would be a
    second arithmetic under the arm's exemption.
    """
    module = module_of(open_criteria)
    text = (SOURCE / module).read_text()
    names = names_of(module, text, SOURCE)
    (function,) = ast.parse(textwrap.dedent(inspect.getsource(open_criteria))).body
    assert isinstance(function, ast.FunctionDef)
    refusal, returned = rollup_arm(function)
    assert isinstance(returned.value, ast.Call)
    assert denoted(returned.value.func, names) is compute_gap
    assert _closed_readings(refusal, names)
    assert _closed_readings([returned], names) == []


def test_a_rollup_arm_restating_the_rule_after_its_refusal_is_reported():
    names = names_of_tree(ast.parse(f"from {VOCABULARY_MODULE} import {VOCABULARY}\n"))
    (function,) = ast.parse(
        "def open_criteria(criteria, *, ref):\n"
        f"    if any(c.state_kind is {VOCABULARY}.CANCELED for c in criteria):\n"
        "        raise LookupError(ref)\n"
        "    return tuple(\n"
        f"        c for c in criteria if c.state_kind is not {VOCABULARY}.COMPLETED\n"
        "    )\n"
    ).body
    assert isinstance(function, ast.FunctionDef)
    refusal, returned = rollup_arm(function)
    assert _closed_readings(refusal, names)
    assert _closed_readings([returned], names) != []
    assert isinstance(returned.value, ast.Call)
    assert denoted(returned.value.func, names) is not compute_gap


def test_every_exemption_names_a_site_the_walk_reports():
    """A stale exemption reds: the table cannot outlive the site it excuses."""
    assert frozenset(EXEMPT) <= surface(SOURCE), sorted(
        frozenset(EXEMPT) - surface(SOURCE)
    )


def test_every_exemption_carries_the_reason_it_is_one():
    assert all(reason.strip() for reason in EXEMPT.values())
    assert RULE_SITE not in EXEMPT


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "def finished(criteria):\n"
            "    return not [\n"
            "        row\n"
            "        for row in criteria\n"
            f"        if row.state_kind is not {VOCABULARY}.COMPLETED\n"
            f"        and row.state_kind is not {VOCABULARY}.CANCELED\n"
            "    ]\n",
            id="the-members-themselves",
        ),
        pytest.param(
            "def finished(criteria):\n"
            f"    return not any({OPENNESS}(row.state_kind) for row in criteria)\n",
            id="through-the-openness-predicate",
        ),
    ],
)
def test_a_second_body_deciding_finishedness_is_reported(tmp_path, body):
    (tmp_path / "second.py").write_text(body)
    assert surface(tmp_path) == frozenset({"second.py::finished"})


VOCABULARY_MODULE = WorkflowStateKind.__module__
HELD_MODULE = "kodezart.domain.criterion_cross_off"

#: A second body deciding finishedness, in each spelling of a closed kind the
#: walk resolves, each with the bodies it must report.
CLOSED_KIND_SPELLINGS = {
    "vocabulary-as-import": (
        f"from {VOCABULARY_MODULE} import {VOCABULARY} as Kind\n\n\n"
        "def finished(rows):\n"
        "    return all(c.state_kind is Kind.COMPLETED for c in rows)\n",
        {"finished"},
    ),
    "module-attribute-chain": (
        "from kodezart.types.domain import tracker\n\n\n"
        "def finished(rows):\n"
        "    return all(\n"
        f"        c.state_kind is tracker.{VOCABULARY}.COMPLETED for c in rows\n"
        "    )\n",
        {"finished"},
    ),
    "string-value": (
        "def finished(rows):\n"
        '    return all(c.state_kind == "completed" for c in rows)\n',
        {"finished"},
    ),
    "string-value-on-the-left": (
        "def finished(rows):\n"
        '    return all("canceled" != c.state_kind.value for c in rows)\n',
        {"finished"},
    ),
    "module-alias-read-in-a-body": (
        f"done = {VOCABULARY}.COMPLETED\n\n\n"
        "def finished(row):\n"
        "    return row.state_kind is done\n",
        {MODULE_BODY, "finished"},
    ),
    "imported-held-state": (
        f"from {HELD_MODULE} import HELD_CRITERION_STATE\n\n\n"
        "def finished(rows):\n"
        "    return all(row.state_kind is HELD_CRITERION_STATE for row in rows)\n",
        {"finished"},
    ),
    "imported-held-state-negated": (
        f"from {HELD_MODULE} import HELD_CRITERION_STATE\n\n\n"
        "def finished(criteria):\n"
        "    return not any(\n"
        "        row.state_kind is not HELD_CRITERION_STATE for row in criteria\n"
        "    )\n",
        {"finished"},
    ),
    "imported-closed-set": (
        f"from {VOCABULARY_MODULE} import _CLOSED_STATE_KINDS\n\n\n"
        "def finished(rows):\n"
        "    return all(row.state_kind in _CLOSED_STATE_KINDS for row in rows)\n",
        {"finished"},
    ),
    "vocabulary-as-import-in-a-display": (
        f"from {VOCABULARY_MODULE} import {VOCABULARY} as Kind\n\n\n"
        "def finished(rows):\n"
        "    return all(\n"
        "        c.state_kind in (Kind.COMPLETED, Kind.CANCELED) for c in rows\n"
        "    )\n",
        {"finished"},
    ),
    "openness-passed-to-map": (
        f"from {VOCABULARY_MODULE} import {OPENNESS}\n\n\n"
        "def finished(rows):\n"
        f"    return not any(map({OPENNESS}, (r.state_kind for r in rows)))\n",
        {"finished"},
    ),
    "openness-held-uncalled": (
        f"from {VOCABULARY_MODULE} import {OPENNESS}\n\n\n"
        "def finished(rows):\n"
        f"    check = {OPENNESS}\n"
        "    return not any(check(r.state_kind) for r in rows)\n",
        {"finished"},
    ),
    "openness-imported-as": (
        f"from {VOCABULARY_MODULE} import {OPENNESS} as still_open\n\n\n"
        "def finished(rows):\n"
        "    return not any(still_open(r.state_kind) for r in rows)\n",
        {"finished"},
    ),
    "closure-by-string": (
        "class SubtreeClosure:\n"
        "    def is_closed(self, key):\n"
        "        return all(\n"
        '            row.state_kind == "completed" for row in self.roster(key)\n'
        "        )\n",
        {"SubtreeClosure.is_closed"},
    ),
    "closure-by-held-state": (
        f"from {HELD_MODULE} import HELD_CRITERION_STATE\n\n\n"
        "class SubtreeClosure:\n"
        "    def is_closed(self, key):\n"
        "        return all(\n"
        "            row.state_kind is HELD_CRITERION_STATE\n"
        "            for row in self.roster(key)\n"
        "        )\n",
        {"SubtreeClosure.is_closed"},
    ),
}


@pytest.mark.parametrize("spelling", sorted(CLOSED_KIND_SPELLINGS))
def test_a_closed_kind_is_read_however_it_is_spelled(tmp_path, spelling):
    body, sites = CLOSED_KIND_SPELLINGS[spelling]
    (tmp_path / "second.py").write_text(body)
    assert surface(tmp_path) == frozenset(f"second.py::{site}" for site in sites)


def test_the_closed_kinds_and_their_values_are_read_off_the_vocabulary():
    assert {kind.name for kind in CLOSED} == TERMINAL
    assert CLOSED_VALUES == {kind.value for kind in CLOSED}
    assert STATE_FIELD


def test_an_open_kind_or_a_mixed_set_is_not_a_closed_reading(tmp_path):
    (tmp_path / "open.py").write_text(
        f"from {VOCABULARY_MODULE} import {VOCABULARY}\n\n"
        f"MIXED = frozenset({{{VOCABULARY}.UNSTARTED, {VOCABULARY}.STARTED}})\n\n\n"
        "def owed(rows):\n"
        f"    return [r for r in rows if r.state_kind is {VOCABULARY}.UNSTARTED]\n"
        "\n\n"
        "def fresh(rows):\n"
        "    return [r for r in rows if r.state_kind in MIXED]\n"
    )
    assert surface(tmp_path) == frozenset()


def test_a_value_handed_across_a_function_boundary_is_not_seen(tmp_path):
    """The stated limit: a closed kind returned from a helper elsewhere."""
    (tmp_path / "second.py").write_text(
        "def finished(rows, kinds):\n"
        "    closed = kinds.closed()\n"
        "    return all(row.state_kind in closed for row in rows)\n"
    )
    assert surface(tmp_path) == frozenset()


def test_a_name_built_at_run_time_is_not_seen(tmp_path):
    """The stated limit: a member reached by a name composed when it runs."""
    (tmp_path / "second.py").write_text(
        f"from {VOCABULARY_MODULE} import {VOCABULARY}\n\n\n"
        "def finished(rows):\n"
        f"    done = getattr({VOCABULARY}, 'COMP' + 'LETED')\n"
        "    return all(row.state_kind is done for row in rows)\n"
    )
    assert surface(tmp_path) == frozenset()


def test_a_binding_made_only_when_a_function_runs_is_not_seen(tmp_path):
    """The stated limit: a closed member bound through ``globals()``."""
    (tmp_path / "second.py").write_text(
        f"from {HELD_MODULE} import HELD_CRITERION_STATE\n\n\n"
        "def bind():\n"
        "    globals()['DONE_KIND'] = HELD_CRITERION_STATE\n\n\n"
        "def finished(rows):\n"
        "    return all(row.state_kind is DONE_KIND for row in rows)\n"
    )
    assert surface(tmp_path) == frozenset({"second.py::bind"})


#: The stage a finished criterion is moved to, and the write that moves it,
#: each read off the vocabulary and the port rather than spelled here.
DONE = LifecycleStage.DONE.name
STATE_MOVE = TrackerPort.set_workflow_state.__name__


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next(
        (word.value for word in call.keywords if word.arg == name),
        None,
    )


def _root_name(expression: ast.expr) -> str | None:
    cursor: ast.expr = expression
    while isinstance(cursor, ast.Attribute | ast.Subscript):
        cursor = cursor.value
    return cursor.id if isinstance(cursor, ast.Name) else None


def finished_identities(sources: dict[str, str], *, method: str) -> dict[str, str]:
    """What each move into the finished state addresses, by annotation.

    The moving functions are the ones the stage-move finder reports, and
    each is re-walked for the call itself, so what this reads is the call
    that finder found rather than a second search for one.
    """
    addressed: dict[str, str] = {}
    for module, text in sorted(sources.items()):
        tree = ast.parse(text)
        movers = frozenset(stage_moves(tree, method=method, stage=DONE))
        if not movers:
            continue
        where = qualified_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            function = where[id(node)]
            if function not in movers:
                continue
            annotations = {
                argument.arg: (
                    ""
                    if argument.annotation is None
                    else ast.unparse(argument.annotation)
                )
                for argument in [*node.args.args, *node.args.kwonlyargs]
            }
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                stage = _keyword(call, "stage")
                key = _keyword(call, "issue_key")
                if stage is None or key is None:
                    continue
                root = _root_name(key)
                addressed[f"{module}::{function}"] = annotations.get(root or "", "")
    return addressed


def test_the_finished_state_is_written_only_onto_a_criterion_identity():
    """The one move into the finished state addresses a criterion, by type.

    A sibling guard says how many functions make the move; this says what
    the move addresses: the identity it names comes from a parameter the
    function declares as the criterion record, so a fire key or an issue key
    cannot ride through the same write.
    """
    assert finished_identities(source_tree(), method=STATE_MOVE) == {
        "services/lane_state_writer.py::TrackerLaneStateWriter._write_one": (
            TrackerCriterion.__name__
        )
    }


MOVER = (
    "async def move(self, *, {parameter}) -> None:\n"
    f"    await self._tracker.{STATE_MOVE}(\n"
    f"        issue_key={{addressed}}, stage=LifecycleStage.{DONE}\n"
    "    )\n"
)


@pytest.mark.parametrize(
    ("parameter", "addressed", "annotation"),
    [
        pytest.param(
            f"criterion: {TrackerCriterion.__name__}",
            "criterion.id",
            TrackerCriterion.__name__,
            id="a-criterion-record",
        ),
        pytest.param("lane: LaneBinding", "lane.lane_key", "LaneBinding", id="a-lane"),
        pytest.param(
            "issue: TrackerIssue", "issue.issue_key", "TrackerIssue", id="an-issue"
        ),
        pytest.param("key: str", '"KOD-1"', "", id="a-written-down-key"),
    ],
)
def test_what_a_planted_move_addresses_is_read_off_its_own_parameter(
    parameter, addressed, annotation
):
    sources = {
        "chains/planted.py": MOVER.format(parameter=parameter, addressed=addressed)
    }
    assert finished_identities(sources, method=STATE_MOVE) == {
        "chains/planted.py::move": annotation
    }
