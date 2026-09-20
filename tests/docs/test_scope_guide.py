"""The run-a-scope page, checked against the code it instructs against.

Every side of every assertion here is derived: the events out of `src/`, the
variables out of the shipped settings model, the failure classes out of the error
modules, and the config keys out of the shipped operation file itself. A page
that told an operator to watch for an event nothing emits, to set a variable
nothing reads, or to declare a key the file does not carry would read as correct
and leave them stuck.
"""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, get_args, get_origin, get_type_hints

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core import errors as core_errors
from kodezart.domain import errors as domain_errors
from kodezart.types.domain import operation as operation_types
from kodezart.types.domain import scope_runtime
from tests.docs.configuration import shipped_config_variables
from tests.docs.test_documented_surface import _config_variables_named_in
from tests.docs.test_setup_guide import _emitted_events
from tests.tools.scratch_scope import COMMANDS

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDE = REPO_ROOT / "docs" / "running-a-scope.md"
SCOPE_CONFIG = REPO_ROOT / "docs" / "operation.scope.toml"

#: The environment the page MUST instruct an operator to set. A floor, not an
#: inventory: every `KODEZART_` name the page carries is checked against the
#: shipped model by the test below, so a seventh needs no entry here.
REQUIRED_VARIABLES: frozenset[str] = frozenset(
    {
        "KODEZART_TRACKER__TOKEN",
        "KODEZART_GITHUB_TOKEN",
        "KODEZART_OPERATION_CONFIG",
        "KODEZART_ORGANIZE__MAX_ADMISSION_ROUNDS",
        "KODEZART_ORGANIZE__MAX_CONVERGENCE_ROUNDS",
        "KODEZART_WRITE_BACK__MAX_VERIFY_ROUNDS",
    },
)

#: The documents that used to say scoped execution could not run.
CORRECTED_DOCUMENTS: tuple[Path, ...] = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "api.md",
    REPO_ROOT / "docs" / "configuration.md",
    REPO_ROOT / "docs" / "delivery.md",
)

#: Each statement one of those documents made, and no longer makes.
STALE_STATEMENTS: tuple[str, ...] = (
    "not yet implemented",
    "currently unavailable",
    "no active scope walker",
    "UnsupportedClaimError",
)

#: Where a failure class the page names has to resolve.
ERROR_MODULES = (core_errors, domain_errors, operation_types)

_SECTION = re.compile(r"^## (?P<heading>.+)$", re.MULTILINE)


def guide() -> str:
    return GUIDE.read_text(encoding="utf-8")


def section(heading: str) -> str:
    """One `##` section's body, so a match elsewhere on the page is not one."""
    text = guide()
    start = text.index(f"## {heading}")
    rest = text[start:]
    match = _SECTION.search(rest, pos=len(heading) + 4)
    return rest if match is None else rest[: match.start()]


def fenced(language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)```", guide(), flags=re.DOTALL)


def test_the_page_exists_and_has_content() -> None:
    """A floor under every assertion below: an empty page satisfies them all."""
    assert len(guide()) > 2000
    assert "docs/operation.scope.toml" in guide()
    # And the derived sides are not empty, which would make the subset
    # assertions below pass while checking nothing.
    assert len(_emitted_events()) > 50
    assert len(shipped_config_variables()) > 50


def stream_row_types() -> set[str]:
    """Every stream row type the scope runtime discriminates on.

    Derived from the module's own annotations. A row type is a snake_case name
    the page cites exactly the way it cites an event, and is not one, so it has
    to come off the code rather than out of a list here.
    """
    found: set[str] = set()
    for value in vars(scope_runtime).values():
        if not isinstance(value, type) or value.__module__ != scope_runtime.__name__:
            continue
        annotation = get_type_hints(value).get("type")
        if annotation is None or get_origin(annotation) is not Literal:
            continue
        found.update(item for item in get_args(annotation) if isinstance(item, str))
    return found


def test_every_event_the_page_names_is_emitted_under_src() -> None:
    """The WHOLE page, not one hand-named section.

    An event cited anywhere — in the run section, in a refusal row — is an event
    an operator will go looking for. The scan takes every backticked snake_case
    name, which also collects the stream row types; those are subtracted from
    the code rather than allowed by name.
    """
    cited = set(re.findall(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`", guide()))
    row_types = stream_row_types()
    assert row_types
    assert cited
    events = cited - row_types
    assert events
    assert events <= _emitted_events(), sorted(events - _emitted_events())


def test_every_command_the_page_prints_exists() -> None:
    """The page's command block, against the builder's own command list.

    A renamed command on either side leaves an operator typing something the
    tool does not answer to.
    """
    blocks = fenced("text")
    assert len(blocks) == 1
    names = {item.name for item in COMMANDS}
    assert names
    for name in sorted(names):
        assert f"python -m tests.tools.scratch_scope {name}" in blocks[0], name
    assert set(re.findall(r"scratch_scope ([a-z][a-z-]*)", blocks[0])) == names


def test_every_variable_the_page_names_is_a_shipped_config_field() -> None:
    shipped = shipped_config_variables()
    named = _config_variables_named_in(GUIDE)
    assert named
    assert named <= shipped, sorted(named - shipped)


@pytest.mark.parametrize("variable", sorted(REQUIRED_VARIABLES))
def test_the_page_tells_an_operator_to_set_each_variable_a_run_needs(
    variable: str,
) -> None:
    assert variable in guide()


def test_the_page_prints_one_environment_block_and_no_config_block() -> None:
    """The config is a file one copies; a block here is a copy that goes stale."""
    assert fenced("toml") == []
    assert len(fenced("bash")) == 1
    block = fenced("bash")[0]
    assert REQUIRED_VARIABLES <= set(re.findall(r"(KODEZART_[A-Z0-9_]+)=", block))


def test_every_failure_class_the_page_names_exists() -> None:
    """An operator matching a traceback against a class that is gone is stuck."""
    cited = set(re.findall(r"`([A-Z]\w+Error)`", guide()))
    assert cited
    unresolved = [
        name
        for name in sorted(cited)
        if not any(hasattr(module, name) for module in ERROR_MODULES)
    ]
    assert unresolved == []


def test_every_config_key_the_refusal_table_names_is_in_the_shipped_file() -> None:
    """The table is about the file the page tells you to copy, or about nothing."""
    loaded = load_operation_config(SCOPE_CONFIG)
    sections: dict[str, Mapping[str, str]] = {
        "issue_labels": loaded.issue_labels,
        "scope_labels": loaded.scope_labels,
        "marker_prefixes": loaded.marker_prefixes,
        "workflow_states": {
            stage.value: name for stage, name in loaded.workflow_states.items()
        },
    }
    cited = set(
        re.findall(r"`([a-z_]+)\.([a-z_]+)`", section("What refuses, and where"))
    )
    assert cited
    unresolved = [
        f"{table}.{key}"
        for table, key in sorted(cited)
        if table in sections and key not in sections[table]
    ]
    assert unresolved == []
    # Every table the citation shape can reach is one this test knows about, or
    # a row could name an absent section and go unchecked.
    assert {table for table, _ in cited} <= {*sections, "write_back"}


@pytest.mark.parametrize("document", CORRECTED_DOCUMENTS, ids=lambda path: path.name)
def test_no_document_says_scoped_execution_is_unimplemented(document: Path) -> None:
    """A document that says the path cannot run is a document nobody can act on."""
    text = document.read_text(encoding="utf-8")
    assert [statement for statement in STALE_STATEMENTS if statement in text] == []


def test_the_page_names_no_tracker_key() -> None:
    """Tracker keys are internal; a page cites the fact, not the key."""
    assert re.findall(r"\bKOD-\d+\b", guide()) == []
