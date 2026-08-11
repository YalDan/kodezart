"""The working-directory MCP injection guard, machine-checked over ``src/``.

The service clones an arbitrary repository into an isolated worktree and
runs an agent with ``cwd`` set to it.  Without ``strict_mcp_config``, a
``.mcp.json`` committed into that repository is loaded into the session
alongside the configured servers — attacker-authored tool injection into a
session that already holds credentials.

The invariant that closes it: **wherever a session is configured with
``mcp_servers``, the same option source also sets ``strict_mcp_config=True``.**
This module asserts it structurally over every construction in the package,
so a future site added without the flag fails the gate, and behaviourally
against a workspace that actually carries a server-definition file.

Why the scan resolves ``**`` unpacking rather than reading literal keywords:
both construction sites reach ``mcp_servers`` through a mapping a helper
builds, so a keyword-only scan would range over zero sites and pass
vacuously.  An option source the scan cannot resolve is a FAILURE, never a
skip — an invariant that quietly stops covering a site is worse than none.
"""

import ast
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Final

import pytest

from kodezart.types.domain.session import SessionType
from tests.fakes import (
    DEFAULT_SETTING_SOURCES,
    EXECUTOR_MODULES,
    FIXTURE_KNOWLEDGE_SERVER,
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


def _contributions(
    call: ast.Call,
    functions: Mapping[str, list[ast.FunctionDef]],
) -> Iterator[dict[str, ast.expr] | None]:
    """Every option source feeding one construction.

    The explicit keywords are one source; each ``**`` unpack is another,
    resolved through the function that builds it.  Yielding ``None`` marks
    a source this scan cannot read.
    """
    explicit = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
    yield explicit
    for keyword in call.keywords:
        if keyword.arg is not None:
            continue
        inline = _mapping_items(keyword.value)
        if inline is not None and not isinstance(keyword.value, ast.Call):
            yield inline
            continue
        name = (
            _called_name(keyword.value.func)
            if isinstance(keyword.value, ast.Call)
            else None
        )
        builders = functions.get(name or "", [])
        if not builders:
            yield None
            continue
        for builder in builders:
            yield from _returned_mappings(builder)


def _is_true(node: ast.expr) -> bool:
    """Whether an expression is the literal ``True``."""
    return isinstance(node, ast.Constant) and node.value is True


def pairing_failures(sources: Mapping[str, str]) -> list[str]:
    """Every violation of the pairing across *sources*.

    Docstrings and comments cannot register: the walk visits call nodes,
    so prose naming ``ClaudeAgentOptions`` is invisible to it.
    """
    trees = {origin: ast.parse(text) for origin, text in sources.items()}
    functions = _functions(trees)
    failures: list[str] = []
    for origin, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _called_name(node.func) != OPTIONS_CALLABLE:
                continue
            where = f"{origin}:{node.lineno}"
            for contribution in _contributions(node, functions):
                if contribution is None:
                    failures.append(f"{where}: unresolvable option source")
                    continue
                if SERVERS_KEYWORD not in contribution:
                    continue
                strict = contribution.get(STRICT_KEYWORD)
                if strict is None:
                    failures.append(
                        f"{where}: {SERVERS_KEYWORD} without {STRICT_KEYWORD}"
                    )
                elif not _is_true(strict):
                    failures.append(f"{where}: {STRICT_KEYWORD} is not True")
    return failures


def server_sites(sources: Mapping[str, str]) -> set[str]:
    """The origins whose constructions can carry ``mcp_servers`` at all."""
    trees = {origin: ast.parse(text) for origin, text in sources.items()}
    functions = _functions(trees)
    origins: set[str] = set()
    for origin, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _called_name(node.func) != OPTIONS_CALLABLE:
                continue
            for contribution in _contributions(node, functions):
                if contribution and SERVERS_KEYWORD in contribution:
                    origins.add(origin)
    return origins


def package_sources() -> dict[str, str]:
    """Every module in the package, keyed by its path relative to ``src/``."""
    return {
        path.relative_to(SRC.parent).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(SRC.rglob("*.py"))
    }


# ---------------------------------------------------------------------------
# KOD-82-AC-1 — the structural invariant, and the controls that give it teeth
# ---------------------------------------------------------------------------


def test_no_construction_in_the_package_configures_servers_without_the_flag() -> None:
    """The invariant, over every ``ClaudeAgentOptions`` construction in src/."""
    assert pairing_failures(package_sources()) == []


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

    failures = pairing_failures(violation)

    assert failures == ["fixture.py:4: mcp_servers without strict_mcp_config"]


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

    failures = pairing_failures(violation)

    assert failures == ["fixture.py:5: mcp_servers without strict_mcp_config"]


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

    assert pairing_failures(violation) == [
        "fixture.py:2: strict_mcp_config is not True"
    ]


def test_an_option_source_the_scan_cannot_read_is_a_failure_not_a_skip() -> None:
    """An invariant that silently stops covering a site is worse than none."""
    opaque = {
        "fixture.py": (
            'def build(extra):\n    return ClaudeAgentOptions(cwd="/tmp", **extra)\n'
        ),
    }

    assert pairing_failures(opaque) == ["fixture.py:2: unresolvable option source"]


def test_prose_naming_the_pairing_never_registers_as_a_site() -> None:
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

    assert pairing_failures(docstring_only) == []
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
