"""No shipped body reads criterion-shaped content out of issue prose.

Criterion membership and satisfaction are sub-issue facts, keyed on each
criterion's own key.  The failure this guards is the quiet return of the
retired body parser as a fallback: one reader that, when the sub-issue
listing says nothing, goes looking through an issue's description for a
checkbox row, an ``AC-n`` identity or a template row label and mints
membership out of what it finds.  Such a reader answers a reorder or a
reflow differently from a keyed one, and nothing red says so.

The criterion template grammar itself is legitimate and has exactly one
owner: the module that holds the field reader, which reads a criterion's
own body for the fields the template states.  That owner is located below
through the function itself rather than by a written-down path, and every
other body in the tree must contain no such match at all.

The walk is textual and executes nothing, which is what lets it speak for
the whole tree rather than for the paths a fixture happens to reach.  The
controls inject each spelling a body scan can hide behind, and each way of
naming criterion-shaped text without matching anything against it.

The boundary against locating a criterion by text: comparing a criterion's
recorded text with a target the caller already holds by key is not a body
scan and is not reported here, because nothing is matched against a body.
Resolution by key, and a writer that finds its target by text instead, are
another guard's subject; this one owns the row grammar and the body scan.

What it does not see: an equality against a row label over a body split by
some other means, because only membership compares and matcher calls are
read; a row label assembled from a name bound in an earlier statement,
because only one expression's own literal parts are folded; a scan keyed on
bare markdown bold with no row label at all, because a row shape here is a
label followed by a colon and bold alone names no field — that form is held
behaviourally by the reader ordering cases over both tracker
implementations; a caller that hands a PARENT's body to the sanctioned
reader, because the scan reads the reader and not the argument's
provenance — that is held behaviourally by the reader conformance cases
that give a parent a criterion-shaped body and read nothing out of it; a
tuple of literal prefixes handed to a string matcher, because only one
expression's own literal parts are folded and a tuple is not folded, so
``line.startswith(("- [ ]", "- [x]"))`` shows the matcher no shaped
argument; a matcher called with keyword arguments, such as
``re.search(pattern=…, string=…)``, because only a call's positional
arguments are read; and any matcher reached by reflection.

Who reads the one row traversal is derived tree-wide from calls, imports
and string constants that name it. Outside that reach: a name built at run
time, a value handed across a function boundary where the other function is
not resolved at this site, and a binding made only when a function runs.

Under every spelling this layer does not see lies the behavioural floor,
which is where a fallback that mints membership out of a parent's prose
actually dies: ``test_criterion_reader.py`` at
``test_parent_text_cannot_mint_criterion_membership`` and the
``- [x] old-AC-9`` row of
``test_parent_rewrite_preserves_every_criterion_key_state_and_evidence``,
and ``test_empty_fire_entry.py`` at
``test_parent_heading_shapes_all_read_empty_and_refuse_fire``, each over
both tracker implementations.
"""

import ast
import re
import sys
from functools import cache
from pathlib import Path

import pytest

from kodezart.domain.fire_spec import (
    criterion_check,
    criterion_field_bodies,
    replace_criterion_fields,
    tracker_spec_from_issues,
)
from tests.domain.test_criterion_cross_off import (
    called_name,
    callers_of,
    qualified_names,
    source_tree,
)
from tests.identity_guards import _constructor_names

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "kodezart"
#: Where the criterion template grammar is written, and so the one module
#: a match against criterion-shaped text belongs in.  Located through the
#: field reader itself, so extracting or renaming a reader moves the guard
#: with the code instead of reddening it.
RULE_MODULE = (
    Path(sys.modules[criterion_field_bodies.__module__].__file__ or "")
    .resolve()
    .relative_to(SOURCE_ROOT.resolve())
    .as_posix()
)

#: The spellings of criterion-shaped content a scan would look for: a
#: markdown checkbox, an authored ``AC-n`` identity, any template row
#: label — including the alternation a row-label pattern is written as —
#: a bold row whose label is assembled somewhere else, and a pattern for a
#: bold row label whose label is a capture group (``\*\*(\w+):\*\*``),
#: which reads every row label and leaves choosing the field to a compare.
CRITERION_SHAPES = (
    re.compile(r"\[\s*[xX]?\s*\]"),
    re.compile(r"AC-"),
    re.compile(r"(Check|Do|Evidence|Class)(\\?\*)*\s*[:|]"),
    re.compile(r"\*\*[^*]*:\*\*"),
    re.compile(r"(?:\\\*\\\*|\\\*\{2\})\(.+?\)\s*:\s*(?:\\\*\\\*|\\\*\{2\})"),
)
#: Module-level entry points that match a pattern against text, and the
#: same names called on a compiled pattern.
RE_MATCHERS = frozenset(
    {"match", "search", "fullmatch", "findall", "finditer", "split", "sub", "subn"}
)
#: String methods that locate content rather than merely rewriting it.
TEXT_MATCHERS = frozenset(
    {
        "find",
        "rfind",
        "index",
        "rindex",
        "startswith",
        "endswith",
        "count",
        "partition",
        "rpartition",
        "split",
        "rsplit",
    }
)


def _literal_text(node: ast.expr) -> str | None:
    """The text this expression spells, as far as it spells one literally.

    A plain string, the constant parts of a formatted string, or a sum of
    either folded: a row label assembled around a field name is the row
    shape it assembles, not an unrelated pair of fragments.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        parts = (_literal_text(node.left), _literal_text(node.right))
        return "".join(part for part in parts if part is not None)
    return None


def _is_criterion_shaped(node: ast.expr) -> bool:
    """Whether this expression spells criterion-shaped content literally."""
    text = _literal_text(node)
    return text is not None and any(shape.search(text) for shape in CRITERION_SHAPES)


def _bare_matchers(tree: ast.Module) -> dict[str, str]:
    """{local name: pattern-library function} for every name imported bare.

    ``from re import findall`` and ``from re import compile as rx`` bind the
    library's matcher to a plain name, which a call then spells with no
    module in front of it; read from the module's own imports, so a call
    through that name is the matcher it was imported as.
    """
    return {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "re"
        for alias in node.names
        if alias.name in RE_MATCHERS | {"compile"}
    }


def _called_matcher(func: ast.expr, bare: dict[str, str]) -> str | None:
    """The pattern-library function a call's callee names, however spelled."""
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return bare.get(func.id)
    return None


def _compiles_a_criterion_shape(
    node: ast.expr, bare: dict[str, str] | None = None
) -> bool:
    """Whether this expression is a pattern compiled over such a spelling."""
    return (
        isinstance(node, ast.Call)
        and _called_matcher(node.func, bare or {}) == "compile"
        and bool(node.args)
        and _is_criterion_shaped(node.args[0])
    )


def _pattern_names(tree: ast.Module) -> set[str]:
    """Every identity bound to a compiled criterion-shaped pattern."""
    bare = _bare_matchers(tree)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        if node.value is None or not _compiles_a_criterion_shape(node.value, bare):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def _matches_criterion_shaped_text(
    node: ast.AST, names: set[str], bare: dict[str, str]
) -> bool:
    """Whether this expression puts criterion-shaped content against text."""
    if isinstance(node, ast.Compare):
        return any(
            isinstance(operator, ast.In | ast.NotIn) for operator in node.ops
        ) and _is_criterion_shaped(node.left)
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    matcher = _called_matcher(func, bare)
    if matcher in RE_MATCHERS and node.args and _is_criterion_shaped(node.args[0]):
        return True
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in TEXT_MATCHERS and any(
        _is_criterion_shaped(argument) for argument in node.args
    ):
        return True
    if func.attr in RE_MATCHERS:
        owner = func.value
        if isinstance(owner, ast.Name) and owner.id in names:
            return True
        if isinstance(owner, ast.Attribute) and owner.attr in names:
            return True
        if _compiles_a_criterion_shape(owner, bare):
            return True
    return False


def _sites(tree: ast.Module, names: set[str]) -> set[str]:
    """Label each scope that matches criterion-shaped content, once per scope."""
    bare = _bare_matchers(tree)
    found: set[str] = set()

    def walk(node: ast.AST, label: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            here = label
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                here = child.name if label is None else f"{label}.{child.name}"
            elif label is None:
                here = f"line {child.lineno}"
            if _matches_criterion_shaped_text(child, names, bare):
                found.add(here or "")
            walk(child, here)

    walk(tree, None)
    return found


def pattern_names(sources: dict[str, str]) -> frozenset[str]:
    """Every name *sources* binds to a compiled criterion-shaped pattern.

    Pooled over the whole map before any module is scanned, so a pattern
    compiled in one module and matched in another is a site where it is
    matched rather than nowhere at all.
    """
    return frozenset(
        name
        for source in sources.values()
        for name in _pattern_names(ast.parse(source))
    )


def _module_sites(tree: ast.Module, pool: frozenset[str]) -> set[str]:
    """The sites of one module, the pool's names resolved to its own words."""
    local: set[str] = set()
    for name in pool:
        local |= _constructor_names(tree, name)
    return _sites(tree, local)


def body_scan_sites(sources: dict[str, str]) -> dict[str, list[str]]:
    """Every scope in *sources* that matches criterion-shaped text, by module."""
    pool = pattern_names(sources)
    found: dict[str, list[str]] = {}
    for module, source in sources.items():
        sites = _module_sites(ast.parse(source), pool)
        if sites:
            found[module] = sorted(sites)
    return found


def grammar_readers(
    sources: dict[str, str],
) -> tuple[frozenset[str], dict[str, list[str]]]:
    """The grammar's own reader names, and every scope elsewhere calling one.

    Readers begin as the scopes of the rule module that match its compiled
    row pattern and grow, inside that module alone, by "calls a reader
    name", to a fixed point: a helper extracted out of a reader is reached
    by its caller and joins the set with it.  Consumers are every scope in
    every other module that calls a reader name.  Both are derived; the
    only input not read off the tree is the module the field reader lives
    in, and that is read off the function.
    """
    rule = ast.parse(sources[RULE_MODULE])
    readers = _module_sites(rule, frozenset(_pattern_names(rule)))
    while True:
        grown = {
            caller for reader in readers for caller in callers_of(rule, name=reader)
        }
        if grown <= readers:
            break
        readers |= grown
    consumers: dict[str, list[str]] = {}
    for module, source in sources.items():
        if module == RULE_MODULE:
            continue
        tree = ast.parse(source)
        scopes = {
            caller for reader in readers for caller in callers_of(tree, name=reader)
        }
        if scopes:
            consumers[module] = sorted(scopes)
    return frozenset(readers), consumers


def called_inside(tree: ast.Module, *, name: str) -> set[str]:
    """Every name the module-level definition *name* in *tree* calls."""
    (definition,) = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == name
    ]
    return {
        called
        for node in ast.walk(definition)
        if isinstance(node, ast.Call) and (called := called_name(node)) is not None
    }


def traversal_callers(
    sources: dict[str, str], *, traversal: str
) -> set[tuple[str, str]]:
    """Every (module, scope) in *sources* that reaches *traversal* directly.

    A call counts however its callee is spelled, plain or as an attribute.
    An import of the traversal counts as a call, because a module that
    imports a private row walker has no other use for it; and a string
    constant naming it (``getattr``, a ``module:attr`` reference) counts as
    well. A scope that is the module itself is labelled by line.
    """
    shaped = re.compile(rf"(?:[\w.]+[.:])?{re.escape(traversal)}")
    found: set[tuple[str, str]] = set()
    for module, source in sources.items():
        tree = ast.parse(source)
        where = qualified_names(tree)
        found |= {(module, caller) for caller in callers_of(tree, name=traversal)}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == traversal for alias in node.names
            ):
                found.add((module, where[id(node)] or f"line {node.lineno}"))
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and shaped.fullmatch(node.value)
            ):
                found.add((module, where[id(node)] or f"line {node.lineno}"))
    return found


def sanctioned_traversal_callers(
    sources: dict[str, str], *, traversal: str
) -> set[tuple[str, str]]:
    """The two readers the traversal has, both named off the owner's parse.

    The field reader, and the one row-offset reader the edit reads its rows
    through: a function the edit calls that itself calls the traversal and
    is not the field reader. More than one such function, or none, is not
    this shape, and fails the unpacking here.
    """
    rule = ast.parse(sources[RULE_MODULE])
    in_rule = set(callers_of(rule, name=traversal))
    edit_reads = called_inside(rule, name=replace_criterion_fields.__name__)
    (row_reader,) = (edit_reads & in_rule) - {criterion_field_bodies.__name__}
    return {
        (RULE_MODULE, criterion_field_bodies.__name__),
        (RULE_MODULE, row_reader),
    }


@cache
def _grammar_pattern_names() -> frozenset[str]:
    """The names the shipped tree binds to a criterion-shaped pattern."""
    return pattern_names(source_tree())


def _snippet_sites(source: str) -> set[str]:
    """The sites one standalone body states, read as a module of the tree.

    The pool is the tree's own, so a body reaching the shipped grammar's
    pattern under another word is read the way the whole-tree scan reads it.
    """
    tree = ast.parse(source)
    return _module_sites(tree, _grammar_pattern_names() | _pattern_names(tree))


def test_only_the_grammar_owner_matches_criterion_shaped_text():
    sources = source_tree()
    # The module keys the scan reports are the keys the tree is read under;
    # a packaging change that broke that agreement would otherwise make
    # every scan below vacuous instead of red.
    assert RULE_MODULE in sources

    found = body_scan_sites(sources)

    assert set(found) == {RULE_MODULE}
    # One scope of the owner matches, and it is the one row traversal: a
    # second loop over the rows with its own match, however faithful a copy,
    # is a second parser that can drift from the first.
    (traversal,) = found[RULE_MODULE]
    # The field reader and the edit both reach that traversal by call, so the
    # one scope that matches is the one both of them read the rows through.
    rule = ast.parse(sources[RULE_MODULE])
    reaching = {traversal}
    while True:
        grown = reaching | {
            caller for name in reaching for caller in callers_of(rule, name=name)
        }
        if grown == reaching:
            break
        reaching = grown
    assert {criterion_field_bodies.__name__, replace_criterion_fields.__name__} <= (
        reaching - {traversal}
    )
    # And the traversal has exactly two direct readers in the whole tree: the
    # field reader, and the row-offset reader the edit reads through. A
    # second field reader over the same rows, in the owner or anywhere else,
    # matches nothing itself, and is a second parser all the same.
    assert traversal_callers(sources, traversal=traversal) == (
        sanctioned_traversal_callers(sources, traversal=traversal)
    )


def test_the_grammar_is_reached_from_outside_by_call_and_never_re_matched():
    """The grammar is read by calling it, from modules that match nothing.

    The reader set is grown inside the owner module, so non-vacuity is
    stated at the three tiers the growth has to cross: the field reader the
    rule names, the Check reader that calls it, and the spec capture that
    calls that one.  A fixed point that stalled would lose the deepest.
    """
    sources = source_tree()

    readers, consumers = grammar_readers(sources)

    assert {
        criterion_field_bodies.__name__,
        criterion_check.__name__,
        tracker_spec_from_issues.__name__,
    } <= readers
    assert consumers
    # Reaching the grammar by call is the sanctioned way, so no consumer is
    # among the modules the scan reports as matching shaped text itself.
    assert set(consumers).isdisjoint(set(body_scan_sites(sources)) - {RULE_MODULE})


@pytest.mark.parametrize(
    "body",
    [
        "import re\n"
        "def criteria(issue):\n"
        "    return re.findall(r'^- \\[([ x])\\] (.+)$', issue.body)\n",
        "import re\ndef ids(issue):\n    return re.findall(r'AC-[0-9]+', issue.body)\n",
        "import re\n"
        "ROW = re.compile(r'- \\[[ x]\\] (?P<name>AC-[0-9]+)')\n"
        "def criteria(issue):\n"
        "    return [row.group('name') for row in ROW.finditer(issue.body)]\n",
        "def satisfied(issue):\n    return '- [x]' in issue.description\n",
        "def criteria(issue):\n"
        "    return [line for line in issue.body.split('**Check:**')]\n",
        "def criteria(issue):\n"
        "    return [line for line in issue.body.splitlines()"
        " if line.startswith('- [ ] AC-')]\n",
        "class Reader:\n"
        "    def fallback(self, issue):\n"
        "        return issue.body.count('- [x]')\n",
        "import re\n"
        "def outer():\n"
        "    def inner(issue):\n"
        "        return re.compile(r'\\*\\*Check:\\*\\*').search(issue.body)\n"
        "    return inner\n",
        "def rows(body, field):\n"
        "    return [line for line in body.splitlines()"
        " if line.startswith(f'**{field}:**')]\n",
        "def rows(body, field):\n"
        "    return [line for line in body.splitlines()"
        " if line.startswith('**' + field + ':**')]\n",
        "import re\n"
        "EVIDENCE = re.compile(r'^\\*\\*Evidence:\\*\\*(.*)$')\n"
        "def evidence(issue):\n"
        "    return EVIDENCE.match(issue.body)\n",
        "def evidence(issue):\n    return issue.body.split('**Evidence:**')[1]\n",
        "from kodezart.domain.fire_spec import _CRITERION_ROW as ROW\n"
        "def rows(issue):\n"
        "    return ROW.match(issue.body)\n",
        "from re import findall\n"
        "def evidence(issue):\n"
        "    return findall(r'^\\*\\*Evidence:\\*\\*(.*)$', issue.body)\n",
        "from re import search as seek\n"
        "def evidence(issue):\n"
        "    return seek(r'\\*\\*Evidence:\\*\\*', issue.body)\n",
        "from re import compile as rx\n"
        "def evidence(issue):\n"
        "    return rx(r'\\*\\*Evidence:\\*\\*(.*)').match(issue.body)\n",
        "from re import compile as rx\n"
        "ROW = rx(r'^\\*\\*Evidence:\\*\\*(.*)$')\n"
        "def evidence(issue):\n"
        "    return ROW.match(issue.body)\n",
        "import re\n"
        "ROW = re.compile(r'^ {0,3}\\*\\*(\\w+):\\*\\*(.*)$')\n"
        "def evidence(body):\n"
        "    for line in body.splitlines():\n"
        "        row = ROW.match(line)\n"
        "        if row is not None and row[1] == 'Evidence':\n"
        "            return row[2]\n",
        "import re\n"
        "def labels(body):\n"
        "    return re.findall(r'\\*{2}(?P<label>[A-Z][a-z]+):\\*{2}', body)\n",
    ],
)
def test_every_spelling_of_a_body_scan_is_reported(body):
    assert _snippet_sites(body)


@pytest.mark.parametrize(
    "body",
    [
        "def draft(check, do):\n"
        "    return f'**Check:** {check}\\n\\n**Do:** {do}\\n'\n",
        "PREFIX = 'AC-'\ndef mint(index):\n    return f'{PREFIX}{index}'\n",
        "def report(claim):\n"
        "    return f'violation of the current Check: {claim.check}'\n",
        "import re\n"
        "FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')\n"
        "def fenced(line):\n"
        "    return FENCE.match(line)\n",
        "def membership(children):\n"
        "    return [child.key for child in children if 'criterion' in child.labels]\n",
        "def amend(body):\n    return body.replace('- [ ]', '- [x]')\n",
        "def held(issue, criterion):\n"
        "    return criterion_field_bodies(issue.body, field='Check')"
        " != (criterion.text,)\n",
        "def live(issue, recorded):\n    return body_digest(issue.body) == recorded\n",
        "import re\n"
        "BOLD = re.compile(r'\\*\\*(.+?)\\*\\*')\n"
        "def plain(text):\n"
        "    return BOLD.sub(r'\\1', text)\n",
    ],
)
def test_naming_criterion_shaped_text_without_matching_it_is_not_a_site(body):
    assert _snippet_sites(body) == set()


def test_one_body_scanning_twice_is_one_site_and_two_bodies_are_two():
    source = (
        "def criteria(issue, other):\n"
        "    if '- [x]' in issue.body:\n"
        "        return True\n"
        "    return other.body.startswith('- [ ] AC-1')\n"
        "def fallback(issue):\n"
        "    return issue.body.count('**Check:**')\n"
    )

    assert _snippet_sites(source) == {"criteria", "fallback"}


#: Each way a second body scan could arrive, as the module text it would
#: arrive as: a checkbox scan, a second row pattern, a split on a row
#: label, a pattern compiled in one module and matched in another, a scan
#: for an authored ``AC-n`` identity alone, and a matcher imported by bare
#: name out of the pattern library — six in all.
PLANTED_SCANS = {
    "checkbox-scan": {
        "services/reader.py": "def criteria(issue):\n"
        "    return [line for line in issue.body.splitlines()"
        " if line.startswith('- [ ]')]\n"
    },
    "second-row-pattern": {
        "services/reader.py": "import re\n"
        "ROW = re.compile(r'^\\*\\*(Check|Evidence):\\*\\*')\n"
        "def rows(issue):\n"
        "    return ROW.findall(issue.body)\n"
    },
    "label-split": {
        "services/reader.py": "def evidence(issue):\n"
        "    return issue.body.split('**Evidence:**')[1]\n"
    },
    "pattern-compiled-elsewhere": {
        "domain/rows.py": "import re\nROW = re.compile(r'- \\[[ x]\\] AC-')\n",
        "services/reader.py": "from kodezart.domain.rows import ROW\n"
        "def criteria(issue):\n"
        "    return ROW.findall(issue.body)\n",
    },
    "ac-identity-scan": {
        "services/reader.py": "def ids(issue):\n"
        "    return [line for line in issue.body.splitlines()"
        " if 'AC-' in line]\n"
    },
    "bare-matcher-import": {
        "services/reader.py": "from re import search\n"
        "def evidence(issue):\n"
        "    return search(r'^\\*\\*Evidence:\\*\\*(.*)$', issue.body)\n"
    },
}


@pytest.mark.parametrize("planted", sorted(PLANTED_SCANS))
def test_a_second_body_scan_anywhere_in_the_tree_fails_the_assertion(planted):
    sources = source_tree()
    assert set(body_scan_sites(sources)) == {RULE_MODULE}

    sources.update(PLANTED_SCANS[planted])
    found = body_scan_sites(sources)

    assert set(found) == {RULE_MODULE, "services/reader.py"}
    assert found["services/reader.py"]


def test_a_module_that_only_calls_the_reader_is_a_consumer_and_not_a_site():
    """The sanctioned way of reading a row is not what the scan reports.

    A module that quotes a criterion's Check by calling the field reader is
    a consumer of the grammar, which the derivation must see, and no site,
    which the scan must not report — otherwise the guard would forbid the
    very reading the rule permits.
    """
    sources = source_tree()
    sources["services/quoter.py"] = (
        f"from {criterion_field_bodies.__module__} import"
        f" {criterion_field_bodies.__name__}\n"
        "def check(issue):\n"
        f"    return {criterion_field_bodies.__name__}(issue.body, field='Check')\n"
    )

    _, consumers = grammar_readers(sources)

    assert "services/quoter.py" in consumers
    assert "services/quoter.py" not in body_scan_sites(sources)


#: Each way a second reader of the one row traversal could arrive, with
#: ``TRAVERSAL`` standing for the traversal's name as the scan derives it: a
#: verbatim second field reader in the owner, the same assembly inline in
#: another module through an import, a call through the owner module's
#: attribute, and a reach by a literal name.
PLANTED_READERS = {
    "second-reader-in-the-owner": (
        RULE_MODULE,
        "\n\ndef evidence_field_bodies(body):\n"
        "    checks = []\n"
        "    lines = []\n"
        "    active = False\n"
        "    for label, _, text in TRAVERSAL(body):\n"
        "        if label is not None:\n"
        "            if active:\n"
        "                checks.append('\\n'.join(lines).strip())\n"
        "            active = label == 'Evidence'\n"
        "            lines = [text] if active else []\n"
        "        elif active:\n"
        "            lines.append(text)\n"
        "    return tuple(checks)\n",
    ),
    "inline-through-an-import": (
        "domain/criterion_evidence.py",
        "\n\ndef evidence(body):\n"
        f"    from {criterion_field_bodies.__module__} import TRAVERSAL\n"
        "    return [text for label, _, text in TRAVERSAL(body)"
        " if label == 'Evidence']\n",
    ),
    "import-alone": (
        "services/audit_failures.py",
        f"\nfrom {criterion_field_bodies.__module__} import TRAVERSAL as rows\n",
    ),
    "owner-attribute": (
        "services/reader.py",
        f"import {criterion_field_bodies.__module__} as owner\n"
        "def evidence(body):\n"
        "    return list(owner.TRAVERSAL(body))\n",
    ),
    "literal-name": (
        "services/reader.py",
        f"import {criterion_field_bodies.__module__} as owner\n"
        "def evidence(body):\n"
        "    return list(getattr(owner, 'TRAVERSAL')(body))\n",
    ),
}


def _traversal(sources: dict[str, str]) -> str:
    """The one row traversal, as the scan finds it in the owner."""
    (traversal,) = body_scan_sites(sources)[RULE_MODULE]
    return traversal


@pytest.mark.parametrize("planted", sorted(PLANTED_READERS))
def test_a_second_reader_of_the_row_traversal_fails_the_assertion(planted):
    sources = source_tree()
    traversal = _traversal(sources)
    assert traversal_callers(sources, traversal=traversal) == (
        sanctioned_traversal_callers(sources, traversal=traversal)
    )

    module, text = PLANTED_READERS[planted]
    sources[module] = sources.get(module, "") + text.replace("TRAVERSAL", traversal)

    assert traversal_callers(sources, traversal=traversal) - (
        sanctioned_traversal_callers(sources, traversal=traversal)
    )


def test_the_edit_reading_through_a_second_row_reader_fails_the_derivation():
    """Two row-offset readers under the edit are not the one it reads through."""
    sources = source_tree()
    traversal = _traversal(sources)
    rule = sources[RULE_MODULE]
    edit = f"def {replace_criterion_fields.__name__}("
    assert rule.count(edit) == 1
    sources[RULE_MODULE] = rule.replace(
        edit,
        f"def _more_rows(body):\n    return tuple({traversal}(body))\n\n\n{edit}",
    ).replace(
        "    rows = _criterion_rows(body)\n",
        "    rows = _criterion_rows(body)\n    _more_rows(body)\n",
    )
    assert sources[RULE_MODULE].count("_more_rows(body)") == 2

    with pytest.raises(ValueError, match="too many values to unpack"):
        sanctioned_traversal_callers(sources, traversal=traversal)


def test_a_traversal_name_built_at_run_time_is_outside_the_reach():
    """The stated limit, held: a name assembled when the code runs is unseen."""
    sources = source_tree()
    traversal = _traversal(sources)
    head, tail = traversal[: len(traversal) // 2], traversal[len(traversal) // 2 :]
    sources["services/reader.py"] = (
        f"import {criterion_field_bodies.__module__} as owner\n"
        "def evidence(body):\n"
        f"    return list(getattr(owner, {head!r} + {tail!r})(body))\n"
    )

    assert traversal_callers(sources, traversal=traversal) == (
        sanctioned_traversal_callers(sources, traversal=traversal)
    )
