"""One body in the shipped sources weighs a graded sha against a head sha.

Two readers weighing the same two shas is how a lapse comes to mean one
thing in the compliance mark and another in the lane check: one of them
eventually grows an ancestry test, a prefix match or a null case, and
nothing red says so.  The rule therefore lives in exactly one function,
and this guard is what keeps a second one from appearing.

The walk is textual and executes nothing, which is what lets it speak for
the whole tree rather than for the paths a fixture happens to reach.  The
controls below inject each spelling the comparison can hide behind, and
each way of naming both shas without weighing one against the other.
"""

import ast
import sys
from pathlib import Path

import pytest

from kodezart.types.domain.lapse import graded_state

SOURCE = Path(__file__).resolve().parents[1] / "src" / "kodezart"
RULE = graded_state.__name__
#: Where the rule is written, and so the one module the comparison belongs in.
RULE_MODULE = (
    Path(sys.modules[graded_state.__module__].__file__ or "")
    .resolve()
    .relative_to(SOURCE)
    .as_posix()
)

#: An identity naming the revision a grading was taken at.
GRADED = "graded_sha"
#: The identities naming the revision that grading is being read at.  The
#: bare spellings are included because dropping the suffix is exactly how a
#: second comparison would slip past a guard keyed on ``head_sha`` alone.
HEADS = frozenset({"head", "head_commit"})


def _names(node):
    """Every identity one side of a comparison reads."""
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


def _reads_a_graded_sha(names):
    return any(GRADED in name for name in names)


def _reads_a_head_sha(names):
    return any("head_sha" in name or name in HEADS for name in names)


def _weighs_graded_against_head(node):
    """Whether this expression puts a graded sha opposite a head sha."""
    if not isinstance(node, ast.Compare):
        return False
    sides = [_names(operand) for operand in [node.left, *node.comparators]]
    return any(
        _reads_a_graded_sha(one) and _reads_a_head_sha(other)
        for index, one in enumerate(sides)
        for position, other in enumerate(sides)
        if index != position
    )


def _sites(tree):
    """Label each scope that weighs the two revisions, once per scope."""
    found = set()

    def walk(node, label):
        for child in ast.iter_child_nodes(node):
            here = label
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                here = child.name if label is None else f"{label}.{child.name}"
            elif label is None:
                here = f"line {child.lineno}"
            if _weighs_graded_against_head(child):
                found.add(here)
            walk(child, here)

    walk(tree, None)
    return found


def _comparison_sites(root):
    found = {}
    for path in sorted(root.rglob("*.py")):
        sites = _sites(ast.parse(path.read_text()))
        if sites:
            found[path.relative_to(root).as_posix()] = sorted(sites)
    return found


def test_the_sources_weigh_a_graded_sha_against_a_head_sha_in_one_body_only():
    sites = _comparison_sites(SOURCE)
    assert sum(len(group) for group in sites.values()) == 1, sites
    assert sites == {RULE_MODULE: [RULE]}, sites


@pytest.mark.parametrize(
    "body",
    [
        "def lapsed(evidence, head_sha):\n    return evidence.graded_sha != head_sha\n",
        "def lapsed(evidence, head_sha):\n    return head_sha == evidence.graded_sha\n",
        "def lapsed(graded_sha, head):\n    return graded_sha != head\n",
        "def lapsed(graded_sha, head_commit):\n    return graded_sha is head_commit\n",
        "def lapsed(record):\n"
        "    return record.evidence.graded_sha != record.claim.head_sha\n",
        "def lapsed(a, b, c):\n    return a.graded_sha == b.other_sha == c.head_sha\n",
        "def lapsed(record):\n"
        "    return record.recorded_graded_sha != record.remote_head_sha\n",
        "class Record:\n"
        "    def lapsed(self):\n"
        "        return self.graded_sha != self.head_sha\n",
        "def outer():\n"
        "    def inner(evidence, head_sha):\n"
        "        return evidence.graded_sha != head_sha\n"
        "    return inner\n",
    ],
)
def test_every_spelling_that_weighs_the_two_revisions_is_reported(body):
    assert _sites(ast.parse(body))


@pytest.mark.parametrize(
    "body",
    [
        "def audited(pair, evidence, head_sha):\n"
        "    return pair.source_sha in {evidence.graded_sha, head_sha}\n",
        "def exact(checks, evidence):\n"
        "    return checks.commit_sha != evidence.graded_sha\n",
        "def stamp(evidence, head_sha):\n"
        "    return Report(graded_sha=evidence.graded_sha, head_sha=head_sha)\n",
        "def resolved(evidence, head_sha, resolve):\n"
        "    return [sha for sha in (evidence.graded_sha, head_sha) if resolve(sha)]\n",
        "def ancestry(git, evidence, head_sha):\n"
        "    return git.is_ancestor(evidence.graded_sha, head_sha)\n",
        "def annotated(graded_sha: str, head_sha: str) -> str:\n"
        "    return graded_sha\n",
        "def other(evidence, head_sha):\n"
        "    return evidence.graded_sha != evidence.recorded_sha\n",
    ],
)
def test_naming_both_revisions_without_weighing_them_is_not_a_site(body):
    assert _sites(ast.parse(body)) == set()


def test_one_body_weighing_them_twice_is_one_site_and_two_bodies_are_two():
    source = (
        "def lapsed(evidence, head_sha, other_head_sha):\n"
        "    if evidence.graded_sha == head_sha:\n"
        "        return False\n"
        "    return evidence.graded_sha != other_head_sha\n"
        "def stale(evidence, head_sha):\n"
        "    return evidence.graded_sha != head_sha\n"
    )
    assert _sites(ast.parse(source)) == {"lapsed", "stale"}


def test_a_second_module_performing_the_comparison_fails_the_assertion(
    tmp_path, monkeypatch
):
    rule = tmp_path / RULE_MODULE
    rule.parent.mkdir(parents=True, exist_ok=True)
    rule.write_text((SOURCE / RULE_MODULE).read_text())
    monkeypatch.setattr(sys.modules[__name__], "SOURCE", tmp_path)
    test_the_sources_weigh_a_graded_sha_against_a_head_sha_in_one_body_only()
    (tmp_path / "reader.py").write_text(
        "def lapsed(evidence, head_sha):\n    return evidence.graded_sha != head_sha\n"
    )
    with pytest.raises(AssertionError):
        test_the_sources_weigh_a_graded_sha_against_a_head_sha_in_one_body_only()
