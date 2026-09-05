"""The working-directory MCP injection guard, machine-checked over ``src/``.

The service clones an arbitrary repository into an isolated worktree and
runs an agent with ``cwd`` set to it.  Without ``strict_mcp_config``, a
``.mcp.json`` committed into that repository is loaded into the session
alongside the configured servers — attacker-authored tool injection into a
session that already holds credentials.

The invariant that closes it: **every ``ClaudeAgentOptions`` construction
in the package sets ``strict_mcp_config=True``**, whether or not that same
construction configures ``mcp_servers``.  The guard answers the working
directory and not the server map, so a construction naming neither keyword
is the SDK default — the cloned repository loaded unguarded — and is a
failure here rather than a site the scan never classified.  This module
asserts it structurally over every construction in the package, so a future
site added without the flag fails the gate, and behaviourally against a
workspace that actually carries a server-definition file.

The scan reads the keyword sets a construction can hand the SDK: the
explicit keywords merged with every ``**`` unpack, resolved through the
mapping a helper builds, because both construction sites reach their MCP
options that way and a keyword-only scan would range over zero sites and
pass vacuously.  A builder returning more than one mapping contributes one
merged set per return rather than one union of them all, so a guard set on
the branch that describes a server cannot answer for the branch beside it.
The callable is matched through the names a module's imports bind it to, so
an aliased import is not an exit from the walk.  An option source the scan
cannot resolve is a FAILURE, never a skip — an invariant that quietly stops
covering a site is worse than none.
"""

import ast
from collections.abc import Iterator, Mapping
from itertools import product
from pathlib import Path
from typing import Final

import pytest

from kodezart.types.domain.session import SessionType
from tests.fakes import (
    DEFAULT_SETTING_SOURCES,
    EXECUTOR_MODULES,
    FIXTURE_KNOWLEDGE_SERVER,
    NO_KNOWLEDGE_GRANT,
    knowledge_grant_for,
    recorded_session,
)

SRC: Final[Path] = Path(__file__).resolve().parents[2] / "src" / "kodezart"
OPTIONS_CALLABLE: Final[str] = "ClaudeAgentOptions"
SERVERS_KEYWORD: Final[str] = "mcp_servers"
STRICT_KEYWORD: Final[str] = "strict_mcp_config"


def _called_name(func: ast.expr) -> str | None:
    """The bare callable name of a call target, dotted access included."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _option_bindings(tree: ast.AST) -> set[str]:
    """Every name a module's imports bind the options callable to.

    The callable's own name is always one of them, ``import ... as`` binds
    a further one, and access through a module object is matched by
    attribute name, so no import form leaves a construction unscanned.
    """
    bound = {OPTIONS_CALLABLE}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        bound.update(
            alias.asname
            for alias in node.names
            if alias.name == OPTIONS_CALLABLE and alias.asname is not None
        )
    return bound


def _is_options_call(func: ast.expr, bindings: set[str]) -> bool:
    """Whether a call target names the options callable under *bindings*."""
    if isinstance(func, ast.Name):
        return func.id in bindings
    if isinstance(func, ast.Attribute):
        return func.attr == OPTIONS_CALLABLE
    return False


def _mapping_items(node: ast.expr) -> dict[str, ast.expr] | None:
    """The ``key -> value node`` pairs a mapping expression contributes.

    ``None`` when the expression is not a statically readable mapping —
    a bare name, a comprehension, a subscript.  Callers treat ``None`` as
    a failure rather than as an absence.
    """
    if isinstance(node, ast.Dict):
        literal: dict[str, ast.expr] = {}
        for key, value in zip(node.keys, node.values, strict=True):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                return None
            literal[key.value] = value
        return literal
    if isinstance(node, ast.Call):
        built: dict[str, ast.expr] = {}
        for keyword in node.keywords:
            if keyword.arg is None:
                nested = _mapping_items(keyword.value)
                if nested is None:
                    return None
                built.update(nested)
            else:
                built[keyword.arg] = keyword.value
        return built
    return None


def _functions(trees: Mapping[str, ast.AST]) -> dict[str, list[ast.FunctionDef]]:
    """Every function definition in the scanned sources, keyed by name."""
    found: dict[str, list[ast.FunctionDef]] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                found.setdefault(node.name, []).append(node)
    return found


def _returned_mappings(
    function: ast.FunctionDef,
) -> Iterator[dict[str, ast.expr] | None]:
    """Each value this function returns, read as a mapping."""
    for node in ast.walk(function):
        if isinstance(node, ast.Return) and node.value is not None:
            yield _mapping_items(node.value)


def _assignments(scope: ast.AST) -> dict[str, ast.expr]:
    """Single-name assignments made anywhere inside *scope*."""
    bound: dict[str, ast.expr] = {}
    for node in ast.walk(scope):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            bound[node.targets[0].id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            bound[node.target.id] = node.value
    return bound


def _enclosing_bindings(
    node: ast.AST,
    parents: Mapping[ast.AST, ast.AST],
    tree: ast.AST,
) -> dict[str, ast.expr]:
    """The names in scope where *node* sits: its function's, else the module's."""
    cursor: ast.AST | None = parents.get(node)
    while cursor is not None:
        if isinstance(cursor, ast.FunctionDef | ast.AsyncFunctionDef):
            return _assignments(cursor)
        cursor = parents.get(cursor)
    return _assignments(tree)


def _dereference(node: ast.expr, bound: Mapping[str, ast.expr]) -> ast.expr:
    """Follow a local name to the expression it was assigned, once.

    A construction that builds its option mapping into a local first is the
    shape the package actually uses, and an invariant that cannot see
    through one variable would report every such site as unreadable.
    """
    if isinstance(node, ast.Name) and node.id in bound:
        return bound[node.id]
    return node


def _option_sources(
    call: ast.Call,
    functions: Mapping[str, list[ast.FunctionDef]],
    bound: Mapping[str, ast.expr],
) -> Iterator[list[dict[str, ast.expr] | None]]:
    """Every option source feeding one construction, with its alternatives.

    The explicit keywords are one source; each ``**`` unpack is another,
    resolved through the local it names and the function that builds it.  A
    source carries one alternative per mapping it can evaluate to, so a
    builder with two returns offers the call either of them.  A ``None``
    alternative marks a source this scan cannot read, and a source offering
    no alternative at all is one it could not read either.
    """
    yield [{kw.arg: kw.value for kw in call.keywords if kw.arg is not None}]
    for keyword in call.keywords:
        if keyword.arg is not None:
            continue
        source = _dereference(keyword.value, bound)
        if isinstance(source, ast.Call):
            builders = functions.get(_called_name(source.func) or "", [])
            yield [
                mapping
                for builder in builders
                for mapping in _returned_mappings(builder)
            ] or [None]
            continue
        yield [_mapping_items(source)]


def _is_true(node: ast.expr) -> bool:
    """Whether an expression is the literal ``True``."""
    return isinstance(node, ast.Constant) and node.value is True


def _merged_views(
    call: ast.Call,
    functions: Mapping[str, list[ast.FunctionDef]],
    bound: Mapping[str, ast.expr],
) -> list[dict[str, ast.expr]] | None:
    """Every keyword set one construction can hand the SDK, or ``None``.

    The SDK receives one set per call, so the invariant is asked of a merge
    of the sources rather than of each source alone — which is what lets a
    construction naming neither keyword be classified at all.  Where a
    source offers several alternatives the merge is taken once per choice,
    so a guard a builder sets on one return is never read as covering the
    return beside it.  One unreadable alternative makes the whole
    construction unreadable.
    """
    readable: list[list[dict[str, ast.expr]]] = []
    for source in _option_sources(call, functions, bound):
        alternatives = [item for item in source if item is not None]
        if len(alternatives) != len(source):
            return None
        readable.append(alternatives)
    return [
        {key: value for item in choice for key, value in item.items()}
        for choice in product(*readable)
    ]


def _constructions(
    sources: Mapping[str, str],
) -> Iterator[tuple[str, int, list[dict[str, ast.expr]] | None]]:
    """Every construction's option keywords, with where it was written.

    Docstrings and comments cannot register: the walk visits call nodes, so
    prose naming ``ClaudeAgentOptions`` is invisible to it.
    """
    trees = {origin: ast.parse(text) for origin, text in sources.items()}
    functions = _functions(trees)
    for origin, tree in trees.items():
        bindings = _option_bindings(tree)
        parents: dict[ast.AST, ast.AST] = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_options_call(node.func, bindings):
                continue
            bound = _enclosing_bindings(node, parents, tree)
            yield origin, node.lineno, _merged_views(node, functions, bound)


def _view_failure(view: Mapping[str, ast.expr]) -> str | None:
    """Why one keyword set violates the guard, or ``None`` if it does not."""
    strict = view.get(STRICT_KEYWORD)
    if strict is None:
        return f"no {STRICT_KEYWORD}"
    if not _is_true(strict):
        return f"{STRICT_KEYWORD} is not True"
    return None


def strictness_failures(sources: Mapping[str, str]) -> list[str]:
    """Every construction across *sources* that does not carry the guard."""
    failures: list[str] = []
    for origin, line, views in _constructions(sources):
        where = f"{origin}:{line}"
        if views is None:
            failures.append(f"{where}: unresolvable option source")
            continue
        reasons = dict.fromkeys(
            reason for view in views if (reason := _view_failure(view)) is not None
        )
        failures.extend(f"{where}: {reason}" for reason in reasons)
    return failures


def server_sites(sources: Mapping[str, str]) -> set[str]:
    """The origins whose constructions can carry ``mcp_servers`` at all."""
    return {
        origin
        for origin, _line, views in _constructions(sources)
        if views is not None and any(SERVERS_KEYWORD in view for view in views)
    }


def package_sources() -> dict[str, str]:
    """Every module in the package, keyed by its path relative to ``src/``."""
    return {
        path.relative_to(SRC.parent).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(SRC.rglob("*.py"))
    }


# ---------------------------------------------------------------------------
# KOD-82-AC-1 — the structural invariant, and the controls that give it teeth
# ---------------------------------------------------------------------------


def test_every_construction_in_the_package_carries_the_guard() -> None:
    """The invariant, over every ``ClaudeAgentOptions`` construction in src/."""
    assert strictness_failures(package_sources()) == []


def test_the_scan_actually_ranges_over_both_executor_sites() -> None:
    """A vacuous pass is the failure this asserts against.

    Both adapters reach ``mcp_servers`` through a ``**`` unpack, so a scan
    that only reads literal keywords would find nothing and report green.
    """
    origins = server_sites(package_sources())

    assert origins == {
        "kodezart/adapters/claude_client_executor.py",
        "kodezart/adapters/claude_agent_executor.py",
    }


def test_a_direct_site_missing_the_flag_is_detected() -> None:
    """Negative control: the plain violation."""
    violation = {
        "fixture.py": (
            "from claude_agent_sdk import ClaudeAgentOptions\n"
            "\n"
            "def build():\n"
            '    return ClaudeAgentOptions(cwd="/tmp", mcp_servers={"x": {}})\n'
        ),
    }

    failures = strictness_failures(violation)

    assert failures == ["fixture.py:4: no strict_mcp_config"]


def test_a_construction_naming_neither_keyword_is_a_failure() -> None:
    """KOD-136: the shape the audit was blind to.

    A construction that describes no server is precisely the session the
    SDK default would let a committed ``.mcp.json`` furnish, so silence
    about the guard is the defect rather than the absence of one.
    """
    unguarded = {
        "fixture.py": (
            "from claude_agent_sdk import ClaudeAgentOptions\n"
            "\n"
            "def build():\n"
            '    return ClaudeAgentOptions(cwd="/tmp", allowed_tools=["Read"])\n'
        ),
    }

    assert strictness_failures(unguarded) == ["fixture.py:4: no strict_mcp_config"]


def test_a_construction_made_through_an_import_alias_is_seen_by_the_walk() -> None:
    """The callable is matched through its bindings, not through one literal.

    An aliased import and a call on a module object are the two ways a site
    can be written without the callable's own name standing as the callee,
    and neither is an exit from the scan.
    """
    escapes = {
        "alias.py": (
            "from claude_agent_sdk import ClaudeAgentOptions as _Options\n"
            "\n"
            "def build():\n"
            '    return _Options(cwd="/tmp", mcp_servers={"x": {}})\n'
        ),
        "attribute.py": (
            "import claude_agent_sdk as sdk\n"
            "\n"
            "def build():\n"
            '    return sdk.ClaudeAgentOptions(cwd="/tmp")\n'
        ),
    }

    assert strictness_failures(escapes) == [
        "alias.py:4: no strict_mcp_config",
        "attribute.py:4: no strict_mcp_config",
    ]
    assert server_sites(escapes) == {"alias.py"}


def test_a_helper_built_site_missing_the_flag_is_detected() -> None:
    """Negative control: the violation hidden behind the shape src/ uses."""
    violation = {
        "fixture.py": (
            "def _servers():\n"
            '    return {"mcp_servers": {"x": {}}}\n'
            "\n"
            "def build():\n"
            '    return ClaudeAgentOptions(cwd="/tmp", **_servers())\n'
        ),
    }

    failures = strictness_failures(violation)

    assert failures == ["fixture.py:5: no strict_mcp_config"]


def test_a_builder_whose_returns_disagree_about_the_flag_is_a_failure() -> None:
    """Each return is its own option set, and the guarded one cannot cover it.

    Folding a builder's returns into one union would let the branch that
    sets the guard answer for the branch that does not — a site the SDK
    runs unguarded whenever the second branch is the one taken.
    """
    violation = {
        "fixture.py": (
            "def _servers(named):\n"
            "    if named:\n"
            '        return {"mcp_servers": {"x": {}}, "strict_mcp_config": True}\n'
            '    return {"mcp_servers": {}}\n'
            "\n"
            "def build(named):\n"
            '    return ClaudeAgentOptions(cwd="/tmp", **_servers(named))\n'
        ),
    }

    assert strictness_failures(violation) == ["fixture.py:7: no strict_mcp_config"]


def test_a_site_whose_mapping_passes_through_a_local_is_still_read() -> None:
    """Negative control: the local the package's own sites use.

    A scan that could not see through one variable would report the real
    construction sites as unreadable and this violation as invisible.
    """
    violation = {
        "fixture.py": (
            "def _servers():\n"
            '    return {"mcp_servers": {"x": {}}}\n'
            "\n"
            "def build():\n"
            "    knowledge = _servers()\n"
            '    return ClaudeAgentOptions(cwd="/tmp", **knowledge)\n'
        ),
    }

    assert strictness_failures(violation) == [
        "fixture.py:6: no strict_mcp_config",
    ]


def test_the_flag_set_to_something_other_than_true_is_detected() -> None:
    """Negative control: present is not the claim — ``True`` is."""
    violation = {
        "fixture.py": (
            "def build(flag):\n"
            "    return ClaudeAgentOptions(\n"
            '        mcp_servers={"x": {}},\n'
            "        strict_mcp_config=flag,\n"
            "    )\n"
        ),
    }

    assert strictness_failures(violation) == [
        "fixture.py:2: strict_mcp_config is not True"
    ]


def test_an_option_source_the_scan_cannot_read_is_a_failure_not_a_skip() -> None:
    """An invariant that silently stops covering a site is worse than none."""
    opaque = {
        "fixture.py": (
            'def build(extra):\n    return ClaudeAgentOptions(cwd="/tmp", **extra)\n'
        ),
    }

    assert strictness_failures(opaque) == ["fixture.py:2: unresolvable option source"]


def test_prose_naming_the_guard_never_registers_as_a_site() -> None:
    """The walk visits calls, so a docstring cannot be mistaken for one."""
    docstring_only = {
        "fixture.py": (
            '"""Sessions are configured via ClaudeAgentOptions(mcp_servers=...).\n'
            "\n"
            "    Deliberately written without strict_mcp_config to prove that\n"
            "    prose does not register as a construction site.\n"
            '    """\n'
        ),
    }

    assert strictness_failures(docstring_only) == []
    assert server_sites(docstring_only) == set()


# ---------------------------------------------------------------------------
# KOD-82-AC-2 — the behavioural half: a workspace carrying a definition file
# ---------------------------------------------------------------------------

SENTINEL_SERVER: Final[str] = "workspace-injected-sentinel"
SENTINEL_TOOL: Final[str] = "workspace_injected_tool"


def _repo_with_a_server_definition(root: Path) -> Path:
    """A cloned-repository stand-in carrying an attacker-authored server."""
    repo = root / "cloned-target"
    repo.mkdir()
    (repo / ".mcp.json").write_text(
        '{"mcpServers": {"'
        + SENTINEL_SERVER
        + '": {"command": "/bin/false", "args": ["'
        + SENTINEL_TOOL
        + '"]}}}\n',
        encoding="utf-8",
    )
    (repo / "README.md").write_text("a target repository\n", encoding="utf-8")
    return repo


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_a_session_in_a_repo_carrying_a_server_file_is_configured_with_none(
    module: str,
    tmp_path: Path,
) -> None:
    """AC-2, offline: the session's server set holds nothing from the file.

    Exercised on a GRANTED session, per the recorded ruling: that is the
    arrangement under which a session carries configured servers at all,
    and the flag that makes the SDK ignore the working directory is set
    with them.  No vendor connection is made — the sentinel server is
    declared by this fixture, and the assertion is about what this
    codebase configures, which is the property the criterion is about.
    """
    repo = _repo_with_a_server_definition(tmp_path)

    options = (
        await recorded_session(
            module,
            grant=knowledge_grant_for(SessionType.TICKET_FIRE),
            session_type=SessionType.TICKET_FIRE,
            cwd=str(repo),
        )
    ).options

    assert options.cwd == str(repo)
    assert options.strict_mcp_config is True
    assert isinstance(options.mcp_servers, dict)
    assert SENTINEL_SERVER not in options.mcp_servers
    assert set(options.mcp_servers) == {FIXTURE_KNOWLEDGE_SERVER}
    assert SENTINEL_TOOL not in repr(options)


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_the_fixture_repo_really_declares_the_sentinel_server(
    module: str,
    tmp_path: Path,
) -> None:
    """The control for the test above: absence must be caused, not trivial."""
    repo = _repo_with_a_server_definition(tmp_path)

    declaration = (repo / ".mcp.json").read_text(encoding="utf-8")

    assert SENTINEL_SERVER in declaration
    assert SENTINEL_TOOL in declaration
    assert module in EXECUTOR_MODULES


# ---------------------------------------------------------------------------
# KOD-82-AC-3 — the grant's own site carries the flag
# ---------------------------------------------------------------------------


async def test_the_granted_session_option_assertion_includes_the_flag() -> None:
    """The Notion grant is not exempt from the invariant it rides with."""
    for module in EXECUTOR_MODULES:
        options = (
            await recorded_session(
                module,
                grant=knowledge_grant_for(SessionType.TICKET_FIRE),
                session_type=SessionType.TICKET_FIRE,
            )
        ).options
        assert options.strict_mcp_config is True
        assert set(options.mcp_servers or {}) == {FIXTURE_KNOWLEDGE_SERVER}
        assert options.setting_sources == list(DEFAULT_SETTING_SOURCES)


# ---------------------------------------------------------------------------
# KOD-128 — the guard is the SESSION's, not the grant's
# ---------------------------------------------------------------------------
#
# The structural invariant above ranges over every construction, one that
# configures a server and one that configures none alike, because the
# danger is the working directory rather than whatever this process
# happened to describe.  The block below is that same claim behaviourally:
# the shipped grant names no session type, so every shipped session is an
# ungranted one, and each of them still carries the guard.


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_an_ungranted_session_in_a_cloned_repo_still_carries_the_guard(
    module: str,
    tmp_path: Path,
) -> None:
    """The shipped arrangement: nothing granted, a cloned cwd, guard on.

    Exhaustive over the vocabulary rather than a spot check, because the
    defect was that a session type nobody named was a session type nobody
    guarded.  Each one runs with an EMPTY server map and the guard set:
    no MCP at all, which is the safe outcome for a directory whose
    contents an attacker authored.
    """
    repo = _repo_with_a_server_definition(tmp_path)

    for session_type in SessionType:
        options = (
            await recorded_session(
                module,
                grant=NO_KNOWLEDGE_GRANT,
                session_type=session_type,
                cwd=str(repo),
            )
        ).options

        assert options.cwd == str(repo), session_type
        assert options.strict_mcp_config is True, session_type
        assert options.mcp_servers == {}, session_type
        assert SENTINEL_SERVER not in repr(options), session_type


def test_the_mapping_names_every_session_kind_and_carries_no_default_arm() -> None:
    """A kind added later fails the build rather than shipping unguarded.

    mypy is what reports the missing return; this asserts the shape that
    makes it do so, so the guarantee cannot be lost to a wildcard arm that
    silently answers for a member nobody classified.
    """
    tree = ast.parse((SRC / "adapters" / "_mcp_mapping.py").read_text("utf-8"))
    statements = [node for node in ast.walk(tree) if isinstance(node, ast.Match)]

    assert len(statements) == 1
    named: set[str] = set()
    for case in statements[0].cases:
        assert case.guard is None
        for node in ast.walk(case.pattern):
            assert not isinstance(node, ast.MatchAs | ast.MatchStar), ast.dump(node)
            if isinstance(node, ast.MatchValue) and isinstance(
                node.value,
                ast.Attribute,
            ):
                named.add(node.value.attr)

    assert named == {member.name for member in SessionType}
