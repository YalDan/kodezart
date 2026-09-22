"""Every body that reads a graded sha is named, so one body weighs it (KOD-413).

Two readers weighing the same two revisions is how a lapse comes to mean one
thing on a compliance mark and another on a lane check: one of them
eventually grows an ancestry test, a prefix match or a null case, and
nothing red says so.  The rule therefore lives in exactly one function and
every other reader consults it, which is what this guard keeps true.

What is scanned is the OPERAND, not the operator.  You cannot compare a
value you do not read, so this guard censuses the bodies that read the
graded identity at all — an attribute, a serialised key, a parameter, an
unpack, a comprehension target, a name bound from any of those to a fixed
point, a value handed to a call — and requires every one of them to be
registered with the reason it reads it.  A comparison is then reachable only
from a body this file names, whichever way it is spelled: an operator, a
comparison dunder, the ``operator`` module, a prefix match, a helper called
through a receiver or under a bare name, a port that weighs the pair inside
itself.  None of those spellings appears in this file, which is the point:
there is no list of them to walk past.

The identity a grading's revision is recorded under is read off the Evidence
record's own fields; the rule's module and name are read off the rule itself,
so renaming either moves the guard with it; the scanned tree is the package
the rule is packaged in.  One thing IS listed by hand: the register, each row
with the reason that body reads the identity, checked against the census in
both directions so a row for a body that no longer reads it is as red as a
body that reads it and is not registered.

The walk is textual and executes nothing, which is what lets it speak for
the whole tree rather than for the paths a fixture happens to reach.  Its
boundaries, which review has to read from the code instead:

* a revision reached by ``getattr`` or by any other name composed at run
  time is invisible, here as anywhere a textual walk is used;
* a body that takes the identity as a parameter spelled otherwise reads no
  identity of its own — its CALLER hands it over and is censused, which is
  what makes "reachable only from a named body" the claim this guard keeps,
  and it is read from both sides below;
* a read at class or module level is where the identity is DECLARED (a field
  on a record, a key in a template) rather than a body that weighs it, so
  the census is over function and method bodies only.
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

#: The bodies the census is over, and the scopes a body's name is built from.
BODIES = (ast.FunctionDef, ast.AsyncFunctionDef)
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
#: Containers that hold their elements without changing them.
LITERALS = (ast.Tuple, ast.List, ast.Set)
COMPREHENSIONS = (ast.SetComp, ast.ListComp, ast.GeneratorExp)

#: Every body in the sources that reads the graded identity, and what it
#: reads it FOR.  The rule is not here: weighing the pair is its whole job.
#:
#: A revision recorded against ITSELF, or against a second recorded
#: revision, is provenance: it answers whether a reading is about the commit
#: it says it is, which is a different question from whether that reading
#: still stands.  A revision handed to a port as a ref, quoted into a
#: session's prompt, recorded onto a row or carried into the rule's own
#: arguments weighs nothing at all.
REGISTERED = {
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
    "chains/audit_forge.py::AuditForgeVerifier._forge": (
        "a ref, not an operand: the forge checks are awaited AT the recorded "
        "commit, and what comes back is read against the roster, never "
        "against a head"
    ),
    "chains/audit_forge.py::AuditForgeVerifier._forge.result": (
        "the same body's reply, with the recorded commit quoted into the "
        "reason it reports"
    ),
    "chains/audit_detection_removal.py::DetectorRemovalVerifier."
    "_require_removed_quote": (
        "a baseline commit against the graded commit, never against a head"
    ),
    "chains/audit_detection_removal.py::DetectorRemovalVerifier.observe": (
        "quoted into the session's prompt and recorded onto the observation: "
        "prose and a record, neither of them a reading of the pair"
    ),
    "chains/audit_evidence.py::AuditEvidenceVerifier._head.observe": (
        "self-resolution: a recorded revision must resolve to itself; and the "
        "ancestry weighing beside it, which asks whether the graded commit "
        "sits on the recorded branch at all — a miss there is a refusal to "
        "read the record, not a reading that the grading stopped standing"
    ),
    "chains/audit_evidence.py::AuditEvidenceVerifier._observe": (
        "the audit lane's one consultation of the rule: both revisions are "
        "handed over and the answer is read, which is the registered way to "
        "ask this question"
    ),
    "chains/audit_evidence.py::AuditRestampVerifier.observe": (
        "the recorded commit handed to the restamp reading over the row "
        "history, and quoted into the trace's reason beside the last grading"
    ),
    "chains/audit_overclaim.py::AuditOverclaimVerifier._adoption": (
        "membership: whether an adopted source sha is one of the audited pair"
    ),
    "chains/audit_overclaim.py::AuditOverclaimVerifier.observe": (
        "the same prompt-and-record shape: quoted into the session and "
        "recorded onto the observation"
    ),
    "chains/audit_sweep.py::AuditReadSweep._observe_forge": (
        "the recorded commit of a historical forge observation, carried onto "
        "the claim the mandate is asked about so the mandate reads the commit "
        "the observation was taken at"
    ),
    "chains/ralph_loop.py::RalphLoop._cross_off": (
        "the sha this attempt graded at, carried to the builder that records "
        "it on each row"
    ),
    "chains/ralph_loop.py::RalphLoop._moved_since": (
        "the interval's base, not an operand: each distinct graded sha is "
        "handed to the Git service port as one end of the digest the rule "
        "consumes, and this body decides nothing about the pair"
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
    "domain/criterion_cross_off.py::cross_offs_for": (
        "the row builder: the sha this attempt graded at is recorded, and a "
        "lapsed grading keeps the sha it was taken at unchanged"
    ),
    "domain/lapse.py::held_standing": (
        "the lane arm's one consultation of the rule: it hands both revisions "
        "over, keys the digest map by the recorded one, and reads the answer"
    ),
    "services/assertion_drift.py::AssertionDriftDetector.compare": (
        "the same self-resolution, and the shape of the graded reference itself"
    ),
    "services/audit_runtime.py::_reports": (
        "the recorded commit carried onto the publication, whose own "
        "construction does the provenance reading"
    ),
    "services/audit_sources.py::AuditSourceReader.read.resolve": (
        "the same self-resolution, over both revisions as one loop, and the "
        "same ancestry weighing of the graded commit against the branch"
    ),
    "services/lane_state_writer.py::TrackerLaneStateWriter.write_cross_offs": (
        "the sha the cross-off already carries, written onto the tracker row "
        "and onto the run event that records the take-back"
    ),
    "services/recorded_assertion_drift.py::RecordedAssertionDriftDetector.compare": (
        "a ref again: the revision the recorded assertions are read AT, handed "
        "to the detector beside the head it reads them against"
    ),
    "types/domain/audit_evidence.py::AuditEvidenceObservation.is_lapse": (
        "the audit lane's observation of a finished claim's recorded grading, "
        "which hands both revisions to the rule rather than weighing them"
    ),
    "types/domain/audit_evidence.py::AuditRestampTrace."
    "verdict_follows_the_recorded_history": (
        "the same reading, refused at construction so the wrong verdict cannot "
        "be published; still recorded against recorded"
    ),
}


def _is_graded_key(node: ast.Subscript) -> bool:
    """Whether *node* reads the graded identity out of a serialised record.

    A record dumped to a mapping spells the same field as a constant key, so
    ``record[GRADED]`` carries exactly the value ``record.GRADED`` does. The
    key is compared with the derived identity rather than with a spelling
    repeated here, so renaming the field on the record moves this arm too.
    """
    key = node.slice
    return isinstance(key, ast.Constant) and key.value == GRADED


def _carries(node: ast.expr, aliases: frozenset[str]) -> bool:
    """Whether evaluating *node* reads the graded identity.

    The identity itself, the same field read off a serialised record, a name
    that already stands for it, and anything built out of one of those: a
    container literal, a comprehension, a conditional, an interpolation, or a
    call handed one of them.  A call is followed because the walk cannot know
    what a call does to its argument, and a wrapper that hands the revision
    back — ``str``, ``sorted``, a strip — is the ordinary way a reader loses
    the spelling without losing the value.  Every arm recurses on a strictly
    smaller expression, so the descent is bounded by the node's depth.
    """
    value = node.value if isinstance(node, ast.Await) else node
    if isinstance(value, ast.Attribute):
        return value.attr == GRADED or _carries(value.value, aliases)
    if isinstance(value, ast.Name):
        return value.id == GRADED or value.id in aliases
    if isinstance(value, ast.Subscript):
        return _is_graded_key(value) or _carries(value.value, aliases)
    if isinstance(value, LITERALS):
        return any(_carries(element, aliases) for element in value.elts)
    if isinstance(value, ast.Dict):
        return any(
            item is not None and _carries(item, aliases)
            for item in (*value.keys, *value.values)
        )
    if isinstance(value, ast.Call):
        return any(
            _carries(operand, aliases)
            for operand in (
                value.func,
                *value.args,
                *(word.value for word in value.keywords),
            )
        )
    if isinstance(value, COMPREHENSIONS):
        return _carries(value.elt, aliases) or any(
            _carries(clause.iter, aliases) for clause in value.generators
        )
    if isinstance(value, ast.DictComp):
        return (
            _carries(value.key, aliases)
            or _carries(value.value, aliases)
            or any(_carries(clause.iter, aliases) for clause in value.generators)
        )
    if isinstance(value, ast.IfExp):
        return _carries(value.body, aliases) or _carries(value.orelse, aliases)
    if isinstance(value, ast.JoinedStr):
        return any(_carries(part, aliases) for part in value.values)
    if isinstance(value, ast.FormattedValue):
        return _carries(value.value, aliases)
    if isinstance(value, ast.Starred):
        return _carries(value.value, aliases)
    return False


def _bindings(scope: ast.AST) -> list[tuple[list[ast.expr], ast.expr]]:
    """Every name this scope binds, with what it binds it from.

    Assignment and annotated assignment, an unpack (the targets are walked
    for their names), a loop or comprehension target bound from its iterable,
    a context manager's ``as``, and a walrus.
    """
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
            elif isinstance(child, ast.withitem):
                if child.optional_vars is not None:
                    found.append(([child.optional_vars], child.context_expr))
            elif isinstance(child, ast.NamedExpr):
                found.append(([child.target], child.value))
            if not isinstance(child, SCOPES):
                stack.append(child)
    return found


def _aliases(scope: ast.AST, inherited: frozenset[str]) -> frozenset[str]:
    """The names that stand for the graded identity inside *scope*.

    Grown to a fixed point, because an alias can be bound before the name it
    is bound from is: each pass can only add names, and there are no more
    names to add than there are bindings, so the loop is bounded by that
    count and leaves early as soon as a pass adds nothing.
    """
    aliases = set(inherited)
    bindings = _bindings(scope)
    for _ in range(len(bindings) + 1):
        previous = set(aliases)
        for targets, value in bindings:
            if not _carries(value, frozenset(aliases)):
                continue
            for target in targets:
                aliases.update(
                    node.id for node in ast.walk(target) if isinstance(node, ast.Name)
                )
        if aliases == previous:
            break
    return frozenset(aliases)


def _reads(scope: ast.AST, aliases: frozenset[str]) -> bool:
    """Whether the graded identity is read in *scope* itself.

    A nested body is its own row in the census and is not walked here, so a
    reading is attributed to the body that performs it.
    """
    stack: list[ast.AST] = [scope]
    while stack:
        current = stack.pop()
        for child in ast.iter_child_nodes(current):
            if isinstance(child, SCOPES):
                continue
            if isinstance(child, ast.expr) and _carries(child, aliases):
                return True
            stack.append(child)
    return False


def _readers(tree: ast.AST) -> frozenset[str]:
    """Every body in *tree* that reads the graded identity, once each.

    Names that stand for the identity are inherited by the bodies nested in
    the scope that bound them, because a closure reads what it closes over.
    """
    found: set[str] = set()

    def visit(scope: ast.AST, label: str | None, inherited: frozenset[str]) -> None:
        aliases = _aliases(scope, inherited)
        if label is not None and isinstance(scope, BODIES) and _reads(scope, aliases):
            found.add(label)
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
                stack.append(child)

    visit(tree, None, frozenset())
    return frozenset(found)


def census(root: Path) -> frozenset[str]:
    """Every body in *root* that reads a graded sha, as module::qualname."""
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        found.update(
            f"{module}::{site}" for site in _readers(ast.parse(path.read_text()))
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


def test_every_body_that_reads_a_graded_sha_is_the_rule_or_is_registered():
    """A body nobody registered reading the identity is the finding."""
    found = census(SOURCE)
    assert found - frozenset(REGISTERED) == frozenset({RULE_SITE}), sorted(
        found - frozenset(REGISTERED)
    )


def test_every_registration_names_a_body_the_census_reports():
    """A stale row reds: the register cannot outlive the reading it explains."""
    assert frozenset(REGISTERED) <= census(SOURCE), sorted(
        frozenset(REGISTERED) - census(SOURCE)
    )


def test_every_registration_carries_the_reason_it_reads_the_identity():
    assert all(reason.strip() for reason in REGISTERED.values())
    assert RULE_SITE not in REGISTERED


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "def lapsed(evidence, head_sha):\n"
            "    return evidence.graded_sha != head_sha\n",
            id="attribute-read",
        ),
        pytest.param(
            "def lapsed(graded_sha, head_sha):\n    return graded_sha == head_sha\n",
            id="parameter-read",
        ),
        pytest.param(
            "def lapsed(evidence, head_sha):\n"
            "    taken = evidence.graded_sha\n"
            "    return taken is not head_sha\n",
            id="aliased-read",
        ),
        pytest.param(
            "def lapsed(evidence, head_sha):\n"
            f"    recorded = evidence.{GRADED}\n"
            "    taken = recorded\n"
            "    return taken is not head_sha\n",
            id="twice-aliased-read",
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
            id="loop-bound-read",
        ),
        pytest.param(
            "def lapsed(pair, evidence, head_sha):\n"
            "    return pair.source_sha in {evidence.graded_sha, head_sha}\n",
            id="container-literal-read",
        ),
        pytest.param(
            "class Record:\n"
            "    def lapsed(self):\n"
            "        return self.graded_sha != self.head_sha\n",
            id="method-read",
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
            "def lapsed(evidence, head_sha):\n"
            "    return bool(head_sha.partition(evidence.graded_sha)[1])\n",
            id="prefix-arithmetic-under-another-name",
        ),
        pytest.param(
            "import operator\n"
            "def lapsed(evidence, head_sha):\n"
            "    return operator.ne(evidence.graded_sha, head_sha)\n",
            id="operator-module",
        ),
        pytest.param(
            "def lapsed(evidence, head_sha):\n"
            "    return not evidence.graded_sha.__eq__(head_sha)\n",
            id="comparison-dunder",
        ),
        pytest.param(
            "def lapsed(is_ancestor, evidence, head_sha):\n"
            "    return not is_ancestor(evidence.graded_sha, head_sha)\n",
            id="bare-name-ancestry",
        ),
        pytest.param(
            "def lapsed(git, evidence, head_sha):\n"
            "    return not git.is_ancestor(evidence.graded_sha, head_sha)\n",
            id="ancestry-call-through-a-receiver",
        ),
        pytest.param(
            "async def lapsed(git, repo, evidence, head_sha):\n"
            "    moved = await git.diff_summary(\n"
            "        cwd=repo, base_ref=evidence.graded_sha, head_ref=head_sha\n"
            "    )\n"
            "    return bool(moved.file_paths)\n",
            id="digest-call-by-keyword",
        ),
        pytest.param(
            "def lapsed(evidence, head_sha):\n"
            "    recorded = str(evidence.graded_sha)\n"
            "    return recorded != head_sha\n",
            id="wrapped-in-a-call",
        ),
        pytest.param(
            "def lapsed(git, prior, head_sha):\n"
            "    shas = sorted({row.evidence.graded_sha for row in prior})\n"
            "    return any(not git.is_ancestor(sha, head_sha) for sha in shas)\n",
            id="comprehension-bound-read",
        ),
        pytest.param(
            "async def lapsed(git, cwd, prior, head_sha):\n"
            "    digests = {\n"
            "        sha: await git.diff_summary(\n"
            "            cwd=cwd, base_ref=sha, head_ref=head_sha\n"
            "        )\n"
            "        for sha in sorted({row.evidence.graded_sha for row in prior})\n"
            "    }\n"
            "    return any(digest.file_paths for digest in digests.values())\n",
            id="comprehension-bound-port-call",
        ),
        pytest.param(
            "def lapsed(prior, head_sha):\n"
            "    return any(\n"
            "        not head_sha.startswith(sha)\n"
            "        for sha in {row.evidence.graded_sha for row in prior}\n"
            "    )\n",
            id="comprehension-bound-prefix-match",
        ),
        pytest.param(
            "def outer(evidence, head_sha):\n"
            "    recorded = evidence.graded_sha\n"
            "    def inner():\n"
            "        return recorded != head_sha\n"
            "    return inner\n",
            id="closure-over-the-alias",
        ),
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
            "def reported(prompts, evidence, head_sha, judge):\n"
            "    prompt = prompts.render({'graded_sha': evidence.graded_sha})\n"
            "    judgment = judge(prompt=prompt, head_sha=head_sha)\n"
            "    return judgment.criterion_key != evidence.criterion_key\n",
            id="quoted-into-a-session",
        ),
        pytest.param(
            "def consulted(evidence, head_sha):\n"
            f"    return {RULE}("
            f"{GRADED}=evidence.{GRADED}, head_sha=head_sha)\n",
            id="handed-to-the-rule",
        ),
    ],
)
def test_every_body_that_reads_the_graded_identity_is_censused(body):
    """The last six read it without weighing it, and are censused all the same.

    The census is a reading question, not a weighing one: a body that quotes
    the revision into a prompt or hands it to a port is named here and
    answers for it in the register, which is why no spelling of a comparison
    needs to be listed anywhere in this file.
    """
    assert _readers(ast.parse(body))


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "def other(evidence):\n"
            "    return evidence.recorded_sha != evidence.checked_sha\n",
            id="two-other-revisions",
        ),
        pytest.param(
            "def moved(head_sha, other_head_sha):\n"
            "    return head_sha != other_head_sha\n",
            id="two-heads",
        ),
        pytest.param(
            "def pointed(evidence, pointer):\n"
            "    return evidence.test.endswith(pointer)\n",
            id="another-field-of-the-record",
        ),
        pytest.param(
            "def dumped(record, head_sha):\n"
            "    return record['head_sha'] != head_sha\n",
            id="another-serialised-key",
        ),
    ],
)
def test_a_body_that_reads_no_graded_sha_is_not_censused(body):
    assert _readers(ast.parse(body)) == frozenset()


def test_the_identity_under_another_name_is_reached_from_a_censused_caller():
    """The boundary, read from both sides.

    A body handed the revision under some other parameter name reads no
    identity and is not censused — that is what a textual walk can do. What
    keeps the claim is the other side: the body that HANDS it over reads it,
    is censused, and answers in the register for what it handed the revision
    to. So a second weighing is reachable only from a named body.
    """
    taken = "def lapsed(recorded, head_sha):\n    return recorded != head_sha\n"
    assert _readers(ast.parse(taken)) == frozenset()
    handing = taken + (
        "def asks(evidence, head_sha):\n"
        f"    return lapsed(evidence.{GRADED}, head_sha)\n"
    )
    assert _readers(ast.parse(handing)) == frozenset({"asks"})


def test_one_body_reading_twice_is_one_row_and_two_bodies_are_two():
    source = (
        "def lapsed(evidence, head_sha, other_head_sha):\n"
        "    if evidence.graded_sha == head_sha:\n"
        "        return False\n"
        "    return evidence.graded_sha != other_head_sha\n"
        "def stale(evidence, head_sha):\n"
        "    return evidence.graded_sha != head_sha\n"
    )
    assert _readers(ast.parse(source)) == frozenset({"lapsed", "stale"})


@pytest.mark.parametrize(
    "reader",
    [
        pytest.param(
            "import operator\n"
            "def lapsed(evidence, head_sha):\n"
            "    return operator.ne(evidence.graded_sha, head_sha)\n",
            id="operator-module",
        ),
        pytest.param(
            "def lapsed(evidence, head_sha):\n"
            "    return not evidence.graded_sha.__eq__(head_sha)\n",
            id="comparison-dunder",
        ),
        pytest.param(
            "def lapsed(is_ancestor, evidence, head_sha):\n"
            "    return not is_ancestor(evidence.graded_sha, head_sha)\n",
            id="bare-name-ancestry",
        ),
        pytest.param(
            "def lapsed(evidence, head_sha):\n"
            "    recorded = str(evidence.graded_sha)\n"
            "    return recorded != head_sha\n",
            id="local-then-compared",
        ),
        pytest.param(
            "def lapsed(git, prior, head_sha):\n"
            "    shas = sorted({row.evidence.graded_sha for row in prior})\n"
            "    return any(not git.is_ancestor(sha, head_sha) for sha in shas)\n",
            id="comprehension-bound",
        ),
    ],
)
def test_a_second_module_performing_the_comparison_is_reported(tmp_path, reader):
    """Each spelling the sweep found, planted as a second module, reds here."""
    rule = tmp_path / RULE_MODULE
    rule.parent.mkdir(parents=True, exist_ok=True)
    rule.write_text((SOURCE / RULE_MODULE).read_text())
    assert census(tmp_path) - frozenset(REGISTERED) == frozenset({RULE_SITE})

    (tmp_path / "reader.py").write_text(reader)
    found = census(tmp_path)
    assert found - frozenset(REGISTERED) == frozenset({RULE_SITE, "reader.py::lapsed"})


def _is_rule_call(node: ast.AST) -> bool:
    """Whether this expression is the rule being consulted."""
    if not isinstance(node, ast.Call):
        return False
    called = node.func
    if isinstance(called, ast.Attribute):
        return called.attr == RULE
    return isinstance(called, ast.Name) and called.id == RULE


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


def test_every_reader_of_the_rule_is_registered_as_a_body_that_reads_it():
    """Consulting the rule is reading the identity, so both tables see it."""
    assert frozenset(CALLERS) <= census(SOURCE)
    assert frozenset(CALLERS) <= frozenset(REGISTERED)


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
