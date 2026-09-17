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


def string_bindings(sources: dict[str, str]) -> dict[str, dict[str, tuple[str, ...]]]:
    """Per module, the names bound to a string or to a tuple of strings.

    Keyed by the module that binds them: one name can stand for different
    strings in two modules, and a single table across the tree would let
    whichever was walked last answer for both.
    """
    bound: dict[str, dict[str, tuple[str, ...]]] = {}

    def strings(node: ast.AST) -> tuple[str, ...] | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return (node.value,)
        if isinstance(node, ast.Tuple | ast.List) and node.elts:
            parts = [strings(element) for element in node.elts]
            if all(part is not None for part in parts):
                return tuple(value for part in parts if part for value in part)
        return None

    for path, source in sources.items():
        module: dict[str, tuple[str, ...]] = {}
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Assign):
                targets, values = node.targets, strings(node.value)
            elif isinstance(node, ast.For):
                targets, values = [node.target], strings(node.iter)
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and values is not None:
                    module[target.id] = values
        bound[path] = module
    return bound


def imported_from(source: str) -> dict[str, str]:
    """The module path each imported name in *source* is imported from."""
    origins: dict[str, str] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            origin = node.module.removeprefix("kodezart.").replace(".", "/") + ".py"
            for alias in node.names:
                origins[alias.asname or alias.name] = origin
    return origins


def positional(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """The names this function answers positionally, its receiver excluded."""
    names = [
        argument.arg for argument in (*function.args.posonlyargs, *function.args.args)
    ]
    return names[1:] if names[:1] in (["self"], ["cls"]) else names


def purpose_readers(sources: dict[str, str]) -> dict[str, set[tuple[str, int | None]]]:
    """The two configuration readers, each with where its purpose is passed.

    The position is read off the definition rather than assumed, so a
    reader whose signature changes is still resolved at its call sites.
    """
    readers: dict[str, set[tuple[str, int | None]]] = {}
    for source in sources.values():
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name in PURPOSE_READERS
            ):
                names = positional(node)
                index = names.index("purpose") if "purpose" in names else None
                readers.setdefault(node.name, set()).add(("purpose", index))
    return readers


def written_purposes(sources: dict[str, str]) -> tuple[frozenset[str], tuple[str, ...]]:
    """Every purpose this tree writes under, and every one it cannot resolve.

    The set of functions that reach configuration is a fixed point, not the
    two the tree starts from: a function that passes its own parameter to a
    reader is itself a reader, under that parameter's name and position, and
    the tree is rescanned until no further one appears. That is what makes a
    forwarding wrapper's call sites part of the scanned surface instead of
    an excuse for skipping them.
    """
    bound = string_bindings(sources)
    origins = {path: imported_from(source) for path, source in sources.items()}
    readers = purpose_readers(sources)
    while True:
        purposes, unresolved, grown = _scan(sources, readers, bound, origins)
        if not grown:
            return frozenset(purposes), tuple(unresolved)


def enclosing_function(
    node: ast.AST, parents: dict[int, ast.AST]
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """The function *node* sits inside, or None at module level."""
    while id(node) in parents:
        node = parents[id(node)]
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return node
    return None


def _scan(
    sources: dict[str, str],
    readers: dict[str, set[tuple[str, int | None]]],
    bound: dict[str, dict[str, tuple[str, ...]]],
    origins: dict[str, dict[str, str]],
) -> tuple[set[str], list[str], bool]:
    """One pass: the purposes found, the calls not resolved, and any new reader."""
    purposes: set[str] = set()
    unresolved: list[str] = []
    grown = False
    for path in sources:
        found, missed, more = _scan_module(path, sources[path], readers, bound, origins)
        purposes |= found
        unresolved.extend(missed)
        grown = grown or more
    return purposes, unresolved, grown


def _scan_module(
    path: str,
    source: str,
    readers: dict[str, set[tuple[str, int | None]]],
    bound: dict[str, dict[str, tuple[str, ...]]],
    origins: dict[str, dict[str, str]],
) -> tuple[set[str], list[str], bool]:
    """One module's reader calls, resolved against its own bindings alone."""
    tree = ast.parse(source)
    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    purposes: set[str] = set()
    unresolved: list[str] = []
    grown = False

    def strings_named(name: str) -> tuple[str, ...] | None:
        if name in bound[path]:
            return bound[path][name]
        origin = origins[path].get(name)
        if origin is not None and name in bound.get(origin, {}):
            return bound[origin][name]
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else getattr(node.func, "id", None)
        )
        for parameter, index in sorted(readers.get(called, ()), key=str):
            argument = next(
                (word.value for word in node.keywords if word.arg == parameter), None
            )
            if argument is None and index is not None and len(node.args) > index:
                argument = node.args[index]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                purposes.add(argument.value)
                continue
            named = argument.id if isinstance(argument, ast.Name) else None
            forwarded = None if named is None else strings_named(named)
            if forwarded is not None:
                purposes.update(forwarded)
                continue
            owner = None if named is None else enclosing_function(node, parents)
            seat = None if owner is None else _seat(owner, named)
            if seat is not None and owner is not None:
                if seat not in readers.setdefault(owner.name, set()):
                    readers[owner.name].add(seat)
                    grown = True
                continue
            unresolved.append(f"{path}:{node.lineno}")
    return purposes, unresolved, grown


def _seat(
    owner: ast.FunctionDef | ast.AsyncFunctionDef, name: str | None
) -> tuple[str, int | None] | None:
    """Where *name* sits in *owner*'s own parameters, if it is one of them."""
    names = positional(owner)
    if name in names:
        return name, names.index(name)
    if name is not None and name in {arg.arg for arg in owner.args.kwonlyargs}:
        return name, None
    return None


def test_the_annotated_example_declares_every_purpose_the_tree_writes_under():
    purposes, unresolved = written_purposes(source_tree())
    assert unresolved == ()
    assert purposes, "a tree writing under no purpose states nothing about the example"
    declared = tomllib.loads(EXAMPLE.read_text())["marker_prefixes"]
    assert sorted(purposes - set(declared)) == []


def test_a_purpose_reaching_configuration_through_a_wrapper_is_reported():
    sources = source_tree()
    sources["adapters/new_markers.py"] = (
        "from kodezart.domain.comment_markers import configured_marker_prefix\n"
        "class Markers:\n"
        "    def _prefix(self, purpose):\n"
        "        return configured_marker_prefix(self._prefixes, purpose=purpose)\n"
        "\n"
        "    def invented_pattern(self):\n"
        "        return self._prefix('invented')\n"
    )
    purposes, unresolved = written_purposes(sources)
    declared = tomllib.loads(EXAMPLE.read_text())["marker_prefixes"]
    assert unresolved == ()
    assert sorted(purposes - set(declared)) == ["invented"]


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
