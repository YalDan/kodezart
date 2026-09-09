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

**These are lint approximations, and the approximations are stated rather
than hidden.**

*Resolution* is read as a comparison — ``==`` or ``in`` — that equates one
expression naming a row's ``issue_key`` with a *different* expression naming
a criterion key.  That is the shape of narrowing a family down to the
criterion asked for.  It is deliberately not the shape of a guard, which
rejects on ``!=`` rather than selecting on ``==``, nor of the duplicate
checks that compare a row's key against an accumulator built from the same
family — neither of those supplies a second address.  A resolver written
against tersely named locals (``r.key == k``) is missed.

*Matching criterion text* is read as a comparison or a search — ``.find``,
``.startswith``, a regex — over an expression whose source names both a
criterion and its text or body.  Reading a criterion's own body into template
fields by their labels is not matching its text and is not an offender; the
gate does not fire on it, because the label is not the criterion.

*Checkbox syntax* is read as a string constant, excluding docstrings, that
carries a task-list box — ``[ ]``, ``[x]``, their regex-escaped forms, or the
words for the construct.  Prose about checkboxes in a docstring is not a
scan; a pattern compiled to find one is.

**No meta-test.**  Nothing here asserts that the checks can fire; a test of
the test asserts its own fixture.  Falsifiability is demonstrated by probing
the detectors against injected offenders at authoring time — a second
resolver by comprehension and by loop, a text match by ``in``, by
``.startswith`` and by regex, a literal tick and a checkbox pattern — and
recorded on the tracker, never committed.
"""

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "src" / "kodezart"

#: The one place allowed to resolve a criterion to its sub-issue key.
RESOLUTION_SITE = "src/kodezart/services/criterion_sources.py"

#: An expression naming a criterion's own key, rather than a row's.
CRITERION_KEY = re.compile(r"criterion.*key|key.*criterion", re.IGNORECASE)
#: An expression naming the key a tracker row carries.
ISSUE_KEY = re.compile(r"issue_key", re.IGNORECASE)
#: An expression naming a criterion's prose.
CRITERION_TEXT = re.compile(
    r"(criterion|criteria|check).*(text|body)|(text|body).*(criterion|criteria|check)",
    re.IGNORECASE,
)
#: String methods that locate by matching rather than by addressing.
SEARCHERS = frozenset({"startswith", "endswith", "find", "rfind", "index", "count"})
#: Regex entry points, whose subject is an argument rather than a receiver.
MATCHERS = frozenset({"match", "fullmatch", "search", "findall", "finditer"})
#: A task-list box, written plainly or as the pattern that hunts for one.
CHECKBOX = re.compile(
    r"\[[ xX]\]|\\\[[^\]]*[ xX][^\]]*\\\]|checkbox|task[ -]list",
    re.IGNORECASE,
)


def _segment(source: str, node: ast.AST) -> str:
    return ast.get_source_segment(source, node) or ""


def _operands(source: str, node: ast.Compare) -> list[str]:
    return [_segment(source, node.left)] + [
        _segment(source, comparator) for comparator in node.comparators
    ]


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


def criterion_resolution_sites(source: str) -> list[str]:
    """Every place *source* narrows a family to the criterion key asked for."""
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not _operators(node) & {"Eq", "In"}:
            continue
        operands = _operands(source, node)
        rows = {index for index, text in enumerate(operands) if ISSUE_KEY.search(text)}
        keys = {
            index for index, text in enumerate(operands) if CRITERION_KEY.search(text)
        }
        if any(row != key for row in rows for key in keys):
            offenders.append(f"line {node.lineno}: {' '.join(operands)}")
    return offenders


def criterion_text_matches(source: str) -> list[str]:
    """Every place *source* matches a criterion's prose instead of its key."""
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and _operators(node) & {
            "Eq",
            "NotEq",
            "In",
            "NotIn",
        }:
            operands = _operands(source, node)
            if any(CRITERION_TEXT.search(text) for text in operands):
                offenders.append(f"line {node.lineno}: {' '.join(operands)}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            receiver = _segment(source, node.func.value)
            if node.func.attr in SEARCHERS and CRITERION_TEXT.search(receiver):
                offenders.append(f"line {node.lineno}: {receiver}.{node.func.attr}(")
            if node.func.attr in MATCHERS and any(
                CRITERION_TEXT.search(_segment(source, argument))
                for argument in node.args
            ):
                offenders.append(f"line {node.lineno}: regex over criterion text")
    return offenders


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
        and CHECKBOX.search(node.value)
    ]


def _modules() -> list[tuple[str, str]]:
    return [
        (
            path.relative_to(REPO_ROOT).as_posix(),
            path.read_text(encoding="utf-8"),
        )
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
    ]


def test_exactly_one_module_resolves_a_criterion_to_its_sub_issue_key() -> None:
    found = {
        module: sites
        for module, source in _modules()
        if (sites := criterion_resolution_sites(source))
    }
    assert sorted(found) == [RESOLUTION_SITE], (
        f"a criterion resolves to its sub-issue key at exactly one site; found {found}"
    )


def test_no_module_locates_a_write_target_by_matching_criterion_text() -> None:
    found = {
        module: matches
        for module, source in _modules()
        if (matches := criterion_text_matches(source))
    }
    assert found == {}, (
        f"no module may locate a target by matching criterion text; found {found}"
    )


def test_no_module_scans_for_checkbox_syntax() -> None:
    found = {
        module: scans
        for module, source in _modules()
        if (scans := checkbox_scans(source))
    }
    assert found == {}, (
        f"no module may locate a target by checkbox syntax; found {found}"
    )
