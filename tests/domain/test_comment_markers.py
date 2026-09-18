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


def strings(node: ast.AST) -> tuple[str, ...] | None:
    """The string or strings *node* stands for, or None if it is not one."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, ast.Tuple | ast.List) and node.elts:
        parts = [strings(element) for element in node.elts]
        if all(part is not None for part in parts):
            return tuple(value for part in parts if part for value in part)
    return None


def bindings_in(body: list[ast.stmt] | ast.AST) -> dict[str, tuple[str, ...]]:
    """The names *body* binds to a string, read off its own statements.

    A module's own top level and a function's own body are the two scopes a
    name is resolved in, and never each other's: a local binding in one
    function says nothing about a name another function passes, and the
    last one walked would otherwise answer for both.
    """
    nodes = (
        [inner for statement in body for inner in ast.walk(statement)]
        if isinstance(body, list)
        else list(ast.walk(body))
    )
    bound: dict[str, tuple[str, ...]] = {}
    for node in nodes:
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


def string_bindings(sources: dict[str, str]) -> dict[str, dict[str, tuple[str, ...]]]:
    """Per module, the module-level names bound to a string or to strings.

    Keyed by the module that binds them: one name can stand for different
    strings in two modules, and a single table across the tree would let
    whichever was walked last answer for both.
    """
    return {
        path: bindings_in(ast.parse(source).body) for path, source in sources.items()
    }


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
    locals_of: dict[int, dict[str, tuple[str, ...]]] = {}

    def strings_named(name: str) -> tuple[str, ...] | None:
        if name in bound[path]:
            return bound[path][name]
        origin = origins[path].get(name)
        if origin is not None and name in bound.get(origin, {}):
            return bound[origin][name]
        return None

    def strings_local(
        owner: ast.FunctionDef | ast.AsyncFunctionDef, name: str
    ) -> tuple[str, ...] | None:
        scope = locals_of.setdefault(id(owner), bindings_in(owner))
        return scope.get(name)

    callees = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name | ast.Attribute) and id(node) not in callees:
            # A reader handed on as a value — an alias, a partial application,
            # a decorator — reaches configuration at a call this walk cannot
            # see, so it is reported rather than passed over in silence.
            referenced = node.attr if isinstance(node, ast.Attribute) else node.id
            if referenced in readers:
                unresolved.append(f"{path}:{node.lineno}")
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
            owner = None if named is None else enclosing_function(node, parents)
            # The parameter seat is asked first: a name a function answers
            # for is the forwarding shape, whatever else the module binds
            # under the same word.
            seat = None if owner is None or named is None else _seat(owner, named)
            if seat is not None and owner is not None:
                if seat not in readers.setdefault(owner.name, set()):
                    readers[owner.name].add(seat)
                    grown = True
                continue
            forwarded = (
                strings_local(owner, named)
                if owner is not None and named is not None
                else None
            )
            if forwarded is None and named is not None:
                forwarded = strings_named(named)
            if forwarded is not None:
                purposes.update(forwarded)
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
    """The example table is the complete list of purposes this tree writes.

    What the guard covers: every call of the two configuration readers, and
    of any function that forwards its own parameter to one of them, found
    by rescanning the tree until no further forwarder appears; the purpose
    at each such call resolved from a literal, from the calling function's
    own bindings, or from its module's top-level bindings, including one
    imported from another module's top level. A reader named anywhere other
    than as the callee of a call is reported as unresolved rather than
    passed over, and an unresolved call fails this test.

    What it does not see, and what would therefore have to be read from the
    code itself: a prefix mapping subscripted or ``.get``-read directly
    instead of through a reader; a reader reached by runtime reflection; a
    purpose computed at runtime rather than written down; and a forwarder
    that passes a name it received other than as one of its own parameters.
    """
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


def test_a_local_binding_elsewhere_does_not_answer_for_a_forwarded_parameter():
    """The seat a function answers for outranks any name its module reuses.

    ``purpose`` is an ordinary word, and a module is free to bind it to a
    declared purpose in one function while another forwards its own
    parameter under the same name; resolved the other way round, the
    wrapper reads as a declared purpose and its call sites go unscanned.
    """
    sources = source_tree()
    sources["adapters/reused_name.py"] = (
        "from kodezart.domain.comment_markers import configured_marker_prefix\n"
        "class Markers:\n"
        "    def declared(self):\n"
        "        purpose = 'claim'\n"
        "        return configured_marker_prefix(self._prefixes, purpose=purpose)\n"
        "\n"
        "    def _fwd(self, purpose):\n"
        "        return configured_marker_prefix(self._prefixes, purpose=purpose)\n"
        "\n"
        "    def invented_pattern(self):\n"
        "        return self._fwd('invented')\n"
    )
    purposes, unresolved = written_purposes(sources)
    declared = tomllib.loads(EXAMPLE.read_text())["marker_prefixes"]
    assert unresolved == ()
    assert sorted(purposes - set(declared)) == ["invented"]


def test_a_purpose_bound_inside_one_function_does_not_answer_for_another():
    """Each function's own bindings resolve its own names, and no others.

    Read across the module, the binding walked last would answer for every
    function that passes that name, so an undeclared purpose bound in one
    of them would be reported as whichever declared purpose came after it.
    """
    sources = source_tree()
    sources["adapters/two_bindings.py"] = (
        "from kodezart.domain.comment_markers import configured_marker_prefix\n"
        "def invented(prefixes):\n"
        "    purpose = 'invented'\n"
        "    return configured_marker_prefix(prefixes, purpose=purpose)\n"
        "\n"
        "def declared(prefixes):\n"
        "    purpose = 'claim'\n"
        "    return configured_marker_prefix(prefixes, purpose=purpose)\n"
    )
    purposes, unresolved = written_purposes(sources)
    declared = tomllib.loads(EXAMPLE.read_text())["marker_prefixes"]
    assert unresolved == ()
    assert sorted(purposes - set(declared)) == ["invented"]


@pytest.mark.parametrize(
    "module",
    [
        "from kodezart.domain.comment_markers import configured_marker_prefix\n"
        "read = configured_marker_prefix\n"
        "def prefix(prefixes):\n"
        "    return read(prefixes, purpose='invented')\n",
        "import functools\n"
        "from kodezart.domain.comment_markers import configured_marker_prefix\n"
        "def prefix(prefixes):\n"
        "    read = functools.partial(configured_marker_prefix, purpose='invented')\n"
        "    return read(prefixes)\n",
    ],
)
def test_a_reader_handed_on_as_a_value_is_reported_as_unresolved(module: str):
    """A reader reached through another name reaches configuration unseen.

    The purpose is then passed at a call this walk cannot follow, so the
    reference itself is the finding: the guard says it cannot answer for
    this module rather than answering that the module writes nothing.
    """
    sources = source_tree()
    sources["adapters/aliased_reader.py"] = module
    _, unresolved = written_purposes(sources)
    assert [place for place in unresolved if place.startswith("adapters/")]


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
