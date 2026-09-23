"""The negative shape of the tree, named here and nowhere else.

A hole nothing counts is a hole that grows.  The measure is an absolute
in-tree baseline: every suppression directive, every form that keeps a
collected test from running, every mark the collection gate deselects,
every test declaration, every file's assertion count and the project
file's tool tables are read from the tree as it stands and compared with
what is written down below.  Nothing is diffed against a base ref -- none is
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
record (KOD-865).  The floor is a count per file, so an assertion deleted
from a file in the same change that adds another to that file is invisible
in the same way; that is the price of a floor that lets every unlanded
branch add tests without a table edit.  The tree's form-comparing
instrument, the detector in
`services/assertion_drift.py`, reads the shape of a designated test's
assertions between two commits; that is a different question, asked
elsewhere, of a surface somebody nominated.

One class of the shipped proxy is out of this census: a model configuration
whose `extra` setting is loosened from forbidding unknown fields to
allowing them.  The tree carries none today and nothing here would see one.

A second class is out of it too, and it is stated here as a class.  The
gate's tools are four: the test runner, the type checker, the linter and
the formatter (its fifth recipe, `verify-no-origin-literal`, is a text
search that honours no suppression).  The census reads the modules of the
two trees it walks, `src/kodezart` and `tests/` (the test files, and the
conftest files under `tests/` among them; a conftest at the repository
root is outside its walk), and the pinned tool tables of `pyproject.toml`,
and it reads in them only the shapes this census counts.  It also looks,
beside the project file and in every directory the walk reaches, for
another configuration file one of those tools would read
(`FOREIGN_TOOL_FILES`), and reds the suite on any it finds.  Nothing
else that changes what any of the gate's tools checks, or how it checks
it, is read: not what lives outside those files, and not what lives
inside them in any other form.  Of the runner's table the key set is
pinned whole, and of its values only `filterwarnings` and `markers` are
read: a key it does not carry today -- `addopts`, `norecursedirs`, a collection-name key
(`python_files`, `python_classes`, `python_functions`),
`empty_parameter_set_mark`, `collect_imported_tests` -- reds the suite when
it is added, while a changed value of another key it carries, `testpaths`
among them, is not seen.  Examples of the class, none of them read:

- the gate's own invocation: every recipe the Makefile `check:` target
  runs, with its options, its paths and its config-file flags
  (`verify-no-origin-literal:`, `format-check:`, `lint:`, `type-check:`,
  `test:`), so a `--disable-error-code`
  handed to the type checker or an `--extend-ignore` handed to the linter
  is honoured and unseen; the prerequisite list of `check:`, which decides
  which of those recipes run at all; the CI step that runs the gate
  (`make check` in `.github/workflows/check.yml`); and the environment
  variables each tool reads, `PYTEST_ADDOPTS`, `PYTEST_PLUGINS` and
  `MYPYPATH` among them;
- code the type checker does not reach, which it checks nothing in: a body
  under `if not TYPE_CHECKING:`, a branch behind a platform or version
  guard (`sys.platform`, `sys.version_info`) that the checked platform or
  version rules out, and a body under the checker's own skip decorator
  (`typing.no_type_check`), none of which carries a comment to count;
- a hook that a conftest or a plugin runs at any phase, at collection or at
  run time: `pytest_collection_modifyitems` removing items,
  `pytest_configure` changing the selection options,
  `pytest_ignore_collect`, `pytest_runtest_protocol`,
  `pytest_runtest_call`, `pytest_pyfunc_call`, and
  `pytest_runtest_makereport` rewriting an outcome.  `pytest_deselected`
  is only a notification hook: it is told which items were removed and
  cannot remove any itself;
- a plugin loaded any way: through `pytest_plugins` in a conftest or in a
  test module, through the project's own `pytest11` entry points, or
  through the entry point of a plugin that a new dependency brings;
- `__test__` set false on a module, a class or a function;
- a `pytest_generate_tests` that parametrizes over an empty set, and a
  `parametrize` mark whose parameter set is empty, which the runner
  reports as a skip at collection under a setting of its own table;
- `warnings.simplefilter("ignore")`, whether called at import or during a
  test or a fixture, an autouse fixture relaxing the pinned
  `filterwarnings = ["error"]`, and a `filterwarnings` mark on one test or
  a whole module;
- `collect_ignore` and `collect_ignore_glob` in a conftest, a module-level
  deletion or rebinding of a test's name, a fixture decorator applied to a
  test, and a skip form reached through a submodule
  (`unittest.case.SkipTest`).

One member of that class is worth its own words: a stub carrying no
directive at all, sitting beside the module it shadows, takes that module
out of the type checker's reach, because the checker reads the stub in
place of it.  That shape has no directive to count and no roster can see
it; it is a diff the code review has to catch.

A new row in any table below, and a deleted name or a lowered count in
`negative_shape_baseline.json`, is a decision.  It belongs in the commit
that needs it, with its reason written there.
"""

import json
from pathlib import Path

import pytest

from tests import negative_shape
from tests.conftest import GATED_MARKERS, pytest_collection_modifyitems
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
#:
#: The linter's table is pinned whole rather than at its lint subtree alone.
#: The keys that decide which files it reads at all -- the exclusions, the
#: includes, the inherited configuration -- are an open family, and one
#: exact table subsumes them where a hand-written list of hole spellings
#: would not.  So a key that narrows the tree the linter reads, a key that
#: widens it, a raised line length (which is one rule's threshold) and a
#: formatter change are each a row edit here, with the reason written beside
#: it.
CONFIG_TABLES: tuple[str, ...] = (
    "tool.mypy",
    "tool.pydantic-mypy",
    "tool.pytest.ini_options.filterwarnings",
    "tool.ruff",
)
#: The keys of the runner's own table, as a set: their values are read
#: where one matters here (`filterwarnings` above, `markers` by the gate's
#: cases), and the set is pinned so that a key added to the table -- one that
#: stops a directory, a file or a name being collected, or adds options to
#: every run -- is a row edit here, with its reason beside it.
RUNNER_KEYS: frozenset[str] = frozenset(
    {
        "asyncio_mode",
        "asyncio_default_fixture_loop_scope",
        "pythonpath",
        "testpaths",
        "filterwarnings",
        "markers",
    }
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
    "tool.ruff": {
        "target-version": "py312",
        "line-length": 88,
        # No exclusion key, no include key.  What the gate hands the linter
        # is not read here: that is the gate's invocation, stated above.
        "src": ("src", "tests"),
        "lint": {
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
        "format": {"quote-style": "double", "indent-style": "space"},
    },
}

#: One hand-written source per form, each binding the form's own module a
#: different way, so the import alias, the from-import, the assignment
#: alias, a name bound twice, the annotated assignment, an import inside the
#: function that calls the form, a from-import inside that function and a
#: wrapped import an alias is copied from each have a control, and so does a
#: form a longer chain continues.  The tree aliases neither module today, so
#: these are the only proof those arms work.
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
    # The import that binds pytest sits inside the function that calls the
    # form; three modules of the suite import it this way today.
    ("pytest.skip", "def test_a():\n    import pytest\n    pytest.skip('x')\n"),
    # The mark factory's own combinator: the form is the chain the call is
    # built on rather than the whole chain.
    (
        "pytest.mark.skipif",
        "import pytest\n@pytest.mark.skipif.with_args(True, reason='x')\n"
        "def test_a(): ...\n",
    ),
    # The standard library's own forms.  Four of them -- the unconditional
    # skip, the two conditional ones and the raised exception -- the runner
    # honours on a plain test function as well as on a case class, so each of
    # those is a collected test that does not run.  The fifth, the expected
    # failure, it honours on a case class only, and that one is rostered for a
    # reason of its own: a test carrying it is a test whose failure does not
    # count.  The suite binds the module nowhere today, which is why the
    # roster gains no row.  One binding shape each, as above.
    ("unittest.skip", "import unittest\n@unittest.skip('x')\ndef test_a(): ...\n"),
    (
        "unittest.skipIf",
        "import unittest as ut\n@ut.skipIf(True, 'x')\ndef test_a(): ...\n",
    ),
    (
        "unittest.skipUnless",
        "from unittest import skipUnless as unless\n@unless(False, 'x')\n"
        "def test_a(): ...\n",
    ),
    (
        "unittest.SkipTest",
        "import unittest\ndef test_a():\n    raise unittest.SkipTest('x')\n",
    ),
    (
        "unittest.expectedFailure",
        "from unittest import expectedFailure\n@expectedFailure\ndef test_a(): ...\n",
    ),
    # The from-import written inside the function that calls the form: the
    # other local-import control binds through a plain import, so without
    # this one the from-import arm could be narrowed back to the module body
    # and nothing would say so.
    (
        "pytest.importorskip",
        "def test_a():\n    from pytest import importorskip as need\n"
        "    need('yaml')\n",
    ),
    # A wrapped import the alias is copied from: the import sits nested and
    # the assignment that copies it sits at module level below, so the two
    # are only read in the right order when the reading follows the source.
    (
        "pytest.mark.skip",
        "try:\n    import pytest\nexcept ImportError:\n    pytest = None\n"
        "m = pytest.mark\n@m.skip\ndef test_a(): ...\n",
    ),
)

#: One directive of each form the pattern claims to read, held as data and
#: fed through a source built from text, so this module carries none of
#: them as a comment and adds nothing to its own census.
DIRECTIVE_CONTROLS: tuple[str, ...] = (
    "# type: ignore[arg-type]",
    "# noqa: E501",
    "# ruff: noqa",
    "# flake8: noqa",
    "# isort: skip_file",
    # The linter honours a sorter exemption under its own prefix as well as
    # bare, and the bare spelling above does not read the prefixed one.
    "# ruff: isort: skip_file",
    "# ruff: isort: off",
    "# mypy: ignore-errors",
    "# mypy: disable-error-code=attr-defined",
    "# mypy: allow-untyped-defs",
    # The linter matches its exemptions without regard to case, so an
    # upper-case and a mixed-case spelling each have a control.  The tree
    # carries neither today, so ALLOWED does not gain a row for them.
    "# NOQA",
    "# flake8: NoQA",
    # The formatter the gate runs honours this one and its partner, and a
    # per-statement form beside them.  The tree carries none today.
    "# fmt: off",
    # The same formatter honours the whole-region pair of the formatter it
    # replaced, so that pair is in the same family.  The tree carries none.
    "# yapf: disable",
    # The linter honours a range suppression under its own prefix: every
    # line between the pair is exempt from the named rules.  The tree
    # carries none today.
    "# ruff: disable[E501]",
    "# ruff: enable[E501]",
)

#: The files a tool discovers instead of, or ahead of, the project file.
#: The linter reads the one closest to each file it checks and inherits
#: nothing from the one above, so a file with any of these names beside the
#: project file, or anywhere under a walked tree, makes the pinned table
#: above meaningless for everything below it.  The project file's own name
#: is a row here because a second copy of it under a walked tree is read the
#: same way; the pinned one at the root is not a hit.  The runner searches
#: its own files, dotted and bare, ahead of the project file, so each of them
#: is a row too.
FOREIGN_TOOL_FILES: tuple[str, ...] = (
    ".flake8",
    ".mypy.ini",
    ".pytest.ini",
    ".pytest.toml",
    ".ruff.toml",
    "mypy.ini",
    "pyproject.toml",
    "pytest.ini",
    "pytest.toml",
    "ruff.toml",
    "setup.cfg",
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


def test_the_walk_reads_a_stub_as_well_as_a_module(tmp_path: Path) -> None:
    """A stub the tools read is a file the census has to read too.

    The linter lints a stub under the same table and honours a directive
    comment in it, and the type checker reads a stub beside a module in
    place of that module and honours the stub's own inline setting.  Two
    files under a walked tree, one of each suffix: both are walked, in one
    order, and the stub's directive is read from it.
    """
    package = tmp_path / "src" / "kodezart" / "core"
    package.mkdir(parents=True)
    (package / "shadow.py").write_text("value = 1\n", encoding="utf-8")
    (package / "shadow.pyi").write_text("import os  # noqa\n", encoding="utf-8")

    walked = negative_shape.walk(tmp_path)

    assert [module.path for module in walked] == [
        "src/kodezart/core/shadow.py",
        "src/kodezart/core/shadow.pyi",
    ]
    assert negative_shape.comment_directives(walked[0]) == ()
    assert negative_shape.comment_directives(walked[1]) == ("# noqa",)


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


def test_a_comment_that_only_mentions_a_directive_is_not_one() -> None:
    """The pattern reads the marker and the family, not the word in prose.

    Reading every family without regard to case widens what a comment may
    say and still be a directive, so the other direction has a control too.
    """
    prose = Source.of("control.py", "x = 1  # never write a noqa on a shipped line\n")

    assert negative_shape.comment_directives(prose) == ()


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


class _UnfilteredConfig:
    """A run configuration with no marker expression, as the default run has."""

    def getoption(self, name: str, default: object = None) -> object:
        return ""


class _CollectedItem:
    """A collected item reduced to what the gate reads and writes."""

    def __init__(self, *, keywords: set[str], marks: tuple[pytest.Mark, ...] = ()):
        self.keywords = dict.fromkeys(keywords, True)
        self.own_marks = marks
        self.added: list[pytest.MarkDecorator] = []

    def iter_markers(self, name: str | None = None) -> list[pytest.Mark]:
        return [mark for mark in self.own_marks if name is None or mark.name == name]

    def add_marker(self, marker: pytest.MarkDecorator) -> None:
        self.added.append(marker)


def test_the_gate_skips_a_test_only_for_a_gated_mark_it_carries() -> None:
    """A keyword spelled like a gated marker is not the marker.

    A parametrize id or a function attribute lands in an item's keywords
    under its own spelling, so a gate reading keywords would skip a test that
    carries no gated mark, and the gated-mark roster could not see it. Each
    gated marker is checked both ways: the keyword alone gains nothing, and
    the mark itself gains the gate's skip.
    """
    for marker in GATED_MARKERS:
        keyworded = _CollectedItem(keywords={marker})
        marked = _CollectedItem(
            keywords={marker}, marks=(getattr(pytest.mark, marker).mark,)
        )

        pytest_collection_modifyitems(_UnfilteredConfig(), [keyworded, marked])

        assert keyworded.added == []
        assert [decorator.mark.name for decorator in marked.added] == ["skip"]


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


def test_importing_the_mock_package_binds_no_skip_form() -> None:
    """Why the roster gains no row for the standard library's forms.

    Most of the suite imports the mock package.  A from-import of it binds a
    member of the package, never the module whose skip forms the roster
    names.  The plain-import shape below is a weaker claim: a dotted import
    of the package binds the dotted string and not the root it sits under, so
    a form spelled on that root after such an import alone resolves to
    nothing -- the blind spot the resolver states, not a property of the
    package.  Either way, no module reports one of those forms today.
    """
    forms = SKIP_FORMS | negative_shape.gated_mark_forms()
    control = Source.of(
        "control.py",
        "from unittest.mock import AsyncMock, patch\nimport unittest.mock\n"
        "def test_a():\n    AsyncMock()\n    patch('x')\n",
    )

    assert negative_shape.form_bindings(control.tree) == {
        "AsyncMock": "unittest.mock.AsyncMock",
        "patch": "unittest.mock.patch",
        "unittest.mock": "unittest.mock",
    }
    assert negative_shape.sites(control, forms) == ()


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


def test_an_assert_is_a_statement_and_not_a_line() -> None:
    """Two on one line are two; one quoted in prose is none."""
    two = Source.of("control.py", "x = 1; assert x; assert y\n")
    prose = Source.of("control.py", '"""\nassert this is prose\n"""\nassert a\n')

    assert negative_shape.assert_count(two) == 2
    assert negative_shape.assert_count(prose) == 1


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


def test_the_runner_table_carries_exactly_the_pinned_keys() -> None:
    """A key added to the runner's table can stop tests being collected at all."""
    table = negative_shape.config_tables(
        REPO_ROOT / "pyproject.toml", ("tool.pytest.ini_options",)
    )["tool.pytest.ini_options"]

    assert isinstance(table, dict)
    assert set(table) == RUNNER_KEYS


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
    rows = read["tool.ruff"]["lint"]["per-file-ignores"]
    pinned = CONFIG_BASELINE["tool.ruff"]["lint"]["per-file-ignores"]

    assert read != CONFIG_BASELINE
    assert read["tool.mypy"]["strict"] is False
    assert rows["tests/probes/*.py"] == ("S101",)
    assert {key: row for key, row in rows.items() if key in pinned} == pinned
    assert [key for key in read if read[key] != CONFIG_BASELINE[key]] == [
        "tool.mypy",
        "tool.ruff",
    ]


def test_the_project_file_is_the_only_tool_configuration() -> None:
    """A second configuration file would make the pinned tables meaningless.

    Searched wherever the walk reaches, not at the root alone: the linter
    reads the configuration closest to each file it checks, so a file under
    a walked package exempts that package and leaves the pinned table above
    reading exactly as it does now.
    """
    walked = [module.path for module in negative_shape.sources()]

    assert (
        negative_shape.foreign_configuration(
            REPO_ROOT / "pyproject.toml", walked, FOREIGN_TOOL_FILES
        )
        == []
    )


def test_the_configuration_scan_reads_every_directory_the_walk_reaches(
    tmp_path: Path,
) -> None:
    """A file nested under a walked tree is the same hole as one beside the root.

    Two hits over a fake tree, one under a package and one a second copy of
    the project file; the pinned file at the root is not a hit, and the same
    tree without them is clean.
    """
    walked = ("src/kodezart/services/agent_service.py", "tests/domain/test_a.py")
    project = tmp_path / "pyproject.toml"
    project.write_text("", encoding="utf-8")
    nested = tmp_path / "src" / "kodezart" / "services"
    nested.mkdir(parents=True)

    assert (
        negative_shape.foreign_configuration(project, walked, FOREIGN_TOOL_FILES) == []
    )

    (nested / "ruff.toml").write_text("", encoding="utf-8")
    (tmp_path / "src" / "pyproject.toml").write_text("", encoding="utf-8")

    assert negative_shape.foreign_configuration(
        project, walked, FOREIGN_TOOL_FILES
    ) == ["src/kodezart/services/ruff.toml", "src/pyproject.toml"]
