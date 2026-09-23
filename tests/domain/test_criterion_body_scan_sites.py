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
argument; a matcher imported by bare name out of the pattern library
(``from re import findall``, or ``compile as rx``), because a matcher call
is read as an attribute of its module and not as a plain name; a matcher
called with keyword arguments, such as ``re.search(pattern=…, string=…)``,
because only a call's positional arguments are read; and any matcher
reached by reflection.

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
import importlib.util
import re
import sys
from functools import cache
from pathlib import Path

import pytest

from kodezart.domain import fire_spec
from kodezart.domain.fire_spec import (
    _CRITERION_ROW,
    _criterion_rows,
    criterion_check,
    criterion_field_bodies,
    replace_criterion_fields,
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
    # Not vacuous, and closed inside the owner too: the owner's own matching
    # scopes are exactly the two the grammar is written as — the field reader
    # and the row locator the amendment edit addresses rows through — read off
    # the functions rather than spelled. A third scope inside the owner is a
    # second statement of the grammar as much as one in another module is.
    assert found[RULE_MODULE] == sorted(
        {criterion_field_bodies.__name__, _criterion_rows.__name__}
    )
    # A scope name is not enough to close the owner, because a second
    # statement of the grammar matched inside one of those two scopes adds no
    # third name.  The names this syntactic reading binds to a compiled
    # criterion-shaped pattern must be exactly the names that denote the one
    # grammar object, so a rename moves both sides at once.  It sees only a
    # ``<module>.compile`` call over a literal, bound by assignment.  The pins
    # that do not read how a pattern is compiled, with their stated limits,
    # are in ``test_the_owner_states_its_row_grammar_once`` below.
    assert _pattern_names(ast.parse(sources[RULE_MODULE])) == {
        name for name, value in vars(fire_spec).items() if value is _CRITERION_ROW
    }


def _docstring_ids(tree: ast.Module) -> set[int]:
    """The nodes that are a module's, a class's or a function's docstring."""
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }


def shaped_literals(tree: ast.Module) -> list[tuple[str, str]]:
    """Every criterion-shaped literal outside a docstring, by the scope holding it.

    Whatever it is handed to — a compile call by position or by keyword, a
    name bound earlier, a matcher, a string built for writing — a literal is
    read where it is written.  Folded the way ``_literal_text`` folds, and
    counted once at the widest expression that spells it.
    """
    docstrings = _docstring_ids(tree)
    found: list[tuple[str, str]] = []

    def walk(node: ast.AST, label: str) -> None:
        for child in ast.iter_child_nodes(node):
            here = label
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                here = child.name if label == "<module>" else f"{label}.{child.name}"
            if (
                isinstance(child, ast.expr)
                and id(child) not in docstrings
                and _is_criterion_shaped(child)
            ):
                found.append((here, _literal_text(child) or ""))
                continue
            walk(child, here)

    walk(tree, "<module>")
    return sorted(found)


def grammar_uses(tree: ast.Module, names: frozenset[str]) -> list[tuple[str, str]]:
    """Every read of the grammar object, by scope and by what is read off it.

    A read of one of *names* as the receiver of an attribute is recorded
    under that attribute; any other read — handed on, aliased, returned — is
    recorded as bare.
    """
    found: list[tuple[str, str]] = []

    def walk(node: ast.AST, label: str) -> None:
        for child in ast.iter_child_nodes(node):
            here = label
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                here = child.name if label == "<module>" else f"{label}.{child.name}"
            if (
                isinstance(child, ast.Attribute)
                and isinstance(child.value, ast.Name)
                and child.value.id in names
            ):
                found.append((here, child.attr))
                continue
            if (
                isinstance(child, ast.Name)
                and child.id in names
                and isinstance(child.ctx, ast.Load)
            ):
                found.append((here, "<bare>"))
            walk(child, here)

    walk(tree, "<module>")
    return sorted(found)


def compiled_row_patterns(source: str, directory: Path) -> list[str]:
    """The names the owner's executed source binds to a criterion-shaped pattern.

    The source is loaded as a module of its own, so what is read is the
    compiled objects themselves and not how any of them was spelled: a
    keyword, an aliased ``compile``, a name bound earlier and a text derived
    from the grammar's own all end as a pattern object here.  Module-level
    names only.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "criterion_grammar_owner.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("criterion_grammar_owner", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return sorted(
        name
        for name, value in vars(module).items()
        if isinstance(value, re.Pattern)
        and any(
            shape.search(
                value.pattern
                if isinstance(value.pattern, str)
                else value.pattern.decode("latin-1")
            )
            for shape in CRITERION_SHAPES
        )
    )


def owner_grammar_readings(
    source: str, directory: Path
) -> tuple[list[str], list[tuple[str, str]], list[tuple[str, str]]]:
    """What the owner compiles, writes and reads of its row grammar."""
    tree = ast.parse(source)
    return (
        compiled_row_patterns(source, directory),
        shaped_literals(tree),
        grammar_uses(tree, GRAMMAR_NAMES),
    )


#: Every name the owner binds to the one grammar object, by identity.
GRAMMAR_NAMES = frozenset(
    name for name, value in vars(fire_spec).items() if value is _CRITERION_ROW
)


def test_the_owner_states_its_row_grammar_once(tmp_path):
    """One row grammar in the owner, pinned on what it is and not how it is written.

    Three readings, none keyed on the spelling of a compile call.  What
    executes: loaded as a module of its own, the owner binds exactly one
    criterion-shaped compiled pattern, under the one name that denotes the
    grammar object, so a second pattern at module level reds however it was
    compiled, and so does the identical text compiled again under a second
    name, which re's cache hands back as the same object.  What is written:
    its criterion-shaped literals are the grammar's own text once, at module
    level, and the two row templates the amendment writer renders, so a
    second pattern written anywhere in the owner, a sanctioned scope
    included, adds a literal.  What is read: the grammar object is read only
    as the receiver of ``match`` in the two sanctioned scopes, so a pattern
    derived from its text, or the object handed on under another name, reds.

    Not seen: a pattern text assembled at run time from fragments none of
    which is criterion-shaped, compiled inside a function, and any reach by
    reflection; the behavioural floor named in the module docstring is where
    a reader built that way dies.
    """
    compiled, literals, uses = owner_grammar_readings(
        source_tree()[RULE_MODULE], tmp_path
    )

    assert len(GRAMMAR_NAMES) == 1
    assert compiled == sorted(GRAMMAR_NAMES)
    assert literals == sorted(
        [
            ("<module>", _CRITERION_ROW.pattern),
            (replace_criterion_fields.__name__, "**:** \n\n"),
            (replace_criterion_fields.__name__, "\n\n**:** \n"),
        ]
    )
    assert uses == sorted(
        (scope, "match")
        for scope in (criterion_field_bodies.__name__, _criterion_rows.__name__)
    )


ROW_TEXT = r"^ {0,3}\*\*(Check|Do|Evidence|Class):\*\*(.*)$"
EVIDENCE_TEXT = r"^ {0,3}\*\*Evidence:\*\*(.*)$"
#: Each way a second statement of the row grammar can be written into the
#: owner: appended at module level, or put in place of a sanctioned read.
SECOND_GRAMMARS = {
    "keyword compile": (
        None,
        f"_EVIDENCE_ROW = re.compile(pattern=r'{EVIDENCE_TEXT}')\n",
    ),
    "name bound earlier": (
        None,
        f"_EVIDENCE_TEXT = r'{EVIDENCE_TEXT}'\n"
        "_EVIDENCE_ROW = re.compile(_EVIDENCE_TEXT)\n",
    ),
    "aliased compile": (
        None,
        f"from re import compile as _rx\n_EVIDENCE_ROW = _rx(r'{EVIDENCE_TEXT}')\n",
    ),
    "text derived from the grammar": (
        None,
        "_EVIDENCE_ROW = re.compile("
        "_CRITERION_ROW.pattern.replace('Check', 'Evidence'))\n",
    ),
    "identical text compiled again": (
        None,
        f"_ROW_AGAIN = re.compile(r'{ROW_TEXT}')\n",
    ),
    "alias of the grammar object": (None, "_ROW = _CRITERION_ROW\n"),
    "inline in a sanctioned scope": (
        "_CRITERION_ROW.match(line)",
        f"re.compile(pattern=r'{ROW_TEXT}').match(line)",
    ),
    "recompiled in a sanctioned scope": (
        "_CRITERION_ROW.match(line)",
        "re.compile(_CRITERION_ROW.pattern).match(line)",
    ),
}


@pytest.mark.parametrize("planted", sorted(SECOND_GRAMMARS))
def test_a_second_row_grammar_in_the_owner_changes_what_it_states(planted, tmp_path):
    source = source_tree()[RULE_MODULE]
    anchor, text = SECOND_GRAMMARS[planted]
    if anchor is None:
        changed = source + "\n" + text
    else:
        assert source.count(anchor) == 2
        changed = source.replace(anchor, text, 1)

    head = owner_grammar_readings(source, tmp_path / "head")
    assert owner_grammar_readings(changed, tmp_path / "planted") != head


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
#: label, a pattern compiled in one module and matched in another, and a
#: scan for an authored ``AC-n`` identity alone — five in all.
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
