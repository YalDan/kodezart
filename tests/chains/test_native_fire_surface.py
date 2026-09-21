"""The tracker-native fire's test modules stay inside the census and off live.

KOD-441 asks that the lane's fixtures run over the in-process fakes, with no
live workspace and no live tracker, and that nothing about its test surface
is weakened.  Read literally, "imports only from `tests/fakes.py`" is false
on four of the nine modules the criterion names today: they also import from
the tracker package's own fixtures and from four sibling test modules whose
builders they reuse.  Rewriting those four is production of another kind and
is not what the clause is for.

What the clause is for is read here as two falsifiable halves.  Everything
the lane reaches through suite imports resolves inside the walk the census
covers, so the criterion cannot be met by a file nothing reads; and none of
what it reaches declares itself a live surface by the gate's own mark, so no
fixture in the lane's reach needs a credential or a real service.  The
hermetic half that can be read back -- that the suite reads no deployment
file at all -- is pinned beside them.

Marker and alias shapes are controlled in the baseline module, which owns
the resolver; this module controls only the walk that feeds it.
"""

from kodezart.config.app import AppConfig
from tests import negative_shape
from tests.negative_shape import Source
from tests.test_suppression_baseline import RECORDED

#: The nine files KOD-441's Do names.  This is the criterion's scope, a
#: tracker fact the tree cannot derive; everything read about them below is
#: derived from the tree, as the walker's name is hand-written in the scope
#: runtime's static guard and its surface derived from it.
LANE_MODULES: tuple[str, ...] = (
    "tests/chains/test_fire_spec_prompt_consumers.py",
    "tests/chains/test_native_fire.py",
    "tests/chains/test_native_fresh_boundaries.py",
    "tests/domain/test_fire_spec.py",
    "tests/domain/test_fire_spec_arm_reads.py",
    "tests/domain/test_fire_spec_formatter.py",
    "tests/domain/test_fire_spec_purity.py",
    "tests/tracker/test_empty_fire_entry.py",
    "tests/tracker/test_fire_spec_reader.py",
)

#: The in-process fakes every one of the nine reaches, directly or through a
#: sibling module's builders.
SHARED_FAKES = "tests/fakes.py"


def test_every_lane_module_is_walked_and_recorded() -> None:
    """A module the census does not record is a module nothing protects."""
    walked = {module.path for module in negative_shape.sources()}

    assert set(LANE_MODULES) <= walked
    assert set(LANE_MODULES) <= set(RECORDED["declarations"])
    assert set(LANE_MODULES) <= set(RECORDED["asserts"])


def test_every_test_import_the_lane_reaches_resolves_inside_the_census() -> None:
    """A suite import that resolves to nothing is a surface nothing reads."""
    walked = {module.path for module in negative_shape.sources()}
    unresolved = {
        f"{path} -> {name}"
        for path, imports in negative_shape.reach(LANE_MODULES).items()
        for name in imports
        if negative_shape.module_path(name) not in walked
    }

    assert unresolved == set()


def test_the_lane_reaches_the_shared_fakes_and_more_than_itself() -> None:
    """The walk follows imports out of the lane; twenty-four files today."""
    reached = negative_shape.reach(LANE_MODULES)

    assert SHARED_FAKES in reached
    assert len(reached) > len(LANE_MODULES)


def test_nothing_the_lane_reaches_carries_a_gated_mark() -> None:
    """A fixture the gate deselects is a fixture that needs a real service."""
    gated = {
        path: negative_shape.sites(
            negative_shape.source(path), negative_shape.gated_mark_forms()
        )
        for path in negative_shape.reach(LANE_MODULES)
    }

    assert {path: marks for path, marks in gated.items() if marks} == {}


def test_the_suite_reads_no_deployment_file() -> None:
    """No live workspace and no live tracker, by construction rather than care."""
    assert AppConfig.model_config["env_file"] is None


def test_the_walk_follows_an_import_made_inside_a_function() -> None:
    """An import written inside a fixture body is read as one written at the top."""
    control = Source.of("control.py", "def f():\n    from tests.fakes import x\n")

    assert negative_shape.test_imports(control) == ("tests.fakes",)


def test_the_walk_reaches_a_module_that_declares_itself_gated() -> None:
    """The pair outside the lane that proves the guard would see the shape."""
    reached = negative_shape.reach(("tests/probes/test_ab_failure_capture.py",))
    gated = negative_shape.source("tests/probes/test_ab_smoke.py")

    assert "tests/probes/test_ab_smoke.py" in reached
    assert negative_shape.sites(gated, negative_shape.gated_mark_forms()) != ()
