"""One criterion resolution site, and no writer locating a target by text.

A criterion is addressed by the key of its own sub-issue, resolved against a
fresh read of the criterion family, at exactly one place.  Two things defeat
that rule without any behavioural assertion noticing.

The first is a *second* resolver.  It returns the row the sanctioned one
would return, on every input anybody thinks to write a test for, right up to
the run where the two reads disagree about membership — so a suite of
behaviours cannot see it.  Only the shape of the source can.

The second is a resolver that does not address a criterion by key at all: one
that finds its write target by matching the criterion's *text*, or by
scanning a parent body for task-list checkbox syntax.  Both re-derive
identity from prose a model can echo back changed, and both look perfectly
correct until the prose drifts.

So the demonstration reads the syntax tree of ``src/kodezart`` and asks the
three questions directly.

**A resolution is a narrowing, however it is spelled.**  An earlier reading
of this rule saw only a comparison — ``row.issue_key == criterion_key`` — and
so missed the idiomatic spellings: a dict built on row keys and then indexed
(``by_key[criterion_key]``) or asked (``by_key.get(criterion_key)``), a list
of row keys turned into a position (``keys.index(criterion_key)``), and a
one-line alias (``wanted = criterion_key``) that renames the key out of the
comparison's sight.  The reading here follows names across assignment and
asks the question of subscripts, of ``.get`` and ``.index`` calls, and of
comparisons alike, so that all four spell the same offence.

Likewise, matching a criterion's prose is not only ``criterion.text in
line``: it is any comparison or search that puts a criterion's own text —
directly, or under an alias — against a tracker row's ``title`` or
``description``.  And a checkbox scan is not only a literal tick: a compiled
pattern that picks task-list lines out of a body and leaves ordinary prose
alone is the same scan, whatever its constant is named.  A string constant is
read as a checkbox scan when it carries a box outright, or when it compiles
to a pattern that matches checkbox lines and only those.

**These remain lint approximations, and the approximations are stated rather
than hidden.**  A resolver written against tersely named locals (``r.key ==
k``) is still missed, and an equality guard between two differently named
keys is still a false positive.  A guard rejecting on ``!=``, and the
duplicate checks that compare a row's key against an accumulator built from
the same family, are deliberately not resolutions: neither supplies a second
address.  Reading a criterion's own body into template fields by their labels
is not matching its text, and prose about checkboxes in a docstring is not a
scan.

**Falsifiability is asserted, not just recorded.**  The module the refutation
used to walk past the earlier reading — every evasion in one ordinary-looking
writeback module — is carried here as source and scanned through the same
three detectors, each of which must report every line the module marks as an
evasion.
"""

import ast
import re
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "src" / "kodezart"

#: The one place allowed to resolve a criterion to its sub-issue key.
RESOLUTION_SITE = "src/kodezart/services/criterion_sources.py"

#: An expression naming a criterion's own key, rather than a row's.
CRITERION_KEY = re.compile(r"criterion.*key|key.*criterion", re.IGNORECASE)
#: An expression naming the key a tracker row carries.
ISSUE_KEY = re.compile(r"issue_key", re.IGNORECASE)
#: An expression naming a criterion, whatever of it is then read.
CRITERION = re.compile(r"criterion|criteria", re.IGNORECASE)
#: An expression naming a criterion's prose.
CRITERION_TEXT = re.compile(
    r"(criterion|criteria|check).*(text|body)|(text|body).*(criterion|criteria|check)",
    re.IGNORECASE,
)
#: The fields a tracker row carries its prose in.
PROSE_FIELDS = frozenset({"title", "description", "body", "text"})
#: String methods that locate by matching rather than by addressing.
SEARCHERS = frozenset({"startswith", "endswith", "find", "rfind", "index", "count"})
#: Regex entry points, whose subject is an argument rather than a receiver.
MATCHERS = frozenset({"match", "fullmatch", "search", "findall", "finditer"})
#: Collection methods that turn a key into the row or the position it keys.
LOOKUPS = frozenset({"get", "index"})
#: A task-list box, written plainly or as the pattern that hunts for one.
CHECKBOX = re.compile(
    r"\[[ xX]\]|\\\[[^\]]*[ xX][^\]]*\\\]|checkbox|task[ -]list",
    re.IGNORECASE,
)
#: Lines a pattern must pick out before it counts as a checkbox scan.
CHECKBOX_LINES = ("- [ ] a task", "* [x] a task", "- [X] a task", "  - [ ] a task")
#: Lines it must leave alone, so that a merely permissive pattern is not one.
PLAIN_LINES = (
    "- a bullet",
    "* another bullet",
    "an ordinary sentence",
    "1. a numbered item",
    "  indented prose",
    "- [todo] a labelled bullet",
    "* [see notes] another labelled bullet",
    "a task for later",
    "a stray ] bracket",
)


def _segment(source: str, node: ast.AST) -> str:
    return ast.get_source_segment(source, node) or ""


def _operands(node: ast.Compare) -> list[ast.expr]:
    return [node.left, *node.comparators]


def _operators(node: ast.Compare) -> set[str]:
    return {type(operator).__name__ for operator in node.ops}


def _docstrings(tree: ast.Module) -> set[int]:
    """Identities of the string constants that are docstrings, not values."""
    held: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node,
            ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        ):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            held.add(id(first.value))
    return held


def _own_nodes(scope: ast.AST) -> tuple[list[ast.AST], list[ast.AST]]:
    """The nodes of one function or module body, and the functions inside it."""
    own: list[ast.AST] = []
    nested: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                nested.append(child)
                continue
            own.append(child)
            visit(child)

    visit(scope)
    return own, nested


def _bindings(nodes: Iterable[ast.AST]) -> list[tuple[str, ast.expr]]:
    """Every ``name = value`` the given nodes carry, in source order."""
    bound: list[tuple[str, ast.expr]] = []
    for node in nodes:
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign | ast.NamedExpr):
            targets, value = [node.target], node.value
        else:
            continue
        if value is None:
            continue
        bound.extend(
            (target.id, value) for target in targets if isinstance(target, ast.Name)
        )
    return bound


class _Reading:
    """What each name in one scope has been seen to hold.

    Assignment is where a resolution hides: a key renamed once is a key still,
    and a mapping or a list built out of row keys is an address book however
    it is later consulted.  So names are followed to a fixed point before any
    question is asked of an expression.

    Only a plain reference — a name or an attribute of one — renames a key or
    a criterion's prose.  A call, a conditional or a comprehension computes
    something new, and its spelling says nothing about what came out, so it
    binds nothing here beyond the collections built visibly out of row keys.
    """

    def __init__(self, source: str, bindings: Iterable[tuple[str, ast.expr]]) -> None:
        self._source = source
        self.criterion_keys: set[str] = set()
        self.criterion_texts: set[str] = set()
        self.row_keys: set[str] = set()
        self.key_collections: set[str] = set()
        self._follow(list(bindings))

    def _follow(self, bindings: list[tuple[str, ast.expr]]) -> None:
        for _ in range(len(bindings) + 1):
            before = self._state()
            for name, value in bindings:
                if self.is_criterion_key(value):
                    self.criterion_keys.add(name)
                if self.is_criterion_text(value):
                    self.criterion_texts.add(name)
                if self.is_row_key(value):
                    self.row_keys.add(name)
                if self.is_key_collection(value):
                    self.key_collections.add(name)
            if self._state() == before:
                return

    def _state(self) -> tuple[frozenset[str], ...]:
        return (
            frozenset(self.criterion_keys),
            frozenset(self.criterion_texts),
            frozenset(self.row_keys),
            frozenset(self.key_collections),
        )

    def _named(self, node: ast.expr, held: set[str]) -> bool:
        return isinstance(node, ast.Name) and node.id in held

    def _spelled(self, node: ast.expr, pattern: re.Pattern[str]) -> bool:
        return isinstance(node, ast.Name | ast.Attribute | ast.Subscript) and bool(
            pattern.search(self.text(node))
        )

    def text(self, node: ast.AST) -> str:
        return _segment(self._source, node)

    def is_criterion_key(self, node: ast.expr) -> bool:
        return self._spelled(node, CRITERION_KEY) or self._named(
            node, self.criterion_keys
        )

    def is_criterion_text(self, node: ast.expr) -> bool:
        return self._spelled(node, CRITERION_TEXT) or self._named(
            node, self.criterion_texts
        )

    def is_criterion(self, node: ast.expr) -> bool:
        return (
            self._spelled(node, CRITERION)
            or self.is_criterion_key(node)
            or self.is_criterion_text(node)
        )

    def is_row_key(self, node: ast.expr) -> bool:
        return self._spelled(node, ISSUE_KEY) or self._named(node, self.row_keys)

    def is_prose_field(self, node: ast.expr) -> bool:
        return isinstance(node, ast.Attribute) and node.attr in PROSE_FIELDS

    def is_key_collection(self, node: ast.expr) -> bool:
        """A mapping or sequence whose members are the family's row keys."""
        if self._named(node, self.key_collections):
            return True
        if isinstance(node, ast.DictComp):
            return self.is_row_key(node.key)
        if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp):
            return self.is_row_key(node.elt)
        if isinstance(node, ast.Dict):
            return any(key is not None and self.is_row_key(key) for key in node.keys)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            return node.func.attr in {"keys", "values"} and self.is_key_collection(
                node.func.value
            )
        return False


def _readings(source: str) -> list[tuple[_Reading, list[ast.AST]]]:
    """One reading per scope, each over the nodes that scope owns."""
    tree = ast.parse(source)
    scopes: list[tuple[_Reading, list[ast.AST]]] = []
    pending: list[tuple[ast.AST, tuple[tuple[str, ast.expr], ...]]] = [(tree, ())]
    while pending:
        scope, inherited = pending.pop()
        own, nested = _own_nodes(scope)
        bindings = inherited + tuple(_bindings(own))
        scopes.append((_Reading(source, bindings), own))
        pending.extend((child, bindings) for child in nested)
    return scopes


def criterion_resolution_sites(source: str) -> list[str]:
    """Every place *source* narrows a family to the criterion key asked for."""
    offenders: list[str] = []
    for reading, nodes in _readings(source):
        for node in nodes:
            offenders.extend(_narrowings(reading, node))
    return offenders


def _narrowings(reading: _Reading, node: ast.AST) -> list[str]:
    """The narrowings one expression performs, under one scope's reading."""
    if isinstance(node, ast.Compare) and _operators(node) & {"Eq", "In"}:
        operands = _operands(node)
        if any(reading.is_key_collection(term) for term in operands):
            return []
        rows = {
            index for index, term in enumerate(operands) if reading.is_row_key(term)
        }
        keys = {
            index
            for index, term in enumerate(operands)
            if reading.is_criterion_key(term)
        }
        if any(row != key for row in rows for key in keys):
            rendered = " ".join(reading.text(term) for term in operands)
            return [f"line {node.lineno}: {rendered}"]
    if (
        isinstance(node, ast.Subscript)
        and reading.is_key_collection(node.value)
        and reading.is_criterion_key(node.slice)
    ):
        return [f"line {node.lineno}: {reading.text(node)}"]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in LOOKUPS
        and reading.is_key_collection(node.func.value)
        and any(reading.is_criterion_key(argument) for argument in node.args)
    ):
        return [f"line {node.lineno}: {reading.text(node)}"]
    return []


def criterion_text_matches(source: str) -> list[str]:
    """Every place *source* matches a criterion's prose instead of its key."""
    offenders: list[str] = []
    for reading, nodes in _readings(source):
        for node in nodes:
            offenders.extend(_prose_matches(reading, node))
    return offenders


def _prose_matches(reading: _Reading, node: ast.AST) -> list[str]:
    """The prose matches one expression performs, under one scope's reading."""
    offenders: list[str] = []
    if isinstance(node, ast.Compare) and _operators(node) & {
        "Eq",
        "NotEq",
        "In",
        "NotIn",
    }:
        operands = _operands(node)
        if any(reading.is_criterion_text(term) for term in operands) or (
            any(reading.is_prose_field(term) for term in operands)
            and any(reading.is_criterion(term) for term in operands)
        ):
            rendered = " ".join(reading.text(term) for term in operands)
            offenders.append(f"line {node.lineno}: {rendered}")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        receiver = node.func.value
        if node.func.attr in SEARCHERS and (
            reading.is_criterion_text(receiver)
            or (
                reading.is_prose_field(receiver)
                and any(reading.is_criterion(argument) for argument in node.args)
            )
        ):
            offenders.append(
                f"line {node.lineno}: {reading.text(receiver)}.{node.func.attr}("
            )
        if node.func.attr in MATCHERS and any(
            reading.is_criterion_text(argument) for argument in node.args
        ):
            offenders.append(f"line {node.lineno}: regex over criterion text")
    return offenders


def _picks_out_checkboxes(pattern: str) -> bool:
    """Whether *pattern* is a regex that finds task-list lines and only those."""
    try:
        compiled = re.compile(pattern)
    except re.error:
        return False
    return all(compiled.search(line) for line in CHECKBOX_LINES) and not any(
        compiled.search(line) for line in PLAIN_LINES
    )


def checkbox_scans(source: str) -> list[str]:
    """Every task-list box *source* carries as a value rather than as prose."""
    tree = ast.parse(source)
    docstrings = _docstrings(tree)
    return [
        f"line {node.lineno}: {node.value!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and (CHECKBOX.search(node.value) or _picks_out_checkboxes(node.value))
    ]


#: A writeback module committing every evasion the refutation walked past.
ADVERSARIAL_MODULE = '''"""Write a criterion's outcome back, the forbidden ways."""

import re

CHECKBOX_LINE = re.compile(r"^\\s*[-*]\\s+\\[[ xX]\\]\\s+")  # evasion: checkbox


def resolve_by_index(rows, criterion_key):
    by_key = {row.issue_key: row for row in rows}
    return by_key[criterion_key]  # evasion: resolution


def resolve_by_asking(rows, criterion_key):
    addressed = {row.issue_key: row for row in rows}
    return addressed.get(criterion_key)  # evasion: resolution


def resolve_by_position(rows, criterion_key):
    keys = [row.issue_key for row in rows]
    return rows[keys.index(criterion_key)]  # evasion: resolution


def resolve_by_alias(rows, criterion_key):
    wanted = criterion_key
    matched = [row for row in rows if row.issue_key == wanted]  # evasion: resolution
    return matched[0]


def locate_by_prose(rows, criterion):
    sought = criterion.text
    for row in rows:
        if row.title.startswith(sought):  # evasion: text
            return row
        if sought in row.description:  # evasion: text
            return row
    return None


def rewrite_ticks(body):
    return [line for line in body.splitlines() if CHECKBOX_LINE.match(line)]
'''

#: The path the refutation gave that module; it never reaches the tree.
ADVERSARIAL_PATH = "src/kodezart/services/criterion_writeback.py"


def _modules() -> list[tuple[str, str]]:
    return [
        (
            path.relative_to(REPO_ROOT).as_posix(),
            path.read_text(encoding="utf-8"),
        )
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
    ]


def _scan(
    detector: Callable[[str], list[str]], modules: Iterable[tuple[str, str]]
) -> dict[str, list[str]]:
    return {module: found for module, source in modules if (found := detector(source))}


def _reported_lines(found: Mapping[str, list[str]]) -> set[int]:
    return {
        int(match.group(1))
        for entries in found.values()
        for entry in entries
        if (match := re.match(r"line (\d+):", entry))
    }


def _evasion_lines(source: str, kind: str) -> set[int]:
    return {
        number
        for number, line in enumerate(source.splitlines(), start=1)
        if f"# evasion: {kind}" in line
    }


def test_exactly_one_module_resolves_a_criterion_to_its_sub_issue_key() -> None:
    found = _scan(criterion_resolution_sites, _modules())
    assert sorted(found) == [RESOLUTION_SITE], (
        f"a criterion resolves to its sub-issue key at exactly one site; found {found}"
    )
    assert found[RESOLUTION_SITE] == found[RESOLUTION_SITE][:1], (
        f"the sanctioned module narrows once, not repeatedly; found {found}"
    )


def test_no_module_locates_a_write_target_by_matching_criterion_text() -> None:
    found = _scan(criterion_text_matches, _modules())
    assert found == {}, (
        f"no module may locate a target by matching criterion text; found {found}"
    )


def test_no_module_scans_for_checkbox_syntax() -> None:
    found = _scan(checkbox_scans, _modules())
    assert found == {}, (
        f"no module may locate a target by checkbox syntax; found {found}"
    )


def test_every_detector_fires_on_the_refuted_writeback_module() -> None:
    """The module that walked past the earlier reading is caught line by line."""
    modules = [(ADVERSARIAL_PATH, ADVERSARIAL_MODULE)]
    for detector, kind in (
        (criterion_resolution_sites, "resolution"),
        (criterion_text_matches, "text"),
        (checkbox_scans, "checkbox"),
    ):
        found = _scan(detector, modules)
        assert sorted(found) == [ADVERSARIAL_PATH], (
            f"the {kind} detector must fire on the refuted module; found {found}"
        )
        expected = _evasion_lines(ADVERSARIAL_MODULE, kind)
        assert expected, f"the refuted module must mark its {kind} evasions"
        assert expected <= _reported_lines(found), (
            f"the {kind} detector misses {expected - _reported_lines(found)}"
        )
