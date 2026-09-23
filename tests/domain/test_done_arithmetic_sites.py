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

The walk is textual and executes nothing.  Its blind spots, which review has
to read from the code instead: a state kind reached through ``getattr`` or
any other name composed at run time, and a helper that decides finishedness
inside itself and is called somewhere this walk therefore leaves alone.
"""

import ast
import sys
from pathlib import Path

import pytest

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.core.protocols import TrackerPort
from kodezart.domain.gap import compute_gap, in_gap
from kodezart.domain.issue_tree import SubtreeClosure, open_criteria
from kodezart.types.domain.criteria import TrackerCriterion
from kodezart.types.domain.operation import LifecycleStage
from kodezart.types.domain.tracker import WorkflowStateKind, is_open
from tests.domain.test_criterion_cross_off import (
    qualified_names,
    source_tree,
    stage_moves,
)
from tests.fakes import make_tracker_issue

#: The package the rule is packaged in, and so the tree it speaks for.
SOURCE = Path(sys.modules[in_gap.__module__].__file__ or "").resolve().parents[1]
#: The four symbols this arithmetic is stated through, each with its owner.
ARITHMETIC = (in_gap, compute_gap, SubtreeClosure, open_criteria)
#: The kinds the vocabulary treats as closed, read off its own predicate.
TERMINAL = frozenset(kind.name for kind in WorkflowStateKind if not is_open(kind))
VOCABULARY = WorkflowStateKind.__name__
OPENNESS = is_open.__name__
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


def _calls_openness(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id == OPENNESS) or (
        isinstance(func, ast.Attribute) and func.attr == OPENNESS
    )


def _sites(tree: ast.Module) -> frozenset[str]:
    """Every body of *tree* that reads a criterion's finishedness."""
    where = qualified_names(tree)
    named = _aliases(tree)
    return frozenset(
        where.get(id(node), "") or MODULE_BODY
        for node in ast.walk(tree)
        if _member(node)
        or (isinstance(node, ast.Name) and node.id in named)
        or _calls_openness(node)
    )


def surface(root: Path) -> frozenset[str]:
    """Every such body under *root*, as module::qualname."""
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        found.update(
            f"{module}::{site}" for site in _sites(ast.parse(path.read_text()))
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
            assert owed and superseded, kind
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
    assert surface(tmp_path) - frozenset(EXEMPT) != frozenset({RULE_SITE})


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
