"""Configured marker identities and the static ban on literal write prefixes."""

import ast
import re
from pathlib import Path

import pytest

from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError

SOURCE = Path(__file__).resolve().parents[2] / "src" / "kodezart"
LITERAL_PREFIX = re.compile(r"<!--(?:\s|\\s[*+?]?)*[A-Za-z][\w.-]*|\[[A-Za-z][\w.-]*:")


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
