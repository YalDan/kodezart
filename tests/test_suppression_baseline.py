"""Every suppression the tree carries is named here, and nowhere else.

A `# type: ignore` or a `# noqa` is a hole in the gate, and a hole nothing
counts is a hole that grows.  The baseline below names each one that
exists by the file that carries it and the exact directive text, so a
suppression added anywhere under `src/` or `tests/` reds the suite at the
moment it lands rather than at the next review of a diff.

Comments are read from the token stream, so the same words inside a string
-- the prompts that TELL a reviewer to grep for these tokens, and the tests
that assert those prompts render -- are not suppressions and are not
counted.
"""

import re
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The two trees the gate covers: shipped code and the suite that exercises it.
SCANNED = ("src/kodezart", "tests")

SUPPRESSION = re.compile(r"#\s*(?:type:\s*ignore|(?:ruff:\s*)?noqa)")

#: Every suppression the tree is allowed to carry, by repository-relative
#: path and the directive text in file order.  Each one is a deliberate
#: call against a typed surface that the call is proving rejects it: a
#: constructor handed an unknown keyword, a port member replaced to stage a
#: theft, a payload assembled as a mapping the model forbids.  Production
#: code appears nowhere in this map, and a new entry is a decision, not a
#: convenience -- it belongs in a commit that says why.
ALLOWED: dict[str, tuple[str, ...]] = {
    "tests/adapters/test_judgment_scanner.py": (
        "# type: ignore[call-arg]",
        "# type: ignore[call-arg]",
    ),
    "tests/core/test_config_error_redaction.py": (
        "# type: ignore[arg-type]",
        "# type: ignore[arg-type]",
    ),
    "tests/core/test_notion_credential.py": ("# type: ignore[call-arg]",),
    "tests/core/test_tracker_credential.py": ("# type: ignore[call-arg]",),
    "tests/fakes.py": ("# type: ignore[arg-type]",),
    "tests/prompts/test_v5_fragments.py": ("# type: ignore[arg-type]",),
    "tests/services/test_claim_heartbeat.py": ("# type: ignore[arg-type]",),
    "tests/services/test_fire_dispatcher.py": (
        "# type: ignore[arg-type]",
        "# type: ignore[method-assign]",
        "# type: ignore[method-assign]",
    ),
    "tests/services/test_prompt_passes.py": (
        "# type: ignore[arg-type]",
        "# type: ignore[arg-type]",
        "# type: ignore[arg-type]",
    ),
    "tests/tracker/test_linear_mcp_tracker.py": (
        "# type: ignore[arg-type]",
        "# type: ignore[arg-type]",
    ),
}


def _modules():
    for tree in SCANNED:
        yield from sorted((REPO_ROOT / tree).rglob("*.py"))


def _comment_suppressions(module):
    """The suppression directives module carries, as comments, in file order."""
    with module.open("rb") as handle:
        tokens = list(tokenize.tokenize(handle.readline))
    return tuple(
        token.string.strip()
        for token in tokens
        if token.type is tokenize.COMMENT and SUPPRESSION.search(token.string)
    )


def _suppressions():
    found = {}
    for module in _modules():
        directives = _comment_suppressions(module)
        if directives:
            found[module.relative_to(REPO_ROOT).as_posix()] = directives
    return found


def test_the_tree_carries_exactly_the_suppressions_the_baseline_names() -> None:
    """A suppression nobody wrote down is a suppression nobody agreed to."""
    assert _suppressions() == ALLOWED


def test_no_shipped_module_suppresses_the_type_checker_or_the_linter() -> None:
    """Production code carries no hole at all; the baseline is tests-only."""
    assert [path for path in _suppressions() if not path.startswith("tests/")] == []


def test_a_suppression_inside_a_string_is_not_counted_as_one() -> None:
    """The prompts quote these tokens to teach a reviewer to hunt them."""
    quoted = REPO_ROOT / "tests" / "prompts" / "test_criteria_generation_prompts.py"

    assert SUPPRESSION.search(quoted.read_text()) is not None
    assert _comment_suppressions(quoted) == ()
