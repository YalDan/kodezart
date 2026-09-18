"""The walker reads no checkpoint, asserted over its own source (KOD-840).

A read that RETURNS the same values the tracker would is invisible to every
behavioural assertion by construction, so the absence is asserted over the
syntax tree instead: the walker and the service and domain modules it imports
name none of the four things a checkpoint is read or addressed through.

**Blind spots, stated rather than hidden.**

* The scanned set is the walker plus the ``kodezart.services`` and
  ``kodezart.domain`` modules its own import nodes name, and nothing deeper.
  Their imports are not followed: a checkpoint read two modules away is not
  seen here. The chain modules the walker imports are deliberately OUT of the
  set — the fire and lane graphs legitimately take a checkpointer, and the
  scope path's is settled by composition, which
  ``tests/integration/test_scope_runtime.py::
  test_the_scoped_arm_holds_no_checkpointer_while_the_authored_arm_keeps_it``
  asserts by construction instead.
* It is a name scan over names and over string literals equal to them, which
  is how a checkpoint is usually addressed
  (``config["configurable"]["thread_id"]``). A read reached through
  ``getattr`` with a computed name, or through a name built at runtime, is
  not seen.
* It says nothing about WRITES. That the scoped run leaves the configured
  saver empty is asserted by the composition test named above.

The detector has two controls. The scanned packages' own sibling — a chain
module the walker imports, found through the code and not picked — shows it
alive over real source. The per-shape controls are one-line sources written
here, because a control for one of the detector's arms cannot come from the
scanned surface: the surface is expected to name nothing, and until this file
carried them, three of the four names and the whole string-literal arm could
be removed with every assertion still passing.
"""

import ast
from pathlib import Path

import pytest

#: How a checkpoint is read, and how one is addressed.
FORBIDDEN = frozenset({"aget_state", "get_state", "checkpointer", "thread_id"})

#: The package prefixes the scan follows out of the walker's import nodes.
SCANNED_PACKAGES = ("kodezart.services.", "kodezart.domain.")

#: The prefixes the detector's own control is drawn from, out of the same
#: import nodes: a chain module the walker imports names a checkpointer
#: legitimately, so a detector that finds nothing THERE would find nothing
#: anywhere and the empty result below would say nothing.
CONTROL_PACKAGES = ("kodezart.chains.",)

SRC = Path(__file__).resolve().parents[2] / "src"
WALKER = SRC / "kodezart" / "services" / "scope_runtime.py"


def imported_modules(
    tree: ast.AST, prefixes: tuple[str, ...] = SCANNED_PACKAGES
) -> set[str]:
    """Every module the source imports from *prefixes*."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.startswith(prefixes):
                found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(
                alias.name for alias in node.names if alias.name.startswith(prefixes)
            )
    return found


def path_of(module: str) -> Path:
    return SRC.joinpath(*module.split(".")).with_suffix(".py")


def named_sites(path: Path) -> list[str]:
    """Every place *path* names one of the forbidden things."""
    return sites_in(ast.parse(path.read_text(encoding="utf-8")), label=path.name)


def sites_in(tree: ast.AST, *, label: str) -> list[str]:
    """Every place *tree* names one of the forbidden things.

    A string literal equal to one of them counts, because that is how a
    checkpoint is addressed: ``config["configurable"]["thread_id"] = ...``
    names the thing as a constant and as nothing else.

    Over a tree rather than a path, so the detector can be asked about a source
    written for the purpose: a control cannot be drawn from the surface it is
    the control for.
    """
    sites: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN:
            sites.append(f"{label}:{node.lineno}: .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN:
            sites.append(f"{label}:{node.lineno}: {node.id}")
        elif isinstance(node, ast.keyword) and node.arg in FORBIDDEN:
            sites.append(f"{label}:{node.lineno}: {node.arg}=")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in FORBIDDEN
        ):
            sites.append(f'{label}:{node.lineno}: "{node.value}"')
    return sites


#: One control per shape the detector claims to see, and one forbidden name per
#: control. Hand-written one-line sources, and deliberately so: a control drawn
#: from the scanned surface would say nothing about a detector arm the surface
#: happens not to exercise, which is how the string-constant arm and three of
#: the four names came to have no control at all. The set below is compared with
#: ``FORBIDDEN`` itself, so a name added there or dropped from it needs its own
#: line here.
CONTROLS = (
    ("aget_state", "x.aget_state"),
    ("get_state", "get_state(config)"),
    ("checkpointer", "build(checkpointer=saver)"),
    ("thread_id", 'config["configurable"]["thread_id"] = key'),
)


@pytest.mark.parametrize(("name", "source"), CONTROLS, ids=[n for n, _ in CONTROLS])
def test_the_detector_sees_each_shape_a_checkpoint_is_named_by(name, source) -> None:
    sites = sites_in(ast.parse(source), label="control")
    assert len(sites) == 1 and name in sites[0], sites


def test_every_forbidden_name_is_controlled() -> None:
    """A name the scan carries with no control is a name it could stop seeing."""
    assert {name for name, _ in CONTROLS} == FORBIDDEN


def test_the_walker_names_no_checkpoint_read() -> None:
    walker = ast.parse(WALKER.read_text(encoding="utf-8"))
    scanned = [WALKER, *(path_of(module) for module in imported_modules(walker))]
    # Derived, not hand-picked: the walker's own imports decide the set, and a
    # module added to it is scanned without this test being edited.
    assert len(scanned) > 1, "the walker imports no service or domain module"
    assert all(path.exists() for path in scanned)
    # The detector's own control, derived from the code and not picked: the
    # chain modules the walker's import nodes name, one of which compiles a
    # graph with its checkpointer. A detector that finds nothing there is
    # indistinguishable from a clean walker.
    controls = [
        path_of(module) for module in imported_modules(walker, CONTROL_PACKAGES)
    ]
    assert controls, "the walker imports no chain module to control the detector on"
    assert [path.name for path in controls if named_sites(path)]
    offenders = {path.name: sites for path in scanned if (sites := named_sites(path))}
    assert offenders == {}
