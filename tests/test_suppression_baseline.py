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

import json
from pathlib import Path

import pytest

from tests import negative_shape
from tests.negative_shape import REPO_ROOT, SKIP_FORMS, Source

#: Every suppression the tree is allowed to carry, by repository-relative
#: path and the directive text in file order.  Each one is a deliberate
#: call against a typed surface that the call is proving rejects it: a
#: constructor handed an unknown keyword, a port member replaced to stage a
#: theft, a payload assembled as a mapping the model forbids.  Production
#: code appears nowhere in this map, and a new entry is a decision, not a
#: convenience -- it belongs in a commit that says why.
ALLOWED: dict[str, tuple[str, ...]] = {
    "tests/adapters/test_judgment_scanner.py": ("# type: ignore[call-arg]",),
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

#: Every place the tree names a form that keeps a collected test from
#: running, by path and form in file order.  Three inventories of this
#: surface disagreed; the roster is derived from the tree instead, and a
#: sixth site is a row added here with the reason it earns its place.
ALLOWED_SKIPS: dict[str, tuple[str, ...]] = {
    # The gate's own mechanism: the one place a gated marker becomes a skip.
    "tests/conftest.py": ("pytest.mark.skip",),
    # Both files guard the presence of the set KOD-88 authors.  The set
    # shipped, so neither guard fires; they are rostered, not load-bearing.
    "tests/prompts/test_set_completeness.py": ("pytest.mark.skipif",),
    "tests/prompts/test_v5_style_rules.py": (
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "pytest.mark.skipif",
    ),
}

#: The configuration subtrees that decide what the linter lets through.
#: Read from the project file and compared exactly, because a rule relaxed
#: there is a hole no inline directive shows: the row for
#: `src/kodezart/config/git.py` below is one that landed unnoticed.
CONFIG_TABLES: tuple[str, ...] = ("tool.ruff.lint",)
CONFIG_BASELINE: dict[str, object] = {
    "tool.ruff.lint": {
        "select": (
            "E",
            "W",
            "F",
            "I",
            "B",
            "C4",
            "UP",
            "N",
            "ANN",
            "S",
            "A",
            "ARG",
            "RUF",
        ),
        "ignore": ("S101",),
        "per-file-ignores": {
            "tests/**/*.py": (
                "S101",
                "ARG001",
                "ARG002",
                "ANN",
                "S105",
                "S108",
                "S603",
                "S607",
            ),
            # Four rows on shipped modules: temporary-path defaults and
            # token-shaped constants the rules read as findings.
            "src/kodezart/config/app.py": ("S108",),
            "src/kodezart/config/git.py": ("S108",),
            "src/kodezart/types/domain/prompts.py": ("S105",),
            "src/kodezart/types/domain/session.py": ("S105",),
            "tests/types/**/*.py": ("A005",),
        },
        "isort": {"known-first-party": ("kodezart",)},
    },
}

#: One hand-written source per form, each binding pytest a different way,
#: so the import alias, the from-import and the assignment alias each have
#: a control.  The tree aliases pytest nowhere today, so these are the only
#: proof those arms work.
FORM_CONTROLS: tuple[tuple[str, str], ...] = (
    (
        "pytest.mark.skip",
        "import pytest\n@pytest.mark.skip(reason='x')\ndef test_a(): ...\n",
    ),
    (
        "pytest.mark.skipif",
        "import pytest\npytest.param(1, marks=pytest.mark.skipif(True, reason='x'))\n",
    ),
    ("pytest.skip", "import pytest\npytest.skip('x')\n"),
    ("pytest.importorskip", "import pytest\nyaml = pytest.importorskip('yaml')\n"),
    ("pytest.mark.xfail", "from pytest import mark\n@mark.xfail\ndef test_a(): ...\n"),
    ("pytest.xfail", "import pytest as pt\npt.xfail('x')\n"),
)

#: The recorded declaration roster and per-file assertion floors.  A name
#: deleted from this file, or a count lowered in it, is the diff to read
#: twice: it says a test or an assertion went away.  New files and new
#: names are added freely.
BASELINE: Path = Path(__file__).with_name("negative_shape_baseline.json")
RECORDED: dict[str, dict[str, object]] = json.loads(
    BASELINE.read_text(encoding="utf-8")
)


def test_the_tree_carries_exactly_the_suppressions_the_baseline_names() -> None:
    """A suppression nobody wrote down is a suppression nobody agreed to."""
    assert negative_shape.census(negative_shape.comment_directives) == ALLOWED


def test_no_shipped_module_suppresses_the_type_checker_or_the_linter() -> None:
    """Production code carries no hole at all; the baseline is tests-only."""
    carried = negative_shape.census(negative_shape.comment_directives)

    assert [path for path in carried if not path.startswith("tests/")] == []


def test_a_suppression_inside_a_string_is_not_counted_as_one() -> None:
    """The prompts quote these tokens to teach a lane to hunt them."""
    quoted = negative_shape.source("tests/prompts/test_criteria_generation_prompts.py")

    assert negative_shape.SUPPRESSION.search(quoted.text.decode("utf-8")) is not None
    assert negative_shape.comment_directives(quoted) == ()


def test_the_tree_carries_exactly_the_skip_forms_the_baseline_names() -> None:
    """A test that does not run is a test that proves nothing."""
    carried = negative_shape.census(
        lambda module: negative_shape.sites(module, SKIP_FORMS)
    )

    assert carried == ALLOWED_SKIPS


@pytest.mark.parametrize(
    ("form", "control"),
    FORM_CONTROLS,
    ids=[f"{index}-{form}" for index, (form, _) in enumerate(FORM_CONTROLS)],
)
def test_the_resolver_sees_each_form_through_each_alias_shape(
    form: str, control: str
) -> None:
    """The name a module binds pytest to does not change what it names."""
    assert negative_shape.sites(Source.of("control.py", control), SKIP_FORMS) == (form,)


def test_every_form_is_controlled() -> None:
    """A form added to the roster without a control is a form nothing proves."""
    assert {form for form, _ in FORM_CONTROLS} == SKIP_FORMS


def test_a_recorded_test_declaration_never_vanishes() -> None:
    """A declaration recorded here may not go; a new one costs nothing."""
    current = negative_shape.census(negative_shape.test_declarations)

    assert negative_shape.vanished(RECORDED["declarations"], current) == []


def test_a_module_never_drops_below_its_recorded_assert_count() -> None:
    """A file may assert more than it did; it may not assert less."""
    current = negative_shape.census(negative_shape.assert_count)

    assert negative_shape.shortfalls(RECORDED["asserts"], current) == []


def test_the_declaration_reader_sees_functions_methods_and_nothing_else() -> None:
    """A method is qualified by its class; a helper is not a declaration."""
    control = Source.of(
        "control.py",
        "def test_a(): ...\n"
        "class TestB:\n"
        "    def test_c(self): ...\n"
        "def helper(): ...\n"
        "async def test_d(): ...\n",
    )

    assert negative_shape.test_declarations(control) == (
        "test_a",
        "TestB.test_c",
        "test_d",
    )


def test_an_assert_over_three_lines_is_one() -> None:
    """The unit is the statement, not the line it is spelled across."""
    control = Source.of("control.py", "assert (\n    a\n)\nassert b\n")

    assert negative_shape.assert_count(control) == 2


def test_a_replaced_assert_leaves_the_count_unchanged() -> None:
    """A stronger assertion written over a weaker one is not a removal.

    The decision of record (KOD-865) reads a same-line replacement as no
    removal.  Counting statements makes that structural rather than judged:
    both shapes below are one assertion.
    """
    narrow = Source.of("control.py", 'assert item["pattern"] == PATTERN\n')
    wider = Source.of(
        "control.py", 'assert item == {"pattern": PATTERN, "type": "string"}\n'
    )

    assert negative_shape.assert_count(narrow) == 1
    assert negative_shape.assert_count(wider) == 1


def test_the_comparison_reports_a_vanished_name_a_missing_row_and_nothing_else() -> (
    None
):
    """A lost name is named, a file with no row is named, a new name is free."""
    recorded = {"a.py": ["test_x", "test_y"]}

    assert negative_shape.vanished(
        recorded, {"a.py": ("test_x", "test_z"), "b.py": ("test_q",)}
    ) == ["a.py::test_y", "b.py has no row"]
    assert negative_shape.vanished(recorded, {"a.py": ("test_x", "test_y")}) == []
    assert negative_shape.vanished(
        {"a.py": ["test_x", "test_x", "test_y"]}, {"a.py": ("test_x", "test_y")}
    ) == ["a.py::test_x"]


def test_the_comparison_reports_a_shortfall_and_not_a_surplus() -> None:
    """Falling below the floor is a finding; rising above it is not."""
    assert negative_shape.shortfalls({"a.py": 3}, {"a.py": 2}) == ["a.py: 2 below 3"]
    assert negative_shape.shortfalls({"a.py": 3}, {"a.py": 4}) == []
    assert negative_shape.shortfalls({"a.py": 3}, {"a.py": 3, "b.py": 1}) == [
        "b.py has no row"
    ]


def test_the_gate_configuration_is_the_pinned_literal() -> None:
    """A rule relaxed in the project file is a hole no inline directive shows."""
    read = negative_shape.config_tables(REPO_ROOT / "pyproject.toml", CONFIG_TABLES)

    assert read == CONFIG_BASELINE


def test_the_configuration_scan_sees_a_new_per_file_row(tmp_path: Path) -> None:
    """The shape that landed unnoticed: one more row under per-file-ignores."""
    original = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    header = "[tool.ruff.lint.per-file-ignores]\n"
    mutated = original.replace(header, f'{header}"tests/probes/*.py" = ["S101"]\n')
    copy = tmp_path / "pyproject.toml"
    copy.write_text(mutated, encoding="utf-8")

    assert mutated != original

    read = negative_shape.config_tables(copy, CONFIG_TABLES)
    rows = read["tool.ruff.lint"]["per-file-ignores"]
    pinned = CONFIG_BASELINE["tool.ruff.lint"]["per-file-ignores"]

    assert read != CONFIG_BASELINE
    assert rows["tests/probes/*.py"] == ("S101",)
    assert {key: row for key, row in rows.items() if key in pinned} == pinned
