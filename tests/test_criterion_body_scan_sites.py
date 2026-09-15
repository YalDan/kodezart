"""No shipped body reads criterion-shaped content out of issue prose.

Criterion membership and satisfaction are sub-issue facts, keyed on each
criterion's own key.  The failure this guards is the quiet return of the
retired body parser as a fallback: one reader that, when the sub-issue
listing says nothing, goes looking through an issue's description for a
checkbox row, an ``AC-n`` identity or a ``Check:`` row label and mints
membership out of what it finds.  Such a reader answers a reorder or a
reflow differently from a keyed one, and nothing red says so.

The criterion template grammar itself is legitimate and has exactly one
owner: the field reader, which reads a criterion's own body for the
fields the template states.  That owner is located below through the
functions themselves rather than by a written-down path, and every other
body in the tree must contain no such match at all.

The walk is textual and executes nothing, which is what lets it speak for
the whole tree rather than for the paths a fixture happens to reach.  The
controls inject each spelling a body scan can hide behind, and each way
of naming criterion-shaped text without matching anything against it.
"""

import ast
import re
import sys
from pathlib import Path

import pytest

from kodezart.domain.fire_spec import criterion_field_bodies, replace_criterion_fields

SOURCE = Path(__file__).resolve().parents[1] / "src" / "kodezart"
#: Where the criterion template grammar is written, and so the one module
#: a match against criterion-shaped text belongs in.
RULE_MODULE = (
    Path(sys.modules[criterion_field_bodies.__module__].__file__ or "")
    .resolve()
    .relative_to(SOURCE)
    .as_posix()
)
RULE_SCOPES = sorted(
    {criterion_field_bodies.__name__, replace_criterion_fields.__name__}
)

#: The spellings of criterion-shaped content a scan would look for: a
#: markdown checkbox, an authored ``AC-n`` identity, and a template row
#: label — including the alternation a row-label pattern is written as.
CRITERION_SHAPES = (
    re.compile(r"\[\s*[xX]?\s*\]"),
    re.compile(r"AC-"),
    re.compile(r"Check(\\?\*)*\s*[:|]"),
)

#: Module-level entry points that match a pattern against text.
RE_MATCHERS = frozenset(
    {"match", "search", "fullmatch", "findall", "finditer", "split", "sub", "subn"}
)
#: The same, called on a compiled pattern.
PATTERN_MATCHERS = RE_MATCHERS
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


def _is_criterion_shaped(node):
    """Whether this expression spells criterion-shaped content literally."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and any(shape.search(node.value) for shape in CRITERION_SHAPES)
    )


def _compiles_a_criterion_shape(node):
    """Whether this expression is ``re.compile`` over such a spelling."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compile"
        and bool(node.args)
        and _is_criterion_shaped(node.args[0])
    )


def _pattern_names(tree):
    """Every identity bound to a compiled criterion-shaped pattern."""
    names = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        value = node.value
        if value is None or not _compiles_a_criterion_shape(value):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def _matches_criterion_shaped_text(node, names):
    """Whether this expression puts criterion-shaped content against text."""
    if isinstance(node, ast.Compare):
        return any(
            isinstance(operator, (ast.In, ast.NotIn)) for operator in node.ops
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
    if func.attr in PATTERN_MATCHERS:
        owner = func.value
        if isinstance(owner, ast.Name) and owner.id in names:
            return True
        if isinstance(owner, ast.Attribute) and owner.attr in names:
            return True
        if _compiles_a_criterion_shape(owner):
            return True
    return False


def _sites(tree, names):
    """Label each scope that matches criterion-shaped content, once per scope."""
    found = set()

    def walk(node, label):
        for child in ast.iter_child_nodes(node):
            here = label
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                here = child.name if label is None else f"{label}.{child.name}"
            elif label is None:
                here = f"line {child.lineno}"
            if _matches_criterion_shaped_text(child, names):
                found.add(here)
            walk(child, here)

    walk(tree, None)
    return found


def _snippet_sites(source):
    """The sites one standalone body states, patterns resolved within it."""
    tree = ast.parse(source)
    return _sites(tree, _pattern_names(tree))


def _scan_sites(root):
    trees = {
        path.relative_to(root).as_posix(): ast.parse(path.read_text())
        for path in sorted(root.rglob("*.py"))
    }
    names = set().union(*(_pattern_names(tree) for tree in trees.values()))
    found = {}
    for module, tree in trees.items():
        sites = _sites(tree, names)
        if sites:
            found[module] = sorted(sites)
    return found


def test_only_the_criterion_field_reader_matches_criterion_shaped_text():
    assert _scan_sites(SOURCE) == {RULE_MODULE: RULE_SCOPES}


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


def test_a_second_module_scanning_an_issue_body_fails_the_assertion(
    tmp_path, monkeypatch
):
    rule = tmp_path / RULE_MODULE
    rule.parent.mkdir(parents=True, exist_ok=True)
    rule.write_text((SOURCE / RULE_MODULE).read_text())
    monkeypatch.setattr(sys.modules[__name__], "SOURCE", tmp_path)
    test_only_the_criterion_field_reader_matches_criterion_shaped_text()
    (tmp_path / "reader.py").write_text(
        "def criteria(issue):\n"
        "    return [line for line in issue.body.splitlines()"
        " if line.startswith('- [ ]')]\n"
    )
    with pytest.raises(AssertionError):
        test_only_the_criterion_field_reader_matches_criterion_shaped_text()
