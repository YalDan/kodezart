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
import re
import sys
import warnings
from functools import cache
from pathlib import Path
from typing import get_args

import pytest

from kodezart.domain import fire_spec
from kodezart.domain.criterion_creation import criterion_body
from kodezart.domain.criterion_evidence import apply_evidence, evidence_field_value
from kodezart.domain.fire_spec import (
    _CRITERION_ROW,
    CriterionField,
    _criterion_rows,
    criterion_check,
    criterion_field_bodies,
    replace_criterion_fields,
    tracker_spec_from_issues,
)
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from tests.domain.test_criterion_cross_off import callers_of, source_tree
from tests.identity_guards import _constructor_names
from tests.name_resolution import module_namespace, referencing_definitions

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
#: and a bold row whose label is assembled somewhere else.  They decide what
#: the body scan reports; what a row grammar is, is decided by what it
#: matches, in ``is_row_grammar`` below.
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
    # scope is exactly the one row walker the field reader, the duplicate
    # check and the amendment edit all read rows through, read off the
    # function rather than spelled. A second scope inside the owner is a
    # second statement of the grammar as much as one in another module is.
    assert found[RULE_MODULE] == [_criterion_rows.__name__]
    # A scope name is not enough to close the owner, because a second
    # statement of the grammar matched inside the walker adds no second
    # name.  The names this syntactic reading binds to a compiled
    # criterion-shaped pattern must be exactly the names that denote the one
    # grammar object, so a rename moves both sides at once.  It sees only a
    # ``<module>.compile`` call over a literal, bound by assignment.  The pins
    # that do not read how a pattern is compiled, with their stated limits,
    # are in ``test_the_owner_states_its_row_grammar_once`` below.
    assert _pattern_names(ast.parse(sources[RULE_MODULE])) == {
        name for name, value in vars(fire_spec).items() if value is _CRITERION_ROW
    }


#: Every field a criterion row can carry, read off the grammar's own field type.
ROW_FIELDS = get_args(CriterionField)
#: A full commit identity, the value an Evidence row grades against; forty
#: hex digits, the shorter of the two lengths ``CriterionEvidence`` admits.
GRADED_SHA = "548994ad" * 5
#: The record the Evidence codec writes, as a fenced JSON value.
RECORDED_EVIDENCE = CriterionEvidence(
    graded_sha=GRADED_SHA, test="tests/domain/test_gap.py::test_in_gap"
)
#: What a row's body holds, one of each kind the writers are handed: a graded
#: sha, prose, prose that shows a row as an indented example the way a Do
#: tells its executor what to write, a fenced JSON record, and nothing.
ROW_BODIES = {
    "sha": GRADED_SHA,
    "prose": "The gap answers each criterion by its own key.",
    "prose showing a row": "Record the graded sha on the criterion's own row, as in"
    f"\n\n    **Evidence:** {GRADED_SHA}\n\nand nowhere else.",
    "fenced JSON": evidence_field_value(RECORDED_EVIDENCE),
    "empty": "",
}


@cache
def rendered_bodies() -> tuple[str, ...]:
    """Every criterion body the writers render, and every row of each alone.

    Rendered by the tree's own writers, never spelled here: the creation
    writer over each kind of body that is not empty (it refuses an empty
    Check or Do), the field edit setting each field of the grammar to each
    kind over a created body, and the Evidence codec applied to all of them.
    Each row is then placed alone as well, cut out of its body along the span
    the owner's walker reads it at.
    """
    created = [
        criterion_body(parent_key="KZ-1", check=text, do=text)
        for text in ROW_BODIES.values()
        if text.strip()
    ]
    edited = [
        replace_criterion_fields(body, replacements={field: text})
        for body in created
        for field in ROW_FIELDS
        for text in ROW_BODIES.values()
    ]
    bodies = [*created, *edited]
    bodies += [apply_evidence(body=body, evidence=RECORDED_EVIDENCE) for body in bodies]
    rows = [
        body[row.begin : row.end] for body in bodies for row in _criterion_rows(body)
    ]
    return tuple(dict.fromkeys([*bodies, *rows]))


@cache
def _label_spots(text: str) -> tuple[tuple[int, int], ...]:
    """Where each rendered label ``**{field}:**`` begins in *text*, with its name.

    Every occurrence, in a row or in prose that shows one.
    """
    spots: list[tuple[int, int]] = []
    for field in ROW_FIELDS:
        label = f"**{field}:**"
        at = text.find(label)
        while at >= 0:
            spots.append((at, len(field)))
            at = text.find(label, at + 1)
    return tuple(spots)


def covers_a_label(text: str, start: int, end: int) -> bool:
    """Whether ``text[start:end]`` takes in a rendered label's field name and bold.

    The whole field name, and with it the label's bold on at least one side:
    the ``**`` before the name, or the colon and a ``*`` after it.  The name
    alone, or the name and its colon, is a word prose uses as well; the name
    inside its bold is the label and nothing else.
    """
    for at, length in _label_spots(text):
        name, after = at + 2, at + 2 + length
        if start <= name and end >= after and (start <= at + 1 or end >= after + 2):
            return True
    return False


#: The flags a pattern is also tried under, each added alone to its own: the
#: ones that change what a row pattern matches in a body, its case and how
#: its anchors and dots meet the lines.
TRIED_FLAGS = (re.NOFLAG, re.IGNORECASE, re.MULTILINE, re.DOTALL)


def matches_a_rendered_row(pattern: re.Pattern[str] | re.Pattern[bytes]) -> bool:
    """Whether *pattern* matches a rendered body across a label.

    Decided by what the pattern does over the bodies the writers render, not
    by how it is written: any match, in any rendered body or row alone, that
    covers a label recognises the row by it.  Tried with its own flags, and
    with each of ``TRIED_FLAGS`` added, so a pattern that parses rows only
    under a flag handed to it at its call is tried under that flag.
    """
    for extra in TRIED_FLAGS:
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            try:
                tried = re.compile(pattern.pattern, pattern.flags | extra)
            except (re.error, ValueError):
                continue
        for text in rendered_bodies():
            subject = text if isinstance(tried.pattern, str) else text.encode("ascii")
            if any(
                covers_a_label(text, found.start(), found.end())
                for found in tried.finditer(subject)  # the rendered texts are ASCII
            ):
                return True
    return False


@cache
def is_row_grammar(text: str, flags: int = 0) -> bool:
    """Whether a literal is a row grammar: over the rendered bodies, it takes a label.

    Read two ways.  As plain text, the way a ``startswith``, ``split`` or
    ``==`` parser holds it: some occurrence of it in a rendered body covers a
    label.  As a pattern, compiled with the flags spelled beside it at its
    call: ``matches_a_rendered_row``.  A text ``re`` warns about still
    compiles, and is read as the pattern it compiles to.
    """
    for body in rendered_bodies():
        at = body.find(text) if text else -1
        while at >= 0:
            if covers_a_label(body, at, at + len(text)):
                return True
            at = body.find(text, at + 1)
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        try:
            pattern = re.compile(text, flags)
        except (re.error, ValueError):
            return False
    return matches_a_rendered_row(pattern)


#: The ``re`` entry points whose first argument is a pattern, each with the
#: position its flags take when they are passed without a keyword.
FLAG_POSITIONS = {
    "compile": 1,
    "match": 2,
    "fullmatch": 2,
    "search": 2,
    "findall": 2,
    "finditer": 2,
    "split": 3,
    "sub": 4,
    "subn": 4,
}


def _flag_value(node: ast.expr) -> int:
    """The ``re`` flags an expression spells: a flag by name, or several or-ed."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _flag_value(node.left) | _flag_value(node.right)
    word = (
        node.attr
        if isinstance(node, ast.Attribute)
        else node.id
        if isinstance(node, ast.Name)
        else None
    )
    value = getattr(re, word, None) if word is not None else None
    return int(value) if isinstance(value, re.RegexFlag) else 0


def call_flags(tree: ast.Module) -> dict[int, int]:
    """The flags spelled beside each pattern argument of an ``re`` call in *tree*.

    Keyed by the pattern argument's node: the first positional argument or
    ``pattern=``, of a call to one of ``FLAG_POSITIONS`` by attribute or by
    name, with its ``flags=`` keyword or its flags by position.  Only flags
    spelled by name are read; a flag value bound to a name first is not.
    """
    found: dict[int, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        word = (
            func.attr
            if isinstance(func, ast.Attribute)
            else func.id
            if isinstance(func, ast.Name)
            else None
        )
        if word not in FLAG_POSITIONS:
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        pattern = node.args[0] if node.args else keywords.get("pattern")
        position = FLAG_POSITIONS[word]
        flags = (
            keywords.get("flags")
            if "flags" in keywords
            else node.args[position]
            if len(node.args) > position
            else None
        )
        if pattern is not None and flags is not None:
            found[id(pattern)] = _flag_value(flags)
    return found


def row_literals(tree: ast.Module) -> list[tuple[str, str]]:
    """Every literal that is a row grammar, by the scope holding it.

    Whatever it is handed to — a compile call by position or by keyword, a
    name bound earlier, a matcher, a string built for writing — a literal is
    read where it is written, and as a pattern under the flags spelled beside
    it at its ``re`` call.  Folded the way ``_literal_text`` folds, and
    counted once at the widest expression that is one; a literal that is none
    is read part by part.
    """
    found: list[tuple[str, str]] = []
    flags = call_flags(tree)

    def walk(node: ast.AST, label: str) -> None:
        for child in ast.iter_child_nodes(node):
            here = label
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                here = child.name if label == "<module>" else f"{label}.{child.name}"
            text = _literal_text(child) if isinstance(child, ast.expr) else None
            if text is not None and is_row_grammar(text, flags.get(id(child), 0)):
                found.append((here, text))
                continue
            walk(child, here)

    walk(tree, "<module>")
    return sorted(found)


@cache
def _row_literals_of(source: str) -> tuple[tuple[str, str], ...]:
    """One module's row-grammar literals, read once per text."""
    return tuple(row_literals(ast.parse(source)))


def row_grammar_literals(sources: dict[str, str]) -> dict[str, list[tuple[str, str]]]:
    """Every module of *sources* holding a row-grammar literal, with each one."""
    return {
        module: list(_row_literals_of(source))
        for module, source in sorted(sources.items())
        if _row_literals_of(source)
    }


@cache
def _row_patterns_of(module: str, source: str) -> tuple[str, ...]:
    """The names one module's globals bind to a pattern matching a rendered row."""
    return tuple(
        sorted(
            name
            for name, value in module_namespace(module, source).items()
            if isinstance(value, re.Pattern) and matches_a_rendered_row(value)
        )
    )


def row_patterns(sources: dict[str, str]) -> dict[str, list[str]]:
    """Every module of *sources* whose globals hold a row pattern, with its names.

    The module as it runs: the package's own module for its text on disk, and
    a planted or changed text run fresh under the same name.  So what is read
    is the compiled object itself, however it was compiled, and whichever
    module it was compiled in and imported from.  Module-level names only.
    """
    return {
        module: list(_row_patterns_of(module, source))
        for module, source in sorted(sources.items())
        if _row_patterns_of(module, source)
    }


def grammar_uses(tree: ast.Module, names: frozenset[str]) -> list[tuple[str, str]]:
    """Every read of the grammar object, by scope and by what is read off it.

    A read of one of *names* as the receiver of an attribute is recorded
    under that attribute; any other read — handed on, aliased, returned — is
    recorded as bare, and so is a string constant spelling one of *names*,
    the way ``globals()`` or ``getattr`` is handed the word.
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
            ) or (isinstance(child, ast.Constant) and child.value in names):
                found.append((here, "<bare>"))
            walk(child, here)

    walk(tree, "<module>")
    return sorted(found)


def owner_grammar_readings(
    source: str,
) -> tuple[list[str], list[tuple[str, str]], list[tuple[str, str]]]:
    """What the owner compiles, writes and reads of its row grammar."""
    tree = ast.parse(source)
    return (
        row_patterns({RULE_MODULE: source}).get(RULE_MODULE, []),
        row_literals(tree),
        grammar_uses(tree, GRAMMAR_NAMES),
    )


#: Every name the owner binds to the one grammar object, by identity.
GRAMMAR_NAMES = frozenset(
    name for name, value in vars(fire_spec).items() if value is _CRITERION_ROW
)


def test_a_row_grammar_is_what_matches_a_rendered_row():
    """The rendered bodies carry every field with every kind of body, as rows.

    So the predicate below speaks for the rows the writers really render, and
    a field the grammar gains is a row it is asked about.  Every row is read
    back through the owner's own field reader with the body it was given,
    alone and inside its body.  The texts are ASCII, so a bytes pattern is
    matched at the same offsets as a text pattern.
    """
    assert ROW_FIELDS
    bodies = rendered_bodies()
    assert all(text.isascii() for text in bodies)
    for field in ROW_FIELDS:
        for kind, text in ROW_BODIES.items():
            read = {
                body
                for body in bodies
                if criterion_field_bodies(body, field=field) == (text.strip(),)
            }
            assert any(len(_criterion_rows(body)) == 1 for body in read), kind
            assert any(len(_criterion_rows(body)) > 1 for body in read), kind
    assert matches_a_rendered_row(_CRITERION_ROW)


@pytest.mark.parametrize(
    ("text", "grammar"),
    [
        (_CRITERION_ROW.pattern, True),
        (r"^ {0,3}\*\*(\w+):\*\*(.*)$", True),
        (r"^\s*\*\*(Evidence):\*\*(.*)$", True),
        (r"Evidence:\*\*", True),
        ("**Evidence:**", True),
        ("**Check:** ", True),
        ("**Evidence:", True),
        ("Evidence:**", True),
        ("**Evidence", True),
        (r"\*\*evidence:\*\*(.*)", True),
        (r"\n\*\*Evidence:\*\*(.*)", True),
        (r"^ {4}\*\*Evidence:\*\*(.*)$", True),
        (r"(?<=\n)\*\*Evidence:\*\*", True),
        (r"^\*\*Evidence:\*\*$", True),
        (r"^\*\*[Ee]vidence:\*\*\s*`?([0-9a-f]{7,40})", True),
        ("Evidence:", False),
        (fire_spec._FENCE.pattern, False),
        (fire_spec._HEADING.pattern, False),
        (fire_spec._LIST_ITEM.pattern, False),
        ("", False),
        ("Evidence", False),
        ("**", False),
        ("the Check a criterion states, and the Evidence it records", False),
    ],
)
def test_a_literal_is_a_row_grammar_by_what_it_matches(text, grammar):
    assert is_row_grammar(text) is grammar


#: A pattern that parses rows only under two flags at once, case and lines,
#: which no single tried flag gives it.
TWO_FLAG_ROW = r"\n^\*\*evidence:\*\*"
#: Each way flags are handed to an ``re`` call beside the pattern.
FLAG_SPELLINGS = {
    "compile by position": "re.compile(P, re.I | re.M)",
    "compile by keyword": "re.compile(P, flags=re.IGNORECASE | re.MULTILINE)",
    "pattern and flags by keyword": "re.compile(pattern=P, flags=re.I | re.M)",
    "match by position": "re.match(P, body, re.I | re.M)",
    "search by keyword": "re.search(P, body, flags=re.I | re.M)",
    "findall by position": "re.findall(P, body, re.I | re.M)",
    "finditer by position": "re.finditer(P, body, re.I | re.M)",
    "split by position": "re.split(P, body, 0, re.I | re.M)",
    "sub by position": "re.sub(P, '', body, 0, re.I | re.M)",
    "matcher imported by name": "findall(P, body, re.I | re.M)",
}


def test_a_pattern_that_needs_two_flags_is_no_grammar_without_them():
    assert not is_row_grammar(TWO_FLAG_ROW)
    assert is_row_grammar(TWO_FLAG_ROW, re.IGNORECASE | re.MULTILINE)


@pytest.mark.parametrize("spelling", sorted(FLAG_SPELLINGS))
def test_the_flags_spelled_at_a_call_decide_its_pattern(spelling):
    call = FLAG_SPELLINGS[spelling].replace("P", f"r'{TWO_FLAG_ROW}'", 1)
    source = f"import re\nfrom re import findall\ndef rows(body):\n    return {call}\n"
    flagless = source.replace("re.I | re.M", "0").replace(
        "re.IGNORECASE | re.MULTILINE", "0"
    )
    assert "re.I" not in flagless

    assert row_literals(ast.parse(source)) == [("rows", TWO_FLAG_ROW)]
    assert row_literals(ast.parse(flagless)) == []


#: Why the creation writer may hold row labels: each of its three is one.
_CREATION_WRITER = (
    "Renders a new criterion's three rows, then reads them back through the "
    "owner's field reader."
)
#: The literals outside the owner that are a row grammar because they write
#: rows, each with why it may be: every one renders rows for a writer, and
#: none reads one.  The creation writer's template is read part by part,
#: because no rendered body leaves both its Check and its Do empty.
ROW_WRITERS = {
    ("domain/criterion_creation.py", "criterion_body", "**Check:** "): (
        _CREATION_WRITER
    ),
    ("domain/criterion_creation.py", "criterion_body", "\n\n**Do:** "): (
        _CREATION_WRITER
    ),
    ("domain/criterion_creation.py", "criterion_body", "\n\n**Evidence:**\n"): (
        _CREATION_WRITER
    ),
    (
        "domain/criterion_evidence.py",
        "render_evidence_field",
        "**Evidence:**",
    ): "Renders the Evidence row label the codec writes.",
}
#: The literals outside the owner that are a row grammar only by running from
#: one fenced record to another, each with why it reads no row.
FENCED_BLOCK_PATTERNS = {
    (
        "adapters/linear/markers.py",
        "LinearMarkers.grant_pattern",
        r"^```\n(?P<payload>.*?)\n```$",
    ): "The claim marker's fenced block, compiled with DOTALL and MULTILINE. Read "
    "with its configured info string folded away, its lazy payload runs from one "
    "fence line to the next, so over a body with two fenced records it takes in "
    "the row between them; as compiled it opens only on the claim info string, "
    "which no criterion body carries, and it reads a marker's payload, never a "
    "row.",
}


def test_no_literal_outside_the_owner_is_a_row_grammar():
    """A Check or Evidence row parser outside the owner has no literal to hold.

    Every literal of the tree is read, and one that is a row grammar — a
    pattern text that matches a rendered row, or a text holding a rendered
    label — is allowed only as the owner's own grammar, once, as a writer's
    row registered with its reason, or as a fenced-block pattern registered
    with why it reads no row.  A second grammar compiled anywhere,
    handed on by name, imported into the owner's reader or matched with a
    string method adds a literal, and reds.

    Not seen: a grammar assembled at call time from pieces none of which is
    a row grammar alone, such as a label built around a field name.
    """
    found = row_grammar_literals(source_tree())

    assert found.pop(RULE_MODULE) == [("<module>", _CRITERION_ROW.pattern)]
    assert sorted(
        (module, scope, text)
        for module, literals in found.items()
        for scope, text in literals
    ) == sorted([*ROW_WRITERS, *FENCED_BLOCK_PATTERNS])


def test_no_module_outside_the_owner_binds_a_row_pattern():
    """Every module of the package, as it runs, binds a row pattern only in the owner.

    Read off the compiled objects in each module's globals, so a pattern
    compiled in one module and imported into another is seen in both, and
    inside the owner the one such object is the grammar, under its one name.
    """
    assert row_patterns(source_tree()) == {RULE_MODULE: sorted(GRAMMAR_NAMES)}


EVIDENCE_ROW_TEXT = r"^\s*\*\*(Evidence):\*\*(.*)$"
#: An Evidence parser spelled in lower case, which parses rows only under the
#: case flag handed to it at its call.
LOWERCASE_EVIDENCE_TEXT = r"^\*\*evidence:\*\*[ \t]*(.*)$"
#: An Evidence parser that constrains what follows the label to a graded sha.
GRADED_SHA_TEXT = r"^\*\*[Ee]vidence:\*\*\s*`?([0-9a-f]{7,40})"
#: The Evidence label alone on its line, the way both writers render it.
LABEL_ONLY_TEXT = r"^\*\*Evidence:\*\*$"
#: Each way a second Check or Evidence row parser could arrive outside the
#: owner, as the module texts it would arrive as, with what it must add to
#: the literals and to the compiled patterns read above.
PLANTED_ROW_GRAMMARS = {
    "evidence parser compiled elsewhere and imported into the walker": (
        {
            "domain/evidence_rows.py": "import re\n"
            f"EVIDENCE_ROW = re.compile(r'{EVIDENCE_ROW_TEXT}')\n",
            RULE_MODULE: source_tree()[RULE_MODULE].replace(
                "            row = None if delimiter is not None"
                " else _CRITERION_ROW.match(line)\n",
                "            from kodezart.domain.evidence_rows"
                " import EVIDENCE_ROW as _E\n"
                "\n"
                "            row = None if delimiter is not None"
                " else _CRITERION_ROW.match(line) or _E.match(line)\n",
            ),
        },
        [("domain/evidence_rows.py", "<module>", EVIDENCE_ROW_TEXT)],
        {"domain/evidence_rows.py": ["EVIDENCE_ROW"]},
    ),
    "pattern text imported and matched by re.match": (
        {
            "domain/evidence_rows.py": f"EVIDENCE_TEXT = r'{EVIDENCE_ROW_TEXT}'\n",
            "services/evidence_reader.py": "import re\n"
            "def evidence(body):\n"
            "    from kodezart.domain.evidence_rows import EVIDENCE_TEXT\n"
            "    return [\n"
            "        re.match(EVIDENCE_TEXT, line) for line in body.splitlines()\n"
            "    ]\n",
        },
        [("domain/evidence_rows.py", "<module>", EVIDENCE_ROW_TEXT)],
        {},
    ),
    "startswith parser in a service": (
        {
            "services/evidence_reader.py": "def evidence(body):\n"
            "    return [\n"
            "        line.removeprefix('**Evidence:**')\n"
            "        for line in body.splitlines()\n"
            "        if line.startswith('**Evidence:**')\n"
            "    ]\n",
        },
        [
            ("services/evidence_reader.py", "evidence", "**Evidence:**"),
            ("services/evidence_reader.py", "evidence", "**Evidence:**"),
        ],
        {},
    ),
    "lowercase findall with flags at the call in a service": (
        {
            "services/evidence_reader.py": "import re\n"
            "def evidence(body: str) -> list[str]:\n"
            f"    return re.findall(r'{LOWERCASE_EVIDENCE_TEXT}', body,"
            " re.IGNORECASE | re.MULTILINE)\n",
        },
        [("services/evidence_reader.py", "evidence", LOWERCASE_EVIDENCE_TEXT)],
        {},
    ),
    "sha-constrained pattern compiled at module level": (
        {
            "services/evidence_sha.py": "import re\n"
            f"GRADED_SHA = re.compile(r'{GRADED_SHA_TEXT}', re.MULTILINE)\n"
            "def graded_sha(body: str) -> str | None:\n"
            "    found = GRADED_SHA.search(body)\n"
            "    return found[1] if found else None\n",
        },
        [("services/evidence_sha.py", "<module>", GRADED_SHA_TEXT)],
        {"services/evidence_sha.py": ["GRADED_SHA"]},
    ),
    "label alone on its line, compiled by keyword": (
        {
            "services/evidence_label.py": "import re\n"
            f"EVIDENCE_LABEL = re.compile(pattern=r'{LABEL_ONLY_TEXT}', flags=re.M)\n"
            "def evidence_rows(body: str) -> int:\n"
            "    return len(EVIDENCE_LABEL.findall(body))\n",
        },
        [("services/evidence_label.py", "<module>", LABEL_ONLY_TEXT)],
        {"services/evidence_label.py": ["EVIDENCE_LABEL"]},
    ),
}


@pytest.mark.parametrize("planted", sorted(PLANTED_ROW_GRAMMARS))
def test_a_row_parser_planted_outside_the_owner_is_a_literal_and_a_pattern(planted):
    """A second row parser outside the owner is seen by what it holds.

    Planted as the module texts it would arrive as — each planted text is
    new to the tree, the owner's reader rewired included — and read by both
    whole-tree readings: its literal, and its compiled pattern when it binds
    one at module level.
    """
    files, literals, patterns = PLANTED_ROW_GRAMMARS[planted]
    sources = {**source_tree(), **files}
    assert all(source_tree().get(module) != text for module, text in files.items())

    found = row_grammar_literals(sources)
    found.pop(RULE_MODULE)

    assert sorted(
        (module, scope, text)
        for module, literals in found.items()
        for scope, text in literals
    ) == sorted([*ROW_WRITERS, *FENCED_BLOCK_PATTERNS, *literals])
    assert row_patterns(sources) == {
        RULE_MODULE: sorted(GRAMMAR_NAMES),
        **patterns,
    }


def test_the_owner_states_its_row_grammar_once():
    """One row grammar in the owner, pinned on what it is and not how it is written.

    What a row grammar is, is decided by what it matches: a pattern that
    matches a rendered row across its label, or a text holding a rendered
    label.  Three readings of the owner, none keyed on the spelling of a
    compile call.  What executes: run as a module, the owner binds exactly one
    compiled pattern that matches a rendered row, under the one name that
    denotes the grammar object, so a second such pattern at module level reds
    however it was compiled and however generic its text, and so does the
    identical text compiled again under a second name, which re's cache hands
    back as the same object.  What is written: its row-grammar literals are
    the grammar's own text once, at module level, so a second pattern written
    anywhere in the owner, a sanctioned scope included, adds a literal.  What
    is read: the grammar object is read only as the receiver of ``match`` in
    the one row walker the field reader, the duplicate check and the
    amendment edit all read rows through, so a pattern derived from its text,
    the object handed on under another name, or the object reached by a
    string spelling its name, reds.

    Not seen: a grammar assembled at call time from pieces none of which is a
    row grammar alone, and a name built at run time; the behavioural floor
    named in the module docstring is where a reader built that way dies.
    """
    compiled, literals, uses = owner_grammar_readings(source_tree()[RULE_MODULE])

    assert len(GRAMMAR_NAMES) == 1
    assert compiled == sorted(GRAMMAR_NAMES)
    assert literals == [("<module>", _CRITERION_ROW.pattern)]
    assert uses == [(_criterion_rows.__name__, "match")]


@cache
def _grammar_referrers_of(module: str, source: str) -> tuple[str, ...]:
    """The definitions of one module that refer to the grammar object."""
    return tuple(
        name
        for name, _ in referencing_definitions(
            module,
            ast.parse(source),
            module_namespace(module, source),
            wanted=(_CRITERION_ROW,),
        )
    )


def grammar_referrers(sources: dict[str, str]) -> list[tuple[str, str]]:
    """Every definition of *sources* that refers to the grammar object, by module.

    Read by identity, the way ``referencing_definitions`` reads it: a name an
    import anywhere in the module binds to the object, an attribute that is
    the object, a string naming it, called or handed on — so a bound
    ``match`` of it mapped over a body's lines is a reference like a call.
    """
    return sorted(
        (module, name)
        for module, source in sorted(sources.items())
        for name in _grammar_referrers_of(module, source)
    )


def test_the_walker_is_the_one_definition_referring_to_the_grammar():
    """Over the whole tree, the grammar object is referred to by the walker alone.

    Every definition of every module that refers to the object, read by what
    its names denote rather than how they are spelled, is exactly the one row
    walker the field reader, the duplicate check and the amendment edit read
    rows through.
    """
    assert grammar_referrers(source_tree()) == [(RULE_MODULE, _criterion_rows.__name__)]


#: Each way the grammar object could be reused outside the walker, as the
#: module text it would arrive as, with the definition it adds.
GRAMMAR_REUSES = {
    "bound match mapped over lines after a function-level import": (
        "services/evidence_reader.py",
        "def evidence(body):\n"
        f"    from {criterion_field_bodies.__module__} import _CRITERION_ROW\n"
        "\n"
        "    return [\n"
        "        found[2]\n"
        "        for found in map(_CRITERION_ROW.match, body.splitlines())\n"
        "        if found and found[1] == 'Evidence'\n"
        "    ]\n",
        "evidence",
    ),
    "aliased at module level and matched": (
        "services/evidence_reader.py",
        f"from {criterion_field_bodies.__module__} import _CRITERION_ROW as ROW\n"
        "\n"
        "def evidence(body):\n"
        "    return [ROW.match(line) for line in body.splitlines()]\n",
        "evidence",
    ),
}


@pytest.mark.parametrize("planted", sorted(GRAMMAR_REUSES))
def test_a_reuse_of_the_grammar_outside_the_walker_is_a_referring_definition(planted):
    module, text, definition = GRAMMAR_REUSES[planted]
    sources = {**source_tree(), module: text}
    assert module not in source_tree()

    assert grammar_referrers(sources) == sorted(
        [(RULE_MODULE, _criterion_rows.__name__), (module, definition)]
    )


ROW_TEXT = r"^ {0,3}\*\*(Check|Do|Evidence|Class):\*\*(.*)$"
EVIDENCE_TEXT = r"^ {0,3}\*\*Evidence:\*\*(.*)$"
GENERIC_ROW_TEXT = r"^ {0,3}\*\*(\w+):\*\*(.*)$"
#: Each way a second statement of the row grammar can be written into the
#: owner: appended at module level, or put in place of the walker's one read.
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
    "inline in the walker": (
        "_CRITERION_ROW.match(line)",
        f"re.compile(pattern=r'{ROW_TEXT}').match(line)",
    ),
    "recompiled in the walker": (
        "_CRITERION_ROW.match(line)",
        "re.compile(_CRITERION_ROW.pattern).match(line)",
    ),
    "generic row pattern used in a third function": (
        None,
        f"_ANY_ROW = re.compile(r'{GENERIC_ROW_TEXT}')\n"
        "\n"
        "def evidence_rows(body):\n"
        "    return [\n"
        "        found[2]\n"
        "        for found in map(_ANY_ROW.match, body.splitlines())\n"
        "        if found and found.group(1) == 'Evidence'\n"
        "    ]\n",
    ),
    "lowercase findall with flags at the call": (
        None,
        "def evidence_rows(body: str) -> list[str]:\n"
        f"    return re.findall(r'{LOWERCASE_EVIDENCE_TEXT}', body,"
        " re.IGNORECASE | re.MULTILINE)\n",
    ),
    "grammar reached through globals()": (
        None,
        "def evidence_rows(body):\n"
        "    row = globals()['_CRITERION_ROW']\n"
        "    return [\n"
        "        found[2]\n"
        "        for found in map(row.match, body.splitlines())\n"
        "        if found and found.group(1) == 'Evidence'\n"
        "    ]\n",
    ),
}


@pytest.mark.parametrize("planted", sorted(SECOND_GRAMMARS))
def test_a_second_row_grammar_in_the_owner_changes_what_it_states(planted):
    source = source_tree()[RULE_MODULE]
    anchor, text = SECOND_GRAMMARS[planted]
    if anchor is None:
        changed = source + "\n" + text
    else:
        assert source.count(anchor) == 1
        changed = source.replace(anchor, text, 1)

    assert owner_grammar_readings(changed) != owner_grammar_readings(source)


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
