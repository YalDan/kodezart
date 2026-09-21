"""The negative shape of the tree, named here and nowhere else.

A hole nothing counts is a hole that grows.  The measure is an absolute
in-tree baseline: every suppression directive, every form that keeps a
collected test from running, every mark the collection gate deselects,
every test declaration, every file's assertion count and the gate's own
configuration are read from the tree as it stands and compared with what is
written down below.  Nothing is diffed against a base ref -- none is
recorded, and a baseline needs none.  It is the stronger measure either
way: a changed-lines reading catches a hole only where it was added, while
here every line is a changed line, so a hole reds the suite wherever it
lands and whoever landed it.

Directive comments are read from the token stream, so the same words inside
a string -- the prompts that name these tokens, and the tests that assert
those prompts render -- are not directives and are not counted.  Skip forms
and marks are read from the syntax, so a docstring that quotes one is not
one either.

An assertion is counted, never compared.  The unit is the statement, so a
weaker assertion replaced on the same line by a stronger one leaves the
count alone and is invisible here by construction, which is the decision of
record (KOD-865).  The tree's form-comparing instrument, the detector in
`services/assertion_drift.py`, reads the shape of a designated test's
assertions between two commits; that is a different question, asked
elsewhere, of a surface somebody nominated.

One class of the shipped proxy is out of this census: a model configuration
whose `extra` setting is loosened from forbidding unknown fields to
allowing them.  The tree carries none today and nothing here would see one.

A new row in any table below, and a deleted name or a lowered count in
`negative_shape_baseline.json`, is a decision.  It belongs in the commit
that needs it, with its reason written there.
"""

import json
from pathlib import Path

import pytest

from tests import negative_shape
from tests.conftest import GATED_MARKERS
from tests.negative_shape import REPO_ROOT, SKIP_FORMS, Source

#: Every suppression the tree is allowed to carry, by repository-relative
#: path and the directive text in file order.  Each one is a deliberate
#: call against a typed surface that the call is proving rejects it: a
#: constructor handed an unknown keyword, a port member replaced to stage a
#: theft, a payload assembled as a mapping the model forbids.  Production
#: code appears nowhere in this map, and a new entry is a decision, not a
#: convenience -- it belongs in a commit that says why.  The relaxations
#: shipped code does carry are configuration-level, and they are rows of
#: CONFIG_BASELINE below.
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

#: Every mark the collection gate deselects, by path and mark in file
#: order.  A test newly carrying one stops running in the gate, which is
#: the shape a skipped test takes here, so a fifteenth site is a row added
#: below with the reason it earns its place.
ALLOWED_GATED_MARKS: dict[str, tuple[str, ...]] = {
    # The one class that needs a database rather than a credential.
    "tests/integration/test_checkpointer_postgres.py": ("pytest.mark.postgres",),
    # Each of these drives a real external surface, and the run that
    # exercises them is recorded on the board rather than in the gate.
    "tests/integration/test_live_criteria_probes.py": ("pytest.mark.live",),
    "tests/integration/test_session_resume.py": ("pytest.mark.live",),
    "tests/probes/test_ab_smoke.py": ("pytest.mark.live",),
    "tests/probes/test_harness_capabilities.py": (
        "pytest.mark.live",
        "pytest.mark.live",
        "pytest.mark.live",
        "pytest.mark.live",
        "pytest.mark.live",
        "pytest.mark.live",
    ),
    "tests/probes/test_live_ownership.py": ("pytest.mark.live",),
    "tests/probes/test_strict_output_enforcement.py": ("pytest.mark.live",),
    "tests/probes/test_v5_orchestration_live.py": ("pytest.mark.live",),
    "tests/spec/test_model_agreement.py": ("pytest.mark.live",),
}

#: The configuration subtrees that decide what the type checker, the linter
#: and the warning filter let through.  Read from the project file and
#: compared exactly, because a rule relaxed there is a hole no inline
#: directive shows: the row for `src/kodezart/config/git.py` below is one
#: that landed unnoticed.
CONFIG_TABLES: tuple[str, ...] = (
    "tool.mypy",
    "tool.pydantic-mypy",
    "tool.pytest.ini_options.filterwarnings",
    "tool.ruff.lint",
)
CONFIG_BASELINE: dict[str, object] = {
    "tool.mypy": {
        "python_version": "3.12",
        "plugins": ("pydantic.mypy",),
        "mypy_path": "src",
        "strict": True,
        "disallow_any_explicit": True,
        # One module-scoped relaxation on shipped code: the settings model
        # whose loader signature the checker cannot express.
        "overrides": (
            {
                "module": ("kodezart.config.app",),
                "disallow_any_explicit": False,
            },
        ),
    },
    # The model plugin's own guard: a constructor handed an unknown field
    # is an error, which is the typed-boundary claim the suite rests on.
    "tool.pydantic-mypy": {
        "init_forbid_extra": True,
        "init_typed": True,
        "warn_required_dynamic_aliases": True,
    },
    # Every warning fails the suite.  A bare category here would re-open
    # the hole that setting closes.
    "tool.pytest.ini_options.filterwarnings": ("error",),
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
#: so the import alias, the from-import, the assignment alias, a name bound
#: twice and the annotated assignment each have a control.  The tree aliases
#: pytest nowhere today, so these are the only proof those arms work.
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
    (
        "pytest.mark.live",
        "import pytest\npytestmark = [pytest.mark.live, pytest.mark.asyncio()]\n",
    ),
    ("pytest.mark.live", "import pytest\npytestmark = pytest.mark.live\n"),
    (
        "pytest.mark.postgres",
        "from pytest import mark\nm = mark\n@m.postgres\ndef test_a(): ...\n",
    ),
    (
        "pytest.mark.postgres",
        "import pytest\nfrom pytest import mark\n"
        "m = pytest\nm = mark\n@m.postgres\ndef test_a(): ...\n",
    ),
    (
        "pytest.mark.skip",
        "import pytest\nfrom typing import Final\n"
        "m: Final = pytest.mark\n@m.skip\ndef test_a(): ...\n",
    ),
)

#: One directive of each form the pattern claims to read, held as data and
#: fed through a source built from text, so this module carries none of
#: them as a comment and adds nothing to its own census.
DIRECTIVE_CONTROLS: tuple[str, ...] = (
    "# type: ignore[arg-type]",
    "# noqa: E501",
    "# ruff: noqa",
    "# mypy: ignore-errors",
    "# mypy: disable-error-code=attr-defined",
)

#: The files the three tools discover instead of the project file.  A
#: `ruff.toml` beside it would make the pinned table above meaningless.
FOREIGN_TOOL_FILES: tuple[str, ...] = (
    "ruff.toml",
    ".ruff.toml",
    "mypy.ini",
    ".mypy.ini",
    "setup.cfg",
    "pytest.ini",
    "tox.ini",
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
    """Production code carries no inline hole.

    The configuration-level ones it does carry are pinned rows of
    CONFIG_BASELINE, each with its reason beside it.
    """
    carried = negative_shape.census(negative_shape.comment_directives)

    assert [path for path in carried if not path.startswith("tests/")] == []


def test_a_suppression_inside_a_string_is_not_counted_as_one() -> None:
    """The prompts quote these tokens to teach a lane to hunt them."""
    quoted = negative_shape.source("tests/prompts/test_criteria_generation_prompts.py")

    assert negative_shape.SUPPRESSION.search(quoted.text.decode("utf-8")) is not None
    assert negative_shape.comment_directives(quoted) == ()


@pytest.mark.parametrize("directive", DIRECTIVE_CONTROLS)
def test_each_directive_form_is_read_from_a_comment(directive: str) -> None:
    """A form the pattern stops reading is a class of hole nothing counts."""
    control = Source.of("control.py", f"x = 1  {directive}\n")

    assert negative_shape.comment_directives(control) == (directive,)


def test_the_tree_carries_exactly_the_skip_forms_the_baseline_names() -> None:
    """A test that does not run is a test that proves nothing."""
    carried = negative_shape.census(
        lambda module: negative_shape.sites(module, SKIP_FORMS)
    )

    assert carried == ALLOWED_SKIPS


def test_the_tree_carries_exactly_the_gated_marks_the_baseline_names() -> None:
    """A mark the gate deselects is a test that does not run in the gate."""
    carried = negative_shape.census(
        lambda module: negative_shape.sites(module, negative_shape.gated_mark_forms())
    )

    assert carried == ALLOWED_GATED_MARKS


def test_the_gate_deselects_exactly_the_markers_the_project_declares() -> None:
    """A marker declared and not gated, or gated and not declared, is a silence."""
    declared = negative_shape.declared_markers(REPO_ROOT / "pyproject.toml")

    assert frozenset(GATED_MARKERS) == declared


def test_a_mark_inside_a_docstring_is_not_a_site() -> None:
    """The words are in that file's prose as well as in its decorators."""
    module = negative_shape.source("tests/probes/test_harness_capabilities.py")

    assert b"pytest.mark.live" in module.text
    assert (
        negative_shape.sites(module, negative_shape.gated_mark_forms())
        == (ALLOWED_GATED_MARKS["tests/probes/test_harness_capabilities.py"])
    )


@pytest.mark.parametrize(
    ("form", "control"),
    FORM_CONTROLS,
    ids=[f"{index}-{form}" for index, (form, _) in enumerate(FORM_CONTROLS)],
)
def test_the_resolver_sees_each_form_through_each_alias_shape(
    form: str, control: str
) -> None:
    """The name a module binds pytest to does not change what it names."""
    forms = SKIP_FORMS | negative_shape.gated_mark_forms()

    assert negative_shape.sites(Source.of("control.py", control), forms) == (form,)


def test_every_form_is_controlled() -> None:
    """A form added to the roster without a control is a form nothing proves."""
    assert {form for form, _ in FORM_CONTROLS} == (
        SKIP_FORMS | negative_shape.gated_mark_forms()
    )


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
    """The shape that landed unnoticed, and the flag that turns the checker off.

    Two relaxations over a copy of the project file: one more row under
    per-file-ignores, and the type checker's strict flag off.  Both are seen,
    and nothing else moves.
    """
    original = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    header = "[tool.ruff.lint.per-file-ignores]\n"
    mutated = original.replace(
        header, f'{header}"tests/probes/*.py" = ["S101"]\n'
    ).replace("strict = true\n", "strict = false\n")
    copy = tmp_path / "pyproject.toml"
    copy.write_text(mutated, encoding="utf-8")

    assert mutated != original

    read = negative_shape.config_tables(copy, CONFIG_TABLES)
    rows = read["tool.ruff.lint"]["per-file-ignores"]
    pinned = CONFIG_BASELINE["tool.ruff.lint"]["per-file-ignores"]

    assert read != CONFIG_BASELINE
    assert read["tool.mypy"]["strict"] is False
    assert rows["tests/probes/*.py"] == ("S101",)
    assert {key: row for key, row in rows.items() if key in pinned} == pinned
    assert [key for key in read if read[key] != CONFIG_BASELINE[key]] == [
        "tool.mypy",
        "tool.ruff.lint",
    ]


def test_the_project_file_is_the_only_tool_configuration() -> None:
    """A second configuration file would make the pinned tables meaningless."""
    assert [name for name in FOREIGN_TOOL_FILES if (REPO_ROOT / name).exists()] == []
