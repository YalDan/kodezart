"""The lane's whole claim, as a test: it adds a capability and nothing else.

Every other block in the knowledge lane asserts what it ADDS.  This one
asserts what it did not touch, and that negative is the deliverable.  A
reviewer's memory is not the mechanism — every clause below is measured
against a recorded commit.

**The baseline matters and is not trunk.** "Unchanged" is measured against
the tip of the stack immediately BEFORE this lane's fire, recorded in
:data:`BASE_SHA`.  The upstream lanes are what introduced the skills
selection and the setting sources into the executors' option construction,
so a trunk-relative diff would attribute their deltas to this lane and every
assertion here would be measuring the wrong thing.  The provenance section
proves the baseline is that tip rather than trunk, so a future reader can
tell which lane owns which delta.
"""

import ast
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: The pre-fire stack tip this lane was cut from — every "unchanged" claim
#: below is measured against this commit and against no other.
# Advanced 2026-08-31: the stack tip moved when the v0.2 close-out
# landed thirteen lane-6 commits and this lane merged them (Ruling B —
# a dependent lane stacks on its blocker's branch).  The baseline is
# the lane's own definition of "upstream", so it moves with the stack
# and never with trunk; the provenance tests below hold it honest.
BASE_SHA: Final[str] = "61b4b5ee3a0d8ac6570110e50585ad6c36610419"

#: Trunk at the time the lane's issues were written.  Present only so the
#: provenance assertions can show the baseline is NOT this commit.
TRUNK_SHA: Final[str] = "92597c0"

EXECUTOR_SOURCES: Final[dict[str, str]] = {
    "kodezart.adapters.claude_client_executor": (
        "src/kodezart/adapters/claude_client_executor.py"
    ),
    "kodezart.adapters.claude_agent_executor": (
        "src/kodezart/adapters/claude_agent_executor.py"
    ),
}

#: The upstream lane's suites.  Named as paths rather than as a directory
#: glob so an emptied directory cannot make the assertion vacuous.
TRACKER_SUITES: Final[tuple[str, ...]] = (
    "tests/tracker/test_tracker_conformance.py",
    "tests/tracker/test_linear_mcp_tracker.py",
    "tests/tracker/test_tracker_boot.py",
    "tests/tracker/test_tracker_boot_wiring.py",
    "tests/tracker/conftest.py",
    "tests/services/test_tracker_lifecycle.py",
)

#: The modules that own tracker writes.  A content class migrating out of
#: the tracker, or a tracker write redirected to the knowledge store, would
#: have to edit one of them.
TRACKER_WRITE_MODULES: Final[tuple[str, ...]] = (
    "src/kodezart/adapters/linear_mcp_tracker.py",
    "src/kodezart/services/tracker_boot.py",
    "src/kodezart/services/tracker_lifecycle.py",
    "src/kodezart/composition/tracker.py",
)

#: Anything that would make a module a client rather than a mapping.
CLIENT_IMPORTS: Final[tuple[str, ...]] = (
    "httpx",
    "requests",
    "aiohttp",
    "urllib",
    "http.client",
    "socket",
)

GOLDENS: Final[str] = "tests/prompts/goldens"
PROTOCOLS: Final[str] = "src/kodezart/core/protocols.py"
THREADED_PARAMETER: Final[str] = "session_type"


def git(*args: str) -> str:
    """Run git in the repository and return stdout, refusing to guess."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"git {' '.join(args)} failed: {result.stderr.strip()!r}. The additivity "
        f"baseline {BASE_SHA} must be present — fetch it rather than skipping "
        f"this check, which is the whole deliverable."
    )
    return result.stdout


def source_at(sha: str, path: str) -> str:
    """A file's contents at *sha*."""
    return git("show", f"{sha}:{path}")


def changed_paths() -> set[str]:
    """Every path this lane's diff touches, uncommitted work included."""
    tracked = git("diff", "--name-only", BASE_SHA).split()
    untracked = git("ls-files", "--others", "--exclude-standard").split()
    return set(tracked) | set(untracked)


def added_paths(prefix: str) -> set[str]:
    """Paths under *prefix* that exist at head and did not exist at the base."""
    at_base = set(git("ls-tree", "-r", "--name-only", BASE_SHA, "--", prefix).split())
    return {path for path in changed_paths() if path.startswith(prefix)} - at_base


def blob_ids(sha: str, prefix: str) -> dict[str, str]:
    """``path -> blob id`` for every file under *prefix* at *sha*."""
    listing: dict[str, str] = {}
    for line in git("ls-tree", "-r", sha, "--", prefix).splitlines():
        meta, _, path = line.partition("\t")
        listing[path] = meta.split()[2]
    return listing


def all_option_keywords(source: str) -> set[str]:
    """Every keyword name any options construction in *source* passes."""
    return {name for names in option_keywords(source).values() for name in names}


def option_keywords(source: str) -> dict[str, list[str]]:
    """``callee -> keyword names`` for every options construction in *source*."""
    found: dict[str, list[str]] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "ClaudeAgentOptions":
            continue
        found.setdefault(
            f"{name}@{node.lineno}",
            [kw.arg for kw in node.keywords if kw.arg is not None],
        )
    return found


def protocol_shape(source: str) -> dict[str, dict[str, list[str]]]:
    """``protocol -> method -> parameter names`` for a protocols module."""
    shape: dict[str, dict[str, list[str]]] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef):
            continue
        methods: dict[str, list[str]] = {}
        for member in node.body:
            if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                args = member.args
                methods[member.name] = [
                    arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)
                ]
        shape[node.name] = methods
    return shape


def declares_class_with_methods(source: str) -> bool:
    """Whether *source* declares a class carrying behaviour."""
    return any(
        isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ClassDef)
        for member in node.body
    )


def imported_modules(source: str) -> set[str]:
    """Every module name *source* imports."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def _touches(paths: Iterable[str], changed: set[str]) -> list[str]:
    return sorted(path for path in paths if path in changed)


# ---------------------------------------------------------------------------
# KOD-85-AC-5 — the baseline is the pre-fire stack tip, demonstrably not trunk
# ---------------------------------------------------------------------------


def test_the_recorded_baseline_is_an_ancestor_of_head_and_a_descendant_of_trunk() -> (
    None
):
    """The baseline sits where the lane says it does, in the real history."""
    git("merge-base", "--is-ancestor", BASE_SHA, "HEAD")
    git("merge-base", "--is-ancestor", TRUNK_SHA, BASE_SHA)

    assert git("rev-parse", BASE_SHA).strip() == BASE_SHA
    assert git("rev-parse", TRUNK_SHA).strip() != BASE_SHA


def test_the_upstream_option_delta_is_visible_between_trunk_and_the_baseline() -> None:
    """Which lane owns which delta, made readable rather than asserted.

    The skills selection and the setting sources enter the construction
    UPSTREAM of this lane.  A trunk-relative baseline would hand them to
    this lane's diff, which is exactly the misattribution AC-5 names.
    """
    for path in EXECUTOR_SOURCES.values():
        trunk = all_option_keywords(source_at(TRUNK_SHA, path))
        base = all_option_keywords(source_at(BASE_SHA, path))

        assert trunk < base, path
        assert base - trunk == {"skills", "setting_sources"}, path


def test_the_option_equality_baseline_was_captured_at_the_recorded_base() -> None:
    """The fixture the equality assertions compare against has provenance.

    Read off the base-sha sources rather than off a memory of them: the
    literal in the executor suite must carry exactly the argument names the
    base construction passed, argument name for argument name.
    """
    fixture = (REPO_ROOT / "tests/adapters/test_claude_executors.py").read_text(
        encoding="utf-8",
    )
    baselines = [
        keywords
        for name, keywords in sorted(option_keywords(fixture).items())
        if "mcp_servers" not in keywords
    ]
    assert len(baselines) == len(EXECUTOR_SOURCES)

    at_base = [
        sorted(all_option_keywords(source_at(BASE_SHA, path)))
        for path in EXECUTOR_SOURCES.values()
    ]

    assert sorted(sorted(entry) for entry in baselines) == sorted(at_base)


def test_neither_option_argument_this_lane_adds_existed_at_the_baseline() -> None:
    """The strict flag is this lane's delta and is attributed to it."""
    for path in EXECUTOR_SOURCES.values():
        base = all_option_keywords(source_at(BASE_SHA, path))

        assert "mcp_servers" not in base, path
        assert "strict_mcp_config" not in base, path


# ---------------------------------------------------------------------------
# KOD-85-AC-1 — no new port, no new adapter, no edited tracker suite
# ---------------------------------------------------------------------------


def test_the_lane_adds_no_protocol_and_no_protocol_method() -> None:
    """AC-1: the seam is threaded, never widened."""
    base = protocol_shape(source_at(BASE_SHA, PROTOCOLS))
    head = protocol_shape((REPO_ROOT / PROTOCOLS).read_text(encoding="utf-8"))

    assert set(head) == set(base)
    for protocol, methods in head.items():
        assert set(methods) == set(base[protocol]), protocol


def test_the_only_parameter_the_lane_threads_is_the_session_type() -> None:
    """Carrying a parameter the seam already needed is not widening a port."""
    base = protocol_shape(source_at(BASE_SHA, PROTOCOLS))
    head = protocol_shape((REPO_ROOT / PROTOCOLS).read_text(encoding="utf-8"))

    for protocol, methods in head.items():
        for method, parameters in methods.items():
            added = set(parameters) - set(base[protocol][method])
            assert added in ({THREADED_PARAMETER}, set()), f"{protocol}.{method}"


def test_every_adapter_module_the_lane_adds_is_a_private_mapping_helper() -> None:
    """AC-1: no vendor client, and nothing that could grow into one."""
    added = added_paths("src/kodezart/adapters/")
    assert added, "the check must range over the module this lane did add"

    for path in sorted(added):
        source = (REPO_ROOT / path).read_text(encoding="utf-8")

        assert Path(path).name.startswith("_"), path
        assert not declares_class_with_methods(source), path
        assert imported_modules(source).isdisjoint(CLIENT_IMPORTS), path
        assert "async def" not in source, path


def test_nothing_in_the_package_dials_the_knowledge_server_itself() -> None:
    """The server definition is handed to the SDK; nothing here connects.

    One reader of the grant's endpoint, and it builds a mapping.  A second
    reader would be the bespoke client the lane is forbidden to add.
    """
    readers = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "src").rglob("*.py")
        if "grant.server_url" in path.read_text(encoding="utf-8")
    )

    assert readers == ["src/kodezart/adapters/_mcp_mapping.py"]


def test_no_module_the_lane_touches_gained_a_client_import() -> None:
    """Additivity at import level: no changed module learned to open a socket."""
    at_base = set(git("ls-tree", "-r", "--name-only", BASE_SHA, "--", "src/").split())
    gained: dict[str, set[str]] = {}
    for path in sorted(p for p in changed_paths() if p.endswith(".py")):
        if not path.startswith("src/"):
            continue
        head = imported_modules((REPO_ROOT / path).read_text(encoding="utf-8"))
        base = imported_modules(source_at(BASE_SHA, path)) if path in at_base else set()
        added = (head - base) & set(CLIENT_IMPORTS)
        if added:
            gained[path] = added

    assert gained == {}


def test_no_tracker_suite_file_is_edited_by_this_lane() -> None:
    """AC-2: a green suite whose fixtures this lane adjusted proves nothing."""
    for path in TRACKER_SUITES:
        assert (REPO_ROOT / path).is_file(), path

    assert _touches(TRACKER_SUITES, changed_paths()) == []


# ---------------------------------------------------------------------------
# KOD-85-AC-3 — exactly one prompt delta
# ---------------------------------------------------------------------------


def test_every_golden_is_the_byte_the_baseline_recorded() -> None:
    """Byte-identity in its strongest form: the pinned bytes never moved.

    Compared as blob ids rather than as a name list, so a rewrite that kept
    a file's path could not pass.
    """
    at_base = blob_ids(BASE_SHA, GOLDENS)
    at_head = blob_ids("HEAD", GOLDENS)

    assert at_base
    assert at_head == at_base
    assert _touches(at_base, changed_paths()) == []


def test_the_only_prompt_the_lane_adds_is_the_knowledge_map() -> None:
    """One prompt delta: one new member, and no existing member rewritten."""
    sets_root = "src/kodezart/prompts/sets/"
    at_base = blob_ids(BASE_SHA, sets_root)
    at_head = blob_ids("HEAD", sets_root)

    added = set(at_head) - set(at_base)
    rewritten = {
        path
        for path in set(at_head) & set(at_base)
        if at_head[path] != at_base[path] and not path.endswith("set.toml")
    }

    assert added == {f"{sets_root}claude-opus/knowledge_map.md"}
    assert rewritten == set()


# ---------------------------------------------------------------------------
# D-5 — the out-of-scope list is enforced, not merely stated
# ---------------------------------------------------------------------------


def test_no_tracker_write_module_is_touched_by_this_lane() -> None:
    """A content class migrating out of the tracker would have to edit one."""
    for path in TRACKER_WRITE_MODULES:
        assert (REPO_ROOT / path).is_file(), path

    assert _touches(TRACKER_WRITE_MODULES, changed_paths()) == []


def test_the_lane_touches_no_source_surface_outside_its_declared_diff() -> None:
    """The diff is auditable by inspection because it is small and named.

    Not a list of files someone remembered to keep current: the assertion is
    that every source file the lane changed is one of the surfaces its
    blocks name — the two executors and their mapping helper, the session
    vocabulary, the configuration that resolves the grant, the composition
    that renders the map, the prompt vocabulary and set, the threading seam,
    the egress redaction the credential joined, and the package docstring
    whose worked example a future author copies a construction from.
    """
    declared = {
        "src/kodezart/adapters/_mcp_mapping.py",
        "src/kodezart/adapters/agent_content_scanner.py",
        "src/kodezart/adapters/claude_agent_executor.py",
        "src/kodezart/adapters/claude_client_executor.py",
        "src/kodezart/adapters/git_change_persister.py",
        "src/kodezart/agents/__init__.py",
        "src/kodezart/chains/ralph_loop.py",
        "src/kodezart/chains/ralph_workflow.py",
        "src/kodezart/chains/ticket_generation.py",
        "src/kodezart/composition/knowledge.py",
        "src/kodezart/core/config.py",
        "src/kodezart/core/error_egress.py",
        "src/kodezart/core/protocols.py",
        "src/kodezart/handlers/agent_handler.py",
        "src/kodezart/main.py",
        "src/kodezart/prompts/sets/claude-opus/knowledge_map.md",
        "src/kodezart/prompts/sets/claude-opus/set.toml",
        "src/kodezart/services/agent_service.py",
        "src/kodezart/types/domain/prompts.py",
        "src/kodezart/types/domain/session.py",
    }
    touched = {path for path in changed_paths() if path.startswith("src/")}

    assert touched <= declared, sorted(touched - declared)
