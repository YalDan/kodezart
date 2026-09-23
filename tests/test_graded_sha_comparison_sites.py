"""One function weighs a graded sha against a head sha (KOD-413, KOD-696, KOD-596).

Two readers weighing the same two revisions is how a lapse comes to mean one
thing on a compliance mark and another on a lane check: one of them
eventually grows an ancestry test, a prefix match or a null case, and
nothing red says so.  The rule therefore lives in exactly one function, and
this guard keeps it there.

It does not try to recognise a head.  A head is just a string, and whatever
it is called -- ``head``, ``tip``, ``branch_head``, a value a port resolved
-- nothing static can tell it from any other.  The graded sha can be told,
because it is one field of one record.  So the guard keys on the graded side
alone and asks two questions of every scope in the package (a module body,
a class body, a function, a lambda, a comprehension):

* **Which scopes read the graded sha directly?**  Reading it directly means
  an attribute read of the field, a name spelled as the field, a subscript
  or ``.get`` (or any other read) by the field's name or its serialisation
  alias, a ``match`` class pattern keyed on it, or a variable annotated as
  the Evidence record and used whole (iterated, dumped, handed on), which
  reads every field it has.  Inside that same scope the value is followed
  through every binding form to a fixed point: assignment, unpacking, a
  container stored into by subscript, a ``for`` target, ``with ... as``,
  the walrus, a comprehension target, a ``match`` capture, a default
  argument whose default carries it, and a closure over a name that
  carries it.  Every such scope is the rule or a registered row with its
  reason; a new one is red whatever it does with the value, and a row whose
  scope no longer reads the value is red too.
* **Where, inside each registered scope, is the value used?**  Every
  statement in which it appears is pinned verbatim in the scope's row, as a
  multiset (a compound statement by its header, its body elided).  A new
  use inside a registered scope is red, whatever the other operand is
  called: a comparison, a rebinding, a default-argument lambda, a container
  built from it.  The rule is the one scope whose uses are not pinned:
  weighing the pair is its whole job.

The field and its alias are read off ``CriterionEvidence.model_fields``; the
rule's module and name are read off the rule itself; the scanned tree is the
package the rule is packaged in.  The register lives beside this file in
``graded_sha_readers.json``.

The static assertion's reach, as the Check states it: it covers every scope
that reads the graded sha directly (the evidence field or its alias, and any
value bound from them inside that same scope) and pins every statement there
that touches the value, whatever the other operand is called.  A value
carried across a function boundary (returned, stored on an object or a
module global, or passed as an argument) is outside it, as is deliberate
evasion (a name built at run time, ``eval``/``exec``).  Two examples of that
limit, read as code in ``test_the_stated_limit_is_evasion_and_it_is_not_seen``:
a global set in one function and compared in another, and a value one
function returns (``Ledger.recorded()``) compared in its caller.  In each,
the function that reads the field directly is reported and the function
that compares what it was handed is not.
"""

import ast
import copy
import json
import sys
from collections import Counter
from collections.abc import Iterator
from inspect import signature
from pathlib import Path
from typing import NamedTuple

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
#: The graded sha's spellings: the field and the alias it is serialised under.
SPELLINGS = frozenset(
    spelling
    for info in (CriterionEvidence.model_fields.get(GRADED),)
    if info is not None
    for spelling in (GRADED, info.alias)
    if spelling
)
#: The record the field is declared on, and its other fields: a variable
#: annotated as this record reads the graded sha whenever it is used as
#: anything but a read of one of these.
RECORD = CriterionEvidence.__name__
OTHER_FIELDS = frozenset(CriterionEvidence.model_fields) - {GRADED}

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
NAMED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
SCOPES = (*FUNCTIONS, ast.ClassDef, *COMPREHENSIONS)
#: The scopes whose names a nested scope closes over.  A class body's names
#: are not visible to its methods, and a module's are globals.
CLOSING = (*FUNCTIONS, *COMPREHENSIONS)
#: The nodes a use is pinned by: a statement, a ``match`` case or an
#: ``except`` clause, each rendered with its body elided.
CLAUSES = (ast.stmt, ast.match_case, ast.excepthandler)
ANONYMOUS = {
    ast.Lambda: "<lambda>",
    ast.ListComp: "<listcomp>",
    ast.SetComp: "<setcomp>",
    ast.GeneratorExp: "<genexpr>",
    ast.DictComp: "<dictcomp>",
}
MODULE = "<module>"

type ScopeNode = (
    ast.Module
    | ast.FunctionDef
    | ast.AsyncFunctionDef
    | ast.Lambda
    | ast.ClassDef
    | ast.ListComp
    | ast.SetComp
    | ast.GeneratorExp
    | ast.DictComp
)


def _split(node: ScopeNode) -> tuple[list[ast.AST], list[ast.AST]]:
    """What of a scope its enclosing scope evaluates, and what it evaluates.

    Decorators, defaults, annotations and a class's bases are evaluated where
    the scope is written; so is a comprehension's first iterable.  The rest
    is the scope's own.
    """
    if isinstance(node, COMPREHENSIONS):
        inner: list[ast.AST] = [
            value
            for name, value in ast.iter_fields(node)
            if name != "generators" and isinstance(value, ast.AST)
        ]
        for index, generator in enumerate(node.generators):
            inner.extend([generator.target, *generator.ifs])
            if index:
                inner.append(generator.iter)
        return [node.generators[0].iter], inner
    if isinstance(node, ast.Module):
        return [], list(node.body)
    outer: list[ast.AST] = [
        child
        for name, value in ast.iter_fields(node)
        if name != "body"
        for child in (value if isinstance(value, list) else [value])
        if isinstance(child, ast.AST)
    ]
    body = node.body
    return outer, list(body) if isinstance(body, list) else [body]


def _owned(parts: list[ast.AST]) -> tuple[list[ast.AST], list[ScopeNode]]:
    """Every node these parts evaluate in their own scope, and the scopes nested."""
    owned: list[ast.AST] = []
    nested: list[ScopeNode] = []
    stack = list(parts)
    while stack:
        node = stack.pop()
        owned.append(node)
        if isinstance(node, SCOPES):
            nested.append(node)
            stack.extend(_split(node)[0])
        else:
            stack.extend(ast.iter_child_nodes(node))
    return owned, nested


def _parameters(node: ScopeNode) -> list[ast.arg]:
    if not isinstance(node, FUNCTIONS):
        return []
    arguments = node.args
    return [
        *arguments.posonlyargs,
        *arguments.args,
        *([arguments.vararg] if arguments.vararg else []),
        *arguments.kwonlyargs,
        *([arguments.kwarg] if arguments.kwarg else []),
    ]


def _defaults(node: ScopeNode) -> list[tuple[ast.arg, ast.expr]]:
    """Each parameter with a default, and that default."""
    if not isinstance(node, FUNCTIONS):
        return []
    arguments = node.args
    positional = [*arguments.posonlyargs, *arguments.args]
    paired = list(
        zip(
            positional[len(positional) - len(arguments.defaults) :],
            arguments.defaults,
            strict=True,
        )
    )
    paired.extend(
        (parameter, default)
        for parameter, default in zip(
            arguments.kwonlyargs, arguments.kw_defaults, strict=True
        )
        if default is not None
    )
    return paired


def _names_the_record(annotation: ast.AST | None) -> bool:
    return annotation is not None and any(
        (isinstance(node, ast.Name) and node.id == RECORD)
        or (isinstance(node, ast.Attribute) and node.attr == RECORD)
        for node in ast.walk(annotation)
    )


def _annotated(owned: list[ast.AST]) -> frozenset[str]:
    """The names this scope annotates as the Evidence record."""
    return frozenset(
        node.target.id
        for node in owned
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and _names_the_record(node.annotation)
    )


def _bound(target: ast.AST) -> frozenset[str]:
    """The names a binding target binds.

    A name; each element of an unpacking; and the container a subscript
    stores into, which holds the value from then on.  An attribute store is
    a value parked on an object, which is outside the reach.
    """
    if isinstance(target, ast.Name):
        return frozenset({target.id})
    if isinstance(target, ast.Starred):
        return _bound(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        return frozenset().union(*(_bound(element) for element in target.elts))
    if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
        return frozenset({target.value.id})
    return frozenset()


def _captures(pattern: ast.AST) -> frozenset[str]:
    """The names a ``match`` pattern captures."""
    found: set[str] = set()
    for node in ast.walk(pattern):
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            found.add(node.name)
        if isinstance(node, ast.MatchMapping) and node.rest:
            found.add(node.rest)
    return frozenset(found)


def _bindings(
    scope: ScopeNode, owned: list[ast.AST]
) -> Iterator[tuple[list[ast.expr], ast.expr]]:
    """Every binding this scope makes, as its targets and the value bound.

    A subscript store binds the container it stores into, whether the value
    or the key carries the graded sha.
    """
    for node in owned:
        if isinstance(node, ast.Assign):
            yield list(node.targets), node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            if node.value is not None:
                yield [node.target], node.value
        elif isinstance(node, ast.NamedExpr):
            yield [node.target], node.value
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            yield [node.target], node.iter
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None:
                yield [node.optional_vars], node.context_expr
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            yield [node.value], node.slice
    if isinstance(scope, COMPREHENSIONS):
        for generator in scope.generators:
            yield [generator.target], generator.iter


def _render(clause: ast.AST) -> str:
    """A clause as written, with any body it has elided."""
    shallow = copy.copy(clause)
    for name in ("body", "orelse", "finalbody"):
        if isinstance(getattr(shallow, name, None), list) and getattr(shallow, name):
            setattr(shallow, name, [ast.Expr(ast.Constant(...))])
    for name in ("handlers", "cases"):
        if isinstance(getattr(shallow, name, None), list):
            setattr(shallow, name, [])
    return ast.unparse(shallow)


class _Module:
    """One module, read for the scopes that read the graded sha directly."""

    def __init__(self, tree: ast.Module) -> None:
        self.parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        self.readers: dict[str, Counter[str]] = {}
        self._scope(tree, MODULE, frozenset(), frozenset(), frozenset())

    def _reads(self, node: ast.AST, records: frozenset[str]) -> bool:
        """Whether *node* reads the graded sha directly."""
        if isinstance(node, ast.Attribute):
            return isinstance(node.ctx, ast.Load) and node.attr in SPELLINGS
        if isinstance(node, ast.Constant):
            return node.value in SPELLINGS
        if isinstance(node, ast.MatchClass):
            return any(attribute in SPELLINGS for attribute in node.kwd_attrs)
        if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
            return False
        if node.id in SPELLINGS:
            return True
        above = self.parents.get(node)
        return node.id in records and not (
            isinstance(above, ast.Attribute) and above.attr in OTHER_FIELDS
        )

    def _uses(
        self, node: ast.AST, carriers: frozenset[str], records: frozenset[str]
    ) -> bool:
        """Whether *node* is the graded sha, read here or through a local."""
        return self._reads(node, records) or (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in carriers
        )

    def _carries(
        self, value: ast.AST, carriers: frozenset[str], records: frozenset[str]
    ) -> bool:
        return any(self._uses(node, carriers, records) for node in ast.walk(value))

    def _captured(
        self, case: ast.match_case, carriers: frozenset[str], records: frozenset[str]
    ) -> frozenset[str]:
        """What a ``match`` case captures from the graded sha."""
        match = self.parents[case]
        assert isinstance(match, ast.Match)
        if self._carries(match.subject, carriers, records):
            return _captures(case.pattern)
        found: set[str] = set()
        for node in ast.walk(case.pattern):
            if isinstance(node, ast.MatchClass):
                for attribute, pattern in zip(
                    node.kwd_attrs, node.kwd_patterns, strict=True
                ):
                    if attribute in SPELLINGS:
                        found |= _captures(pattern)
            if isinstance(node, ast.MatchMapping):
                for key, pattern in zip(node.keys, node.patterns, strict=True):
                    if self._carries(key, carriers, records):
                        found |= _captures(pattern)
        return frozenset(found)

    def _settle(
        self,
        scope: ScopeNode,
        owned: list[ast.AST],
        carriers: frozenset[str],
        records: frozenset[str],
    ) -> frozenset[str]:
        """The names carrying the graded sha in *scope*, to a fixed point.

        One pass would miss a name bound before the name it is bound from.
        Each pass adds a name or stops, so it ends within the scope's names.
        """
        cases = [node for node in owned if isinstance(node, ast.match_case)]
        while True:
            grown = set(carriers)
            for targets, value in _bindings(scope, owned):
                if self._carries(value, carriers, records):
                    for target in targets:
                        grown |= _bound(target)
            for case in cases:
                grown |= self._captured(case, carriers, records)
            if grown == carriers:
                return carriers
            carriers = frozenset(grown)

    def _clause(self, node: ast.AST) -> ast.AST:
        while not isinstance(node, CLAUSES):
            node = self.parents[node]
        return node

    def _scope(
        self,
        node: ScopeNode,
        label: str,
        closure: frozenset[str],
        typed: frozenset[str],
        defaulted: frozenset[str],
    ) -> None:
        owned, nested = _owned(_split(node)[1])
        parameters = _parameters(node)
        shadowed = frozenset(parameter.arg for parameter in parameters)
        records = (
            (typed - shadowed)
            | _annotated(owned)
            | frozenset(
                parameter.arg
                for parameter in parameters
                if _names_the_record(parameter.annotation)
            )
        )
        carriers = self._settle(node, owned, (closure - shadowed) | defaulted, records)
        clauses = {
            id(clause): clause
            for clause in (
                self._clause(item)
                for item in owned
                if self._uses(item, carriers, records)
            )
        }
        if clauses:
            uses = self.readers.setdefault(label, Counter())
            uses.update(_render(clause) for clause in clauses.values())
        for child in nested:
            closes = isinstance(node, CLOSING) or isinstance(child, COMPREHENSIONS)
            name = child.name if isinstance(child, NAMED) else ANONYMOUS[type(child)]
            self._scope(
                child,
                name if label == MODULE else f"{label}.{name}",
                carriers if closes else frozenset(),
                records if closes else frozenset(),
                frozenset(
                    parameter.arg
                    for parameter, default in _defaults(child)
                    if self._carries(default, carriers, records)
                ),
            )


def readers(sources: dict[str, str]) -> dict[str, Counter[str]]:
    """Every scope of *sources* reading the graded sha directly, with its uses.

    Keyed ``module::qualified.name``; the module body is ``<module>`` and an
    anonymous scope carries Python's own name for it (``<lambda>``,
    ``<genexpr>`` and so on), so two of them in one scope share a row.
    """
    found: dict[str, Counter[str]] = {}
    for module, text in sources.items():
        for label, uses in _Module(ast.parse(text)).readers.items():
            found[f"{module}::{label}"] = uses
    return found


def _sources() -> dict[str, str]:
    return {
        path.relative_to(SOURCE).as_posix(): path.read_text()
        for path in sorted(SOURCE.rglob("*.py"))
    }


SHIPPED = _sources()
SHIPPED_READERS = readers(SHIPPED)


class Row(NamedTuple):
    """A registered reader: why it reads the graded sha, and where."""

    reason: str
    uses: tuple[str, ...]


#: Every scope that reads the graded sha directly and is not the rule, each
#: with the reason it is not the rule's business and every statement in it
#: that touches the value.
#:
#: A recorded revision read against ITSELF, or against a second recorded
#: revision, is provenance: it answers whether a reading is about the commit
#: it says it is, which is a different question from whether that reading
#: still stands.  A revision handed to a port as a ref, quoted into a
#: session's prompt, recorded onto a row or carried into the rule's own
#: arguments weighs nothing at all.  A row is not a licence for the body:
#: one more use, or a changed one, is red.
REGISTER_FILE = Path(__file__).with_name("graded_sha_readers.json")
REGISTERED: dict[str, Row] = {
    site: Row(reason=row["reason"], uses=tuple(row["uses"]))
    for site, row in json.loads(REGISTER_FILE.read_text()).items()
}


def findings(found: dict[str, Counter[str]], register: dict[str, Row]) -> list[str]:
    """Every way *found* differs from the register, in words.

    A reader nobody registered, a registered one using the value anywhere
    its row does not pin, and a row for a scope of these modules that no
    longer reads it.
    """
    messages: list[str] = []
    for site, uses in sorted(found.items()):
        if site == RULE_SITE:
            continue
        row = register.get(site)
        if row is None:
            messages.append(f"{site} reads the graded sha and is not registered")
            continue
        pinned = Counter(row.uses)
        if uses != pinned:
            messages.append(
                f"{site} uses it at {sorted((uses - pinned).elements())} "
                f"beyond its row, and not at {sorted((pinned - uses).elements())}"
            )
    modules = {site.partition("::")[0] for site in found}
    messages.extend(
        f"{site} is registered and no longer reads the graded sha"
        for site in sorted(register)
        if site.partition("::")[0] in modules and site not in found
    )
    return messages


def _scope_node(tree: ast.Module, label: str) -> ast.AST | None:
    """The named scope a dotted path names, or None for an anonymous one."""
    node: ast.AST = tree
    for name in label.split(".") if label != MODULE else []:
        stack: list[ast.AST] = list(ast.iter_child_nodes(node))
        while stack:
            current = stack.pop(0)
            if isinstance(current, NAMED) and current.name == name:
                node = current
                break
            if not isinstance(current, SCOPES):
                stack.extend(ast.iter_child_nodes(current))
        else:
            return None
    return node


def planted(site: str, block: str) -> dict[str, Counter[str]]:
    """The site's module read again with *block* written into the site.

    The block goes in as the first statement of the scope's body, indented
    to it; at module scope it goes at the end.  Where it goes inside the
    scope does not matter: bindings are settled over the whole scope.
    """
    module, _, label = site.partition("::")
    source = SHIPPED[module]
    node = _scope_node(ast.parse(source), label)
    assert isinstance(node, (ast.Module, *NAMED)), site
    lines = source.splitlines(keepends=True)
    if isinstance(node, ast.Module):
        at, indent = len(lines), ""
    else:
        first = node.body[0]
        assert first.lineno > node.lineno, site
        at, indent = first.lineno - 1, " " * first.col_offset
    written = [f"{indent}{line}" for line in block.splitlines(keepends=True)]
    return readers({module: "".join([*lines[:at], *written, *lines[at:]])})


def alone(source: str) -> dict[str, Counter[str]]:
    """One new module, read on its own."""
    return readers({"reader.py": source})


def _indented(block: str) -> str:
    return "".join(f"    {line}" for line in block.splitlines(keepends=True))


def _named_scope(site: str) -> str:
    """The named scope a site is written in: itself, or the nearest one around it.

    A lambda or a comprehension holds no statement, and no named scope can be
    written inside one, so its anonymous segments are the trailing ones.
    """
    module, _, label = site.partition("::")
    names = label.split(".")
    while names and names[-1] in ANONYMOUS.values():
        names.pop()
    return f"{module}::{'.'.join(names) or MODULE}"


#: Where the plants below are written: every registered reader, and for an
#: anonymous one the named scope it is written in.
PLANT_SITES = tuple(sorted({_named_scope(site) for site in REGISTERED}))

#: Each spelling that walked past earlier rounds of this guard, and a few
#: more a reader would call ordinary Python, written as statements so each
#: can be planted as a module of its own and inside any scope.  The names
#: are the ones a reader would use; none of them is bound anywhere, and the
#: heads are called whatever a reader would call them.
PLANTS = {
    "operator-module": (
        "import operator\n_planted = operator.ne(evidence.graded_sha, head_sha)\n"
    ),
    "comparison-dunder": "_planted = evidence.graded_sha.__eq__(head_sha)\n",
    "local-then-compared": (
        "_recorded = str(evidence.graded_sha)\n_planted = _recorded != head_sha\n"
    ),
    "comprehension-bound-operand": (
        "_planted = any(\n"
        "    sha != head_sha for sha in {row.evidence.graded_sha for row in prior}\n"
        ")\n"
    ),
    "serialised-record-get": (
        "_planted = evidence.model_dump().get('graded_sha') != head_sha\n"
    ),
    "match-class-pattern": (
        "match evidence:\n"
        "    case CriterionEvidence(graded_sha=_recorded):\n"
        "        _planted = _recorded != head_sha\n"
    ),
    "match-mapping-pattern": (
        "match row:\n"
        "    case {'graded_sha': _recorded}:\n"
        "        _planted = _recorded != head_sha\n"
    ),
    "match-value-pattern": (
        "match head_sha:\n    case evidence.graded_sha:\n        _planted = True\n"
    ),
    "match-class-keyword-against-a-head": (
        "match evidence:\n"
        "    case CriterionEvidence(graded_sha=lane.head_sha):\n"
        "        _planted = True\n"
    ),
    "attrgetter": (
        "from operator import attrgetter\n"
        "_planted = attrgetter('graded_sha')(evidence) != head_sha\n"
    ),
    "iterating-the-models-fields": (
        "for _key, _value in evidence:\n"
        "    if _key == 'graded_sha':\n"
        "        _planted = _value != head_sha\n"
    ),
    "iterating-a-typed-record": (
        "_record: CriterionEvidence = evidence\n"
        "_planted = any(_value == head_sha for _, _value in _record)\n"
    ),
    "lambda": "_lapsed = lambda head_sha: evidence.graded_sha != head_sha\n",
    "class-level-staticmethod-lambda": (
        "class _Rule:\n"
        "    lapsed = staticmethod(lambda head_sha: evidence.graded_sha != head_sha)\n"
    ),
    "comprehension-scope": (
        "_planted = [evidence.graded_sha != head for head in (head_sha,)]\n"
    ),
    "bare-name-ancestry": "_planted = is_ancestor(evidence.graded_sha, head_sha)\n",
    "prefix-match": "_planted = head_sha.startswith(evidence.graded_sha)\n",
    "helper-under-other-names": (
        "def _differs(recorded, current):\n"
        "    return recorded != current\n"
        "_planted = _differs(evidence.graded_sha, head_sha)\n"
    ),
    "value-put-in-a-container": (
        "_seen = set()\n_seen.add(evidence.graded_sha)\n_planted = head_sha in _seen\n"
    ),
    "walrus": "_planted = (_recorded := evidence.graded_sha) != head_sha\n",
    "serialised-alias-key": "_planted = row['gradedSha'] != head_sha\n",
    "a-lane-records-pushed-head": (
        "_planted = lane.pushed_head_sha == evidence.graded_sha\n"
    ),
    "a-head-read-from-the-port": (
        "_head = git.current_sha(cwd)\n_planted = _head != evidence.graded_sha\n"
    ),
    "a-head-called-head": "_planted = evidence.graded_sha != head\n",
    "a-head-called-tip": "_planted = evidence.graded_sha != tip\n",
    "a-head-called-current-sha": "_planted = evidence.graded_sha != current_sha\n",
    "a-head-called-head-ref": "_planted = evidence.graded_sha != head_ref\n",
    "a-head-in-a-branch-head-field": (
        "_planted = evidence.graded_sha != lane.branch_head\n"
    ),
    "a-head-from-resolve-commit": (
        "_tip = git.resolve_commit(cwd=cwd, ref=branch)\n"
        "_planted = evidence.graded_sha != _tip\n"
    ),
    "a-digest-from-the-graded-sha": (
        "_planted = git.diff_summary(\n"
        "    cwd=cwd, base_ref=evidence.graded_sha, head_ref=branch\n"
        ")\n"
    ),
    "a-default-argument-lambda": (
        "_lapsed = lambda recorded=evidence.graded_sha: recorded != head_sha\n"
    ),
    "one-operand-holding-both": (
        "from operator import itemgetter\n"
        "_blobs = ((evidence.graded_sha, 1), (head_sha, 2))\n"
        "_planted = len(set(map(itemgetter(0), _blobs))) == 1\n"
    ),
    "a-rebinding-before-a-compare": (
        "latest_head = evidence.graded_sha\n_planted = latest_head != head_sha\n"
    ),
}


def test_the_graded_identity_the_guard_scans_for_is_the_evidence_records_own_field():
    """The walk keys on the record's field and its alias, not on a spelling here."""
    assert len(GRADED_IDENTITIES) == 1
    assert GRADED in CriterionEvidence.model_fields
    assert GRADED in signature(graded_state).parameters
    alias = CriterionEvidence.model_fields[GRADED].alias
    assert alias
    assert alias != GRADED
    assert frozenset({GRADED, alias}) == SPELLINGS
    assert RECORD
    assert OTHER_FIELDS
    assert GRADED not in OTHER_FIELDS


def test_the_rule_the_guard_permits_is_the_one_the_sources_import():
    """The permitted site is derived, so a rename carries the guard with it."""
    assert (SOURCE / RULE_MODULE).is_file()
    assert RULE_MODULE.startswith("domain/")
    assert RULE in (SOURCE / RULE_MODULE).read_text()


def test_the_rule_is_reported_as_a_direct_reader():
    """The walk sees the rule itself, so it is not blind where it matters most."""
    assert SHIPPED_READERS[RULE_SITE]


def test_the_sources_compare_a_graded_sha_with_a_head_sha_in_one_body_only():
    """Every direct reader is the rule or registered, using it only as pinned."""
    assert findings(SHIPPED_READERS, REGISTERED) == []


def test_every_exemption_names_a_site_the_walk_actually_reports():
    """A stale row reds: the register cannot outlive the reading it explains."""
    assert REGISTERED
    assert frozenset(REGISTERED) <= frozenset(SHIPPED_READERS), sorted(
        frozenset(REGISTERED) - frozenset(SHIPPED_READERS)
    )


def test_a_row_whose_scope_stopped_reading_is_reported():
    """The same staleness, read through the findings a plant is judged by."""
    stale = "domain/lapse.py::no_longer_reads"
    register = {**REGISTERED, stale: Row(reason="gone", uses=("x",))}
    assert findings(readers({RULE_MODULE: SHIPPED[RULE_MODULE]}), register) == [
        f"{stale} is registered and no longer reads the graded sha"
    ]


def test_every_exemption_carries_the_reason_it_is_one():
    assert all(row.reason.strip() for row in REGISTERED.values())
    assert all(row.uses for row in REGISTERED.values())
    assert RULE_SITE not in REGISTERED


def test_removing_any_row_or_any_pinned_use_is_reported():
    """Each row, and each statement each row pins, is load-bearing."""
    assert REGISTERED
    for site, row in REGISTERED.items():
        without_row = {key: value for key, value in REGISTERED.items() if key != site}
        assert findings(SHIPPED_READERS, without_row), site
        for index in range(len(row.uses)):
            fewer = row.uses[:index] + row.uses[index + 1 :]
            shrunk = {**REGISTERED, site: row._replace(uses=fewer)}
            assert findings(SHIPPED_READERS, shrunk), (site, row.uses[index])


def test_every_registered_reader_is_planted_into_or_through_its_named_scope():
    """Every plant site is a scope a statement can be written into."""
    assert PLANT_SITES
    assert frozenset(REGISTERED) - frozenset(PLANT_SITES) <= {
        site for site in REGISTERED if _named_scope(site) != site
    }
    for site in PLANT_SITES:
        module, _, label = site.partition("::")
        assert _scope_node(ast.parse(SHIPPED[module]), label) is not None, site


@pytest.mark.parametrize("spelling", list(PLANTS))
def test_every_spelling_that_compares_the_graded_sha_is_reported(spelling):
    """Each spelling, planted as a module of its own, reds -- and in a body."""
    block = PLANTS[spelling]
    assert findings(alone(block), REGISTERED)
    body = "def lapsed(evidence, head_sha, prior, row, lane, git, cwd):\n"
    assert findings(alone(body + _indented(block)), REGISTERED)


@pytest.mark.parametrize("spelling", list(PLANTS))
def test_a_second_module_performing_the_comparison_is_reported(spelling):
    """Beside the rule's own module, a second module using the value reds.

    The rule's module is read as shipped and is clean; the reader's own
    scopes are the only findings.
    """
    sources = {RULE_MODULE: SHIPPED[RULE_MODULE]}
    assert findings(readers(sources), REGISTERED) == []
    sources["reader.py"] = "def lapsed(evidence, head_sha, prior, row, lane, git):\n"
    sources["reader.py"] += _indented(PLANTS[spelling])
    found = findings(readers(sources), REGISTERED)
    assert found
    assert all(
        finding.startswith("reader.py::lapsed")
        and finding.endswith("reads the graded sha and is not registered")
        for finding in found
    ), found


@pytest.mark.parametrize("spelling", list(PLANTS))
def test_every_spelling_planted_inside_a_registered_scope_is_reported(spelling):
    """A row pins uses, not a body: a use added inside any registered reader reds."""
    missed = [
        site
        for site in PLANT_SITES
        if not findings(planted(site, PLANTS[spelling]), REGISTERED)
    ]
    assert missed == []


def test_a_default_argument_lambda_in_the_lane_reader_is_reported():
    site = "domain/lapse.py::held_standing"
    found = findings(
        planted(
            site,
            "_lapsed = lambda recorded=cross_off.evidence.graded_sha: "
            "recorded != head_sha\n",
        ),
        REGISTERED,
    )
    assert any(finding.startswith(f"{site} uses it") for finding in found), found
    assert f"{site}.<lambda> reads the graded sha and is not registered" in found


def test_one_operand_holding_both_revisions_in_the_drift_detector_is_reported():
    """``blobs`` is keyed by the graded commit in the same scope, so it carries it."""
    site = "services/assertion_drift.py::AssertionDriftDetector.compare"
    block = "_planted = len(set(map(itemgetter(0), blobs))) == 1\n"
    assert findings(planted(site, block), REGISTERED) == [
        f"{site} uses it at {[block.strip()]} beyond its row, and not at []"
    ]


def test_a_rebinding_before_a_compare_in_the_audit_observation_is_reported():
    """The shipped head-against-head compare there becomes a use of the value.

    Bindings are settled over the whole scope, so rebinding the name the
    compare reads to the graded sha turns that compare into a use nobody
    pinned, as well as the rebinding itself.
    """
    site = "chains/audit_evidence.py::AuditEvidenceVerifier._observe"
    block = "latest_head = evidence.graded_sha\n"
    added = sorted([block.strip(), "if latest_head != head:\n    ..."])
    assert findings(planted(site, block), REGISTERED) == [
        f"{site} uses it at {added} beyond its row, and not at []"
    ]


#: One case per binding form the walk follows inside a scope.  In each, the
#: named statement touches the graded sha only through that binding, so
#: undoing the form in the walk drops the statement from the scope's uses.
BINDINGS = {
    "assignment": (
        "def lapsed(evidence, head):\n"
        "    recorded = evidence.graded_sha\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "annotated-assignment": (
        "def lapsed(evidence, head):\n"
        "    recorded: str = evidence.graded_sha\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "augmented-assignment": (
        "def lapsed(evidence, head):\n"
        "    recorded = ''\n"
        "    recorded += evidence.graded_sha\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "unpacking": (
        "def lapsed(evidence, head):\n"
        "    recorded, current = evidence.graded_sha, head\n"
        "    return recorded != current\n",
        "reader.py::lapsed",
        "return recorded != current",
    ),
    "a-container-stored-into": (
        "def lapsed(evidence, head):\n"
        "    seen = {}\n"
        "    seen[evidence.graded_sha] = 1\n"
        "    return head in seen\n",
        "reader.py::lapsed",
        "return head in seen",
    ),
    "for-target": (
        "def lapsed(evidence, head):\n"
        "    for recorded in (evidence.graded_sha,):\n"
        "        pass\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "with-as": (
        "def lapsed(evidence, head):\n"
        "    with held(evidence.graded_sha) as recorded:\n"
        "        pass\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "walrus": (
        "def lapsed(evidence, head):\n"
        "    (recorded := evidence.graded_sha)\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "comprehension-target": (
        "def lapsed(evidence, head):\n"
        "    return [recorded != head for recorded in (evidence.graded_sha,)]\n",
        "reader.py::lapsed.<listcomp>",
        "return [recorded != head for recorded in (evidence.graded_sha,)]",
    ),
    "match-capture": (
        "def lapsed(evidence, head):\n"
        "    match evidence.graded_sha:\n"
        "        case recorded:\n"
        "            pass\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "match-class-keyword": (
        "def lapsed(evidence, head):\n"
        "    match evidence:\n"
        "        case Evidence(graded_sha=recorded):\n"
        "            pass\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "match-mapping-key": (
        "def lapsed(row, head):\n"
        "    match row:\n"
        "        case {'graded_sha': recorded}:\n"
        "            pass\n"
        "    return recorded != head\n",
        "reader.py::lapsed",
        "return recorded != head",
    ),
    "default-argument": (
        "def lapsed(evidence, head):\n"
        "    return lambda recorded=evidence.graded_sha: recorded != head\n",
        "reader.py::lapsed.<lambda>",
        "return lambda recorded=evidence.graded_sha: recorded != head",
    ),
    "closure": (
        "def lapsed(evidence):\n"
        "    recorded = evidence.graded_sha\n"
        "    def against(head):\n"
        "        return recorded != head\n"
        "    return against\n",
        "reader.py::lapsed.against",
        "return recorded != head",
    ),
    "a-record-annotated-as-the-evidence": (
        "def lapsed(evidence: CriterionEvidence, head):\n"
        "    return head in dict(evidence).values()\n",
        "reader.py::lapsed",
        "return head in dict(evidence).values()",
    ),
}


@pytest.mark.parametrize("form", list(BINDINGS))
def test_every_binding_form_carries_the_graded_sha_to_its_use(form):
    """Each binding form is followed; undoing any one of them reds its case."""
    source, site, use = BINDINGS[form]
    found = alone(source)
    assert site in found, sorted(found)
    assert use in found[site], found[site]


def test_naming_the_graded_sha_without_comparing_it_is_not_a_site():
    """Naming the field to write it is not reading it.

    A record built with the field as a keyword, a field declared under the
    name, an attribute stored under it, another field of the record, and
    every other revision, are not reads of the graded sha.
    """
    sources = (
        "def stamp(head_sha):\n    return Report(graded_sha=head_sha)\n",
        "class Report:\n    graded_sha: str\n",
        "def park(self, head_sha):\n    self.graded_sha = head_sha\n",
        "def other(evidence):\n"
        "    return evidence.recorded_sha != evidence.checked_sha\n",
        "def moved(head_sha, other_head_sha):\n    return head_sha != other_head_sha\n",
        "def pointed(evidence: CriterionEvidence, pointer):\n"
        "    return evidence.test.endswith(pointer)\n",
    )
    assert [source for source in sources if alone(source)] == []


def test_a_reader_consulting_the_rule_is_not_a_site_and_its_own_arithmetic_is():
    """The consultation is a pinned use; arithmetic beside it is not.

    Read on the shipped readers of the rule: each is green as it stands, and
    the same reader with the pair weighed beside the consultation is red.
    """
    assert CALLERS
    for site in CALLERS:
        module = site.partition("::")[0]
        assert findings(readers({module: SHIPPED[module]}), REGISTERED) == []
        weighed = planted(site, "_planted = evidence.graded_sha != head_sha\n")
        assert [
            finding.partition(" ")[0] for finding in findings(weighed, REGISTERED)
        ] == [site]


def test_one_body_comparing_twice_is_one_site_and_two_bodies_are_two():
    source = (
        "def lapsed(evidence, head_sha, other_head_sha):\n"
        "    if evidence.graded_sha == head_sha:\n"
        "        return False\n"
        "    return evidence.graded_sha != other_head_sha\n"
        "def stale(evidence, head_sha):\n"
        "    return evidence.graded_sha != head_sha\n"
    )
    assert alone(source) == {
        "reader.py::lapsed": Counter(
            {
                "if evidence.graded_sha == head_sha:\n    ...": 1,
                "return evidence.graded_sha != other_head_sha": 1,
            }
        ),
        "reader.py::stale": Counter({"return evidence.graded_sha != head_sha": 1}),
    }


def test_the_stated_limit_is_evasion_and_it_is_not_seen():
    """What the docstring says this check does not claim, read as code.

    In each boundary case the function that reads the field directly is
    reported, and the function comparing what it was handed is not.
    """
    evasions = (
        "def lapsed(evidence, head_sha):\n"
        "    return getattr(evidence, 'graded' + '_sha') != head_sha\n"
        "def evaluated(evidence, head_sha):\n"
        "    return eval('evidence.graded_sha') != head_sha\n"
    )
    assert alone(evasions) == {}
    through_a_global = (
        "def keep(evidence):\n"
        "    global _RECORDED\n"
        "    _RECORDED = evidence.graded_sha\n"
        "def lapsed(head):\n"
        "    return _RECORDED != head\n"
    )
    assert frozenset(alone(through_a_global)) == {"reader.py::keep"}
    through_a_return = (
        "class Ledger:\n"
        "    def recorded(self):\n"
        "        return self._evidence.graded_sha\n"
        "def lapsed(ledger, head):\n"
        "    return ledger.recorded() != head\n"
    )
    assert frozenset(alone(through_a_return)) == {"reader.py::Ledger.recorded"}
    through_an_object = (
        "class Reader:\n"
        "    def keep(self, row):\n"
        "        self._taken = row.graded_sha\n"
        "    def lapsed(self, head):\n"
        "        return self._taken != head\n"
    )
    assert frozenset(alone(through_an_object)) == {"reader.py::Reader.keep"}
    through_an_argument = (
        "def differs(recorded, head):\n"
        "    return recorded != head\n"
        "def asks(evidence, head):\n"
        "    return differs(evidence.graded_sha, head)\n"
    )
    assert frozenset(alone(through_an_argument)) == {"reader.py::asks"}


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
            if isinstance(child, NAMED):
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


def test_every_reader_of_the_rule_is_a_registered_direct_reader():
    """Consulting the rule means handing it the graded sha, so both tables see it."""
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
