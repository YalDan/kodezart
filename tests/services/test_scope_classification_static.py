"""No tracker call on the scoped path can run before the ready read (KOD-465).

The criterion asks two things of the source rather than of a run. First, that
``read_scope_ready`` OPENS with ``require_issue_classification_reads()``, so an
operation that declares no ``issue_labels['criterion']`` is refused by the first
scoped read and not by the second thing that read happens to do. Second, that
there is no path through a scope service in which a tracker call executes before
such a read — which is a statement about every function in the module, not about
the one function a behavioural test happens to drive.

Both are asserted over the syntax tree. A behavioural test can only show that
the refusal happens on the paths it drives; the claim is about the paths it does
not.

**The reading.** For each scope service — derived, see below — let S be every
function in the module that LOADS ``self._tracker``. A function is ANCHORED when
its first ``await`` in source order is a call to
``read_scope_ready(..., tracker=self._tracker)``. A function is COVERED when it
is anchored, or when it is called somewhere in the module and every one of those
call sites is after the anchoring await of an anchored function or inside a
covered function. Every member of S must be covered, and every public coroutine
of the class that loads ``self._tracker`` must be anchored — a caller reaching
one of those has no earlier statement of this module's to stand behind.

**Blind spots, stated rather than hidden.**

* A tracker call made through a collaborator this module was CONSTRUCTED with is
  invisible here: ``self._entries``, ``self._resolver`` and the lane workflows
  reach their own ports, and what orders those reads is the graph they belong
  to, not this scan.
* It is a name scan. A load reached through ``getattr(self, "_tracker")``, or a
  call assembled at runtime, is not seen.
* Call sites are matched by NAME within the module, so a member handed on as a
  value (``callbacks.append(self._put_back)``) is not read as a call site.
* Ordering is source order. A call site textually after an anchor but reachable
  before it — through a loop that continues past the anchor on a later pass, say
  — is accepted; what the scan refuses is a call the source places first.

**The scanned set is derived, not listed.** Every ``kodezart.services`` module
whose own import nodes bind ``read_scope_ready`` from
``kodezart.chains.scope_walker`` is scanned. A new scope service is scanned
without this file being edited, and deleting one shrinks the set the same way.

**The detector's controls are hand-written sources.** A control cannot be drawn
from the scanned surface, because the surface is expected to yield nothing: until
a source that violates each arm is fed through the same function, an arm that had
stopped seeing anything would be indistinguishable from a clean module.
"""

import ast
from pathlib import Path

import pytest

#: The attribute a scope service holds its tracker port under.
TRACKER_ATTR = "_tracker"

#: The read every tracker call on this path must stand behind.
READY_READ = "read_scope_ready"

#: Where the ready read lives, and the module whose opening statement is read.
WALKER_MODULE = "kodezart.chains.scope_walker"

#: The check that read must open with.
CLASSIFICATION_CHECK = "require_issue_classification_reads"

#: The package whose modules are scanned when they bind the ready read.
SERVICES = "kodezart.services"

SRC = Path(__file__).resolve().parents[2] / "src"


def _module_path(module: str) -> Path:
    return SRC.joinpath(*module.split(".")).with_suffix(".py")


def _parse(module: str) -> ast.Module:
    return ast.parse(_module_path(module).read_text(encoding="utf-8"))


def _call_name(node: ast.expr) -> str:
    """The bare name a call's target ends in: ``self._settle`` is ``_settle``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def scope_service_modules() -> tuple[str, ...]:
    """Every service module that binds the ready read, from its own imports.

    Read out of the source and not listed here: the set is exactly the modules
    that could place a tracker call before a ready read, and it follows the
    code rather than a hand-kept list beside it.
    """
    found: set[str] = set()
    package = SRC.joinpath(*SERVICES.split("."))
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == WALKER_MODULE
                and any(alias.name == READY_READ for alias in node.names)
            ):
                found.add(f"{SERVICES}.{path.stem}")
    return tuple(sorted(found))


Function = ast.FunctionDef | ast.AsyncFunctionDef


def _owners(tree: ast.Module) -> dict[ast.AST, Function | None]:
    """Which function each node sits inside, innermost winning."""
    owners: dict[ast.AST, Function | None] = {}

    def visit(node: ast.AST, owner: Function | None) -> None:
        for child in ast.iter_child_nodes(node):
            owners[child] = owner
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                visit(child, child)
            else:
                visit(child, owner)

    visit(tree, None)
    return owners


def _position(node: ast.AST) -> tuple[int, int]:
    return (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))


def _loads_tracker(fn: Function, owners: dict[ast.AST, Function | None]) -> bool:
    return any(
        isinstance(node, ast.Attribute)
        and node.attr == TRACKER_ATTR
        and isinstance(node.ctx, ast.Load)
        and owners.get(node) is fn
        for node in ast.walk(fn)
    )


def _first_await(
    fn: Function, owners: dict[ast.AST, Function | None]
) -> ast.Await | None:
    awaits = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Await) and owners.get(node) is fn
    ]
    return min(awaits, key=_position) if awaits else None


def _is_ready_read(node: ast.expr) -> bool:
    """Whether *node* is ``read_scope_ready(..., tracker=self._tracker)``."""
    if not isinstance(node, ast.Call) or _call_name(node.func) != READY_READ:
        return False
    return any(
        keyword.arg == "tracker"
        and isinstance(keyword.value, ast.Attribute)
        and keyword.value.attr == TRACKER_ATTR
        for keyword in node.keywords
    )


def _anchor(fn: Function, owners: dict[ast.AST, Function | None]) -> ast.Await | None:
    """The ready read this function opens with, when it opens with one."""
    first = _first_await(fn, owners)
    if first is None or not _is_ready_read(first.value):
        return None
    return first


def offenders(tree: ast.Module) -> list[str]:
    """Every function of *tree* that could reach the tracker before a ready read.

    Two rules, one list: an uncovered tracker load, and a public coroutine
    holding one that does not itself open with the read.
    """
    owners = _owners(tree)
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    anchors = {fn.name: _anchor(fn, owners) for fn in functions}
    sites: dict[str, list[ast.Call]] = {fn.name: [] for fn in functions}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (name := _call_name(node.func)) in sites:
            sites[name].append(node)
    covered: dict[str, bool] = {}
    walking: set[str] = set()

    def is_covered(name: str) -> bool:
        if anchors.get(name) is not None:
            return True
        if name in covered:
            return covered[name]
        if name in walking:
            # A call cycle stands behind no anchor of its own.
            return False
        walking.add(name)
        calls = [call for call in sites[name] if owners.get(call) is not None]
        answer = len(calls) == len(sites[name]) and bool(calls)
        for call in calls:
            owner = owners[call]
            assert owner is not None
            anchor = anchors.get(owner.name)
            if anchor is not None:
                answer = answer and _position(call) > _position(anchor)
            else:
                answer = answer and is_covered(owner.name)
        walking.discard(name)
        covered[name] = answer
        return answer

    found = [
        f"{fn.name} loads self.{TRACKER_ATTR} and stands behind no ready read"
        for fn in functions
        if _loads_tracker(fn, owners) and not is_covered(fn.name)
    ]
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        found.extend(
            f"{node.name}.{member.name} is entered from outside and is not anchored"
            for member in node.body
            if isinstance(member, ast.AsyncFunctionDef)
            and not member.name.startswith("_")
            and _loads_tracker(member, owners)
            and anchors.get(member.name) is None
        )
    return found


def test_the_ready_read_opens_with_the_classification_check() -> None:
    """The refusal is the read's FIRST act, not something it reaches later.

    A later position would let a scope whose operation declares no criterion
    mapping make tracker calls first and be refused afterwards, which is the
    one thing the criterion forbids.
    """
    tree = _parse(WALKER_MODULE)
    read = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == READY_READ
    )
    body = [
        statement
        for statement in read.body
        if not (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        )
    ]
    opening = body[0]
    assert isinstance(opening, ast.Expr), ast.dump(opening)
    call = opening.value
    assert isinstance(call, ast.Call)
    assert _call_name(call.func) == CLASSIFICATION_CHECK
    # On the read's OWN tracker parameter: a check made against anything else
    # would be a statement about some other port than the one about to be used.
    assert isinstance(call.func, ast.Attribute)
    assert isinstance(call.func.value, ast.Name)
    assert call.func.value.id == read.args.kwonlyargs[1].arg == "tracker"
    # No arguments: the three classifications the scoped path needs are the
    # check's own list, so no call site can narrow it.
    assert call.args == [] and call.keywords == []


@pytest.mark.parametrize("module", scope_service_modules())
def test_no_tracker_call_can_run_before_a_ready_read(module: str) -> None:
    assert offenders(_parse(module)) == []


def test_the_scan_follows_the_code_to_its_modules() -> None:
    """An empty scanned set would make the parametrized case say nothing."""
    assert scope_service_modules()


#: One source per arm of the scan, each a violation the arm exists to find.
#: Hand-written, because the scanned modules are expected to yield nothing.
OFFENDING_SOURCES = (
    (
        "a tracker call before the anchor",
        """
class Runner:
    async def run(self, ref):
        await self._tracker.restore_workflow_state(issue_key="k", state_name="s")
        ready = await read_scope_ready(ref=ref, tracker=self._tracker)
""",
    ),
    (
        "a helper called before the anchor",
        """
class Runner:
    async def run(self, ref):
        await self._put_back(key="k")
        ready = await read_scope_ready(ref=ref, tracker=self._tracker)

    async def _put_back(self, *, key):
        await self._tracker.restore_workflow_state(issue_key=key, state_name="s")
""",
    ),
    (
        "an unanchored public coroutine",
        """
class Runner:
    async def run_pass(self):
        await self._log.ainfo("starting")
        await self._tracker.restore_workflow_state(issue_key="k", state_name="s")
""",
    ),
)


@pytest.mark.parametrize(
    ("label", "source"),
    OFFENDING_SOURCES,
    ids=[label for label, _ in OFFENDING_SOURCES],
)
def test_the_scan_flags_a_tracker_call_placed_before_the_ready_read(
    label: str, source: str
) -> None:
    assert offenders(ast.parse(source)) != [], label


def test_a_source_that_stands_behind_its_anchor_is_accepted() -> None:
    """The shape the scanned modules have, so the scan is not refusing everything.

    Anchored public entry, a helper called after the anchor, and a tracker load
    inside that helper — the arrangement ``scope_runtime`` itself has.
    """
    source = """
class Runner:
    async def run(self, ref):
        ready = await read_scope_ready(ref=ref, tracker=self._tracker)
        await self._settle(ready=ready)

    async def _settle(self, *, ready):
        await self._put_back(key=ready)

    async def _put_back(self, *, key):
        await self._tracker.restore_workflow_state(issue_key=key, state_name="s")
"""
    assert offenders(ast.parse(source)) == []
