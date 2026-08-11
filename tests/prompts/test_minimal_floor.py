"""The minimal-floor fixture: the smallest config that boots (KOD-112 R5).

``docs/operation.minimal.toml`` is what a new operator copies first, and
``docs/operation.example.toml`` is the complete annotated counterpart it
grows into.  The fixture is evidence only if it actually loads through the
shipped loader with every collection empty — a floor stated in prose and
never loaded is a claim, not a floor.
"""

from pathlib import Path

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.types.domain.operation import OperationConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
MINIMAL = REPO_ROOT / "docs" / "operation.minimal.toml"


def minimal_fixture() -> OperationConfig:
    return load_operation_config(MINIMAL)


def test_the_minimal_fixture_loads_through_the_shipped_loader() -> None:
    """The floor in one line: two scalars are a bootable operation."""
    config = minimal_fixture()
    assert config.operation_name == "example-operation"
    assert config.workspace == "example-workspace"


def test_the_minimal_fixture_declares_nothing_beyond_the_floor() -> None:
    """Smallest means smallest: every collection is empty, no field is set.

    A "minimal" fixture that quietly carries a principal or a queue map
    stops demonstrating that the floor boots, which is the whole claim.
    """
    config = minimal_fixture()
    scalars = {"operation_name", "workspace"}
    for field in set(OperationConfig.model_fields) - scalars - {"private_surface"}:
        assert len(getattr(config, field)) == 0, field
    assert config.private_surface is None


def test_the_minimal_fixture_yields_a_boot_ready_binding_namespace() -> None:
    """Boots means past the registry too: bindings build and stay disjoint.

    ``bindings_for`` asserts namespace disjointness on the way through, so
    this is the same check boot performs, run over the floor.
    """
    bindings = bindings_for(minimal_fixture())
    assert bindings["operation_name"] == "example-operation"
    assert bindings["principals"] is None
    assert bindings["principals_absent"] is True


def test_the_readme_points_a_new_operator_at_the_floor() -> None:
    """Discoverable next to the complete example, or it helps nobody."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/operation.minimal.toml" in readme
    assert "docs/operation.example.toml" in readme
