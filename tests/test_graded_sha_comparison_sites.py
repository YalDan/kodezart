"""One body in the shipped sources compares a graded sha with a head sha (KOD-413).

Two readers weighing the same two revisions is how a lapse comes to mean one
thing on a compliance mark and another on a lane check: one of them
eventually grows an ancestry test, a prefix match or a null case, and
nothing red says so.  The rule therefore lives in exactly one function and
every other reader consults it, which is what this guard keeps true.

The identity a grading's revision is recorded under is read off the Evidence
record's own fields; the rule's module and name are read off the rule itself,
so renaming either moves the guard with it; the scanned tree is the package
the rule is packaged in.  Two things ARE listed by hand: the exemptions, each
with the reason it is one, checked against the walk in both directions so an
exemption for a site that no longer exists is as red as an unexempted site;
and :data:`WEIGHINGS`, the calls that weigh two revisions inside themselves,
which says below what it is named from and why it cannot be derived.

The walk is textual and executes nothing, which is what lets it speak for
the whole tree rather than for the paths a fixture happens to reach.  Its
blind spots, which review has to read from the code instead: a helper under
a name :data:`WEIGHINGS` does not carry that compares the two revisions
inside itself rather than at the call site, and a revision reached by
``getattr`` or by any other name composed at run time.
"""

import ast
import sys
from inspect import signature
from pathlib import Path

import pytest

from kodezart.domain.lapse import graded_state
from kodezart.types.domain.criterion_evidence import CriterionEvidence

#: The package the rule is packaged in, and so the tree it speaks for.
SOURCE = Path(sys.modules[graded_state.__module__].__file__ or "").resolve().parents[1]
#: Where the rule is written, and the name it is written under.
RULE_MODULE = (
    Path(sys.modules[graded_state.__module__].__file__ or "")
    .resolve()
    .relative_to(SOURCE)
    .as_posix()
)
RULE = graded_state.__name__
RULE_SITE = f"{RULE_MODULE}::{RULE}"

#: The identity a grading's revision is recorded under: the one name the
#: Evidence record and the rule both spell, so a rename of either is a
#: rename of both or this guard says so.
GRADED_IDENTITIES = frozenset(CriterionEvidence.model_fields) & frozenset(
    signature(graded_state).parameters
)
GRADED = min(GRADED_IDENTITIES, default="")

#: The comparisons a reader can weigh two revisions with.
COMPARISONS = (ast.Eq, ast.NotEq, ast.Is, ast.IsNot, ast.In, ast.NotIn)
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
LITERALS = (ast.Tuple, ast.List, ast.Set)

#: The calls that weigh two revisions against each other inside themselves,
#: by the attribute they are called under.
#:
#: This set is NAMED, not derived, and it is the one hand-written surface in
#: this guard.  There is nothing here to derive it from: the string methods
#: belong to ``str`` and reach a revision through whatever a reader bound it
#: to, and no port marks a method as revision-weighing anywhere a textual
#: walk could read.  So it is listed, from two places a reader can check it
#: against:
#:
#: * ``str.startswith`` and ``str.endswith`` — a prefix match, which is one of
#:   the drifts ``domain/lapse.py``'s own docstring says the rule exists to
#:   prevent, and which reads as a lapse test without ever comparing;
#: * every method of the ``GitService`` port that takes two revisions and
#:   answers about the pair: ``is_ancestor`` (whether one is reachable from
#:   the other) and ``diff_summary`` (what moved between them).  The port's
#:   other revision methods take one revision — ``reset_hard``, ``tree_of``,
#:   ``create_worktree``, ``merge_scratch_head``, ``remote_branch_sha`` — and
#:   ``commit_tree`` writes a commit from a tree and a parent rather than
#:   weighing a pair, so none of them can be a second reading of a lapse.
#:
#: What naming it costs: the same arithmetic under some other name is still
#: unseen, which is the blind spot the module docstring keeps.
WEIGHINGS = frozenset({"startswith", "endswith", "is_ancestor", "diff_summary"})

#: Every site that compares a graded sha with something and is not the rule,
#: each with the reason it is not the rule's business.  A revision weighed
#: against ITSELF, or against a second recorded revision, is provenance:
#: it answers whether a reading is about the commit it says it is, which is
#: a different question from whether that reading still stands.
EXEMPT = {
    "types/domain/audit_runtime.py::AuditForgePublication."
    "_recorded_revision_matches_report": (
        "head against head: the forge report's own provenance, not the head now"
    ),
    "types/domain/audit_forge.py::AuditForgeObservation."
    "verdict_has_exact_observation": (
        "the checked commit against the graded commit: the forge reading's "
        "provenance, not the head now"
    ),
    "chains/audit_forge.py::AuditForgeVerifier._checked_snapshot": (
        "the same provenance reading, where the observation is assembled"
    ),
    "chains/audit_detection_removal.py::DetectorRemovalVerifier."
    "_require_removed_quote": (
        "a baseline commit against the graded commit, never against a head"
    ),
    "chains/audit_evidence.py::AuditEvidenceVerifier._head.observe": (
        "self-resolution: a recorded revision must resolve to itself; and the "
        "ancestry weighing beside it, which asks whether the graded commit "
        "sits on the recorded branch at all — a miss there is a refusal to "
        "read the record, not a reading that the grading stopped standing"
    ),
    "services/audit_sources.py::AuditSourceReader.read.resolve": (
        "the same self-resolution, over both revisions as one loop, and the "
        "same ancestry weighing of the graded commit against the branch"
    ),
    "services/assertion_drift.py::AssertionDriftDetector.compare": (
        "the same self-resolution, and the shape of the graded reference itself"
    ),
    "chains/audit_overclaim.py::AuditOverclaimVerifier._adoption": (
        "membership: whether an adopted source sha is one of the audited pair"
    ),
    "domain/audit_claims.py::evidence_row_history": (
        "not a revision comparison at all: an event is selected by the subject "
        "it is keyed to, and a grading naming no commit is dropped"
    ),
    "domain/audit_claims.py::restamp_verdict": (
        "the last recorded grading against the row's own recorded commit: "
        "whether a restamp names the grading that actually last ran, never the "
        "head now"
    ),
    "types/domain/audit_evidence.py::AuditRestampTrace."
    "verdict_follows_the_recorded_history": (
        "the same reading, refused at construction so the wrong verdict cannot "
        "be published; still recorded against recorded"
    ),
}


def _is_rule_call(node: ast.AST) -> bool:
    """Whether this expression is the rule being consulted.

    A reader comparing the rule's ANSWER with a member of its reading is
    doing what this guard exists to make it do, so the arguments it hands
    the rule are not a comparison of its own.
    """
    if not isinstance(node, ast.Call):
        return False
    called = node.func
    if isinstance(called, ast.Attribute):
        return called.attr == RULE
    return isinstance(called, ast.Name) and called.id == RULE


def _strip(node: ast.expr) -> ast.expr:
    return node.value if isinstance(node, ast.Await) else node


def _is_graded_key(node: ast.Subscript) -> bool:
    """Whether *node* reads the graded identity out of a serialised record.

    A record dumped to a mapping spells the same field as a constant key, so
    ``record[GRADED]`` carries exactly the value ``record.GRADED`` does. The
    key is compared with the derived identity rather than with a spelling
    repeated here, so renaming the field on the record moves this arm too.
    """
    key = node.slice
    return isinstance(key, ast.Constant) and key.value == GRADED


def _names_the_graded_sha(node: ast.AST) -> bool:
    """Whether this expression spells the graded identity itself."""
    stack: list[ast.AST] = [node]
    while stack:
        current = stack.pop()
        if _is_rule_call(current):
            continue
        if isinstance(current, ast.Attribute) and current.attr == GRADED:
            return True
        if isinstance(current, ast.Name) and current.id == GRADED:
            return True
        if isinstance(current, ast.Subscript) and _is_graded_key(current):
            return True
        stack.extend(ast.iter_child_nodes(current))
    return False


def _is_direct_read(node: ast.expr, aliases: frozenset[str] = frozenset()) -> bool:
    """Whether *node* IS the graded identity, or a literal collecting it.

    The identity itself, the same field read off a serialised record, a name
    that already stands for it, or a container literal collecting any of
    those. What is deliberately NOT followed is a local the identity was
    handed to as one argument among many: that local carries a value of some
    other kind, and following it would make the walk report every reader
    downstream of a prompt or a report that happens to quote the sha.
    """
    value = _strip(node)
    if isinstance(value, ast.Attribute):
        return value.attr == GRADED
    if isinstance(value, ast.Name):
        return value.id == GRADED or value.id in aliases
    if isinstance(value, ast.Subscript):
        return _is_graded_key(value)
    if isinstance(value, LITERALS):
        return any(_is_direct_read(element, aliases) for element in value.elts)
    return False


def _bindings(scope: ast.AST) -> list[tuple[list[ast.expr], ast.expr]]:
    """Every name this scope binds, with what it binds it from."""
    found: list[tuple[list[ast.expr], ast.expr]] = []
    stack: list[ast.AST] = [scope]
    while stack:
        current = stack.pop()
        for child in ast.iter_child_nodes(current):
            if isinstance(child, ast.Assign):
                found.append((list(child.targets), child.value))
            elif isinstance(child, (ast.AnnAssign, ast.AugAssign)):
                if child.value is not None:
                    found.append(([child.target], child.value))
            elif isinstance(child, (ast.For, ast.AsyncFor, ast.comprehension)):
                found.append(([child.target], child.iter))
            if not isinstance(child, SCOPES):
                stack.append(child)
    return found


def _aliases(scope: ast.AST, inherited: frozenset[str]) -> frozenset[str]:
    """The names that stand for the graded identity inside *scope*.

    Grown to a fixed point: a name bound from another name that already
    stands for the identity stands for it too, because a plain rebinding
    carries the same value rather than a value of another kind. A single pass
    would also miss an alias bound before the name it is bound from.
    """
    aliases = set(inherited)
    bindings = _bindings(scope)
    changed = True
    while changed:
        previous = set(aliases)
        for targets, value in bindings:
            if not _is_direct_read(value, frozenset(aliases)):
                continue
            for target in targets:
                aliases.update(
                    node.id for node in ast.walk(target) if isinstance(node, ast.Name)
                )
        changed = aliases != previous
    return frozenset(aliases)


def _reads_the_graded_sha(node: ast.expr, aliases: frozenset[str]) -> bool:
    if _names_the_graded_sha(node):
        return True
    stack: list[ast.AST] = [node]
    while stack:
        current = stack.pop()
        if _is_rule_call(current):
            continue
        if isinstance(current, ast.Name) and current.id in aliases:
            return True
        stack.extend(ast.iter_child_nodes(current))
    return False


def _compares_the_graded_sha(node: ast.AST, aliases: frozenset[str]) -> bool:
    """Whether *node* weighs the graded identity with a comparison operator."""
    return (
        isinstance(node, ast.Compare)
        and any(isinstance(op, COMPARISONS) for op in node.ops)
        and any(
            _reads_the_graded_sha(operand, aliases)
            for operand in (node.left, *node.comparators)
        )
    )


def _weighs_the_graded_sha_in_a_call(node: ast.AST, aliases: frozenset[str]) -> bool:
    """Whether *node* hands the graded identity to a named revision weighing.

    The receiver counts alongside the arguments, positional and keyword alike:
    ``head.startswith(graded)`` and ``graded.startswith(head)`` are one
    reading spelled two ways, and a port called by keyword is the same call as
    one called by position.
    """
    if not isinstance(node, ast.Call):
        return False
    called = node.func
    if not isinstance(called, ast.Attribute) or called.attr not in WEIGHINGS:
        return False
    operands = (called.value, *node.args, *(word.value for word in node.keywords))
    return any(_reads_the_graded_sha(operand, aliases) for operand in operands)


def _sites(tree: ast.AST) -> frozenset[str]:
    """Each scope that weighs the graded identity against something, once.

    Two shapes, because a reader has two ways to reach the same reading: a
    comparison operator over the two revisions, and a call that performs the
    weighing inside itself under one of the names :data:`WEIGHINGS` lists.
    """
    found: set[str] = set()

    def visit(scope: ast.AST, label: str | None, inherited: frozenset[str]) -> None:
        aliases = _aliases(scope, inherited)
        stack: list[ast.AST] = [scope]
        while stack:
            current = stack.pop()
            for child in ast.iter_child_nodes(current):
                if isinstance(child, SCOPES):
                    visit(
                        child,
                        child.name if label is None else f"{label}.{child.name}",
                        aliases,
                    )
                    continue
                if _compares_the_graded_sha(
                    child, aliases
                ) or _weighs_the_graded_sha_in_a_call(child, aliases):
                    found.add(label if label is not None else f"line {child.lineno}")
                stack.append(child)

    visit(tree, None, frozenset())
    return frozenset(found)


def surface(root: Path) -> frozenset[str]:
    """Every site in *root* that compares a graded sha, as module::qualname."""
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        found.update(
            f"{module}::{site}" for site in _sites(ast.parse(path.read_text()))
        )
    return frozenset(found)


def test_the_graded_identity_the_guard_scans_for_is_the_evidence_records_own_field():
    """The walk keys on the record's field, not on a spelling repeated here."""
    assert len(GRADED_IDENTITIES) == 1
    assert GRADED in CriterionEvidence.model_fields
    assert GRADED in signature(graded_state).parameters


def test_the_rule_the_guard_permits_is_the_one_the_sources_import():
    """The permitted site is derived, so a rename carries the guard with it."""
    assert (SOURCE / RULE_MODULE).is_file()
    assert RULE_MODULE.startswith("domain/")
    assert RULE in (SOURCE / RULE_MODULE).read_text()


def test_the_sources_compare_a_graded_sha_with_a_head_sha_in_one_body_only():
    found = surface(SOURCE)
    assert found - frozenset(EXEMPT) == frozenset({RULE_SITE}), sorted(
        found - frozenset(EXEMPT)
    )


def test_every_exemption_names_a_site_the_walk_actually_reports():
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
            "def lapsed(evidence, head_sha):\n"
            "    return evidence.graded_sha != head_sha\n",
            id="attribute-operand",
        ),
        pytest.param(
            "def lapsed(graded_sha, head_sha):\n    return graded_sha == head_sha\n",
            id="parameter-operand",
        ),
        pytest.param(
            "def lapsed(evidence, head_sha):\n"
            "    taken = evidence.graded_sha\n"
            "    return taken is not head_sha\n",
            id="aliased-operand",
        ),
        pytest.param(
            "def lapsed(evidence, head_sha):\n"
            f"    recorded = evidence.{GRADED}\n"
            "    taken = recorded\n"
            "    return taken is not head_sha\n",
            id="twice-aliased-operand",
        ),
        pytest.param(
            "def lapsed(evidence, head_sha):\n"
            f"    return evidence.model_dump()[{GRADED!r}] != head_sha\n",
            id="serialised-record-subscript",
        ),
        pytest.param(
            "def lapsed(evidence, head_sha, resolve):\n"
            "    for sha in (evidence.graded_sha, head_sha):\n"
            "        if resolve(sha) != sha:\n"
            "            return True\n"
            "    return False\n",
            id="loop-bound-operand",
        ),
        pytest.param(
            "def lapsed(pair, evidence, head_sha):\n"
            "    return pair.source_sha in {evidence.graded_sha, head_sha}\n",
            id="container-literal-operand",
        ),
        pytest.param(
            "class Record:\n"
            "    def lapsed(self):\n"
            "        return self.graded_sha != self.head_sha\n",
            id="method-operand",
        ),
        pytest.param(
            "def lapsed(evidence, head_sha):\n"
            "    return not head_sha.startswith(evidence.graded_sha)\n",
            id="prefix-match-argument",
        ),
        pytest.param(
            "def lapsed(evidence, head_sha):\n"
            "    return not evidence.graded_sha.endswith(head_sha)\n",
            id="prefix-match-receiver",
        ),
        pytest.param(
            "def lapsed(git, evidence, head_sha):\n"
            "    return not git.is_ancestor(evidence.graded_sha, head_sha)\n",
            id="ancestry-call",
        ),
        pytest.param(
            "async def lapsed(git, repo, evidence, head_sha):\n"
            "    moved = await git.diff_summary(\n"
            "        cwd=repo, base_ref=evidence.graded_sha, head_ref=head_sha\n"
            "    )\n"
            "    return bool(moved.file_paths)\n",
            id="digest-call-by-keyword",
        ),
    ],
)
def test_every_spelling_that_compares_the_graded_sha_is_reported(body):
    assert _sites(ast.parse(body))


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "def pinned(git, repo, evidence):\n"
            "    return git.reset_hard(cwd=repo, ref=evidence.graded_sha)\n",
            id="one-revision-port-call",
        ),
        pytest.param(
            "def audited(evidence, head_sha):\n"
            "    return frozenset({evidence.graded_sha, head_sha})\n",
            id="set-construction",
        ),
        pytest.param(
            "def resolved(source, repo, evidence):\n"
            "    return source.resolve_commit(cwd=repo, ref=evidence.graded_sha)\n",
            id="self-resolution-call",
        ),
        pytest.param(
            "def stamp(evidence, head_sha):\n"
            "    return Report(graded_sha=evidence.graded_sha, head_sha=head_sha)\n",
            id="record-construction",
        ),
        pytest.param(
            "def other(evidence):\n"
            "    return evidence.recorded_sha != evidence.checked_sha\n",
            id="two-other-revisions",
        ),
        pytest.param(
            "def reported(prompts, evidence, head_sha, judge):\n"
            "    prompt = prompts.render({'graded_sha': evidence.graded_sha})\n"
            "    judgment = judge(prompt=prompt, head_sha=head_sha)\n"
            "    return judgment.criterion_key != evidence.criterion_key\n",
            id="quoted-into-a-session",
        ),
    ],
)
def test_naming_the_graded_sha_without_comparing_it_is_not_a_site(body):
    assert _sites(ast.parse(body)) == frozenset()


def test_a_reader_consulting_the_rule_is_not_a_site_and_its_own_arithmetic_is():
    """Reading the rule's answer is the point; comparing the shas is not."""
    consumer = (
        "def is_lapse(evidence, head_sha):\n"
        f"    return {RULE}("
        f"{GRADED}=evidence.{GRADED}, head_sha=head_sha) is GradedState.lapsed\n"
    )
    assert _sites(ast.parse(consumer)) == frozenset()
    rolled = consumer + (
        f"def also(evidence, head_sha):\n    return evidence.{GRADED} != head_sha\n"
    )
    assert _sites(ast.parse(rolled)) == frozenset({"also"})


def test_one_body_comparing_twice_is_one_site_and_two_bodies_are_two():
    source = (
        "def lapsed(evidence, head_sha, other_head_sha):\n"
        "    if evidence.graded_sha == head_sha:\n"
        "        return False\n"
        "    return evidence.graded_sha != other_head_sha\n"
        "def stale(evidence, head_sha):\n"
        "    return evidence.graded_sha != head_sha\n"
    )
    assert _sites(ast.parse(source)) == frozenset({"lapsed", "stale"})


def test_a_second_module_performing_the_comparison_is_reported(tmp_path):
    """The injected reader is reported, and no exemption covers it."""
    rule = tmp_path / RULE_MODULE
    rule.parent.mkdir(parents=True, exist_ok=True)
    rule.write_text((SOURCE / RULE_MODULE).read_text())
    assert surface(tmp_path) == frozenset({RULE_SITE})

    (tmp_path / "reader.py").write_text(
        "def lapsed(evidence, head_sha):\n    return evidence.graded_sha != head_sha\n"
    )
    found = surface(tmp_path)
    assert found - frozenset(EXEMPT) == frozenset({RULE_SITE, "reader.py::lapsed"})


#: Every body that consults the rule, each with the reading it takes from it.
#: One body per package: a package asking twice is two readers of one answer,
#: which is how the second of them starts qualifying it.
CALLERS = {
    "domain/lapse.py::held_standing": (
        "the lane arm's one reader: the partition the loop's next iteration "
        "is dispatched from"
    ),
    "types/domain/audit_evidence.py::AuditEvidenceObservation.is_lapse": (
        "the audit lane's observation of a finished claim's recorded grading"
    ),
    "chains/audit_evidence.py::AuditEvidenceVerifier._observe": (
        "whether a finished claim is still worth verifying afresh at the head"
    ),
}


def _calls(tree: ast.AST) -> frozenset[str]:
    """Each scope that consults the rule, once per scope."""
    found: set[str] = set()

    def walk(node: ast.AST, label: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            here = label
            if isinstance(child, SCOPES):
                here = child.name if label is None else f"{label}.{child.name}"
            elif label is None:
                here = f"line {child.lineno}"
            if _is_rule_call(child):
                found.add(here if here is not None else f"line {child.lineno}")
            walk(child, here)

    walk(tree, None)
    return frozenset(found)


def callers(root: Path) -> frozenset[str]:
    """Every body in *root* that consults the rule, as module::qualname."""
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        found.update(
            f"{module}::{site}" for site in _calls(ast.parse(path.read_text()))
        )
    return frozenset(found)


def test_every_reader_of_the_rule_is_named_with_the_reading_it_takes():
    """A new consumer is a decision, so it arrives here or it reds."""
    assert callers(SOURCE) == frozenset(CALLERS), sorted(
        callers(SOURCE) ^ frozenset(CALLERS)
    )
    assert all(reason.strip() for reason in CALLERS.values())


def test_the_rule_is_consulted_from_one_body_in_each_package_that_reads_it():
    """One reader per package, so the answer is not qualified twice over."""
    packages: dict[str, list[str]] = {}
    for site in sorted(callers(SOURCE)):
        module, _, _ = site.partition("::")
        packages.setdefault(module.rpartition("/")[0], []).append(site)
    assert packages
    assert all(len(sites) == 1 for sites in packages.values()), packages


def test_a_second_reader_in_one_package_is_reported(tmp_path):
    source = (
        "def one(evidence, head_sha):\n"
        f"    return {RULE}({GRADED}=evidence.{GRADED}, head_sha=head_sha)\n"
        "def two(evidence, head_sha):\n"
        f"    return {RULE}({GRADED}=evidence.{GRADED}, head_sha=head_sha)\n"
    )
    assert _calls(ast.parse(source)) == frozenset({"one", "two"})
