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
because only one expression's own literal parts are folded; a caller that
hands a PARENT's body to the sanctioned reader, because the scan reads the
reader and not the argument's provenance — that is held behaviourally by
the reader conformance cases that give a parent a criterion-shaped body
and read nothing out of it; and any matcher reached by reflection.
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
    tracker_spec_from_issues,
)
from tests.domain.test_criterion_cross_off import callers_of, source_tree
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
#: and a bold row whose label is assembled somewhere else.
CRITERION_SHAPES = (
    re.compile(r"\[\s*[xX]?\s*\]"),
    re.compile(r"AC-"),
    re.compile(r"(Check|Do|Evidence|Class)(\\?\*)*\s*[:|]"),
    re.compile(r"\*\*[^*]*:\*\*"),
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


def _compiles_a_criterion_shape(node: ast.expr) -> bool:
    """Whether this expression is a pattern compiled over such a spelling."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compile"
        and bool(node.args)
        and _is_criterion_shaped(node.args[0])
    )


def _pattern_names(tree: ast.Module) -> set[str]:
    """Every identity bound to a compiled criterion-shaped pattern."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        if node.value is None or not _compiles_a_criterion_shape(node.value):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def _matches_criterion_shaped_text(node: ast.AST, names: set[str]) -> bool:
    """Whether this expression puts criterion-shaped content against text."""
    if isinstance(node, ast.Compare):
        return any(
            isinstance(operator, ast.In | ast.NotIn) for operator in node.ops
        ) and _is_criterion_shaped(node.left)
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in TEXT_MATCHERS and any(
        _is_criterion_shaped(argument) for argument in node.args
    ):
        return True
    if func.attr in RE_MATCHERS and node.args and _is_criterion_shaped(node.args[0]):
        return True
    if func.attr in RE_MATCHERS:
        owner = func.value
        if isinstance(owner, ast.Name) and owner.id in names:
            return True
        if isinstance(owner, ast.Attribute) and owner.attr in names:
            return True
        if _compiles_a_criterion_shape(owner):
            return True
    return False


def _sites(tree: ast.Module, names: set[str]) -> set[str]:
    """Label each scope that matches criterion-shaped content, once per scope."""
    found: set[str] = set()

    def walk(node: ast.AST, label: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            here = label
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                here = child.name if label is None else f"{label}.{child.name}"
            elif label is None:
                here = f"line {child.lineno}"
            if _matches_criterion_shaped_text(child, names):
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
    # Not vacuous: the owner is seen matching its own pattern.
    assert found[RULE_MODULE]


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
#: label, and a pattern compiled in one module and matched in another.
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
