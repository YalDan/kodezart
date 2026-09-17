"""Configured marker identities and the static ban on literal write prefixes."""

import ast
import re
import tomllib
from pathlib import Path

import pytest

from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src" / "kodezart"
EXAMPLE = REPO / "docs" / "operation.example.toml"
LITERAL_PREFIX = re.compile(r"<!--(?:\s|\\s[*+?]?)*[A-Za-z][\w.-]*|\[[A-Za-z][\w.-]*:")
#: The two functions that turn a purpose into a configured prefix.  Every
#: purpose the tree writes under reaches configuration through one of them.
PURPOSE_READERS = frozenset({"compose_comment_marker", "configured_marker_prefix"})


WRITING_METHODS = frozenset(
    {
        "upsert_comment",
        "post_comment",
        "edit_description",
        "update_issue",
        "record_work_ref",
        "record_base_spec",
        "compose_comment_marker",
        "claim_body",
        "work_ref_body",
        "base_spec_body",
    }
)


def is_tracker_writer(source: str) -> bool:
    return any(
        (isinstance(node, ast.Attribute) and node.attr in WRITING_METHODS)
        or (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name in WRITING_METHODS
        )
        or (isinstance(node, ast.Name) and node.id == "_TOOL_SAVE_COMMENT")
        for node in ast.walk(ast.parse(source))
    )


def literal_prefixes(source: str) -> list[int]:
    tree = ast.parse(source)
    documentation = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in documentation
        and LITERAL_PREFIX.search(node.value)
    ]


def test_no_writing_module_contains_a_literal_marker_prefix():
    assert {
        str(path.relative_to(SOURCE)): lines
        for path in SOURCE.rglob("*.py")
        if is_tracker_writer(source := path.read_text())
        and (lines := literal_prefixes(source))
    } == {}


@pytest.mark.parametrize(
    "source",
    [
        'body = "<!-- arbitrary-prefix payload -->"',
        'body = f"<!-- arbitrary-prefix {payload} -->"',
        'marker = f"[arbitrary-prefix:{lane}]"',
        'pattern = r"<!--\\s*arbitrary-prefix\\s+payload"',
    ],
)
def test_static_guard_catches_new_prefix_spellings(source: str):
    assert literal_prefixes(source)


def test_lane_and_occurrence_identity_are_composed_from_configuration():
    operation = OperationConfig(
        operation_name="fixture",
        workspace="fixture",
        marker_prefixes={"decision": "configured-decision"},
    )
    assert (
        compose_comment_marker(
            prefixes=operation.marker_prefixes, purpose="decision", lane="lane/one"
        )
        == "[configured-decision:lane%2Fone]"
    )
    assert (
        compose_comment_marker(
            prefixes=operation.marker_prefixes,
            purpose="decision",
            lane="lane/one",
            occurrence_key="ruling:two",
        )
        == "[configured-decision:lane%2Fone:ruling%3Atwo]"
    )


def test_identity_components_cannot_collide_through_delimiters():
    prefixes = {"purpose": "configured"}
    assert compose_comment_marker(
        prefixes=prefixes, purpose="purpose", lane="lane:occurrence"
    ) != compose_comment_marker(
        prefixes=prefixes, purpose="purpose", lane="lane", occurrence_key="occurrence"
    )


def test_absent_purpose_refuses_at_use_without_inventing_a_prefix():
    operation = OperationConfig(operation_name="fixture", workspace="fixture")
    with pytest.raises(OperationMemberAbsentError, match="marker_prefixes"):
        compose_comment_marker(
            prefixes=operation.marker_prefixes, purpose="missing", lane="lane"
        )


@pytest.mark.parametrize(
    "prefixes",
    [
        {"purpose": ""},
        {"purpose": "two words"},
        {"purpose": "line\nbreak"},
        {"purpose": "[nested]"},
        {"purpose": "a:b"},
        {"purpose": 'a"b'},
        {"one": "same", "two": "same"},
    ],
)
def test_invalid_or_shared_prefixes_are_rejected_by_operation_config(prefixes):
    with pytest.raises(ValueError, match="marker_prefixes"):
        OperationConfig(
            operation_name="fixture", workspace="fixture", marker_prefixes=prefixes
        )


def source_tree() -> dict[str, str]:
    return {
        path.relative_to(SOURCE).as_posix(): path.read_text()
        for path in SOURCE.rglob("*.py")
    }


def string_bindings(sources: dict[str, str]) -> dict[str, tuple[str, ...]]:
    """Module-level names bound to a string, or to a tuple of strings."""
    bound: dict[str, tuple[str, ...]] = {}

    def strings(node: ast.AST) -> tuple[str, ...] | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return (node.value,)
        if isinstance(node, ast.Tuple | ast.List) and node.elts:
            parts = [strings(element) for element in node.elts]
            if all(part is not None for part in parts):
                return tuple(value for part in parts if part for value in part)
        return None

    for source in sources.values():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Assign):
                targets, values = node.targets, strings(node.value)
            elif isinstance(node, ast.For):
                targets, values = [node.target], strings(node.iter)
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and values is not None:
                    bound[target.id] = values
    return bound


def written_purposes(sources: dict[str, str]) -> tuple[frozenset[str], tuple[str, ...]]:
    """Every purpose this tree writes under, and every one it cannot resolve.

    A purpose forwarded from the enclosing function's own parameter is
    answered at that function's call sites, not here.
    """
    bound = string_bindings(sources)
    purposes: set[str] = set()
    unresolved: list[str] = []
    for path, source in sources.items():
        tree = ast.parse(source)
        parameters = {
            argument.arg
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            for argument in (*node.args.args, *node.args.kwonlyargs)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", None)
            )
            if called not in PURPOSE_READERS:
                continue
            argument = next(
                (word.value for word in node.keywords if word.arg == "purpose"), None
            )
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                purposes.add(argument.value)
            elif isinstance(argument, ast.Name) and argument.id in bound:
                purposes.update(bound[argument.id])
            elif not (isinstance(argument, ast.Name) and argument.id in parameters):
                unresolved.append(f"{path}:{node.lineno}")
    return frozenset(purposes), tuple(unresolved)


def test_the_annotated_example_declares_every_purpose_the_tree_writes_under():
    purposes, unresolved = written_purposes(source_tree())
    assert unresolved == ()
    assert purposes, "a tree writing under no purpose states nothing about the example"
    declared = tomllib.loads(EXAMPLE.read_text())["marker_prefixes"]
    assert sorted(purposes - set(declared)) == []


def test_a_purpose_no_operation_declares_is_reported_by_the_same_guard():
    sources = source_tree()
    sources["chains/new_writer.py"] = (
        "from kodezart.domain.comment_markers import compose_comment_marker\n"
        "def marker(prefixes, lane):\n"
        "    return compose_comment_marker(\n"
        "        prefixes=prefixes, purpose='invented', lane=lane\n"
        "    )\n"
    )
    purposes, unresolved = written_purposes(sources)
    declared = tomllib.loads(EXAMPLE.read_text())["marker_prefixes"]
    assert unresolved == ()
    assert sorted(purposes - set(declared)) == ["invented"]
